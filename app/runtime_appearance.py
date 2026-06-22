from __future__ import annotations

from typing import Any

from PIL import Image

from app.media.quality import assess_image_quality
from app.portrait_embeddings import appearance_record as fallback_appearance_record
from app.portrait_model_runtime_capability import get_capability_runtime, runtime_input_size, runtime_input_value
from app.portrait_model_runtime_preprocess import resize_tensor
from app.runtime_common import embedding_rows, round_normalized_embedding
from app.runtime_execution import run_model_bundle


async def run_attribute_reid_appearance(image: Image.Image) -> tuple[list[float], dict[str, Any]] | None:
    runtime = await get_capability_runtime("appearance", {"attribute_reid"})
    if runtime is None:
        return None
    input_height, input_width = runtime_input_size(runtime, (256, 128))
    normalize = str(runtime_input_value(runtime, "normalize", "imagenet"))
    color = str(runtime_input_value(runtime, "color", "rgb"))
    tensor = resize_tensor(image, input_height, input_width, normalize=normalize, color=color)
    raw_outputs, _, _ = await run_model_bundle(runtime.bundle, tensor)
    rows = embedding_rows(raw_outputs, 1)
    if rows.size == 0:
        return None
    embedding = round_normalized_embedding(rows[0])
    return embedding, {
        "quality": assess_image_quality(image),
        "embedding_dim": len(embedding),
        "model_id": runtime.model_id,
        "model_version": runtime.version,
        "model_status": "attribute_reid_onnx",
        "adapter": runtime.adapter,
    }


async def infer_appearance_record_for_image(image: Image.Image, *, include_embedding: bool = True) -> dict[str, Any]:
    result = await run_attribute_reid_appearance(image)
    if result is None:
        return fallback_appearance_record(image, include_embedding=include_embedding)
    embedding, meta = result
    fallback = fallback_appearance_record(image, include_embedding=False)
    fallback.update(meta)
    fallback["embedding_dim"] = len(embedding)
    if include_embedding:
        fallback["embedding"] = embedding
    return fallback


__all__ = ["get_capability_runtime", "infer_appearance_record_for_image", "run_attribute_reid_appearance", "run_model_bundle"]
