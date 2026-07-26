from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api_contracts import ContractAPIRouter as APIRouter
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency, require_step_up_authentication
from app.portrait_commercial import (
    apply_industry_template,
    change_entitlement_status,
    commercial_state_payload,
    compliance_status,
    compute_sla_report,
    create_entitlement,
    create_incident,
    create_rights_request,
    create_support_case,
    get_commercial_profile,
    health_timeline,
    list_entitlements,
    list_evidence_packages,
    list_incidents,
    list_industry_templates,
    list_rights_requests,
    list_sla_definitions,
    list_sla_reports,
    list_support_cases,
    list_template_applications,
    preview_template,
    quota_forecast,
    restore_commercial_state,
    rollback_industry_template,
    update_commercial_profile,
    update_incident,
    update_rights_request,
    update_support_case,
    upsert_compliance_record,
    upsert_sla_definition,
    usage_summary,
    usage_timeseries,
)
from app.portrait_commercial_license import public_license_status
from app.portrait_metering import (
    create_cost_model,
    list_cost_models,
    list_usage_events,
    metering_state_payload,
    restore_metering_state,
    reverse_usage_event,
)
from app.portrait_pagination import (
    filter_sort_dict_rows,
    normalize_list_pagination,
    page_items_cursor,
)
from app.portrait_projects import identity_claims_from_request
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.security import require_api_token

router = APIRouter(dependencies=[Depends(require_api_token)])


def paginate_control_rows(
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


def actor_from_request(request: Request) -> str:
    claims = identity_claims_from_request(request) or {}
    for key in ("sub", "email", "preferred_username", "name"):
        value = str(claims.get(key) or "").strip()
        if value:
            return value[:256]
    application = str(request.headers.get("x-application-id") or "").strip()
    return application[:256] if application else "platform-api"


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
        await run_blocking_io(restore_commercial_state, snapshot)
        raise


async def audit_metering_or_restore(
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
        await run_blocking_io(restore_metering_state, snapshot)
        raise


class CommercialProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commercial_status: str | None = Field(default=None, max_length=32)
    delivery_tier: str | None = Field(default=None, max_length=64)
    environment: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
    budget_limit: float | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)
    retention_policy_id: str | None = Field(default=None, max_length=128)
    notification_channels: list[str] | None = Field(default=None, max_length=32)
    effective_at: float | None = Field(default=None, ge=0)
    expires_at: float | None = Field(default=None, ge=0)
    expected_version: int | None = Field(default=None, ge=1)
    cancel_scheduled_transition: bool = False
    reason: str = Field(default="profile update", min_length=1, max_length=1000)
    approved_by: str | None = Field(default=None, max_length=256)


class EntitlementCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_version: str = Field(default="1.0", min_length=1, max_length=64)
    product_version: str = Field(default="1.0", min_length=1, max_length=64)
    delivery_tier: str = Field(default="platform_api", min_length=1, max_length=64)
    allowed_capabilities: list[str] = Field(..., min_length=1, max_length=128)
    allowed_models: list[str] = Field(default_factory=list, max_length=128)
    project_limit: int = Field(default=1, ge=1, le=100_000)
    concurrency_limit: int = Field(default=1, ge=1, le=1_000_000)
    stream_limit: int = Field(default=0, ge=0, le=1_000_000)
    support_level: str = Field(default="standard", min_length=1, max_length=64)
    starts_at: float | None = Field(default=None, ge=0)
    expires_at: float | None = Field(default=None, ge=0)
    grace_period_seconds: int = Field(default=0, ge=0, le=31_536_000)
    change_type: str | None = Field(
        default=None,
        pattern="^(new|renewal|upgrade|downgrade|temporary_expansion|emergency)$",
    )
    reason: str = Field(default="entitlement version change", min_length=1, max_length=1000)
    rollback_target_id: str | None = Field(default=None, max_length=128)
    expected_current_entitlement_id: str | None = Field(default=None, max_length=128)
    approved_by: str = Field(..., min_length=1, max_length=256)


class EntitlementActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., pattern="^(cancel|revoke|rollback)$")
    reason: str = Field(..., min_length=1, max_length=1000)
    approved_by: str = Field(..., min_length=1, max_length=256)
    expected_version: int | None = Field(default=None, ge=1)
    expected_current_entitlement_id: str | None = Field(default=None, max_length=128)


class CostModelCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., min_length=1, max_length=64)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    effective_at: float | None = Field(default=None, ge=0)
    request_unit_cost: float = Field(default=0, ge=0)
    image_unit_cost: float = Field(default=0, ge=0)
    video_second_cost: float = Field(default=0, ge=0)
    gpu_second_cost: float = Field(default=0, ge=0)
    storage_gb_month_cost: float = Field(default=0, ge=0)
    network_gb_cost: float = Field(default=0, ge=0)
    third_party_unit_cost: float = Field(default=0, ge=0)
    reason: str = Field(..., min_length=1, max_length=1000)
    approved_by: str = Field(..., min_length=1, max_length=256)


class UsageReversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class SLADefinitionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_version: str = Field(default="1.0", min_length=1, max_length=64)
    availability_target: float = Field(default=0.995, gt=0, le=1)
    p95_latency_ms: int = Field(default=2000, ge=1, le=3_600_000)
    p99_latency_ms: int = Field(default=5000, ge=1, le=3_600_000)
    window_seconds: int = Field(default=2_592_000, ge=60, le=31_536_000)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    exclusion_rules: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    effective_at: float | None = Field(default=None, ge=0)
    expires_at: float | None = Field(default=None, ge=0)


class SLAReportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_since: float = Field(..., ge=0)
    created_until: float = Field(default_factory=time.time, ge=0)


class IncidentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=256)
    severity: str = Field(default="sev3", max_length=16)
    impact_scope: str = Field(..., min_length=1, max_length=1000)
    customer_visible_summary: str = Field(default="", max_length=4000)
    internal_summary: str = Field(default="", max_length=8000)
    started_at: float | None = Field(default=None, ge=0)
    owner: str | None = Field(default=None, max_length=256)
    related_request_ids: list[str] = Field(default_factory=list, max_length=100)
    related_model_versions: list[str] = Field(default_factory=list, max_length=100)


class IncidentPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None, max_length=32)
    severity: str | None = Field(default=None, max_length=16)
    impact_scope: str | None = Field(default=None, max_length=1000)
    customer_visible_summary: str | None = Field(default=None, max_length=4000)
    internal_summary: str | None = Field(default=None, max_length=8000)
    owner: str | None = Field(default=None, max_length=256)
    root_cause: str | None = Field(default=None, max_length=8000)
    action_items: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    timeline_message: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class ComplianceRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="draft", max_length=32)
    definition_version: str = Field(default="1.0", min_length=1, max_length=64)
    applicability: str = Field(default="pending", max_length=64)
    legal_basis: str = Field(default="", max_length=2000)
    processing_purpose: str = Field(default="", max_length=2000)
    data_categories: list[str] = Field(default_factory=list, max_length=100)
    data_subjects: list[str] = Field(default_factory=list, max_length=100)
    storage_regions: list[str] = Field(default_factory=list, max_length=100)
    retention: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    risk_summary: str = Field(default="", max_length=4000)
    mitigations: list[str] = Field(default_factory=list, max_length=100)
    control_data: dict[str, Any] = Field(default_factory=dict)
    approved_by: str | None = Field(default=None, max_length=256)
    expires_at: float | None = Field(default=None, ge=0)


class RightsRequestCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: str = Field(..., min_length=1, max_length=32)
    subject_reference: str = Field(..., min_length=1, max_length=2000)
    due_at: float | None = Field(default=None, ge=0)


class RightsRequestPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., min_length=1, max_length=32)
    identity_verification: str | None = Field(default=None, max_length=64)
    exception_basis: str | None = Field(default=None, max_length=4000)
    execution_evidence: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    timeline_message: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class IndustryTemplateApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_fingerprint: str = Field(..., min_length=64, max_length=64)
    dry_run: bool = True


class IndustryTemplateRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class SupportCaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=256)
    description: str = Field(..., min_length=1, max_length=8000)
    severity: str = Field(default="sev3", max_length=16)
    environment: str = Field(..., min_length=1, max_length=128)
    product_version: str = Field(..., min_length=1, max_length=64)
    request_ids: list[str] = Field(default_factory=list, max_length=100)
    task_ids: list[str] = Field(default_factory=list, max_length=100)
    owner: str | None = Field(default=None, max_length=256)
    response_due_at: float | None = Field(default=None, ge=0)
    redacted_attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class SupportCasePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None, max_length=32)
    severity: str | None = Field(default=None, max_length=16)
    description: str | None = Field(default=None, max_length=8000)
    owner: str | None = Field(default=None, max_length=256)
    response_due_at: float | None = Field(default=None, ge=0)
    request_ids: list[str] | None = Field(default=None, max_length=100)
    task_ids: list[str] | None = Field(default=None, max_length=100)
    redacted_attachments: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    expected_version: int | None = Field(default=None, ge=1)


@router.get(
    "/v1/access/projects/{project_id}/commercial-profile",
    dependencies=[Depends(permission_dependency("commercial:read"))],
)
async def v1_commercial_profile(
    project_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    profile = await run_blocking_io(get_commercial_profile, ctx.tenant_id, project_id)
    return portrait_success(ctx.request_id, {"commercial_profile": profile})


@router.patch(
    "/v1/access/projects/{project_id}/commercial-profile",
    dependencies=[
        Depends(permission_dependency("commercial:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_update_commercial_profile(
    project_id: str,
    payload: CommercialProfilePatchRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    values = payload.model_dump(
        exclude={"reason", "approved_by", "expected_version", "cancel_scheduled_transition"},
        exclude_none=True,
    )
    profile = await run_blocking_io(
        update_commercial_profile,
        ctx.tenant_id,
        project_id,
        values,
        actor=actor,
        approved_by=payload.approved_by,
        reason=payload.reason,
        expected_version=payload.expected_version,
        cancel_scheduled_transition=payload.cancel_scheduled_transition,
    )
    await audit_or_restore(
        "commercial_profile_updated",
        snapshot,
        ctx,
        actor=actor,
        target_project_id=project_id,
        profile_version=profile["version"],
        commercial_status=profile["commercial_status"],
        scheduled_transition=profile.get("scheduled_transition"),
        approved_by=payload.approved_by,
        reason=payload.reason,
    )
    return portrait_success(ctx.request_id, {"commercial_profile": profile})


@router.get("/v1/access/entitlements", dependencies=[Depends(permission_dependency("commercial:read"))])
async def v1_entitlements(
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^(created_at|version|starts_at|expires_at)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_entitlements, ctx.tenant_id, ctx.project_id, limit=None)
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("definition_version", "product_version", "delivery_tier", "support_level", "status"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="entitlement_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "entitlements": page, **metadata})


@router.post(
    "/v1/access/entitlements",
    dependencies=[Depends(permission_dependency("commercial:write")), Depends(require_step_up_authentication)],
)
async def v1_create_entitlement(
    payload: EntitlementCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_entitlement,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(exclude={"approved_by"}),
        actor=actor,
        approved_by=payload.approved_by,
    )
    await audit_or_restore(
        "entitlement_created",
        snapshot,
        ctx,
        actor=actor,
        entitlement_id=record["entitlement_id"],
        entitlement_version=record["version"],
        entitlement_status=record["status"],
        change_type=record["change_type"],
        rollback_target_id=record["rollback_target_id"],
        approved_by=payload.approved_by,
        reason=payload.reason,
    )
    return portrait_success(ctx.request_id, {"entitlement": record})


@router.post(
    "/v1/access/entitlements/{entitlement_id}/actions",
    dependencies=[Depends(permission_dependency("commercial:write")), Depends(require_step_up_authentication)],
)
async def v1_change_entitlement_status(
    entitlement_id: str,
    payload: EntitlementActionRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    result = await run_blocking_io(
        change_entitlement_status,
        ctx.tenant_id,
        ctx.project_id,
        entitlement_id,
        payload.action,
        actor=actor,
        approved_by=payload.approved_by,
        reason=payload.reason,
        expected_version=payload.expected_version,
        expected_current_entitlement_id=payload.expected_current_entitlement_id,
    )
    await audit_or_restore(
        f"entitlement_{payload.action}",
        snapshot,
        ctx,
        actor=actor,
        entitlement_id=entitlement_id,
        entitlement_status=result["entitlement"]["status"],
        approved_by=payload.approved_by,
        reason=payload.reason,
    )
    return portrait_success(ctx.request_id, result)


@router.get("/v1/access/usage/events", dependencies=[Depends(permission_dependency("commercial:read"))])
async def v1_usage_events(
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    q: str | None = Query(default=None, max_length=256),
    sort_by: str = Query(default="event_time", pattern="^(event_time|received_at)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(
        list_usage_events,
        ctx.tenant_id,
        ctx.project_id,
        created_since=created_since,
        created_until=created_until,
    )
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("usage_event_id", "request_id", "endpoint", "model_version", "capability"),
        created_since=None,
        created_until=None,
        time_field="event_time",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="usage_event_id",
    )
    return portrait_success(
        ctx.request_id,
        {"items": page, "usage_events": page, "definition_version": "1.0", **metadata},
    )


@router.post(
    "/v1/access/usage/events/{usage_event_id}/reversal",
    dependencies=[
        Depends(permission_dependency("commercial:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_reverse_usage_event(
    usage_event_id: str,
    payload: UsageReversalRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(metering_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        reverse_usage_event,
        ctx.tenant_id,
        ctx.project_id,
        usage_event_id,
        actor=actor,
        reason=payload.reason,
    )
    await audit_metering_or_restore(
        "usage_event_reversed",
        snapshot,
        ctx,
        actor=actor,
        usage_event_id=usage_event_id,
        reversal_event_id=record["usage_event_id"],
        reason=payload.reason,
    )
    return portrait_success(ctx.request_id, {"usage_reversal": record})


@router.get("/v1/access/cost-models", dependencies=[Depends(permission_dependency("commercial:read"))])
async def v1_cost_models(ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    rows = await run_blocking_io(list_cost_models, ctx.tenant_id, ctx.project_id)
    return portrait_success(
        ctx.request_id,
        {"items": rows, "cost_models": rows, "count": len(rows), "definition_version": "1.0"},
    )


@router.post(
    "/v1/access/cost-models",
    dependencies=[
        Depends(permission_dependency("commercial:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_create_cost_model(
    payload: CostModelCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(metering_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_cost_model,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
    )
    await audit_metering_or_restore(
        "cost_model_created",
        snapshot,
        ctx,
        actor=actor,
        cost_model_id=record["cost_model_id"],
        version=record["version"],
        approved_by=record["approved_by"],
        model_sha256=record["model_sha256"],
    )
    return portrait_success(ctx.request_id, {"cost_model": record})


@router.get("/v1/access/usage/summary", dependencies=[Depends(permission_dependency("commercial:read"))])
async def v1_usage_summary(
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    summary = await run_blocking_io(
        usage_summary,
        ctx.tenant_id,
        ctx.project_id,
        created_since=created_since,
        created_until=created_until,
    )
    return portrait_success(ctx.request_id, {"usage_summary": summary})


@router.get("/v1/access/usage/timeseries", dependencies=[Depends(permission_dependency("commercial:read"))])
async def v1_usage_timeseries(
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    timezone: str = Query(default="UTC", min_length=1, max_length=64),
    granularity: str = Query(default="day", pattern="^(day|month)$"),
    sort_by: str = Query(default="date", pattern="^date$"),
    sort_order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    points = await run_blocking_io(
        usage_timeseries,
        ctx.tenant_id,
        ctx.project_id,
        created_since=created_since,
        created_until=created_until,
        timezone=timezone,
        granularity=granularity,
    )
    page, metadata = paginate_control_rows(
        points,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=None,
        search_fields=(),
        created_since=None,
        created_until=None,
        time_field="date",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="date",
    )
    return portrait_success(
        ctx.request_id,
        {
            "items": page,
            "timeseries": page,
            "definition_version": "1.0",
            "timezone": timezone,
            "granularity": granularity,
            **metadata,
        },
    )


@router.get("/v1/access/quota/forecast", dependencies=[Depends(permission_dependency("commercial:read"))])
async def v1_quota_forecast(ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    forecast = await run_blocking_io(quota_forecast, ctx.tenant_id, ctx.project_id)
    return portrait_success(ctx.request_id, {"quota_forecast": forecast})


@router.get("/v1/access/license/status", dependencies=[Depends(permission_dependency("commercial:read"))])
async def v1_commercial_license_status(
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    result = await run_blocking_io(public_license_status)
    return portrait_success(ctx.request_id, {"commercial_license": result})


@router.get("/v1/access/support/cases", dependencies=[Depends(permission_dependency("support:read"))])
async def v1_support_cases(
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|severity|status)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(
        list_support_cases,
        ctx.tenant_id,
        ctx.project_id,
        status_filter=status_filter,
        limit=None,
    )
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=(
            "support_case_id",
            "title",
            "description",
            "severity",
            "status",
            "environment",
            "product_version",
            "owner",
        ),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="support_case_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "support_cases": page, **metadata})


@router.post("/v1/access/support/cases", dependencies=[Depends(permission_dependency("support:write"))])
async def v1_create_support_case(
    payload: SupportCaseCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_support_case,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
        request_id=ctx.request_id,
    )
    await audit_or_restore(
        "support_case_created",
        snapshot,
        ctx,
        actor=actor,
        support_case_id=record["support_case_id"],
        severity=record["severity"],
    )
    return portrait_success(ctx.request_id, {"support_case": record})


@router.patch(
    "/v1/access/support/cases/{support_case_id}",
    dependencies=[Depends(permission_dependency("support:write"))],
)
async def v1_update_support_case(
    support_case_id: str,
    payload: SupportCasePatchRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        update_support_case,
        ctx.tenant_id,
        ctx.project_id,
        support_case_id,
        payload.model_dump(exclude={"expected_version"}, exclude_none=True),
        actor=actor,
        expected_version=payload.expected_version,
    )
    await audit_or_restore(
        "support_case_updated",
        snapshot,
        ctx,
        actor=actor,
        support_case_id=record["support_case_id"],
        support_status=record["status"],
        support_version=record["version"],
    )
    return portrait_success(ctx.request_id, {"support_case": record})


@router.get("/v1/admin/operations/sla", dependencies=[Depends(permission_dependency("operations:read"))])
async def v1_sla_definitions(
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="effective_at", pattern="^(created_at|effective_at|expires_at|availability_target)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_sla_definitions, ctx.tenant_id, ctx.project_id, limit=None)
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("sla_definition_id", "definition_version", "timezone"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="sla_definition_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "sla_definitions": page, **metadata})


@router.post("/v1/admin/operations/sla", dependencies=[Depends(permission_dependency("operations:write"))])
async def v1_create_sla_definition(
    payload: SLADefinitionCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        upsert_sla_definition,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
    )
    await audit_or_restore(
        "sla_definition_created",
        snapshot,
        ctx,
        actor=actor,
        sla_definition_id=record["sla_definition_id"],
        definition_version=record["definition_version"],
    )
    return portrait_success(ctx.request_id, {"sla_definition": record})


@router.get("/v1/admin/operations/sla/reports", dependencies=[Depends(permission_dependency("operations:read"))])
async def v1_sla_reports(
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^(created_at|availability|request_count|error_count)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_sla_reports, ctx.tenant_id, ctx.project_id, limit=None)
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("sla_report_id", "definition_version"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="sla_report_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "sla_reports": page, **metadata})


@router.post("/v1/admin/operations/sla/reports", dependencies=[Depends(permission_dependency("operations:write"))])
async def v1_create_sla_report(
    payload: SLAReportCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    report = await run_blocking_io(
        compute_sla_report,
        ctx.tenant_id,
        ctx.project_id,
        created_since=payload.created_since,
        created_until=payload.created_until,
        actor=actor,
    )
    await audit_or_restore(
        "sla_report_created",
        snapshot,
        ctx,
        actor=actor,
        sla_report_id=report["sla_report_id"],
        definition_version=report["definition_version"],
        met=report["met"],
    )
    return portrait_success(ctx.request_id, {"sla_report": report})


@router.get("/v1/admin/operations/incidents", dependencies=[Depends(permission_dependency("operations:read"))])
async def v1_incidents(
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    severity: str | None = Query(default=None, max_length=16),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="started_at", pattern="^(started_at|created_at|updated_at|severity|status)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(
        list_incidents,
        ctx.tenant_id,
        ctx.project_id,
        status_filter=status_filter,
        limit=None,
    )
    if severity is not None:
        rows = [item for item in rows if item.get("severity") == severity]
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("incident_id", "incident_number", "title", "impact_scope", "severity", "status", "owner"),
        created_since=created_since,
        created_until=created_until,
        time_field="started_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="incident_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "incidents": page, **metadata})


@router.post("/v1/admin/operations/incidents", dependencies=[Depends(permission_dependency("operations:write"))])
async def v1_create_incident(
    payload: IncidentCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_incident,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
        request_id=ctx.request_id,
    )
    await audit_or_restore(
        "incident_created",
        snapshot,
        ctx,
        actor=actor,
        incident_id=record["incident_id"],
        severity=record["severity"],
    )
    return portrait_success(ctx.request_id, {"incident": record})


@router.patch(
    "/v1/admin/operations/incidents/{incident_id}",
    dependencies=[Depends(permission_dependency("operations:write"))],
)
async def v1_update_incident(
    incident_id: str,
    payload: IncidentPatchRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        update_incident,
        ctx.tenant_id,
        ctx.project_id,
        incident_id,
        payload.model_dump(exclude={"expected_version"}, exclude_none=True),
        actor=actor,
        expected_version=payload.expected_version,
    )
    await audit_or_restore(
        "incident_updated",
        snapshot,
        ctx,
        actor=actor,
        incident_id=record["incident_id"],
        incident_status=record["status"],
        incident_version=record["version"],
    )
    return portrait_success(ctx.request_id, {"incident": record})


@router.get(
    "/v1/admin/operations/health-timeline",
    dependencies=[Depends(permission_dependency("operations:read"))],
)
async def v1_health_timeline(
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="at", pattern="^at$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(health_timeline, ctx.tenant_id, ctx.project_id, limit=None)
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("source_id", "severity", "status", "message", "type"),
        created_since=created_since,
        created_until=created_until,
        time_field="at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="source_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "events": page, **metadata})


@router.get("/v1/admin/compliance/status", dependencies=[Depends(permission_dependency("compliance:read"))])
async def v1_compliance_status(ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    result = await run_blocking_io(compliance_status, ctx.tenant_id, ctx.project_id)
    return portrait_success(ctx.request_id, {"compliance": result})


@router.put(
    "/v1/admin/compliance/records/{control_id}",
    dependencies=[
        Depends(permission_dependency("compliance:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_upsert_compliance_record(
    control_id: str,
    payload: ComplianceRecordRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        upsert_compliance_record,
        ctx.tenant_id,
        ctx.project_id,
        control_id,
        payload.model_dump(exclude={"approved_by"}),
        actor=actor,
        approved_by=payload.approved_by,
    )
    await audit_or_restore(
        "compliance_record_updated",
        snapshot,
        ctx,
        actor=actor,
        control_id=record["control_id"],
        compliance_status=record["status"],
        record_version=record["version"],
        approved_by=payload.approved_by,
    )
    return portrait_success(ctx.request_id, {"compliance_record": record})


@router.get("/v1/admin/compliance/rights-requests", dependencies=[Depends(permission_dependency("compliance:read"))])
async def v1_rights_requests(
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    request_type: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|due_at|status)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_rights_requests, ctx.tenant_id, ctx.project_id, limit=None)
    if status_filter is not None:
        rows = [item for item in rows if item.get("status") == status_filter]
    if request_type is not None:
        rows = [item for item in rows if item.get("request_type") == request_type]
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("rights_request_id", "request_type", "status", "subject_reference", "identity_verification"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="rights_request_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "rights_requests": page, **metadata})


@router.post(
    "/v1/admin/compliance/rights-requests",
    dependencies=[
        Depends(permission_dependency("compliance:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_create_rights_request(
    payload: RightsRequestCreateRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_rights_request,
        ctx.tenant_id,
        ctx.project_id,
        payload.model_dump(),
        actor=actor,
    )
    await audit_or_restore(
        "rights_request_created",
        snapshot,
        ctx,
        actor=actor,
        rights_request_id=record["rights_request_id"],
        request_type=record["request_type"],
    )
    return portrait_success(ctx.request_id, {"rights_request": record})


@router.patch(
    "/v1/admin/compliance/rights-requests/{rights_request_id}",
    dependencies=[
        Depends(permission_dependency("compliance:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_update_rights_request(
    rights_request_id: str,
    payload: RightsRequestPatchRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        update_rights_request,
        ctx.tenant_id,
        ctx.project_id,
        rights_request_id,
        payload.model_dump(exclude={"expected_version"}, exclude_none=True),
        actor=actor,
        expected_version=payload.expected_version,
    )
    await audit_or_restore(
        "rights_request_updated",
        snapshot,
        ctx,
        actor=actor,
        rights_request_id=rights_request_id,
        rights_request_status=record["status"],
        rights_request_version=record["version"],
    )
    return portrait_success(ctx.request_id, {"rights_request": record})


@router.get("/v1/admin/evidence", dependencies=[Depends(permission_dependency("evidence:read"))])
async def v1_evidence_packages(
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^created_at$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_evidence_packages, ctx.tenant_id, ctx.project_id, limit=None)
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("package_id", "evidence_package_id", "release_id", "status", "environment"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="package_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "evidence_packages": page, **metadata})


@router.get("/v1/admin/industry-templates", dependencies=[Depends(permission_dependency("commercial:read"))])
async def v1_industry_templates(
    q: str | None = Query(default=None, max_length=256),
    sort_by: str = Query(default="name", pattern="^(name|template_id|version|created_at)$"),
    sort_order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_industry_templates)
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("template_id", "name", "version", "status", "allowed_capabilities"),
        created_since=None,
        created_until=None,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="template_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "industry_templates": page, **metadata})


@router.get(
    "/v1/admin/industry-template-applications",
    dependencies=[Depends(permission_dependency("commercial:read"))],
)
async def v1_industry_template_applications(
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|status|template_id)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_template_applications, ctx.tenant_id, ctx.project_id, limit=None)
    page, metadata = paginate_control_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("template_application_id", "template_id", "template_version", "status"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="template_application_id",
    )
    return portrait_success(ctx.request_id, {"items": page, "template_applications": page, **metadata})


@router.get(
    "/v1/admin/industry-templates/{template_id}/preview",
    dependencies=[Depends(permission_dependency("commercial:read"))],
)
async def v1_preview_industry_template(
    template_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    preview = await run_blocking_io(preview_template, ctx.tenant_id, ctx.project_id, template_id)
    return portrait_success(ctx.request_id, {"preview": preview})


@router.post(
    "/v1/admin/industry-templates/{template_id}/apply",
    dependencies=[
        Depends(permission_dependency("commercial:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_apply_industry_template(
    template_id: str,
    payload: IndustryTemplateApplyRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    result = await run_blocking_io(
        apply_industry_template,
        ctx.tenant_id,
        ctx.project_id,
        template_id,
        actor=actor,
        expected_fingerprint=payload.expected_fingerprint,
        dry_run=payload.dry_run,
    )
    if not payload.dry_run:
        await audit_or_restore(
            "industry_template_applied",
            snapshot,
            ctx,
            actor=actor,
            template_id=template_id,
            template_version=result["template"]["version"],
            fingerprint=result["fingerprint"],
        )
    return portrait_success(ctx.request_id, {"template_application": result})


@router.post(
    "/v1/admin/industry-template-applications/{template_application_id}/rollback",
    dependencies=[
        Depends(permission_dependency("commercial:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_rollback_industry_template(
    template_application_id: str,
    payload: IndustryTemplateRollbackRequest,
    request: Request,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    snapshot = await run_blocking_io(commercial_state_payload)
    actor = actor_from_request(request)
    result = await run_blocking_io(
        rollback_industry_template,
        ctx.tenant_id,
        ctx.project_id,
        template_application_id,
        actor=actor,
        reason=payload.reason,
    )
    await audit_or_restore(
        "industry_template_rolled_back",
        snapshot,
        ctx,
        actor=actor,
        template_application_id=template_application_id,
        reason=payload.reason,
    )
    return portrait_success(ctx.request_id, {"template_rollback": result})


__all__ = ["router"]
