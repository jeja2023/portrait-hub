import asyncio
import hashlib
from copy import deepcopy
from typing import Any, Callable

from app.observability import logger, wall_time
from app.portrait_response import exception_log_summary
from app.portrait_security import redact_sensitive_fields
from app.portrait_state import append_jsonl
from app.portrait_streams import STREAMS, StreamRecord, StreamStatus, persist_stream, restore_stream, stream_key
from app.settings import MAX_STREAM_FRAMES, STREAM_EVENT_STATE_PATH, STREAM_FRAME_INTERVAL, STREAM_READ_TIMEOUT_SECONDS
from app.video_io import extract_video_frames_from_path


STREAM_WORKER_SESSIONS: dict[tuple[str, str], dict[str, Any]] = {}
STREAM_FRAME_BUFFER_LIMIT = 1


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
        STREAMS[stream_key(stream.tenant_id, stream.stream_id)] = stream
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
    frame_handler: Callable[[Any, StreamRecord, int], Any] | None = None,
    max_reconnects: int = 3,
) -> dict[str, Any]:
    """Pull a stream once per reconnect window and expose heartbeat/backpressure state."""
    session = start_stream_worker_session(stream)
    attempts = 0
    while stream.status == StreamStatus.RUNNING and attempts <= max_reconnects:
        try:
            frames, metadata = await asyncio.to_thread(
                extract_video_frames_from_path,
                stream.stream_url,
                int(stream.settings.get("frame_interval", STREAM_FRAME_INTERVAL)),
                int(stream.settings.get("max_frames", MAX_STREAM_FRAMES)),
                int(stream.settings.get("read_timeout_seconds", STREAM_READ_TIMEOUT_SECONDS)),
            )
            heartbeat_stream_worker_session(stream, frame_buffer_depth=min(len(frames), STREAM_FRAME_BUFFER_LIMIT), frames_sampled=len(frames))
            for index, image in enumerate(frames):
                if index >= STREAM_FRAME_BUFFER_LIMIT:
                    record_stream_backpressure_drop(stream)
                elif frame_handler is not None:
                    result = frame_handler(image, stream, index)
                    if hasattr(result, "__await__"):
                        await result
                key = stream_session_key(stream)
                STREAM_WORKER_SESSIONS[key]["frames_processed"] = int(STREAM_WORKER_SESSIONS[key].get("frames_processed", 0)) + 1
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
