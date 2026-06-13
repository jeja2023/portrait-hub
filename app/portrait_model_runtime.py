from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image

from app.geometry import crop_person, nms, restore_boxes
from app.media.quality import assess_image_quality, clamp01
from app.model_config import config_value, configured_input_size, model_config
from app.model_config_resolver import resolve_model_reference
from app.model_package import get_model_path
from app.portrait_compare import l2_normalize_vector
from app.portrait_embeddings import (
    FALLBACK_EMBEDDING_MODEL_ID,
    FALLBACK_EMBEDDING_VERSION,
    best_quality_index,
    detect_face_candidates,
    fallback_face_candidate,
    gait_embedding as fallback_gait_embedding,
    image_fingerprint_embedding,
    pose_record as fallback_pose_record,
)
from app.portrait_model_capabilities import capability_status, production_model_ready
from app.runtime_execution import run_model_bundle, run_model_bundle_batch
from app.runtime_registry import get_or_load_model
from app.schemas import LetterboxMeta, ModelBundle


FACE_DETECTION_FALLBACK_MODEL_ID = "opencv/haarcascade_frontalface_default"
FACE_KEYPOINT_NAMES = ["left_eye", "right_eye", "nose", "left_mouth", "right_mouth"]
COCO17_KEYPOINT_NAMES = [
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
]
COCO17_SKELETON = [
    ["left_eye", "right_eye"],
    ["left_eye", "nose"],
    ["right_eye", "nose"],
    ["left_eye", "left_ear"],
    ["right_eye", "right_ear"],
    ["left_shoulder", "right_shoulder"],
    ["left_shoulder", "left_elbow"],
    ["left_elbow", "left_wrist"],
    ["right_shoulder", "right_elbow"],
    ["right_elbow", "right_wrist"],
    ["left_shoulder", "left_hip"],
    ["right_shoulder", "right_hip"],
    ["left_hip", "right_hip"],
    ["left_hip", "left_knee"],
    ["left_knee", "left_ankle"],
    ["right_hip", "right_knee"],
    ["right_knee", "right_ankle"],
]
ARCFACE_CANONICAL_112 = np.asarray(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


@dataclass(frozen=True)
class CapabilityRuntime:
    capability_name: str
    model_id: str
    cache_key: str
    adapter: str
    capability: dict[str, Any]
    config: dict[str, Any]
    bundle: ModelBundle
    cold_loaded: bool = False
    load_seconds: float = 0.0

    @property
    def version(self) -> str:
        return str(self.config.get("version") or self.capability.get("version") or "")


async def get_capability_runtime(capability_name: str, adapters: Iterable[str]) -> CapabilityRuntime | None:
    capability = capability_status(capability_name)
    adapter = str(capability.get("adapter") or "").strip().lower()
    if adapter not in {item.lower() for item in adapters} or not production_model_ready(capability_name):
        return None
    model_id = str(capability.get("model_id") or "").strip()
    project_name, model_name, cache_key_value, _ = resolve_model_reference(model_id, None, None)
    model_path = get_model_path(project_name, model_name)
    bundle, cold_loaded, load_seconds = await get_or_load_model(cache_key_value, model_path)
    return CapabilityRuntime(
        capability_name=capability_name,
        model_id=cache_key_value,
        cache_key=cache_key_value,
        adapter=adapter,
        capability=capability,
        config=model_config(cache_key_value),
        bundle=bundle,
        cold_loaded=cold_loaded,
        load_seconds=load_seconds,
    )


def runtime_input_size(runtime: CapabilityRuntime, default: tuple[int, int]) -> tuple[int, int]:
    configured = runtime.capability.get("input_size")
    if isinstance(configured, list) and len(configured) == 2:
        try:
            height, width = int(configured[0]), int(configured[1])
            if height > 0 and width > 0:
                default = (height, width)
        except (TypeError, ValueError):
            pass
    if runtime.adapter == "opengait":
        shape = runtime.bundle["session"].get_inputs()[0].shape
        if len(shape) >= 5 and isinstance(shape[-2], int) and isinstance(shape[-1], int):
            if shape[-2] > 0 and shape[-1] > 0:
                return int(shape[-2]), int(shape[-1])
    return configured_input_size(runtime.cache_key, runtime.bundle["session"], default)


def runtime_input_value(runtime: CapabilityRuntime, key: str, default: Any = None) -> Any:
    capability_input = runtime.capability.get("input")
    capability_default = capability_input.get(key) if isinstance(capability_input, dict) and key in capability_input else runtime.capability.get(key, default)
    return config_value(runtime.config, "input", key, capability_default)


def runtime_output_value(runtime: CapabilityRuntime, key: str, default: Any = None) -> Any:
    capability_output = runtime.capability.get("output")
    capability_default = capability_output.get(key) if isinstance(capability_output, dict) and key in capability_output else runtime.capability.get(key, default)
    return config_value(runtime.config, "output", key, capability_default)


def preprocess_rgb_array(image: Image.Image, *, normalize: str = "none", color: str = "rgb") -> np.ndarray:
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    if str(color).strip().lower() == "bgr":
        array = array[:, :, ::-1]
    normalize_key = str(normalize or "none").strip().lower()
    if normalize_key in {"rgb_minus_127p5_div_128", "minus_127p5_div_128", "arcface"}:
        array = (array - 127.5) / 128.0
    elif normalize_key == "imagenet":
        array = array / 255.0
        mean = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
        array = (array - mean) / std
    elif normalize_key in {"raw", "uint8"}:
        pass
    else:
        array = array / 255.0
    return np.transpose(array, (2, 0, 1)).astype(np.float32)


def resize_tensor(
    image: Image.Image,
    input_height: int,
    input_width: int,
    *,
    normalize: str = "none",
    color: str = "rgb",
) -> np.ndarray:
    resized = image.resize((input_width, input_height), Image.Resampling.BILINEAR)
    return preprocess_rgb_array(resized, normalize=normalize, color=color)[None, :, :, :]


def letterbox_tensor(
    image: Image.Image,
    input_height: int,
    input_width: int,
    *,
    normalize: str = "none",
    color: str = "rgb",
) -> tuple[np.ndarray, LetterboxMeta]:
    original_width, original_height = image.size
    scale = min(input_width / max(1, original_width), input_height / max(1, original_height))
    resized_width = max(1, int(round(original_width * scale)))
    resized_height = max(1, int(round(original_height * scale)))
    pad_left = (input_width - resized_width) / 2
    pad_top = (input_height - resized_height) / 2
    resized = image.convert("RGB").resize((resized_width, resized_height), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (input_width, input_height), (114, 114, 114))
    canvas.paste(resized, (int(round(pad_left - 0.1)), int(round(pad_top - 0.1))))
    meta: LetterboxMeta = {
        "original_width": original_width,
        "original_height": original_height,
        "input_width": input_width,
        "input_height": input_height,
        "scale": scale,
        "pad_left": pad_left,
        "pad_top": pad_top,
    }
    return preprocess_rgb_array(canvas, normalize=normalize, color=color)[None, :, :, :], meta


def batch_slice(output: Any, batch_index: int, batch_size: int) -> np.ndarray:
    array = np.asarray(output)
    if array.ndim >= 3 and array.shape[0] == batch_size:
        return np.asarray(array[batch_index])
    return array


def rows_with_last_dim(array: np.ndarray) -> np.ndarray:
    if array.ndim == 0:
        return array.reshape(1, 1)
    if array.ndim == 1:
        return array.reshape(1, -1)
    return array.reshape(-1, array.shape[-1])


def normalize_scores(scores: np.ndarray) -> np.ndarray:
    scores = np.asarray(scores, dtype=np.float32).reshape(-1)
    if scores.size and (float(scores.min()) < 0.0 or float(scores.max()) > 1.0):
        scores = 1.0 / (1.0 + np.exp(-scores))
    return scores


def infer_scrfd_stride(count: int, input_height: int, input_width: int) -> tuple[int, int] | None:
    for stride in (8, 16, 32, 4):
        grid_h = math.ceil(input_height / stride)
        grid_w = math.ceil(input_width / stride)
        for anchor_count in (1, 2):
            if grid_h * grid_w * anchor_count == count:
                return stride, anchor_count
    return None


def scrfd_anchor_centers(count: int, input_height: int, input_width: int) -> tuple[np.ndarray, int] | None:
    inferred = infer_scrfd_stride(count, input_height, input_width)
    if inferred is None:
        return None
    stride, anchor_count = inferred
    grid_h = math.ceil(input_height / stride)
    grid_w = math.ceil(input_width / stride)
    ys, xs = np.mgrid[:grid_h, :grid_w]
    centers = np.stack([(xs.astype(np.float32) + 0.5) * stride, (ys.astype(np.float32) + 0.5) * stride], axis=-1)
    centers = np.repeat(centers.reshape(-1, 2), anchor_count, axis=0)
    return centers[:count], stride


def decode_scrfd_anchor_outputs(
    boxes: np.ndarray,
    landmarks: np.ndarray | None,
    *,
    input_height: int,
    input_width: int,
    scale: str | float,
) -> tuple[np.ndarray, np.ndarray | None]:
    anchors = scrfd_anchor_centers(len(boxes), input_height, input_width)
    if anchors is None:
        return boxes, landmarks
    centers, stride = anchors
    if isinstance(scale, str) and scale == "stride":
        factor = float(stride)
    else:
        try:
            factor = float(scale)
        except (TypeError, ValueError):
            factor = float(stride)
    distances = boxes.astype(np.float32) * factor
    decoded_boxes = np.empty_like(distances, dtype=np.float32)
    decoded_boxes[:, 0] = centers[:, 0] - distances[:, 0]
    decoded_boxes[:, 1] = centers[:, 1] - distances[:, 1]
    decoded_boxes[:, 2] = centers[:, 0] + distances[:, 2]
    decoded_boxes[:, 3] = centers[:, 1] + distances[:, 3]
    decoded_landmarks = None
    if landmarks is not None:
        decoded_landmarks = landmarks.reshape(-1, 5, 2).astype(np.float32) * factor + centers[:, None, :]
    return decoded_boxes, decoded_landmarks


def match_rows_by_length(rows: list[np.ndarray], length: int, used: set[int]) -> np.ndarray | None:
    for index, row in enumerate(rows):
        if index not in used and len(row) == length:
            used.add(index)
            return row
    return None


def parse_scrfd_outputs(
    raw_outputs: list[np.ndarray],
    *,
    batch_index: int,
    batch_size: int,
    input_height: int,
    input_width: int,
    decoded_output: bool,
    bbox_scale: str | float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    combined_rows: list[np.ndarray] = []
    score_rows: list[np.ndarray] = []
    box_rows: list[np.ndarray] = []
    landmark_rows: list[np.ndarray] = []
    for output in raw_outputs:
        rows = rows_with_last_dim(batch_slice(output, batch_index, batch_size))
        if rows.shape[1] >= 5:
            combined_rows.append(rows)
        elif rows.shape[1] == 4:
            box_rows.append(rows.astype(np.float32))
        elif rows.shape[1] in {1, 2}:
            score_rows.append(rows[:, -1].astype(np.float32))
        elif rows.shape[1] == 10:
            landmark_rows.append(rows.astype(np.float32))

    if combined_rows:
        rows = np.concatenate(combined_rows, axis=0).astype(np.float32)
        boxes = rows[:, :4]
        scores = normalize_scores(rows[:, 4])
        landmarks = rows[:, 5:15].reshape(-1, 5, 2) if rows.shape[1] >= 15 else None
        return boxes, scores, landmarks

    decoded_boxes: list[np.ndarray] = []
    decoded_scores: list[np.ndarray] = []
    decoded_landmarks: list[np.ndarray] = []
    used_scores: set[int] = set()
    used_landmarks: set[int] = set()
    for boxes in box_rows:
        scores = match_rows_by_length(score_rows, len(boxes), used_scores)
        landmarks = match_rows_by_length(landmark_rows, len(boxes), used_landmarks)
        if scores is None:
            scores = np.ones((len(boxes),), dtype=np.float32)
        if not decoded_output:
            boxes, decoded_lm = decode_scrfd_anchor_outputs(
                boxes,
                landmarks,
                input_height=input_height,
                input_width=input_width,
                scale=bbox_scale,
            )
        else:
            decoded_lm = landmarks.reshape(-1, 5, 2) if landmarks is not None else None
        decoded_boxes.append(boxes)
        decoded_scores.append(normalize_scores(scores))
        if decoded_lm is not None:
            decoded_landmarks.append(decoded_lm)

    if not decoded_boxes:
        return np.empty((0, 4), dtype=np.float32), np.empty((0,), dtype=np.float32), None
    landmarks_out = np.concatenate(decoded_landmarks, axis=0) if decoded_landmarks else None
    return np.concatenate(decoded_boxes, axis=0), np.concatenate(decoded_scores, axis=0), landmarks_out


def restore_landmarks(landmarks: np.ndarray | None, meta: LetterboxMeta) -> np.ndarray | None:
    if landmarks is None:
        return None
    restored = landmarks.copy().astype(np.float32)
    restored[:, :, 0] = (restored[:, :, 0] - meta["pad_left"]) / meta["scale"]
    restored[:, :, 1] = (restored[:, :, 1] - meta["pad_top"]) / meta["scale"]
    restored[:, :, 0] = np.clip(restored[:, :, 0], 0, meta["original_width"])
    restored[:, :, 1] = np.clip(restored[:, :, 1], 0, meta["original_height"])
    return restored


def face_crop_from_box(image: Image.Image, box: list[float]) -> Image.Image:
    crop = crop_person(image, box, min_size=2)
    return crop if crop is not None else image


def strip_face_crop(face: dict[str, Any]) -> dict[str, Any]:
    face.pop("crop", None)
    return face


async def run_scrfd_face_detection(image: Image.Image) -> list[dict[str, Any]] | None:
    runtime = await get_capability_runtime("face_detection", {"scrfd"})
    if runtime is None:
        return None
    input_height, input_width = runtime_input_size(runtime, (640, 640))
    normalize = str(runtime_input_value(runtime, "normalize", runtime.capability.get("preprocess", "none")))
    color = str(runtime_input_value(runtime, "color", "rgb"))
    tensor, meta = letterbox_tensor(image, input_height, input_width, normalize=normalize, color=color)
    raw_outputs, _, _, _ = await run_model_bundle_batch(runtime.bundle, [tensor])
    boxes, scores, landmarks = parse_scrfd_outputs(
        raw_outputs,
        batch_index=0,
        batch_size=1,
        input_height=input_height,
        input_width=input_width,
        decoded_output=bool(runtime_output_value(runtime, "decoded", runtime.capability.get("decoded_output", False))),
        bbox_scale=runtime_output_value(runtime, "bbox_scale", runtime.capability.get("bbox_scale", "stride")),
    )
    if boxes.size == 0:
        return []
    confidence = float(runtime_output_value(runtime, "confidence", runtime.capability.get("confidence", 0.35)))
    iou_threshold = float(runtime_output_value(runtime, "iou", runtime.capability.get("iou", 0.45)))
    max_detections = int(runtime_output_value(runtime, "max_detections", runtime.capability.get("max_detections", 32)))
    keep_mask = scores >= confidence
    boxes = boxes[keep_mask]
    scores = scores[keep_mask]
    landmarks = landmarks[keep_mask] if landmarks is not None and len(landmarks) == len(keep_mask) else None
    if boxes.size == 0:
        return []
    restored_boxes = restore_boxes(boxes, meta)
    restored_landmarks = restore_landmarks(landmarks, meta)
    keep = nms(restored_boxes, scores, iou_threshold)[:max_detections]
    faces: list[dict[str, Any]] = []
    for face_index, raw_index in enumerate(keep):
        box = restored_boxes[raw_index].tolist()
        crop = face_crop_from_box(image, box)
        quality = assess_image_quality(crop)
        face_area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        area_score = clamp01(face_area / max(1.0, float(image.width * image.height)) / 0.12)
        landmarks_payload: list[list[float]] = []
        if restored_landmarks is not None:
            landmarks_payload = [
                [round(float(point[0]), 2), round(float(point[1]), 2)]
                for point in restored_landmarks[raw_index].tolist()
            ]
        faces.append(
            {
                "face_index": face_index,
                "box": [round(float(value), 2) for value in box],
                "score": round(float(scores[raw_index]), 6),
                "landmarks": landmarks_payload,
                "landmark_schema": FACE_KEYPOINT_NAMES if landmarks_payload else [],
                "quality": {**quality, "detection_area_score": round(area_score, 6)},
                "embedding_dim": 0,
                "detection_strategy": "scrfd_onnx",
                "model_id": runtime.model_id,
                "model_version": runtime.version,
                "model_status": "scrfd_onnx",
                "adapter": runtime.adapter,
                "crop": crop,
            }
        )
    return faces


def arcface_aligned_crop(
    image: Image.Image,
    *,
    crop: Image.Image,
    landmarks: list[list[float]] | None,
    output_height: int,
    output_width: int,
) -> Image.Image:
    if not landmarks or len(landmarks) < 5:
        return crop
    points = np.asarray(landmarks[:5], dtype=np.float32)
    if points.shape != (5, 2):
        return crop
    dst = ARCFACE_CANONICAL_112.copy()
    dst[:, 0] *= output_width / 112.0
    dst[:, 1] *= output_height / 112.0
    try:
        matrix, _ = cv2.estimateAffinePartial2D(points, dst, method=cv2.LMEDS)
    except Exception:
        matrix = None
    if matrix is None:
        return crop
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    aligned = cv2.warpAffine(rgb, matrix, (output_width, output_height), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    return Image.fromarray(aligned)


def embedding_rows(raw_outputs: list[np.ndarray], batch_size: int) -> np.ndarray:
    for output in raw_outputs:
        array = np.asarray(output, dtype=np.float32)
        if array.ndim == 1:
            return array.reshape(1, -1)
        if array.ndim >= 2 and array.shape[0] == batch_size:
            return array.reshape(batch_size, -1)
        if batch_size == 1:
            return array.reshape(1, -1)
    return np.empty((0, 0), dtype=np.float32)


def round_normalized_embedding(vector: np.ndarray | list[float]) -> list[float]:
    normalized = l2_normalize_vector(vector)
    return [round(float(value), 8) for value in normalized.tolist()]


async def apply_arcface_embeddings(image: Image.Image, faces: list[dict[str, Any]]) -> bool:
    runtime = await get_capability_runtime("face_embedding", {"arcface"})
    if runtime is None or not faces:
        return False
    input_height, input_width = runtime_input_size(runtime, (112, 112))
    normalize = str(runtime_input_value(runtime, "normalize", runtime.capability.get("preprocess", "rgb_minus_127p5_div_128")))
    color = str(runtime_input_value(runtime, "color", "rgb"))
    inputs: list[np.ndarray] = []
    for face in faces:
        crop = face.get("crop")
        if not isinstance(crop, Image.Image):
            crop = face_crop_from_box(image, [float(value) for value in face.get("box", [0, 0, image.width, image.height])[:4]])
            face["crop"] = crop
        aligned = arcface_aligned_crop(
            image,
            crop=crop,
            landmarks=face.get("landmarks") if isinstance(face.get("landmarks"), list) else None,
            output_height=input_height,
            output_width=input_width,
        )
        inputs.append(resize_tensor(aligned, input_height, input_width, normalize=normalize, color=color))
    raw_outputs, _, _, mode = await run_model_bundle_batch(runtime.bundle, inputs)
    rows = embedding_rows(raw_outputs, len(inputs))
    if rows.shape[0] != len(faces):
        return False
    for face, vector in zip(faces, rows):
        embedding = round_normalized_embedding(vector)
        face["embedding"] = embedding
        face["embedding_dim"] = len(embedding)
        face["embedding_model_id"] = runtime.model_id
        face["embedding_model_version"] = runtime.version
        face["embedding_model_status"] = "arcface_onnx"
        face["embedding_adapter"] = runtime.adapter
        face["embedding_batch_mode"] = mode
    return True


def apply_fallback_face_embeddings(faces: list[dict[str, Any]]) -> None:
    for face in faces:
        crop = face.get("crop")
        if not isinstance(crop, Image.Image):
            continue
        embedding = image_fingerprint_embedding(crop)
        face["embedding"] = embedding
        face["embedding_dim"] = len(embedding)
        face["embedding_model_id"] = FALLBACK_EMBEDDING_MODEL_ID
        face["embedding_model_version"] = FALLBACK_EMBEDDING_VERSION
        face["embedding_model_status"] = "image_fingerprint_fallback"


def fallback_face_records_with_crops(image: Image.Image, *, fallback: bool) -> list[dict[str, Any]]:
    faces = detect_face_candidates(image)
    if not faces and fallback:
        faces = [fallback_face_candidate(image)]
    for face in faces:
        face.setdefault("model_id", FACE_DETECTION_FALLBACK_MODEL_ID)
        face.setdefault("model_version", FALLBACK_EMBEDDING_VERSION)
        face.setdefault("model_status", face.get("detection_strategy", "opencv_haar_bounded"))
    return faces


async def infer_face_records_for_image(
    image: Image.Image,
    *,
    include_embeddings: bool = False,
    fallback: bool = False,
) -> list[dict[str, Any]]:
    faces = await run_scrfd_face_detection(image)
    if faces is None:
        faces = fallback_face_records_with_crops(image, fallback=fallback)
    elif not faces and fallback:
        faces = [fallback_face_candidate(image)]

    if include_embeddings:
        arcface_applied = await apply_arcface_embeddings(image, faces)
        if not arcface_applied:
            apply_fallback_face_embeddings(faces)
    return [strip_face_crop(face) for face in faces]


async def infer_best_face_embedding_for_image(image: Image.Image) -> tuple[list[float], dict[str, Any]]:
    faces = await infer_face_records_for_image(image, include_embeddings=True, fallback=True)
    index = best_quality_index(faces)
    selected = faces[index]
    return selected["embedding"], selected


def softmax_max(logits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    logits = np.asarray(logits, dtype=np.float32)
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    probs = exp / np.maximum(exp.sum(axis=-1, keepdims=True), 1e-12)
    return probs.argmax(axis=-1), probs.max(axis=-1)


def scale_pose_point(
    x: float,
    y: float,
    *,
    image_width: int,
    image_height: int,
    input_width: int,
    input_height: int,
) -> tuple[float, float]:
    if 0.0 <= x <= 1.5 and 0.0 <= y <= 1.5:
        return x * image_width, y * image_height
    return x / max(1.0, float(input_width)) * image_width, y / max(1.0, float(input_height)) * image_height


def decode_rtmpose_outputs(
    raw_outputs: list[np.ndarray],
    *,
    image_width: int,
    image_height: int,
    input_width: int,
    input_height: int,
) -> tuple[np.ndarray, np.ndarray]:
    arrays = [np.asarray(output, dtype=np.float32) for output in raw_outputs]
    for array in arrays:
        sliced = batch_slice(array, 0, 1)
        if sliced.ndim == 3 and sliced.shape[0] == len(COCO17_KEYPOINT_NAMES):
            keypoint_count, heatmap_h, heatmap_w = sliced.shape
            coords = np.zeros((keypoint_count, 2), dtype=np.float32)
            scores = np.zeros((keypoint_count,), dtype=np.float32)
            for index in range(keypoint_count):
                flat_index = int(np.argmax(sliced[index]))
                y, x = divmod(flat_index, heatmap_w)
                coords[index, 0] = (x + 0.5) / max(1.0, float(heatmap_w)) * image_width
                coords[index, 1] = (y + 0.5) / max(1.0, float(heatmap_h)) * image_height
                scores[index] = clamp01(float(sliced[index].max()))
            return coords, scores
        if sliced.ndim == 3 and sliced.shape[-1] == 2:
            coords = sliced.reshape(-1, 2)[: len(COCO17_KEYPOINT_NAMES)]
            scores = np.ones((coords.shape[0],), dtype=np.float32)
            for score_array in arrays:
                score_rows = rows_with_last_dim(batch_slice(score_array, 0, 1))
                if len(score_rows) == len(coords) and score_rows.shape[1] in {1, 2}:
                    scores = normalize_scores(score_rows[:, -1])[: len(coords)]
                    break
            scaled = np.asarray(
                [
                    scale_pose_point(
                        float(point[0]),
                        float(point[1]),
                        image_width=image_width,
                        image_height=image_height,
                        input_width=input_width,
                        input_height=input_height,
                    )
                    for point in coords
                ],
                dtype=np.float32,
            )
            return scaled, scores
    if len(arrays) >= 2:
        x_logits = batch_slice(arrays[0], 0, 1)
        y_logits = batch_slice(arrays[1], 0, 1)
        if x_logits.ndim == 2 and y_logits.ndim == 2 and x_logits.shape[0] == y_logits.shape[0]:
            x_index, x_score = softmax_max(x_logits)
            y_index, y_score = softmax_max(y_logits)
            count = min(len(x_index), len(COCO17_KEYPOINT_NAMES))
            coords = np.asarray(
                [
                    [
                        float(x_index[index]) / max(1.0, x_logits.shape[1] - 1) * image_width,
                        float(y_index[index]) / max(1.0, y_logits.shape[1] - 1) * image_height,
                    ]
                    for index in range(count)
                ],
                dtype=np.float32,
            )
            scores = np.asarray([clamp01(float((x_score[index] + y_score[index]) / 2.0)) for index in range(count)], dtype=np.float32)
            return coords, scores
    return np.empty((0, 2), dtype=np.float32), np.empty((0,), dtype=np.float32)


async def run_rtmpose(image: Image.Image) -> dict[str, Any] | None:
    runtime = await get_capability_runtime("pose", {"rtmpose"})
    if runtime is None:
        return None
    input_height, input_width = runtime_input_size(runtime, (256, 192))
    normalize = str(runtime_input_value(runtime, "normalize", "imagenet"))
    color = str(runtime_input_value(runtime, "color", "rgb"))
    tensor = resize_tensor(image, input_height, input_width, normalize=normalize, color=color)
    raw_outputs, _, _ = await run_model_bundle(runtime.bundle, tensor)
    coords, scores = decode_rtmpose_outputs(
        raw_outputs,
        image_width=image.width,
        image_height=image.height,
        input_width=input_width,
        input_height=input_height,
    )
    keypoints = [
        {
            "name": COCO17_KEYPOINT_NAMES[index],
            "point": [round(float(point[0]), 2), round(float(point[1]), 2)],
            "score": round(float(scores[index]), 6),
        }
        for index, point in enumerate(coords[: len(COCO17_KEYPOINT_NAMES)])
    ]
    return {
        "quality": assess_image_quality(image),
        "keypoints": keypoints,
        "skeleton": COCO17_SKELETON,
        "model_id": runtime.model_id,
        "model_version": runtime.version,
        "model_status": "rtmpose_onnx",
        "adapter": runtime.adapter,
        "keypoint_schema": "coco17",
        "keypoint_count": len(keypoints),
    }


async def infer_pose_record_for_image(image: Image.Image) -> dict[str, Any]:
    record = await run_rtmpose(image)
    return record if record is not None else fallback_pose_record(image)


def gait_sequence_tensor(images: list[Image.Image], input_height: int, input_width: int, *, layout: str = "ntchw") -> np.ndarray:
    frames: list[np.ndarray] = []
    for image in images:
        gray = image.convert("L").resize((input_width, input_height), Image.Resampling.BILINEAR)
        frames.append(np.asarray(gray, dtype=np.float32) / 255.0)
    sequence = np.stack(frames, axis=0)
    layout_key = str(layout or "ntchw").strip().lower()
    if layout_key in {"ncthw", "batch_channel_time_height_width"}:
        return sequence[None, None, :, :, :].astype(np.float32)
    if layout_key in {"nthw", "batch_time_height_width"}:
        return sequence[None, :, :, :].astype(np.float32)
    return sequence[None, :, None, :, :].astype(np.float32)


async def run_opengait(images: list[Image.Image]) -> tuple[list[float] | None, dict[str, Any]] | None:
    runtime = await get_capability_runtime("gait", {"opengait"})
    if runtime is None:
        return None
    if len(images) < 2:
        return None, {"quality": None, "reason": "not_enough_frames", "tracklet_frames": len(images)}
    input_height, input_width = runtime_input_size(runtime, (64, 44))
    layout = str(runtime_input_value(runtime, "layout", runtime.capability.get("sequence_layout", "ntchw")))
    tensor = gait_sequence_tensor(images, input_height, input_width, layout=layout)
    raw_outputs, _, _ = await run_model_bundle(runtime.bundle, tensor)
    rows = embedding_rows(raw_outputs, 1)
    if rows.size == 0:
        return None, {"quality": None, "reason": "embedding_missing", "tracklet_frames": len(images)}
    qualities = [float(assess_image_quality(image).get("score", 0.0)) for image in images]
    embedding = round_normalized_embedding(rows[0])
    return embedding, {
        "quality": round(float(np.mean(qualities)), 6) if qualities else None,
        "tracklet_frames": len(images),
        "embedding_dim": len(embedding),
        "model_id": runtime.model_id,
        "model_version": runtime.version,
        "model_status": "opengait_onnx",
        "adapter": runtime.adapter,
        "sequence_layout": layout,
    }


async def infer_gait_embedding_for_images(images: list[Image.Image]) -> tuple[list[float] | None, dict[str, Any]]:
    result = await run_opengait(images)
    if result is not None:
        return result
    return fallback_gait_embedding(images)


def embedding_model_info(subject: dict[str, Any]) -> tuple[str, str]:
    model_id = subject.get("embedding_model_id") or subject.get("model_id") or FALLBACK_EMBEDDING_MODEL_ID
    version = subject.get("embedding_model_version") or subject.get("model_version") or FALLBACK_EMBEDDING_VERSION
    return str(model_id), str(version)


def face_model_summary(faces: list[dict[str, Any]], *, include_embeddings: bool) -> dict[str, Any]:
    face = faces[0] if faces else {}
    return {
        "id": face.get("model_id", FACE_DETECTION_FALLBACK_MODEL_ID),
        "version": face.get("model_version", FALLBACK_EMBEDDING_VERSION),
        "status": face.get("model_status", "opencv_haar_bounded"),
        "adapter": face.get("adapter", "haar_face_detection"),
        "fallback_embedding_model_id": FALLBACK_EMBEDDING_MODEL_ID if include_embeddings else None,
        "embedding_model_id": face.get("embedding_model_id") if include_embeddings else None,
        "embedding_model_version": face.get("embedding_model_version") if include_embeddings else None,
        "embedding_model_status": face.get("embedding_model_status") if include_embeddings else None,
    }
