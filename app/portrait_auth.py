import base64
import binascii
import hashlib
import hmac
import json
import math
import time
from collections.abc import Callable
from typing import Any

from fastapi import Header, HTTPException, Request, status

from app.settings import (
    API_TOKEN,
    AUTH_REQUIRED,
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_PUBLIC_KEY,
    JWT_PUBLIC_KEY_PATH,
    JWT_PUBLIC_KEYRING,
    JWT_REQUIRE_AUD,
    JWT_REQUIRE_EXP,
    JWT_REQUIRE_ISS,
    JWT_REQUIRE_TENANT,
    JWT_SECRET,
    JWT_SECRET_ID,
    JWT_SECRET_KEYRING,
    RBAC_ENABLED,
    STEP_UP_AUTH_MAX_AGE_SECONDS,
)

try:  # pragma: no cover - 可选的生产环境依赖
    import jwt as pyjwt
except Exception:  # pragma: no cover - 当依赖不存在时执行
    pyjwt = None


ROLE_PERMISSIONS = {
    "admin": {"*"},
    "operator": {
        "infer", "compare", "gallery:read", "gallery:write", "jobs", "streams", "models:read",
        "admin:status", "metrics:read", "access:read", "commercial:read", "operations:read",
        "operations:write", "support:read", "support:write",
    },
    "algorithm": {
        "infer", "compare", "models:read", "models:write", "models:approve", "thresholds:write",
        "datasets:read", "datasets:write",
    },
    "auditor": {
        "gallery:read", "jobs:read", "streams:read", "models:read", "admin:status", "admin:export",
        "metrics:read", "access:read", "commercial:read", "operations:read", "compliance:read",
        "evidence:read",
    },
    "viewer": {
        "gallery:read", "jobs:read", "streams:read", "models:read", "commercial:read",
        "operations:read", "support:read",
    },
}
DEFAULT_JWT_SECRET_ID = "primary"
MAX_JWT_SECRET_ID_LENGTH = 64
JWT_SECRET_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
SUPPORTED_ASYMMETRIC_JWT_ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}


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


def parse_jwt_public_keyring() -> dict[str, str]:
    keyring: dict[str, str] = {}
    raw = str(JWT_PUBLIC_KEYRING or "").strip()
    if not raw:
        return keyring
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for raw_key_id, raw_key in parsed.items():
                key_id = normalize_jwt_secret_id(raw_key_id)
                if not isinstance(raw_key, str) or not raw_key.strip():
                    raise unauthorized("JWT public keyring is invalid")
                keyring[key_id] = raw_key
            return keyring
    except HTTPException:
        raise
    except Exception:
        pass
    for raw_entry in raw.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise unauthorized("JWT public keyring is invalid")
        raw_key_id, raw_key = entry.split("=", 1)
        keyring[normalize_jwt_secret_id(raw_key_id)] = raw_key.strip().replace("\\n", "\n")
    return keyring


def configured_jwt_public_key() -> str:
    if JWT_PUBLIC_KEY:
        return JWT_PUBLIC_KEY.replace("\\n", "\n")
    if JWT_PUBLIC_KEY_PATH:
        try:
            with open(JWT_PUBLIC_KEY_PATH, encoding="utf-8") as file:
                return file.read()
        except Exception as exc:
            raise unauthorized("JWT public key is unavailable") from exc
    raise unauthorized("JWT public key is not configured")


def candidate_jwt_public_keys(header: dict[str, Any]) -> list[tuple[str, str]]:
    keyring = parse_jwt_public_keyring()
    raw_kid = header.get("kid")
    if raw_kid is None:
        if keyring:
            return list(keyring.items())
        return [(DEFAULT_JWT_SECRET_ID, configured_jwt_public_key())]
    key_id = normalize_jwt_secret_id(raw_kid)
    if key_id in keyring:
        return [(key_id, keyring[key_id])]
    if key_id == current_jwt_secret_id():
        return [(key_id, configured_jwt_public_key())]
    raise unauthorized("invalid JWT signature")


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
    configured_algorithm = JWT_ALGORITHM or "HS256"
    if not isinstance(header, dict):
        raise unauthorized("invalid JWT header")
    token_algorithm = str(header.get("alg") or "")
    if token_algorithm != configured_algorithm:
        raise unauthorized("unsupported JWT algorithm")
    if configured_algorithm in SUPPORTED_ASYMMETRIC_JWT_ALGORITHMS:
        return verify_pyjwt_token(token, header, configured_algorithm)
    if configured_algorithm != "HS256":
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


