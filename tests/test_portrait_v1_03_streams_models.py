from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app import (
    routes_portrait_models,
    routes_portrait_streams,
)
from app.portrait_gallery import GALLERY
from app.portrait_streams import STREAMS, create_stream, stream_key
from app.portrait_thresholds import threshold_snapshot, validate_threshold_modality
from app.runtime_state import MODEL_REGISTRY
from main import app


def image_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (96, 80), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(
    name: str, color: tuple[int, int, int]
) -> tuple[str, tuple[str, bytes, str]]:
    return name, (f"{name}.png", image_bytes(color), "image/png")


def v1_error_message(response) -> str:
    return response.json()["error"]["message"]


def v1_error_details(response):
    return response.json()["error"].get("details", {})


def v1_validation_issues(response) -> list[dict[str, object]]:
    return v1_error_details(response)["issues"]


def test_v1_threshold_update_and_stream_masking() -> None:
    client = TestClient(app)

    update = client.put("/v1/thresholds/normal", json={"body": 0.5})
    assert update.status_code == 200
    assert update.json()["data"]["thresholds"]["body"]["normal"] == 0.5
    client.put("/v1/thresholds/normal", json={"body": 0.68})

    created = client.post(
        "/v1/streams",
        json={
            "stream_url": "rtsp://user:secret@example.com/live",
            "name": "front-door",
            "settings": {"api_key": "stream-api-key", "safe": "visible"},
            "metadata": {"token": "stream-token"},
        },
    )
    assert created.status_code == 200
    stream = created.json()["data"]["stream"]
    assert "secret" not in stream["stream_url"]
    assert stream["settings"]["api_key"] == "<redacted>"
    assert stream["settings"]["safe"] == "visible"
    assert stream["metadata"]["token"] == "<redacted>"

    started = client.post(f"/v1/streams/{stream['stream_id']}/start")
    assert started.status_code == 200
    assert started.json()["data"]["stream"]["status"] in {"blocked", "running"}


def test_stream_create_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    snapshot = dict(STREAMS)
    STREAMS.clear()

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_streams, "audit_event", fail_audit)
    monkeypatch.setattr(
        routes_portrait_streams,
        "remove_stream",
        lambda stream_id, tenant_id: STREAMS.pop(stream_key(tenant_id, stream_id), None)
        is not None,
    )
    try:
        response = client.post(
            "/v1/streams",
            json={
                "stream_url": "http://example.com/audit-create",
                "name": "audit-create",
            },
        )

        assert response.status_code == 503
        assert STREAMS == {}
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_stream_start_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    snapshot = dict(STREAMS)
    STREAMS.clear()
    monkeypatch.setattr(routes_portrait_streams, "persist_stream", lambda stream: None)
    monkeypatch.setattr(
        "app.portrait_stream_worker.append_jsonl",
        lambda path, payload, fail_closed=False: None,
    )
    monkeypatch.setattr(
        "app.portrait_stream_worker.persist_stream", lambda stream: None
    )

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_streams, "audit_event", fail_audit)
    try:
        stream = create_stream("http://example.com/audit-start")
        before_status = stream.status
        before_events = [event.event_id for event in stream.events]

        response = client.post(f"/v1/streams/{stream.stream_id}/start")

        assert response.status_code == 503
        stored = STREAMS[stream_key("default", stream.stream_id)]
        assert stored.status == before_status
        assert [event.event_id for event in stored.events] == before_events
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_stream_stop_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    snapshot = dict(STREAMS)
    STREAMS.clear()
    monkeypatch.setattr(routes_portrait_streams, "persist_stream", lambda stream: None)
    monkeypatch.setattr(
        "app.portrait_stream_worker.append_jsonl",
        lambda path, payload, fail_closed=False: None,
    )
    monkeypatch.setattr(
        "app.portrait_stream_worker.persist_stream", lambda stream: None
    )

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_streams, "audit_event", fail_audit)
    try:
        stream = create_stream("http://example.com/audit-stop")
        stream.status = "running"
        stream.add_event("stream_started", "stream session started")
        before_events = [event.event_id for event in stream.events]

        response = client.post(f"/v1/streams/{stream.stream_id}/stop")

        assert response.status_code == 503
        stored = STREAMS[stream_key("default", stream.stream_id)]
        assert stored.status == "running"
        assert [event.event_id for event in stored.events] == before_events
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_v1_threshold_update_rejects_boolean_values() -> None:
    client = TestClient(app)

    response = client.put("/v1/thresholds/normal", json={"body": True})

    assert response.status_code == 422


