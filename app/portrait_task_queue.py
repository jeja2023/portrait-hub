from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.observability import logger, wall_time
from app.portrait_response import HEALTH_CHECK_FAILED, exception_log_summary
from app.portrait_security import redact_sensitive_fields
from app.portrait_state import append_jsonl
from app.settings import REDIS_URL, TASK_QUEUE_BACKEND, TASK_QUEUE_STATE_PATH

try:  # pragma: no cover - 可选的生产环境依赖
    import redis  # 可选依赖，来自 requirements-prod-optional.txt
except Exception:  # pragma: no cover - 当依赖不存在时执行
    redis = None


@dataclass
class QueueMessage:
    message_id: str
    queue: str
    payload: dict[str, Any]
    status: str = "queued"
    created_at: float = field(default_factory=wall_time)

    def public_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "queue": self.queue,
            "payload": redact_sensitive_fields(self.payload),
            "status": self.status,
            "created_at": self.created_at,
        }


TASK_MESSAGES: list[QueueMessage] = []


def append_task_queue_state(payload: dict[str, Any], *, required: bool = True) -> None:
    append_jsonl(TASK_QUEUE_STATE_PATH, payload, fail_closed=required)


class LocalTaskQueue:
    backend_name = "local_background"

    def enqueue(self, queue: str, payload: dict[str, Any]) -> QueueMessage:
        message = QueueMessage(message_id=f"msg_{uuid4().hex[:16]}", queue=queue, payload=payload)
        TASK_MESSAGES.append(message)
        try:
            append_task_queue_state({**message.public_dict(), "event": "task_enqueued"}, required=True)
        except Exception:
            TASK_MESSAGES.remove(message)
            raise
        return message

    def health(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "queued_messages": len(TASK_MESSAGES), "status": "ready"}


class ExternalTaskQueue(LocalTaskQueue):
    backend_name = "external_queue"

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "configured": TASK_QUEUE_BACKEND not in {"", "local"},
            "status": "adapter_ready",
            "note": "Deploy Redis/RabbitMQ/Kafka workers and bridge this adapter for distributed execution.",
        }


class RedisTaskQueue(LocalTaskQueue):
    backend_name = "redis"

    def __init__(self) -> None:
        self._cached_client: Any | None = None

    def _client(self) -> Any:
        if redis is None:
            raise RuntimeError("redis is not installed; install requirements-prod-optional.txt")
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL is not configured")
        # 复用单个客户端/连接池，而不是每次调用都重新构建一个。
        if self._cached_client is None:
            self._cached_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        return self._cached_client

    def enqueue(self, queue: str, payload: dict[str, Any]) -> QueueMessage:
        import json

        message = QueueMessage(message_id=f"msg_{uuid4().hex[:16]}", queue=queue, payload=payload)
        body = json.dumps({**message.public_dict(), "event": "task_enqueued"}, ensure_ascii=False, sort_keys=True)
        try:
            self._client().lpush(f"portrait:{queue}", body)
            TASK_MESSAGES.append(message)
            append_task_queue_state({**message.public_dict(), "event": "task_enqueued"}, required=False)
            return message
        except Exception as exc:
            logger.warning("redis enqueue failed, falling back to local task queue: %s", exception_log_summary(exc))
            fallback_message = QueueMessage(
                message_id=message.message_id,
                queue=queue,
                payload=payload,
                status="queued_local_fallback",
                created_at=message.created_at,
            )
            TASK_MESSAGES.append(fallback_message)
            try:
                append_task_queue_state(
                    {**fallback_message.public_dict(), "event": "task_enqueued_local_fallback"},
                    required=False,
                )
            except Exception as state_exc:
                logger.warning("local task queue fallback state append failed: %s", exception_log_summary(state_exc))
            return fallback_message

    def health(self) -> dict[str, Any]:
        payload = {
            "backend": self.backend_name,
            "configured": bool(REDIS_URL),
            "driver_available": redis is not None,
            "queued_messages": len(TASK_MESSAGES),
        }
        if not REDIS_URL or redis is None:
            return {**payload, "status": "not_ready"}
        try:
            self._client().ping()
            return {**payload, "status": "ready"}
        except Exception as exc:  # pragma: no cover - 需要外部 Redis 支持
            logger.warning("redis task queue health check failed: %s", exception_log_summary(exc))
            return {**payload, "status": "error", "error": HEALTH_CHECK_FAILED}


def configured_task_queue() -> LocalTaskQueue:
    if TASK_QUEUE_BACKEND == "redis":
        return RedisTaskQueue()
    if TASK_QUEUE_BACKEND in {"local", ""}:
        return LocalTaskQueue()
    return ExternalTaskQueue()


TASK_QUEUE = configured_task_queue()