def verify_pyjwt_token(token: str, header: dict[str, Any], algorithm: str) -> dict[str, Any]:
    if pyjwt is None:
        raise unauthorized("PyJWT is not installed")
    options = {
        "require": [
            claim
            for claim, required in [
                ("exp", JWT_REQUIRE_EXP),
                ("iss", JWT_REQUIRE_ISS),
                ("aud", JWT_REQUIRE_AUD),
            ]
            if required
        ],
        "verify_exp": True,
        "verify_nbf": True,
        "verify_iat": True,
        "verify_iss": JWT_REQUIRE_ISS,
        "verify_aud": JWT_REQUIRE_AUD,
    }
    last_error: Exception | None = None
    for _, public_key in candidate_jwt_public_keys(header):
        try:
            payload = pyjwt.decode(
                token,
                public_key,
                algorithms=[algorithm],
                audience=JWT_AUDIENCE if JWT_REQUIRE_AUD else None,
                issuer=JWT_ISSUER if JWT_REQUIRE_ISS else None,
                options=options,
            )
            if not isinstance(payload, dict):
                raise unauthorized("invalid JWT payload")
            return payload
        except HTTPException:
            raise
        except Exception as exc:
            last_error = exc
    raise unauthorized("invalid JWT signature") from last_error


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


def step_up_seconds_remaining(claims: dict[str, Any], *, allow_session_iat: bool = False) -> int:
    raw_auth_time = claims.get("auth_time")
    if raw_auth_time is None and allow_session_iat:
        raw_auth_time = claims.get("iat")
    if isinstance(raw_auth_time, bool) or not isinstance(raw_auth_time, (int, float)):
        return 0
    authentication_time = float(raw_auth_time)
    if not math.isfinite(authentication_time):
        return 0
    now = time.time()
    if authentication_time > now + 60:
        return 0
    age = max(0.0, now - authentication_time)
    return max(0, int(STEP_UP_AUTH_MAX_AGE_SECONDS - age))


def step_up_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "step_up_authentication_required",
            "message": "recent interactive authentication is required for this high-risk operation",
            "max_age_seconds": STEP_UP_AUTH_MAX_AGE_SECONDS,
        },
        headers={"X-PortraitHub-Step-Up": "required"},
    )


