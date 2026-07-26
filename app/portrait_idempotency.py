from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import Header, HTTPException, Request, Response, status

from app import settings
from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_projects import DEFAULT_PROJECT_ID, identity_claims_from_request
from app.portrait_security import inferred_tenant_id_from_request

redis: Any
try:  # pragma: no cover - optional production dependency
    import redis as _redis
except ImportError:  # pragma: no cover - development installs may omit Redis
    redis = None
else:
    redis = _redis

_LOCK = threading.RLock()
_RECORDS: dict[str, IdempotencyRecord] = {}
_REDIS_CLIENT: Any | None = None
_PREFIX = "portrait:idempotency:"
_RESERVE_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if existing then return existing end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2], 'NX')
return nil
"""
_COMPLETE_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if not existing then return 0 end
local decoded = cjson.decode(existing)
if decoded.owner_token ~= ARGV[1] or decoded.request_hash ~= ARGV[2] then return -1 end
redis.call('SET', KEYS[1], ARGV[3], 'EX', ARGV[4])
return 1
"""
_RELEASE_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if not existing then return 0 end
local decoded = cjson.decode(existing)
if decoded.owner_token ~= ARGV[1] then return -1 end
return redis.call('DEL', KEYS[1])
"""


@dataclass(frozen=True)
class IdempotencyContext:
    storage_key: str
    public_key_fingerprint: str
    request_hash: str
    owner_token: str
    expires_at: float


@dataclass
class IdempotencyRecord:
    request_hash: str
    owner_token: str
    state: str
    expires_at: float
    status_code: int | None = None
    headers: dict[str, str] | None = None
    body_base64: str | None = None


class IdempotencyReplay(Exception):
    def __init__(self, record: IdempotencyRecord) -> None:
        super().__init__("idempotent response replay")
        self.record = record


def _redis_client() -> Any:
    global _REDIS_CLIENT
    if redis is None:
        raise RuntimeError("redis is required when REDIS_URL is configured")
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _REDIS_CLIENT


def _record_payload(record: IdempotencyRecord) -> str:
    return json.dumps(asdict(record), separators=(",", ":"), sort_keys=True)


def _record_from_payload(payload: str) -> IdempotencyRecord:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("invalid idempotency record")
    return IdempotencyRecord(
        request_hash=str(value["request_hash"]),
        owner_token=str(value["owner_token"]),
        state=str(value["state"]),
        expires_at=float(value["expires_at"]),
        status_code=int(value["status_code"]) if value.get("status_code") is not None else None,
        headers={str(key): str(item) for key, item in (value.get("headers") or {}).items()},
        body_base64=str(value["body_base64"]) if value.get("body_base64") is not None else None,
    )


def _backend_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "idempotency_backend_unavailable",
            "message": "idempotency storage is unavailable",
        },
        headers={"Retry-After": "1"},
    )


def _purge_expired(now: float) -> None:
    for storage_key in [key for key, record in _RECORDS.items() if record.expires_at <= now]:
        _RECORDS.pop(storage_key, None)


def _reserve(context: IdempotencyContext) -> IdempotencyRecord | None:
    pending = IdempotencyRecord(
        request_hash=context.request_hash,
        owner_token=context.owner_token,
        state="in_progress",
        expires_at=context.expires_at,
    )
    ttl = max(1, int(context.expires_at - time.time()))
    if settings.REDIS_URL.strip():
        try:
            payload = _redis_client().eval(
                _RESERVE_SCRIPT,
                1,
                context.storage_key,
                _record_payload(pending),
                ttl,
            )
        except Exception as exc:
            raise _backend_unavailable() from exc
        return _record_from_payload(payload) if isinstance(payload, str) else None

    with _LOCK:
        _purge_expired(time.time())
        existing = _RECORDS.get(context.storage_key)
        if existing is not None:
            return IdempotencyRecord(**asdict(existing))
        if len(_RECORDS) >= settings.IDEMPOTENCY_MAX_ENTRIES:
            completed = [key for key, record in _RECORDS.items() if record.state == "completed"]
            if not completed:
                raise _backend_unavailable()
            oldest = min(completed, key=lambda key: _RECORDS[key].expires_at)
            _RECORDS.pop(oldest, None)
        _RECORDS[context.storage_key] = pending
    return None


def _complete(context: IdempotencyContext, record: IdempotencyRecord) -> bool:
    ttl = max(1, int(context.expires_at - time.time()))
    if settings.REDIS_URL.strip():
        try:
            result = int(
                _redis_client().eval(
                    _COMPLETE_SCRIPT,
                    1,
                    context.storage_key,
                    context.owner_token,
                    context.request_hash,
                    _record_payload(record),
                    ttl,
                )
            )
        except Exception:
            return False
        return result == 1
    with _LOCK:
        existing = _RECORDS.get(context.storage_key)
        if (
            existing is None
            or existing.owner_token != context.owner_token
            or existing.request_hash != context.request_hash
        ):
            return False
        _RECORDS[context.storage_key] = record
    return True


def _release(context: IdempotencyContext) -> None:
    if settings.REDIS_URL.strip():
        try:
            _redis_client().eval(_RELEASE_SCRIPT, 1, context.storage_key, context.owner_token)
        except Exception:
            return
        return
    with _LOCK:
        existing = _RECORDS.get(context.storage_key)
        if existing is not None and existing.owner_token == context.owner_token:
            _RECORDS.pop(context.storage_key, None)


def _principal(request: Request) -> str:
    claims = identity_claims_from_request(request) or {}
    for key in ("sub", "email", "preferred_username"):
        value = str(claims.get(key) or "").strip()
        if value:
            return f"claim:{key}:{value}"
    for header in ("x-api-key", "authorization", "cookie"):
        value = str(request.headers.get(header) or "").strip()
        if value:
            return f"header:{header}:{hashlib.sha256(value.encode()).hexdigest()}"
    return "authenticated-default"


def _scope_key(request: Request, idempotency_key: str) -> tuple[str, str]:
    state = getattr(request, "state", None)
    tenant_id = (
        str(getattr(state, "portrait_tenant_id", "") or "").strip()
        or str(request.headers.get("x-tenant-id") or "").strip()
        or str(inferred_tenant_id_from_request(request) or "default")
    )
    project_id = (
        str(getattr(state, "portrait_project_id", "") or "").strip()
        or str(request.headers.get("x-project-id") or "").strip()
        or DEFAULT_PROJECT_ID
    )
    public_fingerprint = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
    digest = hashlib.sha256(
        "\0".join((tenant_id, project_id, _principal(request), idempotency_key)).encode()
    ).hexdigest()
    return _PREFIX + digest, public_fingerprint


async def require_idempotent_write(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=256),
) -> None:
    normalized_key = str(idempotency_key or "").strip()
    if not normalized_key:
        return
    body = await request.body()
    request_hash = hashlib.sha256(
        b"\0".join(
            (
                request.method.upper().encode(),
                request.url.path.encode(),
                request.url.query.encode(),
                body,
            )
        )
    ).hexdigest()
    storage_key, public_fingerprint = _scope_key(request, normalized_key)
    timestamp = time.time()
    context = IdempotencyContext(
        storage_key=storage_key,
        public_key_fingerprint=public_fingerprint,
        request_hash=request_hash,
        owner_token=secrets.token_hex(16),
        expires_at=timestamp + settings.IDEMPOTENCY_WINDOW_SECONDS,
    )
    existing = await run_blocking_io(_reserve, context)
    if existing is None:
        request.state.portrait_idempotency_context = context
        return
    if existing.request_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_key_conflict",
                "message": "idempotency key was already used with a different request",
                "key_fingerprint": public_fingerprint,
            },
        )
    if existing.state != "completed" or existing.status_code is None or existing.body_base64 is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_request_in_progress",
                "message": "an identical request with this idempotency key is still in progress",
                "key_fingerprint": public_fingerprint,
            },
            headers={"Retry-After": "1"},
        )
    raise IdempotencyReplay(existing)


def replay_response(exc: IdempotencyReplay) -> Response:
    record = exc.record
    body = base64.b64decode(record.body_base64 or "")
    headers = dict(record.headers or {})
    headers["Idempotency-Replayed"] = "true"
    headers["Idempotency-Key-Expires-At"] = str(int(record.expires_at))
    return Response(content=body, status_code=record.status_code or 200, headers=headers)


async def finalize_idempotent_response(request: Request, response: Response) -> Response:
    context = getattr(getattr(request, "state", None), "portrait_idempotency_context", None)
    if not isinstance(context, IdempotencyContext):
        return response

    chunks: list[bytes] = []
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        chunks.append(body)
    else:
        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is not None:
            async for chunk in body_iterator:
                chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    payload = b"".join(chunks)
    replacement = Response(
        content=payload,
        status_code=response.status_code,
        headers=dict(response.headers),
        background=response.background,
    )
    if response.status_code >= 500 or len(payload) > settings.IDEMPOTENCY_MAX_RESPONSE_BYTES:
        await run_blocking_io(_release, context)
        replacement.headers["Idempotency-Status"] = "not-stored"
        return replacement

    stored_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {"content-type", "etag", "location", "retry-after"}
    }
    record = IdempotencyRecord(
        request_hash=context.request_hash,
        owner_token=context.owner_token,
        state="completed",
        expires_at=context.expires_at,
        status_code=response.status_code,
        headers=stored_headers,
        body_base64=base64.b64encode(payload).decode("ascii"),
    )
    stored = await run_blocking_io(_complete, context, record)
    replacement.headers["Idempotency-Replayed"] = "false"
    replacement.headers["Idempotency-Key-Expires-At"] = str(int(context.expires_at))
    if not stored:
        replacement.headers["Idempotency-Status"] = "store-failed"
        logger.error(
            "idempotency response could not be committed: key_fingerprint=%s",
            context.public_key_fingerprint,
        )
    return replacement


def reset_idempotency_store() -> None:
    global _REDIS_CLIENT
    with _LOCK:
        _RECORDS.clear()
    _REDIS_CLIENT = None


__all__ = [
    "IdempotencyReplay",
    "finalize_idempotent_response",
    "replay_response",
    "require_idempotent_write",
    "reset_idempotency_store",
]
