import hmac
import json
import math
import re
from copy import deepcopy
from typing import Any

from fastapi import HTTPException, Request, status

from app.settings import (
    API_TOKEN,
    API_TOKEN_TENANT_ID,
    MAX_PUBLIC_METADATA_BYTES,
    MAX_PUBLIC_METADATA_DEPTH,
    MAX_PUBLIC_METADATA_KEYS,
    MAX_PUBLIC_METADATA_STRING_LENGTH,
    TENANT_HEADER_REQUIRED,
)

TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
PERSON_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
REDACTED = "<redacted>"


def is_sensitive_field(key: str) -> bool:
    lowered = key.lower()
    exact = {
        "authorization",
        "credential",
        "credentials",
        "file_name",
        "filename",
        "filenames",
        "jwt",
        "password",
        "secret",
        "stream_url",
        "token",
        "vector",
        "vectors",
        "x-api-key",
    }
    if lowered in exact:
        return True
    if lowered == "embedding":
        return True
    if lowered.startswith("embedding_") and lowered != "embedding_dim":
        return True
    if lowered.endswith("_embedding") or lowered.endswith("_vector"):
        return True
    return any(
        marker in lowered
        for marker in (
            "access_key",
            "api_key",
            "authorization",
            "ciphertext",
            "credential",
            "input_ref",
            "password",
            "private_key",
            "secret",
            "stream_url",
            "token",
        )
    )


def redact_sensitive_fields(value: Any, key: str = "") -> Any:
    if key and is_sensitive_field(key):
        return REDACTED
    if isinstance(value, dict):
        return {item_key: redact_sensitive_fields(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_fields(item) for item in value]
    return deepcopy(value)



def _tenant_id_from_claims(claims: dict[str, Any]) -> str | None:
    tenant_claim = claims.get("tenant_id", claims.get("tenant"))
    if isinstance(tenant_claim, str) and tenant_claim.strip():
        return tenant_claim.strip()
    tenants_claim = claims.get("tenants")
    if isinstance(tenants_claim, list):
        tenants = sorted({item.strip() for item in tenants_claim if isinstance(item, str) and item.strip()})
        if len(tenants) == 1:
            return tenants[0]
    return None


def _tenant_id_from_api_key(api_key: str | None) -> str | None:
    if not api_key or not api_key.strip():
        return None
    normalized = api_key.strip()
    if API_TOKEN and hmac.compare_digest(normalized, API_TOKEN):
        return API_TOKEN_TENANT_ID or None
    from app.portrait_access import application_key_matches_any_tenant

    application = application_key_matches_any_tenant(normalized)
    if application is None:
        return None
    tenant_id = application.get("tenant_id")
    return tenant_id.strip() if isinstance(tenant_id, str) and tenant_id.strip() else None


def _tenant_id_from_bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    if API_TOKEN and hmac.compare_digest(token, API_TOKEN):
        return API_TOKEN_TENANT_ID or None
    from app.portrait_auth import verify_hs256_jwt

    try:
        claims = verify_hs256_jwt(token)
    except HTTPException:
        return None
    return _tenant_id_from_claims(claims)


def inferred_tenant_id_from_request(request: Request) -> str | None:
    from app.oidc_auth import browser_session_claims

    session_claims = (
        browser_session_claims(request)
        if not request.headers.get("authorization") and not request.headers.get("x-api-key")
        else None
    )
    session_tenant = str(session_claims.get("tenant_id") or "").strip() if session_claims else ""
    return (
        session_tenant
        or _tenant_id_from_api_key(request.headers.get("x-api-key"))
        or _tenant_id_from_bearer(request.headers.get("authorization"))
    )


def tenant_id_from_request(request: Request) -> str:
    raw_tenant_id = request.headers.get("x-tenant-id")
    if raw_tenant_id is None:
        raw_tenant_id = inferred_tenant_id_from_request(request)
    if raw_tenant_id is None:
        if TENANT_HEADER_REQUIRED and request.url.path.startswith("/v1/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="缺少 x-tenant-id 请求头，或使用可唯一解析租户的 API Key/JWT",
            )
        raw_tenant_id = "default"
    tenant_id = raw_tenant_id.strip()
    if not TENANT_PATTERN.fullmatch(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="x-tenant-id 必须为 1-64 个字符，且只能包含字母、数字、'_'、'.'、':'、'-'",
        )
    state = getattr(request, "state", None)
    if state is not None:
        state.portrait_tenant_id = tenant_id
    return tenant_id


def validate_person_id(person_id: str, field_name: str = "person_id") -> str:
    value = str(person_id).strip()
    if not PERSON_ID_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} 必须为 1-128 个字符，且只能包含字母、数字、'_'、'.'、':'、'-'",
        )
    return value


def validate_resource_id(resource_id: str, field_name: str) -> str:
    value = str(resource_id).strip()
    if not RESOURCE_ID_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} 必须为 1-128 个字符，且只能包含字母、数字、'_'、'.'、':'、'-'",
        )
    return value


def validate_job_id(job_id: str) -> str:
    return validate_resource_id(job_id, "job_id")


def validate_stream_id(stream_id: str) -> str:
    return validate_resource_id(stream_id, "stream_id")


def normalize_public_metadata(
    value: dict[str, Any] | None,
    *,
    field_name: str = "metadata",
    max_bytes: int = MAX_PUBLIC_METADATA_BYTES,
    max_depth: int = MAX_PUBLIC_METADATA_DEPTH,
    max_keys: int = MAX_PUBLIC_METADATA_KEYS,
    max_string_length: int = MAX_PUBLIC_METADATA_STRING_LENGTH,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 必须是 JSON 对象")

    key_count = 0

    def normalize(item: Any, depth: int, path: str) -> Any:
        nonlocal key_count
        if depth > max_depth:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 超过最大深度 {max_depth}")
        if isinstance(item, dict):
            output: dict[str, Any] = {}
            for raw_key, raw_value in item.items():
                key = str(raw_key).strip()
                if not key:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 包含空键")
                if len(key) > 128:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 键过长")
                key_count += 1
                if key_count > max_keys:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 超过最大键数量 {max_keys}")
                output[key] = normalize(raw_value, depth + 1, f"{path}.{key}" if path else key)
            return output
        if isinstance(item, list):
            return [normalize(child, depth + 1, path) for child in item]
        if isinstance(item, str):
            if len(item) > max_string_length:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{field_name} 字符串值过长，位置：{path or '<root>'}",
                )
            return item
        if isinstance(item, bool) or item is None:
            return item
        if isinstance(item, (int, float)):
            if not math.isfinite(float(item)):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 包含非有限数值")
            return item
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 包含不支持的值类型")

    normalized = normalize(value, 1, "")
    if not isinstance(normalized, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 必须是 JSON 对象")
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 超过最大大小 {max_bytes} 字节")
    return normalized
