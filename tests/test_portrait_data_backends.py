import asyncio
import json

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from app import runtime_execution
from app import portrait_audit
from app import portrait_gallery
from app import portrait_bootstrap
from app import portrait_object_storage
from app import portrait_jobs
from app import portrait_model_capabilities
from app import portrait_state
from app import portrait_stream_worker
from app import portrait_stream_worker_daemon
from tools import portrait_stream_worker_health
from app import portrait_streams
from app import portrait_thresholds
from app import portrait_crypto
from app import portrait_vector_store
from app import server
from app.portrait_gallery import GALLERY, add_feature, gallery_key, patch_person, reindex_gallery_vectors, upsert_person
from app.portrait_jobs import VIDEO_JOBS, VideoJob, create_video_job, job_key, request_cancel_video_job
from app.portrait_jobs import run_video_job
from app.portrait_object_storage import LocalObjectStore, S3ObjectStore, object_key_for
from app.portrait_postgres import postgres_health, vector_literal
from app.portrait_streams import STREAMS, create_stream, start_stream, stop_stream, stream_key
from app.portrait_task_queue import TASK_MESSAGES, LocalTaskQueue, RedisTaskQueue
from app.portrait_vector_store import PgvectorVectorStore, QdrantVectorStore


def test_postgres_health_is_safe_without_external_database() -> None:
    health = postgres_health()

    assert health["configured"] is False
    assert health["status"] == "not_ready"


def test_pgvector_literal_is_finite_and_stable() -> None:
    assert vector_literal([1, 0.25, float("nan"), float("inf")]) == "[1,0.25,0,0]"


def test_production_backend_health_contracts_are_import_safe() -> None:
    assert PgvectorVectorStore().health()["backend"] == "pgvector"

    qdrant = QdrantVectorStore().health()
    assert qdrant["backend"] == "qdrant"
    assert "driver_available" in qdrant

    s3 = S3ObjectStore().health()
    assert s3["backend"] == "s3"
    assert "driver_available" in s3

    redis = RedisTaskQueue().health()
    assert redis["backend"] == "redis"
    assert "driver_available" in redis


def test_model_capability_normalization_exposes_real_model_adapter_contract() -> None:
    capabilities = portrait_model_capabilities.normalize_capabilities(
        {
            "face_embedding": {
                "status": "ready",
                "model_id": "portrait/arcface_r100.onnx",
                "adapter": "arcface",
            },
            "gait": {
                "status": "ready",
                "model_id": "portrait/opengait.onnx",
                "adapter": "opengait",
            },
            "body_embedding": {
                "status": "ready",
                "model_id": "portrait/osnet.onnx",
                "adapter": "reid",
            },
        }
    )

    assert capabilities["face_embedding"]["embedding_dim"] == 512
    assert capabilities["face_embedding"]["input_size"] == [112, 112]
    assert capabilities["gait"]["sequence_input"] is True
    assert capabilities["body_embedding"]["embedding_dim"] == 512
    assert capabilities["body_embedding"]["input_size"] == [256, 128]


def test_object_store_health_redacts_backend_locations(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", workspace_tmp_path / "secret-objects")
    local = LocalObjectStore().health()

    monkeypatch.setattr(portrait_object_storage, "S3_BUCKET", "secret-bucket")
    monkeypatch.setattr(portrait_object_storage, "S3_ENDPOINT_URL", "https://storage.internal")
    monkeypatch.setattr(portrait_object_storage, "S3_REGION", "us-test-1")
    s3 = S3ObjectStore().health()

    assert "path" not in local
    assert local["storage_dir_configured"] is True
    assert "secret-objects" not in json.dumps(local, ensure_ascii=False)
    assert "bucket" not in s3
    assert "endpoint" not in s3
    assert "region" not in s3
    assert s3["bucket_configured"] is True
    assert s3["endpoint_configured"] is True
    assert s3["region_configured"] is True
    encoded = json.dumps(s3, ensure_ascii=False)
    assert "secret-bucket" not in encoded
    assert "storage.internal" not in encoded


def test_object_store_delete_failures_are_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")

    def fail_local_path(object_key):
        raise RuntimeError(f"secret local object path for {object_key}")

    class FailingS3Client:
        def delete_object(self, *args, **kwargs):
            raise RuntimeError("secret S3 delete token for secret-bucket")

    class FailingBoto3:
        def client(self, *args, **kwargs):
            return FailingS3Client()

    monkeypatch.setattr(portrait_object_storage, "local_object_path", fail_local_path)
    monkeypatch.setattr(portrait_object_storage, "S3_BUCKET", "secret-bucket")
    monkeypatch.setattr(portrait_object_storage, "boto3", FailingBoto3())

    local = LocalObjectStore().delete_object({"object_key": "tenant-a/gallery-image/secret-face.png"})
    s3 = S3ObjectStore().delete_object({"object_key": "tenant-a/gallery-image/secret-face.png"})

    assert local == {"backend": "local_file", "deleted": False, "reason": "对象删除失败"}
    assert s3 == {"backend": "s3", "deleted": False, "reason": "对象删除失败"}
    encoded = json.dumps({"local": local, "s3": s3}, ensure_ascii=False)
    assert "secret-face" not in encoded
    assert "secret-bucket" not in encoded
    assert "object_key" not in encoded
    assert "error" not in encoded
    assert "secret-face" not in caplog.text
    assert "secret-bucket" not in caplog.text
    assert "secret local object path" not in caplog.text


def test_state_file_failure_logs_are_redacted(monkeypatch, workspace_tmp_path, caplog) -> None:
    caplog.set_level("WARNING")
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", False)
    monkeypatch.setattr(portrait_state, "STATE_WRITE_FAIL_CLOSED", False)

    secret_dir = workspace_tmp_path / "secret-tenant-path"
    read_target = secret_dir / "read-secret-state.json"
    append_target = secret_dir / "append-secret-audit.jsonl"
    write_target = secret_dir / "write-secret-state.json"
    read_target.parent.mkdir(parents=True, exist_ok=True)
    read_target.write_text("{}", encoding="utf-8")

    original_open = type(read_target).open

    def fail_open(self, *args, **kwargs):
        if self in {read_target, append_target}:
            raise OSError(f"secret-token leaked through exception for {self}")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(read_target), "open", fail_open)

    assert portrait_state.read_json_state(read_target, {"ok": False}) == {"ok": False}
    portrait_state.handle_state_write_error(write_target, OSError(f"secret-write-token {write_target}"))
    portrait_state.append_jsonl(append_target, {"event": "secret"}, fail_closed=False)

    assert "path_hash=" in caplog.text
    assert "OSError" in caplog.text
    for secret in [
        "secret-tenant-path",
        "read-secret-state",
        "append-secret-audit",
        "write-secret-state",
        "secret-token",
        "secret-write-token",
        str(read_target),
        str(append_target),
        str(write_target),
    ]:
        assert secret not in caplog.text


