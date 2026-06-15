from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from app.media.quality import assess_image_quality
from app.portrait_embeddings import gait_embedding as fallback_gait_embedding
from app.portrait_model_runtime_capability import get_capability_runtime, runtime_input_size, runtime_input_value
from app.runtime_common import embedding_rows, round_normalized_embedding
from app.runtime_execution import run_model_bundle


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


__all__ = ["gait_sequence_tensor", "infer_gait_embedding_for_images", "run_opengait"]
