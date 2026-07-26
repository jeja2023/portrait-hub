from __future__ import annotations

import base64
import binascii
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException, status

from app import settings

LICENSE_SCHEMA_VERSION = "1.0"
LICENSE_DELIVERY_PROFILES = {"private_standard", "private_ha"}
_FORBIDDEN_KEYS = {
    "access_token",
    "api_key",
    "password",
    "private_key",
    "secret",
    "secret_key",
    "token",
}
_CACHE_LOCK = threading.RLock()
_CACHE_KEY: tuple[str, int, int, str, int, int] | None = None
_CACHE_VALUE: dict[str, Any] | None = None


class CommercialLicenseError(ValueError):
    pass


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CommercialLicenseError(f"license.{field} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CommercialLicenseError(f"license.{field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None:
        raise CommercialLicenseError(f"license.{field} must be a timezone-aware timestamp")
    return parsed.astimezone(UTC)


def canonical_license_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reject_sensitive_keys(value: Any, path: str = "license") -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_keys(item, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        normalized = str(key).strip().lower()
        if normalized in _FORBIDDEN_KEYS or normalized.endswith("_private_key"):
            raise CommercialLicenseError(f"{path}.{key} is forbidden in a commercial license")
        _reject_sensitive_keys(item, f"{path}.{key}")


def _public_key(path: Path) -> Ed25519PublicKey:
    try:
        loaded = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise CommercialLicenseError("commercial license public key cannot be loaded") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise CommercialLicenseError("commercial license public key must be Ed25519")
    return loaded


def _validate_entitlements(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CommercialLicenseError("license.entitlements must contain at least one project entitlement")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise CommercialLicenseError(f"license.entitlements[{index}] must be an object")
        tenant_id = str(item.get("tenant_id") or "").strip()
        project_id = str(item.get("project_id") or "").strip()
        capabilities = sorted({str(entry).strip() for entry in item.get("capabilities") or [] if str(entry).strip()})
        models = sorted({str(entry).strip() for entry in item.get("models") or [] if str(entry).strip()})
        if not tenant_id or not project_id or not capabilities:
            raise CommercialLicenseError(
                f"license.entitlements[{index}] requires tenant_id, project_id and capabilities"
            )
        scope = (tenant_id, project_id)
        if scope in seen:
            raise CommercialLicenseError(f"license.entitlements[{index}] duplicates a project scope")
        seen.add(scope)
        normalized.append(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "capabilities": capabilities,
                "models": models,
                "project_limit": max(1, int(item.get("project_limit") or 1)),
                "concurrency_limit": max(1, int(item.get("concurrency_limit") or 1)),
                "stream_limit": max(0, int(item.get("stream_limit") or 0)),
                "support_level": str(item.get("support_level") or "standard")[:64],
            }
        )
    return normalized


def verify_license_document(
    document: dict[str, Any],
    public_key_path: Path,
    *,
    expected_instance_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise CommercialLicenseError("commercial license document must be an object")
    payload = document.get("license")
    signature = document.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, dict):
        raise CommercialLicenseError("commercial license requires license and signature objects")
    _reject_sensitive_keys(payload)
    if payload.get("schema_version") != LICENSE_SCHEMA_VERSION:
        raise CommercialLicenseError(f"license.schema_version must be {LICENSE_SCHEMA_VERSION}")
    for field in ("license_id", "issuer", "customer_ref", "instance_id", "product_version"):
        if not isinstance(payload.get(field), str) or not str(payload[field]).strip():
            raise CommercialLicenseError(f"license.{field} is required")
    delivery_profile = str(payload.get("delivery_profile") or "").strip().lower()
    if delivery_profile not in LICENSE_DELIVERY_PROFILES:
        raise CommercialLicenseError("license.delivery_profile must be private_standard or private_ha")
    if not expected_instance_id.strip():
        raise CommercialLicenseError("COMMERCIAL_LICENSE_INSTANCE_ID is required")
    if payload["instance_id"] != expected_instance_id:
        raise CommercialLicenseError("commercial license instance does not match this deployment")
    issued_at = _timestamp(payload.get("issued_at"), "issued_at")
    starts_at = _timestamp(payload.get("starts_at"), "starts_at")
    expires_at = _timestamp(payload.get("expires_at"), "expires_at")
    if not issued_at <= starts_at < expires_at:
        raise CommercialLicenseError("commercial license timestamps are not ordered")
    grace_period_seconds = int(payload.get("grace_period_seconds") or 0)
    if grace_period_seconds < 0 or grace_period_seconds > 31_536_000:
        raise CommercialLicenseError("license.grace_period_seconds must be within 0..31536000")
    entitlements = _validate_entitlements(payload.get("entitlements"))
    if signature.get("algorithm") != "Ed25519":
        raise CommercialLicenseError("commercial license signature algorithm must be Ed25519")
    try:
        signature_bytes = base64.b64decode(str(signature.get("value") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CommercialLicenseError("commercial license signature is not valid base64") from exc
    try:
        _public_key(public_key_path).verify(signature_bytes, canonical_license_bytes(payload))
    except InvalidSignature as exc:
        raise CommercialLicenseError("commercial license signature verification failed") from exc
    current = (now or datetime.now(UTC)).astimezone(UTC)
    grace_until = expires_at.timestamp() + grace_period_seconds
    if current < starts_at:
        runtime_status = "pending"
    elif current <= expires_at:
        runtime_status = "active"
    elif current.timestamp() <= grace_until:
        runtime_status = "grace"
    else:
        runtime_status = "expired"
    return {
        "ok": runtime_status in {"active", "grace"},
        "runtime_status": runtime_status,
        "license_id": str(payload["license_id"]),
        "issuer": str(payload["issuer"]),
        "customer_ref": str(payload["customer_ref"]),
        "instance_id": str(payload["instance_id"]),
        "product_version": str(payload["product_version"]),
        "delivery_profile": delivery_profile,
        "starts_at": starts_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "grace_until": datetime.fromtimestamp(grace_until, tz=UTC).isoformat(),
        "entitlements": entitlements,
        "key_id": str(signature.get("key_id") or ""),
    }


def load_and_verify_commercial_license(*, force: bool = False) -> dict[str, Any]:
    global _CACHE_KEY, _CACHE_VALUE
    license_path = settings.COMMERCIAL_LICENSE_PATH
    public_key_path = settings.COMMERCIAL_LICENSE_PUBLIC_KEY_PATH
    try:
        license_stat = license_path.stat()
        key_stat = public_key_path.stat()
    except OSError as exc:
        raise CommercialLicenseError("commercial license or public key is unavailable") from exc
    cache_key = (
        str(license_path.resolve()),
        license_stat.st_mtime_ns,
        license_stat.st_size,
        str(public_key_path.resolve()),
        key_stat.st_mtime_ns,
        key_stat.st_size,
    )
    with _CACHE_LOCK:
        if not force and cache_key == _CACHE_KEY and _CACHE_VALUE is not None:
            result = dict(_CACHE_VALUE)
            starts_at = _timestamp(result["starts_at"], "starts_at")
            expires_at = _timestamp(result["expires_at"], "expires_at")
            grace_until = _timestamp(result["grace_until"], "grace_until")
            current = datetime.now(UTC)
            if current < starts_at:
                result["runtime_status"] = "pending"
            elif current <= expires_at:
                result["runtime_status"] = "active"
            elif current <= grace_until:
                result["runtime_status"] = "grace"
            else:
                result["runtime_status"] = "expired"
            result["ok"] = result["runtime_status"] in {"active", "grace"}
            return result
        try:
            document = json.loads(license_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommercialLicenseError("commercial license cannot be read") from exc
        result = verify_license_document(
            document,
            public_key_path,
            expected_instance_id=settings.COMMERCIAL_LICENSE_INSTANCE_ID,
        )
        _CACHE_KEY = cache_key
        _CACHE_VALUE = dict(result)
        return result


def public_license_status() -> dict[str, Any]:
    if not settings.COMMERCIAL_LICENSE_REQUIRED:
        return {"required": False, "ok": True, "runtime_status": "not_required"}
    try:
        verified = load_and_verify_commercial_license()
    except CommercialLicenseError as exc:
        return {"required": True, "ok": False, "runtime_status": "invalid", "error": str(exc)}
    return {
        "required": True,
        "ok": verified["ok"],
        "runtime_status": verified["runtime_status"],
        "license_id": verified["license_id"],
        "issuer": verified["issuer"],
        "customer_ref": verified["customer_ref"],
        "product_version": verified["product_version"],
        "delivery_profile": verified["delivery_profile"],
        "starts_at": verified["starts_at"],
        "expires_at": verified["expires_at"],
        "grace_until": verified["grace_until"],
        "key_id": verified["key_id"],
        "entitlement_count": len(verified["entitlements"]),
    }


def _verified_license_or_http() -> dict[str, Any]:
    try:
        return load_and_verify_commercial_license()
    except CommercialLicenseError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "commercial_license_invalid", "message": str(exc)},
        ) from exc


def _license_entitlement(verified: dict[str, Any], tenant_id: str, project_id: str) -> dict[str, Any]:
    entitlement = next(
        (
            item
            for item in verified["entitlements"]
            if item["tenant_id"] == tenant_id and item["project_id"] == project_id
        ),
        None,
    )
    if entitlement is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "project_not_licensed", "message": "project is not present in the signed license"},
        )
    return cast(dict[str, Any], entitlement)


def require_license_allocation(
    tenant_id: str,
    project_id: str,
    operation: str,
    *,
    current_count: int | None = None,
) -> dict[str, Any] | None:
    """Authorize a new commercial allocation; grace permits existing work only."""
    if not settings.COMMERCIAL_LICENSE_REQUIRED:
        return None
    verified = _verified_license_or_http()
    runtime_status = str(verified.get("runtime_status") or "invalid")
    if runtime_status == "grace":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "commercial_license_grace_restriction",
                "message": "new projects, credentials and capacity are disabled during the license grace period",
            },
        )
    if runtime_status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "commercial_license_inactive", "message": "commercial license is not active"},
        )
    entitlement = _license_entitlement(verified, tenant_id, project_id)
    if operation == "project_create" and current_count is not None:
        if current_count >= int(entitlement["project_limit"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "project_limit_exceeded", "message": "signed license project limit is exhausted"},
            )
    if operation in {"stream_create", "stream_start"} and current_count is not None:
        if current_count >= int(entitlement["stream_limit"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "stream_limit_exceeded", "message": "signed license stream limit is exhausted"},
            )
    return entitlement


def require_license_capability(
    tenant_id: str,
    project_id: str,
    capability: str,
    *,
    model_id: str | None = None,
) -> dict[str, Any] | None:
    if not settings.COMMERCIAL_LICENSE_REQUIRED:
        return None
    verified = _verified_license_or_http()
    if verified["runtime_status"] not in {"active", "grace"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "commercial_license_inactive", "message": "commercial license is not active"},
        )
    entitlement = _license_entitlement(verified, tenant_id, project_id)
    if capability not in entitlement["capabilities"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "capability_not_licensed", "message": "capability is not licensed for this project"},
        )
    if model_id and entitlement["models"] and model_id not in entitlement["models"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "model_not_licensed", "message": "model is not licensed for this project"},
        )
    return entitlement


__all__ = [
    "CommercialLicenseError",
    "canonical_license_bytes",
    "load_and_verify_commercial_license",
    "public_license_status",
    "require_license_allocation",
    "require_license_capability",
    "verify_license_document",
]
