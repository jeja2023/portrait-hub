from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from app.portrait_compare import l2_normalize_vector
from app.portrait_model_runtime_preprocess import batch_slice

Array = npt.NDArray[Any]


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


def rows_with_last_dim(array: Array) -> Array:
    if array.ndim == 0:
        return array.reshape(1, 1)
    if array.ndim == 1:
        return array.reshape(1, -1)
    return array.reshape(-1, array.shape[-1])


def normalize_scores(scores: Array) -> Array:
    scores = np.asarray(scores, dtype=np.float32).reshape(-1)
    if scores.size and (float(scores.min()) < 0.0 or float(scores.max()) > 1.0):
        scores = 1.0 / (1.0 + np.exp(-scores))
    return scores


def embedding_rows(raw_outputs: list[Array], batch_size: int) -> Array:
    for output in raw_outputs:
        array = np.asarray(output, dtype=np.float32)
        if array.ndim == 1:
            return array.reshape(1, -1)
        if array.ndim >= 2 and array.shape[0] == batch_size:
            return array.reshape(batch_size, -1)
        if batch_size == 1:
            return array.reshape(1, -1)
    return np.empty((0, 0), dtype=np.float32)


def round_normalized_embedding(vector: Array | list[float]) -> list[float]:
    normalized = l2_normalize_vector(vector)
    return [round(float(value), 8) for value in normalized.tolist()]


def batch_rows_with_last_dim(array: Array, batch_index: int, batch_size: int) -> Array:
    return rows_with_last_dim(batch_slice(array, batch_index, batch_size))


__all__ = [
    "COCO17_KEYPOINT_NAMES",
    "COCO17_SKELETON",
    "batch_rows_with_last_dim",
    "embedding_rows",
    "normalize_scores",
    "round_normalized_embedding",
    "rows_with_last_dim",
]
