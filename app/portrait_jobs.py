import asyncio
import base64
import hashlib
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from typing import Any
from uuid import uuid4

from PIL import Image

from app.inference_tracks import infer_detections_and_embeddings
from app.media.quality import assess_image_quality
from app.model_refs import validate_model_reference_parts
from app.observability import logger, wall_time
from app.portrait_async import run_blocking_io
from app.portrait_response import exception_log_summary
from app.portrait_state import (
    handle_state_read_error,
    read_json_state,
    write_json_state,
)
from app.routes_inference_common import validate_detection_parameters
from app.settings import (
    DEFAULT_CONFIDENCE,
    DEFAULT_DETECTOR_ARTIFACT,
    DEFAULT_DETECTOR_PROJECT,
    DEFAULT_IOU,
    DEFAULT_REID_ARTIFACT,
    PORTRAIT_JOBS_STATE_PATH,
    PORTRAIT_STORAGE_BACKEND,
    VIDEO_JOB_MAX_RETRIES,
    VIDEO_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS,
    VIDEO_JOB_RETRY_BACKOFF_SECONDS,
    VIDEO_JOB_WORKER_IN_PROCESS,
)
from app.video_io import (
    aiter_video_frame_batches,
    public_video_metadata,
    resolve_video_job_input,
)

VIDEO_JOB_ERROR_MESSAGE = "视频任务失败"


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
    preview.thumbnail((max_side, max_side))
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


def lightweight_video_job_result(
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = public_video_job_result(result)
    if payload is None:
        return None
    if isinstance(payload.get("frames"), list):
        payload["frames"] = []
    return payload


def video_job_progress_result(
    metadata: dict[str, Any],
    frame_count: int,
    analysis_mode: str,
    *,
    frames: list[dict[str, Any]] | None = None,
    total_frame_count: int | None = None,
) -> dict[str, Any]:
    visible_frames = deepcopy(frames) if isinstance(frames, list) else []
    return {
        "metadata": public_video_metadata(metadata),
        "frame_count": frame_count,
        "frames_available": frame_count,
        "total_frame_count": total_frame_count,
        "frames": visible_frames,
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
            "max_retries": normalize_retry_count(
                self.max_retries, VIDEO_JOB_MAX_RETRIES
            ),
            "next_retry_at": self.next_retry_at,
        }
        if include_result:
            payload["result"] = public_video_job_result(self.result)
        return payload

    def state_dict(self, *, lightweight_result: bool = False) -> dict[str, Any]:
        result = (
            lightweight_video_job_result(self.result)
            if lightweight_result
            else public_video_job_result(self.result)
        )
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
            "max_retries": normalize_retry_count(
                self.max_retries, VIDEO_JOB_MAX_RETRIES
            ),
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
            result=payload.get("result"),
            cancel_requested=bool(payload.get("cancel_requested", False)),
            attempts=normalize_retry_count(payload.get("attempts", 0)),
            max_retries=normalize_retry_count(
                payload.get("max_retries", VIDEO_JOB_MAX_RETRIES), VIDEO_JOB_MAX_RETRIES
            ),
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
                for job in sorted(
                    VIDEO_JOBS.values(), key=lambda item: (item.tenant_id, item.job_id)
                )
            ],
        }


def save_video_jobs_state(*, lightweight_result: bool = False) -> None:
    write_json_state(
        PORTRAIT_JOBS_STATE_PATH,
        video_jobs_state_payload(lightweight_result=lightweight_result),
    )


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

        # 在锁内构建快照，避免序列化时读到其他线程写一半的字段
        with VIDEO_JOBS_LOCK:
            payload = job.state_dict(lightweight_result=lightweight_result)
        upsert_video_job(payload)
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
        handle_state_read_error(
            f"video jobs state 根节点必须是映射: {PORTRAIT_JOBS_STATE_PATH}"
        )
        return
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        handle_state_read_error(
            f"video jobs state jobs 必须是列表: {PORTRAIT_JOBS_STATE_PATH}"
        )
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
        job = VideoJob(
            job_id=f"job_{uuid4().hex[:16]}", tenant_id=tenant_id, filename=filename
        )
        key = job_key(job.tenant_id, job.job_id)
        VIDEO_JOBS[key] = job
        try:
            persist_video_job(job)
        except Exception:
            VIDEO_JOBS.pop(key, None)
            raise
        return job


def create_batch_job(
    job_type: str, tenant_id: str = "default", *, metadata: dict[str, Any] | None = None
) -> VideoJob:
    with VIDEO_JOBS_LOCK:
        job = VideoJob(
            job_id=f"batch_{uuid4().hex[:16]}", tenant_id=tenant_id, filename=None
        )
        job.result = {
            "type": job_type,
            "metadata": metadata or {},
            "mode": "async_batch",
        }
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


