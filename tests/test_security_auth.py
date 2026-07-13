import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import model_package, portrait_auth, portrait_crypto, portrait_security, routes_health, runtime_sessions, security
from main import app


def encode_segment(payload: dict | bytes) -> str:
    data = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def hs256_token(payload: dict, secret: str = "test-secret", kid: str | None = None) -> str:
    header_payload = {"alg": "HS256", "typ": "JWT"}
    if kid is not None:
        header_payload["kid"] = kid
    header = encode_segment(header_payload)
    body = encode_segment(payload)
    signature = hmac.new(secret.encode("utf-8"), f"{header}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{body}.{encode_segment(signature)}"


def valid_jwt_payload(**overrides: object) -> dict:
    payload = {
        "iss": "portrait-hub",
        "aud": "portrait-hub-api",
        "roles": ["viewer"],
        "exp": int(time.time()) + 60,
    }
    payload.update(overrides)
    return payload


def test_encrypt_bytes_uses_randomized_authenticated_encryption(monkeypatch) -> None:
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY_ID", "active")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEYRING", "")
    monkeypatch.setattr(portrait_crypto, "REQUIRE_ENCRYPTION", True)

    first = portrait_crypto.encrypt_bytes(b"sensitive payload")
    second = portrait_crypto.encrypt_bytes(b"sensitive payload")

    assert first["encrypted"] is True
    assert first["algorithm"] == "aes-256-gcm"
    assert first["key_id"] == "active"
    assert first["data"] != second["data"]
    assert first["nonce"] != second["nonce"]
    assert portrait_crypto.decrypt_bytes(first) == b"sensitive payload"

    tampered = dict(first)
    tampered["data"] = base64.b64encode(b"tampered").decode("ascii")
    with pytest.raises(ValueError) as exc_info:
        portrait_crypto.decrypt_bytes(tampered)

    assert "加密载荷认证失败" in str(exc_info.value)


def test_decrypt_bytes_reads_legacy_xor_payload(monkeypatch) -> None:
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY_ID", "primary")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEYRING", "")
    monkeypatch.setattr(portrait_crypto, "REQUIRE_ENCRYPTION", True)
    key = portrait_crypto.derive_key()
    encrypted = portrait_crypto.xor_stream(b"legacy payload", key)
    legacy = {
        "encrypted": True,
        "algorithm": "hmac-sha256-xor-stream",
        "digest": hmac.new(key, encrypted, hashlib.sha256).hexdigest(),
        "data": base64.b64encode(encrypted).decode("ascii"),
    }

    assert portrait_crypto.decrypt_bytes(legacy) == b"legacy payload"


def test_decrypt_bytes_reads_rotated_keyring_payload(monkeypatch) -> None:
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY", "new-encryption-key")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY_ID", "v2")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEYRING", "v1=old-encryption-key")
    monkeypatch.setattr(portrait_crypto, "REQUIRE_ENCRYPTION", True)

    nonce = b"1" * portrait_crypto.AES_GCM_NONCE_BYTES
    old_key = portrait_crypto.derive_key("old-encryption-key")
    encrypted = portrait_crypto.AESGCM(old_key).encrypt(nonce, b"rotated payload", None)
    old_payload = {
        "encrypted": True,
        "algorithm": "aes-256-gcm",
        "key_id": "v1",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "data": base64.b64encode(encrypted).decode("ascii"),
    }
    legacy_without_key_id = dict(old_payload)
    legacy_without_key_id.pop("key_id")
    new_payload = portrait_crypto.encrypt_bytes(b"new payload")

    assert new_payload["key_id"] == "v2"
    assert portrait_crypto.decrypt_bytes(old_payload) == b"rotated payload"
    assert portrait_crypto.decrypt_bytes(legacy_without_key_id) == b"rotated payload"
    assert portrait_crypto.decrypt_bytes(new_payload) == b"new payload"


def test_encrypt_bytes_fails_closed_when_required_without_key(monkeypatch) -> None:
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY", "")
    monkeypatch.setattr(portrait_crypto, "REQUIRE_ENCRYPTION", True)

    with pytest.raises(RuntimeError) as exc_info:
        portrait_crypto.encrypt_bytes(b"sensitive payload")

    assert "ENCRYPTION_KEY 为必填项" in str(exc_info.value)


