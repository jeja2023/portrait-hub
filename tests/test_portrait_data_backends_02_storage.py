import json

import pytest
from fastapi import HTTPException

from app import (
    portrait_bootstrap,
    portrait_crypto,
    portrait_gallery,
    portrait_jobs,
    portrait_object_storage,
    portrait_state,
    portrait_streams,
    portrait_thresholds,
    portrait_vector_store,
    server,
)
from app.portrait_gallery import (
    GALLERY,
    add_feature,
    gallery_key,
    patch_person,
    reindex_gallery_vectors,
    upsert_person,
)
from app.portrait_object_storage import LocalObjectStore, object_key_for


def test_local_object_store_escapes_path_segments(
    monkeypatch, workspace_tmp_path
) -> None:
    object_root = workspace_tmp_path / "objects"
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)

    info = LocalObjectStore().put_bytes(
        "tenant/../../x", "../gallery", "../face.png", b"payload"
    )

    assert ".." not in info["object_key"]
    assert "/" in info["object_key"]
    stored_path = (object_root / info["object_key"]).resolve()
    assert stored_path.is_file()
    assert stored_path.relative_to(object_root.resolve())


def test_local_object_store_delete_removes_object(
    monkeypatch, workspace_tmp_path
) -> None:
    object_root = workspace_tmp_path / "objects"
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)
    store = LocalObjectStore()
    info = store.put_bytes("tenant-a", "gallery-image", "face.png", b"payload")
    stored_path = (object_root / info["object_key"]).resolve()

    result = store.delete_object(info)

    if result["deleted"]:
        assert not stored_path.exists()
    else:
        assert result["reason"] == "对象删除失败"
        assert stored_path.is_file()


def test_object_store_records_do_not_include_source_filename(
    monkeypatch, workspace_tmp_path
) -> None:
    object_root = workspace_tmp_path / "objects"
    recorded = []

    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)
    monkeypatch.setattr(portrait_object_storage, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(
        "app.portrait_postgres.insert_object_record",
        lambda tenant_id, info, metadata: recorded.append(metadata),
    )

    LocalObjectStore().put_bytes(
        "tenant-a", "gallery-image", "secret-person-name.png", b"payload"
    )

    assert recorded == [{"object_type": "gallery-image", "filename_provided": True}]
    assert "secret-person-name" not in json.dumps(recorded, ensure_ascii=False)
    assert "filename" not in recorded[0]


def test_local_object_store_falls_back_when_atomic_replace_is_unavailable(
    monkeypatch, workspace_tmp_path
) -> None:
    object_root = workspace_tmp_path / "objects"
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)

    def fail_replace(source, target):
        raise OSError("替换失败")

    monkeypatch.setattr(portrait_object_storage.os, "replace", fail_replace)

    info = LocalObjectStore().put_bytes(
        "tenant-a", "gallery-image", "face.png", b"payload"
    )
    stored_path = (object_root / info["object_key"]).resolve()
    payload = json.loads(stored_path.read_text(encoding="utf-8"))

    assert stored_path.is_file()
    assert payload["data"] == "cGF5bG9hZA=="


def test_local_object_store_encrypts_payload_when_key_is_configured(
    monkeypatch, workspace_tmp_path
) -> None:
    object_root = workspace_tmp_path / "objects"
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY_ID", "active")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEYRING", "")
    monkeypatch.setattr(portrait_crypto, "REQUIRE_ENCRYPTION", True)

    info = LocalObjectStore().put_bytes(
        "tenant-a", "gallery-image", "face.png", b"secret-image-bytes"
    )
    stored_path = (object_root / info["object_key"]).resolve()
    payload = json.loads(stored_path.read_text(encoding="utf-8"))

    assert info["encrypted"] is True
    assert payload["encrypted"] is True
    assert payload["algorithm"] == "aes-256-gcm"
    assert payload["key_id"] == "active"
    assert "secret-image-bytes" not in stored_path.read_text(encoding="utf-8")


def test_object_key_for_is_backend_stable_and_encoded() -> None:
    key = object_key_for("tenant:one", "gallery/image", "face image.png", "a" * 64)

    assert key == f"tenant%3Aone/gallery%2Fimage/aa/{'a' * 64}.png.json"


