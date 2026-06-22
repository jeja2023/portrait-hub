from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
from PIL import Image

from app.schemas import LetterboxMeta

Array = npt.NDArray[Any]


def preprocess_rgb_array(image: Image.Image, *, normalize: str = "none", color: str = "rgb") -> Array:
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
    return np.transpose(array, (2, 0, 1))


def resize_tensor(
    image: Image.Image,
    input_height: int,
    input_width: int,
    *,
    normalize: str = "none",
    color: str = "rgb",
) -> Array:
    resized = image.resize((input_width, input_height), Image.Resampling.BILINEAR)
    return preprocess_rgb_array(resized, normalize=normalize, color=color)[None, :, :, :]


def letterbox_tensor(
    image: Image.Image,
    input_height: int,
    input_width: int,
    *,
    normalize: str = "none",
    color: str = "rgb",
) -> tuple[Array, LetterboxMeta]:
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


def batch_slice(output: Any, batch_index: int, batch_size: int) -> Array:
    array = np.asarray(output)
    if array.ndim >= 3 and array.shape[0] == batch_size:
        return np.asarray(array[batch_index])
    return array
