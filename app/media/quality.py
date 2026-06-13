from typing import Any

import cv2
import numpy as np
from PIL import Image


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def round_quality(payload: dict[str, Any]) -> dict[str, Any]:
    rounded: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, float):
            rounded[key] = round(value, 6)
        else:
            rounded[key] = value
    return rounded


def assess_image_quality(image: Image.Image) -> dict[str, Any]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    width, height = image.size
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var()) if gray.size else 0.0
    sharpness = clamp01(blur_variance / 500.0)
    brightness = clamp01(float(gray.mean()) / 255.0) if gray.size else 0.0
    contrast = clamp01(float(gray.std()) / 80.0) if gray.size else 0.0
    dark_ratio = float((gray < 16).mean()) if gray.size else 1.0
    bright_ratio = float((gray > 240).mean()) if gray.size else 1.0
    underexposure = clamp01(dark_ratio / 0.35)
    overexposure = clamp01(bright_ratio / 0.35)
    exposure = clamp01(1.0 - abs(brightness - 0.5) * 2.0)
    size_score = clamp01((width * height) / float(256 * 256))
    aspect_ratio = width / max(1, height)
    aspect_score = clamp01(1.0 - max(0.0, abs(aspect_ratio - 0.75) - 0.75) / 2.5)
    noise_map = cv2.Laplacian(gray, cv2.CV_64F)
    noise = clamp01(float(np.median(np.abs(noise_map))) / 30.0) if gray.size else 1.0
    rg = np.abs(rgb[:, :, 0].astype(np.float32) - rgb[:, :, 1].astype(np.float32))
    yb = np.abs(0.5 * (rgb[:, :, 0].astype(np.float32) + rgb[:, :, 1].astype(np.float32)) - rgb[:, :, 2].astype(np.float32))
    colorfulness = clamp01((float(rg.std()) + float(yb.std())) / 120.0) if rgb.size else 0.0
    exposure_clipping = clamp01((underexposure + overexposure) / 2.0)
    score = clamp01(
        sharpness * 0.28
        + exposure * 0.20
        + contrast * 0.17
        + size_score * 0.15
        + aspect_score * 0.08
        + colorfulness * 0.07
        + (1.0 - exposure_clipping) * 0.05
    )

    return round_quality(
        {
            "score": score,
            "sharpness": sharpness,
            "blur": clamp01(1.0 - sharpness),
            "brightness": brightness,
            "contrast": contrast,
            "exposure": exposure,
            "underexposure": underexposure,
            "overexposure": overexposure,
            "exposure_clipping": exposure_clipping,
            "noise": noise,
            "colorfulness": colorfulness,
            "aspect_ratio": aspect_ratio,
            "aspect_score": aspect_score,
            "size_score": size_score,
            "width": width,
            "height": height,
            "pixel_count": width * height,
        }
    )
