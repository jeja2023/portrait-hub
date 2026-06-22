from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image

from app.media.quality import assess_image_quality, clamp01
from app.portrait_compare import l2_normalize_vector

Array = npt.NDArray[Any]

FALLBACK_EMBEDDING_MODEL_ID = "portrait_hub/image_fingerprint_v1"
FALLBACK_EMBEDDING_VERSION = "1.0.0"
FACE_DETECT_MAX_SIDE = 640
FACE_DETECT_MIN_TEXTURE = 6.0
FACE_CASCADE: cv2.CascadeClassifier | None = None


def image_fingerprint_embedding(image: Image.Image) -> list[float]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    hist_parts = []
    for channel in range(3):
        hist, _ = np.histogram(rgb[:, :, channel], bins=16, range=(0.0, 1.0))
        hist_parts.append(hist.astype(np.float32))
    gray = cv2.cvtColor((rgb * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    coarse = cv2.resize(gray, (4, 4), interpolation=cv2.INTER_AREA).reshape(-1)
    vector = np.concatenate([*hist_parts, coarse.astype(np.float32)], axis=0)
    vector = l2_normalize_vector(vector)
    return [round(float(value), 8) for value in vector.tolist()]


def best_quality_index(items: list[dict[str, Any]]) -> int:
    if not items:
        return -1
    def quality_score(index: int) -> float:
        quality = items[index].get("quality")
        return float(quality.get("score", 0.0)) if isinstance(quality, dict) else 0.0

    return max(range(len(items)), key=quality_score)


def face_cascade() -> cv2.CascadeClassifier | None:
    global FACE_CASCADE
    if FACE_CASCADE is None:
        cv2_data = getattr(cv2, "data", None)
        cascade_path = getattr(cv2_data, "haarcascades", "") + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        FACE_CASCADE = None if cascade.empty() else cascade
    return FACE_CASCADE


def face_detection_image(image: Image.Image) -> tuple[Array, float]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    max_side = max(image.width, image.height)
    if max_side > FACE_DETECT_MAX_SIDE:
        scale = FACE_DETECT_MAX_SIDE / float(max_side)
        resized = cv2.resize(
            rgb,
            (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
        return resized, 1.0 / scale
    return rgb, 1.0


def detect_face_candidates(image: Image.Image, max_faces: int = 8) -> list[dict[str, Any]]:
    cascade = face_cascade()
    if cascade is None:
        return []

    rgb, inverse_scale = face_detection_image(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if float(gray.std()) < FACE_DETECT_MIN_TEXTURE:
        return []

    detections = cascade.detectMultiScale(
        gray,
        scaleFactor=1.2,
        minNeighbors=5,
        minSize=(24, 24),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )
    faces: list[dict[str, Any]] = []
    for index, (x, y, w, h) in enumerate(detections[:max_faces]):
        x1 = float(x) * inverse_scale
        y1 = float(y) * inverse_scale
        x2 = float(x + w) * inverse_scale
        y2 = float(y + h) * inverse_scale
        crop = image.crop((int(x1), int(y1), int(x2), int(y2)))
        quality = assess_image_quality(crop)
        face_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        face_score = clamp01(0.55 + min(face_area / max(1, image.width * image.height), 1.0) * 0.45)
        landmarks = [
            [round(float(x1 + (x2 - x1) * 0.32), 2), round(float(y1 + (y2 - y1) * 0.38), 2)],
            [round(float(x1 + (x2 - x1) * 0.68), 2), round(float(y1 + (y2 - y1) * 0.38), 2)],
            [round(float(x1 + (x2 - x1) * 0.50), 2), round(float(y1 + (y2 - y1) * 0.55), 2)],
            [round(float(x1 + (x2 - x1) * 0.37), 2), round(float(y1 + (y2 - y1) * 0.75), 2)],
            [round(float(x1 + (x2 - x1) * 0.63), 2), round(float(y1 + (y2 - y1) * 0.75), 2)],
        ]
        faces.append(
            {
                "face_index": index,
                "box": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                "score": round(face_score, 6),
                "landmarks": landmarks,
                "quality": quality,
                "embedding_dim": 64,
                "detection_strategy": "opencv_haar_bounded",
                "crop": crop,
            }
        )
    return faces


def fallback_face_candidate(image: Image.Image) -> dict[str, Any]:
    width, height = image.size
    side_w = width * 0.58
    side_h = height * 0.58
    x1 = max(0.0, (width - side_w) / 2.0)
    y1 = max(0.0, (height - side_h) / 2.0)
    x2 = min(float(width), x1 + side_w)
    y2 = min(float(height), y1 + side_h)
    crop = image.crop((int(x1), int(y1), int(x2), int(y2)))
    return {
        "face_index": 0,
        "box": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
        "score": 0.25,
        "landmarks": [
            [round(width * 0.40, 2), round(height * 0.42, 2)],
            [round(width * 0.60, 2), round(height * 0.42, 2)],
            [round(width * 0.50, 2), round(height * 0.52, 2)],
            [round(width * 0.43, 2), round(height * 0.62, 2)],
            [round(width * 0.57, 2), round(height * 0.62, 2)],
        ],
        "quality": assess_image_quality(crop),
        "embedding_dim": 64,
        "detection_strategy": "whole_image_fallback",
        "crop": crop,
    }


def face_records(image: Image.Image, include_embeddings: bool = False, fallback: bool = False) -> list[dict[str, Any]]:
    faces = detect_face_candidates(image)
    if not faces and fallback:
        faces = [fallback_face_candidate(image)]

    records: list[dict[str, Any]] = []
    for face in faces:
        crop = face.pop("crop")
        if include_embeddings:
            face["embedding"] = image_fingerprint_embedding(crop)
        records.append(face)
    return records


def best_face_embedding(image: Image.Image) -> tuple[list[float], dict[str, Any]]:
    faces = face_records(image, include_embeddings=True, fallback=True)
    index = best_quality_index(faces)
    selected = faces[index]
    return selected["embedding"], selected


def body_record(image: Image.Image, include_embedding: bool = True) -> dict[str, Any]:
    quality = assess_image_quality(image)
    width, height = image.size
    record: dict[str, Any] = {
        "box": [0.0, 0.0, float(width), float(height)],
        "score": 0.35,
        "quality": quality,
        "embedding_dim": 64,
        "selection_strategy": "whole_image_fallback",
    }
    if include_embedding:
        record["embedding"] = image_fingerprint_embedding(image)
    return record


def dominant_color(image: Image.Image) -> dict[str, Any]:
    rgb = np.asarray(image.convert("RGB").resize((64, 64)), dtype=np.uint8)
    pixels = rgb.reshape(-1, 3)
    mean = pixels.mean(axis=0)
    color_names = {
        "black": np.array([20, 20, 20]),
        "white": np.array([235, 235, 235]),
        "gray": np.array([128, 128, 128]),
        "red": np.array([200, 40, 40]),
        "green": np.array([40, 160, 80]),
        "blue": np.array([40, 90, 200]),
        "yellow": np.array([220, 200, 40]),
        "purple": np.array([140, 70, 170]),
        "brown": np.array([130, 80, 45]),
    }
    name = min(color_names, key=lambda key: float(np.linalg.norm(mean - color_names[key])))
    return {
        "name": name,
        "rgb": [int(round(value)) for value in mean.tolist()],
    }


def appearance_record(image: Image.Image, include_embedding: bool = True) -> dict[str, Any]:
    quality = assess_image_quality(image)
    # 整图主色既用作顶层 dominant_color，也用作上半身颜色；只计算一次，避免重复跑直方图。
    full_image_color = dominant_color(image)
    record: dict[str, Any] = {
        "quality": quality,
        "dominant_color": full_image_color,
        "attributes": {
            "upper_color": full_image_color,
            "lower_color": dominant_color(image.crop((0, image.height // 2, image.width, image.height))),
        },
        "embedding_dim": 64,
        "model_status": "color_histogram_fallback",
    }
    if include_embedding:
        record["embedding"] = image_fingerprint_embedding(image)
    return record


def pose_record(image: Image.Image) -> dict[str, Any]:
    width, height = image.size
    keypoints: list[dict[str, Any]] = [
        {"name": "nose", "point": [width * 0.50, height * 0.18], "score": 0.15},
        {"name": "left_shoulder", "point": [width * 0.36, height * 0.34], "score": 0.15},
        {"name": "right_shoulder", "point": [width * 0.64, height * 0.34], "score": 0.15},
        {"name": "left_hip", "point": [width * 0.42, height * 0.58], "score": 0.15},
        {"name": "right_hip", "point": [width * 0.58, height * 0.58], "score": 0.15},
        {"name": "left_knee", "point": [width * 0.42, height * 0.76], "score": 0.12},
        {"name": "right_knee", "point": [width * 0.58, height * 0.76], "score": 0.12},
    ]
    return {
        "quality": assess_image_quality(image),
        "keypoints": [
            {
                "name": item["name"],
                "point": [round(float(item["point"][0]), 2), round(float(item["point"][1]), 2)],
                "score": item["score"],
            }
            for item in keypoints
        ],
        "skeleton": [
            ["left_shoulder", "right_shoulder"],
            ["left_shoulder", "left_hip"],
            ["right_shoulder", "right_hip"],
            ["left_hip", "right_hip"],
            ["left_hip", "left_knee"],
            ["right_hip", "right_knee"],
        ],
        "model_status": "geometric_placeholder",
    }


def gait_embedding(images: list[Image.Image]) -> tuple[list[float] | None, dict[str, Any]]:
    if len(images) < 2:
        return None, {
            "quality": None,
            "reason": "not_enough_frames",
            "tracklet_frames": len(images),
        }
    vectors = np.asarray([image_fingerprint_embedding(image) for image in images], dtype=np.float32)
    averaged = l2_normalize_vector(vectors.mean(axis=0))
    qualities = [assess_image_quality(image)["score"] for image in images]
    return [round(float(value), 8) for value in averaged.tolist()], {
        "quality": round(float(np.mean(qualities)), 6),
        "tracklet_frames": len(images),
        "embedding_dim": int(averaged.shape[0]),
        "model_status": "tracklet_fingerprint_fallback",
    }
