from collections.abc import Callable
from functools import partial
from typing import ParamSpec, TypeVar
import asyncio


P = ParamSpec("P")
T = TypeVar("T")


async def run_blocking_io(func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Run known blocking persistence or network IO without pinning the event loop."""
    if kwargs:
        return await asyncio.to_thread(partial(func, *args, **kwargs))
    return await asyncio.to_thread(func, *args)