def test_v1_threshold_update_rejects_unknown_fields() -> None:
    client = TestClient(app)

    unknown = client.put("/v1/thresholds/normal", json={"unknown_modality": 0.5})
    empty = client.put("/v1/thresholds/normal", json={})

    assert unknown.status_code == 422
    assert empty.status_code == 400


def test_v1_threshold_update_normalizes_profile_and_modality_alias() -> None:
    client = TestClient(app)

    update = client.put("/v1/thresholds/Normal", json={"person": 0.51})

    assert update.status_code == 200
    assert update.json()["data"]["profile"] == "normal"
    assert update.json()["data"]["updated"]["body"] == 0.51
    client.put("/v1/thresholds/normal", json={"body": 0.68})


def test_v1_threshold_profile_rejects_invalid_value_without_echo() -> None:
    client = TestClient(app)
    GALLERY.clear()
    secret_profile = "secret_profile_token"

    update = client.put(f"/v1/thresholds/{secret_profile}", json={"body": 0.51})
    compare = client.post(
        "/v1/compare/persons",
        files=[
            upload("image_a", (120, 30, 40)),
            upload("image_b", (120, 30, 40)),
        ],
        data={"threshold_profile": secret_profile},
    )
    search = client.post(
        "/v1/gallery/search",
        files={"file": ("query.png", image_bytes((10, 80, 180)), "image/png")},
        data={"threshold_profile": secret_profile},
    )

    for response in [update, compare, search]:
        assert response.status_code == 400
        assert v1_error_message(response) == "不支持的阈值方案"
        assert secret_profile not in response.text


def test_v1_threshold_update_redacts_extra_field_names() -> None:
    client = TestClient(app)
    secret_modality = "secret_modality_token"

    response = client.put("/v1/thresholds/normal", json={secret_modality: 0.51})

    assert response.status_code == 422
    assert v1_validation_issues(response)[0]["type"] == "extra_forbidden"
    assert v1_validation_issues(response)[0]["loc"] == ["body", "extra_field"]
    assert secret_modality not in response.text


def test_threshold_modality_validator_rejects_invalid_value_without_echo() -> None:
    secret_modality = "secret_modality_token"

    with pytest.raises(HTTPException) as exc_info:
        validate_threshold_modality(secret_modality)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "不支持的模态"
    assert secret_modality not in str(exc_info.value.detail)


def test_v1_gallery_rejects_invalid_modality_before_decoding_without_echo() -> None:
    client = TestClient(app)
    secret_modality = "secret_gallery_modality_token"

    response = client.post(
        "/v1/gallery/search",
        files={"file": ("not-image.bin", b"not-an-image", "application/octet-stream")},
        data={"modality": secret_modality},
    )

    assert response.status_code == 400
    assert v1_error_message(response) == "不支持的模态"
    assert secret_modality not in response.text
    assert "valid image" not in response.text


def test_v1_stream_rejects_deep_settings() -> None:
    client = TestClient(app)
    deep_settings = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "too-deep"}}}}}}}

    response = client.post(
        "/v1/streams",
        json={"stream_url": "http://example.com/live", "settings": deep_settings},
    )

    assert response.status_code == 400
    assert "settings 超过最大深度" in v1_error_message(response)


def test_v1_stream_and_retention_requests_reject_extra_fields() -> None:
    client = TestClient(app)

    stream = client.post(
        "/v1/streams",
        json={"stream_url": "http://example.com/live", "unexpected": True},
    )
    cleanup = client.post(
        "/v1/admin/retention/cleanup", json={"retention_days": 0, "force": True}
    )

    assert stream.status_code == 422
    assert cleanup.status_code == 422


def test_v1_retention_cleanup_confirm_validation() -> None:
    client = TestClient(app)

    # 1. 传递正确的 confirm 的情况
    response_ok = client.post(
        "/v1/admin/retention/cleanup", json={"retention_days": 0, "confirm": "cleanup"}
    )
    assert response_ok.status_code == 200

    # 2. 传递错误的 confirm 的情况
    response_bad = client.post(
        "/v1/admin/retention/cleanup", json={"retention_days": 0, "confirm": "wrong"}
    )
    assert response_bad.status_code == 400
    assert "cleanup" in v1_error_message(response_bad)


