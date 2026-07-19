import os
import re
from pathlib import Path

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
    assert values["PORTRAIT_ACCESS_STATE_PATH"] == str(
        workspace_tmp_path / "runtime-state" / "portrait-access.json"
    )
    assert values["PORTRAIT_REVIEW_STATE_PATH"] == str(
        workspace_tmp_path / "runtime-state" / "portrait-review-annotations.json"
    )


def test_env_example_documents_supported_runtime_and_compose_configuration() -> None:
    from app.runtime_defaults import parse_env_file

    root = Path(__file__).resolve().parents[1]
    template_keys = set(parse_env_file(root / ".env.example"))
    settings_source = (root / "app" / "settings.py").read_text(encoding="utf-8")
    runtime_keys = set(
        re.findall(
            r'(?:os\.getenv|parse_(?:int|bool|float|csv)_env)\(\s*["\']([A-Z][A-Z0-9_]*)["\']',
            settings_source,
        )
    )
    compose_source = "\n".join(
        (root / filename).read_text(encoding="utf-8")
        for filename in ("docker-compose.yml", "docker-compose.cpu.yml")
    )
    compose_keys = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose_source))

    # Canonical settings replace APP_ENV; Docker controls the other two values per runtime.
    intentionally_implicit = {"APP_ENV", "CUDA_VISIBLE_DEVICES", "MODELS_ROOT"}
    missing = (runtime_keys | compose_keys) - intentionally_implicit - template_keys

    assert missing == set()


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


def test_removed_legacy_console_routes_are_not_served() -> None:
    client = TestClient(app)

    for path in [
        "/console/legacy",
        "/assets/console.js",
        "/assets/console.css",
        "/assets/console.config.js",
        "/assets/console",
        "/assets/console/",
        "/assets/console/console.html",
        "/assets/console/views/app.js",
        "/assets/console/templates/core.js",
    ]:
        assert client.get(path).status_code == 404


def test_console_next_is_the_public_product_shell_with_strict_policy() -> None:
    client = TestClient(app)

    home = client.get("/")
    response = client.get("/console")
    direct = client.get("/console/next")

    assert home.status_code == 200
    assert response.status_code == 200
    assert direct.status_code == 200
    assert home.text == response.text
    assert response.text == direct.text
    assert response.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
    csp = response.headers["Content-Security-Policy"]
    assert "img-src 'self' data: blob:" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp
    assert "/assets/console-next/assets/" in response.text

    asset_url = response.text.split('src="', 1)[1].split('"', 1)[0]
    asset = client.get(asset_url)
    assert asset.status_code == 200
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert asset.headers["X-Content-Type-Options"] == "nosniff"

    for blocked_asset in [
        "/assets/console-next/index.html",
        "/assets/console-next/.vite/manifest.json",
        f"{asset_url}.map",
    ]:
        assert client.get(blocked_asset).status_code == 404
