from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import socket
from typing import Any
from uuid import uuid4

from app.model_refs import validate_model_reference_parts
from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_jobs import (
    TERMINAL_JOB_STATUSES,
    get_video_job,
    normalize_job_status,
    refresh_video_job,
    run_video_job,
    video_job_identifier_fingerprint,
)
from app.portrait_response import exception_log_summary
from app.portrait_task_queue import TASK_QUEUE, QueueMessage
from app.routes_inference_common import validate_detection_parameters
from app.settings import (
    DEFAULT_CONFIDENCE,
    DEFAULT_DETECTOR_ARTIFACT,
    DEFAULT_DETECTOR_PROJECT,
    DEFAULT_IOU,
    DEFAULT_REID_ARTIFACT,
    INFERENCE_BATCH_SIZE_LIMIT,
    TASK_QUEUE_POLL_INTERVAL_SECONDS,
    TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS,
    VIDEO_JOB_WORKER_IN_PROCESS,
)
from app.video_io import delete_video_job_input

VIDEO_JOB_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
_TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_JOB_PATTERN = re.compile(r"^job_[a-f0-9]{16}$")


def _message_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"视频任务消息缺少 {key}")
    return value.strip()


def validate_video_job_message(message: QueueMessage) -> dict[str, Any]:
    tenant_id = _message_text(message.payload, "tenant_id")
    job_id = _message_text(message.payload, "job_id")
    input_ref = _message_text(message.payload, "input_ref")
    if not _TENANT_PATTERN.fullmatch(tenant_id):
        raise ValueError("视频任务消息租户无效")
    if not _JOB_PATTERN.fullmatch(job_id):
        raise ValueError("视频任务消息 ID 无效")
    sample_interval_seconds = float(message.payload.get("sample_interval_seconds") or 0.0)
    batch_size = int(message.payload.get("batch_size") or 0)
    if not math.isfinite(sample_interval_seconds) or sample_interval_seconds <= 0:
        raise ValueError("视频任务采样间隔无效")
    if batch_size < 1 or batch_size > INFERENCE_BATCH_SIZE_LIMIT:
        raise ValueError("视频任务批次大小无效")
    detector_project_name = str(
        message.payload.get("detector_project_name") or DEFAULT_DETECTOR_PROJECT
    )
    detector_model_name = str(
        message.payload.get("detector_model_name") or DEFAULT_DETECTOR_ARTIFACT
    )
    reid_project_name = str(
        message.payload.get("reid_project_name") or DEFAULT_DETECTOR_PROJECT
    )
    reid_model_name = str(
        message.payload.get("reid_model_name") or DEFAULT_REID_ARTIFACT
    )
    detector_project_name, detector_model_name, reid_project_name, reid_model_name = (
        validate_model_reference_parts(
            detector_project_name,
            detector_model_name,
            reid_project_name,
            reid_model_name,
        )
    )
    confidence = float(message.payload.get("confidence", DEFAULT_CONFIDENCE))
    iou = float(message.payload.get("iou", DEFAULT_IOU))
    max_detections = int(message.payload.get("max_detections", 100))
    raw_include_embeddings = message.payload.get("include_embeddings", False)
    if isinstance(raw_include_embeddings, bool):
        include_embeddings = raw_include_embeddings
    elif isinstance(
        raw_include_embeddings, str
    ) and raw_include_embeddings.strip().lower() in {"true", "false"}:
        include_embeddings = raw_include_embeddings.strip().lower() == "true"
    else:
        raise ValueError("视频任务 include_embeddings 无效")
    validate_detection_parameters(
        confidence=confidence, iou=iou, max_detections=max_detections
    )
    return {
        "tenant_id": tenant_id,
        "job_id": job_id,
        "input_ref": input_ref,
        "sample_interval_seconds": sample_interval_seconds,
        "batch_size": batch_size,
        "detector_project_name": detector_project_name,
        "detector_model_name": detector_model_name,
        "reid_project_name": reid_project_name,
        "reid_model_name": reid_model_name,
        "confidence": confidence,
        "iou": iou,
        "max_detections": max_detections,
        "include_embeddings": include_embeddings,
    }


