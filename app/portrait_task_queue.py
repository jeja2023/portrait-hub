from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.observability import logger, wall_time
from app.portrait_response import HEALTH_CHECK_FAILED, exception_log_summary
from app.portrait_security import redact_sensitive_fields
from app.portrait_state import append_jsonl, read_json_state, write_json_state
from app.settings import (
    REDIS_URL,
    TASK_QUEUE_BACKEND,
    TASK_QUEUE_DIR,
    TASK_QUEUE_STATE_PATH,
    TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS,
)

try:  # pragma: no cover - optional production dependency
    import redis
except Exception:  # pragma: no cover - executed when the dependency is unavailable
    redis = None


_QUEUE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REDIS_CONSUMER_GROUP = "portrait-workers"


@dataclass
class QueueMessage:
    message_id: str
    queue: str
    payload: dict[str, Any]
    status: str = "queued"
    created_at: float = field(default_factory=wall_time)
    attempts: int = 0
    receipt: str | None = field(default=None, repr=False)
    raw_body: str | None = field(default=None, repr=False)

    def state_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "queue": self.queue,
            "payload": self.payload,
            "status": self.status,
            "created_at": self.created_at,
            "attempts": self.attempts,
        }

    def public_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "queue": self.queue,
            "payload": redact_sensitive_fields(self.payload),
            "status": self.status,
            "created_at": self.created_at,
            "attempts": self.attempts,
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> "QueueMessage":
        message_payload = payload.get("payload")
        if not isinstance(message_payload, dict):
            raise ValueError("Task message payload must be an object")
        return cls(
            message_id=str(payload["message_id"]),
            queue=normalize_queue_name(str(payload["queue"])),
            payload=dict(message_payload),
            status=str(payload.get("status") or "queued"),
            created_at=float(payload.get("created_at") or wall_time()),
            attempts=max(0, int(payload.get("attempts") or 0)),
        )


TASK_MESSAGES: list[QueueMessage] = []


class TaskMessageStore:
    def __init__(self, messages: list[QueueMessage]) -> None:
        self._messages = messages
        self._lock = threading.RLock()

    def append(self, message: QueueMessage) -> None:
        with self._lock:
            self.remove_by_id(message.message_id)
            self._messages.append(message)

    def remove(self, message: QueueMessage) -> None:
        self.remove_by_id(message.message_id)

    def remove_by_id(self, message_id: str) -> None:
        with self._lock:
            self._messages[:] = [item for item in self._messages if item.message_id != message_id]

    def count(self) -> int:
        with self._lock:
            return len(self._messages)

    def snapshot(self) -> list[QueueMessage]:
        with self._lock:
            return list(self._messages)


TASK_MESSAGE_STORE = TaskMessageStore(TASK_MESSAGES)


def normalize_queue_name(queue: str) -> str:
    value = str(queue).strip().lower()
    if not _QUEUE_NAME_PATTERN.fullmatch(value):
        raise ValueError("Invalid task queue name")
    return value


def append_task_queue_state(payload: dict[str, Any], *, required: bool = True) -> None:
    append_jsonl(TASK_QUEUE_STATE_PATH, payload, fail_closed=required)


def local_queue_path(queue: str, state: str) -> Path:
    queue_name = normalize_queue_name(queue)
    if state not in {"pending", "processing"}:
        raise ValueError("Invalid task queue state directory")
    return TASK_QUEUE_DIR / queue_name / state


def _cancellation_key(tenant_id: str, job_id: str) -> str:
    identity = f"{tenant_id}\0{job_id}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def local_cancellation_path(queue: str, tenant_id: str, job_id: str) -> Path:
    return TASK_QUEUE_DIR / normalize_queue_name(queue) / "cancelled" / f"{_cancellation_key(tenant_id, job_id)}.json"


def _message_path(queue: str, state: str, message_id: str) -> Path:
    if not re.fullmatch(r"msg_[a-f0-9]{16}", message_id):
        raise ValueError("Invalid task message ID")
    return local_queue_path(queue, state) / f"{message_id}.json"


def _read_local_message(path: Path) -> QueueMessage:
    payload = read_json_state(path, None)
    if not isinstance(payload, dict):
        raise ValueError("Invalid task message state")
    message = QueueMessage.from_state(payload)
    message.receipt = str(path)
    message.status = "processing"
    return message


