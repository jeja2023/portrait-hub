import pytest

from app import server


@pytest.mark.asyncio
async def test_warmup_is_best_effort_and_isolates_failures(monkeypatch) -> None:
    attempted: list[str] = []

    async def fake_load(key, model_path):
        attempted.append(key)
        if key == "proj/bad.onnx":
            raise RuntimeError("load fail")
        return object(), True, 0.0

    monkeypatch.setattr(server, "WARMUP_MODELS", ["proj/good1.onnx", "proj/bad.onnx", "proj/good2.onnx"])
    monkeypatch.setattr(server, "WARMUP_FAIL_FAST", False)
    monkeypatch.setattr(server, "get_model_path", lambda project, model: f"/models/{project}/{model}")
    monkeypatch.setattr(server, "get_or_load_model", fake_load)

    # A single failing model must NOT abort warmup; the rest are still attempted.
    await server.warmup_models()

    assert attempted == ["proj/good1.onnx", "proj/bad.onnx", "proj/good2.onnx"]


@pytest.mark.asyncio
async def test_warmup_fail_fast_raises_on_first_failure(monkeypatch) -> None:
    attempted: list[str] = []

    async def fake_load(key, model_path):
        attempted.append(key)
        raise RuntimeError("load fail")

    monkeypatch.setattr(server, "WARMUP_MODELS", ["proj/bad.onnx", "proj/good.onnx"])
    monkeypatch.setattr(server, "WARMUP_FAIL_FAST", True)
    monkeypatch.setattr(server, "get_model_path", lambda project, model: f"/models/{project}/{model}")
    monkeypatch.setattr(server, "get_or_load_model", fake_load)

    with pytest.raises(RuntimeError, match="load fail"):
        await server.warmup_models()

    # Fail-fast stops at the first failure; the later model is never attempted.
    assert attempted == ["proj/bad.onnx"]
