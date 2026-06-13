import base64
import binascii
import hashlib
import hmac
import json
import math
import time
from typing import Any

from fastapi import Header, HTTPException, status

from app.settings import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_REQUIRE_AUD,
    JWT_REQUIRE_EXP,
    JWT_REQUIRE_ISS,
    JWT_REQUIRE_TENANT,
    JWT_SECRET,
    JWT_SECRET_ID,
    JWT_SECRET_KEYRING,
    RBAC_ENABLED,
)


ROLE_PERMISSIONS = {
    "admin": {"*"},
    "operator": {"infer", "compare", "gallery:read", "gallery:write", "jobs", "streams", "models:read", "admin:status", "metrics:read"},
    "algorithm": {"infer", "compare", "models:read", "models:write", "thresholds:write"},
    "auditor": {"gallery:read", "jobs:read", "streams:read", "models:read", "admin:status", "admin:export", "metrics:read"},
    "viewer": {"gallery:read", "jobs:read", "streams:read", "models:read"},
}
DEFAULT_JWT_SECRET_ID = "primary"
MAX_JWT_SECRET_ID_LENGTH = 64
JWT_SECRET_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise unauthorized("invalid JWT encoding") from exc


def normalize_jwt_secret_id(value: Any, *, allow_default: bool = False) -> str:
    if not isinstance(value, str):
        raise unauthorized("invalid JWT key id")
    cleaned = value.strip()
    if not cleaned:
        if allow_default:
            return DEFAULT_JWT_SECRET_ID
        raise unauthorized("invalid JWT key id")
    if cleaned != value or len(cleaned) > MAX_JWT_SECRET_ID_LENGTH:
        raise unauthorized("invalid JWT key id")
    if any(char not in JWT_SECRET_ID_CHARS for char in cleaned):
        raise unauthorized("invalid JWT key id")
    return cleaned


def current_jwt_secret_id() -> str:
    return normalize_jwt_secret_id(JWT_SECRET_ID or DEFAULT_JWT_SECRET_ID, allow_default=True)


def parse_jwt_secret_keyring() -> dict[str, str]:
    keyring: dict[str, str] = {}
    for raw_entry in str(JWT_SECRET_KEYRING or "").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise unauthorized("JWT keyring is invalid")
        raw_key_id, raw_secret = entry.split("=", 1)
        key_id = normalize_jwt_secret_id(raw_key_id)
        secret = raw_secret.strip()
        if not secret:
            raise unauthorized("JWT keyring is invalid")
        if key_id in keyring:
            raise unauthorized("JWT keyring is invalid")
        keyring[key_id] = secret
    return keyring


def jwt_secret_materials() -> dict[str, str]:
    materials = parse_jwt_secret_keyring()
    if JWT_SECRET:
        key_id = current_jwt_secret_id()
        previous = materials.get(key_id)
        if previous is not None and previous != JWT_SECRET:
            raise unauthorized("JWT keyring is invalid")
        materials[key_id] = JWT_SECRET
    return materials


def candidate_jwt_secrets(header: dict[str, Any]) -> list[tuple[str, str]]:
    materials = jwt_secret_materials()
    if not materials:
        raise unauthorized("JWT auth is not configured")
    raw_kid = header.get("kid")
    if raw_kid is None:
        return list(materials.items())
    key_id = normalize_jwt_secret_id(raw_kid)
    secret = materials.get(key_id)
    if secret is None:
        raise unauthorized("invalid JWT signature")
    return [(key_id, secret)]