def test_v1_compare_faces_and_gait_expose_input_evidence() -> None:
    client = TestClient(app)

    faces = client.post(
        "/v1/compare/faces",
        files=[upload("image_a", (100, 40, 40)), upload("image_b", (100, 40, 40))],
        data={"threshold_profile": "normal"},
    )
    assert faces.status_code == 200
    face_payload = faces.json()["data"]
    assert "input" in face_payload
    assert "exact_duplicate" in face_payload["input"]
    assert face_payload["decision"]["input_independence"]["independent"] is False
    assert face_payload["decision"]["input_independence"]["risk"] == "duplicate_input"

    gait = client.post(
        "/v1/compare/gait",
        files=[
            upload("sequence_a", (30, 60, 90)),
            upload("sequence_a", (30, 60, 90)),
            upload("sequence_b", (30, 60, 90)),
            upload("sequence_b", (30, 60, 90)),
        ],
        data={"threshold_profile": "normal"},
    )
    assert gait.status_code == 200
    gait_payload = gait.json()["data"]
    assert gait_payload["subjects"]["a"]["used_frame_count"] == 1
    assert gait_payload["subjects"]["a"]["duplicate_frame_count"] == 1
    assert gait_payload["reason"] == "not_enough_unique_frames"


def test_v1_fusion_compare_marks_duplicate_inputs_as_non_independent() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/fusion/compare",
        files=[upload("image_a", (80, 120, 160)), upload("image_b", (80, 120, 160))],
        data={"modalities": "face,body,appearance", "threshold_profile": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["input"]["exact_duplicate"] is True
    assert payload["decision"]["input_independence"]["independent"] is False
    assert payload["decision"]["input_independence"]["confidence_multiplier"] == 0.35
    assert "duplicate_input" in payload["decision"]["risk_factors"]


def test_v1_infer_pose_appearance_and_gait_contracts() -> None:
    client = TestClient(app)

    pose = client.post(
        "/v1/infer/pose",
        files=[upload("files", (80, 120, 160))],
    )
    appearance = client.post(
        "/v1/infer/appearance",
        files=[upload("files", (90, 110, 130))],
        data={"include_embeddings": "true"},
    )
    gait = client.post(
        "/v1/infer/gait",
        files=[upload("files", (30, 60, 90)), upload("files", (50, 80, 110))],
        data={"include_embedding": "true"},
    )

    assert pose.status_code == 200
    assert pose.json()["data"]["frames"][0]["pose"]["keypoints"]
    assert pose.json()["data"]["model"]["status"] == "placeholder"

    assert appearance.status_code == 200
    appearance_payload = appearance.json()["data"]
    assert appearance_payload["frames"][0]["appearance"]["embedding_dim"] > 0
    assert "embedding" in appearance_payload["frames"][0]["appearance"]
    assert appearance_payload["model"]["status"] in {
        "color_histogram_fallback",
        "attribute_reid_onnx",
    }

    assert gait.status_code == 200
    gait_payload = gait.json()["data"]
    assert gait_payload["tracklet"]["frame_count"] == 2
    assert gait_payload["tracklet"]["embedding_dim"] > 0
    assert "embedding" in gait_payload["tracklet"]


def test_v1_stream_blocks_private_ip_literals_by_default() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/streams",
        json={"stream_url": "rtsp://127.0.0.1/live", "name": "local"},
    )

    assert response.status_code == 400
    assert "SSRF" in v1_error_message(response)


