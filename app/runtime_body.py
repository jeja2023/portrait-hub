from __future__ import annotations

from typing import Any

from PIL import Image

from app.geometry import crop_person, person_crop_quality
from app.inference_detection import infer_person_frames
from app.inference_reid import infer_reid_images
from app.observability import logger, wall_time
from app.portrait_embeddings import FALLBACK_EMBEDDING_MODEL_ID, FALLBACK_EMBEDDING_VERSION
from app.portrait_embeddings import body_record as fallback_body_record
from app.portrait_model_runtime_capability import get_capability_runtime, runtime_output_value
from app.portrait_response import exception_log_summary
from app.settings import RUNTIME_CAPABILITY_RETRY_COOLDOWN_SECONDS

# 这些变量持有的是 "retry-after"（稍后重试）的时间戳，而不是一个永久的标志：
# 未来某时刻的值意味着运行时在发生故障后处于冷却状态，而 0.0 或 False 表示
# 它目前可用。这允许某项能力从瞬时的冷启动故障中恢复，
# 而不用一直处于禁用状态直至进程重启。
_BODY_EMBEDDING_RUNTIME_UNAVAILABLE: float = 0.0
_PERSON_DETECTION_RUNTIME_UNAVAILABLE: float = 0.0


def _runtime_cooldown_active(retry_after: float) -> bool:
    return (retry_after or 0.0) > wall_time()


def _runtime_cooldown_deadline() -> float:
    return wall_time() + RUNTIME_CAPABILITY_RETRY_COOLDOWN_SECONDS


def best_person_detection(image: Image.Image, persons: list[dict[str, Any]]) -> tuple[Image.Image, dict[str, Any]] | None:
    candidates: list[tuple[float, Image.Image, dict[str, Any], dict[str, Any]]] = []
    for person in persons:
        box = person.get("box")
        if not isinstance(box, list) or len(box) < 4:
            continue
        crop = crop_person(image, [float(value) for value in box[:4]], min_size=2)
        if crop is None:
            continue
        quality = person_crop_quality(image, [float(value) for value in box[:4]])
        try:
            detection_score = float(person.get("score", 0.0))
            quality_score = float(quality.get("score", 0.0))
        except (TypeError, ValueError):
            detection_score = 0.0
            quality_score = 0.0
        candidates.append((detection_score * 0.58 + quality_score * 0.42, crop, person, quality))
    if not candidates:
        return None

    _, crop, person, quality = max(candidates, key=lambda item: item[0])
    return crop, {
        "box": [round(float(value), 2) for value in person.get("box", [])[:4]],
        "score": round(float(person.get("score", 0.0)), 6),
        "quality": quality,
    }


