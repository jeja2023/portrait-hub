import os

import pytest
import numpy as np
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import app.config_hot_reload as config_hot_reload
import app.rate_limit as rate_limit
import app.settings as settings
from app import (
    portrait_auth,
    portrait_model_capabilities,
    routes_debug,
    routes_model_lifecycle,
    routes_model_query,
    routes_person_embeddings,
    routes_portrait_models,
    routes_predict,
    security,
)
from app.runtime_state import MODEL_REGISTRY
from main import app


def test_env_hot_reload_updates_loaded_settings_modules(monkeypatch, workspace_tmp_path) -> None:
    env_path = workspace_tmp_path / ".env"
    env_path.write_text("RATE_LIMIT_PER_MINUTE=37\n", encoding="utf-8")
    original_env = settings.RATE_LIMIT_PER_MINUTE
    original_rate_limit = rate_limit.RATE_LIMIT_PER_MINUTE
    monkeypatch.setattr(config_hot_reload, "ENV_PATH", env_path)
    monkeypatch.setattr(config_hot_reload, "audit_config_reload", lambda source, result: None)

    try:
        result = config_hot_reload.reload_runtime_config(source="test-env", include_env=True)

        assert result["env_loaded"] is True
        assert result["env_changed_key_count"] == 1
        assert settings.RATE_LIMIT_PER_MINUTE == 37
        assert rate_limit.RATE_LIMIT_PER_MINUTE == 37
    finally:
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", str(original_env))
        config_hot_reload.reload_settings_modules()
        rate_limit.RATE_LIMIT_PER_MINUTE = original_rate_limit


def test_env_hot_reload_updates_model_capabilities(monkeypatch, workspace_tmp_path) -> None:
    capabilities_path = workspace_tmp_path / "model-capabilities.yml"
    capabilities_path.write_text(
        """
capabilities:
  face_embedding:
    status: ready
    model_id: portrait_hub/arcface_r100.onnx
    adapter: arcface
    fallback_model_id: portrait_hub/image_fingerprint_v1
""",
        encoding="utf-8",
    )
    env_path = workspace_tmp_path / ".env"
    env_path.write_text(
        f"MODEL_CAPABILITIES_PATH={capabilities_path}\n"
        "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES=false\n",
        encoding="utf-8",
    )
    original_env = os.environ.get("MODEL_CAPABILITIES_PATH")
    original_require_env = os.environ.get("PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES")
    original_capabilities = dict(portrait_model_capabilities.MODEL_CAPABILITIES)
    monkeypatch.setattr(config_hot_reload, "ENV_PATH", env_path)
    monkeypatch.setattr(config_hot_reload, "audit_config_reload", lambda source, result: None)

    try:
        result = config_hot_reload.reload_runtime_config(source="test-capabilities", include_env=True)

        assert result["model_capabilities_reloaded"] is True
        assert portrait_model_capabilities.MODEL_CAPABILITIES["face_embedding"]["model_id"] == "portrait_hub/arcface_r100.onnx"
        assert portrait_model_capabilities.MODEL_CAPABILITIES["face_embedding"]["embedding_dim"] == 512
    finally:
        if original_env is None:
            os.environ.pop("MODEL_CAPABILITIES_PATH", None)
        else:
            os.environ["MODEL_CAPABILITIES_PATH"] = original_env
        if original_require_env is None:
            os.environ.pop("PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES", None)
        else:
            os.environ["PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES"] = original_require_env
        config_hot_reload.reload_settings_modules()
        portrait_model_capabilities.MODEL_CAPABILITIES.clear()
        portrait_model_capabilities.MODEL_CAPABILITIES.update(original_capabilities)


