from __future__ import annotations

import asyncio
from typing import Any

from PIL import Image

import app.runtime_appearance as runtime_appearance
import app.runtime_body as runtime_body
import app.runtime_face as runtime_face
import app.runtime_gait as runtime_gait
import app.runtime_pose as runtime_pose
from app.inference_detection import infer_person_frames
from app.inference_reid import infer_reid_images
from app.portrait_embeddings import FALLBACK_EMBEDDING_MODEL_ID, FALLBACK_EMBEDDING_VERSION
from app.portrait_model_runtime_capability import (
    CapabilityRuntime,
    get_capability_runtime,
    runtime_input_size,
    runtime_input_value,
    runtime_output_value,
)
from app.portrait_model_runtime_preprocess import batch_slice, letterbox_tensor, preprocess_rgb_array, resize_tensor
from app.runtime_common import (
    COCO17_KEYPOINT_NAMES,
    COCO17_SKELETON,
    embedding_rows,
    normalize_scores,
    round_normalized_embedding,
    rows_with_last_dim,
)
from app.runtime_execution import run_model_bundle, run_model_bundle_batch
from app.runtime_face import (
    ARCFACE_CANONICAL_112,
    FACE_DETECTION_FALLBACK_MODEL_ID,
    FACE_KEYPOINT_NAMES,
    arcface_aligned_crop,
    decode_scrfd_anchor_outputs,
    face_crop_from_box,
    match_rows_by_length,
    parse_scrfd_outputs,
    restore_landmarks,
    scrfd_anchor_centers,
    strip_face_crop,
)
from app.runtime_gait import gait_sequence_tensor
from app.runtime_pose import decode_rtmpose_outputs, scale_pose_point, softmax_max


# app.runtime_body 中 retry-after 时间戳的镜像（0.0 = 可用，
# 未来的时间戳 = 冷却中）。此模块是跨调用之间的真理源；
# 这些值会在每次调用前推入 runtime_body，并在调用后读回。
_BODY_EMBEDDING_RUNTIME_UNAVAILABLE: float = 0.0
_PERSON_DETECTION_RUNTIME_UNAVAILABLE: float = 0.0
_RUNTIME_DEPENDENCY_LOCKS: dict[int, asyncio.Lock] = {}


def _runtime_dependency_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _RUNTIME_DEPENDENCY_LOCKS.get(id(loop))
    if lock is None:
        lock = asyncio.Lock()
        _RUNTIME_DEPENDENCY_LOCKS[id(loop)] = lock
    return lock


def _sync_runtime_module_dependencies() -> None:
    runtime_face.get_capability_runtime = get_capability_runtime
    runtime_face.run_model_bundle_batch = run_model_bundle_batch
    runtime_body.get_capability_runtime = get_capability_runtime
    runtime_body.infer_person_frames = infer_person_frames
    runtime_body.infer_reid_images = infer_reid_images
    runtime_body._BODY_EMBEDDING_RUNTIME_UNAVAILABLE = _BODY_EMBEDDING_RUNTIME_UNAVAILABLE
    runtime_body._PERSON_DETECTION_RUNTIME_UNAVAILABLE = _PERSON_DETECTION_RUNTIME_UNAVAILABLE
    runtime_pose.get_capability_runtime = get_capability_runtime
    runtime_pose.run_model_bundle = run_model_bundle
    runtime_gait.get_capability_runtime = get_capability_runtime
    runtime_gait.run_model_bundle = run_model_bundle
    runtime_appearance.get_capability_runtime = get_capability_runtime
    runtime_appearance.run_model_bundle = run_model_bundle


def _sync_body_flags_from_module() -> None:
    global _BODY_EMBEDDING_RUNTIME_UNAVAILABLE, _PERSON_DETECTION_RUNTIME_UNAVAILABLE
    _BODY_EMBEDDING_RUNTIME_UNAVAILABLE = runtime_body._BODY_EMBEDDING_RUNTIME_UNAVAILABLE
    _PERSON_DETECTION_RUNTIME_UNAVAILABLE = runtime_body._PERSON_DETECTION_RUNTIME_UNAVAILABLE


async def _prepare_runtime_dependencies() -> None:
    # 只有依赖注入需要互斥，它只是若干属性赋值。这里刻意不在后续 await 的推理期间持有
    # 该锁，以便并发请求（以及 per-model/per-GPU 信号量）能真正重叠，而不是被一把全局锁串行化。
    async with _runtime_dependency_lock():
        _sync_runtime_module_dependencies()


async def _commit_body_runtime_flags() -> None:
    # 把 body 能力的冷却时间戳回拉到本模块的镜像。这些只是尽力而为的 retry-after 标记，
    # 因此释放依赖锁与此处回读之间的短暂窗口是可接受的。
    async with _runtime_dependency_lock():
        _sync_body_flags_from_module()


def infer_scrfd_stride(count: int, input_height: int, input_width: int) -> tuple[int, int] | None:
    return runtime_face.infer_scrfd_stride(count, input_height, input_width)


async def run_scrfd_face_detection(image: Image.Image) -> list[dict[str, Any]] | None:
    await _prepare_runtime_dependencies()
    return await runtime_face.run_scrfd_face_detection(image)


async def apply_arcface_embeddings(image: Image.Image, faces: list[dict[str, Any]]) -> bool:
    await _prepare_runtime_dependencies()
    return await runtime_face.apply_arcface_embeddings(image, faces)


def apply_fallback_face_embeddings(faces: list[dict[str, Any]]) -> None:
    runtime_face.apply_fallback_face_embeddings(faces)


