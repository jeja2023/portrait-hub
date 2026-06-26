import asyncio
import gc
import hashlib
from pathlib import Path
from typing import Any, cast

from fastapi import HTTPException, status

from app.metrics import observe
from app.model_config import config_value, model_config
from app.model_package import model_hash, validate_model_hash
from app.observability import logger, now, wall_time
from app.portrait_response import exception_log_summary
from app.runtime_sessions import create_session, io_meta, primary_execution_provider
from app.runtime_state import MODEL_LOAD_LOCKS, MODEL_LOAD_RETRY_AFTER, MODEL_REGISTRY, REGISTRY_LOCK, gpu_device_ids
from app.schemas import ModelBundle
from app.settings import MAX_LOADED_MODELS, MODEL_CONCURRENCY_LIMIT, MODEL_LOAD_RETRY_COOLDOWN_SECONDS, MODEL_QUEUE_TIMEOUT_SECONDS


def bundle_providers(bundle: ModelBundle) -> list[str]:
    get_providers = getattr(bundle["session"], "get_providers", None)
    if callable(get_providers):
        return list(get_providers())
    provider = bundle.get("execution_provider")
    return [provider] if isinstance(provider, str) and provider else []


def bundle_info(cache_key_value: str, bundle: ModelBundle) -> dict[str, Any]:
    session = bundle["session"]
    providers = bundle_providers(bundle)
    return {
        "model": cache_key_value,
        "artifact_resolved": bool(bundle.get("path")),
        "model_hash": bundle["model_hash"],
        "file_size": bundle["file_size"],
        "loaded_at": bundle["loaded_at"],
        "last_used_at": bundle["last_used_at"],
        "load_count": bundle["load_count"],
        "inference_count": bundle["inference_count"],
        "max_concurrency": bundle.get("max_concurrency", 1),
        "queue_timeout_seconds": bundle.get("queue_timeout_seconds", 0),
        "gpu_device_id": bundle.get("gpu_device_id"),
        "execution_provider": bundle.get("execution_provider") or primary_execution_provider(providers),
        "providers": providers,
        **io_meta(session),
    }


def model_path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def model_runtime_limits(cache_key_value: str) -> tuple[int, float]:
    config = model_config(cache_key_value)
    raw_concurrency = config_value(config, "runtime", "max_concurrency", config.get("max_concurrency", MODEL_CONCURRENCY_LIMIT))
    raw_timeout = config_value(config, "runtime", "queue_timeout_seconds", config.get("queue_timeout_seconds", MODEL_QUEUE_TIMEOUT_SECONDS))
    try:
        max_concurrency = max(1, int(raw_concurrency))
    except (TypeError, ValueError):
        max_concurrency = max(1, MODEL_CONCURRENCY_LIMIT)
    try:
        queue_timeout = max(0.0, float(raw_timeout))
    except (TypeError, ValueError):
        queue_timeout = max(0.0, MODEL_QUEUE_TIMEOUT_SECONDS)
    return max_concurrency, queue_timeout


def model_gpu_device_id(cache_key_value: str) -> int:
    config = model_config(cache_key_value)
    raw_device = config_value(config, "runtime", "device_id", config.get("device_id"))
    devices = gpu_device_ids()
    try:
        device_id = int(raw_device)
    except (TypeError, ValueError):
        digest = hashlib.sha256(cache_key_value.encode("utf-8")).digest()
        return devices[int.from_bytes(digest[:4], "big") % len(devices)]
    return device_id if device_id in devices else devices[0]


def release_model_bundle(bundle: ModelBundle | None) -> None:
    if not bundle:
        return
    # 在强制 GC 前先移除 bundle 字典自身对 ONNX session 的强引用。仅删除局部变量名
    # 会让 session 仍可通过字典被引用，导致其 GPU 显存要等到 bundle 自身被回收时才释放。
    session = cast(dict[str, Any], bundle).pop("session", None)
    try:
        del session
    finally:
        gc.collect()


async def get_model_load_lock(cache_key_value: str) -> asyncio.Lock:
    async with REGISTRY_LOCK:
        lock = MODEL_LOAD_LOCKS.get(cache_key_value)
        if lock is None:
            lock = asyncio.Lock()
            MODEL_LOAD_LOCKS[cache_key_value] = lock
        return lock
async def evict_lru_if_needed(except_key: str | None = None) -> None:
    if MAX_LOADED_MODELS <= 0:
        return

    async with REGISTRY_LOCK:
        while len(MODEL_REGISTRY) > MAX_LOADED_MODELS:
            # Evict the genuinely least-recently-used model, but never one that has
            # an inference in flight (in_use > 0) — releasing its session mid-run is
            # undefined behaviour. If every evictable model is busy, stop and let the
            # registry exceed the cap transiently rather than corrupt a live session.
            evictable = [
                key
                for key, bundle in MODEL_REGISTRY.items()
                if key != except_key and not int(bundle.get("in_use", 0))
            ]
            if not evictable:
                return
            evict_key = min(evictable, key=lambda key: MODEL_REGISTRY[key].get("last_used_at", 0.0))
            removed = MODEL_REGISTRY.pop(evict_key, None)
            MODEL_LOAD_LOCKS.pop(evict_key, None)
            release_model_bundle(removed)
            observe("model_unloads_total")
            logger.info("evicted model from cache: %s", evict_key)


