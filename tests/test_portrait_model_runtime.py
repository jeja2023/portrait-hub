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
async def test_reid_body_embedding_adapter_returns_model_metadata(monkeypatch) -> None:
    async def fake_get_runtime(capability_name, adapters):
        if capability_name == "body_embedding":
            return fake_runtime("body_embedding", "reid", [1, 3, 256, 128], embedding_dim=512)
        return None

    async def fake_infer_reid_images(bundle, key, images):
        assert key == "portrait/reid.onnx"
        assert len(images) == 1
        return np.ones((1, 512), dtype=np.float32), {
            "input_shape": [1, 3, 256, 128],
            "output_shapes": [[1, 512]],
            "inference_mode": "single",
            "embedding_dim": 512,
        }

    monkeypatch.setattr(portrait_model_runtime, "_BODY_EMBEDDING_RUNTIME_UNAVAILABLE", False)
    monkeypatch.setattr(portrait_model_runtime, "_PERSON_DETECTION_RUNTIME_UNAVAILABLE", False)
    monkeypatch.setattr(portrait_model_runtime, "get_capability_runtime", fake_get_runtime)
    monkeypatch.setattr(portrait_model_runtime, "infer_reid_images", fake_infer_reid_images)

    record = await portrait_model_runtime.infer_body_record_for_image(Image.new("RGB", (128, 256), (80, 120, 160)))

    assert len(record["embedding"]) == 512
    assert record["embedding_dim"] == 512
    assert record["embedding_model_status"] == "reid_onnx"
    assert record["embedding_model_id"] == "portrait/reid.onnx"
    assert record["selection_strategy"] == "whole_image_reid"


@pytest.mark.asyncio
async def test_reid_body_embedding_uses_person_detector_crop(monkeypatch) -> None:
    async def fake_get_runtime(capability_name, adapters):
        if capability_name == "body_embedding":
            return fake_runtime("body_embedding", "reid", [1, 3, 256, 128], embedding_dim=512)
        if capability_name == "person_detection":
            return fake_runtime("person_detection", "yolo", [1, 3, 640, 640])
        return None

    async def fake_infer_person_frames(bundle, key, images, filenames, confidence, iou, max_detections):
        assert key == "portrait/yolo.onnx"
        return [
            {
                "frame_index": 0,
                "width": 128,
                "height": 256,
                "persons": [{"box": [32.0, 40.0, 96.0, 200.0], "score": 0.91}],
                "person_count": 1,
            }
        ], {"inference_mode": "single"}

    async def fake_infer_reid_images(bundle, key, images):
        assert key == "portrait/reid.onnx"
        assert images[0].size == (64, 160)
        return np.ones((1, 512), dtype=np.float32), {
            "input_shape": [1, 3, 256, 128],
            "output_shapes": [[1, 512]],
            "inference_mode": "single",
            "embedding_dim": 512,
        }

    monkeypatch.setattr(portrait_model_runtime, "_BODY_EMBEDDING_RUNTIME_UNAVAILABLE", False)
    monkeypatch.setattr(portrait_model_runtime, "_PERSON_DETECTION_RUNTIME_UNAVAILABLE", False)
    monkeypatch.setattr(portrait_model_runtime, "get_capability_runtime", fake_get_runtime)
    monkeypatch.setattr(portrait_model_runtime, "infer_person_frames", fake_infer_person_frames)
    monkeypatch.setattr(portrait_model_runtime, "infer_reid_images", fake_infer_reid_images)

    record = await portrait_model_runtime.infer_body_record_for_image(Image.new("RGB", (128, 256), (80, 120, 160)))

    assert record["selection_strategy"] == "person_detection_crop_reid"
    assert record["box"] == [32.0, 40.0, 96.0, 200.0]
    assert record["detection_model_status"] == "yolo_onnx"
    assert record["detection_count"] == 1


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
async def test_attribute_reid_appearance_adapter_returns_model_metadata(monkeypatch) -> None:
    async def fake_get_runtime(capability_name, adapters):
        if capability_name == "appearance":
            return fake_runtime("appearance", "attribute_reid", [1, 3, 256, 128], embedding_dim=256)
        return None

    async def fake_run(bundle, input_array):
        assert input_array.shape == (1, 3, 256, 128)
        return [np.ones((1, 256), dtype=np.float32)], 0.0, 0.0

    monkeypatch.setattr(portrait_model_runtime, "get_capability_runtime", fake_get_runtime)
    monkeypatch.setattr(portrait_model_runtime, "run_model_bundle", fake_run)

    record = await portrait_model_runtime.infer_appearance_record_for_image(
        Image.new("RGB", (128, 256), (80, 120, 160)),
        include_embedding=True,
    )

    assert len(record["embedding"]) == 256
    assert record["embedding_dim"] == 256
    assert record["model_status"] == "attribute_reid_onnx"
    assert record["model_id"] == "portrait/attribute_reid.onnx"
    assert record["adapter"] == "attribute_reid"


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