@pytest.mark.asyncio
async def test_require_api_token_rejects_unsigned_jwt_shape_when_rbac_is_enabled(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")

    with pytest.raises(HTTPException) as exc_info:
        await security.require_api_token(authorization="Bearer header.payload.signature")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_token_fails_closed_when_rbac_has_no_credentials(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)

    with pytest.raises(HTTPException) as exc_info:
        await security.require_api_token(authorization=None, x_api_key=None)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_token_fails_closed_when_auth_is_required_without_backend(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)

    with pytest.raises(HTTPException) as exc_info:
        await security.require_api_token(authorization=None, x_api_key=None)

    assert exc_info.value.status_code == 401
    assert "no credential backend" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_require_api_token_accepts_api_key_when_auth_is_required(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", "test-token")

    await security.require_api_token(authorization=None, x_api_key="test-token")


@pytest.mark.asyncio
async def test_require_api_token_accepts_signed_jwt_when_rbac_is_enabled(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload())

    await security.require_api_token(authorization=f"Bearer {token}")


@pytest.mark.asyncio
async def test_require_api_token_rejects_jwt_for_wrong_tenant(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    monkeypatch.setattr(portrait_auth, "JWT_REQUIRE_TENANT", True)
    token = hs256_token(valid_jwt_payload(tenant_id="tenant-a"))

    with pytest.raises(HTTPException) as exc_info:
        await security.require_api_token(authorization=f"Bearer {token}", x_tenant_id="tenant-b")

    assert exc_info.value.status_code == 403
    assert "租户" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_permission_dependency_accepts_tenant_list_claim(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    monkeypatch.setattr(portrait_auth, "JWT_REQUIRE_TENANT", True)
    token = hs256_token(valid_jwt_payload(roles=["viewer"], tenants=["tenant-a", "tenant-b"]))

    await portrait_auth.require_permission(
        "gallery:read",
        authorization=f"Bearer {token}",
        x_tenant_id="tenant-b",
    )


def test_v1_requires_tenant_header_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(portrait_security, "TENANT_HEADER_REQUIRED", True)
    client = TestClient(app)

    response = client.get("/v1/admin/status")

    assert response.status_code == 400
    assert "缺少 x-tenant-id 请求头" in response.json()["detail"]


def test_legacy_model_management_reads_are_rbac_protected(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=["viewer"]))
    client = TestClient(app)

    response = client.get("/models", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


def test_legacy_model_management_writes_require_models_write(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=["viewer"]))
    client = TestClient(app)

    response = client.post("/reload-config", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert "models:write" in response.json()["detail"]


def test_rollout_writes_require_models_write(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=["viewer"]))
    client = TestClient(app)

    response = client.post(
        "/rollout/aliases/switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"alias_name": "detector_default", "target_model_id": "portrait_hub/yolov8n.onnx"},
    )

    assert response.status_code == 403
    assert "models:write" in response.json()["detail"]


def test_legacy_predict_requires_infer_permission(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=[]))
    client = TestClient(app)

    response = client.post(
        "/predict",
        headers={"Authorization": f"Bearer {token}"},
        json={"project_name": "portrait_hub", "model_name": "yolov8n.onnx", "tensor_data": [0.0]},
    )

    assert response.status_code == 403
    assert "infer" in response.json()["detail"]


def test_viewer_role_cannot_run_inference_or_compare(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=["viewer"]))
    client = TestClient(app)

    infer = client.post(
        "/predict",
        headers={"Authorization": f"Bearer {token}"},
        json={"project_name": "portrait_hub", "model_name": "yolov8n.onnx", "tensor_data": [0.0]},
    )
    compare = client.post(
        "/v1/compare/persons",
        headers={"Authorization": f"Bearer {token}"},
        files=[
            ("image_a", ("a.png", b"not-an-image", "image/png")),
            ("image_b", ("b.png", b"not-an-image", "image/png")),
        ],
    )

    assert infer.status_code == 403
    assert "infer" in infer.json()["detail"]
    assert compare.status_code == 403
    assert "compare" in compare.json()["detail"]


def test_algorithm_role_can_run_inference(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=["algorithm"]))

    assert portrait_auth.has_permission(portrait_auth.roles_from_claims(portrait_auth.verify_hs256_jwt(token)), "infer")


