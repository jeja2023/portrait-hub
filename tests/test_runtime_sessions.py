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

    class FakeSession:
        def __init__(self, path, providers):
            self.path = path
            self.providers = providers
            sessions.append(self)

        def get_providers(self):
            return list(self.providers)

    monkeypatch.setattr(runtime_sessions.ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(runtime_sessions.ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(runtime_sessions, "CPU_FALLBACK_ENABLED", True)

    session = runtime_sessions.create_session(model_path, "project/model.onnx", 0)

    assert session is sessions[0]
    assert session.get_providers() == ["CPUExecutionProvider"]


def test_create_session_rejects_cpu_fallback_when_disabled(monkeypatch, workspace_tmp_path) -> None:
    model_path = workspace_tmp_path / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    monkeypatch.setattr(runtime_sessions.ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(runtime_sessions, "CPU_FALLBACK_ENABLED", False)

    with pytest.raises(RuntimeError, match="CUDAExecutionProvider is not available"):
        runtime_sessions.create_session(model_path, "project/model.onnx", 0)


def test_runtime_provider_status_accepts_cpu_fallback(monkeypatch) -> None:
    monkeypatch.setattr(runtime_sessions, "CPU_FALLBACK_ENABLED", True)

    status = runtime_sessions.runtime_provider_status(["CPUExecutionProvider"])

    assert status["ready"] is True
    assert status["cuda_available"] is False
    assert status["cpu_available"] is True
    assert status["cpu_fallback_enabled"] is True
