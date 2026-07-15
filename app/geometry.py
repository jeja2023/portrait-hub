from typing import Any

import numpy as np
import numpy.typing as npt
from PIL import Image

from app.media.quality import assess_image_quality, clamp01
from app.schemas import LetterboxMeta

Array = npt.NDArray[Any]


def xywh_to_xyxy(boxes: Array) -> Array:
    result = np.empty_like(boxes, dtype=np.float32)
    result[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    result[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    result[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    result[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return result


def restore_boxes(boxes: Array, meta: LetterboxMeta) -> Array:
    restored = boxes.copy()
    restored[:, [0, 2]] = (restored[:, [0, 2]] - meta["pad_left"]) / meta["scale"]
    restored[:, [1, 3]] = (restored[:, [1, 3]] - meta["pad_top"]) / meta["scale"]
    restored[:, [0, 2]] = np.clip(restored[:, [0, 2]], 0, meta["original_width"])
    restored[:, [1, 3]] = np.clip(restored[:, [1, 3]], 0, meta["original_height"])
    return restored


def nms(boxes: Array, scores: Array, iou_threshold: float) -> list[int]:
    if boxes.size == 0:
        return []

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []

    while order.size > 0:
        current = int(order[0])
        keep.append(current)
        if order.size == 1:
            break

        rest = order[1:]
        xx1 = np.maximum(x1[current], x1[rest])
        yy1 = np.maximum(y1[current], y1[rest])
        xx2 = np.minimum(x2[current], x2[rest])
        yy2 = np.minimum(y2[current], y2[rest])

        inter_width = np.maximum(0, xx2 - xx1)
        inter_height = np.maximum(0, yy2 - yy1)
        intersection = inter_width * inter_height
        union = areas[current] + areas[rest] - intersection
        iou = intersection / np.maximum(union, 1e-7)
        order = rest[iou <= iou_threshold]

    return keep
def crop_person(image: Image.Image, box: list[float], min_size: int = 2) -> Image.Image | None:
    width, height = image.size
    x1, y1, x2, y2 = box
    left = max(0, min(width, int(round(x1))))
    top = max(0, min(height, int(round(y1))))
    right = max(0, min(width, int(round(x2))))
    bottom = max(0, min(height, int(round(y2))))
    if right - left < min_size or bottom - top < min_size:
        return None
    return image.crop((left, top, right, bottom))


def person_crop_quality(image: Image.Image, box: list[float], min_size: int = 2) -> dict[str, Any]:
    width, height = image.size
    if len(box) < 4 or width <= 0 or height <= 0:
        return {
            "usable": False,
            "score": 0.0,
            "reason": "invalid_box",
        }
    x1, y1, x2, y2 = [float(value) for value in box[:4]]
    clipped = [
        max(0.0, min(float(width), x1)),
        max(0.0, min(float(height), y1)),
        max(0.0, min(float(width), x2)),
        max(0.0, min(float(height), y2)),
    ]
    box_width = max(0.0, clipped[2] - clipped[0])
    box_height = max(0.0, clipped[3] - clipped[1])
    if box_width < min_size or box_height < min_size:
        return {
            "usable": False,
            "score": 0.0,
            "reason": "crop_too_small",
            "clipped_box": [round(value, 3) for value in clipped],
        }
    crop_box: tuple[int, int, int, int] = (
        int(round(clipped[0])),
        int(round(clipped[1])),
        int(round(clipped[2])),
        int(round(clipped[3])),
    )
    crop = image.crop(crop_box)
    quality = assess_image_quality(crop)
    area_ratio = clamp01((box_width * box_height) / max(1.0, float(width * height)))
    aspect_ratio = box_width / max(1.0, box_height)
    person_aspect_score = clamp01(1.0 - abs(aspect_ratio - 0.45) / 1.10)
    clipped_area = max(0.0, (x2 - x1) * (y2 - y1))
    visible_area = box_width * box_height
    truncation = clamp01(1.0 - visible_area / clipped_area) if clipped_area > 0 else 1.0
    area_score = clamp01(area_ratio / 0.18)
    score = clamp01(
        float(quality.get("score", 0.0)) * 0.52
        + area_score * 0.18
        + person_aspect_score * 0.18
        + (1.0 - truncation) * 0.12
    )
    usable = score >= 0.15 and truncation <= 0.65
    return {
        "usable": usable,
        "score": round(score, 6),
        "crop_quality_score": quality.get("score", 0.0),
        "area_ratio": round(area_ratio, 6),
        "area_score": round(area_score, 6),
        "aspect_ratio": round(aspect_ratio, 6),
        "person_aspect_score": round(person_aspect_score, 6),
        "truncation": round(truncation, 6),
        "clipped_box": [round(value, 3) for value in clipped],
        "crop_width": int(crop.width),
        "crop_height": int(crop.height),
        "reason": "ok" if usable else "low_crop_quality",
        "quality": quality,
    }


__all__ = [
    "crop_person",
    "nms",
    "person_crop_quality",
    "restore_boxes",
    "xywh_to_xyxy",
]
