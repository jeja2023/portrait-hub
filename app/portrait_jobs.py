import asyncio
import base64
import hashlib
import inspect
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from typing import Any
from uuid import uuid4

from PIL import Image

from app.media.quality import assess_image_quality
from app.media.video_decode import extract_video_frames_from_bytes
from app.observability import logger, wall_time
from app.portrait_async import run_blocking_io
from app.portrait_model_runtime import infer_appearance_record_for_image
from app.portrait_response import exception_log_summary
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import (
    PORTRAIT_JOBS_STATE_PATH,
    PORTRAIT_STORAGE_BACKEND,
    VIDEO_JOB_MAX_RETRIES,
    VIDEO_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS,
    VIDEO_JOB_RETRY_BACKOFF_SECONDS,
)
from app.video_io import public_video_metadata


VIDEO_JOB_ERROR_MESSAGE = "视频任务失败"


async def appearance_record(image: Any, include_embedding: bool = True) -> dict[str, Any]:
    return await infer_appearance_record_for_image(image, include_embedding=include_embedding)


async def resolve_appearance_record(image: Any, *, include_embedding: bool = True) -> dict[str, Any]:
    record: Any = appearance_record(image, include_embedding=include_embedding)
    if inspect.isawaitable(record):
        record = await record
    return record if isinstance(record, dict) else {}


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


def normalize_job_status(value: str | JobStatus) -> JobStatus:
    try:
        return value if isinstance(value, JobStatus) else JobStatus(str(value))
    except ValueError:
        return JobStatus.QUEUED


