import json
import os
from typing import ClassVar

import numpy as np
import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import app.config_hot_reload as config_hot_reload
import app.rate_limit as rate_limit
import app.settings as settings
from app import (
    portrait_audit,
    portrait_auth,
    portrait_image_results,
    portrait_model_capabilities,
    rollout_audit,
    routes_debug,
    routes_model_query,
    routes_portrait_models,
    routes_predict,
    routes_vision,
    security,
)
from app.runtime_state import MODEL_REGISTRY
from main import app


def v1_error_message(response) -> str:
    return response.json()["error"]["message"]


def v1_validation_issues(response) -> list[dict[str, object]]:
    return response.json()["error"]["details"]["issues"]


def test_env_hot_reload_updates_loaded_settings_modules(
    monkeypatch, workspace_tmp_path
) -> None:
    env_path = workspace_tmp_path / ".env"
    env_path.write_text("RATE_LIMIT_PER_MINUTE=37\n", encoding="utf-8")
    original_env = settings.RATE_LIMIT_PER_MINUTE
    original_rate_limit = rate_limit.RATE_LIMIT_PER_MINUTE
    monkeypatch.setattr(config_hot_reload, "ENV_PATH", env_path)
    monkeypatch.setattr(
        config_hot_reload, "audit_config_reload", lambda source, result: None
    )

    try:
        result = config_hot_reload.reload_runtime_config(
            source="test-env", include_env=True
        )

        assert result["env_loaded"] is True
        assert result["env_changed_key_count"] == 1
        assert settings.RATE_LIMIT_PER_MINUTE == 37
        assert rate_limit.RATE_LIMIT_PER_MINUTE == 37
    finally:
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", str(original_env))
        config_hot_reload.reload_settings_modules()
        rate_limit.RATE_LIMIT_PER_MINUTE = original_rate_limit


def test_dev_start_local_env_clears_api_token_for_browser_console(
    workspace_tmp_path,
) -> None:
    from app.runtime_defaults import parse_env_file
    from dev_start import write_local_dev_env

    env_path = workspace_tmp_path / ".env"
    env_path.write_text(
        "API_TOKEN=123456\nAUTH_REQUIRED=true\nRBAC_ENABLED=true\n", encoding="utf-8"
    )

    local_env_file, overrides = write_local_dev_env(workspace_tmp_path, env_path)
    values = parse_env_file(local_env_file)

    assert overrides["API_TOKEN"] == ""
    assert values["API_TOKEN"] == ""
    assert values["AUTH_REQUIRED"] == "false"
    assert values["RBAC_ENABLED"] == "false"


def test_env_hot_reload_updates_model_capabilities(
    monkeypatch, workspace_tmp_path
) -> None:
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
    original_require_env = os.environ.get(
        "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES"
    )
    original_capabilities = dict(portrait_model_capabilities.MODEL_CAPABILITIES)
    monkeypatch.setattr(config_hot_reload, "ENV_PATH", env_path)
    monkeypatch.setattr(
        config_hot_reload, "audit_config_reload", lambda source, result: None
    )

    try:
        result = config_hot_reload.reload_runtime_config(
            source="test-capabilities", include_env=True
        )

        assert result["model_capabilities_reloaded"] is True
        assert (
            portrait_model_capabilities.MODEL_CAPABILITIES["face_embedding"]["model_id"]
            == "portrait_hub/arcface_r100.onnx"
        )
        assert (
            portrait_model_capabilities.MODEL_CAPABILITIES["face_embedding"][
                "embedding_dim"
            ]
            == 512
        )
    finally:
        if original_env is None:
            os.environ.pop("MODEL_CAPABILITIES_PATH", None)
        else:
            os.environ["MODEL_CAPABILITIES_PATH"] = original_env
        if original_require_env is None:
            os.environ.pop("PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES", None)
        else:
            os.environ["PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES"] = (
                original_require_env
            )
        config_hot_reload.reload_settings_modules()
        portrait_model_capabilities.MODEL_CAPABILITIES.clear()
        portrait_model_capabilities.MODEL_CAPABILITIES.update(original_capabilities)