def test_v1_admin_status_reports_production_adapters() -> None:
    client = TestClient(app)

    response = client.get("/v1/admin/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["storage"]["backend"] in {"json_file", "postgres"}
    assert data["vector_store"]["backend"] in {"local_numpy", "pgvector", "qdrant"}
    assert data["object_storage"]["backend"] in {"local_file", "s3"}
    assert "require_encryption" in data["security"]
    assert "jwt_secret_id_configured" in data["security"]
    assert "jwt_secret_keyring_configured" in data["security"]
    assert "encryption_key_id_configured" in data["security"]
    assert "encryption_keyring_configured" in data["security"]
    assert "model_capabilities" in data


def test_v1_model_load_and_unload_are_audited(monkeypatch) -> None:
    client = TestClient(app)
    events = []

    async def fake_get_or_load_model(key, model_path):
        return {"model_hash": "hash"}, True, 0.01

    async def fake_unload_model_by_key(key):
        return True

    monkeypatch.setattr(
        routes_portrait_models,
        "resolve_model_reference",
        lambda model_id, project_name, model_name: (
            "portrait_hub",
            "yolov8n.onnx",
            "portrait_hub/yolov8n.onnx",
            None,
        ),
    )
    monkeypatch.setattr(
        routes_portrait_models,
        "get_model_path",
        lambda project, model: "models/yolov8n.onnx",
    )
    monkeypatch.setattr(
        routes_portrait_models, "get_or_load_model", fake_get_or_load_model
    )
    monkeypatch.setattr(
        routes_portrait_models, "unload_model_by_key", fake_unload_model_by_key
    )
    monkeypatch.setattr(
        routes_portrait_models, "bundle_info", lambda key, bundle: {"model": key}
    )
    monkeypatch.setattr(
        routes_portrait_models,
        "audit_event",
        lambda event, **fields: events.append((event, fields)),
    )

    load = client.post("/v1/models/portrait_hub/yolov8n.onnx/load")
    unload = client.post("/v1/models/portrait_hub/yolov8n.onnx/unload")

    assert load.status_code == 200
    assert unload.status_code == 200
    assert [event for event, _ in events] == ["model_loaded", "model_unloaded"]


def test_v1_model_load_fails_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    registry_snapshot = dict(MODEL_REGISTRY)
    MODEL_REGISTRY.clear()

    async def fake_get_or_load_model(key, model_path):
        bundle = {"model_hash": "hash", "path": str(model_path), "last_used_at": 1.0}
        MODEL_REGISTRY[key] = bundle
        return bundle, True, 0.01

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(
        routes_portrait_models,
        "resolve_model_reference",
        lambda model_id, project_name, model_name: (
            "portrait_hub",
            "yolov8n.onnx",
            "portrait_hub/yolov8n.onnx",
            None,
        ),
    )
    monkeypatch.setattr(
        routes_portrait_models,
        "get_model_path",
        lambda project, model: "models/yolov8n.onnx",
    )
    monkeypatch.setattr(
        routes_portrait_models, "get_or_load_model", fake_get_or_load_model
    )
    monkeypatch.setattr(
        routes_portrait_models, "bundle_info", lambda key, bundle: {"model": key}
    )
    monkeypatch.setattr(routes_portrait_models, "audit_event", fail_audit)

    try:
        response = client.post("/v1/models/portrait_hub/yolov8n.onnx/load")

        assert response.status_code == 503
        assert "状态写入失败" in v1_error_message(response)
        assert "portrait_hub/yolov8n.onnx" not in MODEL_REGISTRY
    finally:
        MODEL_REGISTRY.clear()
        MODEL_REGISTRY.update(registry_snapshot)


def test_v1_model_unload_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    registry_snapshot = dict(MODEL_REGISTRY)
    MODEL_REGISTRY.clear()
    MODEL_REGISTRY["portrait_hub/yolov8n.onnx"] = {
        "model_hash": "hash",
        "last_used_at": 1.0,
    }

    async def fake_unload_model_by_key(key):
        return MODEL_REGISTRY.pop(key, None) is not None

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(
        routes_portrait_models,
        "resolve_model_reference",
        lambda model_id, project_name, model_name: (
            "portrait_hub",
            "yolov8n.onnx",
            "portrait_hub/yolov8n.onnx",
            None,
        ),
    )
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


def test_v1_threshold_update_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    before = threshold_snapshot()

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_models, "audit_event", fail_audit)
    monkeypatch.setattr(routes_portrait_models, "save_threshold_state", lambda: None)

    response = client.put("/v1/thresholds/normal", json={"body": 0.12})

    assert response.status_code == 503
    assert threshold_snapshot() == before


def test_v1_threshold_update_rollback_failure_is_redacted(monkeypatch) -> None:
    client = TestClient(app)
    before = threshold_snapshot()

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="审计 secret-token")

    def fail_restore_state():
        raise HTTPException(status_code=503, detail="恢复 secret-token")

    monkeypatch.setattr(routes_portrait_models, "audit_event", fail_audit)
    monkeypatch.setattr(
        routes_portrait_models, "save_threshold_state", fail_restore_state
    )

    response = client.put("/v1/thresholds/normal", json={"body": 0.12})

    assert response.status_code == 500
    assert v1_error_message(response) == "模型管理变更失败，且回滚持久化失败"
    assert v1_error_details(response) == {
        "rollback_failed": True,
        "rollback_error_count": 1,
    }
    assert "secret-token" not in response.text
    assert threshold_snapshot() == before


