import asyncio
import json

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from app import (
    portrait_audit,
    portrait_image_results,
    portrait_model_capabilities,
    portrait_object_storage,
    portrait_state,
    portrait_vector_store,
    runtime_execution,
)
from app.portrait_object_storage import LocalObjectStore, S3ObjectStore
from app.portrait_postgres import postgres_health, vector_literal
from app.portrait_task_queue import (
    RedisTaskQueue,
)
from app.portrait_vector_store import PgvectorVectorStore, QdrantVectorStore


def test_local_image_analysis_results_are_capped_and_reloadable(
    monkeypatch, workspace_tmp_path
) -> None:
    state_path = workspace_tmp_path / "image-results.json"
    monkeypatch.setattr(
        portrait_image_results, "PORTRAIT_IMAGE_RESULTS_STATE_PATH", state_path
    )
    monkeypatch.setattr(portrait_image_results, "PORTRAIT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(
        portrait_image_results, "MAX_IMAGE_ANALYSIS_RESULTS_PER_TENANT", 2
    )
    monkeypatch.setattr(portrait_image_results, "IMAGE_ANALYSIS_THUMBNAIL_MAX_SIDE", 16)
    portrait_image_results.IMAGE_ANALYSIS_RESULTS.clear()

    try:
        for index, color in enumerate(["red", "green", "blue"]):
            portrait_image_results.create_image_analysis_result(
                tenant_id="tenant-a",
                request_id=f"req-{index}",
                mode="detection",
                endpoint="/v1/vision/infer",
                payload={"index": index},
                images=[Image.new("RGB", (32, 24), color=color)],
                filenames=[f"input-{index}.png"],
            )

        snapshot = portrait_image_results.image_analysis_results_snapshot("tenant-a")
        assert len(snapshot) == 2
        assert {record.request_id for record in snapshot} == {"req-1", "req-2"}
        assert all(record.previews[0]["src"].startswith("data:image/jpeg;base64,") for record in snapshot)

        raw_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert raw_state["version"] == 1
        assert len(raw_state["results"]) == 2

        portrait_image_results.IMAGE_ANALYSIS_RESULTS.clear()
        portrait_image_results.load_image_analysis_results_state()
        restored = portrait_image_results.image_analysis_results_snapshot("tenant-a")
        assert len(restored) == 2
        assert {record.payload["index"] for record in restored} == {1, 2}
    finally:
        portrait_image_results.IMAGE_ANALYSIS_RESULTS.clear()


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


def test_object_store_health_redacts_backend_locations(
    monkeypatch, workspace_tmp_path
) -> None:
    monkeypatch.setattr(
        portrait_object_storage,
        "OBJECT_STORAGE_DIR",
        workspace_tmp_path / "secret-objects",
    )
    local = LocalObjectStore().health()

    monkeypatch.setattr(portrait_object_storage, "S3_BUCKET", "secret-bucket")
    monkeypatch.setattr(
        portrait_object_storage, "S3_ENDPOINT_URL", "https://storage.internal"
    )
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

    local = LocalObjectStore().delete_object(
        {"object_key": "tenant-a/gallery-image/secret-face.png"}
    )
    s3 = S3ObjectStore().delete_object(
        {"object_key": "tenant-a/gallery-image/secret-face.png"}
    )

    assert local == {
        "backend": "local_file",
        "deleted": False,
        "reason": "对象删除失败",
    }
    assert s3 == {"backend": "s3", "deleted": False, "reason": "对象删除失败"}
    encoded = json.dumps({"local": local, "s3": s3}, ensure_ascii=False)
    assert "secret-face" not in encoded
    assert "secret-bucket" not in encoded
    assert "object_key" not in encoded
    assert "error" not in encoded
    assert "secret-face" not in caplog.text
    assert "secret-bucket" not in caplog.text
    assert "secret local object path" not in caplog.text


def test_state_file_failure_logs_are_redacted(
    monkeypatch, workspace_tmp_path, caplog
) -> None:
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
    portrait_state.handle_state_write_error(
        write_target, OSError(f"secret-write-token {write_target}")
    )
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
    from app import portrait_postgres, portrait_task_queue

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

    monkeypatch.setattr(
        portrait_postgres,
        "POSTGRES_DSN",
        "postgres://user:secret-password@db.internal/app",
    )
    monkeypatch.setattr(portrait_postgres, "psycopg", FailingPsycopg())
    monkeypatch.setattr(
        portrait_task_queue, "REDIS_URL", "redis://:secret-token@redis.internal/0"
    )
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
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

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


