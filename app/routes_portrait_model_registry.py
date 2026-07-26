from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api_contracts import ContractAPIRouter as APIRouter
from app.model_config import reload_model_config_state
from app.model_config_writer import (
    configure_alias_shadow,
    configure_weighted_alias_rollout,
    load_raw_model_config,
    switch_alias_target,
    write_raw_model_config,
)
from app.model_package import get_model_path
from app.model_refs import split_cache_key
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency, require_step_up_authentication
from app.portrait_model_registry import (
    create_model_approval,
    create_model_evaluation,
    list_model_versions,
    list_registered_models,
    list_release_events,
    model_registry_state_payload,
    record_release_event,
    register_model_version,
    release_preflight,
    restore_model_registry_state,
)
from app.portrait_pagination import filter_sort_dict_rows, normalize_list_pagination, page_items_cursor
from app.portrait_projects import identity_claims_from_request
from app.portrait_response import portrait_success
from app.portrait_security import tenant_id_from_request
from app.runtime_execution import (
    SHADOW_ROUTES,
    clear_shadow_bundle,
    configure_shadow_bundle,
    shadow_results_snapshot,
)
from app.runtime_registry import get_or_load_model, prewarm_model_bundle
from app.security import require_api_token

router = APIRouter(dependencies=[Depends(require_api_token)])


def paginate_registry_rows(
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
    max_limit: int = 500,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pagination = normalize_list_pagination(limit, offset, cursor, max_limit=max_limit)
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
    return "platform-api"


class ModelRegistryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str = Field(..., min_length=1, max_length=256)
    capability: str = Field(..., min_length=1, max_length=128)
    version: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    owner: str | None = Field(default=None, max_length=256)
    framework: str = Field(default="onnx", min_length=1, max_length=128)
    runtime: str = Field(default="onnxruntime", min_length=1, max_length=128)
    model_target: str = Field(..., min_length=3, max_length=512)
    sha256: str = Field(..., min_length=64, max_length=64)
    artifact_size: int = Field(default=0, ge=0)
    artifact_uri: str = Field(default="", max_length=2000)
    license: str = Field(..., min_length=1, max_length=512)
    source: str = Field(..., min_length=1, max_length=2000)
    redistribution_allowed: bool = False
    model_card_ref: str = Field(..., min_length=1, max_length=2000)
    governance_ref: str = Field(..., min_length=1, max_length=2000)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    quality_gates: dict[str, Any] = Field(default_factory=dict)
    dataset_lineage: list[str] = Field(default_factory=list, max_length=100)
    supports_cpu: bool = False
    supports_batching: bool = False
    max_batch_size: int = Field(default=1, ge=1, le=4096)


class ModelEvaluationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(..., min_length=1, max_length=256)
    dataset_manifest_sha256: str = Field(..., min_length=64, max_length=64)
    definition_version: str = Field(default="1.0", min_length=1, max_length=64)
    environment: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(..., min_length=1)
    quality_gates: dict[str, Any] = Field(default_factory=dict)
    report_ref: str = Field(default="", max_length=2000)


class ModelApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(default="approve", max_length=32)
    policy: str = Field(default="model_release", max_length=128)
    comment: str = Field(default="", max_length=2000)


class ModelReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_version_id: str = Field(..., min_length=1, max_length=128)
    alias: str = Field(..., min_length=1, max_length=128)
    action: str = Field(default="activate", max_length=32)
    risk_level: str = Field(default="high", max_length=16)
    traffic_percentage: int = Field(default=5, ge=1, le=99)
    expected_current_target: str | None = Field(default=None, max_length=512)
    reason: str = Field(..., min_length=1, max_length=2000)


async def audit_registry_mutation(
    event: str,
    snapshot: dict[str, Any],
    request: Request,
    *,
    actor: str,
    **fields: Any,
) -> None:
    try:
        await run_blocking_io(
            audit_event,
            event,
            request_id=str(request.state.request_id),
            tenant_id=tenant_id_from_request(request),
            actor=actor,
            **fields,
        )
    except Exception:
        await run_blocking_io(restore_model_registry_state, snapshot)
        raise


@router.get(
    "/v1/admin/models/registry",
    dependencies=[Depends(permission_dependency("models:read"))],
)
async def v1_model_registry(
    request: Request,
    capability: str | None = Query(default=None, max_length=128),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="name", pattern="^(name|capability|created_at|updated_at|version_count)$"),
    sort_order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
) -> dict[str, Any]:
    rows = await run_blocking_io(
        list_registered_models,
        capability=capability,
        status_filter=status_filter,
        limit=None,
    )
    page, metadata = paginate_registry_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("model_id", "name", "capability", "description", "owner"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="model_id",
    )
    return portrait_success(str(request.state.request_id), {"items": page, "models": page, **metadata})