async def detect_body_embedding_crop(image: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    global _PERSON_DETECTION_RUNTIME_UNAVAILABLE
    if _runtime_cooldown_active(_PERSON_DETECTION_RUNTIME_UNAVAILABLE):
        return image, {"selection_strategy": "whole_image_reid"}
    try:
        runtime = await get_capability_runtime("person_detection", {"yolo", "yolov8"})
    except Exception as exc:
        _PERSON_DETECTION_RUNTIME_UNAVAILABLE = _runtime_cooldown_deadline()
        logger.warning("person detector unavailable for body embedding crop: %s", exception_log_summary(exc))
        return image, {"selection_strategy": "whole_image_reid"}
    if runtime is None:
        return image, {"selection_strategy": "whole_image_reid"}

    try:
        confidence = float(runtime_output_value(runtime, "confidence", runtime.capability.get("confidence", 0.25)))
        iou = float(runtime_output_value(runtime, "iou", runtime.capability.get("iou", 0.45)))
        max_detections = int(runtime_output_value(runtime, "max_detections", runtime.capability.get("max_detections", 8)))
        frames, meta = await infer_person_frames(
            runtime.bundle,
            runtime.cache_key,
            [image],
            [None],
            confidence=confidence,
            iou=iou,
            max_detections=max_detections,
        )
    except Exception as exc:
        _PERSON_DETECTION_RUNTIME_UNAVAILABLE = _runtime_cooldown_deadline()
        logger.warning("person detector inference failed for body embedding crop: %s", exception_log_summary(exc))
        return image, {"selection_strategy": "whole_image_reid"}

    frame = frames[0] if frames else {}
    selected = best_person_detection(image, frame.get("persons", []) if isinstance(frame.get("persons"), list) else [])
    if selected is None:
        return image, {
            "selection_strategy": "whole_image_reid",
            "detection_model_id": runtime.model_id,
            "detection_model_version": runtime.version,
            "detection_model_status": "yolo_onnx",
            "detection_count": int(frame.get("person_count", 0) or 0),
        }

    crop, detection = selected
    return crop, {
        "selection_strategy": "person_detection_crop_reid",
        "box": detection["box"],
        "score": detection["score"],
        "detection_quality": detection["quality"],
        "detection_model_id": runtime.model_id,
        "detection_model_version": runtime.version,
        "detection_model_status": "yolo_onnx",
        "detection_count": int(frame.get("person_count", 0) or 0),
        "detection_inference_mode": meta.get("inference_mode"),
    }


async def run_reid_body_embedding(image: Image.Image) -> tuple[list[float], dict[str, Any]] | None:
    global _BODY_EMBEDDING_RUNTIME_UNAVAILABLE
    if _runtime_cooldown_active(_BODY_EMBEDDING_RUNTIME_UNAVAILABLE):
        return None
    try:
        runtime = await get_capability_runtime("body_embedding", {"reid"})
    except Exception as exc:
        _BODY_EMBEDDING_RUNTIME_UNAVAILABLE = _runtime_cooldown_deadline()
        logger.warning("人体特征模型不可用，回退到图像指纹: %s", exception_log_summary(exc))
        return None
    if runtime is None:
        return None

    try:
        crop, selection_meta = await detect_body_embedding_crop(image)
        embeddings, meta = await infer_reid_images(runtime.bundle, runtime.cache_key, [crop])
    except Exception as exc:
        _BODY_EMBEDDING_RUNTIME_UNAVAILABLE = _runtime_cooldown_deadline()
        logger.warning("人体特征推理失败，回退到图像指纹: %s", exception_log_summary(exc))
        return None
    if embeddings.ndim != 2 or embeddings.shape[0] < 1 or embeddings.shape[1] < 1:
        logger.warning("人体特征推理返回空输出，回退到图像指纹")
        return None

    embedding = [round(float(value), 8) for value in embeddings[0].tolist()]
    return embedding, {
        **meta,
        **selection_meta,
        "embedding_dim": len(embedding),
        "model_id": runtime.model_id,
        "model_version": runtime.version,
        "model_status": "reid_onnx",
        "adapter": runtime.adapter,
    }


def fallback_body_embedding_record(image: Image.Image, *, include_embedding: bool) -> dict[str, Any]:
    record = fallback_body_record(image, include_embedding=include_embedding)
    record["model_status"] = "whole_image_fallback"
    if include_embedding:
        record["embedding_model_id"] = FALLBACK_EMBEDDING_MODEL_ID
        record["embedding_model_version"] = FALLBACK_EMBEDDING_VERSION
        record["embedding_model_status"] = "image_fingerprint_fallback"
        record["embedding_adapter"] = "image_fingerprint"
    return record


async def infer_body_record_for_image(image: Image.Image, *, include_embedding: bool = True) -> dict[str, Any]:
    if not include_embedding:
        return fallback_body_embedding_record(image, include_embedding=False)

    result = await run_reid_body_embedding(image)
    if result is None:
        return fallback_body_embedding_record(image, include_embedding=True)

    embedding, meta = result
    record = fallback_body_record(image, include_embedding=False)
    record.update(
        {
            "box": meta.get("box", record.get("box")),
            "score": meta.get("score", record.get("score")),
            "embedding": embedding,
            "embedding_dim": len(embedding),
            "embedding_model_id": meta.get("model_id", FALLBACK_EMBEDDING_MODEL_ID),
            "embedding_model_version": meta.get("model_version", FALLBACK_EMBEDDING_VERSION),
            "embedding_model_status": meta.get("model_status", "reid_onnx"),
            "embedding_adapter": meta.get("adapter", "reid"),
            "embedding_batch_mode": meta.get("inference_mode"),
            "model_status": meta.get("model_status", "reid_onnx"),
            "selection_strategy": meta.get("selection_strategy", "whole_image_reid"),
        }
    )
    for key in [
        "detection_model_id",
        "detection_model_version",
        "detection_model_status",
        "detection_count",
        "detection_inference_mode",
        "detection_quality",
    ]:
        if key in meta:
            record[key] = meta[key]
    return record


__all__ = [
    "best_person_detection",
    "detect_body_embedding_crop",
    "fallback_body_embedding_record",
    "get_capability_runtime",
    "infer_body_record_for_image",
    "infer_person_frames",
    "infer_reid_images",
    "run_reid_body_embedding",
]
