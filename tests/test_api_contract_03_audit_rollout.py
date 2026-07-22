import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image

from app import (
    portrait_analysis_archive,
    portrait_audit,
    portrait_auth,
    portrait_object_storage,
    rollout_audit,
    routes_person_tracks,
    routes_portrait_infer,
    routes_vision,
    security,
)
from main import app


def v1_error_message(response) -> str:
    return response.json()["error"]["message"]


def v1_validation_issues(response) -> list[dict[str, object]]:
    return response.json()["error"]["details"]["issues"]


def test_v1_vision_results_are_persisted_and_tenant_scoped(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    monkeypatch.setattr(
        portrait_analysis_archive,
        "PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH",
        workspace_tmp_path / "analysis-archive.sqlite3",
    )
    monkeypatch.setattr(portrait_analysis_archive, "PORTRAIT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(portrait_analysis_archive, "ANALYSIS_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", workspace_tmp_path / "objects")
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

    monkeypatch.setattr(routes_vision, "get_model_path", lambda project, model: "unused.onnx")
    monkeypatch.setattr(routes_vision, "get_or_load_model", fake_get_or_load_model)
    monkeypatch.setattr(routes_vision, "load_images", fake_load_images)
    monkeypatch.setattr(routes_vision, "infer_detection_images", fake_infer_detection_images)
    monkeypatch.setattr(routes_vision, "touch_model", fake_touch_model)
    monkeypatch.setattr(routes_vision, "model_package_info", lambda *args: {"type": "detection"})

    try:
        response = client.post(
            "/v1/vision/infer",
            headers={"x-tenant-id": "tenant-a", "x-request-id": "req-image-result"},
            data={"model_id": "person_detector_default", "confidence": "0.42"},
            files={"files": ("secret.png", b"fake", "image/png")},
        )
        listed = client.get(
            "/v1/analysis/results?source_type=image&limit=10",
            headers={"x-tenant-id": "tenant-a"},
        )
        other_tenant = client.get(
            "/v1/analysis/results?source_type=image&limit=10",
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
        assert "object_key" not in listed.text
        assert "secret.png" not in listed.text
        content = client.get(
            record["previews"][0]["content_url"],
            headers={"x-tenant-id": "tenant-a"},
        )
        assert content.status_code == 200
        assert content.headers["content-type"] == "image/jpeg"
        assert content.content.startswith(b"\xff\xd8")
        other_content = client.get(
            record["previews"][0]["content_url"],
            headers={"x-tenant-id": "tenant-b"},
        )
        assert other_content.status_code == 404
        assert other_tenant.status_code == 200
        assert other_tenant.json()["data"]["total"] == 0
    finally:
        (workspace_tmp_path / "analysis-archive.sqlite3").unlink(missing_ok=True)


def test_v1_track_results_restore_persisted_previews_across_requests(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "track-analysis-archive.sqlite3"
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    monkeypatch.setattr(
        portrait_analysis_archive,
        "PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH",
        state_path,
    )
    monkeypatch.setattr(portrait_analysis_archive, "PORTRAIT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(portrait_analysis_archive, "ANALYSIS_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(
        portrait_object_storage,
        "OBJECT_STORAGE_DIR",
        workspace_tmp_path / "track-objects",
    )
    client = TestClient(app, raise_server_exceptions=False)

    async def fake_load_images(files):
        from PIL import Image

        return (
            [
                Image.new("RGB", (20, 16), color="red"),
                Image.new("RGB", (20, 16), color="blue"),
            ],
            ["private-frame-1.png", "private-frame-2.png"],
            0.01,
        )

    async def fake_infer_tracks_for_images(*args, **kwargs):
        timing = {
            "preprocess_seconds": 0.01,
            "queue_seconds": 0.0,
            "inference_seconds": 0.02,
            "postprocess_seconds": 0.01,
        }
        return {
            "detector_key": "portrait/person_detector.onnx",
            "reid_key": "portrait/person_reid.onnx",
            "detector_cold_loaded": False,
            "reid_cold_loaded": False,
            "detector_load_seconds": 0.0,
            "reid_load_seconds": 0.0,
            "detector_meta": {
                "input_shape": [2, 3, 640, 640],
                "output_shapes": [[2, 1, 6]],
                "inference_mode": "test",
                "timing": timing,
            },
            "embedding_meta": {
                "input_shape": [1, 3, 256, 128],
                "output_shapes": [[1, 512]],
                "inference_mode": "test",
                "embedding_dim": 512,
                "timing": timing,
            },
            "frames": [
                {
                    "frame_index": index,
                    "width": 20,
                    "height": 16,
                    "persons": [],
                    "person_count": 0,
                }
                for index in range(2)
            ],
            "tracks": [
                {
                    "track_id": "track-1",
                    "frame_count": 1,
                    "first_frame_index": 0,
                    "last_frame_index": 0,
                    "average_confidence": 0.9,
                    "average_quality": 0.9,
                    "gap_count": 0,
                    "max_gap": 0,
                    "interpolated_count": 0,
                    "stability_score": 1.0,
                    "tracklet_quality_score": 0.9,
                    "association_quality": {},
                    "template": {},
                    "interpolated": [],
                }
            ],
            "track_count": 1,
            "tracker": {"algorithm": "test"},
            "person_count": 0,
            "embedding_count": 0,
        }

    monkeypatch.setattr(routes_person_tracks, "load_images", fake_load_images)
    monkeypatch.setattr(
        routes_person_tracks,
        "infer_tracks_for_images",
        fake_infer_tracks_for_images,
    )

    try:
        response = client.post(
            "/v1/infer/tracks",
            headers={"x-tenant-id": "tenant-a", "x-request-id": "req-tracks"},
            files=[
                ("files", ("frame-1.png", b"fake-1", "image/png")),
                ("files", ("frame-2.png", b"fake-2", "image/png")),
            ],
        )

        assert response.status_code == 200
        assert response.json()["data"]["track_count"] == 1
        assert "result_id" not in response.json()["data"]
        assert state_path.exists()

        portrait_analysis_archive.load_analysis_archives_state()
        listed = client.get(
            "/v1/analysis/results?source_type=image&limit=10",
            headers={"x-tenant-id": "tenant-a"},
        )

        assert listed.status_code == 200
        payload = listed.json()["data"]
        assert payload["total"] == 1
        record = payload["results"][0]
        assert record["request_id"] == "req-tracks"
        assert record["mode"] == "tracks"
        assert record["endpoint"] == "/v1/infer/tracks"
        assert record["payload"]["track_count"] == 1
        assert len(record["previews"]) == 2
        assert all(preview["src"].startswith("data:image/jpeg;base64,") for preview in record["previews"])
        assert "private-frame" not in listed.text
    finally:
        state_path.unlink(missing_ok=True)


def test_all_portrait_image_modes_write_unified_archives(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    monkeypatch.setattr(
        portrait_analysis_archive,
        "PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH",
        workspace_tmp_path / "all-image-modes.sqlite3",
    )
    monkeypatch.setattr(portrait_analysis_archive, "PORTRAIT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(portrait_analysis_archive, "ANALYSIS_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(
        portrait_object_storage,
        "OBJECT_STORAGE_DIR",
        workspace_tmp_path / "all-image-mode-objects",
    )

    class Frame:
        def to_dict(self):
            return {
                "source_type": "image",
                "source_id": "source-test",
                "frame_index": 0,
                "pts_ms": 0,
                "width": 18,
                "height": 14,
            }

    decoded = [
        SimpleNamespace(
            image=Image.new("RGB", (18, 14), color="white"),
            frame=Frame(),
        )
    ]

    async def fake_decode(files):
        return decoded

    async def fake_faces(*args, **kwargs):
        return []

    async def fake_body(*args, **kwargs):
        return {
            "box": [0, 0, 18, 14],
            "score": 0.9,
            "quality": {"score": 0.9},
            "embedding_dim": 0,
            "selection_strategy": "test",
            "model_status": "test",
        }

    async def fake_pose(*args, **kwargs):
        return {
            "quality": {"score": 0.9},
            "keypoints": [],
            "skeleton": [],
            "model_status": "test",
        }

    async def fake_appearance(*args, **kwargs):
        return {
            "box": [0, 0, 18, 14],
            "quality": {"score": 0.9},
            "dominant_color": {"name": "white", "rgb": [255, 255, 255]},
            "attributes": {},
            "embedding_dim": 0,
            "model_status": "test",
        }

    async def fake_gait(*args, **kwargs):
        return None, {"model_status": "test", "embedding_dim": 0}

    monkeypatch.setattr(routes_portrait_infer, "decode_upload_images", fake_decode)
    monkeypatch.setattr(routes_portrait_infer, "infer_face_records_for_image", fake_faces)
    monkeypatch.setattr(
        routes_portrait_infer, "face_model_summary", lambda *args, **kwargs: {"id": "test-face", "status": "test"}
    )
    monkeypatch.setattr(routes_portrait_infer, "infer_body_record_for_image", fake_body)
    monkeypatch.setattr(routes_portrait_infer, "infer_pose_record_for_image", fake_pose)
    monkeypatch.setattr(routes_portrait_infer, "infer_appearance_record_for_image", fake_appearance)
    monkeypatch.setattr(routes_portrait_infer, "infer_gait_embedding_for_images", fake_gait)
    client = TestClient(app, raise_server_exceptions=False)

    try:
        for mode in ["faces", "persons", "pose", "appearance", "gait"]:
            response = client.post(
                f"/v1/infer/{mode}",
                headers={"x-tenant-id": "tenant-a", "x-request-id": f"req-{mode}"},
                files={"files": (f"{mode}.png", b"fake", "image/png")},
            )
            assert response.status_code == 200, response.text

        listed = client.get(
            "/v1/analysis/results?source_type=image&limit=10",
            headers={"x-tenant-id": "tenant-a"},
        )
        assert listed.status_code == 200
        records = listed.json()["data"]["results"]
        assert {record["mode"] for record in records} == {
            "faces",
            "persons",
            "pose",
            "appearance",
            "gait",
        }
        assert all(record["previews"] for record in records)
    finally:
        (workspace_tmp_path / "all-image-modes.sqlite3").unlink(missing_ok=True)


def test_admin_audit_verify_endpoint_redacts_path_and_reports_chain(monkeypatch, workspace_tmp_path) -> None:
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


def test_admin_audit_events_endpoint_is_tenant_scoped_and_redacted(monkeypatch, workspace_tmp_path) -> None:
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


def test_admin_backups_endpoint_returns_recent_redacted_snapshots(monkeypatch, workspace_tmp_path) -> None:
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
    for record, created_at in zip(records, [1000.0, 1001.0, 1002.0, 1003.0], strict=True):
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

    response = client.get("/v1/admin/backups", params={"limit": 10}, headers={"X-Tenant-ID": "tenant-a"})

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


def test_rollout_audit_endpoint_returns_recent_public_records(monkeypatch, workspace_tmp_path) -> None:
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