@router.post(
    "/v1/admin/models/registry",
    dependencies=[Depends(permission_dependency("models:write"))],
)
async def v1_register_model(payload: ModelRegistryCreateRequest, request: Request) -> dict[str, Any]:
    snapshot = await run_blocking_io(model_registry_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        register_model_version,
        payload.model_dump(),
        actor=actor,
        request_id=str(request.state.request_id),
    )
    await audit_registry_mutation(
        "model_registry_version_created",
        snapshot,
        request,
        actor=actor,
        model_id=record["model_id"],
        model_version_id=record["model_version_id"],
        capability=payload.capability,
        artifact_sha256=record["sha256"],
    )
    return portrait_success(str(request.state.request_id), {"model_version": record})


@router.get(
    "/v1/admin/models/registry/{model_id}/versions",
    dependencies=[Depends(permission_dependency("models:read"))],
)
async def v1_model_versions(
    model_id: str,
    request: Request,
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|version|status|artifact_size)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_model_versions, model_id, limit=None)
    if status_filter is not None:
        rows = [item for item in rows if item.get("status") == status_filter]
    page, metadata = paginate_registry_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("model_version_id", "version", "framework", "runtime", "model_target", "status", "license"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="model_version_id",
    )
    return portrait_success(str(request.state.request_id), {"items": page, "versions": page, **metadata})


@router.post(
    "/v1/admin/models/registry/versions/{model_version_id}/evaluations",
    dependencies=[Depends(permission_dependency("models:write"))],
)
async def v1_create_model_evaluation(
    model_version_id: str,
    payload: ModelEvaluationCreateRequest,
    request: Request,
) -> dict[str, Any]:
    snapshot = await run_blocking_io(model_registry_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_model_evaluation,
        model_version_id,
        payload.model_dump(),
        actor=actor,
        request_id=str(request.state.request_id),
    )
    await audit_registry_mutation(
        "model_evaluation_created",
        snapshot,
        request,
        actor=actor,
        model_version_id=model_version_id,
        evaluation_id=record["evaluation_id"],
        passed=record["passed"],
        dataset_manifest_sha256=record["dataset_manifest_sha256"],
    )
    return portrait_success(str(request.state.request_id), {"evaluation": record})


@router.post(
    "/v1/admin/models/registry/versions/{model_version_id}/approvals",
    dependencies=[Depends(permission_dependency("models:approve"))],
)
async def v1_create_model_approval(
    model_version_id: str,
    payload: ModelApprovalCreateRequest,
    request: Request,
) -> dict[str, Any]:
    snapshot = await run_blocking_io(model_registry_state_payload)
    actor = actor_from_request(request)
    record = await run_blocking_io(
        create_model_approval,
        model_version_id,
        payload.model_dump(),
        actor=actor,
        request_id=str(request.state.request_id),
    )
    await audit_registry_mutation(
        "model_release_approval_recorded",
        snapshot,
        request,
        actor=actor,
        model_version_id=model_version_id,
        approval_id=record["approval_id"],
        decision=record["decision"],
        policy=record["policy"],
    )
    return portrait_success(str(request.state.request_id), {"approval": record})


def release_payload(payload: ModelReleaseRequest, actor: str) -> dict[str, Any]:
    return {**payload.model_dump(), "release_actor": actor}


@router.post(
    "/v1/admin/models/releases/dry-run",
    dependencies=[Depends(permission_dependency("models:write"))],
)
async def v1_model_release_dry_run(payload: ModelReleaseRequest, request: Request) -> dict[str, Any]:
    actor = actor_from_request(request)
    preflight = await run_blocking_io(release_preflight, payload.model_version_id, release_payload(payload, actor))
    return portrait_success(str(request.state.request_id), {"release_preflight": preflight})


