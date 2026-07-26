import asyncio
from typing import Any

import numpy as np
import pytest
from fastapi import HTTPException

from app.inference_scheduler import InferenceScheduler
from app.runtime_execution import run_model_bundle


def scheduler_bundle(**overrides: Any) -> dict[str, Any]:
    return {
        "key": "portrait/test.onnx",
        "gpu_device_id": 0,
        "execution_provider": "CPUExecutionProvider",
        "contract_version": "1",
        "dynamic_batching_enabled": True,
        "dynamic_batch_max_size": 4,
        "dynamic_batch_max_wait_ms": 50.0,
        "dynamic_batch_async_max_wait_ms": 100.0,
        "dynamic_batch_max_queue_size": 32,
        "queue_timeout_seconds": 0.0,
        **overrides,
    }


@pytest.mark.asyncio
async def test_scheduler_combines_compatible_requests_and_splits_outputs() -> None:
    calls: list[tuple[int, ...]] = []

    async def execute(bundle, input_array):
        calls.append(input_array.shape)
        return [input_array * 2, input_array + 1], 0.0, 0.001

    scheduler = InferenceScheduler(scheduler_bundle(), execute)
    tasks = [
        asyncio.create_task(
            scheduler.submit(
                np.asarray([[value]], dtype=np.float32),
                scope=f"tenant-{value % 2}",
                priority="sync",
                weight=1,
                timeout_seconds=1.0,
            )
        )
        for value in range(4)
    ]

    results = await asyncio.gather(*tasks)

    assert calls == [(4, 1)]
    assert [result[0][0].item() for result in results] == [0.0, 2.0, 4.0, 6.0]
    assert [result[0][1].item() for result in results] == [1.0, 2.0, 3.0, 4.0]


@pytest.mark.asyncio
async def test_scheduler_round_robins_scopes_without_starvation() -> None:
    dispatched: list[list[int]] = []

    async def execute(bundle, input_array):
        dispatched.append([int(value) for value in input_array[:, 0]])
        return [input_array], 0.0, 0.0

    scheduler = InferenceScheduler(
        scheduler_bundle(dynamic_batch_max_size=2, dynamic_batch_max_wait_ms=10.0),
        execute,
    )
    requests = [
        (1, "tenant-a"),
        (2, "tenant-a"),
        (3, "tenant-a"),
        (9, "tenant-b"),
    ]
    tasks = [
        asyncio.create_task(
            scheduler.submit(
                np.asarray([[value]], dtype=np.float32),
                scope=scope,
                priority="sync",
                weight=1,
                timeout_seconds=1.0,
            )
        )
        for value, scope in requests
    ]

    await asyncio.gather(*tasks)

    assert set(dispatched[0]) == {1, 9}
    assert sorted(value for batch in dispatched for value in batch) == [1, 2, 3, 9]


@pytest.mark.asyncio
async def test_sync_request_overtakes_waiting_async_request() -> None:
    dispatched: list[int] = []

    async def execute(bundle, input_array):
        dispatched.extend(int(value) for value in input_array[:, 0])
        return [input_array], 0.0, 0.0

    scheduler = InferenceScheduler(
        scheduler_bundle(
            dynamic_batch_max_size=2,
            dynamic_batch_max_wait_ms=0.0,
            dynamic_batch_async_max_wait_ms=100.0,
        ),
        execute,
    )
    async_task = asyncio.create_task(
        scheduler.submit(
            np.asarray([[2]], dtype=np.float32),
            scope="tenant-a",
            priority="async",
            weight=1,
            timeout_seconds=1.0,
        )
    )
    await asyncio.sleep(0)
    sync_task = asyncio.create_task(
        scheduler.submit(
            np.asarray([[1]], dtype=np.float32),
            scope="tenant-b",
            priority="sync",
            weight=1,
            timeout_seconds=1.0,
        )
    )

    await asyncio.gather(async_task, sync_task)

    assert dispatched == [1, 2]


@pytest.mark.asyncio
async def test_scheduler_enforces_queue_limit_timeout_and_cancellation() -> None:
    async def execute(bundle, input_array):
        return [input_array], 0.0, 0.0

    limited = InferenceScheduler(
        scheduler_bundle(
            dynamic_batch_max_size=8,
            dynamic_batch_max_wait_ms=100.0,
            dynamic_batch_max_queue_size=1,
        ),
        execute,
    )
    queued = asyncio.create_task(
        limited.submit(
            np.ones((1, 1), dtype=np.float32),
            scope="tenant-a",
            priority="sync",
            weight=1,
            timeout_seconds=1.0,
        )
    )
    await asyncio.sleep(0)
    with pytest.raises(HTTPException) as full:
        await limited.submit(
            np.ones((1, 1), dtype=np.float32),
            scope="tenant-b",
            priority="sync",
            weight=1,
            timeout_seconds=1.0,
        )
    assert full.value.status_code == 429
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued

    expiring = InferenceScheduler(
        scheduler_bundle(dynamic_batch_max_size=8, dynamic_batch_max_wait_ms=100.0),
        execute,
    )
    with pytest.raises(HTTPException) as timeout:
        await expiring.submit(
            np.ones((1, 1), dtype=np.float32),
            scope="tenant-a",
            priority="sync",
            weight=1,
            timeout_seconds=0.005,
        )
    assert timeout.value.status_code == 503


@pytest.mark.asyncio
async def test_batch_failure_is_retried_and_isolated_per_request() -> None:
    calls: list[int] = []

    async def execute(bundle, input_array):
        calls.append(input_array.shape[0])
        if input_array.shape[0] > 1 or int(input_array[0, 0]) == 2:
            raise RuntimeError("inference failed")
        return [input_array], 0.0, 0.0

    scheduler = InferenceScheduler(
        scheduler_bundle(dynamic_batch_max_size=2, dynamic_batch_max_wait_ms=10.0),
        execute,
    )
    results = await asyncio.gather(
        scheduler.submit(
            np.asarray([[1]], dtype=np.float32),
            scope="tenant-a",
            priority="sync",
            weight=1,
            timeout_seconds=1.0,
        ),
        scheduler.submit(
            np.asarray([[2]], dtype=np.float32),
            scope="tenant-b",
            priority="sync",
            weight=1,
            timeout_seconds=1.0,
        ),
        return_exceptions=True,
    )

    assert results[0][0][0].item() == 1.0
    assert isinstance(results[1], RuntimeError)
    assert calls == [2, 1, 1]


@pytest.mark.asyncio
async def test_disabled_dynamic_batching_preserves_direct_execution(monkeypatch) -> None:
    called = []

    async def direct(bundle, input_array):
        called.append(input_array.shape)
        return [input_array], 0.1, 0.2

    monkeypatch.setattr("app.runtime_execution._run_model_bundle_direct", direct)
    bundle = scheduler_bundle(dynamic_batching_enabled=False)

    result = await run_model_bundle(bundle, np.ones((1, 2), dtype=np.float32))

    assert called == [(1, 2)]
    assert result[1:] == (0.1, 0.2)
