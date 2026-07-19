from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.observability import logger
from app.portrait_auth import ROLE_PERMISSIONS
from app.settings import (
    LOCAL_AUTH_ALLOW_REMOTE,
    LOCAL_AUTH_COOKIE_SECURE,
    LOCAL_AUTH_ENABLED,
    LOCAL_AUTH_PASSWORD,
    LOCAL_AUTH_SESSION_MAX_AGE_SECONDS,
    LOCAL_AUTH_SESSION_SECRET,
    LOCAL_AUTH_TENANT_ID,
    LOCAL_AUTH_USERNAME,
    OIDC_ALLOW_INSECURE_HTTP,
    OIDC_CLIENT_ID,
    OIDC_CLIENT_SECRET,
    OIDC_COOKIE_SECURE,
    OIDC_DEFAULT_ROLE,
    OIDC_DEFAULT_TENANT_ID,
    OIDC_ENABLED,
    OIDC_GROUPS_CLAIM,
    OIDC_HTTP_TIMEOUT_SECONDS,
    OIDC_IDENTITY_ADMIN_URL,
    OIDC_ISSUER,
    OIDC_PROVIDER_NAME,
    OIDC_REDIRECT_URI,
    OIDC_ROLE_CLAIM,
    OIDC_ROLE_MAPPING,
    OIDC_SCOPES,
    OIDC_SESSION_COOKIE_NAME,
    OIDC_SESSION_MAX_AGE_SECONDS,
    OIDC_SESSION_SECRET,
    OIDC_TENANT_CLAIM,
)

router = APIRouter()
_FLOW_COOKIE = "portrait_oidc_flow"
_LOCAL_SESSION_COOKIE = "portrait_local_session"
_CSRF_COOKIE = "portrait_csrf"
_DEFAULT_LOCAL_PASSWORD = "123456"
_DEFAULT_LOCAL_SESSION_SECRET = "portrait-hub-development-local-session-secret"
_TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_DISCOVERY_CACHE: tuple[float, dict[str, Any]] | None = None


class LocalLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC encoding") from exc


def _allowed_oidc_url(value: str) -> bool:
    return value.startswith("https://") or (OIDC_ALLOW_INSECURE_HTTP and value.startswith("http://"))


def oidc_is_configured() -> bool:
    scopes = set(OIDC_SCOPES.split())
    return bool(
        OIDC_ENABLED
        and OIDC_ISSUER
        and OIDC_CLIENT_ID
        and len(OIDC_SESSION_SECRET) >= 32
        and "openid" in scopes
        and _allowed_oidc_url(OIDC_ISSUER)
        and (not OIDC_REDIRECT_URI or _allowed_oidc_url(OIDC_REDIRECT_URI))
    )


