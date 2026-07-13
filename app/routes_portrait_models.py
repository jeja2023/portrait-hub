from copy import deepcopy
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core import (
    MODEL_ALIASES,
    MODEL_CONFIGS,
    MODEL_REGISTRY,
    MODEL_LOAD_LOCKS,
    bundle_info,
    get_model_path,
    get_or_load_model,
    model_config,
    model_package_info,
    public_model_config,
    resolve_model_reference,
    unload_model_by_key,
)
from app.observability import logger
from app.observability import request_id_from_headers
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_response import exception_log_summary, portrait_success, raise_rollback_failure
from app.portrait_security import tenant_id_from_request
from app.portrait_thresholds import THRESHOLD_PROFILES, save_threshold_state, threshold_snapshot, update_threshold_profile
from app.security import require_api_token
from app.schemas import ModelBundle


router = APIRouter(dependencies=[Depends(require_api_token)])


class ThresholdUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face: float | None = Field(default=None, ge=0.0, le=1.0)
    body: float | None = Field(default=None, ge=0.0, le=1.0)
    person: float | None = Field(default=None, ge=0.0, le=1.0)
    gait: float | None = Field(default=None, ge=0.0, le=1.0)
    appearance: float | None = Field(default=None, ge=0.0, le=1.0)
    fusion: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("face", "body", "person", "gait", "appearance", "fusion", mode="before")
    @classmethod
    def reject_boolean_thresholds(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("阈值必须是数值")
        return value


def model_registry_snapshot() -> OrderedDict[str, ModelBundle]:
    return OrderedDict(MODEL_REGISTRY)


def model_load_locks_snapshot() -> dict[str, Any]:
    return dict(MODEL_LOAD_LOCKS)


def restore_model_registry_snapshot(
    previous_registry: OrderedDict[str, ModelBundle],
    previous_locks: dict[str, Any],
) -> None:
    MODEL_REGISTRY.clear()
    MODEL_REGISTRY.update(previous_registry)
    MODEL_LOAD_LOCKS.clear()
    MODEL_LOAD_LOCKS.update(previous_locks)


def restore_threshold_snapshot(previous_thresholds: dict[str, Any]) -> list[str]:
    THRESHOLD_PROFILES.clear()
    THRESHOLD_PROFILES.update(deepcopy(previous_thresholds))
    try:
        save_threshold_state()
    except Exception as exc:
        logger.warning("持久化恢复后的阈值快照失败: %s", exception_log_summary(exc))
        return ["restore thresholds failed"]
    return []


def raise_model_management_rollback_failure(original_error: Exception, rollback_errors: list[str]) -> None:
    raise_rollback_failure("模型管理变更失败，且回滚持久化失败", original_error, rollback_errors)


@router.get("/v1/models", dependencies=[Depends(permission_dependency("models:read"))])
async def v1_models(request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    models = [public_model_config(model_id, config, loaded=model_id in MODEL_REGISTRY) for model_id, config in sorted(MODEL_CONFIGS.items())]
    return portrait_success(
        request_id,
        {
            "config_loaded": True,
            "models": models,
            "aliases": MODEL_ALIASES,
            "loaded_models": [bundle_info(key, bundle) for key, bundle in MODEL_REGISTRY.items()],
            "count": len(models),
        },
    )


@router.post("/v1/models/{model_id:path}/load", dependencies=[Depends(permission_dependency("models:write"))])
async def v1_model_load(request: Request, model_id: str) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    project, model, key, alias_name = resolve_model_reference(model_id, None, None)
    model_path = get_model_path(project, model)
    previous_registry = model_registry_snapshot()
    previous_locks = model_load_locks_snapshot()
    bundle, cold_loaded, load_seconds = await get_or_load_model(key, model_path)
    tenant_id = tenant_id_from_request(request)
    try:
        await run_blocking_io(
            audit_event,
            "model_loaded",
            request_id=request_id,
            tenant_id=tenant_id,
            model_id=key,
            alias=alias_name,
            cold_loaded=cold_loaded,
        )
    except Exception:
        restore_model_registry_snapshot(previous_registry, previous_locks)
        raise
    return portrait_success(
        request_id,
        {
            "model": bundle_info(key, bundle),
            "alias": alias_name,
            "cold_loaded": cold_loaded,
            "load_seconds": load_seconds,
        },
    )


@router.post("/v1/models/{model_id:path}/unload", dependencies=[Depends(permission_dependency("models:write"))])
async def v1_model_unload(request: Request, model_id: str) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    project, model, key, alias_name = resolve_model_reference(model_id, None, None)
    previous_registry = model_registry_snapshot()
    previous_locks = model_load_locks_snapshot()
    unloaded = await unload_model_by_key(key)
    tenant_id = tenant_id_from_request(request)
    try:
        await run_blocking_io(
            audit_event,
            "model_unloaded",
            request_id=request_id,
            tenant_id=tenant_id,
            model_id=key,
            alias=alias_name,
            unloaded=unloaded,
        )
    except Exception:
        restore_model_registry_snapshot(previous_registry, previous_locks)
        raise
    return portrait_success(request_id, {"model_id": key, "alias": alias_name, "unloaded": unloaded})


@router.get("/v1/models/{model_id:path}", dependencies=[Depends(permission_dependency("models:read"))])
async def v1_model_detail(request: Request, model_id: str) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    # 允许来自 resolve_model_reference 的验证错误向上传播；此前这些错误
    # 被吞掉了，且直接使用了未经验证的原始 model_id 来构建键。
    project, model, key, alias_name = resolve_model_reference(model_id, None, None)
    config = model_config(key)
    payload: dict[str, Any] = {
        "model_id": key,
        "alias": alias_name,
        "config": public_model_config(key, config, loaded=key in MODEL_REGISTRY),
        "loaded": key in MODEL_REGISTRY,
    }
    if key in MODEL_REGISTRY:
        bundle = MODEL_REGISTRY[key]
        payload["runtime"] = bundle_info(key, bundle)
        payload["package"] = model_package_info(key, get_model_path(project, model), bundle["model_hash"])
    return portrait_success(request_id, payload)


@router.get("/v1/thresholds", dependencies=[Depends(permission_dependency("models:read"))])
async def v1_thresholds(request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    return portrait_success(request_id, {"thresholds": threshold_snapshot()})


@router.put("/v1/thresholds/{profile}", dependencies=[Depends(permission_dependency("thresholds:write"))])
async def v1_update_thresholds(request: Request, profile: str, payload: ThresholdUpdateRequest) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenant_id = tenant_id_from_request(request)
    update_payload = payload.model_dump(exclude_none=True)
    if not update_payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="阈值请求体不能为空")
    previous_thresholds = threshold_snapshot()
    result = update_threshold_profile(profile, update_payload)
    try:
        await run_blocking_io(
            audit_event,
            "threshold_profile_updated",
            request_id=request_id,
            tenant_id=tenant_id,
            profile=result["profile"],
            updated=result["updated"],
        )
    except Exception as exc:
        rollback_errors = restore_threshold_snapshot(previous_thresholds)
        if rollback_errors:
            raise_model_management_rollback_failure(exc, rollback_errors)
        raise
    return portrait_success(request_id, result)
