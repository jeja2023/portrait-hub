import os

import pytest
from fastapi.testclient import TestClient

import app.config_hot_reload as config_hot_reload
import app.rate_limit as rate_limit
import app.settings as settings
from app import (
    portrait_model_capabilities,
)
from main import app


def v1_error_message(response) -> str:
    return response.json()["error"]["message"]


def v1_validation_issues(response) -> list[dict[str, object]]:
    return response.json()["error"]["details"]["issues"]


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


def test_dev_start_local_env_clears_api_token_for_browser_console(
    workspace_tmp_path,
) -> None:
    from app.runtime_defaults import parse_env_file
    from dev_start import write_local_dev_env

    env_path = workspace_tmp_path / ".env"
    env_path.write_text(
        "API_TOKEN=123456\nAUTH_REQUIRED=true\nRBAC_ENABLED=true\nALLOW_PRIVATE_STREAM_HOSTS=true\n", encoding="utf-8"
    )

    local_env_file, overrides = write_local_dev_env(workspace_tmp_path, env_path)
    values = parse_env_file(local_env_file)

    assert overrides["API_TOKEN"] == ""
    assert values["API_TOKEN"] == ""
    assert values["AUTH_REQUIRED"] == "false"
    assert values["RBAC_ENABLED"] == "false"
    assert values["VIDEO_JOB_WORKER_IN_PROCESS"] == "true"
    assert values["ALLOW_PRIVATE_STREAM_HOSTS"] == "true"