def test_production_capability_requirement_rejects_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        portrait_model_capabilities,
        "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES",
        True,
    )

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
        "/predict",
        "/debug/model-output",
        "/v1/vision/infer",
        "/v1/infer/tracks",
        "/v1/admin/models/warmup",
        "/v1/admin/models/reload",
        "/v1/admin/models/reload-config",
        "/v1/admin/models/rollout/aliases",
        "/v1/admin/models/rollout/audit",
        "/v1/admin/models/rollout/aliases/preview",
        "/v1/admin/models/rollout/aliases/switch",
        "/v1/admin/models/rollout/aliases/weighted",
        "/v1/admin/models/rollout/aliases/rollback",
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
        "/v1/evaluation/datasets",
        "/v1/evaluation/threshold-recommendations",
        "/v1/evaluation/track-reviews",
        "/v1/evaluation/track-reviews/summary",
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
        "/v1/access/tenants",
        "/v1/access/applications",
        "/v1/access/applications/{app_id}",
        "/v1/access/applications/{app_id}/rotate",
        "/v1/access/call-logs",
        "/v1/access/error-codes",
        "/v1/access/webhooks",
        "/v1/access/webhooks/{webhook_id}",
        "/v1/access/webhooks/{webhook_id}/rotate",
        "/v1/access/webhooks/{webhook_id}/sample",
        "/v1/admin/status",
        "/v1/admin/export",
        "/v1/admin/audit/verify",
        "/v1/admin/audit/events",
        "/v1/admin/backups",
        "/v1/admin/backup",
        "/v1/admin/retention/cleanup",
        "/console",
    }

    assert required_paths <= paths
    removed_paths = {
        "/infer/stream/person-tracks",
        "/infer/persons",
        "/infer/person-embeddings",
        "/infer/person-tracks",
        "/infer/video/person-tracks",
        "/vision/infer",
        "/vision/batch-infer",
        "/models",
        "/model-configs",
        "/model-info",
        "/model-package",
        "/warmup",
        "/reload",
        "/unload",
        "/reload-config",
        "/rollout/aliases",
    }
    assert removed_paths.isdisjoint(paths)