def test_legacy_vision_requires_infer_permission(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=[]))
    client = TestClient(app)

    response = client.post(
        "/vision/infer",
        headers={"Authorization": f"Bearer {token}"},
        files={"files": ("frame.png", b"not-an-image", "image/png")},
    )

    assert response.status_code == 403
    assert "infer" in response.json()["detail"]


def test_debug_model_output_requires_models_write_permission(monkeypatch) -> None:
    from app import routes_debug

    monkeypatch.setattr(routes_debug, "DEBUG_ENDPOINTS_ENABLED", True)
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=["viewer"]))
    client = TestClient(app)

    response = client.post(
        "/debug/model-output",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("frame.png", b"not-an-image", "image/png")},
        data={"project_name": "portrait_hub", "model_name": "yolov8n.onnx"},
    )

    assert response.status_code == 403
    assert "models:write" in response.json()["detail"]


def test_ready_deep_requires_models_read_permission(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=[]))
    client = TestClient(app)

    response = client.get("/ready/deep", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert "models:read" in response.json()["detail"]


def test_ready_deep_redacts_model_readiness_errors(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    from app import routes_health

    monkeypatch.setattr(routes_health.ort, "get_available_providers", lambda: ["CUDAExecutionProvider"])
    monkeypatch.setattr(
        routes_health,
        "MODEL_CONFIGS",
        {"portrait_hub/yolov8n.onnx": {"type": "detection"}},
    )

    def fail_model_path(project_name, model_name):
        raise RuntimeError("secret models path token=hidden")

    monkeypatch.setattr(routes_health, "get_model_path", fail_model_path)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/ready/deep")

    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["status"] == "not_ready"
    assert payload["checks"][0]["error"] == "模型就绪检查失败"
    assert "secret" not in response.text
    assert "token=hidden" not in response.text
    assert "models" not in response.text
    assert "path" not in payload["checks"][0]
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text
    assert "token=hidden" not in caplog.text


def test_metrics_requires_metrics_read_permission(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    viewer = hs256_token(valid_jwt_payload(roles=["viewer"]))
    operator = hs256_token(valid_jwt_payload(roles=["operator"]))
    client = TestClient(app)

    denied = client.get("/metrics", headers={"Authorization": f"Bearer {viewer}"})
    allowed = client.get("/metrics", headers={"Authorization": f"Bearer {operator}"})

    assert denied.status_code == 403
    assert "metrics:read" in denied.json()["detail"]
    assert allowed.status_code == 200
    assert "gpu_worker_model_config_info" in allowed.text


def test_admin_namespace_uses_dedicated_rbac_permissions(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    client = TestClient(app)
    viewer = hs256_token(valid_jwt_payload(roles=["viewer"]))
    algorithm = hs256_token(valid_jwt_payload(roles=["algorithm"]))
    operator = hs256_token(valid_jwt_payload(roles=["operator"]))
    auditor = hs256_token(valid_jwt_payload(roles=["auditor"]))

    viewer_export = client.get("/v1/admin/export", headers={"Authorization": f"Bearer {viewer}"})
    algorithm_cleanup = client.post(
        "/v1/admin/retention/cleanup",
        headers={"Authorization": f"Bearer {algorithm}"},
        json={"retention_days": 0},
    )
    operator_status = client.get("/v1/admin/status", headers={"Authorization": f"Bearer {operator}"})
    operator_console = client.get("/console", headers={"Authorization": f"Bearer {operator}"})
    viewer_console = client.get("/console", headers={"Authorization": f"Bearer {viewer}"})
    auditor_export = client.get("/v1/admin/export", headers={"Authorization": f"Bearer {auditor}"})
    auditor_cleanup = client.post(
        "/v1/admin/retention/cleanup",
        headers={"Authorization": f"Bearer {auditor}"},
        json={"retention_days": 0},
    )

    assert viewer_export.status_code == 403
    assert "admin:export" in viewer_export.json()["detail"]
    assert algorithm_cleanup.status_code == 403
    assert "admin:retention" in algorithm_cleanup.json()["detail"]
    assert operator_status.status_code == 200
    assert operator_console.status_code == 200
    assert viewer_console.status_code == 403
    assert "admin:status" in viewer_console.json()["detail"]
    assert auditor_export.status_code == 200
    assert auditor_cleanup.status_code == 403
    assert "admin:retention" in auditor_cleanup.json()["detail"]


def test_public_health_does_not_require_tenant_header_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(portrait_security, "TENANT_HEADER_REQUIRED", True)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200


def test_public_health_and_ready_do_not_expose_runtime_details(monkeypatch) -> None:
    monkeypatch.setattr("app.routes_health.ort.get_available_providers", lambda: [])
    client = TestClient(app)

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert "models_root" not in health.json()
    assert "loaded_models" not in health.json()
    assert "available_providers" not in health.json()
    assert ready.status_code == 503
    assert ready.json()["detail"] == {"status": "not_ready"}


def test_public_ready_accepts_cpu_fallback_without_exposing_runtime_details(monkeypatch) -> None:
    monkeypatch.setattr(routes_health.ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(runtime_sessions, "CPU_FALLBACK_ENABLED", True)
    client = TestClient(app)

    ready = client.get("/ready")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_verify_hs256_jwt_rejects_expired_token(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(exp=int(time.time()) - 1))

    with pytest.raises(HTTPException) as exc_info:
        portrait_auth.verify_hs256_jwt(token)

    assert exc_info.value.status_code == 401
    assert "expired" in str(exc_info.value.detail)


def test_verify_hs256_jwt_accepts_rotated_keyring_secret_with_kid(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "new-secret")
    monkeypatch.setattr(portrait_auth, "JWT_SECRET_ID", "v2")
    monkeypatch.setattr(portrait_auth, "JWT_SECRET_KEYRING", "v1=old-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    old_token = hs256_token(valid_jwt_payload(roles=["viewer"]), secret="old-secret", kid="v1")
    new_token = hs256_token(valid_jwt_payload(roles=["operator"]), secret="new-secret", kid="v2")

    assert portrait_auth.verify_hs256_jwt(old_token)["roles"] == ["viewer"]
    assert portrait_auth.verify_hs256_jwt(new_token)["roles"] == ["operator"]


def test_verify_hs256_jwt_accepts_legacy_keyring_token_without_kid(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "new-secret")
    monkeypatch.setattr(portrait_auth, "JWT_SECRET_ID", "v2")
    monkeypatch.setattr(portrait_auth, "JWT_SECRET_KEYRING", "v1=old-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(roles=["viewer"]), secret="old-secret")

    assert portrait_auth.verify_hs256_jwt(token)["roles"] == ["viewer"]


def test_verify_hs256_jwt_rejects_unknown_kid(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "new-secret")
    monkeypatch.setattr(portrait_auth, "JWT_SECRET_ID", "v2")
    monkeypatch.setattr(portrait_auth, "JWT_SECRET_KEYRING", "v1=old-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(), secret="old-secret", kid="missing")

    with pytest.raises(HTTPException) as exc_info:
        portrait_auth.verify_hs256_jwt(token)

    assert exc_info.value.status_code == 401
    assert "signature" in str(exc_info.value.detail)


def test_verify_hs256_jwt_rejects_blank_kid(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "new-secret")
    monkeypatch.setattr(portrait_auth, "JWT_SECRET_ID", "v2")
    monkeypatch.setattr(portrait_auth, "JWT_SECRET_KEYRING", "v1=old-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(), secret="new-secret", kid="")

    with pytest.raises(HTTPException) as exc_info:
        portrait_auth.verify_hs256_jwt(token)

    assert exc_info.value.status_code == 401
    assert "key id" in str(exc_info.value.detail)


def test_verify_hs256_jwt_rejects_missing_expiration_by_default(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    payload = valid_jwt_payload()
    payload.pop("exp")
    token = hs256_token(payload)

    with pytest.raises(HTTPException) as exc_info:
        portrait_auth.verify_hs256_jwt(token)

    assert exc_info.value.status_code == 401
    assert "expiration" in str(exc_info.value.detail)


def test_verify_hs256_jwt_rejects_missing_issuer_by_default(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    payload = valid_jwt_payload()
    payload.pop("iss")
    token = hs256_token(payload)

    with pytest.raises(HTTPException) as exc_info:
        portrait_auth.verify_hs256_jwt(token)

    assert exc_info.value.status_code == 401
    assert "issuer" in str(exc_info.value.detail)


def test_verify_hs256_jwt_rejects_missing_audience_by_default(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    payload = valid_jwt_payload()
    payload.pop("aud")
    token = hs256_token(payload)

    with pytest.raises(HTTPException) as exc_info:
        portrait_auth.verify_hs256_jwt(token)

    assert exc_info.value.status_code == 401
    assert "audience" in str(exc_info.value.detail)


def test_verify_hs256_jwt_rejects_wrong_audience(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(aud="other-service"))

    with pytest.raises(HTTPException) as exc_info:
        portrait_auth.verify_hs256_jwt(token)

    assert exc_info.value.status_code == 401
    assert "audience" in str(exc_info.value.detail)


def test_verify_hs256_jwt_accepts_audience_list(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(aud=["other-service", "portrait-hub-api"]))

    claims = portrait_auth.verify_hs256_jwt(token)

    assert claims["aud"] == ["other-service", "portrait-hub-api"]


def test_verify_hs256_jwt_rejects_invalid_time_claim_type(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload(exp=True))

    with pytest.raises(HTTPException) as exc_info:
        portrait_auth.verify_hs256_jwt(token)

    assert exc_info.value.status_code == 401
    assert "time claim" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_permission_dependency_still_checks_roles(monkeypatch) -> None:
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = hs256_token(valid_jwt_payload())

    with pytest.raises(HTTPException) as exc_info:
        await portrait_auth.require_permission("models:write", authorization=f"Bearer {token}")

    assert exc_info.value.status_code == 403


def test_get_model_path_uses_configured_artifact_path(monkeypatch, workspace_tmp_path: Path) -> None:
    models_root = workspace_tmp_path / "models"
    models_root.mkdir(parents=True)
    model_path = models_root / "detector.onnx"
    model_path.write_bytes(b"fake onnx")
    monkeypatch.setattr(model_package, "MODELS_ROOT", models_root.resolve())
    monkeypatch.setattr(
        model_package,
        "model_config",
        lambda key: {"artifact": {"path": "detector.onnx"}},
    )

    assert model_package.get_model_path("portrait_hub", "detector.onnx") == model_path.resolve()


def test_get_model_path_rejects_escaping_artifact_path(monkeypatch, workspace_tmp_path: Path) -> None:
    models_root = workspace_tmp_path / "models"
    models_root.mkdir(parents=True)
    monkeypatch.setattr(model_package, "MODELS_ROOT", models_root.resolve())
    monkeypatch.setattr(
        model_package,
        "model_config",
        lambda key: {"artifact": {"path": "../detector.onnx"}},
    )

    with pytest.raises(HTTPException) as exc_info:
        model_package.get_model_path("portrait_hub", "detector.onnx")

    assert exc_info.value.status_code == 400


def test_get_model_path_missing_model_does_not_echo_model_id(monkeypatch, workspace_tmp_path: Path) -> None:
    models_root = workspace_tmp_path / "models"
    models_root.mkdir(parents=True)
    monkeypatch.setattr(model_package, "MODELS_ROOT", models_root.resolve())
    monkeypatch.setattr(model_package, "model_config", lambda key: {})

    with pytest.raises(HTTPException) as exc_info:
        model_package.get_model_path("secret_project", "secret-model.onnx")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "模型构件不存在"
    assert "secret_project" not in str(exc_info.value.detail)
    assert "secret-model" not in str(exc_info.value.detail)