@router.post(
    "/v1/admin/models/releases/apply",
    dependencies=[
        Depends(permission_dependency("models:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_model_release_apply(payload: ModelReleaseRequest, request: Request) -> dict[str, Any]:
    actor = actor_from_request(request)
    values = release_payload(payload, actor)
    preflight = await run_blocking_io(release_preflight, payload.model_version_id, values)
    if not preflight["ok"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "model_release_blocked", "blockers": preflight["blockers"]},
        )
    registry_snapshot = await run_blocking_io(model_registry_state_payload)
    config_snapshot = await run_blocking_io(load_raw_model_config)
    rollout: dict[str, Any] = {"written": False}
    prewarm: dict[str, Any] | None = None
    current_target = str(preflight.get("current_target") or "")
    previous_shadow_route = SHADOW_ROUTES.get(current_target)
    try:
        candidate_bundle = None
        if payload.action in {"shadow", "canary", "activate", "rollback"}:
            project_name, model_name = split_cache_key(str(preflight["target"]))
            model_path = get_model_path(project_name, model_name)
            candidate_bundle, _cold_loaded, _load_seconds = await get_or_load_model(
                str(preflight["target"]), model_path
            )
            prewarm = await prewarm_model_bundle(str(preflight["target"]), candidate_bundle)
        if payload.action == "shadow" and candidate_bundle is not None:
            source_project, source_model = split_cache_key(current_target)
            source_bundle, _source_cold, _source_load_seconds = await get_or_load_model(
                current_target, get_model_path(source_project, source_model)
            )
            rollout = await run_blocking_io(
                configure_alias_shadow,
                payload.alias,
                str(preflight["target"]),
                traffic_percentage=payload.traffic_percentage,
                dry_run=False,
            )
            reload_model_config_state()
            configure_shadow_bundle(
                current_target,
                candidate_bundle,
                percentage=payload.traffic_percentage,
            )
            source_bundle["shadow_target"] = str(preflight["target"])
        elif payload.action in {"activate", "rollback"}:
            await run_blocking_io(configure_alias_shadow, payload.alias, None, dry_run=False)
            clear_shadow_bundle(current_target)
            rollout = await run_blocking_io(
                switch_alias_target,
                payload.alias,
                preflight["target"],
                payload.expected_current_target,
                False,
            )
            reload_model_config_state()
        elif payload.action == "canary":
            await run_blocking_io(configure_alias_shadow, payload.alias, None, dry_run=False)
            clear_shadow_bundle(current_target)
            rollout = await run_blocking_io(
                configure_weighted_alias_rollout,
                payload.alias,
                [
                    {"target_model_id": current_target, "weight": 100 - payload.traffic_percentage, "status": "active"},
                    {
                        "target_model_id": preflight["target"],
                        "weight": payload.traffic_percentage,
                        "status": "candidate",
                    },
                ],
                payload.expected_current_target,
                False,
            )
            reload_model_config_state()
        elif payload.action in {"pause", "deprecate"}:
            rollout = await run_blocking_io(
                configure_alias_shadow,
                payload.alias,
                None,
                dry_run=False,
            )
            reload_model_config_state()
            clear_shadow_bundle(current_target)
        release_event = await run_blocking_io(
            record_release_event,
            payload.model_version_id,
            values,
            actor=actor,
            request_id=str(request.state.request_id),
            previous_target=preflight["current_target"],
            outcome="success",
        )
        await run_blocking_io(
            audit_event,
            "model_release_applied",
            request_id=str(request.state.request_id),
            tenant_id=tenant_id_from_request(request),
            actor=actor,
            model_version_id=payload.model_version_id,
            release_event_id=release_event["release_event_id"],
            action=payload.action,
            alias=payload.alias,
            previous_target=preflight["current_target"],
            target=preflight["target"],
            risk_level=payload.risk_level,
        )
    except Exception:
        await run_blocking_io(write_raw_model_config, config_snapshot)
        reload_model_config_state()
        await run_blocking_io(restore_model_registry_state, registry_snapshot)
        if previous_shadow_route is not None:
            SHADOW_ROUTES[current_target] = previous_shadow_route
        else:
            clear_shadow_bundle(current_target)
        raise
    return portrait_success(
        str(request.state.request_id),
        {"release": release_event, "rollout": rollout, "preflight": preflight, "prewarm": prewarm},
    )


@router.post(
    "/v1/admin/models/releases/rollback",
    dependencies=[
        Depends(permission_dependency("models:write")),
        Depends(require_step_up_authentication),
    ],
)
async def v1_model_release_rollback(payload: ModelReleaseRequest, request: Request) -> dict[str, Any]:
    rollback_payload = payload.model_copy(update={"action": "rollback"})
    return await v1_model_release_apply(rollback_payload, request)


@router.get(
    "/v1/admin/models/releases/audit",
    dependencies=[Depends(permission_dependency("models:read"))],
)
async def v1_model_release_audit(
    request: Request,
    action: str | None = Query(default=None, max_length=32),
    outcome: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="created_at", pattern="^created_at$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
) -> dict[str, Any]:
    rows = await run_blocking_io(list_release_events, limit=None)
    if action is not None:
        rows = [item for item in rows if item.get("action") == action]
    if outcome is not None:
        rows = [item for item in rows if item.get("outcome") == outcome]
    page, metadata = paginate_registry_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("release_event_id", "model_version_id", "alias", "action", "outcome", "actor", "reason"),
        created_since=created_since,
        created_until=created_until,
        time_field="created_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="release_event_id",
    )
    return portrait_success(str(request.state.request_id), {"items": page, "release_events": page, **metadata})


@router.get(
    "/v1/admin/models/releases/shadow-results",
    dependencies=[Depends(permission_dependency("models:read"))],
)
async def v1_model_shadow_results(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    q: str | None = Query(default=None, max_length=256),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    sort_by: str = Query(default="recorded_at", pattern="^recorded_at$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
) -> dict[str, Any]:
    rows = shadow_results_snapshot(1000)
    if status_filter is not None:
        rows = [item for item in rows if item.get("status") == status_filter]
    page, metadata = paginate_registry_rows(
        rows,
        limit=limit,
        offset=offset,
        cursor=cursor,
        query=q,
        search_fields=("active_model_id", "candidate_model_id", "status", "error_type"),
        created_since=created_since,
        created_until=created_until,
        time_field="recorded_at",
        sort_by=sort_by,
        sort_order=sort_order,
        id_field="candidate_fingerprint",
        max_limit=1000,
    )
    return portrait_success(str(request.state.request_id), {"items": page, "shadow_results": page, **metadata})


__all__ = ["router"]
