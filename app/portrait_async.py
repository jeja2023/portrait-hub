import asyncio
import threading
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import ParamSpec, TypeVar, cast

from app.settings import BLOCKING_IO_THREAD_POOL_SIZE

P = ParamSpec("P")
T = TypeVar("T")
R = TypeVar("R")

_IO_EXECUTOR: ThreadPoolExecutor | None = None
_IO_EXECUTOR_LOCK = threading.Lock()


def io_executor() -> ThreadPoolExecutor:
    """阻塞式持久化/网络 IO 专用线程池。

    与 asyncio 默认线程池分开：默认池留给 asyncio.to_thread 承载的 ONNX 推理与媒体解码，
    避免控制面和存储 IO 把线程占满后，推理请求排队等待。
    """
    global _IO_EXECUTOR
    if _IO_EXECUTOR is None:
        with _IO_EXECUTOR_LOCK:
            if _IO_EXECUTOR is None:
                _IO_EXECUTOR = ThreadPoolExecutor(
                    max_workers=BLOCKING_IO_THREAD_POOL_SIZE,
                    thread_name_prefix="portrait-io",
                )
    return _IO_EXECUTOR


def shutdown_io_executor() -> None:
    """关闭 IO 线程池；服务停机时调用，等待在途的持久化写入完成。"""
    global _IO_EXECUTOR
    with _IO_EXECUTOR_LOCK:
        executor = _IO_EXECUTOR
        _IO_EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=True)


async def run_blocking_io(func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Run known blocking persistence or network I/O without blocking the event loop."""

    def call() -> T:
        return func(*args, **kwargs)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(io_executor(), call)


async def gather_limited(
    items: Sequence[T],
    worker: Callable[[int, T], Awaitable[R]],
    *,
    limit: int,
) -> list[R]:
    """Run async workers with bounded concurrency and fail-fast cancellation."""
    if not items:
        return []

    worker_count = min(max(1, int(limit)), len(items))
    results: list[R | None] = [None] * len(items)
    next_item = 0
    next_item_lock = asyncio.Lock()
    stopping = asyncio.Event()

    async def take_next_index() -> int | None:
        nonlocal next_item
        async with next_item_lock:
            if stopping.is_set() or next_item >= len(items):
                return None
            index = next_item
            next_item += 1
            return index

    async def run_worker() -> None:
        while True:
            index = await take_next_index()
            if index is None:
                return
            try:
                results[index] = await worker(index, items[index])
            except Exception:
                stopping.set()
                raise

    tasks = [asyncio.create_task(run_worker()) for _ in range(worker_count)]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        stopping.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    except Exception:
        stopping.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [cast(R, item) for item in results]
