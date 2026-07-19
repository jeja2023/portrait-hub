from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import Any

from app.portrait_access import application_key_matches
from app.portrait_auth import ROLE_PERMISSIONS, optional_header_value, roles_from_claims, unauthorized, verify_hs256_jwt
from app.security import global_api_token_matches
from app.settings import (
    API_TOKEN,
    AUTH_REQUIRED,
    CONSOLE_WS_TICKET_MAX_ENTRIES,
    CONSOLE_WS_TICKET_TTL_SECONDS,
    RBAC_ENABLED,
    REDIS_URL,
)

try:
    import redis
except ImportError:  # pragma: no cover - optional production dependency
    redis = None


@dataclass(frozen=True)
class ConsoleWebSocketTicket:
    tenant_id: str
    resource_type: str
    resource_id: str
    permission: str
    expires_at: float
    fingerprint: str


_TICKETS: dict[str, ConsoleWebSocketTicket] = {}
_TICKETS_LOCK = threading.RLock()
_REDIS_TICKET_PREFIX = "portrait:console:ws-ticket:"
_REDIS_GETDEL_SCRIPT = """
local value = redis.call("GET", KEYS[1])
if value then redis.call("DEL", KEYS[1]) end
return value
"""


def _redis_ticket_client() -> Any | None:
    if not REDIS_URL:
        return None
    if redis is None:
        raise RuntimeError("redis is required when REDIS_URL is configured")
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _ticket_payload(record: ConsoleWebSocketTicket) -> str:
    return json.dumps(
        {
            "tenant_id": record.tenant_id,
            "resource_type": record.resource_type,
            "resource_id": record.resource_id,
            "permission": record.permission,
            "expires_at": record.expires_at,
            "fingerprint": record.fingerprint,
        },
        separators=(",", ":"),
    )


def _ticket_from_payload(payload: str) -> ConsoleWebSocketTicket:
    data = json.loads(payload)
    return ConsoleWebSocketTicket(
        tenant_id=str(data["tenant_id"]),
        resource_type=str(data["resource_type"]),
        resource_id=str(data["resource_id"]),
        permission=str(data["permission"]),
        expires_at=float(data["expires_at"]),
        fingerprint=str(data["fingerprint"]),
    )


def _ticket_digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def _purge_expired_tickets(now: float) -> None:
    expired = [digest for digest, ticket in _TICKETS.items() if ticket.expires_at <= now]
    for digest in expired:
        _TICKETS.pop(digest, None)


def issue_console_ws_ticket(
    *,
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    permission: str,
    now: float | None = None,
) -> tuple[str, ConsoleWebSocketTicket]:
    issued_at = time.time() if now is None else float(now)
    redis_client = _redis_ticket_client()
    if redis_client is not None:
        raw_ticket = f"cwt_{token_urlsafe(32)}"
        digest = _ticket_digest(raw_ticket)
        record = ConsoleWebSocketTicket(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            permission=permission,
            expires_at=issued_at + CONSOLE_WS_TICKET_TTL_SECONDS,
            fingerprint=digest[:16],
        )
        redis_client.setex(
            _REDIS_TICKET_PREFIX + digest,
            CONSOLE_WS_TICKET_TTL_SECONDS,
            _ticket_payload(record),
        )
        return raw_ticket, record

    with _TICKETS_LOCK:
        _purge_expired_tickets(issued_at)
        while len(_TICKETS) >= CONSOLE_WS_TICKET_MAX_ENTRIES:
            oldest_digest = min(_TICKETS, key=lambda digest: _TICKETS[digest].expires_at)
            _TICKETS.pop(oldest_digest, None)
        while True:
            raw_ticket = f"cwt_{token_urlsafe(32)}"
            digest = _ticket_digest(raw_ticket)
            if digest not in _TICKETS:
                break
        record = ConsoleWebSocketTicket(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            permission=permission,
            expires_at=issued_at + CONSOLE_WS_TICKET_TTL_SECONDS,
            fingerprint=digest[:16],
        )
        _TICKETS[digest] = record
        return raw_ticket, record


