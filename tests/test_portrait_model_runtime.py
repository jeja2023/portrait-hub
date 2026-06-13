import numpy as np
import pytest
from PIL import Image

from app import portrait_model_runtime


class FakeIO:
    def __init__(self, shape):
        self.name = "input"
        self.type = "tensor(float)"
        self.shape = shape


class FakeSession:
    def __init__(self, shape):
        self._shape = shape

    def get_inputs(self):
        return [FakeIO(self._shape)]

    def get_outputs(self):
        return []

    def get_providers(self):
        return ["CUDAExecutionProvider"]


def fake_runtime(capability_name: str, adapter: str, shape: list[int], embedding_dim: int = 0):
    return portrait_model_runtime.CapabilityRuntime(
        capability_name=capability_name,
        model_id=f"portrait/{adapter}.onnx",
        cache_key=f"portrait/{adapter}.onnx",
        adapter=adapter,
        capability={"adapter": adapter, "embedding_dim": embedding_dim},
        config={"version": "test"},
        bundle={"session": FakeSession(shape), "lock": None, "inference_count": 0, "gpu_device_id": 0},
    )


@pytest.mark.asyncio
async def test_arcface_adapter_batches_face_embeddings(monkeypatch) -> None:
    async def fake_get_runtime(capability_name, adapters):
        if capability_name == "face_embedding":
            return fake_runtime("face_embedding", "arcface", [1, 3, 112, 112], embedding_dim=512)
        return None

    async def fake_run_batch(bundle, inputs):
        assert len(inputs) == 1
        assert inputs[0].shape == (1, 3, 112, 112)
        return [np.ones((1, 512), dtype=np.float32)], 0.0, 0.0, "batch"

    monkeypatch.setattr(portrait_model_runtime, "get_capability_runtime", fake_get_runtime)
    monkeypatch.setattr(portrait_model_runtime, "run_model_bundle_batch", fake_run_batch)

    embedding, face = await portrait_model_runtime.infer_best_face_embedding_for_image(Image.new("RGB", (80, 80), (120, 80, 40)))

    assert len(embedding) == 512
    assert face["embedding_model_status"] == "arcface_onnx"
    assert face["embedding_model_id"] == "portrait/arcface.onnx"


@pytest.mark.asyncio
async def test_scrfd_adapter_decodes_combined_detection_rows(monkeypatch) -> None:
    async def fake_get_runtime(capability_name, adapters):
        if capability_name == "face_detection":
            return fake_runtime("face_detection", "scrfd", [1, 3, 640, 640])
        return None

    async def fake_run_batch(bundle, inputs):
        rows = np.asarray(
            [
                [40, 50, 160, 180, 0.97, 70, 85, 130, 85, 100, 115, 78, 145, 122, 145],
            ],
            dtype=np.float32,
        )
        return [rows], 0.0, 0.0, "single"

    monkeypatch.setattr(portrait_model_runtime, "get_capability_runtime", fake_get_runtime)
    monkeypatch.setattr(portrait_model_runtime, "run_model_bundle_batch", fake_run_batch)

    faces = await portrait_model_runtime.infer_face_records_for_image(Image.new("RGB", (640, 640), (120, 80, 40)))

    assert len(faces) == 1
    assert faces[0]["detection_strategy"] == "scrfd_onnx"
    assert faces[0]["model_id"] == "portrait/scrfd.onnx"
    assert faces[0]["landmark_schema"] == portrait_model_runtime.FACE_KEYPOINT_NAMES


@pytest.mark.asyncio
async def test_rtmpose_adapter_decodes_heatmaps(monkeypatch) -> None:
    async def fake_get_runtime(capability_name, adapters):
        if capability_name == "pose":
            return fake_runtime("pose", "rtmpose", [1, 3, 256, 192])
        return None

    async def fake_run(bundle, input_array):
        heatmaps = np.zeros((1, 17, 4, 4), dtype=np.float32)
        heatmaps[:, :, 2, 1] = 0.9
        return [heatmaps], 0.0, 0.0

    monkeypatch.setattr(portrait_model_runtime, "get_capability_runtime", fake_get_runtime)
    monkeypatch.setattr(portrait_model_runtime, "run_model_bundle", fake_run)

    pose = await portrait_model_runtime.infer_pose_record_for_image(Image.new("RGB", (192, 256), (60, 90, 120)))

    assert pose["model_status"] == "rtmpose_onnx"
    assert pose["keypoint_count"] == 17
    assert pose["keypoints"][0]["name"] == "nose"


@pytest.mark.asyncio
async def test_opengait_adapter_returns_normalized_embedding(monkeypatch) -> None:
    async def fake_get_runtime(capability_name, adapters):
        if capability_name == "gait":
            return fake_runtime("gait", "opengait", [1, 4, 1, 64, 44], embedding_dim=256)
        return None

    async def fake_run(bundle, input_array):
        assert input_array.shape == (1, 3, 1, 64, 44)
        return [np.ones((1, 256), dtype=np.float32)], 0.0, 0.0

    monkeypatch.setattr(portrait_model_runtime, "get_capability_runtime", fake_get_runtime)
    monkeypatch.setattr(portrait_model_runtime, "run_model_bundle", fake_run)

    embedding, meta = await portrait_model_runtime.infer_gait_embedding_for_images(
        [Image.new("RGB", (44, 64), (index * 20, 80, 120)) for index in range(3)]
    )

    assert embedding is not None
    assert len(embedding) == 256
    assert meta["model_status"] == "opengait_onnx"


@pytest.mark.asyncio
async def test_model_runtime_uses_fallback_when_no_production_model(monkeypatch) -> None:
    async def fake_get_runtime(capability_name, adapters):
        return None

    monkeypatch.setattr(portrait_model_runtime, "get_capability_runtime", fake_get_runtime)

    embedding, meta = await portrait_model_runtime.infer_gait_embedding_for_images(
        [Image.new("RGB", (44, 64), (20, 80, 120)), Image.new("RGB", (44, 64), (40, 80, 120))]
    )

    assert embedding is not None
    assert meta["model_status"] == "tracklet_fingerprint_fallback"
