from io import BytesIO

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import routes_portrait_gallery
from app import routes_portrait_admin
from app import routes_portrait_jobs
from app import routes_portrait_streams
from app import routes_portrait_models
from app import portrait_gallery
from app import portrait_vector_store
from app.portrait_gallery import GALLERY, gallery_key
from app.portrait_jobs import VIDEO_JOBS, VideoJob, get_video_job, job_key
from app.portrait_streams import STREAMS, StreamEvent, create_stream, stream_key
from app.portrait_thresholds import threshold_snapshot, validate_threshold_modality
from app.runtime_state import MODEL_REGISTRY
from main import app


def image_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (96, 80), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(name: str, color: tuple[int, int, int]) -> tuple[str, tuple[str, bytes, str]]:
    return name, (f"{name}.png", image_bytes(color), "image/png")


def test_v1_compare_persons_uses_threshold_contract() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/compare/persons",
        files=[
            upload("image_a", (120, 30, 40)),
            upload("image_b", (120, 30, 40)),
        ],
        data={"threshold_profile": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["modality"] == "body"
    assert payload["passed"] is True
    assert payload["threshold_profile"] == "normal"
    assert payload["similarity"] >= payload["threshold"]
    assert "input" in payload
    assert payload["input"]["exact_duplicate"] is True
    assert payload["decision"]["input_independence"]["risk"] == "duplicate_input"
    assert "duplicate_input" in payload["decision"]["risk_factors"]
    assert "fingerprint" not in payload["input"]["a"]
    assert "fingerprint" not in payload["input"]["b"]
    assert "sha256" not in response.text
    assert "average_hash" not in response.text


def test_v1_compare_batch_async_returns_batch_job_result() -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()

    response = client.post(
        "/v1/compare/batch",
        files=[
            upload("image_a", (120, 30, 40)),
            upload("image_b", (120, 30, 40)),
        ],
        data={"modality": "body", "async_mode": "true"},
    )

    assert response.status_code == 200
    batch_id = response.json()["data"]["batch_id"]
    assert batch_id.startswith("batch_")
    result = client.get(f"/v1/jobs/{batch_id}/result")
    assert result.status_code == 200
    payload = result.json()["data"]
    assert payload["job"]["status"] == "completed"
    assert payload["result"]["pair_count"] == 1
    assert payload["result"]["results"][0]["comparison"]["passed"] is True


def test_v1_gallery_enroll_and_search_round_trip() -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_test_round_trip", "display_name": "Test Person", "modality": "body"},
    )
    assert enroll.status_code == 200
    assert enroll.json()["data"]["person"]["person_id"] == "p_test_round_trip"
    assert enroll.json()["data"]["features"][0]["object"]["backend"] == "local_file"
    assert enroll.json()["data"]["features"][0]["object"]["stored"] is True
    assert "object_key" not in enroll.json()["data"]["features"][0]["object"]
    assert "sha256" not in enroll.json()["data"]["features"][0]["object"]

    search = client.post(
        "/v1/gallery/search",
        files=[upload("file", (10, 80, 180))],
        data={"modality": "body", "top_k": "3"},
    )

    assert search.status_code == 200
    candidates = search.json()["data"]["candidates"]
    assert candidates
    assert candidates[0]["person_id"] == "p_test_round_trip"
    assert search.json()["data"]["query"]["combined_quality_score"] >= search.json()["data"]["query"]["quality_score"] * 0.76


def test_v1_gallery_search_batch_async_returns_batch_job_result() -> None:
    client = TestClient(app)
    GALLERY.clear()
    VIDEO_JOBS.clear()
    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_async_search", "display_name": "Async Search", "modality": "body"},
    )
    assert enroll.status_code == 200

    response = client.post(
        "/v1/gallery/search/batch",
        files=[upload("files", (10, 80, 180))],
        data={"modality": "body", "top_k": "3", "async_mode": "true"},
    )

    assert response.status_code == 200
    batch_id = response.json()["data"]["batch_id"]
    result = client.get(f"/v1/jobs/{batch_id}/result")
    assert result.status_code == 200
    payload = result.json()["data"]
    assert payload["job"]["status"] == "completed"
    assert payload["result"]["query_count"] == 1
    assert payload["result"]["results"][0]["candidate_count"] >= 1


def test_v1_gallery_reindex_rebuilds_vector_index_with_filters(monkeypatch) -> None:
    client = TestClient(app)
    gallery_snapshot = dict(GALLERY)
    GALLERY.clear()
    calls = []
    audit_events = []

    person = portrait_gallery.PersonRecord(
        tenant_id="default",
        person_id="p_reindex_api",
        display_name="Reindex API",
        metadata={},
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_reindex_body",
                modality="body",
                embedding=[1.0, 0.0],
                embedding_dim=2,
                model_id="model-a",
                model_version="v1",
                quality_score=0.9,
                source_id="source-body",
                created_at=0.0,
            ),
            portrait_gallery.FeatureRecord(
                feature_id="f_reindex_face",
                modality="face",
                embedding=[0.0, 1.0],
                embedding_dim=2,
                model_id="model-a",
                model_version="v1",
                quality_score=0.8,
                source_id="source-face",
                created_at=0.0,
            ),
        ],
    )
    other_tenant = portrait_gallery.PersonRecord(
        tenant_id="tenant-b",
        person_id="p_reindex_other",
        display_name=None,
        metadata={},
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_reindex_other",
                modality="body",
                embedding=[0.5, 0.5],
                embedding_dim=2,
                model_id="model-a",
                model_version="v1",
                quality_score=0.7,
                source_id="source-other",
                created_at=0.0,
            )
        ],
    )
    GALLERY[gallery_key("default", person.person_id)] = person
    GALLERY[gallery_key("tenant-b", other_tenant.person_id)] = other_tenant

    class TrackingVectorStore:
        backend_name = "tracking"

        def upsert_feature(self, person_payload, feature_payload):
            calls.append((person_payload, feature_payload))
            return {"backend": self.backend_name, "status": "upserted"}

    monkeypatch.setattr(portrait_vector_store, "VECTOR_STORE", TrackingVectorStore())
    monkeypatch.setattr(routes_portrait_gallery, "audit_event", lambda event, **fields: audit_events.append((event, fields)))

    try:
        dry_run = client.post(
            "/v1/gallery/reindex",
            params={"modality": "body", "model_id": "model-a", "dry_run": "true"},
        )
        assert dry_run.status_code == 200
        dry_run_data = dry_run.json()["data"]
        assert dry_run_data["status"] == "dry_run"
        assert dry_run_data["matched_feature_count"] == 1
        assert dry_run_data["reindexed_feature_count"] == 0
        assert calls == []

        response = client.post("/v1/gallery/reindex", params={"modality": "body", "model_id": "model-a"})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "rebuilt"
        assert data["vector_backend"] == "tracking"
        assert data["person_count"] == 1
        assert data["feature_count"] == 2
        assert data["matched_feature_count"] == 1
        assert data["reindexed_feature_count"] == 1
        assert data["skipped_feature_count"] == 1
        assert data["skip_reasons"] == {"filtered_out": 1}
        assert data["filters"] == {"modality": "body", "model_id": "model-a"}
        assert calls[0][0]["person_id"] == "p_reindex_api"
        assert calls[0][1]["feature_id"] == "f_reindex_body"
        assert calls[0][1]["embedding"] == [1.0, 0.0]
        assert len(audit_events) == 2
        assert audit_events[-1][0] == "gallery_reindex"
        assert audit_events[-1][1]["outcome"] == "success"
        assert audit_events[-1][1]["vector_backend"] == "tracking"
    finally:
        GALLERY.clear()
        GALLERY.update(gallery_snapshot)