def _safe_receipt_path(message: QueueMessage) -> Path | None:
    if not message.receipt:
        return None
    target = Path(message.receipt).resolve()
    root = TASK_QUEUE_DIR.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


class LocalTaskQueue:
    backend_name = "local_spool"

    def enqueue(self, queue: str, payload: dict[str, Any]) -> QueueMessage:
        queue_name = normalize_queue_name(queue)
        message = QueueMessage(message_id=f"msg_{uuid4().hex[:16]}", queue=queue_name, payload=dict(payload))
        target = _message_path(queue_name, "pending", message.message_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json_state(target, message.state_dict())
        message.receipt = str(target)
        TASK_MESSAGE_STORE.append(message)
        try:
            append_task_queue_state({**message.public_dict(), "event": "task_enqueued"}, required=True)
        except Exception:
            target.unlink(missing_ok=True)
            TASK_MESSAGE_STORE.remove(message)
            raise
        return message

    def _requeue_stale(self, queue: str) -> None:
        processing_dir = local_queue_path(queue, "processing")
        if not processing_dir.is_dir():
            return
        stale_before = wall_time() - max(1.0, float(TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS))
        for source in processing_dir.glob("msg_*.json"):
            try:
                if source.stat().st_mtime > stale_before:
                    continue
                target = _message_path(queue, "pending", source.stem)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                append_task_queue_state(
                    {"event": "task_requeued_after_visibility_timeout", "message_id": source.stem, "queue": queue},
                    required=False,
                )
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("Failed to recover expired local task: error=%s", exception_log_summary(exc))

    def claim(self, queue: str, consumer_id: str, block_seconds: float = 0.0) -> QueueMessage | None:
        del consumer_id
        queue_name = normalize_queue_name(queue)
        deadline = time.monotonic() + max(0.0, float(block_seconds))
        while True:
            self._requeue_stale(queue_name)
            pending_dir = local_queue_path(queue_name, "pending")
            sources = sorted(pending_dir.glob("msg_*.json")) if pending_dir.is_dir() else []
            for source in sources:
                target = _message_path(queue_name, "processing", source.stem)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    source.rename(target)
                    os.utime(target, None)
                except (FileNotFoundError, FileExistsError):
                    continue
                try:
                    message = _read_local_message(target)
                except Exception as exc:
                    logger.warning("Discarded invalid local task message: error=%s", exception_log_summary(exc))
                    target.unlink(missing_ok=True)
                    continue
                message.attempts += 1
                write_json_state(target, message.state_dict())
                message.receipt = str(target)
                TASK_MESSAGE_STORE.append(message)
                append_task_queue_state({**message.public_dict(), "event": "task_claimed"}, required=False)
                return message
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def heartbeat(self, message: QueueMessage, consumer_id: str) -> None:
        del consumer_id
        target = _safe_receipt_path(message)
        if target is None or not target.is_file():
            raise RuntimeError("Task message lease no longer exists")
        os.utime(target, None)

    def ack(self, message: QueueMessage) -> None:
        target = _safe_receipt_path(message)
        if target is not None:
            target.unlink(missing_ok=True)
        TASK_MESSAGE_STORE.remove(message)
        append_task_queue_state({**message.public_dict(), "event": "task_acknowledged"}, required=False)

    def release(self, message: QueueMessage) -> None:
        source = _safe_receipt_path(message)
        if source is None or not source.exists():
            return
        target = _message_path(message.queue, "pending", message.message_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        message.receipt = str(target)
        message.status = "queued"
        write_json_state(target, message.state_dict())
        TASK_MESSAGE_STORE.append(message)
        append_task_queue_state({**message.public_dict(), "event": "task_released"}, required=False)

    def remove(self, message: QueueMessage) -> None:
        for state in ("pending", "processing"):
            _message_path(message.queue, state, message.message_id).unlink(missing_ok=True)
        TASK_MESSAGE_STORE.remove(message)
        append_task_queue_state({**message.public_dict(), "event": "task_removed"}, required=False)

    def mark_cancelled(self, queue: str, tenant_id: str, job_id: str) -> None:
        target = local_cancellation_path(queue, tenant_id, job_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json_state(target, {"cancelled": True, "created_at": wall_time()})

    def is_cancelled(self, queue: str, tenant_id: str, job_id: str) -> bool:
        return local_cancellation_path(queue, tenant_id, job_id).is_file()

    def clear_cancelled(self, queue: str, tenant_id: str, job_id: str) -> None:
        local_cancellation_path(queue, tenant_id, job_id).unlink(missing_ok=True)

    def health(self) -> dict[str, Any]:
        pending = 0
        processing = 0
        if TASK_QUEUE_DIR.is_dir():
            pending = sum(1 for _ in TASK_QUEUE_DIR.glob("*/pending/msg_*.json"))
            processing = sum(1 for _ in TASK_QUEUE_DIR.glob("*/processing/msg_*.json"))
        return {
            "backend": self.backend_name,
            "pending_messages": pending,
            "processing_messages": processing,
            "status": "ready",
        }


class ExternalTaskQueue(LocalTaskQueue):
    backend_name = "external_queue"

    def enqueue(self, queue: str, payload: dict[str, Any]) -> QueueMessage:
        del queue, payload
        raise RuntimeError(f"Unsupported task queue backend: {TASK_QUEUE_BACKEND}")

    def health(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "configured": False, "status": "not_ready"}


class RedisTaskQueue(LocalTaskQueue):
    backend_name = "redis"

    def __init__(self) -> None:
        self._cached_client: Any | None = None
        self._known_groups: set[str] = set()

    def _client(self) -> Any:
        if redis is None:
            raise RuntimeError("redis is not installed; install requirements-prod-optional.txt")
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL is not configured")
        if self._cached_client is None:
            self._cached_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        return self._cached_client

    @staticmethod
    def _stream_key(queue: str) -> str:
        return f"portrait:{normalize_queue_name(queue)}"

    @staticmethod
    def _cancel_key(queue: str, tenant_id: str, job_id: str) -> str:
        return f"portrait:{normalize_queue_name(queue)}:cancelled:{_cancellation_key(tenant_id, job_id)}"

    def _ensure_group(self, queue: str) -> None:
        queue_name = normalize_queue_name(queue)
        if queue_name in self._known_groups:
            return
        try:
            self._client().xgroup_create(self._stream_key(queue_name), _REDIS_CONSUMER_GROUP, id="0-0", mkstream=True)
        except Exception as exc:
            message = exc.args[0] if exc.args else None
            if not isinstance(message, str) or not message.startswith("BUSYGROUP"):
                raise
        self._known_groups.add(queue_name)

    @staticmethod
    def _entry_message(queue: str, entry_id: Any, fields: Any) -> QueueMessage:
        if not isinstance(fields, dict):
            raise ValueError("Invalid Redis task message fields")
        raw_body = fields.get("body")
        if isinstance(raw_body, bytes):
            raw_body = raw_body.decode("utf-8")
        if not isinstance(raw_body, str):
            raise ValueError("Invalid Redis task message body")
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("Redis task message body must be an object")
        message = QueueMessage.from_state(payload)
        if message.queue != normalize_queue_name(queue):
            raise ValueError("Redis task message queue mismatch")
        message.receipt = entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)
        message.raw_body = raw_body
        message.status = "processing"
        message.attempts += 1
        return message

    def enqueue(self, queue: str, payload: dict[str, Any]) -> QueueMessage:
        queue_name = normalize_queue_name(queue)
        self._ensure_group(queue_name)
        message = QueueMessage(message_id=f"msg_{uuid4().hex[:16]}", queue=queue_name, payload=dict(payload))
        body = json.dumps(message.state_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            message.receipt = str(self._client().xadd(self._stream_key(queue_name), {"body": body}))
        except Exception as exc:
            logger.warning("Redis task enqueue failed: error=%s", exception_log_summary(exc))
            raise
        message.raw_body = body
        TASK_MESSAGE_STORE.append(message)
        append_task_queue_state({**message.public_dict(), "event": "task_enqueued"}, required=False)
        return message

    def claim(self, queue: str, consumer_id: str, block_seconds: float = 0.0) -> QueueMessage | None:
        queue_name = normalize_queue_name(queue)
        self._ensure_group(queue_name)
        stream = self._stream_key(queue_name)
        client = self._client()
        entries: list[Any] = []
        try:
            claimed = client.xautoclaim(
                stream,
                _REDIS_CONSUMER_GROUP,
                consumer_id,
                min_idle_time=max(1, int(float(TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS) * 1000)),
                start_id="0-0",
                count=1,
            )
            if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 and isinstance(claimed[1], list):
                entries = claimed[1]
        except Exception as exc:
            logger.warning("Redis task reclaim check failed: error=%s", exception_log_summary(exc))
        if not entries:
            kwargs: dict[str, Any] = {
                "groupname": _REDIS_CONSUMER_GROUP,
                "consumername": consumer_id,
                "streams": {stream: ">"},
                "count": 1,
            }
            if block_seconds > 0:
                kwargs["block"] = max(1, int(float(block_seconds) * 1000))
            response = client.xreadgroup(**kwargs)
            if response:
                entries = response[0][1]
        if not entries:
            return None
        entry_id, fields = entries[0]
        try:
            message = self._entry_message(queue_name, entry_id, fields)
        except Exception:
            client.xack(stream, _REDIS_CONSUMER_GROUP, entry_id)
            client.xdel(stream, entry_id)
            raise
        TASK_MESSAGE_STORE.append(message)
        append_task_queue_state({**message.public_dict(), "event": "task_claimed"}, required=False)
        return message

    def heartbeat(self, message: QueueMessage, consumer_id: str) -> None:
        if not message.receipt:
            raise RuntimeError("Redis task message receipt is missing")
        claimed = self._client().xclaim(
            self._stream_key(message.queue),
            _REDIS_CONSUMER_GROUP,
            consumer_id,
            min_idle_time=0,
            message_ids=[message.receipt],
            justid=True,
        )
        if not claimed:
            raise RuntimeError("Redis task message lease no longer exists")

    def ack(self, message: QueueMessage) -> None:
        if message.receipt:
            stream = self._stream_key(message.queue)
            self._client().xack(stream, _REDIS_CONSUMER_GROUP, message.receipt)
            self._client().xdel(stream, message.receipt)
        TASK_MESSAGE_STORE.remove(message)
        append_task_queue_state({**message.public_dict(), "event": "task_acknowledged"}, required=False)

    def release(self, message: QueueMessage) -> None:
        # Another healthy worker reclaims this pending entry after the visibility timeout.
        TASK_MESSAGE_STORE.remove(message)
        append_task_queue_state({**message.public_dict(), "event": "task_released"}, required=False)

    def remove(self, message: QueueMessage) -> None:
        if message.receipt:
            stream = self._stream_key(message.queue)
            self._client().xack(stream, _REDIS_CONSUMER_GROUP, message.receipt)
            self._client().xdel(stream, message.receipt)
        TASK_MESSAGE_STORE.remove(message)
        append_task_queue_state({**message.public_dict(), "event": "task_removed"}, required=False)

    def mark_cancelled(self, queue: str, tenant_id: str, job_id: str) -> None:
        self._client().set(self._cancel_key(queue, tenant_id, job_id), "1", ex=86400)

    def is_cancelled(self, queue: str, tenant_id: str, job_id: str) -> bool:
        return bool(self._client().exists(self._cancel_key(queue, tenant_id, job_id)))

    def clear_cancelled(self, queue: str, tenant_id: str, job_id: str) -> None:
        self._client().delete(self._cancel_key(queue, tenant_id, job_id))

    def health(self) -> dict[str, Any]:
        payload = {
            "backend": self.backend_name,
            "configured": bool(REDIS_URL),
            "driver_available": redis is not None,
            "queued_messages": TASK_MESSAGE_STORE.count(),
        }
        if not REDIS_URL or redis is None:
            return {**payload, "status": "not_ready"}
        try:
            self._client().ping()
            return {**payload, "status": "ready"}
        except Exception as exc:  # pragma: no cover - requires an external Redis instance
            logger.warning("Redis task queue health check failed: error=%s", exception_log_summary(exc))
            return {**payload, "status": "error", "error": HEALTH_CHECK_FAILED}


def configured_task_queue() -> LocalTaskQueue:
    if TASK_QUEUE_BACKEND == "redis":
        return RedisTaskQueue()
    if TASK_QUEUE_BACKEND in {"local", ""}:
        return LocalTaskQueue()
    return ExternalTaskQueue()


TASK_QUEUE = configured_task_queue()