def _require_oidc_config() -> None:
    if not oidc_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Enterprise login is not configured")
    if not OIDC_ALLOW_INSECURE_HTTP and not OIDC_ISSUER.startswith("https://"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC issuer must use HTTPS")


def _production_profile() -> bool:
    from app.settings import PORTRAIT_RUNTIME_PROFILE

    return PORTRAIT_RUNTIME_PROFILE in {"prod", "production"}


def _request_is_loopback(request: Request) -> bool:
    host = request.client.host if request.client is not None else ""
    return host in {"127.0.0.1", "::1", "testclient"}


def local_auth_is_configured() -> bool:
    if not LOCAL_AUTH_ENABLED or not LOCAL_AUTH_USERNAME or not LOCAL_AUTH_PASSWORD:
        return False
    if len(LOCAL_AUTH_SESSION_SECRET) < 32 or not _TENANT_PATTERN.fullmatch(LOCAL_AUTH_TENANT_ID):
        return False
    uses_default_credentials = (
        LOCAL_AUTH_PASSWORD == _DEFAULT_LOCAL_PASSWORD
        or LOCAL_AUTH_SESSION_SECRET == _DEFAULT_LOCAL_SESSION_SECRET
    )
    return not (uses_default_credentials and (_production_profile() or LOCAL_AUTH_ALLOW_REMOTE))


def _signed_payload(payload: dict[str, Any], *, secret: str | None = None) -> str:
    encoded = _b64url_encode(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    signing_secret = secret if secret is not None else OIDC_SESSION_SECRET
    signature = hmac.new(signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64url_encode(signature)}"


def _read_signed_payload(
    value: str | None,
    *,
    purpose: str,
    secret: str | None = None,
) -> dict[str, Any] | None:
    signing_secret = secret if secret is not None else OIDC_SESSION_SECRET
    if not value or not signing_secret:
        return None
    try:
        encoded, raw_signature = value.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = _b64url_decode(raw_signature)
    except HTTPException:
        return None
    if not hmac.compare_digest(expected, supplied):
        return None
    try:
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("purpose") != purpose:
        return None
    expires_at = payload.get("exp")
    if not isinstance(expires_at, (int, float)) or float(expires_at) <= time.time():
        return None
    return payload


def browser_session_claims(request: Request | None) -> dict[str, Any] | None:
    cookies = getattr(request, "cookies", None) if request is not None else None
    if cookies is None:
        return None
    if local_auth_is_configured():
        local_claims = _read_signed_payload(
            cookies.get(_LOCAL_SESSION_COOKIE),
            purpose="local-session",
            secret=LOCAL_AUTH_SESSION_SECRET,
        )
        if local_claims is not None:
            return local_claims
    if oidc_is_configured():
        oidc_claims = _read_signed_payload(
            cookies.get(OIDC_SESSION_COOKIE_NAME),
            purpose="session",
        )
        if oidc_claims is not None:
            return oidc_claims
    return None


def oidc_session_claims(request: Request | None) -> dict[str, Any] | None:
    claims = browser_session_claims(request)
    return claims if claims and claims.get("auth_kind") == "oidc" else None

def require_browser_session_csrf(request: Request, claims: dict[str, Any]) -> None:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return
    expected = claims.get("csrf")
    supplied = request.headers.get("x-csrf-token")
    cookie_value = request.cookies.get(_CSRF_COOKIE)
    if not isinstance(expected, str) or not supplied or not cookie_value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
    if not hmac.compare_digest(expected, supplied) or not hmac.compare_digest(expected, cookie_value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")


def _claim_value(claims: dict[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _string_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _role_mapping() -> dict[str, set[str]]:
    try:
        value = json.loads(OIDC_ROLE_MAPPING or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    output: dict[str, set[str]] = {}
    for external, local in value.items():
        mapped = _string_values(local)
        output[str(external)] = {role for role in mapped if role in ROLE_PERMISSIONS}
    return output


def _local_roles(claims: dict[str, Any]) -> list[str]:
    external = _string_values(_claim_value(claims, OIDC_ROLE_CLAIM))
    external.update(_string_values(_claim_value(claims, OIDC_GROUPS_CLAIM)))
    mapping = _role_mapping()
    roles: set[str] = set()
    for value in external:
        roles.update(mapping.get(value, set()))
    if not roles and OIDC_DEFAULT_ROLE in ROLE_PERMISSIONS:
        roles.add(OIDC_DEFAULT_ROLE)
    if not roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Enterprise identity is missing a valid role or tenant")
    return sorted(roles)


def _tenant_id(claims: dict[str, Any]) -> str:
    value = _claim_value(claims, OIDC_TENANT_CLAIM)
    tenant_id = value.strip() if isinstance(value, str) else OIDC_DEFAULT_TENANT_ID
    if not tenant_id or not _TENANT_PATTERN.fullmatch(tenant_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Enterprise identity is missing a valid role or tenant")
    return tenant_id


def _safe_return_to(value: str | None) -> str:
    candidate = (value or "/").strip()
    return candidate if candidate.startswith("/") and not candidate.startswith("//") else "/"


async def _discovery() -> dict[str, Any]:
    global _DISCOVERY_CACHE
    _require_oidc_config()
    now = time.time()
    if _DISCOVERY_CACHE and _DISCOVERY_CACHE[0] > now:
        return _DISCOVERY_CACHE[1]
    url = OIDC_ISSUER.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=OIDC_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        document = response.json()
    if not isinstance(document, dict) or document.get("issuer") != OIDC_ISSUER.rstrip("/"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid OIDC discovery document")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = document.get(key)
        if not isinstance(endpoint, str) or (
            not OIDC_ALLOW_INSECURE_HTTP and not endpoint.startswith("https://")
        ):
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid OIDC discovery endpoint")
    _DISCOVERY_CACHE = (now + 300, document)
    return document


def _hash_algorithm(algorithm: str) -> hashes.HashAlgorithm:
    values: dict[str, hashes.HashAlgorithm] = {
        "RS256": hashes.SHA256(),
        "RS384": hashes.SHA384(),
        "RS512": hashes.SHA512(),
        "ES256": hashes.SHA256(),
        "ES384": hashes.SHA384(),
        "ES512": hashes.SHA512(),
    }
    selected = values.get(algorithm)
    if selected is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unsupported OIDC signature algorithm")
    return selected


def _verify_jwk_signature(token: str, jwk: dict[str, Any], algorithm: str) -> None:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC ID token")
    signature = _b64url_decode(parts[2])
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    digest = _hash_algorithm(algorithm)
    try:
        if algorithm.startswith("RS") and jwk.get("kty") == "RSA":
            modulus = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
            exponent = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
            public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
            public_key.verify(signature, signing_input, padding.PKCS1v15(), digest)
            return
        if algorithm.startswith("ES") and jwk.get("kty") == "EC":
            curves = {"P-256": ec.SECP256R1(), "P-384": ec.SECP384R1(), "P-521": ec.SECP521R1()}
            curve = curves.get(str(jwk.get("crv")))
            if curve is None or len(signature) % 2:
                raise ValueError("invalid EC key")
            x_value = int.from_bytes(_b64url_decode(str(jwk["x"])), "big")
            y_value = int.from_bytes(_b64url_decode(str(jwk["y"])), "big")
            public_key = ec.EllipticCurvePublicNumbers(x_value, y_value, curve).public_key()
            half = len(signature) // 2
            der_signature = encode_dss_signature(
                int.from_bytes(signature[:half], "big"),
                int.from_bytes(signature[half:], "big"),
            )
            public_key.verify(der_signature, signing_input, ec.ECDSA(digest))
            return
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC ID token validation") from exc
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC JWK does not match the signature algorithm")


async def _validate_id_token(token: str, *, nonce: str) -> dict[str, Any]:
    try:
        encoded_header, encoded_payload, _ = token.split(".", 2)
        header = json.loads(_b64url_decode(encoded_header).decode("utf-8"))
        claims = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC ID token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC ID token")
    algorithm = str(header.get("alg") or "")
    key_id = str(header.get("kid") or "")
    discovery = await _discovery()
    async with httpx.AsyncClient(timeout=OIDC_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = await client.get(str(discovery["jwks_uri"]), headers={"Accept": "application/json"})
        response.raise_for_status()
        document = response.json()
    keys = document.get("keys") if isinstance(document, dict) else None
    jwk = next(
        (
            item
            for item in keys or []
            if isinstance(item, dict)
            and str(item.get("kid") or "") == key_id
            and (not item.get("alg") or item.get("alg") == algorithm)
        ),
        None,
    )
    if jwk is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC signing key was not found")
    _verify_jwk_signature(token, jwk, algorithm)

    now = time.time()
    issuer = claims.get("iss")
    audience = claims.get("aud")
    audiences = ({audience} if isinstance(audience, str) else {str(item) for item in audience if isinstance(item, str)}) if isinstance(audience, (str, list)) else set()
    if issuer != OIDC_ISSUER.rstrip("/") or OIDC_CLIENT_ID not in audiences:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC issuer or audience")
    if len(audiences) > 1 and claims.get("azp") != OIDC_CLIENT_ID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC authorized party")
    if claims.get("nonce") != nonce:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC nonce validation failed")
    expires_at = claims.get("exp")
    if not isinstance(expires_at, (int, float)) or float(expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC ID token expired")
    not_before = claims.get("nbf")
    issued_at = claims.get("iat")
    if isinstance(not_before, (int, float)) and float(not_before) > now + 60:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC ID token validation")
    if isinstance(issued_at, (int, float)) and float(issued_at) > now + 60:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC ID token issue time")
    if not isinstance(claims.get("sub"), str) or not str(claims["sub"]).strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC subject is missing")
    return claims


def _redirect_uri(request: Request) -> str:
    value = OIDC_REDIRECT_URI or str(request.url_for("oidc_callback"))
    if not OIDC_ALLOW_INSECURE_HTTP and not value.startswith("https://"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC redirect URI must use HTTPS")
    return value


def _set_oidc_session(response: RedirectResponse, claims: dict[str, Any]) -> None:
    response.delete_cookie(_LOCAL_SESSION_COOKIE, path="/", secure=LOCAL_AUTH_COOKIE_SECURE, samesite="lax")
    now = int(time.time())
    token_expiry = int(float(claims["exp"]))
    expires_at = min(token_expiry, now + OIDC_SESSION_MAX_AGE_SECONDS)
    csrf = secrets.token_urlsafe(24)
    session = {
        "purpose": "session",
        "auth_kind": "oidc",
        "sub": str(claims["sub"]),
        "name": str(claims.get("name") or claims.get("preferred_username") or claims["sub"]),
        "email": str(claims.get("email") or ""),
        "tenant_id": _tenant_id(claims),
        "roles": _local_roles(claims),
        "csrf": csrf,
        "iat": now,
        "exp": expires_at,
    }
    response.set_cookie(
        OIDC_SESSION_COOKIE_NAME,
        _signed_payload(session),
        max_age=max(1, expires_at - now),
        httponly=True,
        secure=OIDC_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        _CSRF_COOKIE,
        csrf,
        max_age=max(1, expires_at - now),
        httponly=False,
        secure=OIDC_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_oidc_cookies(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(OIDC_SESSION_COOKIE_NAME, path="/", secure=OIDC_COOKIE_SECURE, samesite="lax")
    response.delete_cookie(_CSRF_COOKIE, path="/", secure=OIDC_COOKIE_SECURE, samesite="lax")
    response.delete_cookie(_FLOW_COOKIE, path="/", secure=OIDC_COOKIE_SECURE, samesite="lax")


def _set_local_session(response: JSONResponse, username: str) -> None:
    clear_oidc_cookies(response)
    now = int(time.time())
    expires_at = now + LOCAL_AUTH_SESSION_MAX_AGE_SECONDS
    csrf = secrets.token_urlsafe(24)
    session = {
        "purpose": "local-session",
        "auth_kind": "local",
        "sub": username,
        "name": username,
        "email": "",
        "tenant_id": LOCAL_AUTH_TENANT_ID,
        "roles": ["admin"],
        "csrf": csrf,
        "iat": now,
        "exp": expires_at,
    }
    response.set_cookie(
        _LOCAL_SESSION_COOKIE,
        _signed_payload(session, secret=LOCAL_AUTH_SESSION_SECRET),
        max_age=LOCAL_AUTH_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=LOCAL_AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        _CSRF_COOKIE,
        csrf,
        max_age=LOCAL_AUTH_SESSION_MAX_AGE_SECONDS,
        httponly=False,
        secure=LOCAL_AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_browser_session_cookies(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(_LOCAL_SESSION_COOKIE, path="/", secure=LOCAL_AUTH_COOKIE_SECURE, samesite="lax")
    clear_oidc_cookies(response)


@router.get("/v1/auth/config")
async def auth_public_config(request: Request) -> dict[str, Any]:
    local_enabled = local_auth_is_configured() and (
        LOCAL_AUTH_ALLOW_REMOTE or _request_is_loopback(request)
    )
    return {
        "local_enabled": local_enabled,
        "oidc_enabled": oidc_is_configured(),
        "provider_name": OIDC_PROVIDER_NAME,
        "credential_login_available": True,
    }


@router.post("/v1/auth/local/login")
async def local_login(payload: LocalLoginRequest, request: Request) -> JSONResponse:
    if not local_auth_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="本地账号登录未配置")
    if not LOCAL_AUTH_ALLOW_REMOTE and not _request_is_loopback(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="本地账号仅允许从本机登录")
    username_matches = hmac.compare_digest(payload.username, LOCAL_AUTH_USERNAME)
    password_matches = hmac.compare_digest(payload.password, LOCAL_AUTH_PASSWORD)
    if not username_matches or not password_matches:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    response = JSONResponse({"status": "success"})
    _set_local_session(response, payload.username)
    return response


@router.get("/v1/auth/oidc/config")
async def oidc_public_config() -> dict[str, Any]:
    return {
        "enabled": oidc_is_configured(),
        "provider_name": OIDC_PROVIDER_NAME,
        "credential_login_available": True,
    }


@router.get("/auth/oidc/login")
async def oidc_login(request: Request, return_to: str = "/") -> RedirectResponse:
    discovery = await _discovery()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    flow = {
        "purpose": "flow",
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "return_to": _safe_return_to(return_to),
        "exp": int(time.time()) + 600,
    }
    query = urlencode(
        {
            "client_id": OIDC_CLIENT_ID,
            "response_type": "code",
            "scope": OIDC_SCOPES,
            "redirect_uri": _redirect_uri(request),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(str(discovery["authorization_endpoint"]) + "?" + query, status_code=302)
    response.set_cookie(
        _FLOW_COOKIE,
        _signed_payload(flow),
        max_age=600,
        httponly=True,
        secure=OIDC_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/oidc/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    flow = _read_signed_payload(request.cookies.get(_FLOW_COOKIE), purpose="flow")
    if error or flow is None or not code or not state or not hmac.compare_digest(str(flow.get("state") or ""), state):
        response = RedirectResponse("/?oidc_error=login_failed", status_code=302)
        clear_oidc_cookies(response)
        return response
    try:
        discovery = await _discovery()
        body = {
            "grant_type": "authorization_code",
            "client_id": OIDC_CLIENT_ID,
            "code": code,
            "redirect_uri": _redirect_uri(request),
            "code_verifier": str(flow["verifier"]),
        }
        if OIDC_CLIENT_SECRET:
            body["client_secret"] = OIDC_CLIENT_SECRET
        async with httpx.AsyncClient(timeout=OIDC_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
            token_response = await client.post(
                str(discovery["token_endpoint"]),
                data=body,
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            tokens = token_response.json()
        id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
        if not isinstance(id_token, str):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC response is missing an ID token")
        claims = await _validate_id_token(id_token, nonce=str(flow["nonce"]))
        target = "/?oidc=success&" + urlencode({"redirect": _safe_return_to(str(flow.get("return_to") or "/"))})
        response = RedirectResponse(target, status_code=302)
        _set_oidc_session(response, claims)
        response.delete_cookie(_FLOW_COOKIE, path="/", secure=OIDC_COOKIE_SECURE, samesite="lax")
        return response
    except Exception as exc:
        logger.warning("OIDC callback failed: error_type=%s", type(exc).__name__)
        response = RedirectResponse("/?oidc_error=login_failed", status_code=302)
        clear_oidc_cookies(response)
        return response


@router.post("/v1/auth/logout")
async def browser_logout(request: Request) -> JSONResponse:
    claims = browser_session_claims(request)
    if claims is not None:
        require_browser_session_csrf(request, claims)
    response = JSONResponse({"status": "success"})
    clear_browser_session_cookies(response)
    return response

def oidc_identity_metadata(*, include_admin_url: bool = False) -> dict[str, Any]:
    return {
        "enabled": oidc_is_configured(),
        "provider_name": OIDC_PROVIDER_NAME,
        "issuer": OIDC_ISSUER if oidc_is_configured() else "",
        "identity_admin_url": (
            OIDC_IDENTITY_ADMIN_URL
            if include_admin_url
            and (
                OIDC_IDENTITY_ADMIN_URL.startswith("https://")
                or (OIDC_ALLOW_INSECURE_HTTP and OIDC_IDENTITY_ADMIN_URL.startswith("http://"))
            )
            else ""
        ),
    }