def test_v1_gallery_enroll_response_redacts_object_storage_location(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()

    class LeakyObjectStore:
        backend_name = "s3"

        def put_bytes(self, tenant_id, object_type, filename, data):
            return {
                "backend": self.backend_name,
                "object_key": "tenant-a/gallery-image/secret-object-key.json",
                "bucket": "secret-bucket",
                "sha256": "secret-sha",
                "bytes": len(data),
                "encrypted": True,
            }

        def delete_object(self, info):
            return {"deleted": True}

        def health(self):
            return {"backend": self.backend_name, "status": "ready"}

    monkeypatch.setattr(routes_portrait_gallery, "OBJECT_STORE", LeakyObjectStore())

    response = client.post(
        "/v1/gallery/enroll",
        files={"files": ("secret-person-name.png", image_bytes((10, 80, 180)), "image/png")},
        data={"person_id": "p_object_redaction", "modality": "body"},
    )

    assert response.status_code == 200
    object_payload = response.json()["data"]["features"][0]["object"]
    assert object_payload == {"backend": "s3", "stored": True, "encrypted": True}
    for secret in ["object_key", "secret-object-key", "bucket", "secret-bucket", "sha256", "secret-sha", "bytes", "secret-person-name"]:
        assert secret not in response.text


def test_v1_infer_response_does_not_echo_source_filename() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/infer/faces",
        files={"files": ("secret-person-name.png", image_bytes((10, 80, 180)), "image/png")},
    )

    assert response.status_code == 200
    frame = response.json()["data"]["frames"][0]
    assert "filename" not in frame
    assert "fingerprint" not in frame
    assert "secret-person-name" not in response.text
    assert "sha256" not in response.text
    assert "average_hash" not in response.text


def test_v1_gallery_search_rejects_out_of_range_top_k() -> None:
    client = TestClient(app)

    too_small = client.post(
        "/v1/gallery/search",
        files=[upload("file", (10, 80, 180))],
        data={"modality": "body", "top_k": "0"},
    )
    too_large = client.post(
        "/v1/gallery/search",
        files=[upload("file", (10, 80, 180))],
        data={"modality": "body", "top_k": "101"},
    )

    assert too_small.status_code == 400
    assert "top_k 必须大于等于 1" in too_small.json()["detail"]
    assert too_large.status_code == 400
    assert "top_k 必须介于 1 到 100 之间" in too_large.json()["detail"]


def test_v1_gallery_enroll_skips_duplicate_inputs() -> None:
    client = TestClient(app)
    GALLERY.clear()

    payload = [upload("files", (40, 90, 140)), upload("files", (40, 90, 140))]
    enroll = client.post(
        "/v1/gallery/enroll",
        files=payload,
        data={"person_id": "p_duplicate_skip", "modality": "body"},
    )

    assert enroll.status_code == 200
    data = enroll.json()["data"]
    assert data["input_file_count"] == 2
    assert data["feature_count"] == 1
    assert data["skipped_duplicate_count"] == 1
    assert data["skipped_duplicates"][0]["duplicate_distance"] == 0


def test_v1_gallery_enroll_cleans_object_when_feature_persist_fails(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()
    deleted = []

    class FailingObjectStore:
        backend_name = "local_file"

        def put_bytes(self, tenant_id, object_type, filename, data):
            return {"backend": self.backend_name, "object_key": "tenant/gallery/failed.json"}

        def delete_object(self, info):
            deleted.append(info["object_key"])
            return {"deleted": True, "object_key": info["object_key"]}

        def health(self):
            return {"backend": self.backend_name, "status": "ready"}

    def fail_add_feature(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_gallery, "OBJECT_STORE", FailingObjectStore())
    monkeypatch.setattr(routes_portrait_gallery, "add_feature", fail_add_feature)

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_cleanup_failed_feature", "modality": "body"},
    )

    assert response.status_code == 503
    assert deleted == ["tenant/gallery/failed.json"]


def test_gallery_enroll_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()
    deleted = []

    class TrackingObjectStore:
        backend_name = "local_file"

        def put_bytes(self, tenant_id, object_type, filename, data):
            return {"backend": self.backend_name, "object_key": f"{tenant_id}/{object_type}/{filename}"}

        def delete_object(self, info):
            deleted.append(info["object_key"])
            return {"deleted": True, "object_key": info["object_key"]}

        def health(self):
            return {"backend": self.backend_name, "status": "ready"}

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_gallery, "OBJECT_STORE", TrackingObjectStore())
    monkeypatch.setattr(routes_portrait_gallery, "audit_event", fail_audit)
    monkeypatch.setattr(routes_portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None)

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_audit_rollback", "modality": "body"},
    )

    assert response.status_code == 503
    assert "状态写入失败" in response.json()["detail"]
    assert deleted == ["default/gallery-image/files.png"]
    assert "p_audit_rollback" not in {person.person_id for person in GALLERY.values()}


