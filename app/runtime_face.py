from __future__ import annotations

from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image

from app.geometry import crop_person, nms, restore_boxes
from app.media.quality import assess_image_quality, clamp01
from app.portrait_embeddings import (
    FALLBACK_EMBEDDING_MODEL_ID,
    FALLBACK_EMBEDDING_VERSION,
    best_quality_index,
    detect_face_candidates,
    fallback_face_candidate,
    image_fingerprint_embedding,
)
from app.portrait_model_runtime_capability import (
    get_capability_runtime,
    runtime_input_size,
    runtime_input_value,
    runtime_output_value,
)
from app.portrait_model_runtime_preprocess import batch_slice, letterbox_tensor, resize_tensor
from app.runtime_common import embedding_rows, normalize_scores, round_normalized_embedding, rows_with_last_dim
from app.runtime_execution import run_model_bundle_batch
from app.schemas import LetterboxMeta

Array = npt.NDArray[Any]

FACE_DETECTION_FALLBACK_MODEL_ID = "opencv/haarcascade_frontalface_default"
FACE_KEYPOINT_NAMES = ["left_eye", "right_eye", "nose", "left_mouth", "right_mouth"]
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


def infer_scrfd_stride(count: int, input_height: int, input_width: int) -> tuple[int, int] | None:
    for stride in (8, 16, 32, 4):
        grid_h = int(np.ceil(input_height / stride))
        grid_w = int(np.ceil(input_width / stride))
        for anchor_count in (1, 2):
            if grid_h * grid_w * anchor_count == count:
                return stride, anchor_count
    return None


def scrfd_anchor_centers(count: int, input_height: int, input_width: int) -> tuple[Array, int] | None:
    inferred = infer_scrfd_stride(count, input_height, input_width)
    if inferred is None:
        return None
    stride, anchor_count = inferred
    grid_h = int(np.ceil(input_height / stride))
    grid_w = int(np.ceil(input_width / stride))
    ys, xs = np.mgrid[:grid_h, :grid_w]
    centers = np.stack([(xs.astype(np.float32) + 0.5) * stride, (ys.astype(np.float32) + 0.5) * stride], axis=-1)
    centers = np.repeat(centers.reshape(-1, 2), anchor_count, axis=0)
    return centers[:count], stride


def decode_scrfd_anchor_outputs(
    boxes: Array,
    landmarks: Array | None,
    *,
    input_height: int,
    input_width: int,
    scale: str | float,
) -> tuple[Array, Array | None]:
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


def match_rows_by_length(rows: list[Array], length: int, used: set[int]) -> Array | None:
    for index, row in enumerate(rows):
        if index not in used and len(row) == length:
            used.add(index)
            return row
    return None


def parse_scrfd_outputs(
    raw_outputs: list[Array],
    *,
    batch_index: int,
    batch_size: int,
    input_height: int,
    input_width: int,
    decoded_output: bool,
    bbox_scale: str | float,
) -> tuple[Array, Array, Array | None]:
    combined_rows: list[Array] = []
    score_rows: list[Array] = []
    box_rows: list[Array] = []
    landmark_rows: list[Array] = []
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

    decoded_boxes: list[Array] = []
    decoded_scores: list[Array] = []
    decoded_landmarks: list[Array] = []
    used_scores: set[int] = set()
    used_landmarks: set[int] = set()
    for boxes in box_rows:
        matched_scores = match_rows_by_length(score_rows, len(boxes), used_scores)
        matched_landmarks = match_rows_by_length(landmark_rows, len(boxes), used_landmarks)
        scores = matched_scores if matched_scores is not None else np.ones((len(boxes),), dtype=np.float32)
        if not decoded_output:
            boxes, decoded_lm = decode_scrfd_anchor_outputs(
                boxes,
                matched_landmarks,
                input_height=input_height,
                input_width=input_width,
                scale=bbox_scale,
            )
        else:
            decoded_lm = matched_landmarks.reshape(-1, 5, 2) if matched_landmarks is not None else None
        decoded_boxes.append(boxes)
        decoded_scores.append(normalize_scores(scores))
        if decoded_lm is not None:
            decoded_landmarks.append(decoded_lm)

    if not decoded_boxes:
        return np.empty((0, 4), dtype=np.float32), np.empty((0,), dtype=np.float32), None
    landmarks_out = np.concatenate(decoded_landmarks, axis=0) if decoded_landmarks else None
    return np.concatenate(decoded_boxes, axis=0), np.concatenate(decoded_scores, axis=0), landmarks_out


def restore_landmarks(landmarks: Array | None, meta: LetterboxMeta) -> Array | None:
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


async def run_scrfd_face_detection(
    image: Image.Image,
    *,
    confidence_override: float | None = None,
    iou_override: float | None = None,
    max_detections_override: int | None = None,
) -> list[dict[str, Any]] | None:
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
    confidence = float(
        confidence_override
        if confidence_override is not None
        else runtime_output_value(runtime, "confidence", runtime.capability.get("confidence", 0.35))
    )
    iou_threshold = float(
        iou_override
        if iou_override is not None
        else runtime_output_value(runtime, "iou", runtime.capability.get("iou", 0.45))
    )
    max_detections = int(
        max_detections_override
        if max_detections_override is not None
        else runtime_output_value(runtime, "max_detections", runtime.capability.get("max_detections", 32))
    )
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


async def apply_arcface_embeddings(image: Image.Image, faces: list[dict[str, Any]]) -> bool:
    runtime = await get_capability_runtime("face_embedding", {"arcface"})
    if runtime is None or not faces:
        return False
    input_height, input_width = runtime_input_size(runtime, (112, 112))
    normalize = str(runtime_input_value(runtime, "normalize", runtime.capability.get("preprocess", "rgb_minus_127p5_div_128")))
    color = str(runtime_input_value(runtime, "color", "rgb"))
    inputs: list[Array] = []
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
    for face, vector in zip(faces, rows, strict=False):
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
    confidence: float | None = None,
    iou: float | None = None,
    max_detections: int | None = None,
) -> list[dict[str, Any]]:
    faces = await run_scrfd_face_detection(
        image,
        confidence_override=confidence,
        iou_override=iou,
        max_detections_override=max_detections,
    )
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


__all__ = [
    "ARCFACE_CANONICAL_112",
    "FACE_DETECTION_FALLBACK_MODEL_ID",
    "FACE_KEYPOINT_NAMES",
    "apply_arcface_embeddings",
    "apply_fallback_face_embeddings",
    "arcface_aligned_crop",
    "decode_scrfd_anchor_outputs",
    "embedding_model_info",
    "face_crop_from_box",
    "face_model_summary",
    "fallback_face_records_with_crops",
    "get_capability_runtime",
    "infer_best_face_embedding_for_image",
    "infer_face_records_for_image",
    "infer_scrfd_stride",
    "match_rows_by_length",
    "parse_scrfd_outputs",
    "restore_landmarks",
    "run_model_bundle_batch",
    "run_scrfd_face_detection",
    "scrfd_anchor_centers",
    "strip_face_crop",
]
