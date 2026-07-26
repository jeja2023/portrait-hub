from __future__ import annotations

import hashlib
import threading
import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app import settings

redis: Any
try:  # pragma: no cover - optional production dependency
    import redis as _redis
except Exception:  # pragma: no cover - development installs may omit Redis
    redis = None
else:
    redis = _redis

_LOCK = threading.Lock()
_ACTIVE: dict[tuple[str, str], set[str]] = {}
_REDIS_CLIENT: Any | None = None

_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local expires_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]
local lease_ms = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms)
if redis.call('ZCARD', key) >= limit then
  return 0
end
redis.call('ZADD', key, expires_ms, token)
redis.call('PEXPIRE', key, lease_ms + 1000)
return 1
"""

_RELEASE_SCRIPT = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""


def _slot_key(tenant_id: str, project_id: str) -> str:
    scope_digest = hashlib.sha256(f"{tenant_id}\0{project_id}".encode()).hexdigest()
    return f"portrait:commercial:concurrency:{scope_digest}"


def _client() -> Any:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if redis is None:
        raise RuntimeError("redis is not installed; install requirements/prod-optional.txt")
    _REDIS_CLIENT = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _REDIS_CLIENT


def _backend_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "commercial_concurrency_backend_unavailable",
            "message": "commercial concurrency backend is unavailable",
        },
        headers={"Retry-After": "1"},
    )


def _exhausted() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "commercial_concurrency_exhausted",
            "message": "project commercial concurrency limit is exhausted",
        },
        headers={"Retry-After": "1"},
    )


def acquire_commercial_slot(tenant_id: str, project_id: str, limit: int) -> str:
    normalized_limit = max(1, int(limit))
    token = uuid4().hex
    if settings.REDIS_URL.strip():
        lease_ms = max(5_000, int(float(settings.COMMERCIAL_CONCURRENCY_LEASE_SECONDS) * 1000))
        now_ms = int(time.time() * 1000)
        try:
            acquired = int(
                _client().eval(
                    _ACQUIRE_SCRIPT,
                    1,
                    _slot_key(tenant_id, project_id),
                    now_ms,
                    now_ms + lease_ms,
                    normalized_limit,
                    token,
                    lease_ms,
                )
            )
        except Exception as exc:
            raise _backend_unavailable() from exc
        if acquired != 1:
            raise _exhausted()
        return token

    key = (tenant_id, project_id)
    with _LOCK:
        active = _ACTIVE.setdefault(key, set())
        if len(active) >= normalized_limit:
            raise _exhausted()
        active.add(token)
    return token


def release_commercial_slot(tenant_id: str, project_id: str, token: str) -> None:
    if settings.REDIS_URL.strip():
        try:
            _client().eval(_RELEASE_SCRIPT, 1, _slot_key(tenant_id, project_id), token)
        except Exception:
            # An orphaned lease is bounded by its TTL; request success must not be rewritten during cleanup.
            return
        return

    key = (tenant_id, project_id)
    with _LOCK:
        active = _ACTIVE.get(key)
        if active is None:
            return
        active.discard(token)
        if not active:
            _ACTIVE.pop(key, None)


def reset_commercial_slots() -> None:
    global _REDIS_CLIENT
    with _LOCK:
        _ACTIVE.clear()
    _REDIS_CLIENT = None


__all__ = ["acquire_commercial_slot", "release_commercial_slot", "reset_commercial_slots"]
