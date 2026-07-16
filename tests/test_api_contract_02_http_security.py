import json
import os

import numpy as np
from fastapi.testclient import TestClient

import app.config_hot_reload as config_hot_reload
from app import (
    portrait_auth,
    routes_predict,
    routes_vision,
    security,
)
from main import app


def v1_error_message(response) -> str:
    return response.json()["error"]["message"]


def v1_validation_issues(response) -> list[dict[str, object]]:
    return response.json()["error"]["details"]["issues"]


def test_api_docs_can_be_disabled_in_production(monkeypatch) -> None:
    from app import server

    monkeypatch.setattr(server, "ENABLE_API_DOCS", False)
    production_app = server.create_app()
    client = TestClient(production_app)

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/health").status_code == 200


def test_global_request_body_limit_rejects_oversized_content_length(
    monkeypatch,
) -> None:
    from app import server

    monkeypatch.setattr(server, "MAX_REQUEST_BODY_BYTES", 10)
    limited_app = server.create_app()
    client = TestClient(limited_app)

    response = client.post(
        "/predict", content=b"x" * 11, headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "请求体过大：最大 10 字节"


def test_http_exception_headers_survive_middleware(monkeypatch) -> None:
    from app import security

    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", "test-token")
    client = TestClient(app)

    response = client.get("/v1/models")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["X-Request-ID"]


def test_trusted_host_allowlist_rejects_untrusted_hosts(monkeypatch) -> None:
    from app import server

    monkeypatch.setattr(server, "TRUSTED_HOSTS", ["trusted.local"])
    secured_app = server.create_app()
    client = TestClient(secured_app)

    rejected = client.get("/health", headers={"host": "attacker.local"})
    allowed = client.get("/health", headers={"host": "trusted.local"})

    assert rejected.status_code == 400
    assert "Host 头无效" in rejected.text
    assert allowed.status_code == 200


def test_trusted_host_allowlist_hot_reload_updates_existing_app(
    monkeypatch, workspace_tmp_path
) -> None:
    from app import server

    env_path = workspace_tmp_path / ".env"
    env_path.write_text("TRUSTED_HOSTS=trusted.local\n", encoding="utf-8")
    original_env = os.environ.get("TRUSTED_HOSTS")
    original_server_hosts = list(server.TRUSTED_HOSTS)
    monkeypatch.setattr(config_hot_reload, "ENV_PATH", env_path)
    monkeypatch.setattr(
        config_hot_reload, "audit_config_reload", lambda source, result: None
    )

    server.TRUSTED_HOSTS = ["*"]
    client = TestClient(server.create_app())
    before_reload = client.get("/health", headers={"host": "attacker.local"})
    assert before_reload.status_code == 200

    try:
        result = config_hot_reload.reload_runtime_config(
            source="test-hosts", include_env=True
        )

        assert result["env_loaded"] is True
        assert server.TRUSTED_HOSTS == ["trusted.local"]
        rejected = client.get("/health", headers={"host": "attacker.local"})
        allowed = client.get("/health", headers={"host": "trusted.local"})
        assert rejected.status_code == 400
        assert "Host 头无效" in rejected.text
        assert allowed.status_code == 200
    finally:
        if original_env is None:
            monkeypatch.delenv("TRUSTED_HOSTS", raising=False)
        else:
            monkeypatch.setenv("TRUSTED_HOSTS", original_env)
        config_hot_reload.reload_settings_modules()
        server.TRUSTED_HOSTS = original_server_hosts


def test_request_id_is_normalized_and_reused_between_body_and_header() -> None:
    client = TestClient(app)

    accepted = client.get(
        "/v1/admin/status", headers={"x-request-id": "req-123.trace_A"}
    )
    rejected = client.get(
        "/v1/admin/status", headers={"x-request-id": "bad request-id"}
    )
    too_long = client.get("/v1/admin/status", headers={"x-request-id": "r" * 129})

    assert accepted.status_code == 200
    assert accepted.headers["X-Request-ID"] == "req-123.trace_A"
    assert accepted.json()["request_id"] == "req-123.trace_A"

    assert rejected.status_code == 200
    assert rejected.headers["X-Request-ID"] != "bad request-id"
    assert rejected.headers["X-Request-ID"] == rejected.json()["request_id"]
    assert len(rejected.headers["X-Request-ID"]) == 36

    assert too_long.status_code == 200
    assert too_long.headers["X-Request-ID"] != "r" * 129
    assert too_long.headers["X-Request-ID"] == too_long.json()["request_id"]
    assert len(too_long.headers["X-Request-ID"]) == 36


def test_json_logs_include_context_fields() -> None:
    import logging

    from app.observability import JsonLogFormatter, reset_log_context, set_log_context

    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    tokens = set_log_context(
        request_id="req-log",
        tenant_id="tenant-log",
        traceparent="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
    )
    try:
        payload = json.loads(JsonLogFormatter().format(record))
    finally:
        reset_log_context(tokens)

    assert payload["request_id"] == "req-log"
    assert payload["tenant_id"] == "tenant-log"
    assert payload["traceparent"].startswith("00-")


def test_validation_errors_do_not_echo_input_payloads() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/streams",
        json={
            "stream_url": "http://example.com/live",
            "unexpected": {"token": "secret-token-value", "note": "x" * 200},
        },
    )

    assert response.status_code == 422
    body = response.text
    assert "secret-token-value" not in body
    assert "unexpected" not in body
    issue = v1_validation_issues(response)[0]
    assert "input" not in issue
    assert "ctx" not in issue
    assert issue["type"] == "extra_forbidden"
    assert issue["loc"] == ["body", "extra_field"]