def test_dev_start_runs_stream_worker_with_api(
    monkeypatch,
    workspace_tmp_path,
) -> None:
    import dev_start

    processes = []

    class FakeProcess:
        def __init__(self, args, *, cwd, env):
            self.args = args
            self.cwd = cwd
            self.env = env
            self.terminated = False
            processes.append(self)

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

        def kill(self):
            raise AssertionError("graceful termination should succeed")

    monkeypatch.setattr(dev_start.subprocess, "Popen", FakeProcess)

    def stop_supervisor(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(dev_start.time, "sleep", stop_supervisor)
    python_exe = workspace_tmp_path / "python"
    api_args = [str(python_exe), "-m", "uvicorn", "main:app"]
    service_env = {"ALLOW_PRIVATE_STREAM_HOSTS": "true"}

    with pytest.raises(KeyboardInterrupt):
        dev_start.run_local_services(
            workspace_tmp_path,
            python_exe,
            api_args,
            service_env,
        )

    assert processes[0].args == [
        str(python_exe),
        "-m",
        "app.portrait_stream_worker_daemon",
    ]
    assert processes[1].args == api_args
    assert all(process.env is service_env for process in processes)
    assert all(process.terminated for process in processes)


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
        f"MODEL_CAPABILITIES_PATH={capabilities_path}\nPORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES=false\n",
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
        assert (
            portrait_model_capabilities.MODEL_CAPABILITIES["face_embedding"]["model_id"]
            == "portrait_hub/arcface_r100.onnx"
        )
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
        "/v1/analysis/results",
        "/v1/analysis/artifacts/{archive_id}/{artifact_id}",
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
    request_schema = schema["paths"]["/v1/jobs/video"]["post"]["requestBody"]["content"]["multipart/form-data"][
        "schema"
    ]
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
    assert "/assets/console/styles/components.css?v=unified-media-cards" in body
    assert "/assets/console/visuals/results.js?v=unified-media-cards" in body
    assert "/assets/console/views/results.js?v=unified-media-cards" in body


def test_console_assets_use_light_structured_response_panels() -> None:
    client = TestClient(app)

    config_js = client.get("/assets/console.config.js")
    js = client.get("/assets/console.js")
    runtime_js = client.get("/assets/console/views/app.js")
    template_core_js = client.get("/assets/console/templates/core.js")
    template_access_js = client.get("/assets/console/templates/access.js")
    template_governance_js = client.get("/assets/console/templates/governance.js")
    template_index_js = client.get("/assets/console/templates/index.js")
    formatting_js = client.get("/assets/console/runtime/formatting.js")
    network_js = client.get("/assets/console/runtime/network.js")
    result_visuals_js = client.get("/assets/console/visuals/results.js")
    dashboard_js = client.get("/assets/console/views/dashboard.js")
    data_viewer_js = client.get("/assets/console/renderers/data-viewer.js")
    navigation_js = client.get("/assets/console/views/navigation.js")
    access_js = client.get("/assets/console/views/access.js")
    observability_js = client.get("/assets/console/views/observability.js")
    governance_js = client.get("/assets/console/views/governance.js")
    results_js = client.get("/assets/console/views/results.js")
    css = client.get("/assets/console.css")
    components_css = client.get("/assets/console/styles/components.css")
    data_viewer_css = client.get("/assets/console/styles/data-viewer.css")
    responsive_css = client.get("/assets/console/styles/responsive.css")

    assert config_js.status_code == 200
    assert config_js.headers["Cache-Control"] == "no-cache"
    assert template_core_js.headers["Cache-Control"] == "no-cache"
    assert css.headers["Cache-Control"] == "no-cache"
    assert js.status_code == 200
    assert runtime_js.status_code == 200
    assert template_core_js.status_code == 200
    assert template_access_js.status_code == 200
    assert template_governance_js.status_code == 200
    assert template_index_js.status_code == 200
    assert formatting_js.status_code == 200
    assert network_js.status_code == 200
    assert result_visuals_js.status_code == 200
    assert dashboard_js.status_code == 200
    assert data_viewer_js.status_code == 200
    assert navigation_js.status_code == 200
    assert access_js.status_code == 200
    assert observability_js.status_code == 200
    assert governance_js.status_code == 200
    assert results_js.status_code == 200
    assert css.status_code == 200
    assert components_css.status_code == 200
    assert data_viewer_css.status_code == 200
    assert responsive_css.status_code == 200
    config_body = config_js.text
    js_body = js.text
    runtime_body = runtime_js.text
    template_core_body = template_core_js.text
    template_access_body = template_access_js.text
    template_governance_body = template_governance_js.text
    template_index_body = template_index_js.text
    formatting_body = formatting_js.text
    network_body = network_js.text
    result_visuals_body = result_visuals_js.text
    dashboard_body = dashboard_js.text
    data_viewer_body = data_viewer_js.text
    navigation_body = navigation_js.text
    access_body = access_js.text
    observability_body = observability_js.text
    governance_body = governance_js.text
    results_body = results_js.text
    css_body = "\n".join([css.text, components_css.text, data_viewer_css.text, responsive_css.text])
    # The union preserves content contracts while implementation and templates live in focused assets.
    views_union = "\n".join(
        [
            runtime_body,
            template_core_body,
            template_access_body,
            template_governance_body,
            template_index_body,
            formatting_body,
            network_body,
            result_visuals_body,
            dashboard_body,
            access_body,
            observability_body,
            governance_body,
            results_body,
        ]
    )
    assert "PortraitConsoleConfig" in config_body
    assert "/v1/infer/faces" in config_body
    assert "PortraitConsoleRuntime" in js_body
    assert ".result-visual-card--video .result-visual-stage text" in css_body
    assert "function archivedMediaFrameVisuals" in results_body
    assert 'const unit = sourceType === "image" ? "张" : "帧"' in results_body
    assert '["image", "video", "stream"].includes(sourceType)' in results_body
    assert 'accept="video/*,.mp4,.mov,.m4v,.avi,.mkv,.webm"' in template_core_body
    assert "const endpointMap = consoleConfig.endpointMap" in views_union
    assert "fallbackNavigation" in views_union
    assert "data-viewer" in views_union
    assert "查看完整数据（JSON）" in views_union
    assert "复制数据" in views_union
    assert "call-log-application-input" in views_union
    assert 'value="/v1/gallery/search/batch" data-method="POST"' in views_union
    assert 'value="/v1/compare/batch" data-method="POST"' in views_union
    assert 'value="/v1/streams" data-method="POST"' in views_union
    assert 'value="/v1/streams" data-method="GET"' in views_union
    assert 'value="/v1/streams/{stream_id}/events" data-method="GET"' in views_union
    assert "playground-stream-id-input" in views_union
    assert "playground-stream-url-input" in views_union
    assert "playground-async-mode-input" in views_union
    assert "function apiRaw" in network_body
    assert "function playgroundSelection" in access_body
    assert 'appendFiles(form, "files"' in access_body
    assert "endpoint_template" in access_body
    assert "http_status" in views_union
    assert "controlled_use" in access_body
    assert "function summarizeSloCallLogs" in observability_body
    assert "call_logs_30d" in observability_body
    assert "/v1/access/call-logs?limit=500&created_since=" in observability_body
    assert "queue_p95_seconds" in observability_body
    assert "queue_p99_seconds" in observability_body
    assert "gpu_queue_depth" in observability_body
    assert "gpu_device_queue_depths" in observability_body
    assert "error_budget_burn_rate" in observability_body
    assert "success_rate_source" in observability_body
    assert "call_log_window_seconds" in observability_body
    assert "call-log-error-code-input" in views_union
    assert "call-log-created-since-input" in views_union
    assert "call-log-created-until-input" in views_union
    assert "created_since" in views_union
    assert "created_until" in views_union
    assert "accessAppCallSummary" in access_body
    assert "visionLightboxReturnFocus" in views_union
    assert "trapVisionLightboxFocus" in views_union
    assert 'node.querySelector(".vision-lightbox-close")?.focus()' in views_union
    assert "/v1/access/tenants" in access_body
    assert "access-tenant-form" in views_union
    assert "租户开通" in views_union
    assert "/v1/access/error-codes" in views_union
    assert 'view: "error-codes"' in views_union
    assert "error-codes-table" in views_union
    assert "error-codes-json" in views_union
    assert "renderErrorCodes" in observability_body
    assert "最高错误率" in access_body
    assert "release-audit-table" in views_union
    assert "/v1/admin/models/rollout/audit?limit=20" in governance_body
    assert "/v1/admin/audit/verify" in views_union
    assert "auditVerificationPayload" in governance_body
    assert "audit_chain" in views_union
    assert "path_hash" in views_union
    assert "auditChainErrorCount" in governance_body
    assert "/v1/admin/audit/events?limit=20" not in views_union
    assert "/v1/admin/audit/events?${auditEventQueryParams().toString()}" in governance_body
    assert "auditEventQueryParams" in governance_body
    assert "audit-event-filter-button" in views_union
    assert "audit-category-filter-input" in views_union
    assert 'params.set("category", categoryFilter)' in governance_body
    assert "audit-event-table" in views_union
    assert "renderAuditEventRows" in governance_body
    assert "audit_events" in views_union
    assert "/v1/admin/backups?limit=20" in governance_body
    assert "backup-snapshot-summary" in views_union
    assert "backup-snapshot-table" in views_union
    assert "backup-snapshot-refresh-button" in views_union
    assert "renderBackupSnapshots" in governance_body
    assert "refreshAdminData" in governance_body
    assert "backup_snapshots" in views_union
    assert "track-review-annotation-form" in views_union
    assert "/v1/evaluation/datasets" in views_union
    assert "/v1/evaluation/threshold-recommendations" in views_union
    assert "evaluation-dataset-table" in views_union
    assert "evaluation-threshold-table" in views_union
    assert "renderEvaluationThresholdRecommendations" in governance_body
    assert "/v1/evaluation/track-reviews" in views_union
    assert "/v1/evaluation/track-reviews/summary" in views_union
    assert "evaluation-review-summary" in views_union
    assert "import os" in access_body
    assert 'os.getenv("PORTRAIT_HUB_API_TOKEN")' in access_body
    assert "sdk-batch-code" in views_union
    assert "sdk-video-code" in views_union
    assert "sdk-batch-copy-button" in views_union
    assert "sdk-video-copy-button" in views_union
    assert "client.search_batch" in views_union
    assert "async_mode=True" in views_union
    assert "createVideoJob" in views_union
    assert "client.jobResult" in views_union
    assert "X-API-Key: ${state.apiKey}" not in views_union
    assert 'class="json-view data-viewer"' in views_union
    assert "PortraitConsoleModules" in data_viewer_body
    assert "modules.navigation" in navigation_body
    assert "gallery-rebuild" in navigation_body
    assert '<pre id="dashboard-json"' not in views_union
    assert '<pre id="models-json"' not in views_union
    assert "--code" not in css_body
    assert "#111827" not in css_body
    assert "background: #fbfdff" in css_body
    assert "智能分析" in navigation_body
    assert "比对检索" in navigation_body
    assert "特征重建" in navigation_body
    assert "视频解析结果" in views_union
    assert "视频流解析" in views_union
    assert "stream-results-visuals" in views_union
    assert "/v1/analysis/results?${params.toString()}" in results_body
    assert "loadMoreAnalysisResults" in results_body
    assert 'data-results-load-more="image"' in template_core_body
    assert 'data-results-load-more="video"' in template_core_body
    assert 'data-results-load-more="stream"' in template_core_body
    assert "fetch(contentUrl, { headers: headers() })" in result_visuals_body
    assert "/v1/vision/results" not in results_body
    assert "/events?limit=200" not in results_body
    assert "stream-live-summary" in template_access_body
    assert "stream-live-visuals" in template_access_body
    assert "renderLiveStreamResults(payload)" in network_body
    assert 'watchJsonSocket("stream", `/ws/streams/${id}`' in runtime_body
    assert "人员库查询" not in navigation_body
    assert "智能解析" not in navigation_body
    assert "视频分析" not in navigation_body
