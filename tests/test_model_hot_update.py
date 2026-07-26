import asyncio
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app import runtime_execution, runtime_registry
from app.runtime_state import MODEL_LOAD_LOCKS, MODEL_REGISTRY


class FakeInput:
    name = "input"
    type = "tensor(float)"

    def __init__(self) -> None:
        self.shape = ["batch", 3, "height", "width"]


class FakeSession:
    def get_inputs(self):
        return [FakeInput()]

    def get_providers(self):
        return ["CPUExecutionProvider"]


def fake_bundle(*, in_use: int = 0, fingerprint: str = "old") -> dict[str, Any]:
    return {
        "key": "portrait/model.onnx",
        "session": FakeSession(),
        "lock": asyncio.Lock(),
        "semaphore": asyncio.Semaphore(1),
        "gpu_device_id": 0,
        "path": "model.onnx",
        "model_hash": fingerprint,
        "model_fingerprint": fingerprint,
        "file_size": 1,
        "loaded_at": 1.0,
        "last_used_at": 1.0,
        "load_count": 1,
        "inference_count": 0,
        "in_use": in_use,
        "max_concurrency": 1,
        "queue_timeout_seconds": 0.0,
        "execution_provider": "CPUExecutionProvider",
    }


@pytest.mark.asyncio
async def test_prewarm_uses_configured_shape_and_records_result(monkeypatch) -> None:
    bundle = fake_bundle()
    monkeypatch.setattr(
        runtime_registry,
        "model_config",
        lambda key: {"input": {"size": [112, 96]}},
    )

    async def direct(candidate, input_array):
        assert input_array.shape == (1, 3, 112, 96)
        return [np.ones((1, 8), dtype=np.float32)], 0.01, 0.02

    monkeypatch.setattr(runtime_execution, "_run_model_bundle_direct", direct)

    result = await runtime_registry.prewarm_model_bundle("portrait/model.onnx", bundle)

    assert result["status"] == "passed"
    assert result["output_shapes"] == [[1, 8]]
    assert bundle["prewarm"] == result


@pytest.mark.asyncio
async def test_hot_swap_keeps_old_bundle_until_inflight_request_drains(monkeypatch) -> None:
    MODEL_REGISTRY.clear()
    MODEL_LOAD_LOCKS.clear()
    previous = fake_bundle(in_use=1, fingerprint="old")
    candidate = fake_bundle(fingerprint="new")
    MODEL_REGISTRY["portrait/model.onnx"] = previous

    async def create_candidate(key, path):
        return candidate, 0.1

    async def prewarm(key, bundle):
        bundle["prewarm"] = {"status": "passed"}
        return bundle["prewarm"]

    monkeypatch.setattr(runtime_registry, "create_candidate_model_bundle", create_candidate)
    monkeypatch.setattr(runtime_registry, "prewarm_model_bundle", prewarm)
    monkeypatch.setattr(runtime_registry, "MAX_LOADED_MODELS", 0)

    active, replaced, _, _ = await runtime_registry.replace_model_bundle(
        "portrait/model.onnx", Path("model.onnx")
    )

    assert active is candidate
    assert replaced is previous
    assert MODEL_REGISTRY["portrait/model.onnx"] is candidate
    runtime_registry.retire_model_bundle(previous)
    await asyncio.sleep(0.02)
    assert "session" in previous
    previous["in_use"] = 0
    await asyncio.sleep(0.03)
    assert "session" not in previous


@pytest.mark.asyncio
async def test_failed_candidate_prewarm_does_not_replace_active_bundle(monkeypatch) -> None:
    MODEL_REGISTRY.clear()
    MODEL_LOAD_LOCKS.clear()
    previous = fake_bundle(fingerprint="old")
    candidate = fake_bundle(fingerprint="new")
    MODEL_REGISTRY["portrait/model.onnx"] = previous

    async def create_candidate(key, path):
        return candidate, 0.1

    async def fail_prewarm(key, bundle):
        raise RuntimeError("prewarm failed")

    monkeypatch.setattr(runtime_registry, "create_candidate_model_bundle", create_candidate)
    monkeypatch.setattr(runtime_registry, "prewarm_model_bundle", fail_prewarm)

    with pytest.raises(RuntimeError, match="prewarm failed"):
        await runtime_registry.replace_model_bundle("portrait/model.onnx", Path("model.onnx"))

    assert MODEL_REGISTRY["portrait/model.onnx"] is previous
    assert "session" in previous
    assert "session" not in candidate


def test_model_fingerprint_changes_with_preprocessing_contract(monkeypatch) -> None:
    configs = [
        {"version": "1", "input": {"size": [112, 112], "normalize": "arcface"}},
        {"version": "1", "input": {"size": [112, 112], "normalize": "imagenet"}},
    ]
    monkeypatch.setattr(runtime_registry, "model_config", lambda key: configs[0])
    first = runtime_registry.model_runtime_fingerprint("portrait/model.onnx", "a" * 64)
    monkeypatch.setattr(runtime_registry, "model_config", lambda key: configs[1])
    second = runtime_registry.model_runtime_fingerprint("portrait/model.onnx", "a" * 64)

    assert first != second