def test_legacy_predict_rejects_unknown_body_fields() -> None:
    client = TestClient(app)

    response = client.post(
        "/predict",
        json={
            "project_name": "portrait_hub",
            "model_name": "yolov8n.onnx",
            "tensor_data": [0.0],
            "unexpected": {"secret": "hidden-value"},
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"][0]["type"] == "extra_forbidden"
    assert payload["detail"][0]["loc"] == ["body", "extra_field"]
    assert "hidden-value" not in response.text
    assert "unexpected" not in response.text


def test_legacy_warmup_rejects_nested_unknown_body_fields() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/admin/models/warmup",
        json={
            "models": [
                {
                    "project_name": "portrait_hub",
                    "model_name": "yolov8n.onnx",
                    "unexpected": "bad-field",
                }
            ]
        },
    )

    assert response.status_code == 422
    issue = v1_validation_issues(response)[0]
    assert issue["type"] == "extra_forbidden"
    assert issue["loc"] == ["body", "models", 0, "extra_field"]
    assert "bad-field" not in response.text
    assert "unexpected" not in response.text


def test_rollout_weighted_rejects_nested_unknown_body_fields() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/admin/models/rollout/aliases/weighted",
        json={
            "alias_name": "detector_default",
            "targets": [
                {
                    "target_model_id": "portrait_hub/yolov8n.onnx",
                    "weight": 100,
                    "unexpected": "bad-field",
                }
            ],
        },
    )

    assert response.status_code == 422
    issue = v1_validation_issues(response)[0]
    assert issue["type"] == "extra_forbidden"
    assert issue["loc"] == ["body", "targets", 0, "extra_field"]
    assert "bad-field" not in response.text
    assert "unexpected" not in response.text


def test_unhandled_errors_return_redacted_traceable_json() -> None:
    from app import server

    error_app = server.create_app()

    @error_app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal stack detail")

    client = TestClient(error_app, raise_server_exceptions=False)

    response = client.get("/boom", headers={"x-request-id": "req-500"})

    assert response.status_code == 500
    assert response.json() == {"detail": "内部服务器错误", "request_id": "req-500"}
    assert "secret internal stack detail" not in response.text
    assert response.headers["X-Request-ID"] == "req-500"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_legacy_predict_runtime_errors_are_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    client = TestClient(app, raise_server_exceptions=False)

    async def fail_load(*args, **kwargs):
        raise RuntimeError("secret backend token leaked from runtime")

    monkeypatch.setattr(
        routes_predict, "get_model_path", lambda project, model: "unused.onnx"
    )
    monkeypatch.setattr(routes_predict, "get_or_load_model", fail_load)

    response = client.post(
        "/predict",
        headers={"x-request-id": "req-runtime-redaction"},
        json={
            "project_name": "portrait_hub",
            "model_name": "yolov8n.onnx",
            "tensor_data": [0.0],
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "message": "推理运行时错误",
            "request_id": "req-runtime-redaction",
        }
    }
    assert response.headers["X-Request-ID"] == "req-runtime-redaction"
    assert "secret backend token" not in response.text
    assert "RuntimeError" in caplog.text
    assert "secret backend token" not in caplog.text
    assert "Traceback" not in caplog.text


def test_v1_vision_reid_vectors_are_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    client = TestClient(app, raise_server_exceptions=False)

    async def fake_get_or_load_model(*args, **kwargs):
        return {"model_hash": "test-hash"}, False, 0.0

    async def fake_load_images(files):
        image = type("ImageStub", (), {"width": 8, "height": 8})()
        return [image], ["secret-person-name.png"], 0.0

    async def fake_infer_reid_images(bundle, key, images):
        return (
            np.asarray([[0.1, 0.2]], dtype=np.float32),
            {
                "embedding_dim": 2,
                "timing": {
                    "preprocess_seconds": 0.0,
                    "queue_seconds": 0.0,
                    "inference_seconds": 0.0,
                    "postprocess_seconds": 0.0,
                },
                "inference_mode": "test",
                "input_shape": [1, 3, 16, 16],
                "output_shapes": [[1, 2]],
            },
        )

    async def fake_touch_model(*args, **kwargs):
        return None

    monkeypatch.setattr(
        routes_vision, "get_model_path", lambda project, model: "unused.onnx"
    )
    monkeypatch.setattr(routes_vision, "get_or_load_model", fake_get_or_load_model)
    monkeypatch.setattr(routes_vision, "load_images", fake_load_images)
    monkeypatch.setattr(routes_vision, "infer_reid_images", fake_infer_reid_images)
    monkeypatch.setattr(routes_vision, "touch_model", fake_touch_model)
    monkeypatch.setattr(
        routes_vision, "model_package_info", lambda *args: {"type": "reid"}
    )

    default_response = client.post(
        "/v1/vision/infer",
        data={"model_id": "person_reid_default"},
        files={"files": ("secret-person-name.png", b"fake", "image/png")},
    )
    explicit_response = client.post(
        "/v1/vision/infer",
        data={"model_id": "person_reid_default", "include_vectors": "true"},
        files={"files": ("secret-person-name.png", b"fake", "image/png")},
    )

    assert default_response.status_code == 200
    assert explicit_response.status_code == 200
    default_item = default_response.json()["data"]["results"][0]
    explicit_item = explicit_response.json()["data"]["results"][0]
    assert default_item["embedding_dim"] == 2
    assert "embedding" not in default_item
    assert explicit_item["embedding"] == [0.1, 0.2]
    assert "filename" not in default_item
    assert "secret-person-name" not in default_response.text
    assert "secret-person-name" not in explicit_response.text