def test_gallery_enroll_existing_person_rolls_back_added_feature_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()
    seed = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_existing_audit", "display_name": "Existing", "modality": "body"},
    )
    assert seed.status_code == 200
    person = next(item for item in GALLERY.values() if item.person_id == "p_existing_audit")
    original_feature_ids = [feature.feature_id for feature in person.features]
    delete_calls = []
    deleted_objects = []

    class TrackingObjectStore:
        backend_name = "local_file"

        def put_bytes(self, tenant_id, object_type, filename, data):
            return {"backend": self.backend_name, "object_key": f"{tenant_id}/{object_type}/{filename}"}

        def delete_object(self, info):
            deleted_objects.append(info["object_key"])
            return {"deleted": True, "object_key": info["object_key"]}

        def health(self):
            return {"backend": self.backend_name, "status": "ready"}

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_gallery, "OBJECT_STORE", TrackingObjectStore())
    monkeypatch.setattr(routes_portrait_gallery, "audit_event", fail_audit)

    def record_delete(tenant_id, person_id):
        delete_calls.append((tenant_id, person_id))

    monkeypatch.setattr(routes_portrait_gallery, "persist_person_delete", record_delete)
    monkeypatch.setattr(routes_portrait_gallery, "persist_person", lambda restored_person: None)
    monkeypatch.setattr(routes_portrait_gallery, "persist_feature", lambda restored_person, feature: None)

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 90, 170))],
        data={"person_id": "p_existing_audit", "display_name": "Changed", "modality": "body"},
    )

    assert response.status_code == 503
    restored = next(item for item in GALLERY.values() if item.person_id == "p_existing_audit")
    assert restored.display_name == "Existing"
    assert [feature.feature_id for feature in restored.features] == original_feature_ids
    assert delete_calls == [("default", "p_existing_audit")]
    assert deleted_objects == ["default/gallery-image/files.png"]


def test_gallery_enroll_rollback_failure_redacts_object_cleanup_details(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()

    class LeakyFailingObjectStore:
        backend_name = "local_file"

        def put_bytes(self, tenant_id, object_type, filename, data):
            return {"backend": self.backend_name, "object_key": "tenant/gallery/secret-token.png"}

        def delete_object(self, info):
            return {
                "backend": self.backend_name,
                "deleted": False,
                "object_key": info["object_key"],
                "error": "删除 secret-token 失败",
            }

        def health(self):
            return {"backend": self.backend_name, "status": "ready"}

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="审计 secret-token")

    monkeypatch.setattr(routes_portrait_gallery, "OBJECT_STORE", LeakyFailingObjectStore())
    monkeypatch.setattr(routes_portrait_gallery, "audit_event", fail_audit)
    monkeypatch.setattr(routes_portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None)

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_cleanup_secret", "modality": "body"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "message": "人员库变更失败，且回滚持久化失败",
        "rollback_failed": True,
        "rollback_error_count": 1,
    }
    assert "secret-token" not in response.text
    assert "object_key" not in response.text
    assert "delete secret" not in response.text
    assert "p_cleanup_secret" not in {person.person_id for person in GALLERY.values()}


def test_gallery_delete_person_cleans_persisted_feature_objects(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()
    deleted_objects = []
    audit_events = []
    person = portrait_gallery.PersonRecord(
        tenant_id="default",
        person_id="p_delete_objects",
        display_name=None,
        metadata={},
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_delete_object",
                modality="body",
                embedding=[1.0, 0.0],
                embedding_dim=2,
                model_id="model",
                model_version="v1",
                quality_score=1.0,
                source_id="source",
                created_at=0.0,
                object_info={"backend": "local_file", "object_key": "default/gallery-image/object.json"},
            )
        ],
    )
    GALLERY[gallery_key("default", person.person_id)] = person

    class TrackingObjectStore:
        def delete_object(self, info):
            deleted_objects.append(info["object_key"])
            return {"backend": "local_file", "deleted": True}

        def health(self):
            return {"backend": "local_file", "status": "ready"}

    monkeypatch.setattr(routes_portrait_gallery, "OBJECT_STORE", TrackingObjectStore())
    monkeypatch.setattr(routes_portrait_gallery, "audit_event", lambda event, **kwargs: audit_events.append((event, kwargs)))
    monkeypatch.setattr(portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None)

    response = client.delete("/v1/gallery/p_delete_objects")

    assert response.status_code == 200
    assert deleted_objects == ["default/gallery-image/object.json"]
    assert len(audit_events) == 1
    event_name, audit_payload = audit_events[0]
    assert event_name == "gallery_delete_person_requested"
    assert isinstance(audit_payload.pop("request_id"), str)
    assert audit_payload == {
        "tenant_id": "default",
        "outcome": "started",
        "person_id": "p_delete_objects",
        "feature_count": 1,
        "object_reference_count": 1,
    }
    assert response.json()["data"]["deleted_object_count"] == 1
    assert gallery_key("default", "p_delete_objects") not in GALLERY


def test_gallery_delete_person_rolls_back_when_object_cleanup_fails(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()
    person = portrait_gallery.PersonRecord(
        tenant_id="default",
        person_id="p_delete_cleanup_fails",
        display_name="Delete Me",
        metadata={},
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_delete_fail",
                modality="body",
                embedding=[1.0, 0.0],
                embedding_dim=2,
                model_id="model",
                model_version="v1",
                quality_score=1.0,
                source_id="source",
                created_at=0.0,
                object_info={"backend": "local_file", "object_key": "default/gallery-image/secret-object.json"},
            )
        ],
    )
    GALLERY[gallery_key("default", person.person_id)] = person

    class FailingObjectStore:
        def delete_object(self, info):
            return {
                "backend": "local_file",
                "deleted": False,
                "reason": "secret 对象删除失败",
                "object_key": info["object_key"],
            }

        def health(self):
            return {"backend": "local_file", "status": "ready"}

    monkeypatch.setattr(routes_portrait_gallery, "OBJECT_STORE", FailingObjectStore())
    monkeypatch.setattr(routes_portrait_gallery, "audit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None)
    monkeypatch.setattr(routes_portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None)
    monkeypatch.setattr(routes_portrait_gallery, "persist_person", lambda restored_person: None)
    monkeypatch.setattr(routes_portrait_gallery, "persist_feature", lambda restored_person, feature: None)

    response = client.delete("/v1/gallery/p_delete_cleanup_fails")

    assert response.status_code == 503
    assert response.json()["detail"] == "对象清理失败"
    assert gallery_key("default", "p_delete_cleanup_fails") in GALLERY
    assert GALLERY[gallery_key("default", "p_delete_cleanup_fails")].features[0].object_info["object_key"].endswith("secret-object.json")
    assert "secret-object" not in response.text
    assert "object_key" not in response.text


def test_gallery_patch_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_patch_audit", "display_name": "Before", "modality": "body"},
    )
    assert enroll.status_code == 200

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_gallery, "audit_event", fail_audit)
    monkeypatch.setattr(routes_portrait_gallery, "persist_person", lambda person: None)

    response = client.patch(
        "/v1/gallery/p_patch_audit",
        json={"display_name": "After", "metadata": {"note": "new"}},
    )

    assert response.status_code == 503
    stored = next(person for person in GALLERY.values() if person.person_id == "p_patch_audit")
    assert stored.display_name == "Before"
    assert stored.metadata == {}