def test_backend_health_errors_are_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    from app import portrait_postgres
    from app import portrait_task_queue

    class FailingPsycopg:
        def connect(self, *args, **kwargs):
            raise RuntimeError("postgres password=secret-password host=db.internal")

    class FailingRedisClient:
        def ping(self):
            raise RuntimeError("redis://:secret-token@redis.internal/0")

    class FailingRedisModule:
        class Redis:
            @staticmethod
            def from_url(*args, **kwargs):
                return FailingRedisClient()

    monkeypatch.setattr(portrait_postgres, "POSTGRES_DSN", "postgres://user:secret-password@db.internal/app")
    monkeypatch.setattr(portrait_postgres, "psycopg", FailingPsycopg())
    monkeypatch.setattr(portrait_task_queue, "REDIS_URL", "redis://:secret-token@redis.internal/0")
    monkeypatch.setattr(portrait_task_queue, "redis", FailingRedisModule())

    postgres = portrait_postgres.postgres_health()
    redis = portrait_task_queue.RedisTaskQueue().health()

    assert postgres["status"] == "error"
    assert postgres["error"] == "健康检查失败"
    assert redis["status"] == "error"
    assert redis["error"] == "健康检查失败"
    encoded = json.dumps({"postgres": postgres, "redis": redis}, ensure_ascii=False)
    assert "secret-password" not in encoded
    assert "secret-token" not in encoded
    assert "db.internal" not in encoded
    assert "redis.internal" not in encoded
    assert "RuntimeError" in caplog.text
    for secret in ["secret-password", "secret-token", "db.internal", "redis.internal"]:
        assert secret not in caplog.text


def test_audit_payload_redacts_reserved_fields_and_stays_bounded(monkeypatch) -> None:
    monkeypatch.setattr(portrait_audit, "MAX_AUDIT_PAYLOAD_BYTES", 1024)
    monkeypatch.setattr(portrait_audit, "MAX_AUDIT_STRING_LENGTH", 64)
    monkeypatch.setattr(portrait_audit, "MAX_AUDIT_LIST_ITEMS", 3)
    monkeypatch.setattr(portrait_audit, "MAX_AUDIT_DEPTH", 3)
    monkeypatch.setattr(portrait_audit, "MAX_AUDIT_KEYS", 12)
    nested: dict[str, object] = {"child": {}}
    nested["child"] = {"child": {"child": {"password": "nested-secret"}}}
    payload = portrait_audit.build_audit_payload(
        "gallery_update",
        request_id="req-1",
        tenant_id="tenant-a",
        outcome="success",
        fields={
            "event": "spoofed",
            "audit_hash": "spoofed-hash",
            "token": "secret-token",
            "metadata": {
                "api_key": "secret-key",
                "notes": "x" * 500,
                "items": [1, 2, 3, 4, 5],
                "nested": nested,
            },
        },
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    assert len(encoded) <= 1024
    assert payload["event"] == "gallery_update"
    assert payload["field_event"] == "spoofed"
    assert payload["field_audit_hash"] == "spoofed-hash"
    assert "audit_hash" not in payload
    assert payload["token"] == "<redacted>"
    assert payload["metadata"]["api_key"] == "<redacted>"
    assert len(payload["metadata"]["items"]) == 3
    assert payload["audit_truncated"] is True
    assert "secret-token" not in encoded.decode("utf-8")
    assert "secret-key" not in encoded.decode("utf-8")
    assert "nested-secret" not in encoded.decode("utf-8")


def test_audit_payload_hash_chain_detects_tampering() -> None:
    first = portrait_audit.seal_audit_payload(
        portrait_audit.build_audit_payload(
            "gallery_update",
            request_id="req-1",
            tenant_id="tenant-a",
            outcome="success",
            fields={"person_id": "p1"},
        ),
        None,
    )
    second = portrait_audit.seal_audit_payload(
        portrait_audit.build_audit_payload(
            "gallery_delete",
            request_id="req-2",
            tenant_id="tenant-a",
            outcome="success",
            fields={"person_id": "p1"},
        ),
        first["audit_hash"],
    )
    tampered = dict(first)
    tampered["event"] = "gallery_delete"

    assert first["audit_hash_algorithm"] == "sha256-canonical-json-v1"
    assert first["audit_hash"] == portrait_audit.audit_payload_hash(first)
    assert second["audit_prev_hash"] == first["audit_hash"]
    assert portrait_audit.audit_payload_hash(tampered) != first["audit_hash"]


def test_audit_event_writes_tamper_evident_jsonl_chain(monkeypatch, workspace_tmp_path) -> None:
    audit_path = workspace_tmp_path / "audit.jsonl"
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", audit_path)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", True)

    portrait_audit.audit_event("gallery_update", request_id="req-1", tenant_id="tenant-a")
    portrait_audit.audit_event("gallery_delete", request_id="req-2", tenant_id="tenant-a")

    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

    assert len(records) == 2
    assert records[0]["audit_prev_hash"] is None
    assert records[0]["audit_hash"] == portrait_audit.audit_payload_hash(records[0])
    assert records[1]["audit_prev_hash"] == records[0]["audit_hash"]
    assert records[1]["audit_hash"] == portrait_audit.audit_payload_hash(records[1])
    assert portrait_audit.last_audit_hash(audit_path) == records[1]["audit_hash"]