def normalize_retry_count(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def normalize_retry_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def video_job_identifier_fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def public_video_job_error(error: Any) -> str | None:
    return VIDEO_JOB_ERROR_MESSAGE if error else None




def image_thumbnail_data_url(image: Any, max_side: int = 240) -> str | None:
    if not isinstance(image, Image.Image):
        return None
    preview = image.copy()
    if preview.mode not in {"RGB", "L"}:
        preview = preview.convert("RGB")
    preview.thumbnail((max_side, max_side))  # type: ignore[no-untyped-call]
    buffer = BytesIO()
    preview.save(buffer, format="JPEG", quality=78, optimize=True)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"

def public_video_job_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    payload = deepcopy(result)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        payload["metadata"] = public_video_metadata(metadata)
    return payload


def lightweight_video_job_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    payload = public_video_job_result(result)
    if payload is None:
        return None
    if isinstance(payload.get("frames"), list):
        payload["frames"] = []
    return payload


def video_job_progress_result(metadata: dict[str, Any], frame_count: int, analysis_mode: str) -> dict[str, Any]:
    return {
        "metadata": public_video_metadata(metadata),
        "frame_count": frame_count,
        "frames_available": frame_count,
        "frames": [],
        "analysis_mode": analysis_mode,
        "partial": True,
    }


@dataclass
class VideoJob:
    job_id: str
    tenant_id: str
    filename: str | None
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    created_at: float = field(default_factory=wall_time)
    updated_at: float = field(default_factory=wall_time)
    error: str | None = None
    result: dict[str, Any] | None = None
    cancel_requested: bool = False
    attempts: int = 0
    max_retries: int = VIDEO_JOB_MAX_RETRIES
    next_retry_at: float | None = None

    def public_dict(self, include_result: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "status": str(normalize_job_status(self.status)),
            "progress": round(float(self.progress), 6),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": public_video_job_error(self.error),
            "cancel_requested": self.cancel_requested,
            "attempts": normalize_retry_count(self.attempts),
            "max_retries": normalize_retry_count(self.max_retries, VIDEO_JOB_MAX_RETRIES),
            "next_retry_at": self.next_retry_at,
        }
        if include_result:
            payload["result"] = public_video_job_result(self.result)
        return payload

    def state_dict(self, *, lightweight_result: bool = False) -> dict[str, Any]:
        result = lightweight_video_job_result(self.result) if lightweight_result else public_video_job_result(self.result)
        return {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "status": str(normalize_job_status(self.status)),
            "progress": round(float(self.progress), 6),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": public_video_job_error(self.error),
            "cancel_requested": self.cancel_requested,
            "attempts": normalize_retry_count(self.attempts),
            "max_retries": normalize_retry_count(self.max_retries, VIDEO_JOB_MAX_RETRIES),
            "next_retry_at": self.next_retry_at,
            "result": result,
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> "VideoJob":
        return cls(
            job_id=str(payload["job_id"]),
            tenant_id=str(payload.get("tenant_id", "default")),
            filename=payload.get("filename"),
            status=normalize_job_status(str(payload.get("status", JobStatus.QUEUED))),
            progress=float(payload.get("progress", 0.0)),
            created_at=float(payload.get("created_at", wall_time())),
            updated_at=float(payload.get("updated_at", wall_time())),
            error=public_video_job_error(payload.get("error")),
            result=payload.get("result") if isinstance(payload.get("result"), dict) else payload.get("result"),
            cancel_requested=bool(payload.get("cancel_requested", False)),
            attempts=normalize_retry_count(payload.get("attempts", 0)),
            max_retries=normalize_retry_count(payload.get("max_retries", VIDEO_JOB_MAX_RETRIES), VIDEO_JOB_MAX_RETRIES),
            next_retry_at=normalize_retry_timestamp(payload.get("next_retry_at")),
        )


JobKey = tuple[str, str]


VIDEO_JOBS: dict[JobKey, VideoJob] = {}
VIDEO_JOBS_LOCK = threading.RLock()


def job_key(tenant_id: str, job_id: str) -> JobKey:
    return (str(tenant_id), str(job_id))


def postgres_jobs_enabled() -> bool:
    return PORTRAIT_STORAGE_BACKEND == "postgres"


def video_jobs_state_payload(*, lightweight_result: bool = False) -> dict[str, Any]:
    with VIDEO_JOBS_LOCK:
        return {
            "version": 1,
            "jobs": [
                job.state_dict(lightweight_result=lightweight_result)
                for job in sorted(VIDEO_JOBS.values(), key=lambda item: (item.tenant_id, item.job_id))
            ],
        }


def save_video_jobs_state(*, lightweight_result: bool = False) -> None:
    write_json_state(PORTRAIT_JOBS_STATE_PATH, video_jobs_state_payload(lightweight_result=lightweight_result))


def restore_video_job(job: VideoJob, previous: VideoJob) -> None:
    job.job_id = previous.job_id
    job.tenant_id = previous.tenant_id
    job.filename = previous.filename
    job.status = previous.status
    job.progress = previous.progress
    job.created_at = previous.created_at
    job.updated_at = previous.updated_at
    job.error = previous.error
    job.result = deepcopy(previous.result)
    job.cancel_requested = previous.cancel_requested
    job.attempts = previous.attempts
    job.max_retries = previous.max_retries
    job.next_retry_at = previous.next_retry_at


def persist_video_job(job: VideoJob, *, lightweight_result: bool = False) -> None:
    if postgres_jobs_enabled():
        from app.portrait_postgres import upsert_video_job

        upsert_video_job(job.state_dict(lightweight_result=lightweight_result))
        return
    save_video_jobs_state(lightweight_result=lightweight_result)


def delete_video_job(tenant_id: str, job_id: str) -> None:
    if postgres_jobs_enabled():
        from app.portrait_postgres import delete_video_job as delete_postgres_video_job

        delete_postgres_video_job(tenant_id, job_id)
        return
    save_video_jobs_state()


def load_video_jobs_state() -> None:
    if postgres_jobs_enabled():
        from app.portrait_postgres import load_video_jobs_snapshot

        payload = {"jobs": load_video_jobs_snapshot()}
    else:
        payload = read_json_state(PORTRAIT_JOBS_STATE_PATH, {"jobs": []})
    if not isinstance(payload, dict):
        handle_state_read_error(f"video jobs state 根节点必须是映射: {PORTRAIT_JOBS_STATE_PATH}")
        return
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        handle_state_read_error(f"video jobs state jobs 必须是列表: {PORTRAIT_JOBS_STATE_PATH}")
        return
    with VIDEO_JOBS_LOCK:
        VIDEO_JOBS.clear()
        for item in jobs:
            if not isinstance(item, dict) or "job_id" not in item:
                continue
            try:
                job = VideoJob.from_state(item)
            except Exception as exc:
                logger.warning("已跳过无效视频任务状态: %s", exception_log_summary(exc))
                continue
            VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job


def create_video_job(filename: str | None, tenant_id: str = "default") -> VideoJob:
    with VIDEO_JOBS_LOCK:
        job = VideoJob(job_id=f"job_{uuid4().hex[:16]}", tenant_id=tenant_id, filename=filename)
        key = job_key(job.tenant_id, job.job_id)
        VIDEO_JOBS[key] = job
        try:
            persist_video_job(job)
        except Exception:
            VIDEO_JOBS.pop(key, None)
            raise
        return job


def create_batch_job(job_type: str, tenant_id: str = "default", *, metadata: dict[str, Any] | None = None) -> VideoJob:
    with VIDEO_JOBS_LOCK:
        job = VideoJob(job_id=f"batch_{uuid4().hex[:16]}", tenant_id=tenant_id, filename=None)
        job.result = {"type": job_type, "metadata": metadata or {}, "mode": "async_batch"}
        key = job_key(job.tenant_id, job.job_id)
        VIDEO_JOBS[key] = job
        try:
            persist_video_job(job)
        except Exception:
            VIDEO_JOBS.pop(key, None)
            raise
        return job


def get_video_job(job_id: str, tenant_id: str | None = None) -> VideoJob | None:
    with VIDEO_JOBS_LOCK:
        if tenant_id is not None:
            return VIDEO_JOBS.get(job_key(tenant_id, job_id))
        matches = [job for job in VIDEO_JOBS.values() if job.job_id == job_id]
        return matches[0] if len(matches) == 1 else None


def request_cancel_video_job(job_id: str, tenant_id: str | None = None) -> bool:
    with VIDEO_JOBS_LOCK:
        job = get_video_job(job_id, tenant_id=tenant_id)
        if job is None:
            return False
        if normalize_job_status(job.status) in TERMINAL_JOB_STATUSES:
            return True
        previous_job = deepcopy(job)
        job.cancel_requested = True
        job.status = JobStatus.CANCELLED
        job.updated_at = wall_time()
        try:
            persist_video_job(job)
        except Exception:
            restore_video_job(job, previous_job)
            VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
            raise
        return True


def remove_video_job(job_id: str, tenant_id: str) -> bool:
    with VIDEO_JOBS_LOCK:
        key = job_key(tenant_id, job_id)
        job = VIDEO_JOBS.pop(key, None)
        if job is None:
            return False
        try:
            delete_video_job(tenant_id, job_id)
        except Exception:
            VIDEO_JOBS[key] = job
            raise
        return True


async def run_batch_job(job_id: str, tenant_id: str, handler: Any) -> None:
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        logger.warning(
            "后台执行时批量任务不存在: tenant_hash=%s job_hash=%s",
            video_job_identifier_fingerprint(tenant_id),
            video_job_identifier_fingerprint(job_id),
        )
        return

    base_result = deepcopy(job.result) if isinstance(job.result, dict) else {}
    job.status = JobStatus.RUNNING
    job.progress = 0.05
    job.error = None
    job.updated_at = wall_time()
    await run_blocking_io(persist_video_job, job)
    try:
        if job.cancel_requested:
            job.status = JobStatus.CANCELLED
            job.updated_at = wall_time()
            await run_blocking_io(persist_video_job, job)
            return
        result = await handler(job)
        job.result = {**base_result, **(result if isinstance(result, dict) else {"result": result})}
        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.updated_at = wall_time()
        await run_blocking_io(persist_video_job, job)
    except Exception as exc:
        logger.warning(
            "batch job failed: tenant_hash=%s job_hash=%s error=%s",
            video_job_identifier_fingerprint(tenant_id),
            video_job_identifier_fingerprint(job_id),
            exception_log_summary(exc),
        )
        job.status = JobStatus.CANCELLED if job.cancel_requested else JobStatus.FAILED
        job.error = None if job.cancel_requested else VIDEO_JOB_ERROR_MESSAGE
        job.updated_at = wall_time()
        await run_blocking_io(persist_video_job, job)


async def run_video_job(
    job_id: str,
    tenant_id: str,
    data: bytes,
    filename: str | None,
    frame_interval: int,
    max_frames: int,
) -> None:
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        logger.warning(
            "后台执行时视频任务不存在: tenant_hash=%s job_hash=%s",
            video_job_identifier_fingerprint(tenant_id),
            video_job_identifier_fingerprint(job_id),
        )
        return

    while True:
        job.status = JobStatus.RUNNING
        job.progress = 0.05
        job.error = None
        job.next_retry_at = None
        job.result = None
        job.attempts = normalize_retry_count(job.attempts) + 1
        job.updated_at = wall_time()
        await run_blocking_io(persist_video_job, job)
        try:
            if job.cancel_requested:
                job.status = JobStatus.CANCELLED
                job.updated_at = wall_time()
                await run_blocking_io(persist_video_job, job)
                return

            frames, metadata = await extract_video_frames_from_bytes(data, filename, frame_interval, max_frames)
            frame_results: list[dict[str, Any]] = []
            total = max(1, len(frames))
            # 对中间进度写入做节流：每帧都持久化会每次重写整个任务状态文件（或整行 JSONB），
            # 开销随 任务数 × 帧数 增长。下方的终态仍会立即落盘，完成后的结果也在循环结束后持久化。
            persist_interval = float(VIDEO_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS)
            last_progress_persist_at = wall_time()
            for index, image in enumerate(frames):
                if job.cancel_requested:
                    job.status = JobStatus.CANCELLED
                    job.updated_at = wall_time()
                    await run_blocking_io(persist_video_job, job)
                    return
                quality = assess_image_quality(image)
                appearance = await resolve_appearance_record(image, include_embedding=False)
                frame_results.append(
                    {
                        "frame_index": index,
                        "source_frame_index": metadata.get("source_frame_indexes", [index])[index]
                        if index < len(metadata.get("source_frame_indexes", []))
                        else index,
                        "width": image.width,
                        "height": image.height,
                        "thumbnail": image_thumbnail_data_url(image),
                        "quality": quality,
                        "appearance": appearance,
                        "embedding_dim": 64,
                    }
                )
                job.result = video_job_progress_result(metadata, len(frame_results), "async_media_fallback")
                job.progress = 0.10 + 0.85 * ((index + 1) / total)
                job.updated_at = wall_time()
                if persist_interval <= 0 or (job.updated_at - last_progress_persist_at) >= persist_interval:
                    await run_blocking_io(persist_video_job, job, lightweight_result=True)
                    last_progress_persist_at = job.updated_at

            job.result = public_video_job_result(
                {
                    "metadata": metadata,
                    "frames": frame_results,
                    "frame_count": len(frame_results),
                    "analysis_mode": "async_media_fallback",
                }
            )
            job.status = JobStatus.COMPLETED
            job.progress = 1.0
            job.updated_at = wall_time()
            await run_blocking_io(persist_video_job, job)
            return
        except Exception as exc:
            logger.warning(
                "视频任务失败: tenant_hash=%s job_hash=%s attempt=%s error=%s",
                video_job_identifier_fingerprint(tenant_id),
                video_job_identifier_fingerprint(job_id),
                job.attempts,
                exception_log_summary(exc),
            )
            if job.cancel_requested:
                job.status = JobStatus.CANCELLED
                job.updated_at = wall_time()
                await run_blocking_io(persist_video_job, job)
                return
            if normalize_retry_count(job.attempts) <= normalize_retry_count(job.max_retries, VIDEO_JOB_MAX_RETRIES):
                retry_delay = max(0.0, float(VIDEO_JOB_RETRY_BACKOFF_SECONDS)) * normalize_retry_count(job.attempts, 1)
                job.status = JobStatus.QUEUED
                job.error = VIDEO_JOB_ERROR_MESSAGE
                job.next_retry_at = wall_time() + retry_delay
                job.updated_at = wall_time()
                await run_blocking_io(persist_video_job, job)
                if retry_delay > 0:
                    await asyncio.sleep(retry_delay)
                continue
            job.status = JobStatus.FAILED
            job.error = VIDEO_JOB_ERROR_MESSAGE
            job.next_retry_at = None
            job.updated_at = wall_time()
            await run_blocking_io(persist_video_job, job)
            return