def fallback_face_records_with_crops(image: Image.Image, *, fallback: bool) -> list[dict[str, Any]]:
    return runtime_face.fallback_face_records_with_crops(image, fallback=fallback)


async def infer_face_records_for_image(
    image: Image.Image,
    *,
    include_embeddings: bool = False,
    fallback: bool = False,
) -> list[dict[str, Any]]:
    await _prepare_runtime_dependencies()
    return await runtime_face.infer_face_records_for_image(image, include_embeddings=include_embeddings, fallback=fallback)


async def infer_best_face_embedding_for_image(image: Image.Image) -> tuple[list[float], dict[str, Any]]:
    await _prepare_runtime_dependencies()
    return await runtime_face.infer_best_face_embedding_for_image(image)


def embedding_model_info(subject: dict[str, Any]) -> tuple[str, str]:
    return runtime_face.embedding_model_info(subject)


def face_model_summary(faces: list[dict[str, Any]], *, include_embeddings: bool) -> dict[str, Any]:
    return runtime_face.face_model_summary(faces, include_embeddings=include_embeddings)


def best_person_detection(image: Image.Image, persons: list[dict[str, Any]]) -> tuple[Image.Image, dict[str, Any]] | None:
    return runtime_body.best_person_detection(image, persons)


async def detect_body_embedding_crop(image: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    await _prepare_runtime_dependencies()
    try:
        return await runtime_body.detect_body_embedding_crop(image)
    finally:
        await _commit_body_runtime_flags()


async def run_reid_body_embedding(image: Image.Image) -> tuple[list[float], dict[str, Any]] | None:
    await _prepare_runtime_dependencies()
    try:
        return await runtime_body.run_reid_body_embedding(image)
    finally:
        await _commit_body_runtime_flags()


def fallback_body_embedding_record(image: Image.Image, *, include_embedding: bool) -> dict[str, Any]:
    return runtime_body.fallback_body_embedding_record(image, include_embedding=include_embedding)


async def infer_body_record_for_image(image: Image.Image, *, include_embedding: bool = True) -> dict[str, Any]:
    await _prepare_runtime_dependencies()
    try:
        return await runtime_body.infer_body_record_for_image(image, include_embedding=include_embedding)
    finally:
        await _commit_body_runtime_flags()


async def run_rtmpose(image: Image.Image) -> dict[str, Any] | None:
    await _prepare_runtime_dependencies()
    return await runtime_pose.run_rtmpose(image)


async def infer_pose_record_for_image(image: Image.Image) -> dict[str, Any]:
    await _prepare_runtime_dependencies()
    return await runtime_pose.infer_pose_record_for_image(image)


async def run_opengait(images: list[Image.Image]) -> tuple[list[float] | None, dict[str, Any]] | None:
    await _prepare_runtime_dependencies()
    return await runtime_gait.run_opengait(images)


async def infer_gait_embedding_for_images(images: list[Image.Image]) -> tuple[list[float] | None, dict[str, Any]]:
    await _prepare_runtime_dependencies()
    return await runtime_gait.infer_gait_embedding_for_images(images)


async def run_attribute_reid_appearance(image: Image.Image) -> tuple[list[float], dict[str, Any]] | None:
    await _prepare_runtime_dependencies()
    return await runtime_appearance.run_attribute_reid_appearance(image)


async def infer_appearance_record_for_image(image: Image.Image, *, include_embedding: bool = True) -> dict[str, Any]:
    await _prepare_runtime_dependencies()
    return await runtime_appearance.infer_appearance_record_for_image(image, include_embedding=include_embedding)


__all__ = [
    "ARCFACE_CANONICAL_112",
    "COCO17_KEYPOINT_NAMES",
    "COCO17_SKELETON",
    "CapabilityRuntime",
    "FACE_DETECTION_FALLBACK_MODEL_ID",
    "FACE_KEYPOINT_NAMES",
    "FALLBACK_EMBEDDING_MODEL_ID",
    "FALLBACK_EMBEDDING_VERSION",
    "apply_arcface_embeddings",
    "apply_fallback_face_embeddings",
    "arcface_aligned_crop",
    "batch_slice",
    "best_person_detection",
    "decode_rtmpose_outputs",
    "decode_scrfd_anchor_outputs",
    "detect_body_embedding_crop",
    "embedding_model_info",
    "embedding_rows",
    "face_crop_from_box",
    "face_model_summary",
    "fallback_body_embedding_record",
    "fallback_face_records_with_crops",
    "gait_sequence_tensor",
    "get_capability_runtime",
    "infer_appearance_record_for_image",
    "infer_best_face_embedding_for_image",
    "infer_body_record_for_image",
    "infer_face_records_for_image",
    "infer_gait_embedding_for_images",
    "infer_person_frames",
    "infer_pose_record_for_image",
    "infer_reid_images",
    "infer_scrfd_stride",
    "letterbox_tensor",
    "match_rows_by_length",
    "normalize_scores",
    "parse_scrfd_outputs",
    "preprocess_rgb_array",
    "resize_tensor",
    "restore_landmarks",
    "round_normalized_embedding",
    "rows_with_last_dim",
    "run_attribute_reid_appearance",
    "run_model_bundle",
    "run_model_bundle_batch",
    "run_opengait",
    "run_reid_body_embedding",
    "run_rtmpose",
    "run_scrfd_face_detection",
    "runtime_input_size",
    "runtime_input_value",
    "runtime_output_value",
    "scale_pose_point",
    "scrfd_anchor_centers",
    "softmax_max",
    "strip_face_crop",
]