def test_gallery_delete_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_delete_audit", "display_name": "Delete Me", "modality": "body"},
    )
    assert enroll.status_code == 200

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_gallery, "audit_event", fail_audit)
    monkeypatch.setattr(routes_portrait_gallery, "persist_person", lambda person: None)
    monkeypatch.setattr(routes_portrait_gallery, "persist_feature", lambda person, feature: None)

    response = client.delete("/v1/gallery/p_delete_audit")

    assert response.status_code == 503
    stored = next(person for person in GALLERY.values() if person.person_id == "p_delete_audit")
    assert stored.display_name == "Delete Me"
    assert len(stored.features) == 1


def test_v1_video_job_create_rolls_back_job_when_queue_fails(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()

    class FailingTaskQueue:
        def enqueue(self, queue, payload):
            raise HTTPException(status_code=503, detail="任务队列写入失败")

    async def fake_read_video_file(file):
        return b"video"

    monkeypatch.setattr(routes_portrait_jobs, "TASK_QUEUE", FailingTaskQueue())
    monkeypatch.setattr(routes_portrait_jobs, "read_video_file", fake_read_video_file)

    response = client.post(
        "/v1/jobs/video",
        files={"file": ("video.mp4", b"fake", "video/mp4")},
    )

    assert response.status_code == 503
    assert VIDEO_JOBS == {}


def test_v1_video_job_create_rolls_back_job_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()

    class CapturingTaskQueue:
        def enqueue(self, queue, payload):
            class Message:
                def public_dict(self):
                    return {"message_id": "msg_test", "queue": queue, "payload": payload, "status": "queued"}

            return Message()

    async def fake_read_video_file(file):
        return b"video"

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_jobs, "TASK_QUEUE", CapturingTaskQueue())
    monkeypatch.setattr(routes_portrait_jobs, "read_video_file", fake_read_video_file)
    monkeypatch.setattr(routes_portrait_jobs, "audit_event", fail_audit)
    monkeypatch.setattr("app.portrait_jobs.delete_video_job", lambda tenant_id, job_id: None)

    response = client.post(
        "/v1/jobs/video",
        files={"file": ("video.mp4", b"fake", "video/mp4")},
    )

    assert response.status_code == 503
    assert VIDEO_JOBS == {}


