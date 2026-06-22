from collections.abc import Callable
from typing import ParamSpec, TypeVar
import asyncio


P = ParamSpec("P")
T = TypeVar("T")


async def run_blocking_io(func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Run known blocking persistence or network IO without pinning the event loop."""
    def call() -> T:
        return func(*args, **kwargs)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, call)