def refresh_video_job(job_id: str, tenant_id: str) -> VideoJob | None:
    """从存储后端刷新单个任务到内存态。

    postgres 后端按 (tenant_id, job_id) 单行加载，避免 worker 每条消息全表拉取；
    本地文件后端仍需整文件读取（跨进程可见性），但只在此入口做一次。
    """
    if postgres_jobs_enabled():
        from app.portrait_postgres import load_video_job_record

        record = load_video_job_record(tenant_id, job_id)
        with VIDEO_JOBS_LOCK:
            key = job_key(tenant_id, job_id)
            if record is None:
                VIDEO_JOBS.pop(key, None)
                return None
            try:
                job = VideoJob.from_state(record)
            except Exception as exc:
                logger.warning("已跳过无效视频任务状态: %s", exception_log_summary(exc))
                return None
            VIDEO_JOBS[key] = job
            return job
    load_video_jobs_state()
    return get_video_job(job_id, tenant_id=tenant_id)


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
        job.result = {
            **base_result,
            **(result if isinstance(result, dict) else {"result": result}),
        }
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
    data: bytes | None,
    filename: str | None,
    sample_interval_seconds: float,
    batch_size: int,
    *,
    input_ref: str | None = None,
    detector_project_name: str = DEFAULT_DETECTOR_PROJECT,
    detector_model_name: str = DEFAULT_DETECTOR_ARTIFACT,
    reid_project_name: str = DEFAULT_DETECTOR_PROJECT,
    reid_model_name: str = DEFAULT_REID_ARTIFACT,
    confidence: float = DEFAULT_CONFIDENCE,
    iou: float = DEFAULT_IOU,
    max_detections: int = 100,
    include_embeddings: bool = False,
) -> None:
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        logger.warning(
            "后台执行时视频任务不存在: tenant_hash=%s job_hash=%s",
            video_job_identifier_fingerprint(tenant_id),
            video_job_identifier_fingerprint(job_id),
        )
        return

    async def cancellation_requested() -> bool:
        if job.cancel_requested:
            return True
        from app.portrait_task_queue import TASK_QUEUE

        return await run_blocking_io(
            TASK_QUEUE.is_cancelled, "video_jobs", tenant_id, job_id
        )

    while True:
        # 在锁内做状态转换，避免与 request_cancel_video_job 竞态：
        # 若取消已先行落地（status=CANCELLED），此处不得再覆盖为 RUNNING。
        with VIDEO_JOBS_LOCK:
            if job.cancel_requested or normalize_job_status(job.status) == JobStatus.CANCELLED:
                job.cancel_requested = True
                job.status = JobStatus.CANCELLED
                job.updated_at = wall_time()
                cancelled_on_entry = True
            else:
                job.status = JobStatus.RUNNING
                job.progress = 0.05
                job.error = None
                job.next_retry_at = None
                job.result = None
                job.attempts = normalize_retry_count(job.attempts) + 1
                job.updated_at = wall_time()
                cancelled_on_entry = False
        await run_blocking_io(persist_video_job, job)
        if cancelled_on_entry:
            return
        try:
            if await cancellation_requested():
                job.cancel_requested = True
                job.status = JobStatus.CANCELLED
                job.updated_at = wall_time()
                await run_blocking_io(persist_video_job, job)
                return

            (
                detector_project_name,
                detector_model_name,
                reid_project_name,
                reid_model_name,
            ) = validate_model_reference_parts(
                detector_project_name,
                detector_model_name,
                reid_project_name,
                reid_model_name,
            )
            validate_detection_parameters(
                confidence=confidence, iou=iou, max_detections=max_detections
            )

            # 解析视频源路径
            if input_ref:
                source_path = str(resolve_video_job_input(input_ref))
            elif data is not None:
                # 写入临时文件供 iter_video_frame_batches 使用
                import tempfile
                suffix = f".{(filename or 'video.mp4').rsplit('.', 1)[-1]}" if filename else ".mp4"
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)  # noqa: SIM115  需在 with 外保留落盘路径
                tmp.write(data)
                tmp.flush()
                tmp.close()
                source_path = tmp.name
            else:
                raise ValueError("视频任务缺少输入引用")

            # 按批提特征，收集所有帧结果
            all_frames: list[dict[str, Any]] = []
            all_source_indexes: list[int] = []
            all_source_seconds: list[float] = []
            fps_value = 0.0
            total_frame_count = 0
            total_embedding_count = 0
            frame_index_offset = 0
            batch_count = 0
            detector_key = ""
            reid_key = ""
            detector_load_seconds = 0.0
            reid_load_seconds = 0.0
            detector_timing: dict[str, float] = {}
            reid_timing: dict[str, float] = {}
            persist_interval = float(VIDEO_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS)
            last_progress_persist_at = 0.0

            job.result = video_job_progress_result({}, 0, "person_tracks")
            job.progress = 0.10
            job.updated_at = wall_time()
            await run_blocking_io(persist_video_job, job, lightweight_result=True)

            try:
                async for batch_images, batch_source_indexes, batch_source_seconds, fps, total_fc in aiter_video_frame_batches(
                    source_path, sample_interval_seconds, batch_size
                ):
                    if await cancellation_requested():
                        job.cancel_requested = True
                        job.status = JobStatus.CANCELLED
                        job.updated_at = wall_time()
                        await run_blocking_io(persist_video_job, job)
                        return
                    fps_value = fps
                    total_frame_count = total_fc
                    batch_analysis = await infer_detections_and_embeddings(
                        batch_images,
                        [None] * len(batch_images),
                        detector_project_name,
                        detector_model_name,
                        reid_project_name,
                        reid_model_name,
                        confidence=confidence,
                        iou=iou,
                        max_detections=max_detections,
                        include_embeddings=include_embeddings,
                        frame_index_offset=frame_index_offset,
                        embedding_index_offset=total_embedding_count,
                    )
                    batch_frames = batch_analysis["frames"]
                    total_embedding_count += int(batch_analysis["embedding_count"])
                    detector_key = str(batch_analysis["detector_key"])
                    reid_key = str(batch_analysis["reid_key"])
                    detector_load_seconds += float(batch_analysis["detector_load_seconds"])
                    reid_load_seconds += float(batch_analysis["reid_load_seconds"])
                    for target, source in (
                        (detector_timing, batch_analysis["detector_meta"].get("timing", {})),
                        (reid_timing, batch_analysis["embedding_meta"].get("timing", {})),
                    ):
                        for key, value in source.items():
                            if isinstance(value, (int, float)):
                                target[key] = target.get(key, 0.0) + float(value)
                    for frame, image, src_idx, src_seconds in zip(
                        batch_frames, batch_images, batch_source_indexes, batch_source_seconds, strict=False
                    ):
                        frame["source_frame_index"] = src_idx
                        frame["source_seconds"] = src_seconds
                        frame["thumbnail"] = image_thumbnail_data_url(image)
                        frame["quality"] = assess_image_quality(image)
                        all_frames.append(frame)
                        all_source_indexes.append(src_idx)
                        all_source_seconds.append(src_seconds)
                    frame_index_offset += len(batch_images)
                    batch_count += 1
                    # 进度更新
                    job.result = video_job_progress_result(
                        {
                            "fps": fps_value,
                            "source_frame_indexes": all_source_indexes,
                            "source_seconds": all_source_seconds,
                            "source_frame_count": total_frame_count,
                            "sample_interval_seconds": sample_interval_seconds,
                            "batch_size": batch_size,
                        },
                        len(all_frames),
                        "person_tracks",
                        frames=all_frames,
                        total_frame_count=total_frame_count or None,
                    )
                    if total_frame_count > 0 and batch_source_indexes:
                        decoded_ratio = min(1.0, (batch_source_indexes[-1] + 1) / total_frame_count)
                        job.progress = 0.10 + 0.75 * decoded_ratio
                    else:
                        job.progress = min(0.84, 0.10 + 0.75 * (batch_count / (batch_count + 1)))
                    job.updated_at = wall_time()
                    if persist_interval <= 0 or (job.updated_at - last_progress_persist_at) >= persist_interval:
                        await run_blocking_io(persist_video_job, job, lightweight_result=VIDEO_JOB_WORKER_IN_PROCESS)
                        last_progress_persist_at = job.updated_at
            finally:
                if data is not None:
                    import os as _os
                    try:
                        _os.unlink(source_path)
                    except Exception:
                        pass

            if not all_frames:
                raise ValueError("视频任务未提取到可分析帧")

            # 所有批次完成后统一 associate_person_tracks
            from app.portrait_tracking import associate_person_tracks
            tracking_meta = associate_person_tracks(all_frames, include_template_embeddings=include_embeddings)

            metadata = {
                "fps": fps_value,
                "source_frame_indexes": all_source_indexes,
                "source_seconds": all_source_seconds,
                "source_frame_count": total_frame_count,
                "sample_interval_seconds": sample_interval_seconds,
                "batch_size": batch_size,
            }
            job.result = public_video_job_result(
                {
                    "metadata": metadata,
                    "frames": all_frames,
                    "tracks": tracking_meta["tracks"],
                    "tracker": {k: v for k, v in tracking_meta.items() if k != "tracks"},
                    "frame_count": len(all_frames),
                    "person_count": sum(f.get("person_count", 0) for f in all_frames),
                    "track_count": tracking_meta["track_count"],
                    "embedding_count": total_embedding_count,
                    "analysis_mode": "person_tracks",
                    "models": {"detector": detector_key, "reid": reid_key},
                    "timing": {
                        "detector_load_seconds": detector_load_seconds,
                        "reid_load_seconds": reid_load_seconds,
                        "detector": detector_timing,
                        "reid": reid_timing,
                    },
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
            if await cancellation_requested():
                job.cancel_requested = True
                job.status = JobStatus.CANCELLED
                job.updated_at = wall_time()
                await run_blocking_io(persist_video_job, job)
                return
            if normalize_retry_count(job.attempts) <= normalize_retry_count(
                job.max_retries, VIDEO_JOB_MAX_RETRIES
            ):
                retry_delay = max(
                    0.0, float(VIDEO_JOB_RETRY_BACKOFF_SECONDS)
                ) * normalize_retry_count(job.attempts, 1)
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