def test_video_job_openapi_uses_time_sampling_batch_contract() -> None:
    schema = app.openapi()
    request_schema = schema["paths"]["/v1/jobs/video"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    component_name = request_schema["$ref"].rsplit("/", 1)[-1]
    properties = schema["components"]["schemas"][component_name]["properties"]

    assert "sample_interval_seconds" in properties
    assert "batch_size" in properties
    assert "frame_interval" not in properties
    assert "max_frames" not in properties


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
    assert "/assets/console/views/navigation.js" in body


def test_console_assets_use_light_structured_response_panels() -> None:
    client = TestClient(app)

    config_js = client.get("/assets/console.config.js")
    js = client.get("/assets/console.js")
    runtime_js = client.get("/assets/console/views/app.js")
    data_viewer_js = client.get("/assets/console/renderers/data-viewer.js")
    navigation_js = client.get("/assets/console/views/navigation.js")
    css = client.get("/assets/console.css")

    assert config_js.status_code == 200
    assert js.status_code == 200
    assert runtime_js.status_code == 200
    assert data_viewer_js.status_code == 200
    assert navigation_js.status_code == 200
    assert css.status_code == 200
    config_body = config_js.text
    js_body = js.text
    runtime_body = runtime_js.text
    data_viewer_body = data_viewer_js.text
    navigation_body = navigation_js.text
    css_body = css.text
    assert "PortraitConsoleConfig" in config_body
    assert "/v1/infer/faces" in config_body
    assert "PortraitConsoleRuntime" in js_body
    assert "const endpointMap = consoleConfig.endpointMap" in runtime_body
    assert "fallbackNavigation" in runtime_body
    assert "data-viewer" in runtime_body
    assert "查看完整数据（JSON）" in runtime_body
    assert "复制数据" in runtime_body
    assert "call-log-application-input" in runtime_body
    assert 'value="/v1/gallery/search/batch" data-method="POST"' in runtime_body
    assert 'value="/v1/compare/batch" data-method="POST"' in runtime_body
    assert 'value="/v1/streams" data-method="POST"' in runtime_body
    assert 'value="/v1/streams" data-method="GET"' in runtime_body
    assert 'value="/v1/streams/{stream_id}/events" data-method="GET"' in runtime_body
    assert "playground-stream-id-input" in runtime_body
    assert "playground-stream-url-input" in runtime_body
    assert "playground-async-mode-input" in runtime_body
    assert "function apiRaw" in runtime_body
    assert "function playgroundSelection" in runtime_body
    assert 'appendFiles(form, "files"' in runtime_body
    assert "endpoint_template" in runtime_body
    assert "http_status" in runtime_body
    assert "controlled_use" in runtime_body
    assert "function summarizeSloCallLogs" in runtime_body
    assert "call_logs_30d" in runtime_body
    assert "/v1/access/call-logs?limit=500&created_since=" in runtime_body
    assert "queue_p95_seconds" in runtime_body
    assert "queue_p99_seconds" in runtime_body
    assert "gpu_queue_depth" in runtime_body
    assert "gpu_device_queue_depths" in runtime_body
    assert "error_budget_burn_rate" in runtime_body
    assert "success_rate_source" in runtime_body
    assert "call_log_window_seconds" in runtime_body
    assert "call-log-error-code-input" in runtime_body
    assert "call-log-created-since-input" in runtime_body
    assert "call-log-created-until-input" in runtime_body
    assert "created_since" in runtime_body
    assert "created_until" in runtime_body
    assert "accessAppCallSummary" in runtime_body
    assert "visionLightboxReturnFocus" in runtime_body
    assert "trapVisionLightboxFocus" in runtime_body
    assert 'node.querySelector(".vision-lightbox-close")?.focus()' in runtime_body
    assert "/v1/access/tenants" in runtime_body
    assert "access-tenant-form" in runtime_body
    assert "租户开通" in runtime_body
    assert "/v1/access/error-codes" in runtime_body
    assert 'view: "error-codes"' in runtime_body
    assert "error-codes-table" in runtime_body
    assert "error-codes-json" in runtime_body
    assert "renderErrorCodes" in runtime_body
    assert "最高错误率" in runtime_body
    assert "release-audit-table" in runtime_body
    assert "/v1/admin/models/rollout/audit?limit=20" in runtime_body
    assert "/v1/admin/audit/verify" in runtime_body
    assert "auditVerificationPayload" in runtime_body
    assert "audit_chain" in runtime_body
    assert "path_hash" in runtime_body
    assert "auditChainErrorCount" in runtime_body
    assert "/v1/admin/audit/events?limit=20" not in runtime_body
    assert (
        "/v1/admin/audit/events?${auditEventQueryParams().toString()}" in runtime_body
    )
    assert "auditEventQueryParams" in runtime_body
    assert "audit-event-filter-button" in runtime_body
    assert "audit-category-filter-input" in runtime_body
    assert 'params.set("category", categoryFilter)' in runtime_body
    assert "audit-event-table" in runtime_body
    assert "renderAuditEventRows" in runtime_body
    assert "audit_events" in runtime_body
    assert "/v1/admin/backups?limit=20" in runtime_body
    assert "backup-snapshot-summary" in runtime_body
    assert "backup-snapshot-table" in runtime_body
    assert "backup-snapshot-refresh-button" in runtime_body
    assert "renderBackupSnapshots" in runtime_body
    assert "refreshAdminData" in runtime_body
    assert "backup_snapshots" in runtime_body
    assert "track-review-annotation-form" in runtime_body
    assert "/v1/evaluation/datasets" in runtime_body
    assert "/v1/evaluation/threshold-recommendations" in runtime_body
    assert "evaluation-dataset-table" in runtime_body
    assert "evaluation-threshold-table" in runtime_body
    assert "renderEvaluationThresholdRecommendations" in runtime_body
    assert "/v1/evaluation/track-reviews" in runtime_body
    assert "/v1/evaluation/track-reviews/summary" in runtime_body
    assert "evaluation-review-summary" in runtime_body
    assert "import os" in runtime_body
    assert 'os.getenv("PORTRAIT_HUB_API_TOKEN")' in runtime_body
    assert "sdk-batch-code" in runtime_body
    assert "sdk-video-code" in runtime_body
    assert "sdk-batch-copy-button" in runtime_body
    assert "sdk-video-copy-button" in runtime_body
    assert "client.search_batch" in runtime_body
    assert "async_mode=True" in runtime_body
    assert "createVideoJob" in runtime_body
    assert "client.jobResult" in runtime_body
    assert "X-API-Key: ${state.apiKey}" not in runtime_body
    assert 'class="json-view data-viewer"' in runtime_body
    assert "PortraitConsoleModules" in data_viewer_body
    assert "modules.navigation" in navigation_body
    assert "gallery-rebuild" in navigation_body
    assert '<pre id="dashboard-json"' not in runtime_body
    assert '<pre id="models-json"' not in runtime_body
    assert "--code" not in css_body
    assert "#111827" not in css_body
    assert "background: #fbfdff" in css_body
    assert "智能分析" in navigation_body
    assert "比对检索" in navigation_body
    assert "特征重建" in navigation_body
    assert "视频解析结果" in runtime_body
    assert "视频流解析" in runtime_body
    assert "stream-results-visuals" in runtime_body
    assert "streamResultVisuals" in runtime_body
    assert "人员库查询" not in navigation_body
    assert "智能解析" not in navigation_body
    assert "视频分析" not in navigation_body


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
    import json
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


def test_v1_vision_results_are_persisted_and_tenant_scoped(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    monkeypatch.setattr(
        portrait_image_results,
        "PORTRAIT_IMAGE_RESULTS_STATE_PATH",
        workspace_tmp_path / "image-results.json",
    )
    monkeypatch.setattr(portrait_image_results, "PORTRAIT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(
        portrait_image_results, "MAX_IMAGE_ANALYSIS_RESULTS_PER_TENANT", 4
    )
    portrait_image_results.IMAGE_ANALYSIS_RESULTS.clear()
    client = TestClient(app, raise_server_exceptions=False)

    async def fake_get_or_load_model(*args, **kwargs):
        return {"model_hash": "test-hash"}, False, 0.0

    async def fake_load_images(files):
        from PIL import Image

        return [Image.new("RGB", (16, 12), color="white")], ["secret.png"], 0.0

    async def fake_infer_detection_images(
        bundle, key, images, filenames, confidence=None, iou=None, max_detections=None
    ):
        return (
            [
                {
                    "image_index": 0,
                    "width": images[0].width,
                    "height": images[0].height,
                    "detections": [
                        {
                            "box": [1.0, 2.0, 8.0, 10.0],
                            "score": 0.95,
                            "class_id": 0,
                            "class_name": "person",
                        }
                    ],
                    "detection_count": 1,
                }
            ],
            {
                "input_shape": [1, 3, 16, 16],
                "output_shapes": [[1, 1, 6]],
                "inference_mode": "test",
                "timing": {
                    "preprocess_seconds": 0.0,
                    "queue_seconds": 0.0,
                    "inference_seconds": 0.0,
                    "postprocess_seconds": 0.0,
                },
                "parameters": {"confidence": confidence},
            },
        )

    async def fake_touch_model(*args, **kwargs):
        return None

    monkeypatch.setattr(
        routes_vision, "get_model_path", lambda project, model: "unused.onnx"
    )
    monkeypatch.setattr(routes_vision, "get_or_load_model", fake_get_or_load_model)
    monkeypatch.setattr(routes_vision, "load_images", fake_load_images)
    monkeypatch.setattr(
        routes_vision, "infer_detection_images", fake_infer_detection_images
    )
    monkeypatch.setattr(routes_vision, "touch_model", fake_touch_model)
    monkeypatch.setattr(
        routes_vision, "model_package_info", lambda *args: {"type": "detection"}
    )

    try:
        response = client.post(
            "/v1/vision/infer",
            headers={"x-tenant-id": "tenant-a", "x-request-id": "req-image-result"},
            data={"model_id": "person_detector_default", "confidence": "0.42"},
            files={"files": ("secret.png", b"fake", "image/png")},
        )
        listed = client.get(
            "/v1/vision/results?limit=10",
            headers={"x-tenant-id": "tenant-a"},
        )
        other_tenant = client.get(
            "/v1/vision/results?limit=10",
            headers={"x-tenant-id": "tenant-b"},
        )

        assert response.status_code == 200
        assert listed.status_code == 200
        payload = listed.json()["data"]
        assert payload["count"] == 1
        assert payload["total"] == 1
        record = payload["results"][0]
        assert record["request_id"] == "req-image-result"
        assert record["mode"] == "detection"
        assert record["endpoint"] == "/v1/vision/infer"
        assert record["payload"]["result_count"] == 1
        assert record["previews"][0]["src"].startswith("data:image/jpeg;base64,")
        assert "secret.png" not in listed.text
        assert other_tenant.status_code == 200
        assert other_tenant.json()["data"]["total"] == 0
    finally:
        portrait_image_results.IMAGE_ANALYSIS_RESULTS.clear()


def test_admin_audit_verify_endpoint_redacts_path_and_reports_chain(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    audit_path = workspace_tmp_path / "private-audit.jsonl"
    first = portrait_audit.seal_audit_payload(
        portrait_audit.build_audit_payload(
            "gallery_update",
            request_id="req-audit-1",
            tenant_id="tenant-a",
            outcome="success",
            fields={"person_id": "person-1"},
        ),
        None,
    )
    second = portrait_audit.seal_audit_payload(
        portrait_audit.build_audit_payload(
            "gallery_delete",
            request_id="req-audit-2",
            tenant_id="tenant-a",
            outcome="success",
            fields={"person_id": "person-1"},
        ),
        first["audit_hash"],
    )
    second["event"] = "tampered_event"
    audit_path.write_text(
        json.dumps(first, ensure_ascii=False, sort_keys=True)
        + "\n"
        + json.dumps(second, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", audit_path)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/v1/admin/audit/verify")

    assert response.status_code == 200
    payload = response.json()
    audit_chain = payload["data"]["audit_chain"]
    assert audit_chain["ok"] is False
    assert audit_chain["record_count"] == 2
    assert audit_chain["error_count"] == 1
    assert audit_chain["errors"] == [{"line": 2, "reason": "audit_hash_mismatch"}]
    assert audit_chain["path_hash"]
    assert "path" not in audit_chain
    assert str(audit_path) not in response.text

    assert audit_path.name not in response.text


def test_admin_audit_events_endpoint_is_tenant_scoped_and_redacted(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    audit_path = workspace_tmp_path / "private-audit-events.jsonl"
    first = portrait_audit.seal_audit_payload(
        portrait_audit.build_audit_payload(
            "admin_export",
            request_id="req-audit-a1",
            tenant_id="tenant-a",
            outcome="success",
            fields={"api_key": "secret-token", "people_count": 3},
        ),
        None,
    )
    second = portrait_audit.seal_audit_payload(
        portrait_audit.build_audit_payload(
            "admin_export",
            request_id="req-audit-b1",
            tenant_id="tenant-b",
            outcome="success",
            fields={"people_count": 9},
        ),
        first["audit_hash"],
    )
    third = portrait_audit.seal_audit_payload(
        portrait_audit.build_audit_payload(
            "retention_cleanup",
            request_id="req-audit-a2",
            tenant_id="tenant-a",
            outcome="success",
            fields={"removed_gallery_people": 1},
        ),
        second["audit_hash"],
    )
    first["created_at"] = 1000.0
    first["audit_hash"] = portrait_audit.audit_payload_hash(first)
    second["created_at"] = 1001.0
    second["audit_prev_hash"] = first["audit_hash"]
    second["audit_hash"] = portrait_audit.audit_payload_hash(second)
    third["created_at"] = 1002.0
    third["audit_prev_hash"] = second["audit_hash"]
    third["audit_hash"] = portrait_audit.audit_payload_hash(third)
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(first, ensure_ascii=False, sort_keys=True),
                "not-json",
                json.dumps(second, ensure_ascii=False, sort_keys=True),
                json.dumps(third, ensure_ascii=False, sort_keys=True),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", audit_path)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/v1/admin/audit/events",
        params={"limit": 10},
        headers={"X-Tenant-ID": "tenant-a"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["tenant_id"] == "tenant-a"
    assert payload["count"] == 2
    assert payload["matched_count"] == 2
    assert payload["summary"]["category_counts"]["exports"] == 1
    assert payload["summary"]["category_counts"]["retention"] == 1
    assert payload["summary"]["outcome_counts"] == {"success": 2}
    assert payload["malformed_count"] == 1
    assert [record["request_id"] for record in payload["records"]] == [
        "req-audit-a2",
        "req-audit-a1",
    ]
    assert all(record["tenant_id"] == "tenant-a" for record in payload["records"])
    assert {
        "event",
        "request_id",
        "tenant_id",
        "outcome",
        "created_at",
        "audit_hash",
        "audit_prev_hash",
        "category",
    } <= set(payload["records"][0])
    assert payload["records"][0]["category"] == "retention"
    assert "people_count" not in payload["records"][0]
    assert "secret-token" not in response.text
    assert "tenant-b" not in response.text
    assert str(audit_path) not in response.text
    filtered = client.get(
        "/v1/admin/audit/events",
        params={
            "limit": 10,
            "event": "export",
            "outcome": "success",
            "request_id": "a1",
            "category": "exports",
            "created_until": third["created_at"] - 0.000001,
        },
        headers={"X-Tenant-ID": "tenant-a"},
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()["data"]
    assert filtered_payload["count"] == 1
    assert filtered_payload["records"][0]["request_id"] == "req-audit-a1"
    assert filtered_payload["filters"]["event"] == "export"
    assert filtered_payload["filters"]["category"] == "exports"
    invalid_category = client.get(
        "/v1/admin/audit/events",
        params={"category": "secret"},
        headers={"X-Tenant-ID": "tenant-a"},
    )
    assert invalid_category.status_code == 400
    assert v1_error_message(invalid_category) == "不支持的审计事件类别"


def test_admin_backups_endpoint_returns_recent_redacted_snapshots(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    audit_path = workspace_tmp_path / "private-backup-audit.jsonl"
    records = [
        portrait_audit.seal_audit_payload(
            portrait_audit.build_audit_payload(
                "admin_backup",
                request_id="req-backup-a1",
                tenant_id="tenant-a",
                outcome="success",
                fields={
                    "updated_since": 998.5,
                    "object_backend": "s3",
                    "bytes": 2048,
                    "object_key": "tenant-a/admin-backup/private-key.json",
                    "bucket": "private-bucket",
                    "sha256": "private-digest-a1",
                },
            ),
            None,
        ),
        portrait_audit.seal_audit_payload(
            portrait_audit.build_audit_payload(
                "admin_backup",
                request_id="req-backup-b1",
                tenant_id="tenant-b",
                outcome="success",
                fields={
                    "updated_since": None,
                    "object_backend": "local",
                    "bytes": 11,
                    "object_key": "tenant-b/private.json",
                },
            ),
            None,
        ),
        portrait_audit.seal_audit_payload(
            portrait_audit.build_audit_payload(
                "admin_export",
                request_id="req-export-a1",
                tenant_id="tenant-a",
                outcome="success",
                fields={
                    "object_backend": "s3",
                    "bytes": 4096,
                    "object_key": "tenant-a/export/private.json",
                },
            ),
            None,
        ),
        portrait_audit.seal_audit_payload(
            portrait_audit.build_audit_payload(
                "admin_backup",
                request_id="req-backup-a2",
                tenant_id="tenant-a",
                outcome="success",
                fields={
                    "updated_since": None,
                    "object_backend": "local",
                    "bytes": 1024,
                    "object_key": "tenant-a/admin-backup/private-key-2.json",
                    "bucket": "private-bucket-2",
                    "sha256": "private-digest-a2",
                },
            ),
            None,
        ),
    ]
    previous_hash = None
    for record, created_at in zip(
        records, [1000.0, 1001.0, 1002.0, 1003.0], strict=True
    ):
        record["created_at"] = created_at
        record["audit_prev_hash"] = previous_hash
        record["audit_hash"] = portrait_audit.audit_payload_hash(record)
        previous_hash = record["audit_hash"]
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(records[0], ensure_ascii=False, sort_keys=True),
                "not-json",
                json.dumps(records[1], ensure_ascii=False, sort_keys=True),
                json.dumps(records[2], ensure_ascii=False, sort_keys=True),
                json.dumps(records[3], ensure_ascii=False, sort_keys=True),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", audit_path)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/v1/admin/backups", params={"limit": 10}, headers={"X-Tenant-ID": "tenant-a"}
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["tenant_id"] == "tenant-a"
    assert payload["count"] == 2
    assert payload["malformed_count"] == 1
    assert [row["request_id"] for row in payload["snapshots"]] == [
        "req-backup-a2",
        "req-backup-a1",
    ]
    assert payload["snapshots"][0]["snapshot_id"] == records[3]["audit_hash"]
    assert payload["snapshots"][1]["updated_since"] == 998.5
    assert payload["snapshots"][1]["object_backend"] == "s3"
    assert payload["snapshots"][1]["bytes"] == 2048
    for snapshot in payload["snapshots"]:
        assert "object_key" not in snapshot
        assert "bucket" not in snapshot
        assert "sha256" not in snapshot
    assert "tenant-b" not in response.text
    assert "private-key" not in response.text
    assert "private-bucket" not in response.text
    assert "private-digest" not in response.text
    assert str(audit_path) not in response.text


def test_rollout_audit_endpoint_returns_recent_public_records(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    audit_path = workspace_tmp_path / "rollout-audit.jsonl"

    audit_path.write_text(
        "\n".join(
            [
                '{"time":1,"event":"alias_switch","alias":"detector_default","old_target":"old/model.onnx","new_target":"new/model.onnx","written":true,"secret":"do-not-leak"}',
                "not-json",
                '{"time":2,"event":"alias_weighted_rollout","alias":"detector_default","rollout":[{"target":"old/model.onnx","weight":90,"status":"active","secret":"nested"},{"target":"new/model.onnx","weight":10,"status":"candidate"}],"total_weight":100,"written":true}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rollout_audit, "ROLLOUT_AUDIT_PATH", audit_path)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/v1/admin/models/rollout/audit", params={"limit": 1})

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["count"] == 1
    assert payload["limit"] == 1
    assert payload["malformed_count"] == 1
    assert payload["records"][0]["event"] == "alias_weighted_rollout"
    assert payload["records"][0]["rollout"][1] == {
        "target": "new/model.onnx",
        "weight": 10,
        "status": "candidate",
    }
    assert "do-not-leak" not in response.text
    assert "nested" not in response.text


def test_rollout_alias_preview_invalid_alias_returns_400(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/v1/admin/models/rollout/aliases/preview",
        params={"alias_name": "bad/alias", "traffic_key": "tenant-1"},
    )

    assert response.status_code == 400
    assert v1_error_message(response) == "别名名称无效"
    assert "bad/alias" not in response.text
    assert response.headers["X-Request-ID"]


def test_v1_model_reference_errors_are_fixed_and_redacted() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    infer = client.post(
        "/v1/vision/infer",
        files={"files": ("frame.png", b"not-an-image", "image/png")},
        data={"model_id": "secret/project/secret-model.onnx"},
    )
    info = client.get("/v1/models/secret/project/secret-model.onnx")

    for response in [infer, info]:
        assert response.status_code == 400
        assert v1_error_message(response) == "模型引用无效"
        assert "secret/project" not in response.text
        assert "secret-model" not in response.text


def test_rollout_alias_preview_missing_alias_does_not_echo_alias(monkeypatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/v1/admin/models/rollout/aliases/preview",
        params={"alias_name": "secret_alias", "traffic_key": "tenant-1"},
    )

    assert response.status_code == 404
    assert v1_error_message(response) == "别名不存在"
    assert "secret_alias" not in response.text


@pytest.mark.asyncio
async def test_global_request_body_limit_counts_streamed_body_without_content_length(
    monkeypatch,
) -> None:
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

    assert await limited_request.receive() == {
        "type": "http.request",
        "body": b"12345",
        "more_body": True,
    }
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


def test_debug_model_output_requires_auth_when_enabled_and_auth_required(
    monkeypatch,
) -> None:
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


def test_v1_model_unload_is_audited(monkeypatch) -> None:
    client = TestClient(app)
    events = []

    async def fake_unload_model_by_key(key):
        return True

    monkeypatch.setattr(
        routes_portrait_models, "unload_model_by_key", fake_unload_model_by_key
    )
    monkeypatch.setattr(
        routes_portrait_models,
        "audit_event",
        lambda event, **fields: events.append((event, fields)),
    )

    response = client.post("/v1/models/portrait_hub/yolov8n.onnx/unload")

    assert response.status_code == 200
    assert events[0][0] == "model_unloaded"
    assert events[0][1]["model_id"] == "portrait_hub/yolov8n.onnx"


def test_legacy_reload_config_is_audited(monkeypatch) -> None:
    client = TestClient(app)
    events = []

    monkeypatch.setattr(
        routes_model_query, "reload_model_config_state", lambda: ({"m": {}}, {"a": {}})
    )
    monkeypatch.setattr(
        routes_model_query,
        "audit_event",
        lambda event, **fields: events.append((event, fields)),
    )

    response = client.post("/v1/admin/models/reload-config")

    assert response.status_code == 200
    assert events[0][0] == "model_config_reloaded"
    assert events[0][1]["model_count"] == 1


def test_v1_model_management_responses_do_not_expose_filesystem_paths(
    monkeypatch,
) -> None:
    class FakeTensor:
        name = "input"
        type = "tensor(float)"
        shape: ClassVar[list[int]] = [1, 3, 8, 8]

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

    monkeypatch.setattr(routes_portrait_models, "MODEL_CONFIGS", fake_configs)
    monkeypatch.setattr(routes_portrait_models, "MODEL_ALIASES", {})
    monkeypatch.setattr(routes_portrait_models, "MODEL_REGISTRY", fake_registry)
    monkeypatch.setattr(
        routes_portrait_models, "model_config", lambda key: secret_config
    )
    monkeypatch.setattr(
        routes_portrait_models,
        "get_model_path",
        lambda project, model: "E:/secret-models/tenant-a/secret.onnx",
    )
    monkeypatch.setattr(
        routes_portrait_models,
        "model_package_info",
        lambda key, model_path, digest: fake_package,
    )
    monkeypatch.setattr(
        routes_portrait_models,
        "resolve_model_reference",
        lambda *args, **kwargs: (
            "portrait_hub",
            "secret.onnx",
            "portrait_hub/secret.onnx",
            None,
        ),
    )
    client = TestClient(app)

    list_response = client.get("/v1/models")
    detail_response = client.get("/v1/models/portrait_hub/secret.onnx")

    for response in [list_response, detail_response]:
        assert response.status_code == 200
        assert "secret-models" not in response.text
        assert "secret.labels.txt" not in response.text
        assert "secret-card.yml" not in response.text
        assert "config_path" not in response.text
    assert list_response.json()["data"]["loaded_models"][0]["artifact_resolved"] is True
    assert (
        detail_response.json()["data"]["config"]["artifact"]["path_configured"] is True
    )
    assert detail_response.json()["data"]["package"]["artifact"]["sha256_match"] is True


def test_v1_unload_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    registry_snapshot = dict(MODEL_REGISTRY)
    MODEL_REGISTRY.clear()
    MODEL_REGISTRY["portrait_hub/yolov8n.onnx"] = {"model_hash": "hash"}

    async def fake_unload_model_by_key(key):
        return MODEL_REGISTRY.pop(key, None) is not None

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="state write failed")

    monkeypatch.setattr(
        routes_portrait_models, "unload_model_by_key", fake_unload_model_by_key
    )
    monkeypatch.setattr(routes_portrait_models, "audit_event", fail_audit)

    try:
        response = client.post("/v1/models/portrait_hub/yolov8n.onnx/unload")

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
        routes_model_query.MODEL_ALIASES.update(
            {"new_alias": {"target": "new/model.onnx"}}
        )
        return routes_model_query.MODEL_CONFIGS, routes_model_query.MODEL_ALIASES

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="state write failed")

    monkeypatch.setattr(
        routes_model_query, "reload_model_config_state", fake_reload_model_config_state
    )
    monkeypatch.setattr(routes_model_query, "audit_event", fail_audit)

    try:
        response = client.post("/v1/admin/models/reload-config")

        assert response.status_code == 503
        assert routes_model_query.MODEL_CONFIGS == {"old/model.onnx": {"task": "old"}}
        assert routes_model_query.MODEL_ALIASES == {
            "old_alias": {"target": "old/model.onnx"}
        }
    finally:
        routes_model_query.MODEL_CONFIGS.clear()
        routes_model_query.MODEL_CONFIGS.update(config_snapshot)
        routes_model_query.MODEL_ALIASES.clear()
        routes_model_query.MODEL_ALIASES.update(alias_snapshot)


def test_console_module_assets_are_served() -> None:
    client = TestClient(app)

    modules = [
        "/assets/console/api/client.js",
        "/assets/console/state/store.js",
        "/assets/console/renderers/data-viewer.js",
        "/assets/console/visuals/previews.js",
        "/assets/console/views/navigation.js",
        "/assets/console/views/analysis.js",
        "/assets/console/views/gallery.js",
        "/assets/console/views/operations.js",
    ]
    for path in modules:
        response = client.get(path)
        assert response.status_code == 200
        assert "PortraitConsoleModules" in response.text

    runtime = client.get("/assets/console/views/app.js")
    assert runtime.status_code == 200
    assert "PortraitConsoleRuntime" in runtime.text

    bootstrap = client.get("/assets/console.js")
    assert bootstrap.status_code == 200
    assert "runtime.init" in bootstrap.text
    assert len(bootstrap.text) < 2000

    assert client.get("/assets/console/../console.html").status_code == 404
