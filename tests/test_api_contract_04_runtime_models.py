from typing import ClassVar

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import (
    routes_debug,
    routes_model_query,
    routes_portrait_models,
    security,
)
from app.runtime_state import MODEL_REGISTRY
from main import app


def v1_error_message(response) -> str:
    return response.json()["error"]["message"]


def v1_validation_issues(response) -> list[dict[str, object]]:
    return response.json()["error"]["details"]["issues"]


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


def test_model_gpu_device_inventory_uses_detected_devices(monkeypatch) -> None:
    monkeypatch.setattr(routes_model_query, "gpu_device_ids", lambda: [0, 1])
    monkeypatch.setattr(
        routes_model_query,
        "gpu_memory_metrics",
        lambda: [
            {"device": 0, "used": 2, "free": 6, "total": 8},
            {"device": 2, "used": 1, "free": 15, "total": 16},
        ],
    )
    client = TestClient(app)

    response = client.get("/v1/admin/models/gpu-devices")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["detection_source"] == "nvml"
    assert data["detected_device_ids"] == [0, 2]
    assert [(item["device_id"], item["assignable"]) for item in data["devices"]] == [
        (0, True),
        (1, False),
        (2, True),
    ]


def test_model_gpu_device_assignment_is_audited_and_unloads_old_session(
    monkeypatch,
) -> None:
    model_id = "project/model.onnx"
    events = []
    configure_calls = []
    retry_after = {model_id: 123.0}

    monkeypatch.setattr(routes_model_query, "MODEL_REGISTRY", {model_id: {}})
    monkeypatch.setattr(routes_model_query, "MODEL_LOAD_RETRY_AFTER", retry_after)
    monkeypatch.setattr(routes_model_query, "load_raw_model_config", lambda: {"models": {model_id: {}}})
    monkeypatch.setattr(
        routes_model_query,
        "gpu_device_inventory",
        lambda: {"devices": [{"device_id": 2, "assignable": True}]},
    )

    def fake_configure(target, device_id, allowed):
        configure_calls.append((target, device_id, allowed))
        return {
            "model_id": target,
            "previous_device_id": None,
            "device_id": device_id,
            "assignment": "fixed",
        }

    async def fake_unload(target):
        assert target == model_id
        return True

    monkeypatch.setattr(routes_model_query, "configure_model_gpu_device", fake_configure)
    monkeypatch.setattr(routes_model_query, "reload_model_config_state", lambda: ({}, {}))
    monkeypatch.setattr(routes_model_query, "unload_model_by_key", fake_unload)
    monkeypatch.setattr(
        routes_model_query,
        "audit_event",
        lambda event, **fields: events.append((event, fields)),
    )
    client = TestClient(app)

    response = client.patch(
        "/v1/admin/models/project/model.onnx/gpu-device",
        json={"device_id": 2},
    )

    assert response.status_code == 200
    assert configure_calls == [(model_id, 2, [2])]
    assert events[0][0] == "model_gpu_device_updated"
    assert response.json()["data"]["unloaded"] is True
    assert model_id not in retry_after


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


def test_removed_console_module_assets_are_not_served() -> None:
    client = TestClient(app)

    removed_assets = [
        "/assets/console/api/client.js",
        "/assets/console/state/store.js",
        "/assets/console/renderers/data-viewer.js",
        "/assets/console/visuals/previews.js",
        "/assets/console/views/navigation.js",
        "/assets/console/templates/core.js",
        "/assets/console/templates/access.js",
        "/assets/console/templates/governance.js",
        "/assets/console/templates/index.js",
        "/assets/console/views/analysis.js",
        "/assets/console/views/gallery.js",
        "/assets/console/views/operations.js",
        "/assets/console/runtime/formatting.js",
        "/assets/console/runtime/network.js",
        "/assets/console/visuals/results.js",
        "/assets/console/views/dashboard.js",
        "/assets/console/views/app.js",
        "/assets/console.js",
        "/assets/console.css",
        "/assets/console.config.js",
        "/assets/console",
        "/assets/console/",
    ]
    for path in removed_assets:
        assert client.get(path).status_code == 404

    assert client.get("/assets/console/../console.html").status_code == 404