async def process_video_job_message(message: QueueMessage) -> dict[str, Any]:
    task = validate_video_job_message(message)
    # 按 (tenant_id, job_id) 单条刷新，避免每条消息全量重载任务表
    job = await run_blocking_io(refresh_video_job, task["job_id"], task["tenant_id"])
    if job is None:
        await run_blocking_io(delete_video_job_input, task["input_ref"])
        await run_blocking_io(
            TASK_QUEUE.clear_cancelled, "video_jobs", task["tenant_id"], task["job_id"]
        )
        await run_blocking_io(TASK_QUEUE.ack, message)
        return {
            "status": "orphan_removed",
            "job_id": task["job_id"],
            "tenant_id": task["tenant_id"],
        }
    if (
        job.cancel_requested
        or normalize_job_status(job.status) in TERMINAL_JOB_STATUSES
    ):
        await run_blocking_io(delete_video_job_input, task["input_ref"])
        await run_blocking_io(
            TASK_QUEUE.clear_cancelled, "video_jobs", job.tenant_id, job.job_id
        )
        await run_blocking_io(TASK_QUEUE.ack, message)
        return {
            "status": str(normalize_job_status(job.status)),
            "job_id": job.job_id,
            "tenant_id": job.tenant_id,
        }

    await run_video_job(
        job.job_id,
        job.tenant_id,
        None,
        None,
        task["sample_interval_seconds"],
        task["batch_size"],
        input_ref=task["input_ref"],
        detector_project_name=task["detector_project_name"],
        detector_model_name=task["detector_model_name"],
        reid_project_name=task["reid_project_name"],
        reid_model_name=task["reid_model_name"],
        confidence=task["confidence"],
        iou=task["iou"],
        max_detections=task["max_detections"],
        include_embeddings=task["include_embeddings"],
    )
    final_job = get_video_job(job.job_id, tenant_id=job.tenant_id)
    if (
        final_job is None
        or normalize_job_status(final_job.status) not in TERMINAL_JOB_STATUSES
    ):
        raise RuntimeError("视频任务执行后未进入终态")
    await run_blocking_io(delete_video_job_input, task["input_ref"])
    await run_blocking_io(
        TASK_QUEUE.clear_cancelled, "video_jobs", final_job.tenant_id, final_job.job_id
    )
    await run_blocking_io(TASK_QUEUE.ack, message)
    return {
        "status": str(normalize_job_status(final_job.status)),
        "job_id": final_job.job_id,
        "tenant_id": final_job.tenant_id,
    }


async def maintain_message_lease(message: QueueMessage) -> None:
    interval = max(1.0, min(30.0, float(TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS) / 3.0))
    while True:
        await asyncio.sleep(interval)
        try:
            await run_blocking_io(TASK_QUEUE.heartbeat, message, VIDEO_JOB_WORKER_ID)
        except Exception as exc:
            logger.warning("视频任务租约续期失败: error=%s", exception_log_summary(exc))


async def stop_message_lease(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def run_worker_once(*, block_seconds: float = 0.0) -> dict[str, Any]:
    message = await run_blocking_io(
        TASK_QUEUE.claim, "video_jobs", VIDEO_JOB_WORKER_ID, block_seconds
    )
    if message is None:
        return {"status": "idle", "processed_count": 0}
    try:
        validate_video_job_message(message)
    except (TypeError, ValueError) as exc:
        await run_blocking_io(TASK_QUEUE.ack, message)
        logger.warning("已丢弃无效视频任务消息: error=%s", exception_log_summary(exc))
        return {
            "status": "discarded",
            "processed_count": 1,
            "error": type(exc).__name__,
        }

    lease_task = asyncio.create_task(maintain_message_lease(message))
    try:
        result = await process_video_job_message(message)
        return {"status": "processed", "processed_count": 1, "result": result}
    except Exception as exc:
        try:
            await run_blocking_io(TASK_QUEUE.release, message)
        except Exception as release_exc:
            logger.warning(
                "视频任务释放失败: error=%s", exception_log_summary(release_exc)
            )
        logger.warning(
            "视频任务 worker 执行失败: tenant_hash=%s job_hash=%s error=%s",
            video_job_identifier_fingerprint(
                str(message.payload.get("tenant_id") or "")
            ),
            video_job_identifier_fingerprint(str(message.payload.get("job_id") or "")),
            exception_log_summary(exc),
        )
        return {"status": "error", "processed_count": 0, "error": type(exc).__name__}
    finally:
        await stop_message_lease(lease_task)


async def run_worker_forever(
    *, poll_interval_seconds: float = TASK_QUEUE_POLL_INTERVAL_SECONDS
) -> None:
    block_seconds = max(0.1, float(poll_interval_seconds))
    while True:
        await run_worker_once(block_seconds=block_seconds)
        await asyncio.sleep(0)


def start_in_process_worker() -> asyncio.Task[None] | None:
    if not VIDEO_JOB_WORKER_IN_PROCESS:
        return None
    return asyncio.create_task(run_worker_forever())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="运行 PortraitHub 持久视频任务 worker。"
    )
    parser.add_argument("--once", action="store_true", help="最多处理一条任务后退出。")
    parser.add_argument(
        "--poll-interval", type=float, default=TASK_QUEUE_POLL_INTERVAL_SECONDS
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读结果。")
    args = parser.parse_args()
    if args.once:
        report = asyncio.run(run_worker_once(block_seconds=0.0))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"视频任务 worker：{report['status']}")
        return 0 if report["status"] != "error" else 1
    asyncio.run(run_worker_forever(poll_interval_seconds=args.poll_interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