def consume_console_ws_ticket(
    raw_ticket: str,
    *,
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    permission: str,
    now: float | None = None,
) -> bool:
    current_time = time.time() if now is None else float(now)
    digest = _ticket_digest(raw_ticket)
    redis_client = _redis_ticket_client()
    if redis_client is not None:
        payload = redis_client.eval(
            _REDIS_GETDEL_SCRIPT,
            1,
            _REDIS_TICKET_PREFIX + digest,
        )
        record = _ticket_from_payload(payload) if isinstance(payload, str) else None
    else:
        with _TICKETS_LOCK:
            _purge_expired_tickets(current_time)
            record = _TICKETS.pop(digest, None)
    if record is None:
        return False
    return (
        record.expires_at > current_time
        and record.tenant_id == tenant_id
        and record.resource_type == resource_type
        and record.resource_id == resource_id
        and record.permission == permission
    )


def clear_console_ws_tickets() -> None:
    redis_client = _redis_ticket_client()
    if redis_client is not None:
        cursor = 0
        pattern = _REDIS_TICKET_PREFIX + "*"
        while True:
            cursor, keys = redis_client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                redis_client.delete(*keys)
            if cursor == 0:
                break
    with _TICKETS_LOCK:
        _TICKETS.clear()


def _permissions_for_roles(roles: set[str]) -> list[str]:
    permissions: set[str] = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))
    return sorted(permissions)


def console_principal(
    *,
    tenant_id: str,
    authorization: str | None,
    x_api_key: str | None,
    request: Any | None = None,
) -> dict[str, Any]:
    bearer = optional_header_value(authorization)
    api_key = optional_header_value(x_api_key)
    if not bearer and not api_key and request is not None:
        from app.oidc_auth import browser_session_claims

        session_claims = browser_session_claims(request)
        if session_claims is not None:
            roles = {str(role) for role in session_claims.get("roles", [])}
            return {
                "auth_kind": str(session_claims.get("auth_kind") or "oidc"),
                "subject": str(session_claims.get("sub") or "oidc-user"),
                "display_name": str(session_claims.get("name") or session_claims.get("sub") or "企业用户"),
                "email": str(session_claims.get("email") or ""),
                "roles": sorted(roles),
                "permissions": _permissions_for_roles(roles),
                "scopes": [],
                "expires_at": float(session_claims["exp"]),
            }
    if global_api_token_matches(bearer, api_key):
        return {
            "auth_kind": "global_api_token",
            "subject": "global-api-token",
            "roles": ["admin"],
            "permissions": ["*"],
            "scopes": ["*"],
            "expires_at": None,
        }

    if api_key:
        application = application_key_matches(tenant_id, api_key)
        if application is not None:
            scopes = sorted({str(scope) for scope in application.get("scopes", [])})
            return {
                "auth_kind": "application_api_key",
                "subject": str(application.get("app_id") or "application"),
                "roles": [],
                "permissions": scopes,
                "scopes": scopes,
                "expires_at": None,
            }

    if RBAC_ENABLED and bearer and bearer.startswith("Bearer "):
        claims = verify_hs256_jwt(bearer.removeprefix("Bearer ").strip())
        roles = roles_from_claims(claims)
        expires_at = claims.get("exp")
        return {
            "auth_kind": "jwt",
            "subject": str(claims.get("sub") or "jwt-subject"),
            "roles": sorted(roles),
            "permissions": _permissions_for_roles(roles),
            "scopes": [],
            "expires_at": float(expires_at) if isinstance(expires_at, (int, float)) else None,
        }

    if not AUTH_REQUIRED and not RBAC_ENABLED and not API_TOKEN:
        return {
            "auth_kind": "development_anonymous",
            "subject": "local-development",
            "roles": ["admin"],
            "permissions": ["*"],
            "scopes": ["*"],
            "expires_at": None,
        }
    raise unauthorized("missing authenticated console principal")


__all__ = [
    "ConsoleWebSocketTicket",
    "clear_console_ws_tickets",
    "console_principal",
    "consume_console_ws_ticket",
    "issue_console_ws_ticket",
]