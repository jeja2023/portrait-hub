import asyncio
import logging
from typing import Any

import numpy as np
import onnxruntime as ort
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status

from app.core import *
from app.portrait_auth import permission_dependency
from app.portrait_response import MODEL_READINESS_CHECK_FAILED, exception_log_summary
from app.settings import APP_VERSION


router = APIRouter()


@router.get("/")
async def root() -> dict[str, Any]:
    return await health()


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "version": APP_VERSION,
    }


@router.get("/ready")
async def ready() -> dict[str, Any]:
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" not in available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready"},
        )
    return {"status": "ready"}


@router.get("/ready/deep", dependencies=[Depends(require_api_token), Depends(permission_dependency("models:read"))])
async def ready_deep(
    load_models: bool = Query(False),
    dummy_inference: bool = Query(False),
) -> dict[str, Any]:
    available = ort.get_available_providers()
    checks = []
    ok = "CUDAExecutionProvider" in available

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
            logger.warning("deep readiness model check failed for %s: %s", key, exception_log_summary(exc))
            ok = False
            checks.append({"model": key, "ok": False, "error": MODEL_READINESS_CHECK_FAILED})

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "available_providers": available, "checks": checks},
        )
    return {"status": "ready", "available_providers": available, "checks": checks}


@router.get("/metrics", dependencies=[Depends(require_api_token), Depends(permission_dependency("metrics:read"))])
async def metrics() -> Response:
    return Response(content=prometheus_metrics(), media_type="text/plain; version=0.0.4")
