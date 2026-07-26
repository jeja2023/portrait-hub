from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api_contracts import ContractAPIRouter as APIRouter
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency, require_step_up_authentication
from app.portrait_commercial import require_compliance_control
from app.portrait_feedback import (
    create_dataset_manifest,
    create_feedback_analysis_report,
    create_review_sample,
    export_review_samples,
    feedback_state_payload,
    get_dataset_manifest,
    get_feedback_analysis_report,
    import_annotations,
    list_feedback_analysis_reports,
    list_review_samples,
    restore_feedback_state,
)
from app.portrait_pagination import filter_sort_dict_rows, normalize_list_pagination, page_items_cursor
from app.portrait_projects import identity_claims_from_request
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.security import require_api_token

router = APIRouter(dependencies=[Depends(require_api_token)])


def paginate_feedback_rows(
    rows: Sequence[dict[str, Any]],
    *,
    limit: int | None,
    offset: int | None,
    cursor: str | None,
    query: str | None,
    created_since: float | None,
    created_until: float | None,
    sort_by: str,
    sort_order: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pagination = normalize_list_pagination(limit, offset, cursor)
    ordered = filter_sort_dict_rows(
        rows,
        search=query,
        search_fields=(
            "review_sample_id",
            "source_request_id",
            "source_type",
            "source_item_id",
            "reason",
            "risk_level",
            "model_id",
            "model_version_id",
            "status",
            "tags",
        ),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="review_sample_id",
    )
    return page_items_cursor(
        ordered,
        limit=pagination.limit,
        offset=pagination.offset,
        cursor=pagination.cursor,
    )


def actor_from_request(request: Request) -> str:
    claims = identity_claims_from_request(request) or {}
    for key in ("sub", "email", "preferred_username", "name"):
        value = str(claims.get(key) or "").strip()
        if value:
            return value[:256]
    return "platform-api"


async def audit_or_restore(
    event: str,
    snapshot: dict[str, Any],
    ctx: PortraitRequestContext,
    *,
    actor: str,
    **fields: Any,
) -> None:
    try:
        await run_blocking_io(
            audit_event,
            event,
            request_id=ctx.request_id,
            tenant_id=ctx.tenant_id,
            project_id=ctx.project_id,
            actor=actor,
            **fields,
        )
    except Exception:
        await run_blocking_io(restore_feedback_state, snapshot)
        raise


class ReviewSampleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    source_request_id: str | None = Field(default=None, max_length=128)
    source_type: str = Field(default="inference", max_length=64)
    source_item_id: str = Field(..., min_length=1, max_length=512)
    reason: str = Field(..., min_length=1, max_length=64)
    priority: int = Field(default=50, ge=0, le=100)
    risk_level: str = Field(default="medium", max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    model_id: str = Field(default="", max_length=256)
    model_version_id: str = Field(..., min_length=1, max_length=256)
    model_sha256: str = Field(default="", max_length=64)
    contract_version: str = Field(default="1.0", max_length=64)
    object_ref: str = Field(default="", max_length=2000)
    masked_preview_ref: str = Field(default="", max_length=2000)
    content_sha256: str = Field(default="", max_length=64)
    proposed_labels: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=100)
    retention_policy_id: str | None = Field(default=None, max_length=128)
    expires_at: float | None = Field(default=None, ge=0)


class ReviewSampleExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_ids: list[str] = Field(..., min_length=1, max_length=1000)
    format: str = Field(default="label_studio", max_length=32)
    schema_version: str = Field(default="1.0", max_length=64)
    external_task_id: str | None = Field(default=None, max_length=256)


class AnnotationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_sample_id: str = Field(..., min_length=1, max_length=128)
    labels: dict[str, Any] = Field(..., min_length=1)


class AnnotationImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotation_export_id: str = Field(..., min_length=1, max_length=128)
    schema_version: str = Field(default="1.0", max_length=64)
    annotations: list[AnnotationItem] = Field(..., min_length=1, max_length=1000)
    conflict_policy: str = Field(default="reject", max_length=32)


class DatasetManifestCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=256)
    version: str = Field(..., min_length=1, max_length=128)
    definition_version: str = Field(default="1.0", max_length=64)
    label_schema_version: str = Field(default="1.0", max_length=64)
    splits: dict[str, list[str]] = Field(..., min_length=1)
    lineage: list[str] = Field(default_factory=list, max_length=100)


class FeedbackAnalysisReportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str = Field(..., min_length=1, max_length=256)
    version: str = Field(..., min_length=1, max_length=128)
    definition_version: str = Field(default="feedback-analysis-v1", max_length=64)
    dataset_ids: list[str] = Field(..., min_length=1, max_length=100)
    label_key: str = Field(..., min_length=1, max_length=256)
    positive_value: Any = True
    baseline_model_version_id: str = Field(..., min_length=1, max_length=256)
    candidate_model_version_id: str = Field(..., min_length=1, max_length=256)
    current_threshold: float = Field(default=0.5, ge=0, le=1)
    threshold_candidates: list[float] = Field(
        default_factory=lambda: [0.3, 0.4, 0.5, 0.6, 0.7],
        min_length=1,
        max_length=101,
    )
    minimum_sample_count: int = Field(default=20, ge=1, le=1_000_000)
    minimum_accuracy: float = Field(default=0.8, ge=0, le=1)
    minimum_f1: float = Field(default=0.8, ge=0, le=1)
    maximum_accuracy_regression: float = Field(default=0.02, ge=0, le=1)
    maximum_f1_regression: float = Field(default=0.02, ge=0, le=1)


