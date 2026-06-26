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


def gpu_device_ids() -> list[int]:
    return list(GPU_DEVICE_SEMAPHORES.keys()) or [0]


def gpu_semaphore_for_device(device_id: int | None) -> asyncio.Semaphore:
    if device_id is None:
        return GPU_SEMAPHORE
    return GPU_DEVICE_SEMAPHORES.get(int(device_id), GPU_SEMAPHORE)


__all__ = [
    "MODEL_REGISTRY",
    "MODEL_LOAD_LOCKS",
    "MODEL_LOAD_RETRY_AFTER",
    "REGISTRY_LOCK",
    "GPU_SEMAPHORE",
    "GPU_DEVICE_SEMAPHORES",
    "gpu_device_ids",
    "gpu_semaphore_for_device",
]
