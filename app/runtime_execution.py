import asyncio
import hashlib
from collections import deque
from typing import Any, cast

import numpy as np
import numpy.typing as npt
from fastapi import HTTPException, status

from app.inference_scheduler import InferenceScheduler
from app.metrics import observe
from app.observability import (
    current_request_id,
    current_scheduling_scope,
    logger,
    now,
    trace_span,
    wall_time,
)
from app.portrait_response import exception_log_summary
from app.runtime_sessions import primary_execution_provider, run_session
from app.runtime_state import decrement_gpu_queue_waiters, gpu_semaphore_for_device, increment_gpu_queue_waiters
from app.schemas import ModelBundle
from app.settings import MAX_TENSOR_ITEMS

Array = npt.NDArray[Any]
SHADOW_ROUTES: dict[str, dict[str, Any]] = {}
SHADOW_RESULTS: deque[dict[str, Any]] = deque(maxlen=1000)
_SHADOW_TASKS: set[asyncio.Task[None]] = set()


def configure_shadow_bundle(
    active_model_id: str,
    candidate_bundle: ModelBundle,
    *,
    percentage: int = 100,
) -> None:
    SHADOW_ROUTES[active_model_id] = {
        "candidate": candidate_bundle,
        "percentage": max(1, min(100, int(percentage))),
    }


def clear_shadow_bundle(active_model_id: str) -> None:
    SHADOW_ROUTES.pop(active_model_id, None)


def shadow_results_snapshot(limit: int = 100) -> list[dict[str, Any]]:
    return list(SHADOW_RESULTS)[-max(1, min(1000, int(limit))) :][::-1]


def should_run_shadow(active_model_id: str, percentage: int) -> bool:
    if percentage >= 100:
        return True
    traffic_key = current_request_id() or current_scheduling_scope() or str(wall_time())
    bucket = int.from_bytes(
        hashlib.sha256(f"{active_model_id}:{traffic_key}".encode()).digest()[:4], "big"
    ) % 100
    return bucket < percentage


def schedule_shadow_inference(
    bundle: ModelBundle,
    input_array: Array,
    official_outputs: list[Array],
) -> None:
    active_model_id = str(bundle.get("key") or "")
    route = SHADOW_ROUTES.get(active_model_id)
    if route is None or not should_run_shadow(active_model_id, int(route["percentage"])):
        return
    candidate = route["candidate"]
    if not isinstance(candidate, dict) or candidate is bundle:
        return
    candidate_bundle = cast(ModelBundle, candidate)
    copied_input = np.array(input_array, copy=True)

    async def run_shadow() -> None:
        started = now()
        record: dict[str, Any] = {
            "active_model_id": active_model_id,
            "candidate_model_id": str(candidate_bundle.get("key") or ""),
            "active_fingerprint": bundle.get("model_fingerprint"),
            "candidate_fingerprint": candidate_bundle.get("model_fingerprint"),
            "recorded_at": wall_time(),
        }
        try:
            candidate_outputs, _, _ = await _run_model_bundle_direct(candidate_bundle, copied_input)
            shape_match = [list(output.shape) for output in official_outputs] == [
                list(output.shape) for output in candidate_outputs
            ]
            diffs = []
            if shape_match:
                for official, shadow in zip(official_outputs, candidate_outputs, strict=True):
                    if np.issubdtype(official.dtype, np.number) and np.issubdtype(shadow.dtype, np.number):
                        diffs.append(float(np.mean(np.abs(official.astype(np.float64) - shadow.astype(np.float64)))))
            record.update(
                {
                    "status": "completed",
                    "shape_match": shape_match,
                    "mean_absolute_difference": max(diffs) if diffs else None,
                    "latency_seconds": now() - started,
                }
            )
            observe("shadow_inference_total")
            if not shape_match:
                observe("shadow_output_mismatch_total")
        except Exception as exc:
            record.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "latency_seconds": now() - started,
                }
            )
            observe("shadow_inference_errors_total")
            logger.warning("shadow inference failed: %s", exception_log_summary(exc))
        SHADOW_RESULTS.append(record)

    task = asyncio.create_task(run_shadow(), name="portrait-shadow-inference")
    _SHADOW_TASKS.add(task)
    task.add_done_callback(_SHADOW_TASKS.discard)


def build_input_array(tensor_data: list[Any], dtype: Any) -> Array:
    input_array = np.asarray(tensor_data, dtype=dtype)
    if input_array.size > MAX_TENSOR_ITEMS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"张量过大：{input_array.size} 项，最大 {MAX_TENSOR_ITEMS}",
        )
    return input_array