def test_audit_event_fails_closed_when_existing_jsonl_chain_is_unreadable(monkeypatch, workspace_tmp_path) -> None:
    audit_path = workspace_tmp_path / "audit.jsonl"
    audit_path.write_text("{not-json}\n", encoding="utf-8")
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", audit_path)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", True)

    with pytest.raises(HTTPException) as exc_info:
        portrait_audit.audit_event("gallery_update", request_id="req-1", tenant_id="tenant-a")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "审计链不可用"
    assert audit_path.read_text(encoding="utf-8") == "{not-json}\n"


def test_audit_event_fails_closed_when_jsonl_write_fails(monkeypatch) -> None:
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", True)

    def fail_append(path, payload, *, fail_closed=False):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_audit, "append_jsonl", fail_append)

    with pytest.raises(HTTPException) as exc_info:
        portrait_audit.audit_event("management_change", request_id="req-1", tenant_id="tenant-a")

    assert exc_info.value.status_code == 503


def test_audit_event_fails_closed_when_postgres_audit_fails(monkeypatch) -> None:
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", True)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(portrait_audit, "append_jsonl", lambda path, payload, *, fail_closed=False: None)

    def fail_insert(payload):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("app.portrait_postgres.insert_audit_event", fail_insert)

    with pytest.raises(RuntimeError):
        portrait_audit.audit_event("management_change", request_id="req-1", tenant_id="tenant-a")


def test_audit_event_postgres_failure_logs_are_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", False)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(portrait_audit, "append_jsonl", lambda path, payload, *, fail_closed=False: None)

    def fail_insert(payload):
        raise RuntimeError("postgres://user:secret-password@db.internal/app tenant-secret")

    monkeypatch.setattr("app.portrait_postgres.insert_audit_event", fail_insert)

    portrait_audit.audit_event(
        "management_change",
        request_id="req-1",
        tenant_id="tenant-secret",
        token="secret-token",
    )

    assert "RuntimeError" in caplog.text
    for secret in ["secret-password", "db.internal", "tenant-secret", "secret-token"]:
        assert secret not in caplog.text


def test_vector_backend_fallback_logs_are_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    records = [{"tenant_id": "tenant-a", "person_id": "p1", "embedding": [1.0, 0.0]}]

    def fail_pgvector(*args, **kwargs):
        raise RuntimeError("pgvector secret-token host=db.internal")

    def fail_qdrant_client(self):
        raise RuntimeError("qdrant secret-token url=qdrant.internal")

    monkeypatch.setattr("app.portrait_postgres.search_pgvector", fail_pgvector)
    monkeypatch.setattr(portrait_vector_store, "qdrant_models", object())
    monkeypatch.setattr(portrait_vector_store.QdrantVectorStore, "_client", fail_qdrant_client)

    pgvector = PgvectorVectorStore().search(
        [1.0, 0.0],
        records,
        modality="body",
        threshold_profile="normal",
        top_k=1,
        tenant_id="tenant-secret",
    )
    qdrant = QdrantVectorStore().search(
        [1.0, 0.0],
        records,
        modality="body",
        threshold_profile="normal",
        top_k=1,
        tenant_id="tenant-secret",
    )

    assert pgvector[0]["person_id"] == "p1"
    assert qdrant[0]["person_id"] == "p1"
    assert "RuntimeError" in caplog.text
    for secret in ["secret-token", "db.internal", "qdrant.internal", "tenant-secret"]:
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_batch_inference_fallback_log_is_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    calls = 0

    async def fake_run_model_bundle(bundle, input_array):
        nonlocal calls
        calls += 1
        if input_array.shape[0] > 1:
            raise RuntimeError("GPU OOM secret-token device=/dev/secret-gpu")
        return [np.zeros((input_array.shape[0], 1), dtype=np.float32)], 0.0, 0.0

    monkeypatch.setattr(runtime_execution, "run_model_bundle", fake_run_model_bundle)

    outputs, queue_seconds, inference_seconds, mode = await runtime_execution.run_yolo_frames(
        {},
        np.zeros((2, 1), dtype=np.float32),
    )

    assert calls == 3
    assert mode == "per_frame"
    assert queue_seconds == 0.0
    assert inference_seconds == 0.0
    assert outputs[0].shape == (2, 1)
    assert "RuntimeError" in caplog.text
    for secret in ["secret-token", "/dev/secret-gpu"]:
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_generic_batch_inference_counts_batched_items(monkeypatch) -> None:
    bundle = {"session": object(), "lock": asyncio.Lock(), "inference_count": 0, "gpu_device_id": 0}

    def fake_run_session(session, input_array):
        return [np.ones((input_array.shape[0], 2), dtype=np.float32)]

    monkeypatch.setattr(runtime_execution, "run_session", fake_run_session)

    outputs, queue_seconds, inference_seconds, mode = await runtime_execution.run_model_bundle_batch(
        bundle,
        [np.zeros((1, 3), dtype=np.float32), np.zeros((1, 3), dtype=np.float32)],
    )

    assert mode == "batch"
    assert outputs[0].shape == (2, 2)
    assert queue_seconds >= 0
    assert inference_seconds >= 0
    assert bundle["inference_count"] == 2


def test_local_object_store_escapes_path_segments(monkeypatch, workspace_tmp_path) -> None:
    object_root = workspace_tmp_path / "objects"
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)

    info = LocalObjectStore().put_bytes("tenant/../../x", "../gallery", "../face.png", b"payload")

    assert ".." not in info["object_key"]
    assert "/" in info["object_key"]
    stored_path = (object_root / info["object_key"]).resolve()
    assert stored_path.is_file()
    assert stored_path.relative_to(object_root.resolve())


def test_local_object_store_delete_removes_object(monkeypatch, workspace_tmp_path) -> None:
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