def test_production_capability_requirement_rejects_fallback(monkeypatch) -> None:
    monkeypatch.setattr(portrait_model_capabilities, "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES", True)

    with pytest.raises(RuntimeError, match="face_embedding"):
        portrait_model_capabilities.validate_required_production_capabilities(
            {
                "face_embedding": {
                    "status": "fallback",
                    "model_id": "portrait_hub/image_fingerprint_v1",
                    "fallback_model_id": "portrait_hub/image_fingerprint_v1",
                }
            }
        )


def test_openapi_keeps_core_routes() -> None:
    schema = app.openapi()
    paths = set(schema["paths"])

    required_paths = {
        "/health",
        "/ready",
        "/ready/deep",
        "/models",
        "/model-configs",
        "/model-package",
        "/predict",
        "/infer/persons",
        "/infer/person-embeddings",
        "/infer/person-tracks",
        "/infer/video/person-tracks",
        "/infer/stream/person-tracks",
        "/vision/infer",
        "/vision/batch-infer",
        "/debug/model-output",
        "/rollout/aliases",
        "/rollout/aliases/preview",
        "/rollout/aliases/switch",
        "/rollout/aliases/weighted",
        "/rollout/aliases/rollback",
        "/v1/infer/faces",
        "/v1/infer/persons",
        "/v1/infer/pose",
        "/v1/infer/appearance",
        "/v1/infer/gait",
        "/v1/compare/faces",
        "/v1/compare/persons",
        "/v1/compare/gait",
        "/v1/compare/batch",
        "/v1/fusion/compare",
        "/v1/gallery/enroll",
        "/v1/gallery/search",
        "/v1/gallery/search/batch",
        "/v1/gallery/reindex",
        "/v1/gallery/{person_id}",
        "/v1/jobs/video",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/result",
        "/v1/jobs/{job_id}/cancel",
        "/v1/streams",
        "/v1/streams/{stream_id}",
        "/v1/streams/{stream_id}/start",
        "/v1/streams/{stream_id}/stop",
        "/v1/streams/{stream_id}/status",
        "/v1/streams/{stream_id}/events",
        "/v1/models",
        "/v1/models/{model_id}",
        "/v1/models/{model_id}/load",
        "/v1/models/{model_id}/unload",
        "/v1/thresholds",
        "/v1/thresholds/{profile}",
        "/v1/admin/status",
        "/v1/admin/export",
        "/v1/admin/backup",
        "/v1/admin/retention/cleanup",
        "/console",
    }

    assert required_paths <= paths


def test_console_is_product_admin_shell_with_strict_inline_policy() -> None:
    client = TestClient(app)

    response = client.get("/console")

    assert response.status_code == 200
    body = response.text
    assert "影鉴 业务控制台" in body
    assert "启用 JavaScript" in body
    csp = response.headers["Content-Security-Policy"]
    assert "img-src 'self' data: blob:" in csp
    assert "script-src 'self' 'nonce-" in csp
    assert "style-src 'self' 'nonce-" in csp
    assert "'unsafe-inline'" not in csp
    assert 'style="' not in body
    assert "onclick" not in body


def test_console_assets_use_light_structured_response_panels() -> None:
    client = TestClient(app)

    js = client.get("/assets/console.js")
    css = client.get("/assets/console.css")

    assert js.status_code == 200
    assert css.status_code == 200
    js_body = js.text
    css_body = css.text
    assert "data-viewer" in js_body
    assert "查看完整数据（JSON）" in js_body
    assert "复制数据" in js_body
    assert 'class="json-view data-viewer"' in js_body
    assert '<pre id="dashboard-json"' not in js_body
    assert '<pre id="models-json"' not in js_body
    assert "--code" not in css_body
    assert "#111827" not in css_body
    assert "background: #fbfdff" in css_body
    assert "解析处理" in js_body
    assert "比对检索" in js_body
    assert "视频解析结果" in js_body
    assert "视频流解析" in js_body
    assert "人员库查询" not in js_body
    assert "智能解析" not in js_body
    assert "视频分析" not in js_body


