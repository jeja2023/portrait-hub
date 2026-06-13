import asyncio

from app import runtime_registry, runtime_sessions, runtime_state


def patch_devices(monkeypatch, device_ids: list[int], queue_limit: int = 1) -> None:
    semaphores = {int(device_id): asyncio.Semaphore(queue_limit) for device_id in device_ids}
    monkeypatch.setattr(runtime_state, "GPU_DEVICE_SEMAPHORES", semaphores)


def test_gpu_device_ids_defaults_to_zero_when_empty(monkeypatch) -> None:
    monkeypatch.setattr(runtime_state, "GPU_DEVICE_SEMAPHORES", {})

    assert runtime_state.gpu_device_ids() == [0]


def test_gpu_device_ids_lists_configured_devices(monkeypatch) -> None:
    patch_devices(monkeypatch, [0, 1, 2])

    assert runtime_state.gpu_device_ids() == [0, 1, 2]


def test_gpu_semaphore_for_device_none_returns_global() -> None:
    assert runtime_state.gpu_semaphore_for_device(None) is runtime_state.GPU_SEMAPHORE


def test_gpu_semaphore_for_device_known_device_is_isolated(monkeypatch) -> None:
    patch_devices(monkeypatch, [0, 1])

    sem_zero = runtime_state.gpu_semaphore_for_device(0)
    sem_one = runtime_state.gpu_semaphore_for_device(1)

    assert sem_zero is runtime_state.GPU_DEVICE_SEMAPHORES[0]
    assert sem_one is runtime_state.GPU_DEVICE_SEMAPHORES[1]
    assert sem_zero is not sem_one


def test_gpu_semaphore_for_unknown_device_falls_back_to_global(monkeypatch) -> None:
    patch_devices(monkeypatch, [0])

    # A device id outside the configured set must not raise KeyError.
    assert runtime_state.gpu_semaphore_for_device(7) is runtime_state.GPU_SEMAPHORE


def test_single_card_models_share_one_device_semaphore(monkeypatch) -> None:
    patch_devices(monkeypatch, [0])

    assert runtime_state.gpu_semaphore_for_device(0) is runtime_state.gpu_semaphore_for_device(0)


def test_model_gpu_device_id_honors_explicit_runtime_device(monkeypatch) -> None:
    patch_devices(monkeypatch, [0, 1])
    monkeypatch.setattr(runtime_registry, "model_config", lambda key: {"runtime": {"device_id": 1}})

    assert runtime_registry.model_gpu_device_id("project/model.onnx") == 1


def test_model_gpu_device_id_clamps_out_of_range_device(monkeypatch) -> None:
    patch_devices(monkeypatch, [0, 1])
    monkeypatch.setattr(runtime_registry, "model_config", lambda key: {"device_id": 9})

    # Out-of-range explicit device falls back to the first configured device.
    assert runtime_registry.model_gpu_device_id("project/model.onnx") == 0


def test_model_gpu_device_id_hashes_when_unset(monkeypatch) -> None:
    patch_devices(monkeypatch, [0, 1, 2, 3])
    monkeypatch.setattr(runtime_registry, "model_config", lambda key: {})

    device = runtime_registry.model_gpu_device_id("project/model.onnx")

    # Hash-based assignment must land on a configured device and be deterministic.
    assert device in {0, 1, 2, 3}
    assert device == runtime_registry.model_gpu_device_id("project/model.onnx")


def test_model_gpu_device_id_single_card_always_zero(monkeypatch) -> None:
    patch_devices(monkeypatch, [0])
    monkeypatch.setattr(runtime_registry, "model_config", lambda key: {})

    for key in ("a/m.onnx", "b/m.onnx", "c/m.onnx"):
        assert runtime_registry.model_gpu_device_id(key) == 0


def test_cuda_providers_for_device_none_returns_base() -> None:
    assert runtime_sessions.cuda_providers_for_device(None) is runtime_sessions.CUDA_PROVIDERS


def test_cuda_providers_for_device_sets_device_id() -> None:
    providers = runtime_sessions.cuda_providers_for_device(2)

    cuda = next(item for item in providers if isinstance(item, tuple) and item[0] == "CUDAExecutionProvider")
    assert cuda[1]["device_id"] == 2
    # The CPU fallback provider must be preserved untouched.
    assert "CPUExecutionProvider" in providers


def test_cuda_providers_for_device_does_not_mutate_base() -> None:
    runtime_sessions.cuda_providers_for_device(3)

    base_cuda = next(
        item for item in runtime_sessions.CUDA_PROVIDERS if isinstance(item, tuple) and item[0] == "CUDAExecutionProvider"
    )
    assert base_cuda[1]["device_id"] == 0
