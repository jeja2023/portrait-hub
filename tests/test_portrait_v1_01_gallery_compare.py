from io import BytesIO

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app import (
    portrait_gallery,
    portrait_vector_store,
    routes_portrait_gallery,
)
from app.portrait_gallery import GALLERY, gallery_key
from app.portrait_jobs import VIDEO_JOBS
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
        data={
            "person_id": "p_test_round_trip",
            "display_name": "Test Person",
            "modality": "body",
        },
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
    assert (
        search.json()["data"]["query"]["combined_quality_score"]
        >= search.json()["data"]["query"]["quality_score"] * 0.76
    )


def test_v1_gallery_search_batch_async_returns_batch_job_result() -> None:
    client = TestClient(app)
    GALLERY.clear()
    VIDEO_JOBS.clear()
    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={
            "person_id": "p_async_search",
            "display_name": "Async Search",
            "modality": "body",
        },
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
    monkeypatch.setattr(
        routes_portrait_gallery,
        "audit_event",
        lambda event, **fields: audit_events.append((event, fields)),
    )

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

        response = client.post(
            "/v1/gallery/reindex", params={"modality": "body", "model_id": "model-a"}
        )

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


def test_v1_gallery_enroll_response_redacts_object_storage_location(
    monkeypatch,
) -> None:
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
        files={
            "files": ("secret-person-name.png", image_bytes((10, 80, 180)), "image/png")
        },
        data={"person_id": "p_object_redaction", "modality": "body"},
    )

    assert response.status_code == 200
    object_payload = response.json()["data"]["features"][0]["object"]
    assert object_payload == {"backend": "s3", "stored": True, "encrypted": True}
    for secret in [
        "object_key",
        "secret-object-key",
        "bucket",
        "secret-bucket",
        "sha256",
        "secret-sha",
        "bytes",
        "secret-person-name",
    ]:
        assert secret not in response.text


def test_v1_infer_response_does_not_echo_source_filename() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/infer/faces",
        files={
            "files": ("secret-person-name.png", image_bytes((10, 80, 180)), "image/png")
        },
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
    assert "top_k 必须大于等于 1" in v1_error_message(too_small)
    assert too_large.status_code == 400
    assert "top_k 必须介于 1 到 100 之间" in v1_error_message(too_large)


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


def test_v1_gallery_enroll_cleans_object_when_feature_persist_fails(
    monkeypatch,
) -> None:
    client = TestClient(app)
    GALLERY.clear()
    deleted = []

    class FailingObjectStore:
        backend_name = "local_file"

        def put_bytes(self, tenant_id, object_type, filename, data):
            return {
                "backend": self.backend_name,
                "object_key": "tenant/gallery/failed.json",
            }

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
            return {
                "backend": self.backend_name,
                "object_key": f"{tenant_id}/{object_type}/{filename}",
            }

        def delete_object(self, info):
            deleted.append(info["object_key"])
            return {"deleted": True, "object_key": info["object_key"]}

        def health(self):
            return {"backend": self.backend_name, "status": "ready"}

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_gallery, "OBJECT_STORE", TrackingObjectStore())
    monkeypatch.setattr(routes_portrait_gallery, "audit_event", fail_audit)
    monkeypatch.setattr(
        routes_portrait_gallery,
        "persist_person_delete",
        lambda tenant_id, person_id: None,
    )

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_audit_rollback", "modality": "body"},
    )

    assert response.status_code == 503
    assert "状态写入失败" in v1_error_message(response)
    assert deleted == ["default/gallery-image/files.png"]
    assert "p_audit_rollback" not in {person.person_id for person in GALLERY.values()}


def test_gallery_enroll_existing_person_rolls_back_added_feature_when_audit_fails(
    monkeypatch,
) -> None:
    client = TestClient(app)
    GALLERY.clear()
    seed = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={
            "person_id": "p_existing_audit",
            "display_name": "Existing",
            "modality": "body",
        },
    )
    assert seed.status_code == 200
    person = next(
        item for item in GALLERY.values() if item.person_id == "p_existing_audit"
    )
    original_feature_ids = [feature.feature_id for feature in person.features]
    delete_calls = []
    deleted_objects = []

    class TrackingObjectStore:
        backend_name = "local_file"

        def put_bytes(self, tenant_id, object_type, filename, data):
            return {
                "backend": self.backend_name,
                "object_key": f"{tenant_id}/{object_type}/{filename}",
            }

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
    monkeypatch.setattr(
        routes_portrait_gallery, "persist_person", lambda restored_person: None
    )
    monkeypatch.setattr(
        routes_portrait_gallery,
        "persist_feature",
        lambda restored_person, feature: None,
    )

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 90, 170))],
        data={
            "person_id": "p_existing_audit",
            "display_name": "Changed",
            "modality": "body",
        },
    )

    assert response.status_code == 503
    restored = next(
        item for item in GALLERY.values() if item.person_id == "p_existing_audit"
    )
    assert restored.display_name == "Existing"
    assert [feature.feature_id for feature in restored.features] == original_feature_ids
    assert delete_calls == [("default", "p_existing_audit")]
    assert deleted_objects == ["default/gallery-image/files.png"]


