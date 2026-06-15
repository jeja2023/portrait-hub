import json
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

from fastapi import Request


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "created_at": record.created,
        }
        request_id = current_request_id()
        tenant_id = current_tenant_id()
        traceparent = current_traceparent()
        if request_id:
            payload["request_id"] = request_id
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if traceparent:
            payload["traceparent"] = traceparent
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("gpu-worker")
for handler in logging.getLogger().handlers:
    handler.setFormatter(JsonLogFormatter())
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TRACEPARENT_PATTERN = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")
REQUEST_ID_CONTEXT: ContextVar[str | None] = ContextVar("request_id", default=None)
TENANT_ID_CONTEXT: ContextVar[str | None] = ContextVar("tenant_id", default=None)
TRACEPARENT_CONTEXT: ContextVar[str | None] = ContextVar("traceparent", default=None)


def now() -> float:
    return time.perf_counter()


def wall_time() -> float:
    return time.time()


def normalize_request_id(raw_request_id: str | None) -> str | None:
    if raw_request_id is None or raw_request_id == "":
        return None
    if raw_request_id.strip() != raw_request_id:
        return None
    if not REQUEST_ID_PATTERN.fullmatch(raw_request_id):
        return None
    return raw_request_id


def request_id_from_headers(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id

    request_id = normalize_request_id(request.headers.get("x-request-id")) or str(uuid.uuid4())
    request.state.request_id = request_id
    return request_id


def traceparent_from_headers(request: Request) -> str | None:
    raw_traceparent = request.headers.get("traceparent")
    if raw_traceparent and TRACEPARENT_PATTERN.fullmatch(raw_traceparent.strip().lower()):
        request.state.traceparent = raw_traceparent.strip().lower()
        return request.state.traceparent
    return getattr(request.state, "traceparent", None)


def current_request_id() -> str | None:
    return REQUEST_ID_CONTEXT.get()


def current_tenant_id() -> str | None:
    return TENANT_ID_CONTEXT.get()


def current_traceparent() -> str | None:
    return TRACEPARENT_CONTEXT.get()


def set_log_context(
    *,
    request_id: str | None = None,
    tenant_id: str | None = None,
    traceparent: str | None = None,
) -> tuple[Token[str | None], Token[str | None], Token[str | None]]:
    return (
        REQUEST_ID_CONTEXT.set(request_id),
        TENANT_ID_CONTEXT.set(tenant_id),
        TRACEPARENT_CONTEXT.set(traceparent),
    )


def reset_log_context(tokens: tuple[Token[str | None], Token[str | None], Token[str | None]]) -> None:
    request_token, tenant_token, trace_token = tokens
    REQUEST_ID_CONTEXT.reset(request_token)
    TENANT_ID_CONTEXT.reset(tenant_token)
    TRACEPARENT_CONTEXT.reset(trace_token)


def log_json(level: int, event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {"event": event, **fields}
    payload.setdefault("request_id", current_request_id())
    payload.setdefault("tenant_id", current_tenant_id())
    payload.setdefault("traceparent", current_traceparent())
    logger.log(level, json.dumps({key: value for key, value in payload.items() if value is not None}, ensure_ascii=False))


@contextmanager
def trace_span(name: str, **attributes: Any) -> Iterator[None]:
    try:  # pragma: no cover - optional production dependency
        from opentelemetry import trace
    except Exception:
        yield
        return
    tracer = trace.get_tracer("portrait-hub")
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield
