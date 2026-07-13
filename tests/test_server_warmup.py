import pytest

from app import server


@pytest.mark.asyncio
async def test_warmup_is_best_effort_and_isolates_failures(monkeypatch) -> None:
    attempted: list[str] = []

    async def fake_load(key, model_path):
        attempted.append(key)
        if key == "proj/bad.onnx":
            raise RuntimeError("加载失败")
        return object(), True, 0.0

    monkeypatch.setattr(server, "WARMUP_MODELS", ["proj/good1.onnx", "proj/bad.onnx", "proj/good2.onnx"])
    monkeypatch.setattr(server, "WARMUP_FAIL_FAST", False)
    monkeypatch.setattr(server, "get_model_path", lambda project, model: f"/models/{project}/{model}")
    monkeypatch.setattr(server, "get_or_load_model", fake_load)

    # 单个模型失败不应中止预热；其余模型仍会继续尝试。
    await server.warmup_models()

    assert attempted == ["proj/good1.onnx", "proj/bad.onnx", "proj/good2.onnx"]


@pytest.mark.asyncio
async def test_warmup_fail_fast_raises_on_first_failure(monkeypatch) -> None:
    attempted: list[str] = []

    async def fake_load(key, model_path):
        attempted.append(key)
        raise RuntimeError("加载失败")

    monkeypatch.setattr(server, "WARMUP_MODELS", ["proj/bad.onnx", "proj/good.onnx"])
    monkeypatch.setattr(server, "WARMUP_FAIL_FAST", True)
    monkeypatch.setattr(server, "get_model_path", lambda project, model: f"/models/{project}/{model}")
    monkeypatch.setattr(server, "get_or_load_model", fake_load)

    with pytest.raises(RuntimeError, match="加载失败"):
        await server.warmup_models()

    # 快速失败模式会在首次失败时停止，后续模型不会被尝试。
    assert attempted == ["proj/bad.onnx"]