async def unload_model_by_key(cache_key_value: str) -> bool:
    async with REGISTRY_LOCK:
        removed = MODEL_REGISTRY.pop(cache_key_value, None)
        MODEL_LOAD_LOCKS.pop(cache_key_value, None)
    if removed is not None:
        release_model_bundle(removed)
        observe("model_unloads_total")
        logger.info("unloaded model: %s", cache_key_value)
        return True
    return False


async def touch_model(cache_key_value: str, bundle: ModelBundle) -> None:
    bundle["last_used_at"] = wall_time()
    async with REGISTRY_LOCK:
        if cache_key_value in MODEL_REGISTRY:
            MODEL_REGISTRY.move_to_end(cache_key_value)


def model_load_cooldown_active(cache_key_value: str) -> bool:
    return float(MODEL_LOAD_RETRY_AFTER.get(cache_key_value, 0.0)) > wall_time()


def mark_model_load_failed(cache_key_value: str) -> None:
    # 记录“重试时间戳”，冷却窗口内的后续请求直接快速失败；窗口过后自动重试实现自愈。
    # 冷却时间 <=0 表示关闭冷却（不写标记，保持每次请求都重试的旧行为）。
    if MODEL_LOAD_RETRY_COOLDOWN_SECONDS > 0:
        MODEL_LOAD_RETRY_AFTER[cache_key_value] = wall_time() + MODEL_LOAD_RETRY_COOLDOWN_SECONDS


def raise_model_load_cooldown(cache_key_value: str) -> None:
    observe("model_load_cooldown_rejections_total")
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="model load is cooling down after a recent failure",
    )


async def get_or_load_model(
    cache_key_value: str,
    model_path: Path,
) -> tuple[ModelBundle, bool, float]:
    bundle = MODEL_REGISTRY.get(cache_key_value)
    if bundle is not None:
        observe("cache_hits_total")
        await touch_model(cache_key_value, bundle)
        return bundle, False, 0

    observe("cache_misses_total")
    # 快速路径：上次加载失败且仍在冷却窗口内，直接 503，不去排队抢加载锁、也不重做昂贵的加载。
    if model_load_cooldown_active(cache_key_value):
        raise_model_load_cooldown(cache_key_value)
    load_lock = await get_model_load_lock(cache_key_value)
    async with load_lock:
        bundle = MODEL_REGISTRY.get(cache_key_value)
        if bundle is not None:
            observe("cache_hits_total")
            await touch_model(cache_key_value, bundle)
            return bundle, False, 0
        # 持锁后再次确认：等锁期间可能已有并发请求把该模型置于冷却。
        if model_load_cooldown_active(cache_key_value):
            raise_model_load_cooldown(cache_key_value)

        start = now()
        logger.info("loading model: %s path_hash=%s", cache_key_value, model_path_fingerprint(model_path))
        try:
            digest = await asyncio.to_thread(model_hash, model_path)
            validate_model_hash(cache_key_value, digest)
            gpu_device_id = model_gpu_device_id(cache_key_value)
            session = await asyncio.to_thread(create_session, model_path, cache_key_value, gpu_device_id)
            execution_provider = primary_execution_provider(session.get_providers())
        except HTTPException:
            observe("model_load_errors_total")
            mark_model_load_failed(cache_key_value)
            raise
        except Exception as exc:
            observe("model_load_errors_total")
            mark_model_load_failed(cache_key_value)
            logger.warning("failed to load model: %s error=%s", cache_key_value, exception_log_summary(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="failed to load model runtime",
            ) from exc

        # 加载成功，清除任何残留的冷却标记，使该模型恢复正常服务。
        MODEL_LOAD_RETRY_AFTER.pop(cache_key_value, None)
        load_seconds = now() - start
        stat = model_path.stat()
        max_concurrency, queue_timeout = model_runtime_limits(cache_key_value)
        bundle = {
            "session": session,
            "lock": asyncio.Lock(),
            "semaphore": asyncio.Semaphore(max_concurrency),
            "gpu_device_id": gpu_device_id,
            "path": str(model_path),
            "model_hash": digest,
            "file_size": stat.st_size,
            "loaded_at": wall_time(),
            "last_used_at": wall_time(),
            "load_count": 1,
            "inference_count": 0,
            "in_use": 0,
            "max_concurrency": max_concurrency,
            "queue_timeout_seconds": queue_timeout,
            "execution_provider": execution_provider,
        }
        MODEL_REGISTRY[cache_key_value] = bundle
        await touch_model(cache_key_value, bundle)
        observe("model_loads_total")
        observe("model_load_seconds_sum", load_seconds)
        await evict_lru_if_needed(except_key=cache_key_value)
        logger.info(
            "model loaded: %s execution_provider=%s device_id=%s load_seconds=%.6f hash=%s",
            cache_key_value,
            execution_provider,
            gpu_device_id,
            load_seconds,
            digest,
        )
        return bundle, True, load_seconds


__all__ = [
    "bundle_providers",
    "bundle_info",
    "model_path_fingerprint",
    "model_runtime_limits",
    "model_gpu_device_id",
    "release_model_bundle",
    "get_model_load_lock",
    "evict_lru_if_needed",
    "unload_model_by_key",
    "touch_model",
    "get_or_load_model",
    "model_load_cooldown_active",
    "mark_model_load_failed",
]
