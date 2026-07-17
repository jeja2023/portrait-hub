import shutil
from typing import Any

import numpy as np
import onnxruntime as ort
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.metrics import prometheus_metrics
from app.model_config import MODEL_CONFIGS
from app.model_package import get_model_path
from app.model_refs import split_cache_key
from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_auth import permission_dependency
from app.portrait_response import MODEL_READINESS_CHECK_FAILED, exception_log_summary
from app.runtime import get_or_load_model, input_dtype, run_model_bundle
from app.runtime_sessions import runtime_provider_status
from app.security import request_is_authenticated, require_api_token
from app.settings import APP_VERSION, OBJECT_STORAGE_DIR, READY_CHECK_DEPENDENCIES, RUNTIME_STATE_DIR

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    # 公开存活探针：刻意保持最小化，不向未鉴权调用方泄露精确构建版本号
    # （版本号改在鉴权的 /ready/deep 与管理员状态端点暴露给运维）。
    return {"status": "healthy"}


@router.get("/ready")
async def ready(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> dict[str, Any]:
    # /ready 接口保持公开可达，以便编排器（Orchestrator）探测存活状态，但
    # 依赖项/磁盘的详细细分信息仅披露给已通过身份验证的调用者。
    disclose_detail = request_is_authenticated(authorization, x_api_key, x_tenant_id)
    available = await run_blocking_io(ort.get_available_providers)
    provider_status = runtime_provider_status(available)
    if not provider_status["ready"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready"},
        )
    if not READY_CHECK_DEPENDENCIES:
        return {"status": "ready"}
    checks = await run_blocking_io(readiness_dependency_checks)
    if any(item.get("status") == "error" for item in checks.values()):
        detail: dict[str, Any] = {"status": "not_ready"}
        if disclose_detail:
            detail["checks"] = checks
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )
    if disclose_detail:
        return {"status": "ready", "checks": checks}
    return {"status": "ready"}


def disk_health(path: Any) -> dict[str, Any]:
    try:
        target = path
        target.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(target)
        return {
            "status": "ready",
            "free_bytes": usage.free,
            "total_bytes": usage.total,
        }
    except Exception as exc:
        logger.warning("disk health check failed: %s", exception_log_summary(exc))
        return {"status": "error", "error": MODEL_READINESS_CHECK_FAILED}


def readiness_dependency_checks() -> dict[str, Any]:
    from app.portrait_object_storage import OBJECT_STORE
    from app.portrait_postgres import postgres_health
    from app.portrait_task_queue import TASK_QUEUE
    from app.portrait_vector_store import VECTOR_STORE

    return {
        "postgres": postgres_health(),
        "vector_store": VECTOR_STORE.health(),
        "object_storage": OBJECT_STORE.health(),
        "task_queue": TASK_QUEUE.health(),
        "runtime_state_disk": disk_health(RUNTIME_STATE_DIR),
        "object_storage_disk": disk_health(OBJECT_STORAGE_DIR),
    }


@router.get("/ready/deep", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:read"))])
async def ready_deep(
    load_models: bool = Query(False),
    dummy_inference: bool = Query(False),
) -> dict[str, Any]:
    available = await run_blocking_io(ort.get_available_providers)
    provider_status = runtime_provider_status(available)
    checks = []
    ok = bool(provider_status["ready"])

    for key, config in MODEL_CONFIGS.items():
        try:
            project_name, model_name = split_cache_key(key)
            model_path = get_model_path(project_name, model_name)
            item: dict[str, Any] = {
                "model": key,
                "type": config.get("type"),
                "exists": True,
                "path_checked": True,
            }
            if load_models or dummy_inference:
                bundle, cold_loaded, load_seconds = await get_or_load_model(key, model_path)
                item.update(
                    {
                        "loaded": True,
                        "cold_loaded": cold_loaded,
                        "load_seconds": load_seconds,
                        "providers": bundle["session"].get_providers(),
                        "execution_provider": bundle.get("execution_provider"),
                    }
                )
                if dummy_inference:
                    session = bundle["session"]
                    input_meta = session.get_inputs()[0]
                    shape = [dim if isinstance(dim, int) and dim > 0 else 1 for dim in input_meta.shape]
                    dtype = input_dtype(input_meta.type)
                    dummy = np.zeros(shape, dtype=dtype)
                    _, queue_seconds, inference_seconds = await run_model_bundle(bundle, dummy)
                    item.update(
                        {
                            "dummy_inference": True,
                            "dummy_input_shape": shape,
                            "queue_seconds": queue_seconds,
                            "inference_seconds": inference_seconds,
                        }
                    )
            checks.append(item)
        except Exception as exc:
            logger.warning("深度就绪模型检查失败 %s: %s", key, exception_log_summary(exc))
            ok = False
            checks.append({"model": key, "ok": False, "error": MODEL_READINESS_CHECK_FAILED})

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "runtime_provider": provider_status, "checks": checks},
        )
    return {"status": "ready", "version": APP_VERSION, "runtime_provider": provider_status, "checks": checks}


@router.get("/metrics", dependencies=[Depends(require_api_token), Depends(permission_dependency("metrics:read"))])
async def metrics() -> Response:
    return Response(content=prometheus_metrics(), media_type="text/plain; version=0.0.4")
