from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from app.media.quality import assess_image_quality, clamp01
from app.portrait_embeddings import pose_record as fallback_pose_record
from app.portrait_model_runtime_capability import get_capability_runtime, runtime_input_size, runtime_input_value
from app.portrait_model_runtime_preprocess import batch_slice, resize_tensor
from app.runtime_common import COCO17_KEYPOINT_NAMES, COCO17_SKELETON, normalize_scores, rows_with_last_dim
from app.runtime_execution import run_model_bundle


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


__all__ = [
    "decode_rtmpose_outputs",
    "infer_pose_record_for_image",
    "run_rtmpose",
    "scale_pose_point",
    "softmax_max",
]
