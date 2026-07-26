from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api_contracts import ContractAPIRouter as APIRouter
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_feedback import list_dataset_manifests
from app.portrait_pagination import filter_sort_dict_rows, normalize_list_pagination, page_items_cursor
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.portrait_review import (
    MAX_REVIEW_DATASET_LIMIT,
    MAX_REVIEW_LIST_LIMIT,
    create_review_annotation,
    create_track_correction,
    list_review_annotations,
    list_review_datasets,
    list_track_corrections,
    restore_review_state,
    review_annotation_summary,
    review_state_payload,
    review_threshold_recommendations,
)
from app.portrait_thresholds import threshold_snapshot
from app.security import require_api_token

router = APIRouter(dependencies=[Depends(require_api_token)])


def paginate_review_rows(
    rows: Sequence[dict[str, Any]],
    *,
    limit: int | None,
    offset: int | None,
    cursor: str | None,
    query: str | None,
    search_fields: Sequence[str],
    created_since: float | None,
    created_until: float | None,
    time_field: str,
    sort_by: str,
    sort_order: str,
    id_field: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pagination = normalize_list_pagination(limit, offset, cursor)
    ordered = filter_sort_dict_rows(
        rows,
        search=query,
        search_fields=search_fields,
        created_since=created_since,
        created_until=created_until,
        time_field=time_field,
        sort_by=sort_by,
        sort_order=sort_order,
        id_field=id_field,
    )
    return page_items_cursor(
        ordered,
        limit=pagination.limit,
        offset=pagination.offset,
        cursor=pagination.cursor,
    )


class TrackReviewAnnotationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., min_length=1, max_length=512)
    track_id: str = Field(..., min_length=1, max_length=512)
    label: str = Field(..., min_length=1, max_length=64)
    reviewer: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)
    frame_index: int | None = Field(default=None, ge=0, le=1_000_000_000)
    evidence_ref: str | None = Field(default=None, max_length=512)


class TrackCorrectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., min_length=1, max_length=512)
    action: str = Field(..., pattern="^(merge|split)$")
    track_ids: list[str] = Field(..., min_length=1, max_length=100)
    target_track_id: str | None = Field(default=None, max_length=512)
    split_frame_index: int | None = Field(default=None, ge=0, le=1_000_000_000)
    reviewer: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=2000)
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
    recommendations = await run_blocking_io(review_threshold_recommendations, ctx.scope_id, thresholds=thresholds)
    return portrait_success(ctx.request_id, {"tenant_id": ctx.scope_id, "threshold_recommendations": recommendations})


@router.get("/v1/evaluation/datasets", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_evaluation_datasets(
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^(created_at|name|sample_count)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=20, ge=1, le=MAX_REVIEW_DATASET_LIMIT),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    datasets = await run_blocking_io(list_review_datasets, ctx.scope_id, limit=MAX_REVIEW_DATASET_LIMIT)
    manifests = await run_blocking_io(list_dataset_manifests, ctx.tenant_id, ctx.project_id, limit=None)
    combined = [
        {**item, "_list_time": item.get("created_at", item.get("latest_created_at"))}
        for item in [*manifests, *datasets]
    ]
    page, metadata = paginate_review_rows(
        combined,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("dataset_id", "name", "version", "purpose", "definition_version", "label_schema_version"),
        created_since=created_since,
        created_until=created_until,
        time_field="_list_time",
        sort_by="_list_time" if sort_by == "created_at" else sort_by,
        sort_order=sort_order,
        id_field="dataset_id",
    )
    public_page = [{key: value for key, value in item.items() if key != "_list_time"} for item in page]
    return portrait_success(
        ctx.request_id,
        {"tenant_id": ctx.scope_id, "items": public_page, "datasets": public_page, **metadata},
    )


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
        ctx.scope_id,
        job_id=job_id,
        track_id=track_id,
        label=label,
        recent_limit=limit,
    )
    return portrait_success(ctx.request_id, {"tenant_id": ctx.scope_id, "summary": summary})


@router.get("/v1/evaluation/track-reviews", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_track_review_annotations(
    job_id: str | None = Query(default=None, max_length=512),
    track_id: str | None = Query(default=None, max_length=512),
    label: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^created_at$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=MAX_REVIEW_LIST_LIMIT),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    annotations = await run_blocking_io(
        list_review_annotations,
        ctx.scope_id,
        job_id=job_id,
        track_id=track_id,
        label=label,
        limit=None,
    )
    page, metadata = paginate_review_rows(
        annotations,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("annotation_id", "job_id", "track_id", "label", "reviewer", "note", "source"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="annotation_id",
    )
    return portrait_success(
        ctx.request_id,
        {"tenant_id": ctx.scope_id, "items": page, "annotations": page, **metadata},
    )


@router.post("/v1/evaluation/track-reviews", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_create_track_review_annotation(
    payload: TrackReviewAnnotationCreateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(review_state_payload)
    annotation = await run_blocking_io(
        create_review_annotation,
        ctx.scope_id,
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
        tenant_id=ctx.scope_id,
        annotation_id=annotation["annotation_id"],
        job_id=annotation["job_id"],
        track_id=annotation["track_id"],
        label=annotation["label"],
    )
    return portrait_success(ctx.request_id, {"annotation": annotation})


@router.get("/v1/evaluation/track-corrections", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_track_corrections(
    job_id: str | None = Query(default=None, max_length=512),
    action: str | None = Query(default=None, pattern="^(merge|split)$"),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^created_at$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=MAX_REVIEW_LIST_LIMIT),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    corrections = await run_blocking_io(
        list_track_corrections,
        ctx.scope_id,
        job_id=job_id,
        action=action,
        limit=None,
    )
    page, metadata = paginate_review_rows(
        corrections,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("correction_id", "job_id", "action", "track_ids", "target_track_id", "reviewer", "reason"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="correction_id",
    )
    return portrait_success(
        ctx.request_id,
        {"tenant_id": ctx.scope_id, "items": page, "corrections": page, **metadata},
    )


@router.post("/v1/evaluation/track-corrections", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_create_track_correction(
    payload: TrackCorrectionCreateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(review_state_payload)
    correction = await run_blocking_io(
        create_track_correction,
        ctx.scope_id,
        **payload.model_dump(),
    )
    await audit_or_restore(
        "track_correction_created",
        snapshot,
        request_id=ctx.request_id,
        tenant_id=ctx.scope_id,
        correction_id=correction["correction_id"],
        job_id=correction["job_id"],
        action=correction["action"],
        track_count=len(correction["track_ids"]),
    )
    return portrait_success(ctx.request_id, {"correction": correction})
