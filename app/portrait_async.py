import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import ParamSpec, TypeVar, cast

P = ParamSpec("P")
T = TypeVar("T")
R = TypeVar("R")


async def run_blocking_io(func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Run known blocking persistence or network I/O without blocking the event loop."""

    def call() -> T:
        return func(*args, **kwargs)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, call)


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
