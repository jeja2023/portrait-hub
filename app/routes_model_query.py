import asyncio
from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core import *
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_security import tenant_id_from_request


router = APIRouter()


@router.get("/models", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:read"))])
async def models() -> dict[str, Any]:
    return {
        "loaded_models": [
            bundle_info(key, bundle) for key, bundle in MODEL_REGISTRY.items()
        ],
        "count": len(MODEL_REGISTRY),
        "max_loaded_models": MAX_LOADED_MODELS,
    }


@router.get("/model-configs", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:read"))])
async def model_configs() -> dict[str, Any]:
    return {
        "config_loaded": True,
        "models": {
            key: public_model_config(key, config, loaded=key in MODEL_REGISTRY)
            for key, config in sorted(MODEL_CONFIGS.items())
        },
        "aliases": MODEL_ALIASES,
        "count": len(MODEL_CONFIGS),
        "alias_count": len(MODEL_ALIASES),
    }


@router.post("/reload-config", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:write"))])
async def reload_config(request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenant_id = tenant_id_from_request(request)
    previous_configs = deepcopy(MODEL_CONFIGS)
    previous_aliases = deepcopy(MODEL_ALIASES)
    model_configs, model_aliases = reload_model_config_state()
    try:
        audit_event(
            "model_config_reloaded",
            request_id=request_id,
            tenant_id=tenant_id,
            model_count=len(model_configs),
            alias_count=len(model_aliases),
        )
    except Exception:
        MODEL_CONFIGS.clear()
        MODEL_CONFIGS.update(previous_configs)
        MODEL_ALIASES.clear()
        MODEL_ALIASES.update(previous_aliases)
        raise
    return {
        "status": "success",
        "request_id": request_id,
        "config_loaded": True,
        "models": {
            key: public_model_config(key, config, loaded=key in MODEL_REGISTRY)
            for key, config in sorted(model_configs.items())
        },
        "aliases": model_aliases,
        "count": len(model_configs),
        "alias_count": len(model_aliases),
    }


@router.get("/model-info", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:read"))])
async def model_info(
    project_name: str = Query(..., min_length=1, max_length=128),
    model_name: str = Query(..., min_length=1, max_length=256),
) -> dict[str, Any]:
    project_name, model_name = validate_model_reference_parts(project_name, model_name)

    req = ModelRequest(project_name=project_name, model_name=model_name)
    key = cache_key(req.project_name, req.model_name)
    model_path = get_model_path(req.project_name, req.model_name)
    bundle, cold_loaded, load_seconds = await get_or_load_model(key, model_path)
    return {
        **bundle_info(key, bundle),
        "cold_loaded": cold_loaded,
        "load_seconds": load_seconds,
        "config": public_model_config(key, model_config(key), loaded=True),
        "package": model_package_info(key, model_path, bundle["model_hash"]),
    }


@router.get("/model-package", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:read"))])
async def model_package(
    model_id: str | None = Query(None),
    project_name: str | None = Query(None, min_length=1, max_length=128),
    model_name: str | None = Query(None, min_length=1, max_length=256),
    traffic_key: str | None = Query(None, min_length=1, max_length=256),
) -> dict[str, Any]:
    project, model, key, alias_name = resolve_model_reference(model_id, project_name, model_name, traffic_key=traffic_key)
    model_path = get_model_path(project, model)
    digest = await asyncio.to_thread(model_hash, model_path)
    return {
        "status": "success",
        "model": {
            "id": alias_name or model_id or key,
            "alias": alias_name,
            "traffic_key": traffic_key if alias_name else None,
            "project_name": project,
            "model_name": model,
            "key": key,
            "artifact_resolved": True,
            "hash": digest,
        },
        "config": public_model_config(key, model_config(key), loaded=key in MODEL_REGISTRY),
        "package": model_package_info(key, model_path, digest),
    }
