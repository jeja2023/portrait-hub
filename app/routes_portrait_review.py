from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.portrait_thresholds import threshold_snapshot
from app.portrait_review import (
    MAX_REVIEW_LIST_LIMIT,
    MAX_REVIEW_DATASET_LIMIT,
    create_review_annotation,
    list_review_annotations,
    list_review_datasets,
    review_annotation_summary,
    review_threshold_recommendations,
    restore_review_state,
    review_state_payload,
)
from app.security import require_api_token


router = APIRouter(dependencies=[Depends(require_api_token)])


class TrackReviewAnnotationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., min_length=1, max_length=512)
    track_id: str = Field(..., min_length=1, max_length=512)
    label: str = Field(..., min_length=1, max_length=64)
    reviewer: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)
    frame_index: int | None = Field(default=None, ge=0, le=1_000_000_000)
    evidence_ref: str | None = Field(default=None, max_length=512)


async def audit_or_restore(event: str, snapshot: dict[str, list[dict[str, Any]]], **payload: Any) -> None:
    try:
        await run_blocking_io(audit_event, event, **payload)
    except Exception:
        await run_blocking_io(restore_review_state, snapshot)
        raise


@router.get("/v1/evaluation/threshold-recommendations", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_evaluation_threshold_recommendations(
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    thresholds = await run_blocking_io(threshold_snapshot)
    recommendations = await run_blocking_io(review_threshold_recommendations, ctx.tenant_id, thresholds=thresholds)
    return portrait_success(ctx.request_id, {"tenant_id": ctx.tenant_id, "threshold_recommendations": recommendations})


@router.get("/v1/evaluation/datasets", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_evaluation_datasets(
    limit: int = Query(default=20, ge=1, le=MAX_REVIEW_DATASET_LIMIT),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    datasets = await run_blocking_io(list_review_datasets, ctx.tenant_id, limit=limit)
    return portrait_success(ctx.request_id, {"tenant_id": ctx.tenant_id, "datasets": datasets, "count": len(datasets)})


@router.get("/v1/evaluation/track-reviews/summary", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_track_review_summary(
    job_id: str | None = Query(default=None, max_length=512),
    track_id: str | None = Query(default=None, max_length=512),
    label: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=10, ge=1, le=MAX_REVIEW_LIST_LIMIT),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    summary = await run_blocking_io(
        review_annotation_summary,
        ctx.tenant_id,
        job_id=job_id,
        track_id=track_id,
        label=label,
        recent_limit=limit,
    )
    return portrait_success(ctx.request_id, {"tenant_id": ctx.tenant_id, "summary": summary})


@router.get("/v1/evaluation/track-reviews", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_track_review_annotations(
    job_id: str | None = Query(default=None, max_length=512),
    track_id: str | None = Query(default=None, max_length=512),
    label: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=MAX_REVIEW_LIST_LIMIT),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    annotations = await run_blocking_io(
        list_review_annotations,
        ctx.tenant_id,
        job_id=job_id,
        track_id=track_id,
        label=label,
        limit=limit,
    )
    return portrait_success(
        ctx.request_id,
        {"tenant_id": ctx.tenant_id, "annotations": annotations, "count": len(annotations)},
    )


@router.post("/v1/evaluation/track-reviews", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_create_track_review_annotation(
    payload: TrackReviewAnnotationCreateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(review_state_payload)
    annotation = await run_blocking_io(
        create_review_annotation,
        ctx.tenant_id,
        job_id=payload.job_id,
        track_id=payload.track_id,
        label=payload.label,
        reviewer=payload.reviewer,
        note=payload.note,
        frame_index=payload.frame_index,
        evidence_ref=payload.evidence_ref,
    )
    await audit_or_restore(
        "track_review_annotation_created",
        snapshot,
        request_id=ctx.request_id,
        tenant_id=ctx.tenant_id,
        annotation_id=annotation["annotation_id"],
        job_id=annotation["job_id"],
        track_id=annotation["track_id"],
        label=annotation["label"],
    )
    return portrait_success(ctx.request_id, {"annotation": annotation})