def test_v1_video_job_create_response_does_not_echo_source_filename(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    captured_payloads = []

    class CapturingTaskQueue:
        def enqueue(self, queue, payload):
            captured_payloads.append(payload)

            class Message:
                def public_dict(self):
                    return {"message_id": "msg_test", "queue": queue, "payload": payload, "status": "queued"}

            return Message()

    async def fake_read_video_file(file):
        return b"video"

    async def fake_run_video_job(*args, **kwargs):
        return None

    monkeypatch.setattr(routes_portrait_jobs, "TASK_QUEUE", CapturingTaskQueue())
    monkeypatch.setattr(routes_portrait_jobs, "read_video_file", fake_read_video_file)
    monkeypatch.setattr(routes_portrait_jobs, "run_video_job", fake_run_video_job)
    monkeypatch.setattr(routes_portrait_jobs, "audit_event", lambda *args, **kwargs: None)

    response = client.post(
        "/v1/jobs/video",
        files={"file": ("secret-person-name.mp4", b"fake", "video/mp4")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "filename" not in data["job"]
    assert "filename" not in data["queue_message"]["payload"]
    assert "filename" not in captured_payloads[0]
    assert "secret-person-name" not in response.text
    assert next(iter(VIDEO_JOBS.values())).filename is None


def test_v1_video_job_cancel_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_cancel_audit", tenant_id="default", filename="video.mp4", status="queued")
    VIDEO_JOBS[job_key("default", job.job_id)] = job

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_jobs, "audit_event", fail_audit)
    monkeypatch.setattr(routes_portrait_jobs, "persist_video_job", lambda restored_job: None)

    response = client.post(f"/v1/jobs/{job.job_id}/cancel")

    assert response.status_code == 503
    stored = VIDEO_JOBS[job_key("default", job.job_id)]
    assert stored.status == "queued"
    assert stored.cancel_requested is False



def test_v1_video_job_results_lists_completed_jobs_with_thumbnails(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_video_done", tenant_id="default", filename="video.mp4", status="completed")
    job.result = {
        "metadata": {"filename": "video.mp4"},
        "frame_count": 1,
        "analysis_mode": "async_media_fallback",
        "frames": [
            {
                "frame_index": 0,
                "source_frame_index": 0,
                "width": 64,
                "height": 64,
                "thumbnail": "data:image/jpeg;base64,abcd",
                "quality": {"score": 0.9},
                "appearance": {"quality": {"score": 0.9}},
                "embedding_dim": 64,
            }
        ],
    }
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job

    response = client.get("/v1/jobs/video/results")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["results"][0]["job"]["job_id"] == "job_video_done"
    assert data["results"][0]["result"]["frames"][0]["thumbnail"].startswith("data:image/jpeg;base64,")


def test_v1_video_job_not_found_does_not_echo_job_id() -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    secret_job_id = "job_secret_token"

    responses = [
        client.get(f"/v1/jobs/{secret_job_id}"),
        client.get(f"/v1/jobs/{secret_job_id}/result"),
        client.post(f"/v1/jobs/{secret_job_id}/cancel"),
    ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["detail"] == "任务不存在"
        assert secret_job_id not in response.text


def test_v1_gallery_and_stream_not_found_do_not_echo_resource_ids() -> None:
    client = TestClient(app)
    GALLERY.clear()
    STREAMS.clear()
    secret_person_id = "person_secret_token"
    secret_stream_id = "stream_secret_token"

    responses = [
        (client.get(f"/v1/gallery/{secret_person_id}"), "人员不存在", secret_person_id),
        (client.delete(f"/v1/gallery/{secret_person_id}"), "人员不存在", secret_person_id),
        (client.get(f"/v1/streams/{secret_stream_id}"), "视频流不存在", secret_stream_id),
        (client.post(f"/v1/streams/{secret_stream_id}/start"), "视频流不存在", secret_stream_id),
        (client.post(f"/v1/streams/{secret_stream_id}/stop"), "视频流不存在", secret_stream_id),
        (client.get(f"/v1/streams/{secret_stream_id}/status"), "视频流不存在", secret_stream_id),
        (client.get(f"/v1/streams/{secret_stream_id}/events"), "视频流不存在", secret_stream_id),
    ]

    for response, detail, secret in responses:
        assert response.status_code == 404
        assert response.json()["detail"] == detail
        assert secret not in response.text


def test_v1_job_and_stream_reject_invalid_resource_ids_without_echo() -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    STREAMS.clear()
    invalid_id = "bad id"
    encoded_id = "bad%20id"

    responses = [
        (client.get(f"/v1/jobs/{encoded_id}"), "job_id"),
        (client.get(f"/v1/jobs/{encoded_id}/result"), "job_id"),
        (client.post(f"/v1/jobs/{encoded_id}/cancel"), "job_id"),
        (client.get(f"/v1/streams/{encoded_id}"), "stream_id"),
        (client.post(f"/v1/streams/{encoded_id}/start"), "stream_id"),
        (client.post(f"/v1/streams/{encoded_id}/stop"), "stream_id"),
        (client.get(f"/v1/streams/{encoded_id}/status"), "stream_id"),
        (client.get(f"/v1/streams/{encoded_id}/events"), "stream_id"),
    ]

    for response, field_name in responses:
        assert response.status_code == 400
        assert field_name in response.json()["detail"]
        assert invalid_id not in response.text


def test_v1_video_job_rejects_out_of_range_numeric_controls(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()

    async def fake_read_video_file(file):
        return b"video"

    monkeypatch.setattr(routes_portrait_jobs, "read_video_file", fake_read_video_file)

    bad_interval = client.post(
        "/v1/jobs/video",
        files={"file": ("video.mp4", b"fake", "video/mp4")},
        data={"frame_interval": "0"},
    )
    too_many_frames = client.post(
        "/v1/jobs/video",
        files={"file": ("video.mp4", b"fake", "video/mp4")},
        data={"max_frames": "999999"},
    )

    assert bad_interval.status_code == 400
    assert "frame_interval 必须大于等于 1" in bad_interval.json()["detail"]
    assert too_many_frames.status_code == 400
    assert "max_frames 必须介于 1" in too_many_frames.json()["detail"]
    assert VIDEO_JOBS == {}


def test_v1_gallery_is_tenant_isolated() -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        headers={"x-tenant-id": "tenant:shared"},
        files=[upload("files", (210, 90, 20))],
        data={"person_id": "a:p", "modality": "body"},
    )
    assert enroll.status_code == 200

    collision = client.post(
        "/v1/gallery/enroll",
        headers={"x-tenant-id": "tenant"},
        files=[upload("files", (10, 30, 210))],
        data={"person_id": "shared:a:p", "modality": "body"},
    )
    assert collision.status_code == 200

    hidden = client.post(
        "/v1/gallery/search",
        headers={"x-tenant-id": "tenant-b"},
        files=[upload("file", (210, 90, 20))],
        data={"modality": "body"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["data"]["candidates"] == []

    visible = client.post(
        "/v1/gallery/search",
        headers={"x-tenant-id": "tenant:shared"},
        files=[upload("file", (210, 90, 20))],
        data={"modality": "body"},
    )
    assert visible.status_code == 200
    assert visible.json()["data"]["candidates"][0]["tenant_id"] == "tenant:shared"
    assert visible.json()["data"]["candidates"][0]["person_id"] == "a:p"


def test_v1_gallery_rejects_invalid_person_id() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 40, 60))],
        data={"person_id": "bad/person", "modality": "body"},
    )

    assert response.status_code == 400
    assert "person_id 必须" in response.json()["detail"]


def test_v1_gallery_public_response_redacts_metadata() -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 120, 60))],
        data={
            "person_id": "p_sensitive_metadata",
            "modality": "body",
            "metadata": '{"note":"ok","api_key":"secret-key","nested":{"token":"secret-token"}}',
        },
    )

    assert enroll.status_code == 200
    metadata = enroll.json()["data"]["person"]["metadata"]
    assert metadata["note"] == "ok"
    assert metadata["api_key"] == "<redacted>"
    assert metadata["nested"]["token"] == "<redacted>"


def test_v1_gallery_rejects_oversized_metadata() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 120, 60))],
        data={
            "person_id": "p_oversized_metadata",
            "modality": "body",
            "metadata": '{"note":"' + ("x" * 3000) + '"}',
        },
    )

    assert response.status_code == 400
    assert "metadata 字符串值过长" in response.json()["detail"]


def test_v1_gallery_patch_has_strict_schema() -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 120, 60))],
        data={"person_id": "p_patch_schema", "modality": "body"},
    )
    assert enroll.status_code == 200

    unknown = client.patch("/v1/gallery/p_patch_schema", json={"role": "admin"})
    empty = client.patch("/v1/gallery/p_patch_schema", json={})
    long_name = client.patch("/v1/gallery/p_patch_schema", json={"display_name": "x" * 300})

    assert unknown.status_code == 422
    assert empty.status_code == 400
    assert long_name.status_code == 422


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
    monkeypatch.setattr(routes_portrait_streams, "remove_stream", lambda stream_id, tenant_id: STREAMS.pop(stream_key(tenant_id, stream_id), None) is not None)
    try:
        response = client.post(
            "/v1/streams",
            json={"stream_url": "http://example.com/audit-create", "name": "audit-create"},
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
    monkeypatch.setattr("app.portrait_stream_worker.append_jsonl", lambda path, payload, fail_closed=False: None)
    monkeypatch.setattr("app.portrait_stream_worker.persist_stream", lambda stream: None)

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
    monkeypatch.setattr("app.portrait_stream_worker.append_jsonl", lambda path, payload, fail_closed=False: None)
    monkeypatch.setattr("app.portrait_stream_worker.persist_stream", lambda stream: None)

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
        assert response.json()["detail"] == "不支持的阈值方案"
        assert secret_profile not in response.text


def test_v1_threshold_update_redacts_extra_field_names() -> None:
    client = TestClient(app)
    secret_modality = "secret_modality_token"

    response = client.put("/v1/thresholds/normal", json={secret_modality: 0.51})

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"
    assert response.json()["detail"][0]["loc"] == ["body", "extra_field"]
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
    assert response.json()["detail"] == "不支持的模态"
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
    assert "settings 超过最大深度" in response.json()["detail"]


def test_v1_stream_and_retention_requests_reject_extra_fields() -> None:
    client = TestClient(app)

    stream = client.post(
        "/v1/streams",
        json={"stream_url": "http://example.com/live", "unexpected": True},
    )
    cleanup = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0, "force": True})

    assert stream.status_code == 422
    assert cleanup.status_code == 422


