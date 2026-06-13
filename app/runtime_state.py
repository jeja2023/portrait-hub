import asyncio
from collections import OrderedDict

from app.schemas import ModelBundle
from app.settings import GPU_DEVICE_IDS, GPU_QUEUE_LIMIT, GPU_QUEUE_LIMIT_PER_DEVICE


MODEL_REGISTRY: "OrderedDict[str, ModelBundle]" = OrderedDict()
MODEL_LOAD_LOCKS: dict[str, asyncio.Lock] = {}
REGISTRY_LOCK = asyncio.Lock()
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