def test_gallery_enroll_rollback_failure_redacts_object_cleanup_details(
    monkeypatch,
) -> None:
    client = TestClient(app)
    GALLERY.clear()

    class LeakyFailingObjectStore:
        backend_name = "local_file"

        def put_bytes(self, tenant_id, object_type, filename, data):
            return {
                "backend": self.backend_name,
                "object_key": "tenant/gallery/secret-token.png",
            }

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

    monkeypatch.setattr(
        routes_portrait_gallery, "OBJECT_STORE", LeakyFailingObjectStore()
    )
    monkeypatch.setattr(routes_portrait_gallery, "audit_event", fail_audit)
    monkeypatch.setattr(
        routes_portrait_gallery,
        "persist_person_delete",
        lambda tenant_id, person_id: None,
    )

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={"person_id": "p_cleanup_secret", "modality": "body"},
    )

    assert response.status_code == 500
    assert v1_error_message(response) == "人员库变更失败，且回滚持久化失败"
    assert v1_error_details(response) == {
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
                object_info={
                    "backend": "local_file",
                    "object_key": "default/gallery-image/object.json",
                },
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
    monkeypatch.setattr(
        routes_portrait_gallery,
        "audit_event",
        lambda event, **kwargs: audit_events.append((event, kwargs)),
    )
    monkeypatch.setattr(
        portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None
    )

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


def test_gallery_delete_person_rolls_back_when_object_cleanup_fails(
    monkeypatch,
) -> None:
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
                object_info={
                    "backend": "local_file",
                    "object_key": "default/gallery-image/secret-object.json",
                },
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
    monkeypatch.setattr(
        routes_portrait_gallery, "audit_event", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None
    )
    monkeypatch.setattr(
        routes_portrait_gallery,
        "persist_person_delete",
        lambda tenant_id, person_id: None,
    )
    monkeypatch.setattr(
        routes_portrait_gallery, "persist_person", lambda restored_person: None
    )
    monkeypatch.setattr(
        routes_portrait_gallery,
        "persist_feature",
        lambda restored_person, feature: None,
    )

    response = client.delete("/v1/gallery/p_delete_cleanup_fails")

    assert response.status_code == 503
    assert v1_error_message(response) == "对象清理失败"
    assert gallery_key("default", "p_delete_cleanup_fails") in GALLERY
    assert (
        GALLERY[gallery_key("default", "p_delete_cleanup_fails")]
        .features[0]
        .object_info["object_key"]
        .endswith("secret-object.json")
    )
    assert "secret-object" not in response.text
    assert "object_key" not in response.text


def test_gallery_patch_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={
            "person_id": "p_patch_audit",
            "display_name": "Before",
            "modality": "body",
        },
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
    stored = next(
        person for person in GALLERY.values() if person.person_id == "p_patch_audit"
    )
    assert stored.display_name == "Before"
    assert stored.metadata == {}


def test_gallery_delete_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (10, 80, 180))],
        data={
            "person_id": "p_delete_audit",
            "display_name": "Delete Me",
            "modality": "body",
        },
    )
    assert enroll.status_code == 200

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_gallery, "audit_event", fail_audit)
    monkeypatch.setattr(routes_portrait_gallery, "persist_person", lambda person: None)
    monkeypatch.setattr(
        routes_portrait_gallery, "persist_feature", lambda person, feature: None
    )

    response = client.delete("/v1/gallery/p_delete_audit")

    assert response.status_code == 503
    stored = next(
        person for person in GALLERY.values() if person.person_id == "p_delete_audit"
    )
    assert stored.display_name == "Delete Me"
    assert len(stored.features) == 1


