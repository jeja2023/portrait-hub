from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import onnxruntime as ort

from app.metrics import record_cpu_fallback, record_session_provider
from app.model_config import model_config
from app.observability import logger
from app.portrait_response import exception_log_summary
from app.settings import (
    CPU_FALLBACK_ENABLED,
    CPU_PROVIDERS,
    CUDA_PROVIDERS,
    ENABLE_TENSORRT,
    FORCE_CPU,
    TENSORRT_ENGINE_CACHE_ENABLE,
    TENSORRT_ENGINE_CACHE_PATH,
)

Array = npt.NDArray[Any]


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
    cuda_available = "CUDAExecutionProvider" in providers
    cpu_available = "CPUExecutionProvider" in providers
    if FORCE_CPU:
        ready = cpu_available
    else:
        ready = cuda_available or (CPU_FALLBACK_ENABLED and cpu_available)
    return {
        "available_providers": providers,
        "cuda_available": cuda_available,
        "cpu_available": cpu_available,
        "cpu_fallback_enabled": CPU_FALLBACK_ENABLED,
        "force_cpu": FORCE_CPU,
        "ready": ready,
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
    if FORCE_CPU:
        return CPU_PROVIDERS
    cuda_providers = cuda_providers_for_device(device_id)
    if cache_key_value is None:
        return cuda_providers

    config = model_config(cache_key_value)
    runtime = str(config.get("runtime") or "onnxruntime").strip().lower()
    if runtime not in {"tensorrt", "onnxruntime-tensorrt", "trt"}:
        return cuda_providers
    if not ENABLE_TENSORRT:
        raise RuntimeError("模型请求 TensorRT 运行时，但 ENABLE_TENSORRT 为 false")

    available = set(ort.get_available_providers())
    if "TensorrtExecutionProvider" not in available:
        if "CUDAExecutionProvider" in available:
            logger.warning(
                "TensorRT provider 不可用；模型回退到 CUDA: %s",
                cache_key_value,
            )
            return cuda_providers
        if allow_cpu_fallback and CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
            logger.warning(
                "TensorRT provider 不可用；模型回退到 CPU: %s",
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


def _finalize_session(session: ort.InferenceSession, *, fallback_reason: str | None = None) -> ort.InferenceSession:
    # 记录会话最终落在哪个 provider；fallback_reason 非空时再计一次“本想用 GPU 却回退 CPU”。
    if fallback_reason is not None:
        record_cpu_fallback(fallback_reason)
    get_providers = getattr(session, "get_providers", None)
    active = get_providers() if callable(get_providers) else []
    record_session_provider(primary_execution_provider(active) if active else "unknown")
    return session


def create_session(model_path: Path, cache_key_value: str | None = None, device_id: int | None = None) -> ort.InferenceSession:
    available = set(ort.get_available_providers())
    if FORCE_CPU:
        # 显式纯 CPU：一次性建 CPU 会话，跳过 CUDA 探测与“active 无 CUDA”导致的丢弃重建。
        # 这是主动选择而非回退，故只计 provider、不计 cpu_fallback。
        if "CPUExecutionProvider" not in available:
            raise RuntimeError(
                f"FORCE_CPU is set but CPUExecutionProvider is not available. available providers: {sorted(available)}"
            )
        logger.info("FORCE_CPU enabled; creating CPU-only session for model: %s", cache_key_value or model_path.name)
        return _finalize_session(ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS))
    if "CUDAExecutionProvider" not in available:
        if CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
            logger.warning(
                "CUDAExecutionProvider 不可用；回退到 CPU。可用 providers: %s",
                sorted(available),
            )
            return _finalize_session(
                ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS),
                fallback_reason="cuda_provider_unavailable",
            )
        raise RuntimeError(f"CUDAExecutionProvider 不可用。可用提供程序： {sorted(available)}")

    requested_providers = session_providers(cache_key_value, device_id=device_id)
    try:
        session = ort.InferenceSession(str(model_path), providers=requested_providers)
    except Exception as exc:
        if CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
            logger.warning("CUDA session 创建失败；回退到 CPU: %s", exception_log_summary(exc))
            return _finalize_session(
                ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS),
                fallback_reason="session_init_failed",
            )
        raise
    active = session.get_providers()
    if uses_cpu_provider_only(requested_providers):
        if "CPUExecutionProvider" not in active:
            raise RuntimeError(f"模型会话未启用 CPU。活动提供程序： {active}")
        return _finalize_session(session)
    if requested_providers and isinstance(requested_providers[0], tuple) and requested_providers[0][0] == "TensorrtExecutionProvider":
        if "TensorrtExecutionProvider" not in active:
            if CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
                logger.warning("模型 session 未启用 TensorRT；回退到 CPU。活跃 providers: %s", active)
                return _finalize_session(
                    ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS),
                    fallback_reason="tensorrt_not_active",
                )
            raise RuntimeError(f"模型会话未启用 TensorRT。活动提供程序： {active}")
    if "CUDAExecutionProvider" not in active:
        if CPU_FALLBACK_ENABLED and "CPUExecutionProvider" in available:
            logger.warning("模型 session 未启用 CUDA；回退到 CPU。活跃 providers: %s", active)
            return _finalize_session(
                ort.InferenceSession(str(model_path), providers=CPU_PROVIDERS),
                fallback_reason="cuda_not_active",
            )
        raise RuntimeError(f"模型会话未启用 CUDA。活动提供程序： {active}")
    return _finalize_session(session)
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
def run_session(session: ort.InferenceSession, input_array: Array) -> list[Array]:
    input_meta = session.get_inputs()[0]
    dtype = input_dtype(input_meta.type)
    if input_array.dtype != dtype:
        input_array = input_array.astype(dtype, copy=False)
    outputs = session.run(None, {input_meta.name: input_array})
    return [np.asarray(output) for output in outputs]


__all__ = [
    "create_session",
    "cuda_providers_for_device",
    "input_dtype",
    "io_meta",
    "primary_execution_provider",
    "run_session",
    "runtime_provider_status",
    "session_providers",
    "uses_cpu_provider_only",
]
