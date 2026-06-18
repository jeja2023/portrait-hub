from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort

from app.model_config import model_config
from app.observability import logger
from app.portrait_response import exception_log_summary
from app.settings import CPU_FALLBACK_ENABLED, CPU_PROVIDERS, CUDA_PROVIDERS, ENABLE_TENSORRT, TENSORRT_ENGINE_CACHE_ENABLE, TENSORRT_ENGINE_CACHE_PATH


def cuda_providers_for_device(device_id: int | None = None) -> list[Any]:
    if device_id is None:
        return CUDA_PROVIDERS
    providers: list[Any] = []
    for provider in CUDA_PROVIDERS:
        if isinstance(provider, tuple) and provider[0] == "CUDAExecutionProvider":
            options = dict(provider[1])
            options["device_id"] = int(device_id)
            providers.append((provider[0], options))
        else:
            providers.append(provider)
    return providers


def runtime_provider_status(available: list[str] | None = None) -> dict[str, Any]:
    providers = available if available is not None else ort.get_available_providers()
    return {
        "available_providers": providers,
        "cuda_available": "CUDAExecutionProvider" in providers,
        "cpu_available": "CPUExecutionProvider" in providers,
        "cpu_fallback_enabled": CPU_FALLBACK_ENABLED,
        "ready": "CUDAExecutionProvider" in providers or (CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in providers),
    }


def primary_execution_provider(active_providers: list[str]) -> str:
    for provider in active_providers:
        if provider in {"TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"}:
            return provider
    return active_providers[0] if active_providers else "unknown"


def uses_cpu_provider_only(providers: list[Any]) -> bool:
    return bool(providers) and all(provider == "CPUExecutionProvider" for provider in providers)


def io_meta(session: ort.InferenceSession) -> dict[str, Any]:
    return {
        "inputs": [
            {
                "name": item.name,
                "type": item.type,
                "shape": list(item.shape),
            }
            for item in session.get_inputs()
        ],
        "outputs": [
            {
                "name": item.name,
                "type": item.type,
                "shape": list(item.shape),
            }
            for item in session.get_outputs()
        ],
    }
def session_providers(
    cache_key_value: str | None = None,
    device_id: int | None = None,
    *,
    allow_cpu_fallback: bool = True,
) -> list[Any]:
    cuda_providers = cuda_providers_for_device(device_id)
    if cache_key_value is None:
        return cuda_providers

    config = model_config(cache_key_value)
    runtime = str(config.get("runtime") or "onnxruntime").strip().lower()
    if runtime not in {"tensorrt", "onnxruntime-tensorrt", "trt"}:
        return cuda_providers
    if not ENABLE_TENSORRT:
        raise RuntimeError("model requested TensorRT runtime but ENABLE_TENSORRT is false")

    available = set(ort.get_available_providers())
    if "TensorrtExecutionProvider" not in available:
        if "CUDAExecutionProvider" in available:
            logger.warning(
                "TensorRT provider is unavailable; falling back to CUDA for model: %s",
                cache_key_value,
            )
            return cuda_providers
        if allow_cpu_fallback and CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
            logger.warning(
                "TensorRT provider is unavailable; falling back to CPU for model: %s",
                cache_key_value,
            )
            return CPU_PROVIDERS
        raise RuntimeError(
            f"TensorrtExecutionProvider is not available. available providers: {sorted(available)}"
        )
    return [
        (
            "TensorrtExecutionProvider",
            {
                "trt_engine_cache_enable": TENSORRT_ENGINE_CACHE_ENABLE,
                "trt_engine_cache_path": TENSORRT_ENGINE_CACHE_PATH,
                **({"device_id": int(device_id)} if device_id is not None else {}),
            },
        ),
        *cuda_providers,
    ]


def create_session(model_path: Path, cache_key_value: str | None = None, device_id: int | None = None) -> ort.InferenceSession:
    available = set(ort.get_available_providers())
    if "CUDAExecutionProvider" not in available:
        if CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
            logger.warning(
                "CUDAExecutionProvider is not available; falling back to CPU. available providers: %s",
                sorted(available),
            )
            return ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS)
        raise RuntimeError(f"CUDAExecutionProvider is not available. available providers: {sorted(available)}")

    requested_providers = session_providers(cache_key_value, device_id=device_id)
    try:
        session = ort.InferenceSession(str(model_path), providers=requested_providers)
    except Exception as exc:
        if CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
            logger.warning("CUDA session creation failed; falling back to CPU: %s", exception_log_summary(exc))
            return ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS)
        raise
    active = session.get_providers()
    if uses_cpu_provider_only(requested_providers):
        if "CPUExecutionProvider" not in active:
            raise RuntimeError(f"model session did not enable CPU. active providers: {active}")
        return session
    if requested_providers and isinstance(requested_providers[0], tuple) and requested_providers[0][0] == "TensorrtExecutionProvider":
        if "TensorrtExecutionProvider" not in active:
            if CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
                logger.warning("model session did not enable TensorRT; falling back to CPU. active providers: %s", active)
                return ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS)
            raise RuntimeError(f"model session did not enable TensorRT. active providers: {active}")
    if "CUDAExecutionProvider" not in active:
        if CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
            logger.warning("model session did not enable CUDA; falling back to CPU. active providers: %s", active)
            return ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS)
        raise RuntimeError(f"model session did not enable CUDA. active providers: {active}")
    return session
def input_dtype(input_type: str) -> Any:
    if "double" in input_type:
        return np.float64
    if "float16" in input_type:
        return np.float16
    if "int64" in input_type:
        return np.int64
    if "int32" in input_type:
        return np.int32
    if "bool" in input_type:
        return np.bool_
    return np.float32
def run_session(session: ort.InferenceSession, input_array: np.ndarray) -> list[np.ndarray]:
    input_meta = session.get_inputs()[0]
    dtype = input_dtype(input_meta.type)
    if input_array.dtype != dtype:
        input_array = input_array.astype(dtype, copy=False)
    return session.run(None, {input_meta.name: input_array})