def test_v1_retention_cleanup_confirm_validation() -> None:
    client = TestClient(app)

    # 1. 传递正确的 confirm 的情况
    response_ok = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0, "confirm": "cleanup"})
    assert response_ok.status_code == 200

    # 2. 传递错误的 confirm 的情况
    response_bad = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0, "confirm": "wrong"})
    assert response_bad.status_code == 400
    assert "cleanup" in response_bad.json()["detail"]


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
    assert appearance_payload["model"]["status"] in {"color_histogram_fallback", "attribute_reid_onnx"}

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
    assert "SSRF" in response.json()["detail"]


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

    monkeypatch.setattr(routes_portrait_models, "resolve_model_reference", lambda model_id, project_name, model_name: ("portrait_hub", "yolov8n.onnx", "portrait_hub/yolov8n.onnx", None))
    monkeypatch.setattr(routes_portrait_models, "get_model_path", lambda project, model: "models/yolov8n.onnx")
    monkeypatch.setattr(routes_portrait_models, "get_or_load_model", fake_get_or_load_model)
    monkeypatch.setattr(routes_portrait_models, "unload_model_by_key", fake_unload_model_by_key)
    monkeypatch.setattr(routes_portrait_models, "bundle_info", lambda key, bundle: {"model": key})
    monkeypatch.setattr(routes_portrait_models, "audit_event", lambda event, **fields: events.append((event, fields)))

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

    monkeypatch.setattr(routes_portrait_models, "resolve_model_reference", lambda model_id, project_name, model_name: ("portrait_hub", "yolov8n.onnx", "portrait_hub/yolov8n.onnx", None))
    monkeypatch.setattr(routes_portrait_models, "get_model_path", lambda project, model: "models/yolov8n.onnx")
    monkeypatch.setattr(routes_portrait_models, "get_or_load_model", fake_get_or_load_model)
    monkeypatch.setattr(routes_portrait_models, "bundle_info", lambda key, bundle: {"model": key})
    monkeypatch.setattr(routes_portrait_models, "audit_event", fail_audit)

    try:
        response = client.post("/v1/models/portrait_hub/yolov8n.onnx/load")

        assert response.status_code == 503
        assert "状态写入失败" in response.json()["detail"]
        assert "portrait_hub/yolov8n.onnx" not in MODEL_REGISTRY
    finally:
        MODEL_REGISTRY.clear()
        MODEL_REGISTRY.update(registry_snapshot)


def test_v1_model_unload_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    registry_snapshot = dict(MODEL_REGISTRY)
    MODEL_REGISTRY.clear()
    MODEL_REGISTRY["portrait_hub/yolov8n.onnx"] = {"model_hash": "hash", "last_used_at": 1.0}

    async def fake_unload_model_by_key(key):
        return MODEL_REGISTRY.pop(key, None) is not None

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_models, "resolve_model_reference", lambda model_id, project_name, model_name: ("portrait_hub", "yolov8n.onnx", "portrait_hub/yolov8n.onnx", None))
    monkeypatch.setattr(routes_portrait_models, "unload_model_by_key", fake_unload_model_by_key)
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
    monkeypatch.setattr(routes_portrait_models, "save_threshold_state", fail_restore_state)

    response = client.put("/v1/thresholds/normal", json={"body": 0.12})

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "message": "模型管理变更失败，且回滚持久化失败",
        "rollback_failed": True,
        "rollback_error_count": 1,
    }
    assert "secret-token" not in response.text
    assert threshold_snapshot() == before


def test_security_headers_and_admin_export_contract() -> None:
    client = TestClient(app)
    gallery_snapshot = dict(GALLERY)
    GALLERY.clear()

    try:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.headers["X-Content-Type-Options"] == "nosniff"
        assert health.headers["X-Frame-Options"] == "DENY"
        assert health.headers["Referrer-Policy"] == "no-referrer"
        assert health.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert health.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert health.headers["X-Permitted-Cross-Domain-Policies"] == "none"
        assert health.headers["Content-Security-Policy"].startswith("default-src")

        export = client.get("/v1/admin/export")
        assert export.status_code == 200
        assert "people" in export.json()["data"]
        cleanup = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})
        assert cleanup.status_code == 200
        assert "removed_jobs" in cleanup.json()["data"]
        assert "removed_gallery_people" in cleanup.json()["data"]
    finally:
        GALLERY.clear()
        GALLERY.update(gallery_snapshot)


