from __future__ import annotations

import hashlib

import cv2
import numpy as np
from PIL import Image


def content_sha256(data: bytes | None) -> str | None:
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


def average_hash(image: Image.Image, hash_size: int = 8) -> str:
    gray = np.asarray(image.convert("L").resize((hash_size, hash_size)), dtype=np.float32)
    threshold = float(gray.mean()) if gray.size else 0.0
    bits = (gray >= threshold).astype(np.uint8).reshape(-1)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:0{hash_size * hash_size // 4}x}"


def difference_hash(image: Image.Image, hash_size: int = 8) -> str:
    gray = np.asarray(image.convert("L").resize((hash_size + 1, hash_size)), dtype=np.float32)
    bits = (gray[:, 1:] >= gray[:, :-1]).astype(np.uint8).reshape(-1)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:0{hash_size * hash_size // 4}x}"


def perceptual_hash_payload(image: Image.Image, data: bytes | None = None) -> dict[str, str | None]:
    return {
        "sha256": content_sha256(data),
        "average_hash": average_hash(image),
        "difference_hash": difference_hash(image),
    }


def hamming_hex(left: str | None, right: str | None) -> int | None:
    if not left or not right or len(left) != len(right):
        return None
    return (int(left, 16) ^ int(right, 16)).bit_count()


def image_difference_score(left: Image.Image, right: Image.Image) -> float:
    left_gray = cv2.resize(np.asarray(left.convert("L"), dtype=np.float32), (64, 64))
    right_gray = cv2.resize(np.asarray(right.convert("L"), dtype=np.float32), (64, 64))
    return round(float(np.mean(np.abs(left_gray - right_gray))) / 255.0, 6)