def test_object_store_records_do_not_include_source_filename(monkeypatch, workspace_tmp_path) -> None:
    object_root = workspace_tmp_path / "objects"
    recorded = []

    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)
    monkeypatch.setattr(portrait_object_storage, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr("app.portrait_postgres.insert_object_record", lambda tenant_id, info, metadata: recorded.append(metadata))

    LocalObjectStore().put_bytes("tenant-a", "gallery-image", "secret-person-name.png", b"payload")

    assert recorded == [{"object_type": "gallery-image", "filename_provided": True}]
    assert "secret-person-name" not in json.dumps(recorded, ensure_ascii=False)
    assert "filename" not in recorded[0]


def test_local_object_store_falls_back_when_atomic_replace_is_unavailable(monkeypatch, workspace_tmp_path) -> None:
    object_root = workspace_tmp_path / "objects"
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)

    def fail_replace(source, target):
        raise OSError("替换失败")

    monkeypatch.setattr(portrait_object_storage.os, "replace", fail_replace)

    info = LocalObjectStore().put_bytes("tenant-a", "gallery-image", "face.png", b"payload")
    stored_path = (object_root / info["object_key"]).resolve()
    payload = json.loads(stored_path.read_text(encoding="utf-8"))

    assert stored_path.is_file()
    assert payload["data"] == "cGF5bG9hZA=="


def test_local_object_store_encrypts_payload_when_key_is_configured(monkeypatch, workspace_tmp_path) -> None:
    object_root = workspace_tmp_path / "objects"
    monkeypatch.setattr(portrait_object_storage, "OBJECT_STORAGE_DIR", object_root)
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY_ID", "active")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEYRING", "")
    monkeypatch.setattr(portrait_crypto, "REQUIRE_ENCRYPTION", True)

    info = LocalObjectStore().put_bytes("tenant-a", "gallery-image", "face.png", b"secret-image-bytes")
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