def test_admin_backup_writes_redacted_object(monkeypatch, workspace_tmp_path) -> None:
    from app import portrait_object_storage

    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", workspace_tmp_path / "objects")
    client = TestClient(app)

    response = client.post("/v1/admin/backup", json={"confirm": "backup"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["backup"]["stored"] is True
    assert data["bytes"] > 0


def test_hsts_header_is_explicitly_gated_for_https_deployments(monkeypatch) -> None:
    from app import security_headers

    monkeypatch.setattr(security_headers, "HSTS_ENABLED", True)
    monkeypatch.setattr(security_headers, "HSTS_MAX_AGE_SECONDS", 123)
    monkeypatch.setattr(security_headers, "HSTS_INCLUDE_SUBDOMAINS", True)
    monkeypatch.setattr(security_headers, "HSTS_PRELOAD", True)
    client = TestClient(app)

    response = client.get("/health")

    assert response.headers["Strict-Transport-Security"] == "max-age=123; includeSubDomains; preload"


def test_admin_export_is_redacted_for_sensitive_fields() -> None:
    client = TestClient(app)
    GALLERY.clear()
    snapshot = dict(STREAMS)
    STREAMS.clear()
    try:
        stream = create_stream("http://example.com/live")
        stream.add_event(
            "debug",
            "payload with sensitive fields",
            {"token": "secret-123", "access_key": "key-456", "embedding": [1.0, 2.0, 3.0]},
        )

        export = client.get("/v1/admin/export")

        assert export.status_code == 200
        data = export.json()["data"]
        exported_streams = data["streams"]
        assert exported_streams
        payload = exported_streams[0]["events"][-1]["payload"]
        assert payload["token"] == "<redacted>"
        assert payload["access_key"] == "<redacted>"
        assert payload["embedding"] == "<redacted>"
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_admin_export_is_audited_without_exported_payload(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()
    snapshot = dict(STREAMS)
    STREAMS.clear()
    events = []
    monkeypatch.setattr(routes_portrait_admin, "audit_event", lambda event, **fields: events.append((event, fields)))
    try:
        stream = create_stream("http://example.com/live")
        stream.add_event("debug", "event", {"token": "secret"})

        response = client.get("/v1/admin/export?streams_limit=1&stream_events_limit=1")

        assert response.status_code == 200
        assert events
        event, fields = events[0]
        assert event == "admin_export"
        assert fields["streams_count"] == 1
        assert fields["stream_events_count"] == 1
        assert fields["stream_events_limit"] == 1
        assert "people" not in fields
        assert "streams" not in fields
        assert "thresholds" not in fields
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_admin_export_fails_closed_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_admin, "audit_event", fail_audit)

    response = client.get("/v1/admin/export")

    assert response.status_code == 503
    assert "状态写入失败" in response.json()["detail"]


def test_stream_lists_and_admin_export_are_paginated() -> None:
    client = TestClient(app)
    snapshot = dict(STREAMS)
    STREAMS.clear()
    try:
        streams = [create_stream(f"http://example.com/live-{index}") for index in range(3)]
        for index in range(5):
            streams[0].add_event("debug", f"event {index}")

        listed = client.get("/v1/streams?limit=2&offset=1")
        assert listed.status_code == 200
        data = listed.json()["data"]
        assert data["count"] == 2
        assert data["total"] == 3
        assert data["limit"] == 2
        assert data["offset"] == 1
        assert data["next_offset"] is None

        events = client.get(f"/v1/streams/{streams[0].stream_id}/events?limit=2&offset=1")
        assert events.status_code == 200
        event_data = events.json()["data"]
        assert event_data["count"] == 2
        assert event_data["total"] == len(streams[0].events)
        assert event_data["next_offset"] == 3

        export = client.get("/v1/admin/export?streams_limit=1&stream_events_limit=2")
        assert export.status_code == 200
        export_data = export.json()["data"]
        assert len(export_data["streams"]) == 1
        exported_stream = export_data["streams"][0]
        assert len(exported_stream["events"]) <= 2
        assert export_data["pagination"]["streams"]["total"] == 3
        assert exported_stream["events_pagination"]["limit"] == 2
        assert exported_stream["events_pagination"]["count"] == len(exported_stream["events"])
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_list_limit_rejects_unbounded_requests() -> None:
    client = TestClient(app)

    streams = client.get("/v1/streams?limit=501")
    events = client.get("/v1/streams/str_missing/events?limit=201")
    export = client.get("/v1/admin/export?people_limit=501")

    assert streams.status_code == 422
    assert events.status_code == 422
    assert export.status_code == 422


def test_retention_cleanup_persists_trimmed_streams(monkeypatch) -> None:
    client = TestClient(app)
    snapshot = dict(STREAMS)
    STREAMS.clear()
    persisted = []
    monkeypatch.setattr("app.routes_portrait_admin.persist_stream", lambda stream: persisted.append(stream.stream_id))
    try:
        stream = create_stream("http://example.com/live")
        stream.events.append(StreamEvent(event_id="evt_old", type="old", message="old", created_at=0.0))

        cleanup = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})

        assert cleanup.status_code == 200
        assert cleanup.json()["data"]["trimmed_stream_events"] >= 1
        assert stream.stream_id in persisted
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_retention_cleanup_removes_expired_gallery_people_and_objects(monkeypatch) -> None:
    client = TestClient(app)
    gallery_snapshot = dict(GALLERY)
    GALLERY.clear()
    deleted_objects = []
    audit_events = []
    old_person = portrait_gallery.PersonRecord(
        tenant_id="default",
        person_id="p_retention_old",
        display_name=None,
        metadata={},
        created_at=0.0,
        updated_at=0.0,
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_retention_object",
                modality="body",
                embedding=[1.0, 0.0],
                embedding_dim=2,
                model_id="model",
                model_version="v1",
                quality_score=1.0,
                source_id="source",
                created_at=0.0,
                object_info={"backend": "local_file", "object_key": "default/gallery-image/old-secret.json"},
            )
        ],
    )
    other_tenant = portrait_gallery.PersonRecord(
        tenant_id="tenant-b",
        person_id="p_retention_other_tenant",
        display_name=None,
        metadata={},
        created_at=0.0,
        updated_at=0.0,
    )
    GALLERY[gallery_key("default", old_person.person_id)] = old_person
    GALLERY[gallery_key("tenant-b", other_tenant.person_id)] = other_tenant

    class TrackingObjectStore:
        def delete_object(self, info):
            deleted_objects.append(info["object_key"])
            return {"backend": "local_file", "deleted": True}

        def health(self):
            return {"backend": "local_file", "status": "ready"}

    monkeypatch.setattr(routes_portrait_admin, "OBJECT_STORE", TrackingObjectStore())
    monkeypatch.setattr(routes_portrait_admin, "audit_event", lambda event, **fields: audit_events.append((event, fields)))
    monkeypatch.setattr(portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None)
    try:
        cleanup = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})

        assert cleanup.status_code == 200
        data = cleanup.json()["data"]
        assert data["removed_gallery_people"] == 1
        assert data["deleted_gallery_objects"] == 1
        assert deleted_objects == ["default/gallery-image/old-secret.json"]
        assert gallery_key("default", "p_retention_old") not in GALLERY
        assert gallery_key("tenant-b", "p_retention_other_tenant") in GALLERY
        event_name, event_payload = audit_events[0]
        assert event_name == "retention_cleanup"
        assert event_payload["outcome"] == "started"
        assert event_payload["candidate_gallery_people"] == 1
        assert event_payload["candidate_gallery_feature_count"] == 1
        assert event_payload["candidate_gallery_object_reference_count"] == 1
    finally:
        GALLERY.clear()
        GALLERY.update(gallery_snapshot)