def test_audit_event_writes_tamper_evident_jsonl_chain(
    monkeypatch, workspace_tmp_path
) -> None:
    audit_path = workspace_tmp_path / "audit.jsonl"
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", audit_path)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", True)

    portrait_audit.audit_event(
        "gallery_update", request_id="req-1", tenant_id="tenant-a"
    )
    portrait_audit.audit_event(
        "gallery_delete", request_id="req-2", tenant_id="tenant-a"
    )

    records = [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]

    assert len(records) == 2
    assert records[0]["audit_prev_hash"] is None
    assert records[0]["audit_hash"] == portrait_audit.audit_payload_hash(records[0])
    assert records[1]["audit_prev_hash"] == records[0]["audit_hash"]
    assert records[1]["audit_hash"] == portrait_audit.audit_payload_hash(records[1])
    assert portrait_audit.last_audit_hash(audit_path) == records[1]["audit_hash"]


def test_audit_event_fails_closed_when_existing_jsonl_chain_is_unreadable(
    monkeypatch, workspace_tmp_path
) -> None:
    audit_path = workspace_tmp_path / "audit.jsonl"
    audit_path.write_text("{not-json}\n", encoding="utf-8")
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", audit_path)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", True)

    with pytest.raises(HTTPException) as exc_info:
        portrait_audit.audit_event(
            "gallery_update", request_id="req-1", tenant_id="tenant-a"
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "审计链不可用"
    assert audit_path.read_text(encoding="utf-8") == "{not-json}\n"


def test_audit_event_fails_closed_when_jsonl_write_fails(monkeypatch) -> None:
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", True)

    def fail_append(path, payload, *, fail_closed=False):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_audit, "append_jsonl", fail_append)

    with pytest.raises(HTTPException) as exc_info:
        portrait_audit.audit_event(
            "management_change", request_id="req-1", tenant_id="tenant-a"
        )

    assert exc_info.value.status_code == 503


def test_audit_event_fails_closed_when_postgres_audit_fails(monkeypatch) -> None:
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", True)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(
        portrait_audit, "append_jsonl", lambda path, payload, *, fail_closed=False: None
    )

    def fail_insert(payload):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("app.portrait_postgres.insert_audit_event", fail_insert)

    with pytest.raises(RuntimeError):
        portrait_audit.audit_event(
            "management_change", request_id="req-1", tenant_id="tenant-a"
        )


def test_audit_event_postgres_failure_logs_are_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    monkeypatch.setattr(portrait_audit, "AUDIT_WRITE_FAIL_CLOSED", False)
    monkeypatch.setattr(portrait_audit, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(
        portrait_audit, "append_jsonl", lambda path, payload, *, fail_closed=False: None
    )

    def fail_insert(payload):
        raise RuntimeError(
            "postgres://user:secret-password@db.internal/app tenant-secret"
        )

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
    monkeypatch.setattr(
        portrait_vector_store.QdrantVectorStore, "_client", fail_qdrant_client
    )

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


def test_local_vector_store_reuses_and_invalidates_normalized_cache() -> None:
    store = portrait_vector_store.LocalVectorStore()
    portrait_vector_store.invalidate_local_vector_cache()
    records = [
        {"tenant_id": "tenant-a", "person_id": "p1", "embedding": [1.0, 0.0]},
    ]

    first = store.search(
        [1.0, 0.0],
        records,
        modality="body",
        threshold_profile="normal",
        top_k=1,
        tenant_id="tenant-a",
    )
    records.append({"tenant_id": "tenant-a", "person_id": "p2", "embedding": [0.0, 1.0]})
    cached = store.search(
        [0.0, 1.0],
        records,
        modality="body",
        threshold_profile="normal",
        top_k=1,
        tenant_id="tenant-a",
    )

    store.upsert_feature({"tenant_id": "tenant-a"}, {"modality": "body"})
    refreshed = store.search(
        [0.0, 1.0],
        records,
        modality="body",
        threshold_profile="normal",
        top_k=1,
        tenant_id="tenant-a",
    )

    assert first[0]["person_id"] == "p1"
    assert cached[0]["person_id"] == "p1"
    assert refreshed[0]["person_id"] == "p2"


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

    (
        outputs,
        queue_seconds,
        inference_seconds,
        mode,
    ) = await runtime_execution.run_yolo_frames(
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
    bundle = {
        "session": object(),
        "lock": asyncio.Lock(),
        "inference_count": 0,
        "gpu_device_id": 0,
    }

    def fake_run_session(session, input_array):
        return [np.ones((input_array.shape[0], 2), dtype=np.float32)]

    monkeypatch.setattr(runtime_execution, "run_session", fake_run_session)

    (
        outputs,
        queue_seconds,
        inference_seconds,
        mode,
    ) = await runtime_execution.run_model_bundle_batch(
        bundle,
        [np.zeros((1, 3), dtype=np.float32), np.zeros((1, 3), dtype=np.float32)],
    )

    assert mode == "batch"
    assert outputs[0].shape == (2, 2)
    assert queue_seconds >= 0
    assert inference_seconds >= 0
    assert bundle["inference_count"] == 2


