from typing import Any

from fastapi import Depends, HTTPException, Query, Request, status

from app.api_contracts import ContractAPIRouter as APIRouter
from app.model_config import MODEL_ALIASES, alias_resolution, reload_model_config_state
from app.model_config_writer import (
    configure_weighted_alias_rollout,
    rollback_alias_target,
    switch_alias_target,
)
from app.model_package import get_model_path
from app.model_refs import split_cache_key, validate_alias_name
from app.observability import logger, request_id_from_headers
from app.portrait_auth import permission_dependency
from app.portrait_response import exception_log_summary, portrait_success
from app.rollout_audit import MAX_ROLLOUT_AUDIT_LIMIT, read_rollout_audit
from app.schemas import (
    AliasRollbackRequest,
    AliasSwitchRequest,
    AliasWeightedRolloutRequest,
)
from app.security import require_api_token

router = APIRouter()

ALIAS_RESOLUTION_FAILED = "别名解析失败"
ROLLOUT_PREFIX = "/v1/admin/models/rollout"


def alias_view(alias_name: str, alias_config: Any) -> dict[str, Any]:
    try:
        resolution = alias_resolution(alias_name, alias_config)
        target = resolution["target"]
        ok = True
        error = None
    except Exception as exc:
        logger.warning("别名解析失败 for %s: %s", alias_name, exception_log_summary(exc))
        resolution = None
        target = None
        ok = False
        error = ALIAS_RESOLUTION_FAILED
    return {
        "alias": alias_name,
        "target": target,
        "previous_target": alias_config.get("previous_target") if isinstance(alias_config, dict) else None,
        "resolution": resolution,
        "config": alias_config,
        "ok": ok,
        "error": error,
    }


def aliases_payload() -> list[dict[str, Any]]:
    return [alias_view(name, config) for name, config in sorted(MODEL_ALIASES.items())]


@router.get(
    f"{ROLLOUT_PREFIX}/aliases",
    dependencies=[
        Depends(require_api_token),
        Depends(permission_dependency("models:read")),
    ],
)
async def rollout_aliases(request: Request) -> dict[str, Any]:
    aliases = aliases_payload()
    return portrait_success(
        request_id_from_headers(request),
        {"config_loaded": True, "aliases": aliases, "count": len(aliases)},
    )


@router.get(
    f"{ROLLOUT_PREFIX}/audit",
    dependencies=[
        Depends(require_api_token),
        Depends(permission_dependency("models:read")),
    ],
)
async def rollout_audit_entries(
    request: Request,
    limit: int = Query(100, ge=1, le=MAX_ROLLOUT_AUDIT_LIMIT),
) -> dict[str, Any]:
    try:
        records, malformed_count = read_rollout_audit(limit)
    except Exception as exc:
        logger.warning("读取发布审计失败: %s", exception_log_summary(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rollout audit unavailable",
        ) from exc
    return portrait_success(
        request_id_from_headers(request),
        {
            "records": records,
            "count": len(records),
            "limit": limit,
            "malformed_count": malformed_count,
        },
    )


@router.get(
    f"{ROLLOUT_PREFIX}/aliases/preview",
    dependencies=[
        Depends(require_api_token),
        Depends(permission_dependency("models:read")),
    ],
)
async def rollout_alias_preview(
    request: Request,
    alias_name: str = Query(..., min_length=1, max_length=128),
    traffic_key: str = Query(..., min_length=1, max_length=256),
) -> dict[str, Any]:
    validate_alias_name(alias_name)
    alias_config = MODEL_ALIASES.get(alias_name)
    if alias_config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="别名不存在")
    resolution = alias_resolution(alias_name, alias_config, traffic_key=traffic_key)
    project, model = split_cache_key(str(resolution["target"]))
    return portrait_success(
        request_id_from_headers(request),
        {
            "alias": alias_name,
            "traffic_key": traffic_key,
            "target": resolution["target"],
            "project_name": project,
            "model_name": model,
            "resolution": resolution,
        },
    )


@router.post(
    f"{ROLLOUT_PREFIX}/aliases/switch",
    dependencies=[
        Depends(require_api_token),
        Depends(permission_dependency("models:write")),
    ],
)
async def rollout_alias_switch(req: AliasSwitchRequest, request: Request) -> dict[str, Any]:
    project, model = split_cache_key(req.target_model_id)
    get_model_path(project, model)
    result = switch_alias_target(
        alias_name=req.alias_name,
        target_model_id=req.target_model_id,
        expected_current_target=req.expected_current_target,
        dry_run=req.dry_run,
    )
    if not req.dry_run:
        reload_model_config_state()
    return portrait_success(request_id_from_headers(request), {**result, "aliases": aliases_payload()})


@router.post(
    f"{ROLLOUT_PREFIX}/aliases/weighted",
    dependencies=[
        Depends(require_api_token),
        Depends(permission_dependency("models:write")),
    ],
)
async def rollout_alias_weighted(req: AliasWeightedRolloutRequest, request: Request) -> dict[str, Any]:
    for item in req.targets:
        project, model = split_cache_key(item.target_model_id)
        get_model_path(project, model)
    result = configure_weighted_alias_rollout(
        alias_name=req.alias_name,
        targets=[item.model_dump() for item in req.targets],
        expected_current_target=req.expected_current_target,
        dry_run=req.dry_run,
    )
    if not req.dry_run:
        reload_model_config_state()
    return portrait_success(request_id_from_headers(request), {**result, "aliases": aliases_payload()})


@router.post(
    f"{ROLLOUT_PREFIX}/aliases/rollback",
    dependencies=[
        Depends(require_api_token),
        Depends(permission_dependency("models:write")),
    ],
)
async def rollout_alias_rollback(req: AliasRollbackRequest, request: Request) -> dict[str, Any]:
    dry_result = rollback_alias_target(alias_name=req.alias_name, dry_run=True)
    project, model = split_cache_key(dry_result["new_target"])
    get_model_path(project, model)
    result = dry_result if req.dry_run else rollback_alias_target(alias_name=req.alias_name, dry_run=False)
    if not req.dry_run:
        reload_model_config_state()
    return portrait_success(request_id_from_headers(request), {**result, "aliases": aliases_payload()})
