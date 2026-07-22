from copy import deepcopy
from typing import Any

from fastapi import Depends, Request

from app.api_contracts import ContractAPIRouter as APIRouter
from app.metrics import gpu_memory_metrics
from app.model_config import MODEL_ALIASES, MODEL_CONFIGS, reload_model_config_state
from app.model_config_writer import configure_model_gpu_device, load_raw_model_config, write_raw_model_config
from app.model_package import public_model_config
from app.model_refs import validate_model_target
from app.observability import request_id_from_headers
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_response import portrait_success
from app.portrait_security import tenant_id_from_request
from app.runtime import MODEL_REGISTRY, gpu_device_ids, unload_model_by_key
from app.runtime_state import MODEL_LOAD_RETRY_AFTER
from app.schemas import ModelGpuDeviceRequest
from app.security import require_api_token

router = APIRouter()


def gpu_device_inventory() -> dict[str, Any]:
    configured_ids = sorted({int(item) for item in gpu_device_ids()})
    memory_rows = gpu_memory_metrics()
    memory_by_id = {int(item["device"]): item for item in memory_rows}
    detection_available = bool(memory_rows)
    detected_ids = sorted(memory_by_id)
    all_ids = sorted(set(configured_ids) | set(detected_ids))
    devices = []
    for device_id in all_ids:
        memory = memory_by_id.get(device_id, {})
        configured = device_id in configured_ids
        detected = device_id in memory_by_id
        devices.append(
            {
                "device_id": device_id,
                "configured": configured,
                "detected": detected,
                "assignable": detected if detection_available else configured,
                "memory_used_bytes": int(memory.get("used", 0)),
                "memory_free_bytes": int(memory.get("free", 0)),
                "memory_total_bytes": int(memory.get("total", 0)),
            }
        )
    return {
        "configured_device_ids": configured_ids,
        "detected_device_ids": detected_ids,
        "detection_source": "nvml" if detection_available else "configuration",
        "devices": devices,
    }


@router.get(
    "/v1/admin/models/gpu-devices",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("models:read"))],
)
async def model_gpu_devices(request: Request) -> dict[str, Any]:
    return portrait_success(request_id_from_headers(request), await run_blocking_io(gpu_device_inventory))


@router.patch(
    "/v1/admin/models/{model_id:path}/gpu-device",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("models:write"))],
)
async def update_model_gpu_device(
    model_id: str,
    payload: ModelGpuDeviceRequest,
    request: Request,
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenant_id = tenant_id_from_request(request)
    model_id = validate_model_target(model_id)
    previous_raw = await run_blocking_io(load_raw_model_config)
    inventory = await run_blocking_io(gpu_device_inventory)
    allowed_device_ids = [int(item["device_id"]) for item in inventory["devices"] if item["assignable"]]

    try:
        result = await run_blocking_io(
            configure_model_gpu_device,
            model_id,
            payload.device_id,
            allowed_device_ids,
        )
        reload_model_config_state()
        await run_blocking_io(
            audit_event,
            "model_gpu_device_updated",
            request_id=request_id,
            tenant_id=tenant_id,
            model_id=model_id,
            previous_device_id=result["previous_device_id"],
            device_id=result["device_id"],
            assignment=result["assignment"],
        )
    except Exception:
        await run_blocking_io(write_raw_model_config, previous_raw)
        reload_model_config_state()
        raise

    was_loaded = model_id in MODEL_REGISTRY
    unloaded = await unload_model_by_key(model_id)
    MODEL_LOAD_RETRY_AFTER.pop(model_id, None)
    return portrait_success(
        request_id,
        {
            **result,
            "was_loaded": was_loaded,
            "unloaded": unloaded,
            "applies_on_next_load": True,
        },
    )


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