def test_json_state_write_fails_closed_by_default(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(portrait_state, "STATE_WRITE_FAIL_CLOSED", True)

    def fail_open(*args, **kwargs):
        raise OSError("disk unavailable")

    target = workspace_tmp_path / "state.json"
    monkeypatch.setattr(type(target), "open", fail_open)

    with pytest.raises(HTTPException) as exc_info:
        portrait_state.write_json_state(target, {"ok": True})

    assert exc_info.value.status_code == 503
    assert "状态写入失败" in str(exc_info.value.detail)


def test_json_state_write_can_remain_best_effort(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(portrait_state, "STATE_WRITE_FAIL_CLOSED", False)

    def fail_open(*args, **kwargs):
        raise OSError("disk unavailable")

    target = workspace_tmp_path / "state.json"
    monkeypatch.setattr(type(target), "open", fail_open)

    portrait_state.write_json_state(target, {"ok": True})


def test_json_state_read_fails_closed_when_existing_state_is_malformed(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)
    target = workspace_tmp_path / "state.json"
    target.write_text("{", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        portrait_state.read_json_state(target, {"ok": False})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "状态读取失败"


def test_json_state_read_can_remain_best_effort(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", False)
    target = workspace_tmp_path / "state.json"
    target.write_text("{", encoding="utf-8")

    assert portrait_state.read_json_state(target, {"ok": False}) == {"ok": False}


def test_json_state_read_missing_file_still_uses_default(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)

    assert portrait_state.read_json_state(workspace_tmp_path / "missing.json", {"ok": True}) == {"ok": True}


def test_gallery_state_shape_fails_closed(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "gallery.json"
    state_path.write_text('{"people": {}}', encoding="utf-8")
    monkeypatch.setattr(portrait_state, "STATE_READ_FAIL_CLOSED", True)
    monkeypatch.setattr("app.portrait_gallery.PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr("app.portrait_gallery.PORTRAIT_GALLERY_STATE_PATH", state_path)

    with pytest.raises(HTTPException) as exc_info:
        portrait_gallery.load_gallery_state()

    assert exc_info.value.status_code == 503


def test_invalid_runtime_state_logs_are_redacted(monkeypatch, workspace_tmp_path, caplog) -> None:
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
                        "features": [{"feature_id": "f1", "modality": "body", "embedding": ["secret-token"]}],
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
    monkeypatch.setattr(portrait_bootstrap, "load_portrait_runtime_state", fake_load_state)
    monkeypatch.setattr(server, "reload_runtime_config", fake_reload_runtime_config)
    monkeypatch.setattr(server, "warmup_models", fake_warmup_models)

    async with server.lifespan(server.create_app()):
        assert calls == ["config:startup:True", "state", "warmup"]

    async with server.lifespan(server.create_app()):
        assert calls == ["config:startup:True", "state", "warmup", "config:startup:True", "warmup"]


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


def test_gallery_feature_state_keeps_private_object_info_but_public_output_redacts() -> None:
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

    assert public_payload["object"] == {"backend": "s3", "stored": True, "encrypted": True}
    assert "object_key" not in json.dumps(public_payload, ensure_ascii=False)
    assert state_payload["object_info"]["object_key"] == "tenant/gallery/secret-object.json"
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
    monkeypatch.setattr(portrait_thresholds, "PORTRAIT_THRESHOLDS_STATE_PATH", state_path)

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


def test_gallery_json_wal_replays_incremental_mutations(monkeypatch, workspace_tmp_path) -> None:
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


def test_gallery_feature_wal_entry_is_incremental(monkeypatch, workspace_tmp_path) -> None:
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
        for line in state_path.with_suffix(state_path.suffix + ".wal.jsonl").read_text(encoding="utf-8").splitlines()
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
    person = upsert_person("p_patch", "Before", metadata={"note": "old"}, tenant_id="tenant-a")

    def fail_persist(updated_person):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr("app.portrait_gallery.persist_person", fail_persist)

    with pytest.raises(HTTPException):
        patch_person("p_patch", {"display_name": "After", "metadata": {"note": "new"}}, tenant_id="tenant-a")

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
            upserted.append((person_payload["person_id"], feature_payload["feature_id"]))
            return {"backend": self.backend_name, "status": "upserted"}

    monkeypatch.setattr(portrait_vector_store, "VECTOR_STORE", PartiallyFailingVectorStore())

    result = reindex_gallery_vectors(tenant_id="tenant-a", modality="body", model_id="model-a")

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


def test_video_job_create_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    VIDEO_JOBS.clear()

    def fail_persist(job):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_jobs, "persist_video_job", fail_persist)

    with pytest.raises(HTTPException):
        create_video_job("video.mp4", tenant_id="tenant-a")

    assert VIDEO_JOBS == {}


def test_video_job_cancel_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_cancel", tenant_id="tenant-a", filename="video.mp4", status="queued")
    VIDEO_JOBS[job_key("tenant-a", "job_cancel")] = job

    def fail_persist(updated_job):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_jobs, "persist_video_job", fail_persist)

    with pytest.raises(HTTPException):
        request_cancel_video_job("job_cancel", tenant_id="tenant-a")

    stored = VIDEO_JOBS[job_key("tenant-a", "job_cancel")]
    assert stored.status == "queued"
    assert stored.cancel_requested is False


def test_video_jobs_json_state_round_trip_omits_source_filename(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "jobs.json"
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_JOBS_STATE_PATH", state_path)
    VIDEO_JOBS.clear()

    created = create_video_job("secret-person-name.mp4", tenant_id="tenant-a")
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    VIDEO_JOBS.clear()
    portrait_jobs.load_video_jobs_state()

    assert VIDEO_JOBS[job_key("tenant-a", created.job_id)].filename is None
    assert "filename" not in persisted["jobs"][0]
    assert "secret-person-name" not in json.dumps(persisted, ensure_ascii=False)


def test_video_job_public_error_redacts_legacy_state() -> None:
    job = VideoJob(
        job_id="job_legacy_error",
        tenant_id="tenant-a",
        filename="video.mp4",
        status="failed",
        error="secret-token leaked from old exception",
    )

    assert job.public_dict()["error"] == "视频任务失败"
    assert job.state_dict()["error"] == "视频任务失败"

    restored = VideoJob.from_state(job.state_dict() | {"error": "secret-token from old state"})
    assert restored.error == "视频任务失败"
    assert restored.public_dict()["error"] == "视频任务失败"


def test_video_job_public_and_state_payloads_do_not_include_filename() -> None:
    job = VideoJob(
        job_id="job_filename_redaction",
        tenant_id="tenant-a",
        filename="secret-person-name.mp4",
        status="completed",
        result={
            "metadata": {"filename": "secret-person-name.mp4", "frame_count": 1},
            "frames": [],
            "frame_count": 0,
        },
    )

    public_payload = job.public_dict(include_result=True)
    state_payload = job.state_dict()
    restored = VideoJob.from_state(
        {
            "job_id": "job_legacy_filename",
            "tenant_id": "tenant-a",
            "filename": "legacy-secret-person-name.mp4",
            "result": {"metadata": {"filename": "legacy-secret-person-name.mp4"}},
        }
    )

    assert "filename" not in job.public_dict()
    assert "filename" not in public_payload["result"]["metadata"]
    assert "filename" not in state_payload
    assert "filename" not in state_payload["result"]["metadata"]
    assert "legacy-secret-person-name" not in json.dumps(restored.public_dict(include_result=True), ensure_ascii=False)
    assert "legacy-secret-person-name" not in json.dumps(restored.state_dict(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_run_video_job_is_tenant_scoped(monkeypatch) -> None:
    VIDEO_JOBS.clear()
    VIDEO_JOBS[job_key("tenant-a", "job_same")] = VideoJob(job_id="job_same", tenant_id="tenant-a", filename="a.mp4")
    VIDEO_JOBS[job_key("tenant-b", "job_same")] = VideoJob(job_id="job_same", tenant_id="tenant-b", filename="b.mp4")

    async def fake_extract_video_frames_from_bytes(data, filename, frame_interval, max_frames):
        return [Image.new("RGB", (8, 8), (20, 40, 60))], {
            "source_frame_indexes": [0],
            "filename": "secret-person-name.mp4",
            "video_bytes": 12345,
            "frame_fingerprints": [{"sha256": "secret-sha", "average_hash": "secret-average"}],
        }

    monkeypatch.setattr("app.portrait_jobs.extract_video_frames_from_bytes", fake_extract_video_frames_from_bytes)
    monkeypatch.setattr("app.portrait_jobs.assess_image_quality", lambda image: {"score": 0.9})
    monkeypatch.setattr("app.portrait_jobs.appearance_record", lambda image, include_embedding=False: {"quality": {"score": 0.9}})
    monkeypatch.setattr("app.portrait_jobs.persist_video_job", lambda job: None)

    await run_video_job("job_same", "tenant-b", b"video", "b.mp4", 1, 1)

    assert VIDEO_JOBS[job_key("tenant-a", "job_same")].status == "queued"
    assert VIDEO_JOBS[job_key("tenant-b", "job_same")].status == "completed"
    frame = VIDEO_JOBS[job_key("tenant-b", "job_same")].result["frames"][0]
    assert frame["embedding_dim"] == 64
    assert frame["thumbnail"].startswith("data:image/jpeg;base64,")
    assert "embedding" not in frame
    assert "filename" not in VIDEO_JOBS[job_key("tenant-b", "job_same")].result["metadata"]
    assert "video_bytes" not in VIDEO_JOBS[job_key("tenant-b", "job_same")].result["metadata"]
    assert "frame_fingerprints" not in VIDEO_JOBS[job_key("tenant-b", "job_same")].result["metadata"]
    assert "secret-person-name" not in json.dumps(VIDEO_JOBS[job_key("tenant-b", "job_same")].result, ensure_ascii=False)
    assert "secret-sha" not in json.dumps(VIDEO_JOBS[job_key("tenant-b", "job_same")].result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_run_video_job_failure_error_is_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_failed", tenant_id="tenant-secret", filename="secret-video.mp4")
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
    persisted = []

    async def fail_extract_video_frames_from_bytes(data, filename, frame_interval, max_frames):
        raise RuntimeError(f"secret-token leaked through video decode for {filename}")

    monkeypatch.setattr("app.portrait_jobs.extract_video_frames_from_bytes", fail_extract_video_frames_from_bytes)
    monkeypatch.setattr("app.portrait_jobs.persist_video_job", lambda persisted_job: persisted.append(persisted_job.state_dict()))

    await run_video_job(job.job_id, job.tenant_id, b"video", "secret-video.mp4", 1, 1)

    assert job.status == "failed"
    assert job.error == "视频任务失败"
    assert persisted[-1]["status"] == "failed"
    assert persisted[-1]["error"] == "视频任务失败"
    assert "filename" not in persisted[-1]
    assert "secret-video" not in json.dumps(persisted[-1], ensure_ascii=False)
    assert "RuntimeError" in caplog.text
    for secret in ["secret-token", "secret-video", "tenant-secret", "job_failed"]:
        assert secret not in caplog.text
        assert secret not in str(job.public_dict()["error"])
        assert secret not in str(persisted[-1]["error"])


@pytest.mark.asyncio
async def test_run_video_job_retries_and_persists_progress(monkeypatch) -> None:
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_retry", tenant_id="tenant-a", filename=None, max_retries=1)
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
    calls = {"count": 0}
    persisted = []

    async def flaky_extract_video_frames_from_bytes(data, filename, frame_interval, max_frames):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("临时解码失败")
        return [Image.new("RGB", (8, 8), (20, 40, 60))], {"source_frame_indexes": [0]}

    monkeypatch.setattr("app.portrait_jobs.VIDEO_JOB_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("app.portrait_jobs.extract_video_frames_from_bytes", flaky_extract_video_frames_from_bytes)
    monkeypatch.setattr("app.portrait_jobs.assess_image_quality", lambda image: {"score": 0.9})
    monkeypatch.setattr("app.portrait_jobs.appearance_record", lambda image, include_embedding=False: {"quality": {"score": 0.9}})
    monkeypatch.setattr("app.portrait_jobs.persist_video_job", lambda persisted_job: persisted.append(persisted_job.state_dict()))

    await run_video_job(job.job_id, job.tenant_id, b"video", "video.mp4", 1, 1)

    assert calls["count"] == 2
    assert job.status == "completed"
    assert job.attempts == 2
    assert any(item["status"] == "queued" and item["next_retry_at"] is not None for item in persisted)
    assert persisted[-1]["progress"] == 1.0


@pytest.mark.asyncio
async def test_run_video_job_progress_persistence_stays_lightweight(monkeypatch) -> None:
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_light_progress", tenant_id="tenant-a", filename=None)
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
    persisted = []

    async def fake_extract_video_frames_from_bytes(data, filename, frame_interval, max_frames):
        return [
            Image.new("RGB", (8, 8), (20, 40, 60)),
            Image.new("RGB", (8, 8), (30, 50, 70)),
        ], {"source_frame_indexes": [0, 1]}

    def capture_persist(persisted_job, *, lightweight_result=False):
        persisted.append(persisted_job.state_dict(lightweight_result=lightweight_result))

    monkeypatch.setattr("app.portrait_jobs.VIDEO_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.portrait_jobs.extract_video_frames_from_bytes", fake_extract_video_frames_from_bytes)
    monkeypatch.setattr("app.portrait_jobs.assess_image_quality", lambda image: {"score": 0.9})
    monkeypatch.setattr("app.portrait_jobs.appearance_record", lambda image, include_embedding=False: {"quality": {"score": 0.9}})
    monkeypatch.setattr("app.portrait_jobs.persist_video_job", capture_persist)

    await run_video_job(job.job_id, job.tenant_id, b"video", "video.mp4", 1, 2)

    running_payloads = [item for item in persisted if item["status"] == "running" and item.get("result")]
    assert running_payloads
    assert all(item["result"]["frames"] == [] for item in running_payloads)
    assert running_payloads[-1]["result"]["frames_available"] == 2
    assert persisted[-1]["status"] == "completed"
    assert len(persisted[-1]["result"]["frames"]) == 2
    assert len(job.result["frames"]) == 2


def test_stream_create_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    STREAMS.clear()

    def fail_persist(stream):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_streams, "persist_stream", fail_persist)

    with pytest.raises(HTTPException):
        create_stream("http://example.com/live", tenant_id="tenant-a")

    assert STREAMS == {}


def test_stream_start_and_stop_roll_back_memory_when_persist_fails(monkeypatch) -> None:
    STREAMS.clear()
    stream = create_stream("http://example.com/live", tenant_id="tenant-a")
    initial_event_count = len(stream.events)

    def fail_persist(updated_stream):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_streams, "ALLOW_STREAM_URLS", True)
    monkeypatch.setattr(portrait_streams, "persist_stream", fail_persist)

    with pytest.raises(HTTPException):
        start_stream(stream)

    stored = STREAMS[stream_key("tenant-a", stream.stream_id)]
    assert stored.status == "registered"
    assert len(stored.events) == initial_event_count

    with pytest.raises(HTTPException):
        stop_stream(stream)

    stored = STREAMS[stream_key("tenant-a", stream.stream_id)]
    assert stored.status == "registered"
    assert len(stored.events) == initial_event_count


def test_streams_json_state_round_trip(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "streams.json"
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STREAMS_STATE_PATH", state_path)
    STREAMS.clear()

    created = create_stream("http://example.com/live", tenant_id="tenant-a", name="Lobby")
    STREAMS.clear()
    portrait_streams.load_streams_state()

    stored = STREAMS[stream_key("tenant-a", created.stream_id)]
    assert stored.name == "Lobby"
    assert stored.stream_url == "http://example.com/live"


def test_streams_json_state_protects_stream_url(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "streams.json"
    stream_url = "rtsp://user:secret@example.com/live?token=query-secret"
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STREAMS_STATE_PATH", state_path)
    STREAMS.clear()

    created = create_stream(stream_url, tenant_id="tenant-a", name="Lobby")
    raw_state = state_path.read_text(encoding="utf-8")

    assert "stream_url_protected" in raw_state
    assert "stream_url" not in json.loads(raw_state)["streams"][0]
    assert "secret" not in raw_state
    assert "query-secret" not in raw_state

    STREAMS.clear()
    portrait_streams.load_streams_state()

    stored = STREAMS[stream_key("tenant-a", created.stream_id)]
    assert stored.stream_url == stream_url


def test_stream_sensitive_settings_and_metadata_are_protected_at_rest(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "streams-sensitive.json"
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STREAMS_STATE_PATH", state_path)
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY", "stream-state-secret")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY_ID", "stream-v1")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEYRING", "")
    monkeypatch.setattr(portrait_crypto, "REQUIRE_ENCRYPTION", True)
    STREAMS.clear()

    created = create_stream(
        "http://example.com/live",
        tenant_id="tenant-a",
        settings={"api_key": "stream-secret-key", "safe": "visible"},
        metadata={"nested": {"token": "stream-secret-token"}, "label": "Lobby"},
    )
    raw_state = state_path.read_text(encoding="utf-8")

    assert "stream-secret-key" not in raw_state
    assert "stream-secret-token" not in raw_state
    payload = json.loads(raw_state)
    stream_state = payload["streams"][0]
    assert stream_state["settings"]["api_key"]["__portrait_protected_value__"] is True
    assert stream_state["metadata"]["nested"]["token"]["__portrait_protected_value__"] is True
    assert stream_state["settings"]["safe"] == "visible"

    STREAMS.clear()
    portrait_streams.load_streams_state()

    stored = STREAMS[stream_key("tenant-a", created.stream_id)]
    assert stored.settings["api_key"] == "stream-secret-key"
    assert stored.settings["safe"] == "visible"
    assert stored.metadata["nested"]["token"] == "stream-secret-token"
    assert stored.metadata["label"] == "Lobby"
    assert stored.public_dict()["settings"]["api_key"] == "<redacted>"
    assert stored.public_dict()["metadata"]["nested"]["token"] == "<redacted>"


def test_stream_event_jsonl_payload_is_redacted(monkeypatch) -> None:
    STREAMS.clear()
    stream = create_stream("http://example.com/live", tenant_id="tenant-a")
    appended = []

    def capture_append(path, payload, *, fail_closed=False):
        appended.append(payload)

    monkeypatch.setattr(portrait_stream_worker, "append_jsonl", capture_append)
    monkeypatch.setattr(portrait_stream_worker, "persist_stream", lambda stream: None)

    portrait_stream_worker.emit_stream_event(
        stream,
        "debug",
        "sensitive payload",
        {"token": "secret-token", "access_key": "secret-key", "embedding": [1.0, 2.0]},
    )

    assert appended
    payload = appended[0]["payload"]
    assert payload["token"] == "<redacted>"
    assert payload["access_key"] == "<redacted>"
    assert payload["embedding"] == "<redacted>"


def test_stream_worker_session_tracks_heartbeat_and_backpressure(monkeypatch) -> None:
    STREAMS.clear()
    portrait_stream_worker.STREAM_WORKER_SESSIONS.clear()
    monkeypatch.setattr(portrait_stream_worker, "append_jsonl", lambda path, payload, fail_closed=False: None)
    monkeypatch.setattr(portrait_stream_worker, "persist_stream", lambda stream: None)
    stream = create_stream("http://example.com/session", tenant_id="tenant-a")

    session = portrait_stream_worker.start_stream_worker_session(stream)
    heartbeat = portrait_stream_worker.heartbeat_stream_worker_session(stream, frame_buffer_depth=1, frames_sampled=2)
    dropped = portrait_stream_worker.record_stream_backpressure_drop(stream, count=3)
    status = portrait_stream_worker.stream_worker_status()

    assert session["status"] == "running"
    assert heartbeat["frames_sampled"] == 2
    assert dropped["backpressure_drops"] == 3
    assert status["active_sessions"] == 1
    assert status["daemon_entrypoint"] == "python -m app.portrait_stream_worker_daemon"


def test_stream_worker_health_detects_stale_sessions(monkeypatch) -> None:
    portrait_stream_worker.STREAM_WORKER_SESSIONS.clear()
    monkeypatch.setattr(
        portrait_stream_worker,
        "STREAM_WORKER_SESSIONS",
        {
            ("tenant-a", "stream-stale"): {
                "tenant_id": "tenant-a",
                "stream_id": "stream-stale",
                "status": "running",
                "last_heartbeat_at": 1.0,
            }
        },
    )

    report = portrait_stream_worker_health.evaluate_stream_worker_health(max_heartbeat_age_seconds=0.001)

    assert report["ok"] is False
    assert report["active_sessions"] == 1
    assert report["stale_session_count"] == 1


@pytest.mark.asyncio
async def test_stream_worker_daemon_once_runs_running_streams(monkeypatch) -> None:
    STREAMS.clear()
    monkeypatch.setattr(portrait_stream_worker_daemon, "load_streams_state", lambda: None)
    stream = create_stream("http://example.com/daemon", tenant_id="tenant-a")
    stream.status = "running"
    calls = []

    class FakeProcessLock:
        released = False

        def release(self):
            self.released = True

    process_lock = FakeProcessLock()

    async def fake_run_stream_worker_session(stream, *, max_reconnects=3):
        calls.append((stream.tenant_id, stream.stream_id, max_reconnects))
        return {"tenant_id": stream.tenant_id, "stream_id": stream.stream_id, "status": "running"}

    monkeypatch.setattr(portrait_stream_worker_daemon, "acquire_stream_process_lock", lambda stream, owner_id: process_lock)
    monkeypatch.setattr(portrait_stream_worker_daemon, "run_stream_worker_session", fake_run_stream_worker_session)

    report = await portrait_stream_worker_daemon.run_daemon_once(max_reconnects=2)

    assert report["status"] == "processed"
    assert report["selected_count"] == 1
    assert report["processed_count"] == 1
    assert calls == [("tenant-a", stream.stream_id, 2)]
    assert process_lock.released is True




@pytest.mark.asyncio
async def test_stream_worker_daemon_process_lock_skips_duplicate_process(monkeypatch) -> None:
    STREAMS.clear()
    monkeypatch.setattr(portrait_stream_worker_daemon, "load_streams_state", lambda: None)
    stream = create_stream("http://example.com/daemon-lock", tenant_id="tenant-a")
    stream.status = "running"
    calls = []

    def fake_acquire_process_lock(stream, owner_id):
        calls.append((stream.tenant_id, stream.stream_id, owner_id))
        return None

    def fail_acquire_lease(*args, **kwargs):
        raise AssertionError("state lease should not be attempted without the process lock")

    monkeypatch.setattr(portrait_stream_worker_daemon, "acquire_stream_process_lock", fake_acquire_process_lock)
    monkeypatch.setattr(portrait_stream_worker_daemon, "acquire_stream_worker_lease", fail_acquire_lease)

    report = await portrait_stream_worker_daemon.run_daemon_once(max_reconnects=1)

    assert report["status"] == "idle"
    assert report["selected_count"] == 1
    assert report["processed_count"] == 0
    assert calls == [("tenant-a", stream.stream_id, portrait_stream_worker_daemon.STREAM_WORKER_OWNER_ID)]


def test_stream_worker_process_lock_retries_after_stale_lock_cleanup(monkeypatch, workspace_tmp_path) -> None:
    STREAMS.clear()
    lock_dir = workspace_tmp_path / "stream-locks"
    monkeypatch.setattr(portrait_stream_worker_daemon, "STREAM_WORKER_LOCK_DIR", lock_dir)
    stream = create_stream("http://example.com/daemon-stale-lock", tenant_id="tenant-a")
    attempts = []
    stale_checks = []

    def fake_create_lock_file(lock, owner_id):
        attempts.append((lock.path, owner_id, lock.token))
        if len(attempts) == 1:
            raise FileExistsError(lock.path)

    def fake_remove_stale_lock(path):
        stale_checks.append(path)
        return True

    monkeypatch.setattr(portrait_stream_worker_daemon, "create_stream_process_lock_file", fake_create_lock_file)
    monkeypatch.setattr(portrait_stream_worker_daemon, "remove_stale_stream_process_lock", fake_remove_stale_lock)

    lock = portrait_stream_worker_daemon.acquire_stream_process_lock(stream, "owner-a")

    assert lock is not None
    assert lock.path == portrait_stream_worker_daemon.stream_process_lock_path(stream)
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0] == lock.path
    assert stale_checks == [lock.path]
    assert lock.token == attempts[1][2]


def test_stream_worker_process_lock_removes_stale_malformed_file(monkeypatch, workspace_tmp_path) -> None:
    lock_path = workspace_tmp_path / "stream-locks" / "bad.lock"
    removed = []

    monkeypatch.setattr(type(lock_path), "exists", lambda self: True)
    monkeypatch.setattr(type(lock_path), "unlink", lambda self, missing_ok=False: removed.append(self))
    monkeypatch.setattr(portrait_stream_worker_daemon, "read_stream_process_lock_payload", lambda path: None)
    monkeypatch.setattr(portrait_stream_worker_daemon, "stream_process_lock_created_at", lambda path, payload: 1.0)
    monkeypatch.setattr(portrait_stream_worker_daemon, "wall_time", lambda: 100.0)
    monkeypatch.setattr(portrait_stream_worker_daemon, "STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS", 1.0)

    assert portrait_stream_worker_daemon.remove_stale_stream_process_lock(lock_path) is True
    assert removed == [lock_path]


@pytest.mark.asyncio
async def test_stream_worker_revalidates_url_before_pull(monkeypatch) -> None:
    STREAMS.clear()
    stream = create_stream("http://example.com/rebind", tenant_id="tenant-a")
    stream.status = "running"
    calls = []

    def fail_validation(stream_url):
        calls.append(stream_url)
        raise HTTPException(status_code=400, detail="stream_url 主机被 SSRF 防护策略拒绝")

    def fail_if_pulled(*args, **kwargs):
        raise AssertionError("stream pull should not run after validation failure")

    monkeypatch.setattr(portrait_stream_worker, "validate_media_stream_url", fail_validation)
    monkeypatch.setattr(portrait_stream_worker, "extract_video_frames_from_path", fail_if_pulled)

    report = await portrait_stream_worker.run_stream_worker_session(stream, max_reconnects=0)

    assert calls == ["http://example.com/rebind"]
    assert report["status"] in {"failed", "reconnecting"}


def test_audit_chain_verifier_detects_tampering(workspace_tmp_path) -> None:
    audit_path = workspace_tmp_path / "audit.jsonl"
    first = {"event": "one", "audit_prev_hash": None}
    first_hash = portrait_audit.audit_payload_hash(first)
    first["audit_hash"] = first_hash
    second = {"event": "two", "audit_prev_hash": first_hash}
    second["audit_hash"] = portrait_audit.audit_payload_hash(second)
    audit_path.write_text(json.dumps(first) + "\n" + json.dumps(second | {"event": "tampered"}) + "\n", encoding="utf-8")

    result = portrait_audit.verify_audit_chain(audit_path)

    assert result["ok"] is False
    assert result["record_count"] == 2
    assert result["error_count"] == 1
    assert result["errors"][0]["reason"] == "audit_hash_mismatch"


def test_local_task_queue_rolls_back_message_when_state_write_fails(monkeypatch, workspace_tmp_path) -> None:
    TASK_MESSAGES.clear()
    monkeypatch.setattr("app.portrait_task_queue.TASK_QUEUE_STATE_PATH", workspace_tmp_path / "queue.jsonl")

    def fail_append(path, payload, *, fail_closed=False):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr("app.portrait_task_queue.append_jsonl", fail_append)

    with pytest.raises(HTTPException):
        LocalTaskQueue().enqueue("video_jobs", {"job_id": "job_failed"})

    assert TASK_MESSAGES == []


def test_redis_task_queue_falls_back_locally_when_enqueue_fails(monkeypatch, workspace_tmp_path, caplog) -> None:
    caplog.set_level("WARNING")
    TASK_MESSAGES.clear()
    monkeypatch.setattr("app.portrait_task_queue.REDIS_URL", "redis://:secret-token@redis.internal/0")
    monkeypatch.setattr("app.portrait_task_queue.TASK_QUEUE_STATE_PATH", workspace_tmp_path / "queue.jsonl")

    class FailingRedisClient:
        def lpush(self, *args, **kwargs):
            raise RuntimeError("redis://:secret-token@redis.internal/0 unavailable")

    class FailingRedisModule:
        class Redis:
            @staticmethod
            def from_url(*args, **kwargs):
                return FailingRedisClient()

    monkeypatch.setattr("app.portrait_task_queue.redis", FailingRedisModule())

    message = RedisTaskQueue().enqueue("video_jobs", {"job_id": "job_fallback"})

    assert message.status == "queued_local_fallback"
    assert TASK_MESSAGES == [message]
    assert "task_enqueued_local_fallback" in (workspace_tmp_path / "queue.jsonl").read_text(encoding="utf-8")
    assert "RuntimeError" in caplog.text
    for secret in ["secret-token", "redis.internal"]:
        assert secret not in caplog.text
