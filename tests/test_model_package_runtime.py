import pytest
from fastapi import HTTPException

from app import runtime_registry
from app.model_package import labels_from_config, model_card_for_path
from app.runtime_state import MODEL_LOAD_LOCKS, MODEL_LOAD_RETRY_AFTER, MODEL_REGISTRY


def test_explicit_labels_sidecar_is_required(workspace_tmp_path, caplog) -> None:
    caplog.set_level("ERROR")
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")

    with pytest.raises(HTTPException) as exc_info:
        labels_from_config(
            {"artifact": {"labels": "missing.labels.txt"}, "output": {"classes": "coco"}},
            model_path,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "model labels file not found"
    assert "missing.labels.txt" not in str(exc_info.value.detail)
    assert "sidecar_path_hash=" in caplog.text
    assert "missing.labels.txt" not in caplog.text
    assert str(model_path.parent) not in caplog.text


@pytest.mark.asyncio
async def test_model_load_failure_logs_are_redacted(monkeypatch, workspace_tmp_path, caplog) -> None:
    caplog.set_level("INFO")
    secret_dir = workspace_tmp_path / "secret-model-dir"
    secret_dir.mkdir(parents=True, exist_ok=True)
    model_path = secret_dir / "secret-model.onnx"
    model_path.write_bytes(b"fake onnx")
    MODEL_REGISTRY.clear()
    MODEL_LOAD_LOCKS.clear()

    monkeypatch.setattr(runtime_registry, "model_hash", lambda path: "digest")
    monkeypatch.setattr(runtime_registry, "validate_model_hash", lambda key, digest: None)

    def fail_create_session(path, cache_key, device_id=None):
        raise RuntimeError(f"onnx runtime secret-token failed for {path}")

    monkeypatch.setattr(runtime_registry, "create_session", fail_create_session)

    with pytest.raises(HTTPException) as exc_info:
        await runtime_registry.get_or_load_model("portrait_hub/secret-model.onnx", model_path)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "failed to load model runtime"
    assert "path_hash=" in caplog.text
    assert "RuntimeError" in caplog.text
    for secret in ["secret-model-dir", "secret-token", str(model_path)]:
        assert secret not in caplog.text
        assert secret not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_load_failure_sets_cooldown_then_fast_fails(monkeypatch, workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    MODEL_REGISTRY.clear()
    MODEL_LOAD_LOCKS.clear()
    MODEL_LOAD_RETRY_AFTER.clear()
    create_calls = {"n": 0}

    def fail_create_session(path, cache_key, device_id=None):
        create_calls["n"] += 1
        raise RuntimeError("load fail")

    monkeypatch.setattr(runtime_registry, "model_hash", lambda path: "digest")
    monkeypatch.setattr(runtime_registry, "validate_model_hash", lambda key, digest: None)
    monkeypatch.setattr(runtime_registry, "create_session", fail_create_session)
    monkeypatch.setattr(runtime_registry, "MODEL_LOAD_RETRY_COOLDOWN_SECONDS", 30.0)

    # First request actually attempts the load and fails with 500.
    with pytest.raises(HTTPException) as first:
        await runtime_registry.get_or_load_model("proj/model.onnx", model_path)
    assert first.value.status_code == 500
    assert MODEL_LOAD_RETRY_AFTER.get("proj/model.onnx", 0) > 0

    # Second request inside the cooldown window fast-fails with 503 WITHOUT re-attempting the load.
    with pytest.raises(HTTPException) as second:
        await runtime_registry.get_or_load_model("proj/model.onnx", model_path)
    assert second.value.status_code == 503
    assert create_calls["n"] == 1


@pytest.mark.asyncio
async def test_cooldown_expiry_allows_retry_and_success_clears_it(monkeypatch, workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    MODEL_REGISTRY.clear()
    MODEL_LOAD_LOCKS.clear()
    # Simulate a still-recorded-but-expired cooldown (retry-after in the past).
    MODEL_LOAD_RETRY_AFTER.clear()
    MODEL_LOAD_RETRY_AFTER["proj/model.onnx"] = 1.0

    class FakeSession:
        def get_providers(self):
            return ["CPUExecutionProvider"]

    monkeypatch.setattr(runtime_registry, "model_hash", lambda path: "digest")
    monkeypatch.setattr(runtime_registry, "validate_model_hash", lambda key, digest: None)
    monkeypatch.setattr(runtime_registry, "create_session", lambda path, cache_key, device_id=None: FakeSession())
    monkeypatch.setattr(runtime_registry, "model_gpu_device_id", lambda key: 0)

    bundle, cold_loaded, _ = await runtime_registry.get_or_load_model("proj/model.onnx", model_path)

    assert cold_loaded is True
    # A successful load clears the stale cooldown entry so the model serves normally.
    assert "proj/model.onnx" not in MODEL_LOAD_RETRY_AFTER
    runtime_registry.release_model_bundle(bundle)
    MODEL_REGISTRY.clear()


def test_explicit_labels_sidecar_must_not_be_empty(workspace_tmp_path, caplog) -> None:
    caplog.set_level("ERROR")
    model_path = workspace_tmp_path / "model.onnx"
    labels_path = workspace_tmp_path / "labels.txt"
    model_path.write_bytes(b"fake onnx")
    labels_path.write_text("# comments only\n\n", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        labels_from_config({"artifact": {"labels": "labels.txt"}}, model_path)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "model labels file is empty"
    assert "labels.txt" not in str(exc_info.value.detail)
    assert "sidecar_path_hash=" in caplog.text
    assert "labels.txt" not in caplog.text
    assert str(labels_path) not in caplog.text


def test_explicit_labels_read_failure_is_redacted(monkeypatch, workspace_tmp_path, caplog) -> None:
    caplog.set_level("WARNING")
    model_path = workspace_tmp_path / "model.onnx"
    labels_path = workspace_tmp_path / "secret-labels.txt"
    model_path.write_bytes(b"fake onnx")
    labels_path.write_text("person\n", encoding="utf-8")

    original_open = type(labels_path).open
    target_path = labels_path.resolve()

    def fail_open(self, *args, **kwargs):
        if self.resolve() == target_path:
            raise OSError("secret-labels.txt api_key=hidden")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(labels_path), "open", fail_open)

    with pytest.raises(HTTPException) as exc_info:
        labels_from_config({"artifact": {"labels": "secret-labels.txt"}}, model_path)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "failed to read model labels"
    assert "sidecar_path_hash=" in caplog.text
    assert "OSError" in caplog.text
    assert "secret-labels.txt" not in str(exc_info.value.detail)
    assert "secret-labels.txt" not in caplog.text
    assert "api_key" not in caplog.text


def test_inline_classes_are_used_when_no_explicit_labels_sidecar(workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")

    labels = labels_from_config({"output": {"classes": ["ok", "ng"]}}, model_path)

    assert labels == ["ok", "ng"]


def test_explicit_model_card_sidecar_is_required(workspace_tmp_path, caplog) -> None:
    caplog.set_level("ERROR")
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")

    with pytest.raises(HTTPException) as exc_info:
        model_card_for_path({"artifact": {"model_card": "missing.model-card.yml"}}, model_path)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "model sidecar yaml not found"
    assert "missing.model-card.yml" not in str(exc_info.value.detail)
    assert "sidecar_path_hash=" in caplog.text
    assert "missing.model-card.yml" not in caplog.text
    assert str(model_path.parent) not in caplog.text


def test_explicit_model_card_sidecar_must_be_mapping(workspace_tmp_path, caplog) -> None:
    caplog.set_level("ERROR")
    model_path = workspace_tmp_path / "model.onnx"
    card_path = workspace_tmp_path / "model-card.yml"
    model_path.write_bytes(b"fake onnx")
    card_path.write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        model_card_for_path({"artifact": {"model_card": "model-card.yml"}}, model_path)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "model sidecar yaml root must be a mapping"
    assert "model-card.yml" not in str(exc_info.value.detail)
    assert "sidecar_path_hash=" in caplog.text
    assert "model-card.yml" not in caplog.text
    assert str(card_path) not in caplog.text


def test_explicit_model_card_read_failure_is_redacted(monkeypatch, workspace_tmp_path, caplog) -> None:
    caplog.set_level("WARNING")
    model_path = workspace_tmp_path / "model.onnx"
    card_path = workspace_tmp_path / "secret-card.yml"
    model_path.write_bytes(b"fake onnx")
    card_path.write_text("model:\n  version: v1\n", encoding="utf-8")

    original_open = type(card_path).open
    target_path = card_path.resolve()

    def fail_open(self, *args, **kwargs):
        if self.resolve() == target_path:
            raise OSError("secret-card.yml api_key=hidden")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(card_path), "open", fail_open)

    with pytest.raises(HTTPException) as exc_info:
        model_card_for_path({"artifact": {"model_card": "secret-card.yml"}}, model_path)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "failed to read model sidecar yaml"
    assert "sidecar_path_hash=" in caplog.text
    assert "OSError" in caplog.text
    assert "secret-card.yml" not in str(exc_info.value.detail)
    assert "api_key" not in str(exc_info.value.detail)
    assert "secret-card.yml" not in caplog.text
    assert "api_key" not in caplog.text


def test_default_model_card_sidecar_remains_optional(workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")

    assert model_card_for_path({}, model_path) == {}


@pytest.mark.asyncio
async def test_model_unload_releases_session_reference(monkeypatch) -> None:
    class FakeSession:
        pass

    bundle = {"session": FakeSession(), "model_hash": "hash", "last_used_at": 1.0}
    MODEL_REGISTRY.clear()
    MODEL_LOAD_LOCKS.clear()
    MODEL_REGISTRY["portrait_hub/releasable.onnx"] = bundle
    MODEL_LOAD_LOCKS["portrait_hub/releasable.onnx"] = object()
    collected = []

    monkeypatch.setattr(runtime_registry.gc, "collect", lambda generation=0: collected.append(generation))

    assert await runtime_registry.unload_model_by_key("portrait_hub/releasable.onnx") is True
    assert "portrait_hub/releasable.onnx" not in MODEL_REGISTRY
    assert "portrait_hub/releasable.onnx" not in MODEL_LOAD_LOCKS
    assert "session" not in bundle
    assert collected == [0]
