from collections.abc import Callable
from typing import ParamSpec, TypeVar
import asyncio


P = ParamSpec("P")
T = TypeVar("T")


async def run_blocking_io(func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """运行已知的阻塞式持久化或网络 I/O，而不会卡住事件循环。"""
    def call() -> T:
        return func(*args, **kwargs)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, call)