def test_api_docs_can_be_disabled_in_production(monkeypatch) -> None:
    from app import server

    monkeypatch.setattr(server, "ENABLE_API_DOCS", False)
    production_app = server.create_app()
    client = TestClient(production_app)

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/health").status_code == 200


def test_global_request_body_limit_rejects_oversized_content_length(monkeypatch) -> None:
    from app import server

    monkeypatch.setattr(server, "MAX_REQUEST_BODY_BYTES", 10)
    limited_app = server.create_app()
    client = TestClient(limited_app)

    response = client.post("/predict", content=b"x" * 11, headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert response.json()["detail"] == "request body too large: max 10 bytes"


def test_http_exception_headers_survive_middleware(monkeypatch) -> None:
    from app import security

    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", "test-token")
    client = TestClient(app)

    response = client.get("/models")

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
    assert "Invalid host header" in rejected.text
    assert allowed.status_code == 200


def test_request_id_is_normalized_and_reused_between_body_and_header() -> None:
    client = TestClient(app)

    accepted = client.get("/v1/admin/status", headers={"x-request-id": "req-123.trace_A"})
    rejected = client.get("/v1/admin/status", headers={"x-request-id": "bad request-id"})
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
    from app.observability import JsonLogFormatter, reset_log_context, set_log_context
    import json
    import logging

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    tokens = set_log_context(request_id="req-log", tenant_id="tenant-log", traceparent="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01")
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
    payload = response.json()
    assert "secret-token-value" not in body
    assert "unexpected" not in body
    assert "input" not in payload["detail"][0]
    assert "ctx" not in payload["detail"][0]
    assert payload["detail"][0]["type"] == "extra_forbidden"
    assert payload["detail"][0]["loc"] == ["body", "extra_field"]


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
        "/warmup",
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
    payload = response.json()
    assert payload["detail"][0]["type"] == "extra_forbidden"
    assert payload["detail"][0]["loc"] == ["body", "models", 0, "extra_field"]
    assert "bad-field" not in response.text
    assert "unexpected" not in response.text


def test_rollout_weighted_rejects_nested_unknown_body_fields() -> None:
    client = TestClient(app)

    response = client.post(
        "/rollout/aliases/weighted",
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
    payload = response.json()
    assert payload["detail"][0]["type"] == "extra_forbidden"
    assert payload["detail"][0]["loc"] == ["body", "targets", 0, "extra_field"]
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
    assert response.json() == {"detail": "internal server error", "request_id": "req-500"}
    assert "secret internal stack detail" not in response.text
    assert response.headers["X-Request-ID"] == "req-500"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_legacy_predict_runtime_errors_are_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    client = TestClient(app, raise_server_exceptions=False)

    async def fail_load(*args, **kwargs):
        raise RuntimeError("secret backend token leaked from runtime")

    monkeypatch.setattr(routes_predict, "get_model_path", lambda project, model: "unused.onnx")
    monkeypatch.setattr(routes_predict, "get_or_load_model", fail_load)

    response = client.post(
        "/predict",
        headers={"x-request-id": "req-runtime-redaction"},
        json={"project_name": "portrait_hub", "model_name": "yolov8n.onnx", "tensor_data": [0.0]},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "message": "inference runtime error",
            "request_id": "req-runtime-redaction",
        }
    }
    assert response.headers["X-Request-ID"] == "req-runtime-redaction"
    assert "secret backend token" not in response.text
    assert "RuntimeError" in caplog.text
    assert "secret backend token" not in caplog.text
    assert "Traceback" not in caplog.text


def test_legacy_person_embeddings_vectors_are_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    client = TestClient(app, raise_server_exceptions=False)

    async def fake_get_or_load_model(*args, **kwargs):
        return {}, False, 0.0

    async def fake_load_images(files):
        return [object()], ["secret-person-name.png"], 0.0

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

    monkeypatch.setattr(routes_person_embeddings, "get_model_path", lambda project, model: "unused.onnx")
    monkeypatch.setattr(routes_person_embeddings, "get_or_load_model", fake_get_or_load_model)
    monkeypatch.setattr(routes_person_embeddings, "load_images", fake_load_images)
    monkeypatch.setattr(routes_person_embeddings, "infer_reid_images", fake_infer_reid_images)
    monkeypatch.setattr(routes_person_embeddings, "touch_model", fake_touch_model)

    default_response = client.post(
        "/infer/person-embeddings",
        files={"files": ("secret-person-name.png", b"fake", "image/png")},
    )
    explicit_response = client.post(
        "/infer/person-embeddings",
        data={"include_vectors": "true"},
        files={"files": ("secret-person-name.png", b"fake", "image/png")},
    )

    assert default_response.status_code == 200
    assert explicit_response.status_code == 200
    assert default_response.json()["items"][0]["embedding_dim"] == 2
    assert "embedding" not in default_response.json()["items"][0]
    assert explicit_response.json()["items"][0]["embedding"] == [0.1, 0.2]
    assert "filename" not in default_response.json()["items"][0]
    assert "secret-person-name" not in default_response.text
    assert "secret-person-name" not in explicit_response.text


def test_rollout_alias_preview_invalid_alias_returns_400(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/rollout/aliases/preview",
        params={"alias_name": "bad/alias", "traffic_key": "tenant-1"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid alias name"
    assert "bad/alias" not in response.text
    assert response.headers["X-Request-ID"]


def test_legacy_model_reference_errors_are_fixed_and_redacted() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    infer = client.post(
        "/infer/persons",
        files={"files": ("frame.png", b"not-an-image", "image/png")},
        data={"project_name": "secret/project", "model_name": "secret-model.onnx"},
    )
    info = client.get(
        "/model-info",
        params={"project_name": "secret/project", "model_name": "secret-model.onnx"},
    )

    for response in [infer, info]:
        assert response.status_code == 400
        assert response.json()["detail"] == "invalid model reference"
        assert "secret/project" not in response.text
        assert "secret-model" not in response.text


def test_rollout_alias_preview_missing_alias_does_not_echo_alias(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/rollout/aliases/preview",
        params={"alias_name": "secret_alias", "traffic_key": "tenant-1"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "alias not found"
    assert "secret_alias" not in response.text


@pytest.mark.asyncio
async def test_global_request_body_limit_counts_streamed_body_without_content_length(monkeypatch) -> None:
    from app import server

    monkeypatch.setattr(server, "MAX_REQUEST_BODY_BYTES", 10)
    messages = iter(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"678901", "more_body": False},
        ]
    )

    async def receive() -> dict[str, object]:
        return next(messages)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/predict",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        },
        receive,
    )
    limited_request = server.limit_request_body(request)

    assert await limited_request.receive() == {"type": "http.request", "body": b"12345", "more_body": True}
    with pytest.raises(HTTPException) as exc_info:
        await limited_request.receive()
    assert exc_info.value.status_code == 413


def test_health_endpoint_is_public() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert "available_providers" not in payload
    assert "loaded_models" not in payload
    assert "models_root" not in payload


def test_debug_model_output_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr(routes_debug, "DEBUG_ENDPOINTS_ENABLED", False)
    client = TestClient(app)

    response = client.post(
        "/debug/model-output",
        files={"file": ("frame.png", b"not-an-image", "image/png")},
        data={"project_name": "portrait_hub", "model_name": "yolov8n.onnx"},
    )

    assert response.status_code == 404


def test_debug_model_output_requires_auth_when_enabled_and_auth_required(monkeypatch) -> None:
    monkeypatch.setattr(routes_debug, "DEBUG_ENDPOINTS_ENABLED", True)
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    client = TestClient(app)

    response = client.post(
        "/debug/model-output",
        files={"file": ("frame.png", b"not-an-image", "image/png")},
        data={"project_name": "portrait_hub", "model_name": "yolov8n.onnx"},
    )

    assert response.status_code == 401


def test_legacy_model_lifecycle_writes_are_audited(monkeypatch) -> None:
    client = TestClient(app)
    events = []

    async def fake_unload_model_by_key(key):
        return True

    monkeypatch.setattr(routes_model_lifecycle, "unload_model_by_key", fake_unload_model_by_key)
    monkeypatch.setattr(routes_model_lifecycle, "audit_event", lambda event, **fields: events.append((event, fields)))

    response = client.post("/unload", json={"project_name": "portrait_hub", "model_name": "yolov8n.onnx"})

    assert response.status_code == 200
    assert events[0][0] == "model_unload"
    assert events[0][1]["model"] == "portrait_hub/yolov8n.onnx"


def test_legacy_reload_config_is_audited(monkeypatch) -> None:
    client = TestClient(app)
    events = []

    monkeypatch.setattr(routes_model_query, "reload_model_config_state", lambda: ({"m": {}}, {"a": {}}))
    monkeypatch.setattr(routes_model_query, "audit_event", lambda event, **fields: events.append((event, fields)))

    response = client.post("/reload-config")

    assert response.status_code == 200
    assert events[0][0] == "model_config_reloaded"
    assert events[0][1]["model_count"] == 1


def test_model_management_responses_do_not_expose_filesystem_paths(monkeypatch) -> None:
    class FakeTensor:
        name = "input"
        type = "tensor(float)"
        shape = [1, 3, 8, 8]

    class FakeSession:
        def get_providers(self):
            return ["CUDAExecutionProvider"]

        def get_inputs(self):
            return [FakeTensor()]

        def get_outputs(self):
            return [FakeTensor()]

    secret_config = {
        "task": "detection",
        "type": "yolo",
        "runtime": "onnxruntime",
        "artifact": {
            "path": "E:/secret-models/tenant-a/secret.onnx",
            "labels": "secret.labels.txt",
            "model_card": "secret-card.yml",
            "sha256": "abc123",
        },
    }
    fake_bundle = {
        "session": FakeSession(),
        "path": "E:/secret-models/tenant-a/secret.onnx",
        "model_hash": "abc123",
        "file_size": 123,
        "loaded_at": 1.0,
        "last_used_at": 2.0,
        "load_count": 1,
        "inference_count": 0,
        "max_concurrency": 1,
        "queue_timeout_seconds": 0,
    }
    fake_registry = {"portrait_hub/secret.onnx": fake_bundle}
    fake_configs = {"portrait_hub/secret.onnx": secret_config}
    fake_package = {
        "model": "portrait_hub/secret.onnx",
        "artifact": {
            "path_configured": True,
            "labels_configured": True,
            "model_card_configured": True,
            "sha256": "abc123",
            "sha256_match": True,
        },
    }

    async def fake_get_or_load_model(key, model_path):
        return fake_bundle, True, 0.01

    monkeypatch.setattr(routes_model_query, "MODEL_CONFIGS", fake_configs)
    monkeypatch.setattr(routes_model_query, "MODEL_ALIASES", {})
    monkeypatch.setattr(routes_model_query, "MODEL_REGISTRY", fake_registry)
    monkeypatch.setattr(routes_model_query, "model_config", lambda key: secret_config)
    monkeypatch.setattr(routes_model_query, "get_model_path", lambda project, model: "E:/secret-models/tenant-a/secret.onnx")
    monkeypatch.setattr(routes_model_query, "get_or_load_model", fake_get_or_load_model)
    monkeypatch.setattr(routes_model_query, "model_hash", lambda model_path: "abc123")
    monkeypatch.setattr(routes_model_query, "model_package_info", lambda key, model_path, digest: fake_package)
    monkeypatch.setattr(routes_model_query, "resolve_model_reference", lambda *args, **kwargs: ("portrait_hub", "secret.onnx", "portrait_hub/secret.onnx", None))
    monkeypatch.setattr(routes_portrait_models, "MODEL_CONFIGS", fake_configs)
    monkeypatch.setattr(routes_portrait_models, "MODEL_ALIASES", {})
    monkeypatch.setattr(routes_portrait_models, "MODEL_REGISTRY", fake_registry)
    client = TestClient(app)

    responses = [
        client.get("/models"),
        client.get("/model-configs"),
        client.get("/model-info?project_name=portrait_hub&model_name=secret.onnx"),
        client.get("/model-package?model_id=portrait_hub/secret.onnx"),
        client.get("/v1/models"),
    ]

    for response in responses:
        assert response.status_code == 200
        assert "secret-models" not in response.text
        assert "secret.labels.txt" not in response.text
        assert "secret-card.yml" not in response.text
        assert "config_path" not in response.text
    assert responses[0].json()["loaded_models"][0]["artifact_resolved"] is True
    assert responses[1].json()["models"]["portrait_hub/secret.onnx"]["artifact"]["path_configured"] is True


def test_legacy_unload_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    registry_snapshot = dict(MODEL_REGISTRY)
    MODEL_REGISTRY.clear()
    MODEL_REGISTRY["portrait_hub/yolov8n.onnx"] = {"model_hash": "hash"}

    async def fake_unload_model_by_key(key):
        return MODEL_REGISTRY.pop(key, None) is not None

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="state write failed")

    monkeypatch.setattr(routes_model_lifecycle, "unload_model_by_key", fake_unload_model_by_key)
    monkeypatch.setattr(routes_model_lifecycle, "audit_event", fail_audit)

    try:
        response = client.post("/unload", json={"project_name": "portrait_hub", "model_name": "yolov8n.onnx"})

        assert response.status_code == 503
        assert MODEL_REGISTRY["portrait_hub/yolov8n.onnx"]["model_hash"] == "hash"
    finally:
        MODEL_REGISTRY.clear()
        MODEL_REGISTRY.update(registry_snapshot)


def test_legacy_reload_config_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    config_snapshot = dict(routes_model_query.MODEL_CONFIGS)
    alias_snapshot = dict(routes_model_query.MODEL_ALIASES)
    routes_model_query.MODEL_CONFIGS.clear()
    routes_model_query.MODEL_CONFIGS.update({"old/model.onnx": {"task": "old"}})
    routes_model_query.MODEL_ALIASES.clear()
    routes_model_query.MODEL_ALIASES.update({"old_alias": {"target": "old/model.onnx"}})

    def fake_reload_model_config_state():
        routes_model_query.MODEL_CONFIGS.clear()
        routes_model_query.MODEL_CONFIGS.update({"new/model.onnx": {"task": "new"}})
        routes_model_query.MODEL_ALIASES.clear()
        routes_model_query.MODEL_ALIASES.update({"new_alias": {"target": "new/model.onnx"}})
        return routes_model_query.MODEL_CONFIGS, routes_model_query.MODEL_ALIASES

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="state write failed")

    monkeypatch.setattr(routes_model_query, "reload_model_config_state", fake_reload_model_config_state)
    monkeypatch.setattr(routes_model_query, "audit_event", fail_audit)

    try:
        response = client.post("/reload-config")

        assert response.status_code == 503
        assert routes_model_query.MODEL_CONFIGS == {"old/model.onnx": {"task": "old"}}
        assert routes_model_query.MODEL_ALIASES == {"old_alias": {"target": "old/model.onnx"}}
    finally:
        routes_model_query.MODEL_CONFIGS.clear()
        routes_model_query.MODEL_CONFIGS.update(config_snapshot)
        routes_model_query.MODEL_ALIASES.clear()
        routes_model_query.MODEL_ALIASES.update(alias_snapshot)
