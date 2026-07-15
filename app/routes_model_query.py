from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.model_config import MODEL_ALIASES, MODEL_CONFIGS, reload_model_config_state
from app.model_package import public_model_config
from app.observability import request_id_from_headers
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_response import portrait_success
from app.portrait_security import tenant_id_from_request
from app.runtime import MODEL_REGISTRY
from app.security import require_api_token

router = APIRouter()


@router.post(
    "/v1/admin/models/reload-config",
    dependencies=[
        Depends(require_api_token),
        Depends(permission_dependency("models:write")),
    ],
)
async def reload_config(request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenant_id = tenant_id_from_request(request)
    previous_configs = deepcopy(MODEL_CONFIGS)
    previous_aliases = deepcopy(MODEL_ALIASES)
    model_configs, model_aliases = reload_model_config_state()
    try:
        await run_blocking_io(
            audit_event,
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
    return portrait_success(
        request_id,
        {
            "config_loaded": True,
            "models": [
                public_model_config(key, config, loaded=key in MODEL_REGISTRY)
                for key, config in sorted(model_configs.items())
            ],
            "aliases": model_aliases,
            "count": len(model_configs),
            "alias_count": len(model_aliases),
        },
    )
