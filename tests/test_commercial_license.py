from __future__ import annotations

import base64
import copy
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from app import portrait_commercial_license
from app.portrait_commercial_license import (
    CommercialLicenseError,
    canonical_license_bytes,
    require_license_allocation,
    verify_license_document,
)


def payload(now: datetime) -> dict:
    return {
        "schema_version": "1.0",
        "license_id": "license-test-1",
        "issuer": "portrait-hub-release",
        "customer_ref": "customer-opaque-1",
        "instance_id": "instance-a",
        "product_version": "1.0",
        "delivery_profile": "private_standard",
        "issued_at": (now - timedelta(days=2)).isoformat(),
        "starts_at": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "grace_period_seconds": 86400,
        "entitlements": [
            {
                "tenant_id": "tenant-a",
                "project_id": "default",
                "capabilities": ["face_detection", "tracking"],
                "models": ["face/champion"],
                "concurrency_limit": 8,
                "stream_limit": 2,
            }
        ],
    }


def signed_document(tmp_path, license_payload: dict) -> tuple[dict, object]:
    private_key = Ed25519PrivateKey.generate()
    public_path = tmp_path / "license-public.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    signature = private_key.sign(canonical_license_bytes(license_payload))
    return (
        {
            "license": license_payload,
            "signature": {"algorithm": "Ed25519", "key_id": "release-2026", "value": base64.b64encode(signature).decode()},
        },
        public_path,
    )


def test_signed_offline_license_verifies_scope_and_runtime_status(tmp_path) -> None:
    now = datetime.now(UTC)
    document, public_path = signed_document(tmp_path, payload(now))

    result = verify_license_document(document, public_path, expected_instance_id="instance-a", now=now)

    assert result["ok"] is True
    assert result["runtime_status"] == "active"
    assert result["entitlements"][0]["capabilities"] == ["face_detection", "tracking"]


def test_license_fails_closed_on_tamper_instance_mismatch_and_secret_fields(tmp_path) -> None:
    now = datetime.now(UTC)
    license_payload = payload(now)
    document, public_path = signed_document(tmp_path, license_payload)
    tampered = copy.deepcopy(document)
    tampered["license"]["product_version"] = "9.9"

    with pytest.raises(CommercialLicenseError, match="signature verification failed"):
        verify_license_document(tampered, public_path, expected_instance_id="instance-a", now=now)
    with pytest.raises(CommercialLicenseError, match="instance"):
        verify_license_document(document, public_path, expected_instance_id="instance-b", now=now)

    forbidden_payload = payload(now)
    forbidden_payload["private_key"] = "must-never-be-embedded"
    forbidden, forbidden_public_path = signed_document(tmp_path, forbidden_payload)
    with pytest.raises(CommercialLicenseError, match="forbidden"):
        verify_license_document(forbidden, forbidden_public_path, expected_instance_id="instance-a", now=now)


def test_license_expiry_and_grace_are_explicit(tmp_path) -> None:
    now = datetime.now(UTC)
    license_payload = payload(now)
    license_payload["issued_at"] = (now - timedelta(days=4)).isoformat()
    license_payload["starts_at"] = (now - timedelta(days=3)).isoformat()
    license_payload["expires_at"] = (now - timedelta(hours=1)).isoformat()
    license_payload["grace_period_seconds"] = 7200
    document, public_path = signed_document(tmp_path, license_payload)
    grace = verify_license_document(document, public_path, expected_instance_id="instance-a", now=now)
    assert grace["runtime_status"] == "grace"

    expired = verify_license_document(
        document,
        public_path,
        expected_instance_id="instance-a",
        now=now + timedelta(hours=2),
    )
    assert expired["ok"] is False
    assert expired["runtime_status"] == "expired"


def test_license_allocation_blocks_grace_and_resource_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    entitlement = {
        "tenant_id": "tenant-a",
        "project_id": "default",
        "project_limit": 1,
        "concurrency_limit": 2,
        "stream_limit": 1,
    }
    monkeypatch.setattr(portrait_commercial_license.settings, "COMMERCIAL_LICENSE_REQUIRED", True)
    monkeypatch.setattr(
        portrait_commercial_license,
        "load_and_verify_commercial_license",
        lambda: {"runtime_status": "grace", "entitlements": [entitlement]},
    )

    with pytest.raises(HTTPException) as grace_error:
        require_license_allocation("tenant-a", "default", "credential_create")
    assert grace_error.value.detail["code"] == "commercial_license_grace_restriction"

    monkeypatch.setattr(
        portrait_commercial_license,
        "load_and_verify_commercial_license",
        lambda: {"runtime_status": "active", "entitlements": [entitlement]},
    )
    with pytest.raises(HTTPException) as project_error:
        require_license_allocation("tenant-a", "default", "project_create", current_count=1)
    assert project_error.value.detail["code"] == "project_limit_exceeded"
    with pytest.raises(HTTPException) as stream_error:
        require_license_allocation("tenant-a", "default", "stream_create", current_count=1)
    assert stream_error.value.detail["code"] == "stream_limit_exceeded"
    assert require_license_allocation("tenant-a", "default", "credential_create") == entitlement
