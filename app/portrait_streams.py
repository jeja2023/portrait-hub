import json
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from app.media.stream_decode import mask_stream_url, validate_media_stream_url
from app.observability import logger, wall_time
from app.portrait_crypto import decrypt_bytes, encrypt_bytes
from app.portrait_response import exception_log_summary
from app.portrait_security import is_sensitive_field, redact_sensitive_fields
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import ALLOW_STREAM_URLS, PORTRAIT_STORAGE_BACKEND, PORTRAIT_STREAMS_STATE_PATH

PROTECTED_STATE_VALUE_MARKER = "__portrait_protected_value__"
PROTECTED_STATE_VALUE_PAYLOAD = "payload"


class StreamStatus(StrEnum):
    REGISTERED = "registered"
    RUNNING = "running"
    STOPPED = "stopped"
    BLOCKED = "blocked"
    FAILED = "failed"


def normalize_stream_status(value: str | StreamStatus) -> StreamStatus:
    try:
        return value if isinstance(value, StreamStatus) else StreamStatus(str(value))
    except ValueError:
        return StreamStatus.REGISTERED


@dataclass
class StreamEvent:
    event_id: str
    type: str
    message: str
    created_at: float = field(default_factory=wall_time)
    payload: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "message": self.message,
            "created_at": self.created_at,
            "payload": redact_sensitive_fields(self.payload),
        }

    def state_dict(self) -> dict[str, Any]:
        payload = deepcopy(self.payload)
        frames = payload.get("frames")
        if isinstance(frames, list):
            for frame in frames:
                if isinstance(frame, dict):
                    for key in ("thumbnail", "image", "preview"):
                        frame.pop(key, None)
        return {
            "event_id": self.event_id,
            "type": self.type,
            "message": self.message,
            "created_at": self.created_at,
            "payload": redact_sensitive_fields(payload),
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> "StreamEvent":
        event_payload = payload.get("payload")
        return cls(
            event_id=str(payload["event_id"]),
            type=str(payload.get("type", "")),
            message=str(payload.get("message", "")),
            created_at=float(payload.get("created_at", wall_time())),
            payload=deepcopy(event_payload) if isinstance(event_payload, dict) else {},
        )


@dataclass
class StreamRecord:
    stream_id: str
    tenant_id: str
    stream_url: str
    name: str | None
    settings: dict[str, Any]
    metadata: dict[str, Any]
    status: StreamStatus = StreamStatus.REGISTERED
    created_at: float = field(default_factory=wall_time)
    updated_at: float = field(default_factory=wall_time)
    worker_lease_owner: str | None = None
    worker_lease_expires_at: float | None = None
    events: list[StreamEvent] = field(default_factory=list)

    def add_event(self, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append(
            StreamEvent(
                event_id=f"evt_{uuid4().hex[:16]}",
                type=event_type,
                message=message,
                payload=payload or {},
            )
        )
        self.events = self.events[-200:]
        self.updated_at = wall_time()

    def public_dict(self, include_events: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stream_id": self.stream_id,
            "tenant_id": self.tenant_id,
            "stream_url": mask_stream_url(self.stream_url),
            "name": self.name,
            "settings": redact_sensitive_fields(self.settings),
            "metadata": redact_sensitive_fields(self.metadata),
            "status": str(normalize_stream_status(self.status)),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "event_count": len(self.events),
            "worker_lease_active": bool(self.worker_lease_owner and (self.worker_lease_expires_at or 0.0) > wall_time()),
            "worker_lease_expires_at": self.worker_lease_expires_at,
        }
        if include_events:
            payload["events"] = [event.public_dict() for event in self.events]
        return payload

    def state_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "tenant_id": self.tenant_id,
            "stream_url": self.stream_url,
            "name": self.name,
            "settings": protect_sensitive_state_fields(self.settings),
            "metadata": protect_sensitive_state_fields(self.metadata),
            "status": str(normalize_stream_status(self.status)),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "worker_lease_owner": self.worker_lease_owner,
            "worker_lease_expires_at": self.worker_lease_expires_at,
            "events": [event.state_dict() for event in self.events],
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> "StreamRecord":
        return cls(
            stream_id=str(payload["stream_id"]),
            tenant_id=str(payload.get("tenant_id", "default")),
            stream_url=str(payload.get("stream_url", "")),
            name=payload.get("name"),
            settings=reveal_sensitive_state_fields(payload.get("settings")) if isinstance(payload.get("settings"), dict) else {},
            metadata=reveal_sensitive_state_fields(payload.get("metadata")) if isinstance(payload.get("metadata"), dict) else {},
            status=normalize_stream_status(str(payload.get("status", StreamStatus.REGISTERED))),
            created_at=float(payload.get("created_at", wall_time())),
            updated_at=float(payload.get("updated_at", wall_time())),
            worker_lease_owner=str(payload["worker_lease_owner"]) if payload.get("worker_lease_owner") else None,
            worker_lease_expires_at=float(payload["worker_lease_expires_at"]) if payload.get("worker_lease_expires_at") is not None else None,
            events=[
                StreamEvent.from_state(item)
                for item in payload.get("events", [])
                if isinstance(item, dict) and "event_id" in item
            ],
        )


StreamKey = tuple[str, str]


STREAMS: dict[StreamKey, StreamRecord] = {}
STREAMS_LOCK = threading.RLock()


def stream_key(tenant_id: str, stream_id: str) -> StreamKey:
    return (str(tenant_id), str(stream_id))


def stream_records_snapshot() -> list[StreamRecord]:
    with STREAMS_LOCK:
        return list(STREAMS.values())


def restore_stream_snapshot_in_store(stream: StreamRecord) -> None:
    with STREAMS_LOCK:
        STREAMS[stream_key(stream.tenant_id, stream.stream_id)] = stream


def protect_stream_url(stream_url: str) -> dict[str, Any]:
    return encrypt_bytes(stream_url.encode("utf-8"))


def reveal_stream_url(payload: dict[str, Any]) -> str:
    return decrypt_bytes(payload).decode("utf-8")


def protect_sensitive_state_fields(value: Any, key: str = "") -> Any:
    if key and is_sensitive_field(key):
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return {
            PROTECTED_STATE_VALUE_MARKER: True,
            PROTECTED_STATE_VALUE_PAYLOAD: encrypt_bytes(raw),
        }
    if isinstance(value, dict):
        return {str(item_key): protect_sensitive_state_fields(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [protect_sensitive_state_fields(item) for item in value]
    return deepcopy(value)


def reveal_sensitive_state_fields(value: Any) -> Any:
    if isinstance(value, dict):
        protected_payload = value.get(PROTECTED_STATE_VALUE_PAYLOAD)
        if value.get(PROTECTED_STATE_VALUE_MARKER) is True and isinstance(protected_payload, dict):
            raw = decrypt_bytes(protected_payload).decode("utf-8")
            return json.loads(raw)
        return {str(item_key): reveal_sensitive_state_fields(item_value) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [reveal_sensitive_state_fields(item) for item in value]
    return deepcopy(value)


def stream_state_dict(stream: StreamRecord) -> dict[str, Any]:
    payload = stream.state_dict()
    payload["stream_url_protected"] = protect_stream_url(stream.stream_url)
    payload["settings"] = protect_sensitive_state_fields(stream.settings)
    payload["metadata"] = protect_sensitive_state_fields(stream.metadata)
    payload.pop("stream_url", None)
    return payload


def postgres_streams_enabled() -> bool:
    return PORTRAIT_STORAGE_BACKEND == "postgres"


def streams_state_payload() -> dict[str, Any]:
    with STREAMS_LOCK:
        return {
            "version": 1,
            "streams": [
                stream.state_dict()
                if postgres_streams_enabled()
                else stream_state_dict(stream)
                for stream in sorted(STREAMS.values(), key=lambda item: (item.tenant_id, item.stream_id))
            ],
        }


def save_streams_state() -> None:
    write_json_state(PORTRAIT_STREAMS_STATE_PATH, streams_state_payload())


def restore_stream(stream: StreamRecord, previous: StreamRecord) -> None:
    stream.stream_id = previous.stream_id
    stream.tenant_id = previous.tenant_id
    stream.stream_url = previous.stream_url
    stream.name = previous.name
    stream.settings = deepcopy(previous.settings)
    stream.metadata = deepcopy(previous.metadata)
    stream.status = previous.status
    stream.created_at = previous.created_at
    stream.updated_at = previous.updated_at
    stream.worker_lease_owner = previous.worker_lease_owner
    stream.worker_lease_expires_at = previous.worker_lease_expires_at
    stream.events = deepcopy(previous.events)


def persist_stream(stream: StreamRecord) -> None:
    if postgres_streams_enabled():
        from app.portrait_postgres import upsert_stream

        upsert_stream(stream.state_dict())
        return
    key = stream_key(stream.tenant_id, stream.stream_id)
    with STREAMS_LOCK:
        previous = STREAMS.get(key)
        STREAMS[key] = stream
        try:
            save_streams_state()
        except Exception:
            if previous is None:
                STREAMS.pop(key, None)
            else:
                STREAMS[key] = previous
            raise


def delete_stream_state(tenant_id: str, stream_id: str) -> None:
    if postgres_streams_enabled():
        from app.portrait_postgres import delete_stream

        delete_stream(tenant_id, stream_id)
        return
    save_streams_state()


def load_streams_state() -> None:
    if postgres_streams_enabled():
        from app.portrait_postgres import load_streams_snapshot

        payload = {"streams": load_streams_snapshot()}
    else:
        payload = read_json_state(PORTRAIT_STREAMS_STATE_PATH, {"streams": []})
    if not isinstance(payload, dict):
        handle_state_read_error(f"streams state 根节点必须是映射: {PORTRAIT_STREAMS_STATE_PATH}")
        return
    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        handle_state_read_error(f"streams state streams 必须是列表: {PORTRAIT_STREAMS_STATE_PATH}")
        return
    with STREAMS_LOCK:
        STREAMS.clear()
        for item in streams:
            if not isinstance(item, dict) or "stream_id" not in item:
                continue
            protected_url = item.get("stream_url_protected")
            if isinstance(protected_url, dict):
                try:
                    item = {**item, "stream_url": reveal_stream_url(protected_url)}
                except Exception as exc:
                    logger.warning("skipping stream with unreadable protected URL: %s", exception_log_summary(exc))
                    continue
            try:
                stream = StreamRecord.from_state(item)
            except Exception as exc:
                logger.warning("已跳过无效视频流状态: %s", exception_log_summary(exc))
                continue
            STREAMS[stream_key(stream.tenant_id, stream.stream_id)] = stream


def refresh_streams_state() -> None:
    with STREAMS_LOCK:
        current_streams = dict(STREAMS)
        load_streams_state()
        persisted_streams = dict(STREAMS)
        STREAMS.clear()
        STREAMS.update(current_streams)
        for key, persisted_stream in persisted_streams.items():
            current_stream = current_streams.get(key)
            if current_stream is None or persisted_stream.updated_at > current_stream.updated_at:
                STREAMS[key] = persisted_stream


def create_stream(
    stream_url: str,
    *,
    tenant_id: str = "default",
    name: str | None = None,
    settings: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> StreamRecord:
    validate_media_stream_url(stream_url)
    with STREAMS_LOCK:
        stream = StreamRecord(
            stream_id=f"str_{uuid4().hex[:16]}",
            tenant_id=tenant_id,
            stream_url=stream_url,
            name=name,
            settings=settings or {},
            metadata=metadata or {},
        )
        stream.add_event("stream_registered", "stream registered")
        key = stream_key(stream.tenant_id, stream.stream_id)
        STREAMS[key] = stream
        try:
            persist_stream(stream)
        except Exception:
            STREAMS.pop(key, None)
            raise
        return stream


def get_stream(stream_id: str, tenant_id: str | None = None) -> StreamRecord | None:
    with STREAMS_LOCK:
        if tenant_id is not None:
            return STREAMS.get(stream_key(tenant_id, stream_id))
        matches = [stream for stream in STREAMS.values() if stream.stream_id == stream_id]
        return matches[0] if len(matches) == 1 else None


def start_stream(stream: StreamRecord) -> StreamRecord:
    with STREAMS_LOCK:
        previous_stream = deepcopy(stream)
        if not ALLOW_STREAM_URLS:
            stream.status = StreamStatus.BLOCKED
            stream.add_event(
                "stream_start_blocked",
                "stream pulling is disabled by ALLOW_STREAM_URLS",
                {"allow_stream_urls": False},
            )
            try:
                persist_stream(stream)
            except Exception:
                restore_stream(stream, previous_stream)
                STREAMS[stream_key(stream.tenant_id, stream.stream_id)] = stream
                raise
            return stream
        stream.status = StreamStatus.RUNNING
        stream.worker_lease_owner = None
        stream.worker_lease_expires_at = None
        stream.add_event("stream_started", "stream session started")
        try:
            persist_stream(stream)
        except Exception:
            restore_stream(stream, previous_stream)
            STREAMS[stream_key(stream.tenant_id, stream.stream_id)] = stream
            raise
        return stream


def stop_stream(stream: StreamRecord) -> StreamRecord:
    with STREAMS_LOCK:
        previous_stream = deepcopy(stream)
        stream.status = StreamStatus.STOPPED
        stream.worker_lease_owner = None
        stream.worker_lease_expires_at = None
        stream.add_event("stream_stopped", "stream session stopped")
        try:
            persist_stream(stream)
        except Exception:
            restore_stream(stream, previous_stream)
            STREAMS[stream_key(stream.tenant_id, stream.stream_id)] = stream
            raise
        return stream



def stream_worker_lease_available(stream: StreamRecord, owner_id: str, *, now: float | None = None) -> bool:
    current_time = wall_time() if now is None else float(now)
    if normalize_stream_status(stream.status) != StreamStatus.RUNNING:
        return False
    if not stream.worker_lease_owner:
        return True
    if stream.worker_lease_owner == owner_id:
        return True
    return float(stream.worker_lease_expires_at or 0.0) <= current_time


def acquire_stream_worker_lease(stream: StreamRecord, owner_id: str, ttl_seconds: float) -> StreamRecord | None:
    with STREAMS_LOCK:
        key = stream_key(stream.tenant_id, stream.stream_id)
        current = STREAMS.get(key)
        if current is None or not stream_worker_lease_available(current, owner_id):
            return None
        previous_stream = deepcopy(current)
        current.worker_lease_owner = owner_id
        current.worker_lease_expires_at = wall_time() + max(1.0, float(ttl_seconds))
        current.updated_at = wall_time()
        try:
            persist_stream(current)
        except Exception:
            restore_stream(current, previous_stream)
            STREAMS[key] = current
            raise
        return deepcopy(current)


def renew_stream_worker_lease(stream: StreamRecord, owner_id: str, ttl_seconds: float) -> bool:
    with STREAMS_LOCK:
        key = stream_key(stream.tenant_id, stream.stream_id)
        current = STREAMS.get(key)
        if current is None or current.worker_lease_owner != owner_id:
            return False
        if normalize_stream_status(current.status) != StreamStatus.RUNNING:
            return False
        previous_stream = deepcopy(current)
        current.worker_lease_expires_at = wall_time() + max(1.0, float(ttl_seconds))
        current.updated_at = wall_time()
        try:
            persist_stream(current)
        except Exception:
            restore_stream(current, previous_stream)
            STREAMS[key] = current
            raise
        stream.worker_lease_owner = current.worker_lease_owner
        stream.worker_lease_expires_at = current.worker_lease_expires_at
        return True


def release_stream_worker_lease(stream: StreamRecord, owner_id: str) -> bool:
    with STREAMS_LOCK:
        key = stream_key(stream.tenant_id, stream.stream_id)
        current = STREAMS.get(key)
        if current is None or current.worker_lease_owner != owner_id:
            return False
        previous_stream = deepcopy(current)
        current.worker_lease_owner = None
        current.worker_lease_expires_at = None
        current.updated_at = wall_time()
        try:
            persist_stream(current)
        except Exception:
            restore_stream(current, previous_stream)
            STREAMS[key] = current
            raise
        stream.worker_lease_owner = None
        stream.worker_lease_expires_at = None
        return True


def remove_stream(stream_id: str, tenant_id: str) -> bool:
    with STREAMS_LOCK:
        key = stream_key(tenant_id, stream_id)
        stream = STREAMS.pop(key, None)
        if stream is None:
            return False
        try:
            delete_stream_state(tenant_id, stream_id)
        except Exception:
            STREAMS[key] = stream
            raise
        return True