@router.get(
    "/v1/evaluation/review-samples",
    dependencies=[Depends(permission_dependency("datasets:read"))],
)
async def v1_review_samples(
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    reason: str | None = Query(default=None, max_length=64),
    risk_level: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(
        default="selection_score", pattern="^(selection_score|created_at|priority|confidence|status)$"
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(
        list_review_samples,
        ctx.tenant_id,
        ctx.project_id,
        status_filter=status_filter,
        reason=reason,
        risk_level=risk_level,
        limit=None,
    )
    page, metadata = paginate_feedback_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        created_since=created_since,
        created_until=created_until,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return portrait_success(ctx.request_id, {"items": page, "review_samples": page, **metadata})


@router.post(
    "/v1/evaluation/review-samples",
    dependencies=[Depends(permission_dependency("datasets:write"))],
)
async def v1_create_review_sample(
    payload: ReviewSampleCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(feedback_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_review_sample,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
        request_id=ctx.request_id,
    )
    await audit_or_restore(
        "review_sample_created",
        snapshot,
        ctx,
        actor=actor,
        review_sample_id=record["review_sample_id"],
        reason=record["reason"],
        model_version_id=record["model_version_id"],
        risk_level=record["risk_level"],
    )
    return portrait_success(ctx.request_id, {"review_sample": record})


@router.post(
    "/v1/evaluation/review-samples/export",
    dependencies=[
        Depends(permission_dependency("datasets:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_export_review_samples(
    payload: ReviewSampleExportRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    await run_blocking_io(require_compliance_control, ctx.tenant_id, ctx.project_id, "COM-006")
    snapshot = await run_blocking_io(feedback_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        export_review_samples,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
        request_id=ctx.request_id,
    )
    await audit_or_restore(
        "review_samples_exported",
        snapshot,
        ctx,
        actor=actor,
        annotation_export_id=record["annotation_export_id"],
        sample_count=record["sample_count"],
        export_format=record["format"],
        export_sha256=record["sha256"],
    )
    return portrait_success(ctx.request_id, {"annotation_export": record})


@router.post(
    "/v1/evaluation/review-samples/import",
    dependencies=[Depends(permission_dependency("datasets:write"))],
)
async def v1_import_review_annotations(
    payload: AnnotationImportRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(feedback_state_payload)
    actor = actor_from_request(request)
    values = payload.model_dump()
    record = await run_blocking_io(
        import_annotations,
        ctx.tenant_id,
        ctx.project_id,
        values,
        actor=actor,
        request_id=ctx.request_id,
    )
    await audit_or_restore(
        "review_annotations_imported",
        snapshot,
        ctx,
        actor=actor,
        annotation_import_id=record["annotation_import_id"],
        annotation_export_id=record["annotation_export_id"],
        applied_count=record["applied_count"],
        conflict_count=record["conflict_count"],
    )
    return portrait_success(ctx.request_id, {"annotation_import": record})


@router.post(
    "/v1/evaluation/datasets",
    dependencies=[Depends(permission_dependency("datasets:write"))],
)
async def v1_create_dataset_manifest(
    payload: DatasetManifestCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(feedback_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_dataset_manifest,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
        request_id=ctx.request_id,
    )
    await audit_or_restore(
        "dataset_manifest_created",
        snapshot,
        ctx,
        actor=actor,
        dataset_id=record["dataset_id"],
        dataset_version=record["version"],
        sample_count=record["sample_count"],
        manifest_sha256=record["sha256"],
    )
    return portrait_success(ctx.request_id, {"dataset_manifest": record})


@router.get(
    "/v1/evaluation/datasets/{dataset_id}/manifest",
    dependencies=[Depends(permission_dependency("datasets:read"))],
)
async def v1_dataset_manifest(
    dataset_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    record = await run_blocking_io(get_dataset_manifest, ctx.tenant_id, ctx.project_id, dataset_id)
    return portrait_success(ctx.request_id, {"dataset_manifest": record})


@router.get(
    "/v1/evaluation/feedback-analysis-reports",
    dependencies=[Depends(permission_dependency("datasets:read"))],
)
async def v1_feedback_analysis_reports(
    limit: int = Query(default=100, ge=1, le=500),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    reports = await run_blocking_io(
        list_feedback_analysis_reports,
        ctx.tenant_id,
        ctx.project_id,
        limit=limit,
    )
    return portrait_success(
        ctx.request_id,
        {"items": reports, "analysis_reports": reports, "count": len(reports)},
    )


@router.post(
    "/v1/evaluation/feedback-analysis-reports",
    dependencies=[Depends(permission_dependency("datasets:write"))],
)
async def v1_create_feedback_analysis_report(
    payload: FeedbackAnalysisReportCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(feedback_state_payload)
    actor = actor_from_request(request)
    report = await run_blocking_io(
        create_feedback_analysis_report,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
        request_id=ctx.request_id,
    )
    await audit_or_restore(
        "feedback_analysis_report_created",
        snapshot,
        ctx,
        actor=actor,
        analysis_report_id=report["analysis_report_id"],
        analysis_version=report["version"],
        report_status=report["status"],
        report_sha256=report["sha256"],
        dataset_ids=report["parameters"].get("dataset_ids", payload.dataset_ids),
    )
    return portrait_success(ctx.request_id, {"analysis_report": report})


@router.get(
    "/v1/evaluation/feedback-analysis-reports/{analysis_report_id}",
    dependencies=[Depends(permission_dependency("datasets:read"))],
)
async def v1_feedback_analysis_report(
    analysis_report_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    report = await run_blocking_io(
        get_feedback_analysis_report,
        ctx.tenant_id,
        ctx.project_id,
        analysis_report_id,
    )
    return portrait_success(ctx.request_id, {"analysis_report": report})


__all__ = ["router"]