def test_retention_cleanup_rolls_back_gallery_person_when_object_cleanup_fails(monkeypatch) -> None:
    client = TestClient(app)
    gallery_snapshot = dict(GALLERY)
    GALLERY.clear()
    person = portrait_gallery.PersonRecord(
        tenant_id="default",
        person_id="p_retention_object_fail",
        display_name="Retain Me",
        metadata={},
        created_at=0.0,
        updated_at=0.0,
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_retention_fail",
                modality="body",
                embedding=[1.0, 0.0],
                embedding_dim=2,
                model_id="model",
                model_version="v1",
                quality_score=1.0,
                source_id="source",
                created_at=0.0,
                object_info={"backend": "local_file", "object_key": "default/gallery-image/secret-object.json"},
            )
        ],
    )
    GALLERY[gallery_key("default", person.person_id)] = person

    class FailingObjectStore:
        def delete_object(self, info):
            return {
                "backend": "local_file",
                "deleted": False,
                "reason": "secret 对象删除失败",
                "object_key": info["object_key"],
            }

        def health(self):
            return {"backend": "local_file", "status": "ready"}

    monkeypatch.setattr(routes_portrait_admin, "OBJECT_STORE", FailingObjectStore())
    monkeypatch.setattr(routes_portrait_admin, "audit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None)
    monkeypatch.setattr(routes_portrait_admin, "persist_person", lambda restored_person: None)
    monkeypatch.setattr(routes_portrait_admin, "persist_feature", lambda restored_person, feature: None)
    try:
        response = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})

        assert response.status_code == 503
        assert response.json()["detail"] == "对象清理失败"
        assert gallery_key("default", "p_retention_object_fail") in GALLERY
        restored = GALLERY[gallery_key("default", "p_retention_object_fail")]
        assert restored.display_name == "Retain Me"
        assert restored.features[0].object_info["object_key"].endswith("secret-object.json")
        assert "secret-object" not in response.text
        assert "object_key" not in response.text
    finally:
        GALLERY.clear()
        GALLERY.update(gallery_snapshot)


def test_retention_cleanup_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    job_snapshot = dict(VIDEO_JOBS)
    stream_snapshot = dict(STREAMS)
    VIDEO_JOBS.clear()
    STREAMS.clear()
    monkeypatch.setattr("app.portrait_jobs.delete_video_job", lambda tenant_id, job_id: None)
    monkeypatch.setattr(routes_portrait_admin, "persist_stream", lambda stream: None)
    monkeypatch.setattr(routes_portrait_admin, "persist_video_job", lambda job: None)

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_admin, "audit_event", fail_audit)
    try:
        job = VideoJob(job_id="job_retention_old", tenant_id="default", filename="old.mp4", updated_at=0.0)
        VIDEO_JOBS[job_key("default", job.job_id)] = job
        stream = create_stream("http://example.com/rollback-audit")
        stream.events = [
            StreamEvent(event_id="evt_old", type="old", message="old", created_at=0.0),
            StreamEvent(event_id="evt_keep", type="keep", message="keep", created_at=999999999999.0),
        ]

        response = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})

        assert response.status_code == 503
        assert VIDEO_JOBS[job_key("default", job.job_id)].filename == "old.mp4"
        assert [event.event_id for event in STREAMS[stream_key("default", stream.stream_id)].events] == [
            "evt_old",
            "evt_keep",
        ]
    finally:
        VIDEO_JOBS.clear()
        VIDEO_JOBS.update(job_snapshot)
        STREAMS.clear()
        STREAMS.update(stream_snapshot)


def test_retention_cleanup_rolls_back_when_stream_persist_fails(monkeypatch) -> None:
    client = TestClient(app)
    job_snapshot = dict(VIDEO_JOBS)
    stream_snapshot = dict(STREAMS)
    VIDEO_JOBS.clear()
    STREAMS.clear()
    monkeypatch.setattr("app.portrait_jobs.delete_video_job", lambda tenant_id, job_id: None)
    monkeypatch.setattr(routes_portrait_admin, "persist_video_job", lambda job: None)

    def fail_persist_stream(stream):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_admin, "persist_stream", fail_persist_stream)
    try:
        job = VideoJob(job_id="job_retention_persist", tenant_id="default", filename="old.mp4", updated_at=0.0)
        VIDEO_JOBS[job_key("default", job.job_id)] = job
        stream = create_stream("http://example.com/rollback-persist")
        stream.events = [
            StreamEvent(event_id="evt_old", type="old", message="old", created_at=0.0),
            StreamEvent(event_id="evt_keep", type="keep", message="keep", created_at=999999999999.0),
        ]

        response = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})

        assert response.status_code == 500
        assert response.json()["detail"] == {
            "message": "保留清理失败，且回滚持久化失败",
            "rollback_failed": True,
            "rollback_error_count": 1,
        }
        assert "状态写入失败" not in response.text
        assert VIDEO_JOBS[job_key("default", job.job_id)].filename == "old.mp4"
        assert [event.event_id for event in STREAMS[stream_key("default", stream.stream_id)].events] == [
            "evt_old",
            "evt_keep",
        ]
    finally:
        VIDEO_JOBS.clear()
        VIDEO_JOBS.update(job_snapshot)
        STREAMS.clear()
        STREAMS.update(stream_snapshot)


def test_v1_job_and_stream_state_use_tenant_scoped_keys() -> None:
    job_snapshot = dict(VIDEO_JOBS)
    stream_snapshot = dict(STREAMS)
    VIDEO_JOBS.clear()
    STREAMS.clear()
    try:
        VIDEO_JOBS[job_key("tenant-a", "job_same")] = VideoJob(job_id="job_same", tenant_id="tenant-a", filename="a.mp4")
        VIDEO_JOBS[job_key("tenant-b", "job_same")] = VideoJob(job_id="job_same", tenant_id="tenant-b", filename="b.mp4")

        assert get_video_job("job_same") is None
        assert get_video_job("job_same", tenant_id="tenant-a").filename == "a.mp4"
        assert get_video_job("job_same", tenant_id="tenant-b").filename == "b.mp4"

        stream_a = create_stream("http://example.com/a", tenant_id="tenant-a")
        stream_b = create_stream("http://example.com/b", tenant_id="tenant-b")
        stream_b.stream_id = stream_a.stream_id
        STREAMS.clear()
        STREAMS[stream_key("tenant-a", stream_a.stream_id)] = stream_a
        STREAMS[stream_key("tenant-b", stream_b.stream_id)] = stream_b

        assert STREAMS[stream_key("tenant-a", stream_a.stream_id)].tenant_id == "tenant-a"
        assert STREAMS[stream_key("tenant-b", stream_a.stream_id)].tenant_id == "tenant-b"
    finally:
        VIDEO_JOBS.clear()
        VIDEO_JOBS.update(job_snapshot)
        STREAMS.clear()
        STREAMS.update(stream_snapshot)