def bundle_execution_provider(bundle: ModelBundle) -> str:
    configured = bundle.get("execution_provider")
    if isinstance(configured, str) and configured:
        return configured
    get_providers = getattr(bundle["session"], "get_providers", None)
    if callable(get_providers):
        providers = get_providers()
        if isinstance(providers, list):
            return primary_execution_provider([str(p) for p in providers])
    return "CUDAExecutionProvider"


async def acquire_with_timeout(
    semaphore: asyncio.Semaphore,
    timeout_seconds: float,
    detail: str,
    *,
    gpu_device_id: int | None = None,
    track_gpu_waiters: bool = False,
) -> None:
    if track_gpu_waiters:
        increment_gpu_queue_waiters(gpu_device_id)
    try:
        try:
            if timeout_seconds > 0:
                await asyncio.wait_for(semaphore.acquire(), timeout=timeout_seconds)
            else:
                await semaphore.acquire()
        except TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            ) from exc
    finally:
        if track_gpu_waiters:
            decrement_gpu_queue_waiters(gpu_device_id)


async def _run_model_bundle_direct(bundle: ModelBundle, input_array: Array) -> tuple[list[Array], float, float]:
    session = bundle["session"]
    model_semaphore = bundle.get("semaphore")
    gpu_device_id = bundle.get("gpu_device_id")
    execution_provider = bundle_execution_provider(bundle)
    gpu_semaphore = gpu_semaphore_for_device(gpu_device_id) if execution_provider in {"CUDAExecutionProvider", "TensorrtExecutionProvider"} else None
    queue_timeout = float(bundle.get("queue_timeout_seconds", 0) or 0)
    queue_start = now()
    model_acquired = False
    gpu_acquired = False
    # 标记该 Bundle 为使用中（in-use），以便在当前推理（该推理正在等待工作线程）
    # 仍运行期间，LRU 淘汰机制不会释放其 ONNX 会话。
    bundle["in_use"] = bundle.get("in_use", 0) + 1
    try:
        if model_semaphore is not None:
            await acquire_with_timeout(
                model_semaphore,
                queue_timeout,
                "model inference queue timeout",
            )
            model_acquired = True
        else:
            await bundle["lock"].acquire()
            model_acquired = True

        if gpu_semaphore is not None:
            elapsed = now() - queue_start
            remaining_timeout = max(0.001, queue_timeout - elapsed) if queue_timeout > 0 else 0
            await acquire_with_timeout(
                gpu_semaphore,
                remaining_timeout,
                "GPU inference queue timeout",
                gpu_device_id=gpu_device_id if isinstance(gpu_device_id, int) else None,
                track_gpu_waiters=True,
            )
            gpu_acquired = True

        queue_seconds = now() - queue_start
        inference_start = now()
        with trace_span(
            "portrait.inference.run_session",
            model=str(bundle.get("key", "")),
            gpu_device_id=gpu_device_id,
            execution_provider=execution_provider,
            batch_size=input_array.shape[0] if input_array.ndim > 0 else 1,
        ):
            raw_outputs = await asyncio.to_thread(run_session, session, input_array)
        inference_seconds = now() - inference_start
    finally:
        if gpu_acquired and gpu_semaphore is not None:
            gpu_semaphore.release()
        if model_acquired:
            if model_semaphore is not None:
                model_semaphore.release()
            else:
                bundle["lock"].release()
        bundle["in_use"] = max(0, bundle.get("in_use", 1) - 1)
        # 在实际推理时刷新最后使用时间，以免仅通过单模型端点
        # 访问的热点模型被作为“最久未使用（LRU）”而淘汰。
        bundle["last_used_at"] = wall_time()

    batch_size = input_array.shape[0] if input_array.ndim > 0 else 1
    bundle["inference_count"] += max(1, batch_size)
    observe("queue_seconds_sum", queue_seconds)
    observe("inference_seconds_sum", inference_seconds)
    return raw_outputs, queue_seconds, inference_seconds