def jwt_time_claim(payload: dict[str, Any], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise unauthorized("invalid JWT time claim")
    if isinstance(value, float) and not math.isfinite(value):
        raise unauthorized("invalid JWT time claim")
    return int(value)


def jwt_audience_matches(audience: Any) -> bool:
    if isinstance(audience, str):
        return audience == JWT_AUDIENCE
    if isinstance(audience, list):
        return any(isinstance(item, str) and item == JWT_AUDIENCE for item in audience)
    return False


def jwt_tenant_matches(claims: dict[str, Any], tenant_id: str | None) -> bool:
    if not tenant_id:
        return True
    tenant_claim = claims.get("tenant_id", claims.get("tenant"))
    tenants_claim = claims.get("tenants")
    if tenant_claim is None and tenants_claim is None:
        return not JWT_REQUIRE_TENANT
    if isinstance(tenant_claim, str) and tenant_claim == tenant_id:
        return True
    if isinstance(tenants_claim, list):
        return any(isinstance(item, str) and item == tenant_id for item in tenants_claim)
    return False


def optional_header_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def verify_hs256_jwt(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise unauthorized("invalid JWT")
    try:
        header = json.loads(base64url_decode(parts[0]).decode("utf-8"))
    except HTTPException:
        raise
    except Exception as exc:
        raise unauthorized("invalid JWT header") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise unauthorized("unsupported JWT algorithm")
    signing_input = ".".join(parts[:2]).encode("ascii")
    actual = base64url_decode(parts[2])
    verified = False
    for _, secret in candidate_jwt_secrets(header):
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if hmac.compare_digest(expected, actual):
            verified = True
            break
    if not verified:
        raise unauthorized("invalid JWT signature")
    try:
        payload = json.loads(base64url_decode(parts[1]).decode("utf-8"))
    except Exception as exc:
        raise unauthorized("invalid JWT payload") from exc
    if not isinstance(payload, dict):
        raise unauthorized("invalid JWT payload")
    issuer = payload.get("iss")
    if JWT_REQUIRE_ISS and issuer is None:
        raise unauthorized("missing JWT issuer")
    if issuer is not None and issuer != JWT_ISSUER:
        raise unauthorized("invalid JWT issuer")
    audience = payload.get("aud")
    if JWT_REQUIRE_AUD and audience is None:
        raise unauthorized("missing JWT audience")
    if audience is not None and not jwt_audience_matches(audience):
        raise unauthorized("invalid JWT audience")
    now = int(time.time())
    try:
        exp = jwt_time_claim(payload, "exp")
        if JWT_REQUIRE_EXP and exp is None:
            raise unauthorized("missing JWT expiration")
        if exp is not None and int(exp) <= now:
            raise unauthorized("JWT has expired")
        nbf = jwt_time_claim(payload, "nbf")
        if nbf is not None and int(nbf) > now:
            raise unauthorized("JWT is not active yet")
        iat = jwt_time_claim(payload, "iat")
        if iat is not None and int(iat) > now + 60:
            raise unauthorized("JWT issued-at is in the future")
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise unauthorized("invalid JWT time claim") from exc
    return payload


def roles_from_claims(claims: dict[str, Any]) -> set[str]:
    raw_roles = claims.get("roles", claims.get("role", []))
    if isinstance(raw_roles, str):
        return {raw_roles}
    if isinstance(raw_roles, list):
        return {str(role) for role in raw_roles}
    return set()


def has_permission(roles: set[str], permission: str) -> bool:
    for role in roles:
        permissions = ROLE_PERMISSIONS.get(role, set())
        root_permission = permission.split(":", 1)[0]
        if "*" in permissions or permission in permissions or root_permission in permissions:
            return True
    return False


async def require_permission(
    permission: str,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> None:
    if not RBAC_ENABLED:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized("missing bearer JWT")
    claims = verify_hs256_jwt(authorization.removeprefix("Bearer ").strip())
    if not jwt_tenant_matches(claims, optional_header_value(x_tenant_id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="JWT is not valid for tenant")
    if not has_permission(roles_from_claims(claims), permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing permission: {permission}")


def permission_dependency(permission: str):
    async def dependency(
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
    ) -> None:
        await require_permission(permission, authorization, x_tenant_id)

    return dependency
