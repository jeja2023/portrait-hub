import asyncio
from typing import Any

import numpy as np
import pytest

from app import runtime_execution


def bundle(model_id: str) -> dict[str, Any]:
    return {
        "key": model_id,
        "dynamic_batching_enabled": False,
        "model_fingerprint": f"fingerprint-{model_id}",
    }


@pytest.fixture(autouse=True)
def reset_shadow_state() -> None:
    runtime_execution.SHADOW_ROUTES.clear()
    runtime_execution.SHADOW_RESULTS.clear()


@pytest.mark.asyncio
async def test_shadow_inference_records_comparison_without_changing_response(monkeypatch) -> None:
    active = bundle("portrait/active.onnx")
    candidate = bundle("portrait/candidate.onnx")

    async def direct(selected, input_array):
        if selected["key"] == "portrait/candidate.onnx":
            return [input_array + 1], 0.0, 0.02
        return [input_array], 0.0, 0.01

    monkeypatch.setattr(runtime_execution, "_run_model_bundle_direct", direct)
    runtime_execution.configure_shadow_bundle(
        "portrait/active.onnx", candidate, percentage=100
    )

    outputs, _, _ = await runtime_execution.run_model_bundle(
        active, np.asarray([[2.0]], dtype=np.float32)
    )
    await asyncio.gather(*tuple(runtime_execution._SHADOW_TASKS))

    assert outputs[0].item() == 2.0
    result = runtime_execution.shadow_results_snapshot()[0]
    assert result["status"] == "completed"
    assert result["shape_match"] is True
    assert result["mean_absolute_difference"] == 1.0
    assert result["candidate_model_id"] == "portrait/candidate.onnx"


@pytest.mark.asyncio
async def test_shadow_failure_is_isolated_from_official_inference(monkeypatch) -> None:
    active = bundle("portrait/active.onnx")
    candidate = bundle("portrait/candidate.onnx")

    async def direct(selected, input_array):
        if selected["key"] == "portrait/candidate.onnx":
            raise RuntimeError("candidate failed")
        return [input_array], 0.0, 0.01

    monkeypatch.setattr(runtime_execution, "_run_model_bundle_direct", direct)
    runtime_execution.configure_shadow_bundle("portrait/active.onnx", candidate)

    outputs, _, _ = await runtime_execution.run_model_bundle(
        active, np.asarray([[3.0]], dtype=np.float32)
    )
    await asyncio.gather(*tuple(runtime_execution._SHADOW_TASKS))

    assert outputs[0].item() == 3.0
    result = runtime_execution.shadow_results_snapshot()[0]
    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