def test_json_state_write_fails_closed_by_default(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(portrait_state, "STATE_WRITE_FAIL_CLOSED", True)

    def fail_open(*args, **kwargs):
        raise OSError("disk unavailable")

    target = workspace_tmp_path / "state.json"
    monkeypatch.setattr(type(target), "open", fail_open)

    with pytest.raises(HTTPException) as exc_info:
        portrait_state.write_json_state(target, {"ok": True})

    assert exc_info.value.status_code == 503
    assert "状态写入失败" in str(exc_info.value.detail)


def test_json_state_write_can_remain_best_effort(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(portrait_state, "STATE_WRITE_FAIL_CLOSED", False)

    def fail_open(*args, **kwargs):
        raise OSError("disk unavailable")

    target = workspace_tmp_path / "state.json"
    monkeypatch.setattr(type(target), "open", fail_open)

    portrait_state.write_json_state(target, {"ok": True})


def test_json_state_read_fails_closed_when_existing_state_is_malformed(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)
    target = workspace_tmp_path / "state.json"
    target.write_text("{", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        portrait_state.read_json_state(target, {"ok": False})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "状态读取失败"


def test_json_state_read_can_remain_best_effort(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", False)
    target = workspace_tmp_path / "state.json"
    target.write_text("{", encoding="utf-8")

    assert portrait_state.read_json_state(target, {"ok": False}) == {"ok": False}


def test_json_state_read_missing_file_still_uses_default(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)

    assert portrait_state.read_json_state(
        workspace_tmp_path / "missing.json", {"ok": True}
    ) == {"ok": True}


def test_gallery_state_shape_fails_closed(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "gallery.json"
    state_path.write_text('{"people": {}}', encoding="utf-8")
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)
    monkeypatch.setattr("app.portrait_gallery.PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr("app.portrait_gallery.PORTRAIT_GALLERY_STATE_PATH", state_path)

    with pytest.raises(HTTPException) as exc_info:
        portrait_gallery.load_gallery_state()

    assert exc_info.value.status_code == 503


def test_invalid_runtime_state_logs_are_redacted(
    monkeypatch, workspace_tmp_path, caplog
) -> None:
    caplog.set_level("WARNING")
    gallery_path = workspace_tmp_path / "gallery.json"
    jobs_path = workspace_tmp_path / "jobs.json"
    streams_path = workspace_tmp_path / "streams.json"
    gallery_path.write_text(
        json.dumps(
            {
                "people": [
                    {
                        "person_id": "p_bad",
                        "features": [
                            {
                                "feature_id": "f1",
                                "modality": "body",
                                "embedding": ["secret-token"],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    jobs_path.write_text(
        json.dumps({"jobs": [{"job_id": "job_bad", "progress": "secret-token"}]}),
        encoding="utf-8",
    )
    streams_path.write_text(
        json.dumps(
            {
                "streams": [
                    {
                        "stream_id": "str_bad_protected",
                        "stream_url_protected": {
                            "encrypted": True,
                            "algorithm": "secret-token-algorithm",
                            "data": "",
                        },
                    },
                    {"stream_id": "str_bad_state", "created_at": "secret-token"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_GALLERY_STATE_PATH", gallery_path)
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_JOBS_STATE_PATH", jobs_path)
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STREAMS_STATE_PATH", streams_path)

    portrait_gallery.load_gallery_state()
    portrait_jobs.load_video_jobs_state()
    portrait_streams.load_streams_state()

    assert "ValueError" in caplog.text
    assert "secret-token" not in caplog.text
    assert "secret-token-algorithm" not in caplog.text


@pytest.mark.asyncio
async def test_lifespan_loads_portrait_state_once_before_warmup(monkeypatch) -> None:
    calls = []

    def fake_load_state():
        calls.append("state")

    async def fake_warmup_models():
        calls.append("warmup")

    def fake_reload_runtime_config(*, source, include_env):
        calls.append(f"config:{source}:{include_env}")
        return {"source": source}

    monkeypatch.setattr(portrait_bootstrap, "_STATE_LOADED", False)
    monkeypatch.setattr(portrait_bootstrap, "_STATE_LOAD_LOCK", None)
    monkeypatch.setattr(
        portrait_bootstrap, "load_portrait_runtime_state", fake_load_state
    )
    monkeypatch.setattr(server, "reload_runtime_config", fake_reload_runtime_config)
    monkeypatch.setattr(server, "warmup_models", fake_warmup_models)

    async with server.lifespan(server.create_app()):
        assert calls == ["config:startup:True", "state", "warmup"]

    async with server.lifespan(server.create_app()):
        assert calls == [
            "config:startup:True",
            "state",
            "warmup",
            "config:startup:True",
            "warmup",
        ]


def test_video_jobs_state_shape_fails_closed(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "jobs.json"
    state_path.write_text('{"jobs": {}}', encoding="utf-8")
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_JOBS_STATE_PATH", state_path)

    with pytest.raises(HTTPException) as exc_info:
        portrait_jobs.load_video_jobs_state()

    assert exc_info.value.status_code == 503


def test_gallery_vector_failure_logs_are_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    person = portrait_gallery.PersonRecord(
        tenant_id="tenant-secret",
        person_id="p_vector",
        display_name=None,
        metadata={},
    )
    feature = portrait_gallery.FeatureRecord(
        feature_id="f_vector",
        modality="body",
        embedding=[1.0, 0.0],
        embedding_dim=2,
        model_id="model",
        model_version="v1",
        quality_score=1.0,
        source_id="source",
        created_at=0.0,
    )

    class FailingVectorStore:
        def upsert_feature(self, person_payload, feature_payload):
            raise RuntimeError("vector upsert secret-token tenant-secret")

        def delete_person(self, tenant_id, person_id):
            raise RuntimeError("vector delete secret-token tenant-secret")

    monkeypatch.setattr("app.portrait_gallery.save_gallery_state", lambda: None)
    monkeypatch.setattr("app.portrait_vector_store.VECTOR_STORE", FailingVectorStore())

    portrait_gallery.persist_feature(person, feature)
    portrait_gallery.persist_person_delete("tenant-secret", "p_vector")

    assert "RuntimeError" in caplog.text
    for secret in ["secret-token", "tenant-secret"]:
        assert secret not in caplog.text


def test_gallery_feature_state_keeps_private_object_info_but_public_output_redacts() -> (
    None
):
    feature = portrait_gallery.FeatureRecord(
        feature_id="f_object",
        modality="body",
        embedding=[1.0, 0.0],
        embedding_dim=2,
        model_id="model",
        model_version="v1",
        quality_score=1.0,
        source_id="source",
        created_at=0.0,
        object_info={
            "backend": "s3",
            "object_key": "tenant/gallery/secret-object.json",
            "bucket": "secret-bucket",
            "sha256": "secret-sha",
            "bytes": 123,
            "encrypted": True,
        },
    )

    public_payload = feature.public_dict()
    state_payload = feature.state_dict()
    restored = portrait_gallery.FeatureRecord.from_state(state_payload)

    assert public_payload["object"] == {
        "backend": "s3",
        "stored": True,
        "encrypted": True,
    }
    assert "object_key" not in json.dumps(public_payload, ensure_ascii=False)
    assert (
        state_payload["object_info"]["object_key"]
        == "tenant/gallery/secret-object.json"
    )
    assert restored.object_info == state_payload["object_info"]


def test_streams_state_shape_fails_closed(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "streams.json"
    state_path.write_text('{"streams": {}}', encoding="utf-8")
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STREAMS_STATE_PATH", state_path)

    with pytest.raises(HTTPException) as exc_info:
        portrait_streams.load_streams_state()

    assert exc_info.value.status_code == 503


def test_threshold_state_shape_fails_closed(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "thresholds.json"
    state_path.write_text('{"thresholds": []}', encoding="utf-8")
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)
    monkeypatch.setattr(portrait_thresholds, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(
        portrait_thresholds, "PORTRAIT_THRESHOLDS_STATE_PATH", state_path
    )

    with pytest.raises(HTTPException) as exc_info:
        portrait_thresholds.load_threshold_state()

    assert exc_info.value.status_code == 503


def test_gallery_upsert_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    GALLERY.clear()

    def fail_persist(person):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr("app.portrait_gallery.persist_person", fail_persist)

    with pytest.raises(HTTPException):
        upsert_person("p_rollback", "Rollback", tenant_id="tenant-a")

    assert gallery_key("tenant-a", "p_rollback") not in GALLERY


def test_gallery_json_wal_replays_incremental_mutations(
    monkeypatch, workspace_tmp_path
) -> None:
    GALLERY.clear()
    state_path = workspace_tmp_path / "gallery.json"
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_GALLERY_STATE_PATH", state_path)
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_GALLERY_WAL_ENABLED", True)
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_GALLERY_WAL_COMPACT_EVERY", 0)

    person = upsert_person("p_wal", "WAL", tenant_id="tenant-a")
    add_feature(
        person,
        modality="body",
        embedding=[1.0, 0.0],
        model_id="test-model",
        model_version="test",
        quality_score=0.9,
        source_id="source",
    )

    GALLERY.clear()
    portrait_gallery.load_gallery_state()

    restored = GALLERY[gallery_key("tenant-a", "p_wal")]
    assert restored.display_name == "WAL"
    assert len(restored.features) == 1


def test_gallery_feature_wal_entry_is_incremental(
    monkeypatch, workspace_tmp_path
) -> None:
    GALLERY.clear()
    state_path = workspace_tmp_path / "gallery.json"
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_GALLERY_STATE_PATH", state_path)
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_GALLERY_WAL_ENABLED", True)
    monkeypatch.setattr(portrait_gallery, "PORTRAIT_GALLERY_WAL_COMPACT_EVERY", 0)

    person = upsert_person("p_wal_feature", "Feature WAL", tenant_id="tenant-a")
    add_feature(
        person,
        modality="body",
        embedding=[1.0, 0.0, 0.0],
        model_id="test-model",
        model_version="test",
        quality_score=0.9,
        source_id="source",
    )

    wal_entries = [
        json.loads(line)
        for line in state_path.with_suffix(state_path.suffix + ".wal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    feature_entry = wal_entries[-1]
    assert feature_entry["op"] == "upsert_feature"
    assert len(feature_entry["person"]["features"]) == 0
    assert feature_entry["feature"]["embedding"] == [1.0, 0.0, 0.0]

    GALLERY.clear()
    portrait_gallery.load_gallery_state()

    restored = GALLERY[gallery_key("tenant-a", "p_wal_feature")]
    assert restored.display_name == "Feature WAL"
    assert [feature.embedding for feature in restored.features] == [[1.0, 0.0, 0.0]]


def test_gallery_patch_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    GALLERY.clear()
    person = upsert_person(
        "p_patch", "Before", metadata={"note": "old"}, tenant_id="tenant-a"
    )

    def fail_persist(updated_person):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr("app.portrait_gallery.persist_person", fail_persist)

    with pytest.raises(HTTPException):
        patch_person(
            "p_patch",
            {"display_name": "After", "metadata": {"note": "new"}},
            tenant_id="tenant-a",
        )

    stored = GALLERY[gallery_key("tenant-a", "p_patch")]
    assert stored.display_name == "Before"
    assert stored.metadata == {"note": "old"}
    assert person.display_name == "Before"


def test_gallery_feature_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    GALLERY.clear()
    person = upsert_person("p_feature", "Feature", tenant_id="tenant-a")

    def fail_persist_feature(updated_person, feature):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr("app.portrait_gallery.persist_feature", fail_persist_feature)

    with pytest.raises(HTTPException):
        add_feature(
            person,
            modality="body",
            embedding=[1.0, 0.0],
            model_id="test-model",
            model_version="test",
            quality_score=0.9,
            source_id="source",
        )

    stored = GALLERY[gallery_key("tenant-a", "p_feature")]
    assert stored.features == []
    assert person.features == []


def test_gallery_reindex_vectors_counts_partial_failures(monkeypatch) -> None:
    GALLERY.clear()
    person = portrait_gallery.PersonRecord(
        tenant_id="tenant-a",
        person_id="p_reindex_domain",
        display_name=None,
        metadata={},
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_reindex_ok",
                modality="body",
                embedding=[1.0, 0.0],
                embedding_dim=2,
                model_id="model-a",
                model_version="v1",
                quality_score=0.9,
                source_id="source-ok",
                created_at=0.0,
            ),
            portrait_gallery.FeatureRecord(
                feature_id="f_reindex_fail",
                modality="body",
                embedding=[0.0, 1.0],
                embedding_dim=2,
                model_id="model-a",
                model_version="v1",
                quality_score=0.8,
                source_id="source-fail",
                created_at=0.0,
            ),
            portrait_gallery.FeatureRecord(
                feature_id="f_reindex_empty",
                modality="body",
                embedding=[],
                embedding_dim=0,
                model_id="model-a",
                model_version="v1",
                quality_score=0.1,
                source_id="source-empty",
                created_at=0.0,
            ),
        ],
    )
    GALLERY[gallery_key("tenant-a", person.person_id)] = person
    upserted = []

    class PartiallyFailingVectorStore:
        backend_name = "partial"

        def upsert_feature(self, person_payload, feature_payload):
            if feature_payload["feature_id"] == "f_reindex_fail":
                raise RuntimeError("向量写入失败")
            upserted.append(
                (person_payload["person_id"], feature_payload["feature_id"])
            )
            return {"backend": self.backend_name, "status": "upserted"}

    monkeypatch.setattr(
        portrait_vector_store, "VECTOR_STORE", PartiallyFailingVectorStore()
    )

    result = reindex_gallery_vectors(
        tenant_id="tenant-a", modality="body", model_id="model-a"
    )

    assert result["status"] == "partial_failure"
    assert result["vector_backend"] == "partial"
    assert result["person_count"] == 1
    assert result["feature_count"] == 3
    assert result["matched_feature_count"] == 3
    assert result["reindexed_feature_count"] == 1
    assert result["failed_feature_count"] == 1
    assert result["error_count"] == 1
    assert result["skipped_feature_count"] == 1
    assert result["skip_reasons"] == {"embedding_missing": 1}
    assert upserted == [("p_reindex_domain", "f_reindex_ok")]


def test_threshold_update_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    snapshot = portrait_thresholds.threshold_snapshot()

    def fail_save():
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_thresholds, "save_threshold_state", fail_save)

    with pytest.raises(HTTPException):
        portrait_thresholds.update_threshold_profile("normal", {"body": 0.11})

    assert portrait_thresholds.threshold_snapshot() == snapshot


