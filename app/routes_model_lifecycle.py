from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.model_package import get_model_path
from app.model_refs import cache_key
from app.observability import request_id_from_headers
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_security import tenant_id_from_request
from app.runtime import MODEL_LOAD_LOCKS, MODEL_REGISTRY, get_or_load_model, unload_model_by_key
from app.security import require_api_token
from app.schemas import ModelRequest, WarmupRequest


router = APIRouter()


def model_registry_snapshot() -> OrderedDict[str, dict[str, Any]]:
    return OrderedDict(MODEL_REGISTRY)


def model_load_locks_snapshot() -> dict[str, Any]:
    return dict(MODEL_LOAD_LOCKS)


def restore_model_registry_snapshot(
    previous_registry: OrderedDict[str, dict[str, Any]],
    previous_locks: dict[str, Any],
) -> None:
    MODEL_REGISTRY.clear()
    MODEL_REGISTRY.update(previous_registry)
    MODEL_LOAD_LOCKS.clear()
    MODEL_LOAD_LOCKS.update(previous_locks)


@router.post("/warmup", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:write"))])
async def warmup(req: WarmupRequest, request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenant_id = tenant_id_from_request(request)
    results = []
    previous_registry = model_registry_snapshot()
    previous_locks = model_load_locks_snapshot()
    for model in req.models:
        key = cache_key(model.project_name, model.model_name)
        model_path = get_model_path(model.project_name, model.model_name)
        bundle, cold_loaded, load_seconds = await get_or_load_model(key, model_path)
        results.append(
            {
                "model": key,
                "cold_loaded": cold_loaded,
                "load_seconds": load_seconds,
                "model_hash": bundle["model_hash"],
            }
    )
    try:
        await run_blocking_io(audit_event, "model_warmup", request_id=request_id, tenant_id=tenant_id, models=[item["model"] for item in results])
    except Exception:
        restore_model_registry_snapshot(previous_registry, previous_locks)
        raise
    return {"status": "success", "request_id": request_id, "results": results}


@router.post("/unload", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:write"))])
async def unload(req: ModelRequest, request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenant_id = tenant_id_from_request(request)
    key = cache_key(req.project_name, req.model_name)
    previous_registry = model_registry_snapshot()
    previous_locks = model_load_locks_snapshot()
    unloaded = await unload_model_by_key(key)
    try:
        await run_blocking_io(audit_event, "model_unload", request_id=request_id, tenant_id=tenant_id, model=key, unloaded=unloaded)
    except Exception:
        restore_model_registry_snapshot(previous_registry, previous_locks)
        raise
    return {"status": "success", "request_id": request_id, "model": key, "unloaded": unloaded}


@router.post("/reload", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:write"))])
async def reload_model(req: ModelRequest, request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenant_id = tenant_id_from_request(request)
    key = cache_key(req.project_name, req.model_name)
    previous_registry = model_registry_snapshot()
    previous_locks = model_load_locks_snapshot()
    await unload_model_by_key(key)
    model_path = get_model_path(req.project_name, req.model_name)
    bundle, cold_loaded, load_seconds = await get_or_load_model(key, model_path)
    try:
        await run_blocking_io(audit_event, "model_reload", request_id=request_id, tenant_id=tenant_id, model=key, cold_loaded=cold_loaded)
    except Exception:
        restore_model_registry_snapshot(previous_registry, previous_locks)
        raise
    return {
        "status": "success",
        "request_id": request_id,
        "model": key,
        "cold_loaded": cold_loaded,
        "load_seconds": load_seconds,
        "model_hash": bundle["model_hash"],
    }