async def require_step_up_authentication(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Require a recent human authentication for destructive or sensitive operations."""
    if not AUTH_REQUIRED and not RBAC_ENABLED and not API_TOKEN:
        return
    authorization = optional_header_value(authorization)
    api_key = optional_header_value(x_api_key)
    if not authorization and not api_key:
        from app.oidc_auth import browser_session_claims, require_browser_session_csrf

        session_claims = browser_session_claims(request)
        if session_claims is not None and step_up_seconds_remaining(session_claims, allow_session_iat=True) > 0:
            require_browser_session_csrf(request, session_claims)
            return
        raise step_up_required()
    if api_key or not authorization or not authorization.startswith("Bearer "):
        raise step_up_required()
    token = authorization.removeprefix("Bearer ").strip()
    if API_TOKEN and hmac.compare_digest(token, API_TOKEN):
        raise step_up_required()
    if not RBAC_ENABLED:
        raise step_up_required()
    claims = verify_hs256_jwt(token)
    if step_up_seconds_remaining(claims) <= 0:
        raise step_up_required()


async def require_permission(
    permission: str,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    request: Request | None = None,
) -> None:
    tenant_id = optional_header_value(x_tenant_id)
    authorization = optional_header_value(authorization)
    api_key = optional_header_value(x_api_key)
    if request is not None and not authorization and not api_key:
        from app.oidc_auth import browser_session_claims, require_browser_session_csrf

        session_claims = browser_session_claims(request)
        if session_claims is not None:
            session_tenant = str(session_claims.get("tenant_id") or "")
            if tenant_id and not hmac.compare_digest(tenant_id, session_tenant):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="浏览器会话与租户不匹配")
            if not has_permission(roles_from_claims(session_claims), permission):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限：{permission}")
            require_browser_session_csrf(request, session_claims)
            return
    if api_key:
        from app.portrait_access import (
            application_key_matches,
            application_key_matches_any_tenant,
            application_scopes_allow_permission,
        )

        application = application_key_matches(tenant_id, api_key) if tenant_id else application_key_matches_any_tenant(api_key)
        if application is not None:
            if application_scopes_allow_permission(application.get("scopes"), permission):
                return
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限：{permission}")
        if API_TOKEN and hmac.compare_digest(api_key, API_TOKEN):
            return

    if API_TOKEN and authorization and hmac.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        return

    if authorization or api_key:
        if not (RBAC_ENABLED and authorization and authorization.startswith("Bearer ")):
            raise unauthorized("invalid bearer JWT or API key")
    if not RBAC_ENABLED:
        # RBAC 关闭且未命中任何凭证：仅当认证为必填（生产默认）时 fail-closed，
        # 避免仅挂 permission_dependency 而漏挂 require_api_token 的路由被匿名访问。
        # AUTH_REQUIRED=false 的本地/测试部署保持宽松（与 require_api_token 一致）。
        if AUTH_REQUIRED and not API_TOKEN:
            raise unauthorized("missing API token, application API key, or enabled RBAC JWT backend")
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized("missing bearer JWT")
    claims = verify_hs256_jwt(authorization.removeprefix("Bearer ").strip())
    if not jwt_tenant_matches(claims, tenant_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="JWT 与租户不匹配")
    if tenant_id:
        from app.portrait_access import tenant_is_active

        if not tenant_is_active(tenant_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="租户已停用")
    if not has_permission(roles_from_claims(claims), permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限：{permission}")

def commercial_capability_for_request(permission: str, path: str) -> str | None:
    mappings = (
        ("/v1/infer/faces", "face_detection"),
        ("/v1/infer/persons", "person_detection"),
        ("/v1/infer/pose", "pose"),
        ("/v1/infer/appearance", "appearance"),
        ("/v1/infer/gait", "gait"),
        ("/v1/compare/faces", "face_embedding"),
        ("/v1/compare/persons", "appearance"),
        ("/v1/compare/gait", "gait"),
        ("/v1/compare/multimodal", "multimodal_compare"),
        ("/v1/uploads/video", "video_processing"),
        ("/v1/jobs/video", "video_processing"),
        ("/v1/streams", "stream_processing"),
    )
    if permission not in {"infer", "compare", "jobs", "streams"}:
        return None
    return next((capability for prefix, capability in mappings if path.startswith(prefix)), None)


def permission_dependency(permission: str) -> Callable[..., Any]:
    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> Any:
        await require_permission(permission, authorization, x_tenant_id, x_api_key, request)
        from app import settings

        if not settings.COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED:
            yield
            return
        capability = commercial_capability_for_request(permission, request.url.path)
        if capability is None:
            yield
            return
        from app.commercial_concurrency import acquire_commercial_slot, release_commercial_slot
        from app.portrait_async import run_blocking_io
        from app.portrait_commercial import require_entitlement_capability
        from app.portrait_projects import project_id_from_request
        from app.portrait_security import tenant_id_from_request

        tenant_id = tenant_id_from_request(request)
        project_id = project_id_from_request(request, tenant_id)
        entitlement = await run_blocking_io(
            require_entitlement_capability,
            tenant_id,
            project_id,
            capability,
        )
        limit = int((entitlement or {}).get("effective_concurrency_limit") or 1)
        slot_token = await run_blocking_io(acquire_commercial_slot, tenant_id, project_id, limit)
        try:
            yield
        finally:
            await run_blocking_io(release_commercial_slot, tenant_id, project_id, slot_token)

    return dependency
