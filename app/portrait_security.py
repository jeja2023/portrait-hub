import json
import math
import re
from copy import deepcopy
from typing import Any

from fastapi import HTTPException, Request, status

from app.settings import (
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


def tenant_id_from_request(request: Request) -> str:
    raw_tenant_id = request.headers.get("x-tenant-id")
    if raw_tenant_id is None:
        if TENANT_HEADER_REQUIRED and request.url.path.startswith("/v1/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="x-tenant-id header is required",
            )
        raw_tenant_id = "default"
    tenant_id = raw_tenant_id.strip()
    if not TENANT_PATTERN.fullmatch(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="x-tenant-id must be 1-64 chars and contain only letters, digits, '_', '.', ':', '-'",
        )
    return tenant_id


def validate_person_id(person_id: str, field_name: str = "person_id") -> str:
    value = str(person_id).strip()
    if not PERSON_ID_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be 1-128 chars and contain only letters, digits, '_', '.', ':', '-'",
        )
    return value


def validate_resource_id(resource_id: str, field_name: str) -> str:
    value = str(resource_id).strip()
    if not RESOURCE_ID_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be 1-128 chars and contain only letters, digits, '_', '.', ':', '-'",
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a JSON object")

    key_count = 0

    def normalize(item: Any, depth: int, path: str) -> Any:
        nonlocal key_count
        if depth > max_depth:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} exceeds max depth {max_depth}")
        if isinstance(item, dict):
            output: dict[str, Any] = {}
            for raw_key, raw_value in item.items():
                key = str(raw_key).strip()
                if not key:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} contains an empty key")
                if len(key) > 128:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} key is too long")
                key_count += 1
                if key_count > max_keys:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} exceeds max key count {max_keys}")
                output[key] = normalize(raw_value, depth + 1, f"{path}.{key}" if path else key)
            return output
        if isinstance(item, list):
            return [normalize(child, depth + 1, path) for child in item]
        if isinstance(item, str):
            if len(item) > max_string_length:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{field_name} string value is too long at {path or '<root>'}",
                )
            return item
        if isinstance(item, bool) or item is None:
            return item
        if isinstance(item, (int, float)):
            if not math.isfinite(float(item)):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} contains a non-finite number")
            return item
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} contains unsupported value type")

    normalized = normalize(value, 1, "")
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} exceeds max size {max_bytes} bytes")
    return normalized
