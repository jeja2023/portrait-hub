import pytest

from app import runtime_sessions


def test_session_providers_rejects_tensorrt_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(runtime_sessions, "ENABLE_TENSORRT", False)
    monkeypatch.setattr(runtime_sessions, "model_config", lambda key: {"runtime": "tensorrt"})

    with pytest.raises(RuntimeError, match="ENABLE_TENSORRT"):
        runtime_sessions.session_providers("project/model.onnx")


def test_session_providers_defaults_to_cuda(monkeypatch) -> None:
    monkeypatch.setattr(runtime_sessions, "model_config", lambda key: {"runtime": "onnxruntime"})

    providers = runtime_sessions.session_providers("project/model.onnx")

    assert providers[0][0] == "CUDAExecutionProvider"


def test_create_session_falls_back_to_cpu_when_cuda_is_unavailable(monkeypatch, workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    sessions = []
    fallbacks: list[str] = []
    providers_recorded: list[str] = []

    class FakeSession:
        def __init__(self, path, providers):
            self.path = path
            self.providers = providers
            sessions.append(self)

        def get_providers(self):
            return [item[0] if isinstance(item, tuple) else item for item in self.providers]

    monkeypatch.setattr(runtime_sessions.ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(runtime_sessions.ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(runtime_sessions, "CPU_FALLBACK_ENABLED", True)
    monkeypatch.setattr(runtime_sessions, "record_cpu_fallback", lambda reason: fallbacks.append(reason))
    monkeypatch.setattr(runtime_sessions, "record_session_provider", lambda provider: providers_recorded.append(provider))

    session = runtime_sessions.create_session(model_path, "project/model.onnx", 0)

    assert session is sessions[0]
    assert session.get_providers() == ["CPUExecutionProvider"]
    # 面向 GPU 的主机发生 CPU 回退时，必须能在指标中观测到。
    assert fallbacks == ["cuda_provider_unavailable"]
    assert providers_recorded == ["CPUExecutionProvider"]


def test_create_session_force_cpu_records_provider_without_fallback(monkeypatch, workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    fallbacks: list[str] = []
    providers_recorded: list[str] = []

    class FakeSession:
        def __init__(self, path, providers):
            self.providers = providers

        def get_providers(self):
            return [item[0] if isinstance(item, tuple) else item for item in self.providers]

    monkeypatch.setattr(runtime_sessions, "FORCE_CPU", True)
    monkeypatch.setattr(
        runtime_sessions.ort,
        "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(runtime_sessions.ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(runtime_sessions, "record_cpu_fallback", lambda reason: fallbacks.append(reason))
    monkeypatch.setattr(runtime_sessions, "record_session_provider", lambda provider: providers_recorded.append(provider))

    runtime_sessions.create_session(model_path, "project/model.onnx", 0)

    # 启用 FORCE_CPU 是主动选择而非回退：记录 provider，但不记录 fallback。
    assert fallbacks == []
    assert providers_recorded == ["CPUExecutionProvider"]


def test_create_session_rejects_cpu_fallback_when_disabled(monkeypatch, workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    monkeypatch.setattr(runtime_sessions.ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(runtime_sessions, "CPU_FALLBACK_ENABLED", False)

    with pytest.raises(RuntimeError, match="CUDAExecutionProvider 不可用"):
        runtime_sessions.create_session(model_path, "project/model.onnx", 0)


def test_runtime_provider_status_accepts_cpu_fallback(monkeypatch) -> None:
    monkeypatch.setattr(runtime_sessions, "CPU_FALLBACK_ENABLED", True)

    status = runtime_sessions.runtime_provider_status(["CPUExecutionProvider"])

    assert status["ready"] is True
    assert status["cuda_available"] is False
    assert status["cpu_available"] is True
    assert status["cpu_fallback_enabled"] is True
    assert status["force_cpu"] is False


def test_session_providers_force_cpu_returns_cpu_only(monkeypatch) -> None:
    monkeypatch.setattr(runtime_sessions, "FORCE_CPU", True)
    # 设置 FORCE_CPU 时，即使请求 TensorRT 的模型也必须收敛到 CPU。
    monkeypatch.setattr(runtime_sessions, "model_config", lambda key: {"runtime": "tensorrt"})

    providers = runtime_sessions.session_providers("project/model.onnx")

    assert providers == runtime_sessions.CPU_PROVIDERS


def test_create_session_force_cpu_builds_single_cpu_session(monkeypatch, workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    sessions = []

    class FakeSession:
        def __init__(self, path, providers):
            self.path = path
            self.providers = providers
            sessions.append(self)

        def get_providers(self):
            return list(self.providers)

    monkeypatch.setattr(runtime_sessions, "FORCE_CPU", True)
    # 即使 CUDA 被报告为“可用”（onnxruntime-gpu 在纯 CPU 主机上也会这样），
    # 启用 FORCE_CPU 时仍必须只构建一个 CPU 会话，不尝试 CUDA，也不重建。
    monkeypatch.setattr(
        runtime_sessions.ort,
        "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(runtime_sessions.ort, "InferenceSession", FakeSession)

    session = runtime_sessions.create_session(model_path, "project/model.onnx", 0)

    assert len(sessions) == 1
    assert session.get_providers() == ["CPUExecutionProvider"]


def test_create_session_force_cpu_requires_cpu_provider(monkeypatch, workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    monkeypatch.setattr(runtime_sessions, "FORCE_CPU", True)
    monkeypatch.setattr(runtime_sessions.ort, "get_available_providers", lambda: ["CUDAExecutionProvider"])

    with pytest.raises(RuntimeError, match="FORCE_CPU is set but CPUExecutionProvider is not available"):
        runtime_sessions.create_session(model_path, "project/model.onnx", 0)


def test_runtime_provider_status_force_cpu_ready_without_cuda(monkeypatch) -> None:
    monkeypatch.setattr(runtime_sessions, "FORCE_CPU", True)
    monkeypatch.setattr(runtime_sessions, "CPU_FALLBACK_ENABLED", False)

    status = runtime_sessions.runtime_provider_status(["CPUExecutionProvider"])

    assert status["ready"] is True
    assert status["force_cpu"] is True