async def run_model_bundle(
    bundle: ModelBundle,
    input_array: Array,
    *,
    priority: str = "sync",
    scheduling_scope: str | None = None,
    scheduling_weight: int = 1,
    timeout_seconds: float | None = None,
) -> tuple[list[Array], float, float]:
    if not bundle.get("dynamic_batching_enabled", False):
        result = await _run_model_bundle_direct(bundle, input_array)
        schedule_shadow_inference(bundle, input_array, result[0])
        return result

    scheduler = bundle.get("dynamic_batch_scheduler")
    if not isinstance(scheduler, InferenceScheduler) or not scheduler.compatible_with_current_loop():
        scheduler = InferenceScheduler(bundle, _run_model_bundle_direct)
        bundle["dynamic_batch_scheduler"] = scheduler
    queue_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(bundle.get("queue_timeout_seconds", 0.0) or 0.0)
    )
    result = await scheduler.submit(
        input_array,
        scope=scheduling_scope or current_scheduling_scope() or "anonymous",
        priority=priority,
        weight=scheduling_weight,
        timeout_seconds=max(0.0, queue_timeout),
    )
    schedule_shadow_inference(bundle, input_array, result[0])
    return result


async def run_model_bundle_batch(
    bundle: ModelBundle,
    inputs: list[Array],
) -> tuple[list[Array], float, float, str]:
    if not inputs:
        return [], 0.0, 0.0, "empty"
    if len(inputs) == 1:
        raw_outputs, queue_seconds, inference_seconds = await run_model_bundle(bundle, inputs[0])
        return raw_outputs, queue_seconds, inference_seconds, "single"
    shapes = {tuple(input_array.shape[1:]) for input_array in inputs if input_array.ndim > 0}
    dtypes = {str(input_array.dtype) for input_array in inputs}
    if len(shapes) != 1 or len(dtypes) != 1:
        output_groups: list[list[Array]] = []
        queue_seconds_sum = 0.0
        inference_seconds_sum = 0.0
        for input_array in inputs:
            raw_outputs, queue_seconds, inference_seconds = await run_model_bundle(bundle, input_array)
            output_groups.append(raw_outputs)
            queue_seconds_sum += queue_seconds
            inference_seconds_sum += inference_seconds
        return stack_outputs(output_groups), queue_seconds_sum, inference_seconds_sum, "per_item_mixed_shape"
    try:
        batched_input = np.concatenate(inputs, axis=0)
        raw_outputs, queue_seconds, inference_seconds = await run_model_bundle(bundle, batched_input)
        return raw_outputs, queue_seconds, inference_seconds, "batch"
    except Exception as exc:
        logger.warning("批量推理失败，回退到逐项推理: %s", exception_log_summary(exc))
    output_groups = []
    queue_seconds_sum = 0.0
    inference_seconds_sum = 0.0
    for input_array in inputs:
        raw_outputs, queue_seconds, inference_seconds = await run_model_bundle(bundle, input_array)
        output_groups.append(raw_outputs)
        queue_seconds_sum += queue_seconds
        inference_seconds_sum += inference_seconds
    return stack_outputs(output_groups), queue_seconds_sum, inference_seconds_sum, "per_item"


def stack_outputs(output_groups: list[list[Array]]) -> list[Array]:
    if not output_groups:
        return []

    output_count = len(output_groups[0])
    stacked: list[Array] = []
    for output_index in range(output_count):
        stacked.append(np.concatenate([group[output_index] for group in output_groups], axis=0))
    return stacked
async def run_yolo_frames(
    bundle: ModelBundle,
    input_array: Array,
) -> tuple[list[Array], float, float, str]:
    if input_array.shape[0] == 1:
        raw_outputs, queue_seconds, inference_seconds = await run_model_bundle(bundle, input_array)
        return raw_outputs, queue_seconds, inference_seconds, "single"

    try:
        raw_outputs, queue_seconds, inference_seconds = await run_model_bundle(bundle, input_array)
        return raw_outputs, queue_seconds, inference_seconds, "batch"
    except Exception as exc:
        logger.warning("批量推理失败，回退到逐帧推理: %s", exception_log_summary(exc))

    output_groups: list[list[Array]] = []
    queue_seconds_sum = 0.0
    inference_seconds_sum = 0.0
    for index in range(input_array.shape[0]):
        raw_outputs, queue_seconds, inference_seconds = await run_model_bundle(bundle, input_array[index : index + 1])
        output_groups.append(raw_outputs)
        queue_seconds_sum += queue_seconds
        inference_seconds_sum += inference_seconds
    return stack_outputs(output_groups), queue_seconds_sum, inference_seconds_sum, "per_frame"


__all__ = [
    "_run_model_bundle_direct",
    "acquire_with_timeout",
    "build_input_array",
    "bundle_execution_provider",
    "clear_shadow_bundle",
    "configure_shadow_bundle",
    "run_model_bundle",
    "run_model_bundle_batch",
    "run_yolo_frames",
    "schedule_shadow_inference",
    "shadow_results_snapshot",
    "stack_outputs",
]
