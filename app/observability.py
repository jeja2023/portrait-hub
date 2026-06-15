import json
import logging
import os
import re
import time
import uuid
from typing import Any

from fastapi import Request


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "created_at": record.created,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("gpu-worker")
for handler in logging.getLogger().handlers:
    handler.setFormatter(JsonLogFormatter())
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TRACEPARENT_PATTERN = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


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


def log_json(level: int, event: str, **fields: Any) -> None:
    logger.log(level, json.dumps({"event": event, **fields}, ensure_ascii=False))
