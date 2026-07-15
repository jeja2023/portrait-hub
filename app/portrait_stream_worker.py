import asyncio
import hashlib
import math
from copy import deepcopy
from typing import Any

from fastapi import HTTPException, status
from PIL import Image

from app.inference_tracks import infer_tracks_for_images
from app.media.quality import assess_image_quality
from app.media.stream_decode import validate_media_stream_url
from app.metrics import observe_video_sampling_metrics
from app.model_refs import validate_model_reference_parts
from app.observability import logger, wall_time
from app.portrait_jobs import image_thumbnail_data_url
from app.portrait_request_validation import validate_int_range
from app.portrait_response import exception_log_summary
from app.portrait_security import redact_sensitive_fields
from app.portrait_state import append_jsonl
from app.portrait_streams import (
    StreamRecord,
    StreamStatus,
    persist_stream,
    restore_stream,
    restore_stream_snapshot_in_store,
)
from app.routes_inference_common import validate_detection_parameters
from app.settings import (
    DEFAULT_CONFIDENCE,
    DEFAULT_DETECTOR_ARTIFACT,
    DEFAULT_DETECTOR_PROJECT,
    DEFAULT_IOU,
    DEFAULT_REID_ARTIFACT,
    INFERENCE_BATCH_SIZE_LIMIT,
    MAX_DETECTIONS,
    STREAM_EVENT_STATE_PATH,
    STREAM_INFERENCE_BATCH_SIZE,
    STREAM_READ_TIMEOUT_SECONDS,
    STREAM_SAMPLE_INTERVAL_SECONDS,
)
from app.video_io import aiter_video_frame_batches, public_video_metadata

STREAM_WORKER_SESSIONS: dict[tuple[str, str], dict[str, Any]] = {}
STREAM_FRAME_BUFFER_LIMIT = 1


STREAM_ANALYSIS_SETTING_KEYS = {
    "confidence",
    "detector_model_name",
    "detector_project_name",
    "include_embeddings",
    "iou",
    "max_detections",
    "read_timeout_seconds",
    "reid_model_name",
    "reid_project_name",
    "sample_interval_seconds",
    "batch_size",
}


def _float_setting(settings: dict[str, Any], key: str, default: float) -> float:
    raw = settings.get(key, default)
    if isinstance(raw, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{key} 必须是数字")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{key} 必须是数字") from exc
    if not math.isfinite(value):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{key} 必须是有限数字")
    return value


def _bool_setting(settings: dict[str, Any], key: str, default: bool) -> bool:
    raw = settings.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in {"true", "false"}:
        return raw.strip().lower() == "true"
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{key} 必须是布尔值")


def stream_analysis_parameters(settings: dict[str, Any]) -> dict[str, Any]:
    detector_project_name = str(settings.get("detector_project_name", DEFAULT_DETECTOR_PROJECT))
    detector_model_name = str(settings.get("detector_model_name", DEFAULT_DETECTOR_ARTIFACT))
    reid_project_name = str(settings.get("reid_project_name", DEFAULT_DETECTOR_PROJECT))
    reid_model_name = str(settings.get("reid_model_name", DEFAULT_REID_ARTIFACT))
    detector_project_name, detector_model_name, reid_project_name, reid_model_name = validate_model_reference_parts(
        detector_project_name,
        detector_model_name,
        reid_project_name,
        reid_model_name,
    )
    confidence = _float_setting(settings, "confidence", DEFAULT_CONFIDENCE)
    iou = _float_setting(settings, "iou", DEFAULT_IOU)
    max_detections = validate_int_range(
        "max_detections",
        settings.get("max_detections", 100),
        minimum=1,
        maximum=MAX_DETECTIONS,
    )
    validate_detection_parameters(confidence=confidence, iou=iou, max_detections=max_detections)
    sample_interval_seconds = _float_setting(
        settings, "sample_interval_seconds", STREAM_SAMPLE_INTERVAL_SECONDS
    )
    if sample_interval_seconds <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sample_interval_seconds 必须大于 0",
        )
    return {
        "detector_project_name": detector_project_name,
        "detector_model_name": detector_model_name,
        "reid_project_name": reid_project_name,
        "reid_model_name": reid_model_name,
        "confidence": confidence,
        "iou": iou,
        "max_detections": max_detections,
        "include_embeddings": _bool_setting(settings, "include_embeddings", False),
        "sample_interval_seconds": sample_interval_seconds,
        "batch_size": validate_int_range(
            "batch_size",
            settings.get("batch_size", STREAM_INFERENCE_BATCH_SIZE),
            minimum=1,
            maximum=INFERENCE_BATCH_SIZE_LIMIT,
        ),
        "read_timeout_seconds": validate_int_range(
            "read_timeout_seconds",
            settings.get("read_timeout_seconds", STREAM_READ_TIMEOUT_SECONDS),
            minimum=1,
            maximum=STREAM_READ_TIMEOUT_SECONDS,
        ),
    }


def normalize_stream_analysis_settings(settings: dict[str, Any]) -> dict[str, Any]:
    parameters = stream_analysis_parameters(settings)
    normalized = dict(settings)
    for key in STREAM_ANALYSIS_SETTING_KEYS & settings.keys():
        normalized[key] = parameters[key]
    return normalized


async def analyze_stream_frames(
    stream: StreamRecord,
    images: list[Image.Image],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if not images:
        raise ValueError("视频流未读取到可解析帧")
    parameters = stream_analysis_parameters(stream.settings)
    result = await infer_tracks_for_images(
        images,
        [None] * len(images),
        parameters["detector_project_name"],
        parameters["detector_model_name"],
        parameters["reid_project_name"],
        parameters["reid_model_name"],
        confidence=parameters["confidence"],
        iou=parameters["iou"],
        max_detections=parameters["max_detections"],
        include_embeddings=parameters["include_embeddings"],
    )
    source_indexes = metadata.get("source_frame_indexes", [])
    source_seconds = metadata.get("source_seconds", [])
    fps = float(metadata.get("fps") or 0.0)
    for index, frame in enumerate(result["frames"]):
        image = images[index] if index < len(images) else None
        source_frame_index = source_indexes[index] if index < len(source_indexes) else index
        frame["source_frame_index"] = source_frame_index
        if index < len(source_seconds):
            frame["source_seconds"] = round(float(source_seconds[index]), 6)
        elif fps:
            frame["source_seconds"] = round(source_frame_index / fps, 6)
        if image is not None:
            frame["thumbnail"] = image_thumbnail_data_url(image)
            frame["quality"] = assess_image_quality(image)
    observe_video_sampling_metrics(metadata)
    return {
        "analysis_mode": "person_tracks",
        "stream": public_video_metadata(metadata),
        "frames": result["frames"],
        "tracks": result["tracks"],
        "tracker": result["tracker"],
        "frame_count": len(result["frames"]),
        "person_count": result["person_count"],
        "track_count": result["track_count"],
        "embedding_count": result["embedding_count"],
        "models": {
            "detector": result["detector_key"],
            "reid": result["reid_key"],
        },
    }

def stream_identifier_fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def emit_stream_event(stream: StreamRecord, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
    previous_stream = deepcopy(stream)
    stream.add_event(event_type, message, payload or {})
    persisted_payload = redact_sensitive_fields(payload or {})
    append_jsonl(
        STREAM_EVENT_STATE_PATH,
        {
            "event": event_type,
            "stream_id": stream.stream_id,
            "tenant_id": stream.tenant_id,
            "message": message,
            "payload": persisted_payload,
            "created_at": wall_time(),
        },
    )
    try:
        persist_stream(stream)
    except Exception:
        restore_stream(stream, previous_stream)
        restore_stream_snapshot_in_store(stream)
        raise


def stream_session_key(stream: StreamRecord) -> tuple[str, str]:
    return (stream.tenant_id, stream.stream_id)


def public_stream_worker_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "stream_id": session.get("stream_id"),
        "tenant_id": session.get("tenant_id"),
        "status": session.get("status"),
        "started_at": session.get("started_at"),
        "stopped_at": session.get("stopped_at"),
        "last_heartbeat_at": session.get("last_heartbeat_at"),
        "last_error_at": session.get("last_error_at"),
        "restart_count": int(session.get("restart_count", 0)),
        "frame_buffer_depth": int(session.get("frame_buffer_depth", 0)),
        "backpressure_drops": int(session.get("backpressure_drops", 0)),
        "frames_processed": int(session.get("frames_processed", 0)),
        "frames_sampled": int(session.get("frames_sampled", 0)),
        "last_analysis_at": session.get("last_analysis_at"),
        "last_person_count": int(session.get("last_person_count", 0)),
        "last_track_count": int(session.get("last_track_count", 0)),
    }


def stream_worker_sessions_snapshot() -> dict[tuple[str, str], dict[str, Any]]:
    return deepcopy(STREAM_WORKER_SESSIONS)


def restore_stream_worker_sessions(snapshot: dict[tuple[str, str], dict[str, Any]]) -> None:
    STREAM_WORKER_SESSIONS.clear()
    STREAM_WORKER_SESSIONS.update(deepcopy(snapshot))


def start_stream_worker_session(stream: StreamRecord) -> dict[str, Any]:
    key = stream_session_key(stream)
    now = wall_time()
    session = STREAM_WORKER_SESSIONS.get(key)
    if session is None:
        session = {
            "tenant_id": stream.tenant_id,
            "stream_id": stream.stream_id,
            "started_at": now,
            "restart_count": 0,
            "frame_buffer_depth": 0,
            "backpressure_drops": 0,
            "frames_processed": 0,
            "frames_sampled": 0,
        }
        STREAM_WORKER_SESSIONS[key] = session
    session.update(
        {
            "status": "running",
            "stopped_at": None,
            "last_heartbeat_at": now,
        }
    )
    emit_stream_event(stream, "stream_worker_session_started", "stream worker session started", public_stream_worker_session(session))
    return public_stream_worker_session(session)


def stop_stream_worker_session(stream: StreamRecord) -> dict[str, Any]:
    key = stream_session_key(stream)
    now = wall_time()
    session = STREAM_WORKER_SESSIONS.setdefault(
        key,
        {
            "tenant_id": stream.tenant_id,
            "stream_id": stream.stream_id,
            "started_at": now,
            "restart_count": 0,
            "frame_buffer_depth": 0,
            "backpressure_drops": 0,
            "frames_processed": 0,
            "frames_sampled": 0,
        },
    )
    session.update(
        {
            "status": "stopped",
            "stopped_at": now,
            "last_heartbeat_at": now,
            "frame_buffer_depth": 0,
        }
    )
    emit_stream_event(stream, "stream_worker_session_stopped", "stream worker session stopped", public_stream_worker_session(session))
    return public_stream_worker_session(session)


def heartbeat_stream_worker_session(stream: StreamRecord, *, frame_buffer_depth: int = 0, frames_sampled: int = 0) -> dict[str, Any]:
    key = stream_session_key(stream)
    session = STREAM_WORKER_SESSIONS.setdefault(
        key,
        {
            "tenant_id": stream.tenant_id,
            "stream_id": stream.stream_id,
            "started_at": wall_time(),
            "restart_count": 0,
            "backpressure_drops": 0,
            "frames_processed": 0,
        },
    )
    session.update(
        {
            "status": "running",
            "last_heartbeat_at": wall_time(),
            "frame_buffer_depth": max(0, int(frame_buffer_depth)),
            "frames_sampled": int(session.get("frames_sampled", 0)) + max(0, int(frames_sampled)),
        }
    )
    return public_stream_worker_session(session)


def record_stream_backpressure_drop(stream: StreamRecord, *, count: int = 1) -> dict[str, Any]:
    key = stream_session_key(stream)
    session = STREAM_WORKER_SESSIONS.setdefault(
        key,
        {
            "tenant_id": stream.tenant_id,
            "stream_id": stream.stream_id,
            "started_at": wall_time(),
            "restart_count": 0,
            "frame_buffer_depth": 0,
            "frames_processed": 0,
            "frames_sampled": 0,
        },
    )
    session["backpressure_drops"] = int(session.get("backpressure_drops", 0)) + max(0, int(count))
    session["last_heartbeat_at"] = wall_time()
    return public_stream_worker_session(session)


def record_stream_worker_failure(stream: StreamRecord, error: Exception) -> dict[str, Any]:
    key = stream_session_key(stream)
    session = STREAM_WORKER_SESSIONS.setdefault(
        key,
        {
            "tenant_id": stream.tenant_id,
            "stream_id": stream.stream_id,
            "started_at": wall_time(),
            "frame_buffer_depth": 0,
            "backpressure_drops": 0,
            "frames_processed": 0,
            "frames_sampled": 0,
        },
    )
    session["status"] = "reconnecting"
    session["restart_count"] = int(session.get("restart_count", 0)) + 1
    session["last_error_at"] = wall_time()
    session["last_heartbeat_at"] = wall_time()
    payload = public_stream_worker_session(session)
    payload["error"] = "stream worker failed"
    emit_stream_event(stream, "stream_worker_reconnecting", "stream worker reconnecting", payload)
    logger.warning(
        "stream worker failed: tenant_hash=%s stream_hash=%s error=%s",
        stream_identifier_fingerprint(stream.tenant_id),
        stream_identifier_fingerprint(stream.stream_id),
        exception_log_summary(error),
    )
    return public_stream_worker_session(session)


async def run_stream_worker_session(
    stream: StreamRecord,
    *,
    max_reconnects: int = 3,
) -> dict[str, Any]:
    """Session 内持续 rolling batch：连接保持在 worker 里，每批输出 stream_analysis_completed。"""
    session = start_stream_worker_session(stream)
    attempts = 0
    while stream.status == StreamStatus.RUNNING and attempts <= max_reconnects:
        try:
            await asyncio.to_thread(validate_media_stream_url, stream.stream_url)
            parameters = stream_analysis_parameters(stream.settings)
            async for batch_images, batch_source_indexes, batch_source_seconds, fps, _ in aiter_video_frame_batches(
                stream.stream_url,
                parameters["sample_interval_seconds"],
                parameters["batch_size"],
                parameters["read_timeout_seconds"],
            ):
                if stream.status != StreamStatus.RUNNING:
                    break
                heartbeat_stream_worker_session(
                    stream,
                    frame_buffer_depth=min(len(batch_images), STREAM_FRAME_BUFFER_LIMIT),
                    frames_sampled=len(batch_images),
                )
                metadata = {
                    "source_frame_indexes": batch_source_indexes,
                    "source_seconds": batch_source_seconds,
                    "fps": fps,
                    "extracted_frames": len(batch_images),
                    "source_frames_read": (batch_source_indexes[-1] + 1) if batch_source_indexes else 0,
                }
                analysis = await analyze_stream_frames(stream, batch_images, metadata)
                key = stream_session_key(stream)
                worker_session = STREAM_WORKER_SESSIONS[key]
                worker_session["frames_processed"] = int(worker_session.get("frames_processed", 0)) + len(batch_images)
                worker_session["last_analysis_at"] = wall_time()
                worker_session["last_person_count"] = int(analysis.get("person_count", 0))
                worker_session["last_track_count"] = int(analysis.get("track_count", 0))
                emit_stream_event(stream, "stream_analysis_completed", "stream analysis completed", analysis)
                emit_stream_event(
                    stream,
                    "stream_worker_heartbeat",
                    "stream worker heartbeat",
                    {
                        **heartbeat_stream_worker_session(stream, frame_buffer_depth=0),
                        "source_frames_read": metadata.get("source_frames_read"),
                        "extracted_frames": metadata.get("extracted_frames"),
                    },
                )
            if stream.status == StreamStatus.RUNNING:
                raise ConnectionError("stream source ended")
            return public_stream_worker_session(STREAM_WORKER_SESSIONS[stream_session_key(stream)])
        except Exception as exc:
            attempts += 1
            session = record_stream_worker_failure(stream, exc)
            if attempts > max_reconnects:
                stream.status = StreamStatus.FAILED
                emit_stream_event(stream, "stream_worker_failed", "stream worker failed", session)
                return session
            await asyncio.sleep(min(2.0, 0.25 * attempts))
    return session


def stream_worker_status() -> dict[str, Any]:
    return {
        "backend": "daemon_capable_session_controller",
        "status": "ready",
        "active_sessions": sum(1 for item in STREAM_WORKER_SESSIONS.values() if item.get("status") == "running"),
        "sessions": [public_stream_worker_session(item) for item in STREAM_WORKER_SESSIONS.values()],
        "daemon_entrypoint": "python -m app.portrait_stream_worker_daemon",
        "note": "Run the daemon entrypoint as a separate process for production stream pulling.",
    }
