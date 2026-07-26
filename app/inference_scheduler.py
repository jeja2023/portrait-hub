import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from fastapi import HTTPException, status

from app.metrics import observe, observe_histogram
from app.observability import now
from app.schemas import ModelBundle

Array = npt.NDArray[Any]
ExecutionResult = tuple[list[Array], float, float]
Executor = Callable[[ModelBundle, Array], Awaitable[ExecutionResult]]


@dataclass(slots=True)
class ScheduledInference:
    input_array: Array
    future: asyncio.Future[ExecutionResult]
    enqueued_at: float
    deadline: float | None
    scope: str
    priority: int
    weight: int
    group_key: tuple[Any, ...]

    @property
    def size(self) -> int:
        return int(self.input_array.shape[0]) if self.input_array.ndim > 0 else 1


_SCHEDULERS: set["InferenceScheduler"] = set()


def dynamic_batch_queue_depth() -> int:
    return sum(scheduler.queue_depth for scheduler in tuple(_SCHEDULERS))


class InferenceScheduler:
    def __init__(self, bundle: ModelBundle, executor: Executor) -> None:
        self.bundle = bundle
        self.executor = executor
        self.max_batch_size = max(1, int(bundle.get("dynamic_batch_max_size", 1)))
        self.max_wait_ms = max(0.0, float(bundle.get("dynamic_batch_max_wait_ms", 0.0)))
        self.async_max_wait_ms = max(
            self.max_wait_ms,
            float(bundle.get("dynamic_batch_async_max_wait_ms", self.max_wait_ms)),
        )
        self.max_queue_size = max(1, int(bundle.get("dynamic_batch_max_queue_size", 1)))
        self._queue: deque[ScheduledInference] = deque()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._loop = asyncio.get_running_loop()
        self._last_scope: str | None = None
        _SCHEDULERS.add(self)

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    def compatible_with_current_loop(self) -> bool:
        try:
            return self._loop is asyncio.get_running_loop()
        except RuntimeError:
            return False

    async def submit(
        self,
        input_array: Array,
        *,
        scope: str,
        priority: str,
        weight: int,
        timeout_seconds: float,
    ) -> ExecutionResult:
        self._discard_finished()
        if len(self._queue) >= self.max_queue_size:
            observe("dynamic_batch_dropped_total")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="dynamic batching queue is full",
            )

        loop = asyncio.get_running_loop()
        enqueued_at = now()
        request = ScheduledInference(
            input_array=input_array,
            future=loop.create_future(),
            enqueued_at=enqueued_at,
            deadline=enqueued_at + timeout_seconds if timeout_seconds > 0 else None,
            scope=scope or "anonymous",
            priority=0 if priority == "sync" else 1,
            weight=max(1, min(100, int(weight))),
            group_key=self._group_key(input_array),
        )
        self._queue.append(request)
        _SCHEDULERS.add(self)
        observe("dynamic_batch_enqueued_total")
        self._wake.set()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="portrait-dynamic-batch")
        try:
            return await request.future
        except asyncio.CancelledError:
            request.future.cancel()
            observe("dynamic_batch_cancelled_total")
            self._wake.set()
            raise

    def _group_key(self, input_array: Array) -> tuple[Any, ...]:
        shape = tuple(input_array.shape[1:]) if input_array.ndim > 0 else ()
        return (
            str(self.bundle.get("key", "")),
            shape,
            str(input_array.dtype),
            str(self.bundle.get("contract_version", "1")),
            self.bundle.get("gpu_device_id"),
            self.bundle.get("execution_provider"),
        )

    def _discard_finished(self) -> None:
        active: deque[ScheduledInference] = deque()
        current = now()
        while self._queue:
            item = self._queue.popleft()
            if item.future.cancelled():
                continue
            if item.deadline is not None and current >= item.deadline:
                observe("dynamic_batch_timeouts_total")
                item.future.set_exception(
                    HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="dynamic batching queue timeout",
                    )
                )
                continue
            active.append(item)
        self._queue = active

    def _batch_ready(self, seed: ScheduledInference) -> bool:
        total = sum(
            item.size
            for item in self._queue
            if item.priority == seed.priority and item.group_key == seed.group_key
        )
        return total >= self.max_batch_size

    def _wait_seconds(self, seed: ScheduledInference) -> float:
        configured_ms = self.max_wait_ms if seed.priority == 0 else self.async_max_wait_ms
        dispatch_at = seed.enqueued_at + configured_ms / 1000.0
        if seed.deadline is not None:
            dispatch_at = min(dispatch_at, seed.deadline)
        return max(0.0, dispatch_at - now())

    async def _run(self) -> None:
        try:
            while True:
                self._discard_finished()
                if not self._queue:
                    return
                seed = min(self._queue, key=lambda item: (item.priority, item.enqueued_at))
                wait_seconds = self._wait_seconds(seed)
                if wait_seconds > 0 and not self._batch_ready(seed):
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=wait_seconds)
                    except TimeoutError:
                        pass
                    continue
                batch = self._take_fair_batch(seed)
                await self._dispatch(batch)
        finally:
            self._worker = None
            if not self._queue:
                _SCHEDULERS.discard(self)

    def _take_fair_batch(self, seed: ScheduledInference) -> list[ScheduledInference]:
        candidates = [
            item
            for item in self._queue
            if item.priority == seed.priority and item.group_key == seed.group_key
        ]
        by_scope: dict[str, deque[ScheduledInference]] = defaultdict(deque)
        for item in candidates:
            by_scope[item.scope].append(item)
        scopes = list(by_scope)
        if self._last_scope in scopes and len(scopes) > 1:
            offset = (scopes.index(self._last_scope) + 1) % len(scopes)
            scopes = scopes[offset:] + scopes[:offset]

        selected: list[ScheduledInference] = []
        selected_size = 0
        while scopes and selected_size < self.max_batch_size:
            size_before_round = selected_size
            remaining_scopes: list[str] = []
            for scope in scopes:
                queue = by_scope[scope]
                allowance = queue[0].weight if queue else 1
                for _ in range(allowance):
                    if not queue:
                        break
                    item = queue[0]
                    if selected and selected_size + item.size > self.max_batch_size:
                        break
                    queue.popleft()
                    selected.append(item)
                    selected_size += item.size
                    self._last_scope = scope
                    if selected_size >= self.max_batch_size:
                        break
                if queue:
                    remaining_scopes.append(scope)
                if selected_size >= self.max_batch_size:
                    break
            if selected_size == size_before_round:
                break
            scopes = remaining_scopes

        if not selected:
            selected = [seed]
        selected_ids = {id(item) for item in selected}
        self._queue = deque(item for item in self._queue if id(item) not in selected_ids)
        return selected

    async def _dispatch(self, batch: list[ScheduledInference]) -> None:
        active = [item for item in batch if not item.future.cancelled()]
        if not active:
            return
        observe("dynamic_batches_total")
        actual_size = sum(item.size for item in active)
        observe("dynamic_batch_items_total", actual_size)
        observe("dynamic_batch_utilization_sum", actual_size / self.max_batch_size)
        observe_histogram("dynamic_batch_size", float(actual_size))
        for item in active:
            observe_histogram("dynamic_batch_wait_seconds", max(0.0, now() - item.enqueued_at))

        if len(active) == 1:
            await self._execute_one(active[0])
            return
        try:
            combined = np.concatenate([item.input_array for item in active], axis=0)
            outputs, model_queue_seconds, inference_seconds = await self.executor(self.bundle, combined)
            split_outputs = self._split_outputs(outputs, [item.size for item in active])
        except Exception:
            observe("dynamic_batch_fallback_total")
            await asyncio.gather(*(self._execute_one(item) for item in active))
            return

        for item, item_outputs in zip(active, split_outputs, strict=True):
            if item.future.cancelled() or item.future.done():
                continue
            scheduler_wait = max(0.0, now() - item.enqueued_at - inference_seconds)
            item.future.set_result(
                (item_outputs, scheduler_wait + model_queue_seconds, inference_seconds)
            )

    async def _execute_one(self, item: ScheduledInference) -> None:
        if item.future.cancelled() or item.future.done():
            return
        try:
            outputs, model_queue_seconds, inference_seconds = await self.executor(
                self.bundle, item.input_array
            )
        except Exception as exc:
            if not item.future.done():
                item.future.set_exception(exc)
            return
        if not item.future.done():
            scheduler_wait = max(0.0, now() - item.enqueued_at - inference_seconds)
            item.future.set_result(
                (outputs, scheduler_wait + model_queue_seconds, inference_seconds)
            )

    @staticmethod
    def _split_outputs(outputs: list[Array], sizes: list[int]) -> list[list[Array]]:
        total = sum(sizes)
        for output in outputs:
            if output.ndim == 0 or output.shape[0] != total:
                raise ValueError("batched output does not preserve the input batch dimension")
        grouped: list[list[Array]] = [[] for _ in sizes]
        start = 0
        for index, size in enumerate(sizes):
            end = start + size
            grouped[index] = [output[start:end] for output in outputs]
            start = end
        return grouped


__all__ = ["InferenceScheduler", "dynamic_batch_queue_depth"]
