import asyncio
from collections import OrderedDict

from app.schemas import ModelBundle
from app.settings import GPU_DEVICE_IDS, GPU_QUEUE_LIMIT, GPU_QUEUE_LIMIT_PER_DEVICE

MODEL_REGISTRY: "OrderedDict[str, ModelBundle]" = OrderedDict()
MODEL_LOAD_LOCKS: dict[str, asyncio.Lock] = {}
REGISTRY_LOCK = asyncio.Lock()
# 每个模型 cache_key 的加载“重试时间戳”（epoch 秒）。值在未来表示该模型刚加载失败、正在冷却；
# 缺失或值已过期表示可正常尝试加载。加载成功后清除对应条目。
MODEL_LOAD_RETRY_AFTER: dict[str, float] = {}
GPU_SEMAPHORE = asyncio.Semaphore(max(1, GPU_QUEUE_LIMIT))
GPU_DEVICE_SEMAPHORES = {
    int(device_id): asyncio.Semaphore(max(1, GPU_QUEUE_LIMIT_PER_DEVICE))
    for device_id in GPU_DEVICE_IDS
}
GPU_QUEUE_WAITERS = 0
GPU_DEVICE_QUEUE_WAITERS = {int(device_id): 0 for device_id in GPU_DEVICE_SEMAPHORES}


def gpu_device_ids() -> list[int]:
    try:
        from app.model_config_state import MODEL_CONFIGS

        pending_ids: set[int] = set()
        for config in MODEL_CONFIGS.values():
            runtime = config.get("runtime")
            raw_device = runtime.get("device_id") if isinstance(runtime, dict) else config.get("device_id")
            if isinstance(raw_device, bool) or not isinstance(raw_device, (int, str)):
                continue
            try:
                device_id = int(raw_device)
            except (TypeError, ValueError):
                continue
            if device_id not in GPU_DEVICE_SEMAPHORES:
                pending_ids.add(device_id)
        if pending_ids:
            from app.metrics import gpu_memory_metrics

            detected_ids = {int(item["device"]) for item in gpu_memory_metrics()}
            for device_id in pending_ids & detected_ids:
                GPU_DEVICE_SEMAPHORES[device_id] = asyncio.Semaphore(max(1, GPU_QUEUE_LIMIT_PER_DEVICE))
                GPU_DEVICE_QUEUE_WAITERS[device_id] = 0
    except Exception:
        pass
    return list(GPU_DEVICE_SEMAPHORES.keys()) or [0]


def gpu_semaphore_for_device(device_id: int | None) -> asyncio.Semaphore:
    if device_id is None:
        return GPU_SEMAPHORE
    return GPU_DEVICE_SEMAPHORES.get(int(device_id), GPU_SEMAPHORE)


def gpu_queue_depth_for_device(device_id: int | None) -> int:
    if device_id is None or int(device_id) not in GPU_DEVICE_SEMAPHORES:
        return max(0, GPU_QUEUE_WAITERS)
    return max(0, GPU_DEVICE_QUEUE_WAITERS.get(int(device_id), 0))


def increment_gpu_queue_waiters(device_id: int | None) -> None:
    global GPU_QUEUE_WAITERS
    if device_id is None or int(device_id) not in GPU_DEVICE_SEMAPHORES:
        GPU_QUEUE_WAITERS += 1
        return
    device_key = int(device_id)
    GPU_DEVICE_QUEUE_WAITERS[device_key] = GPU_DEVICE_QUEUE_WAITERS.get(device_key, 0) + 1


def decrement_gpu_queue_waiters(device_id: int | None) -> None:
    global GPU_QUEUE_WAITERS
    if device_id is None or int(device_id) not in GPU_DEVICE_SEMAPHORES:
        GPU_QUEUE_WAITERS = max(0, GPU_QUEUE_WAITERS - 1)
        return
    device_key = int(device_id)
    GPU_DEVICE_QUEUE_WAITERS[device_key] = max(0, GPU_DEVICE_QUEUE_WAITERS.get(device_key, 0) - 1)


__all__ = [
    "GPU_DEVICE_QUEUE_WAITERS",
    "GPU_DEVICE_SEMAPHORES",
    "GPU_QUEUE_WAITERS",
    "GPU_SEMAPHORE",
    "MODEL_LOAD_LOCKS",
    "MODEL_LOAD_RETRY_AFTER",
    "MODEL_REGISTRY",
    "REGISTRY_LOCK",
    "decrement_gpu_queue_waiters",
    "gpu_device_ids",
    "gpu_queue_depth_for_device",
    "gpu_semaphore_for_device",
    "increment_gpu_queue_waiters",
]
