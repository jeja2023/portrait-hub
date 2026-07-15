import asyncio
import hashlib
import math
import os
import queue
import threading
from collections.abc import AsyncGenerator, Callable, Generator
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

import cv2
import numpy as np
import numpy.typing as npt
from fastapi import HTTPException, UploadFile, status
from PIL import Image

from app.media.fingerprint import hamming_hex, perceptual_hash_payload
from app.media.frame_sampler import hybrid_sample_indexes
from app.media.quality import assess_image_quality, clamp01
from app.media.stream_decode import revalidate_stream_url, validate_media_stream_url
from app.media.video_backends import decode_frames_at_indexes
from app.observability import logger, now
from app.settings import MAX_VIDEO_BYTES, VIDEO_JOB_INPUT_DIR, VIDEO_UPLOAD_CHUNK_BYTES

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
SENSITIVE_VIDEO_METADATA_KEYS = {"filename", "video_bytes", "frame_fingerprints"}
VIDEO_EXTENSION_CONTAINERS = {
    ".mp4": {"iso_bmff"},
    ".mov": {"iso_bmff"},
    ".m4v": {"iso_bmff"},
    ".avi": {"avi"},
    ".mkv": {"matroska"},
    ".webm": {"matroska"},
}
Array = npt.NDArray[Any]
VideoFrameBatch = tuple[list[Image.Image], list[int], list[float], float, int]


def public_video_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in (metadata or {}).items()
        if key not in SENSITIVE_VIDEO_METADATA_KEYS
    }


def validate_video_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    suffix = Path(filename).suffix.lower()
    if suffix and suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持的视频扩展名",
        )
    return suffix or None


def sniff_video_container(data: bytes) -> str | None:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "avi"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "iso_bmff"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"
    return None


def validate_video_content(data: bytes, filename: str | None = None) -> str:
    suffix = validate_video_filename(filename)
    container = sniff_video_container(data)
    if container is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="上传视频包含不支持的容器内容",
        )
    if suffix and container not in VIDEO_EXTENSION_CONTAINERS.get(suffix, set()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="视频扩展名与检测到的内容不匹配",
        )
    return container


async def read_video_file(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    chunk_size = max(64 * 1024, int(VIDEO_UPLOAD_CHUNK_BYTES))
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_VIDEO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"上传视频过大：最大 {MAX_VIDEO_BYTES} 字节",
            )
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传视频为空")
    data = b"".join(chunks)
    validate_video_content(data[:64], file.filename)
    return data


def resolve_video_job_input(input_ref: str) -> Path:
    normalized = str(input_ref).replace("\\", "/").strip("/")
    target = (VIDEO_JOB_INPUT_DIR / normalized).resolve()
    root = VIDEO_JOB_INPUT_DIR.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("视频任务输入引用越界") from exc
    return target


def _copy_video_upload(source: BinaryIO, target: Path) -> tuple[int, bytes]:
    total = 0
    prefix = bytearray()
    chunk_size = max(64 * 1024, int(VIDEO_UPLOAD_CHUNK_BYTES))
    with target.open("wb") as output:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_VIDEO_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"上传视频过大：最大 {MAX_VIDEO_BYTES} 字节",
                )
            if len(prefix) < 64:
                prefix.extend(chunk[: 64 - len(prefix)])
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return total, bytes(prefix)


async def stage_video_upload(file: UploadFile, tenant_id: str, job_id: str) -> str:
    suffix = validate_video_filename(file.filename) or ".video"
    tenant_segment = hashlib.sha256(str(tenant_id).encode("utf-8")).hexdigest()[:24]
    input_ref = f"{tenant_segment}/{job_id}{suffix}"
    target = resolve_video_job_input(input_ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
    await file.seek(0)
    try:
        total, prefix = await asyncio.to_thread(_copy_video_upload, file.file, temporary)
        if total <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传视频为空")
        validate_video_content(prefix, file.filename)
        await asyncio.to_thread(os.replace, temporary, target)
        return input_ref
    finally:
        if temporary.exists():
            await asyncio.to_thread(temporary.unlink, missing_ok=True)


def delete_video_job_input(input_ref: str) -> None:
    resolve_video_job_input(input_ref).unlink(missing_ok=True)


def cv_frame_to_image(frame: Array) -> Image.Image:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def video_frame_timestamp_seconds(
    capture: cv2.VideoCapture,
    frame_index: int,
    fps: float,
    previous_seconds: float,
    fallback_seconds: float | None = None,
) -> float:
    timestamp_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
    timestamp_seconds = timestamp_ms / 1000.0
    if math.isfinite(timestamp_seconds) and timestamp_seconds >= previous_seconds:
        if timestamp_seconds > 0.0 or frame_index == 0:
            return timestamp_seconds
    if fps > 0:
        return max(previous_seconds, frame_index / fps)
    if fallback_seconds is not None:
        return max(previous_seconds, fallback_seconds)
    return previous_seconds


def frame_change_score(previous: Image.Image | None, current: Image.Image) -> float:
    if previous is None:
        return 0.0
    prev_gray = cv2.cvtColor(np.asarray(previous.resize((64, 64)).convert("RGB"), dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    curr_gray = cv2.cvtColor(np.asarray(current.resize((64, 64)).convert("RGB"), dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    return round(clamp01(float(np.mean(np.abs(curr_gray.astype(np.float32) - prev_gray.astype(np.float32)))) / 64.0), 6)


def frame_hash_distance(left: dict[str, Any] | None, right: dict[str, Any] | None) -> int | None:
    if not left or not right:
        return None
    distances = [
        distance
        for distance in [
            hamming_hex(left.get("average_hash"), right.get("average_hash")),
            hamming_hex(left.get("difference_hash"), right.get("difference_hash")),
        ]
        if distance is not None
    ]
    return min(distances) if distances else None


def scene_change_at(scene_change_scores: list[float], index: int) -> float:
    if index < 0 or index >= len(scene_change_scores):
        return 0.0
    try:
        return scene_change_scores[index]
    except (TypeError, ValueError):
        return 0.0


def frame_relevance_score(quality: dict[str, Any], scene_change: float) -> float:
    return (
        float(quality.get("score", 0.0)) * 0.70
        + scene_change * 0.22
        + float(quality.get("size_score", 0.0)) * 0.05
        + float(quality.get("contrast", 0.0)) * 0.03
    )


def frame_diversity_score(
    index: int,
    selected: list[int],
    fingerprints: list[dict[str, Any]] | None,
) -> float:
    if not selected or not fingerprints:
        return 1.0
    distances = [
        distance
        for selected_index in selected
        for distance in [frame_hash_distance(fingerprints[index], fingerprints[selected_index])]
        if distance is not None
    ]
    if not distances:
        return 1.0
    return clamp01(min(distances) / 32.0)


def frame_temporal_coverage_score(
    index: int,
    selected: list[int],
    source_frame_indexes: list[int],
) -> float:
    if not source_frame_indexes:
        return 0.0
    start = source_frame_indexes[0]
    end = source_frame_indexes[-1]
    span = max(1, end - start)
    frame_number = source_frame_indexes[index]
    normalized_position = clamp01((frame_number - start) / span)
    edge_coverage = 1.0 if normalized_position <= 0.10 or normalized_position >= 0.90 else 0.35
    if not selected:
        return edge_coverage
    nearest_distance = min(abs(frame_number - source_frame_indexes[other]) for other in selected)
    gap_coverage = clamp01(nearest_distance / max(1.0, span / max(1, len(selected) + 1)))
    return clamp01(gap_coverage * 0.82 + edge_coverage * 0.18)


def derive_scene_segments(
    scene_change_scores: list[float],
    source_frame_indexes: list[int],
    boundary_threshold: float = 0.38,
) -> list[int]:
    if not source_frame_indexes:
        return []
    segments = [0]
    current_segment = 0
    for index in range(1, len(source_frame_indexes)):
        score = scene_change_at(scene_change_scores, index)
        frame_gap = source_frame_indexes[index] - source_frame_indexes[index - 1]
        if score >= boundary_threshold and frame_gap >= 1:
            current_segment += 1
        segments.append(current_segment)
    return segments


def frame_scene_coverage_score(index: int, selected: list[int], segment_ids: list[int]) -> float:
    if not segment_ids:
        return 0.0
    segment_id = segment_ids[index]
    same_segment_count = sum(1 for selected_index in selected if segment_ids[selected_index] == segment_id)
    if same_segment_count <= 0:
        return 1.0
    return clamp01(1.0 / (same_segment_count + 1))


def is_near_duplicate_frame(
    index: int,
    selected: list[int],
    fingerprints: list[dict[str, Any]] | None,
    max_hash_distance: int,
) -> bool:
    if not selected or not fingerprints:
        return False
    for selected_index in selected:
        distance = frame_hash_distance(fingerprints[index], fingerprints[selected_index])
        if distance is not None and distance <= max_hash_distance:
            return True
    return False


def count_near_duplicate_fingerprints(
    fingerprints: list[dict[str, Any]],
    max_hash_distance: int = 4,
) -> int:
    duplicate_count = 0
    seen: list[dict[str, Any]] = []
    for fingerprint in fingerprints:
        if any(
            distance is not None and distance <= max_hash_distance
            for distance in [frame_hash_distance(fingerprint, previous) for previous in seen]
        ):
            duplicate_count += 1
        seen.append(fingerprint)
    return duplicate_count


def consecutive_hash_distances(fingerprints: list[dict[str, Any]]) -> list[int | None]:
    return [frame_hash_distance(left, right) for left, right in zip(fingerprints, fingerprints[1:], strict=False)]


def select_quality_diverse_positions(
    qualities: list[dict[str, Any]],
    scene_change_scores: list[float],
    source_frame_indexes: list[int],
    max_frames: int,
    fingerprints: list[dict[str, Any]] | None = None,
    duplicate_hash_distance: int = 4,
) -> list[int]:
    if len(source_frame_indexes) <= max_frames:
        return list(range(len(source_frame_indexes)))
    if max_frames <= 1:
        return [
            max(
                range(len(source_frame_indexes)),
                key=lambda index: frame_relevance_score(qualities[index], scene_change_at(scene_change_scores, index)),
            )
        ]

    span = max(1, source_frame_indexes[-1] - source_frame_indexes[0])
    min_gap = max(1, span // (max_frames * 2))
    relevance = [
        frame_relevance_score(qualities[index], scene_change_at(scene_change_scores, index))
        for index in range(len(source_frame_indexes))
    ]
    segment_ids = derive_scene_segments(scene_change_scores, source_frame_indexes)
    selected: list[int] = []
    unique_segments = sorted(set(segment_ids))
    if 0 < len(unique_segments) <= max_frames:
        segment_best_candidates: list[int] = []
        for segment_id in unique_segments:
            segment_indices = [index for index, current in enumerate(segment_ids) if current == segment_id]
            best_index = max(
                segment_indices,
                key=lambda index: (
                    relevance[index],
                    scene_change_at(scene_change_scores, index),
                    -source_frame_indexes[index],
                ),
            )
            segment_best_candidates.append(best_index)
        selected = sorted(segment_best_candidates, key=lambda index: source_frame_indexes[index])

    while len(selected) < max_frames:
        best_position: int | None = None
        best_score = -1.0
        for index in range(len(source_frame_indexes)):
            if index in selected:
                continue
            frame_number = source_frame_indexes[index]
            if any(abs(frame_number - source_frame_indexes[other]) < min_gap for other in selected):
                continue
            if is_near_duplicate_frame(index, selected, fingerprints, duplicate_hash_distance):
                continue
            diversity = frame_diversity_score(index, selected, fingerprints)
            temporal_coverage = frame_temporal_coverage_score(index, selected, source_frame_indexes)
            scene_coverage = frame_scene_coverage_score(index, selected, segment_ids)
            score = relevance[index] * 0.60 + diversity * 0.16 + temporal_coverage * 0.12 + scene_coverage * 0.12
            if score > best_score:
                best_score = score
                best_position = index
        if best_position is None:
            break
        selected.append(best_position)

    if len(selected) < max_frames:
        ranked = sorted(range(len(source_frame_indexes)), key=lambda index: relevance[index], reverse=True)
        for index in ranked:
            if index not in selected:
                selected.append(index)
            if len(selected) >= max_frames:
                break
    return sorted(selected, key=lambda index: source_frame_indexes[index])


def validate_stream_url(stream_url: str) -> str:
    return validate_media_stream_url(stream_url)


def sample_candidate_indexes(total_frames: int, frame_interval: int, max_frames: int, read_timeout_seconds: int | None) -> list[int]:
    if total_frames <= 0:
        return []
    if read_timeout_seconds is not None:
        return []
    max_candidate_frames = max(1, max_frames * 3)
    return hybrid_sample_indexes(total_frames, frame_interval, max_candidate_frames)


def candidate_analysis_image(image: Image.Image, max_side: int = 320) -> Image.Image:
    width, height = image.size
    if max(width, height) <= max_side:
        return image.copy()
    scale = max_side / float(max(width, height))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.BILINEAR)


def collect_frame_candidates(
    capture: cv2.VideoCapture,
    candidate_indexes: list[int],
    *,
    keep_original_images: bool = True,
) -> tuple[list[Image.Image], list[int], list[dict[str, Any]], list[float]]:
    candidate_images: list[Image.Image] = []
    candidate_source_indexes: list[int] = []
    candidate_qualities: list[dict[str, Any]] = []
    candidate_scene_scores: list[float] = []
    previous_candidate: Image.Image | None = None
    for source_index in candidate_indexes:
        capture.set(cv2.CAP_PROP_POS_FRAMES, source_index)
        ok, frame = capture.read()
        if not ok:
            continue
        image = cv_frame_to_image(frame)
        analysis_image = image if keep_original_images else candidate_analysis_image(image)
        candidate_images.append(analysis_image)
        candidate_source_indexes.append(source_index)
        candidate_qualities.append(assess_image_quality(image))
        candidate_scene_scores.append(frame_change_score(previous_candidate, analysis_image))
        previous_candidate = analysis_image
    return candidate_images, candidate_source_indexes, candidate_qualities, candidate_scene_scores


def read_frames_at_indexes_with_backend(
    source: str, source_frame_indexes: list[int], fallback_frames: list[Image.Image]
) -> tuple[list[Image.Image], str]:
    if not source_frame_indexes:
        return [], "none"
    decoded, backend = decode_frames_at_indexes(source, source_frame_indexes)
    frames: list[Image.Image] = []
    for position, frame in enumerate(decoded):
        if frame is not None:
            frames.append(cv_frame_to_image(frame))
        elif position < len(fallback_frames):
            frames.append(fallback_frames[position])
    return frames, backend


def read_frames_at_indexes(source: str, source_frame_indexes: list[int], fallback_frames: list[Image.Image]) -> list[Image.Image]:
    frames, _backend = read_frames_at_indexes_with_backend(source, source_frame_indexes, fallback_frames)
    return frames


def select_frames_from_candidates(
    candidate_images: list[Image.Image],
    candidate_source_indexes: list[int],
    candidate_qualities: list[dict[str, Any]],
    candidate_scene_scores: list[float],
    max_frames: int,
) -> tuple[
    list[Image.Image],
    list[int],
    list[dict[str, Any]],
    list[float],
    list[dict[str, Any]],
    list[int | None],
    int,
    list[int],
    int,
]:
    frames: list[Image.Image] = []
    source_frame_indexes: list[int] = []
    frame_qualities: list[dict[str, Any]] = []
    scene_change_scores: list[float] = []
    frame_fingerprints = [perceptual_hash_payload(image) for image in candidate_images]
    near_duplicate_candidate_count = count_near_duplicate_fingerprints(frame_fingerprints)
    selected_frame_hash_distances: list[int | None] = []
    selected_scene_segment_ids: list[int] = []
    candidate_segment_ids = derive_scene_segments(candidate_scene_scores, candidate_source_indexes)
    scene_segment_count = len(set(candidate_segment_ids)) if candidate_segment_ids else 0
    selected_positions = select_quality_diverse_positions(
        candidate_qualities,
        candidate_scene_scores,
        candidate_source_indexes,
        max_frames,
        fingerprints=frame_fingerprints,
    )
    selected_fingerprints = [frame_fingerprints[position] for position in selected_positions]
    for position in selected_positions:
        frames.append(candidate_images[position])
        source_frame_indexes.append(candidate_source_indexes[position])
        frame_qualities.append(candidate_qualities[position])
        scene_change_scores.append(candidate_scene_scores[position])
        if position < len(candidate_segment_ids):
            selected_scene_segment_ids.append(candidate_segment_ids[position])
    selected_frame_hash_distances = consecutive_hash_distances(selected_fingerprints)
    return (
        frames,
        source_frame_indexes,
        frame_qualities,
        scene_change_scores,
        selected_fingerprints,
        selected_frame_hash_distances,
        scene_segment_count,
        selected_scene_segment_ids,
        near_duplicate_candidate_count,
    )


def extract_video_frames_from_capture(
    capture: cv2.VideoCapture,
    sample_interval_seconds: float,
    batch_size: int,
    read_timeout_seconds: int | None = None,
    source: str | None = None,
) -> tuple[list[Image.Image], dict[str, Any]]:
    if not capture.isOpened():
        raise ValueError("打开视频源失败")

    start = now()
    batch_size = max(1, batch_size)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = capture.get(cv2.CAP_PROP_FPS) or 0
    width = capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
    height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0
    interval_seconds = max(0.01, float(sample_interval_seconds))
    candidate_images: list[Image.Image] = []
    candidate_source_indexes: list[int] = []
    candidate_source_seconds: list[float] = []
    candidate_qualities: list[dict[str, Any]] = []
    candidate_scene_scores: list[float] = []
    frame_index = 0
    candidate_limit = batch_size * 3
    last_sample_seconds: float | None = None
    previous_seconds = 0.0
    previous_candidate: Image.Image | None = None
    while len(candidate_images) < candidate_limit:
        if read_timeout_seconds is not None and now() - start > read_timeout_seconds:
            break
        ok, frame = capture.read()
        if not ok:
            break
        source_seconds = video_frame_timestamp_seconds(
            capture,
            frame_index,
            fps,
            previous_seconds,
            fallback_seconds=now() - start,
        )
        previous_seconds = source_seconds
        if last_sample_seconds is None or source_seconds - last_sample_seconds + 1e-9 >= interval_seconds:
            image = cv_frame_to_image(frame)
            candidate_images.append(image)
            candidate_source_indexes.append(frame_index)
            candidate_source_seconds.append(round(source_seconds, 6))
            candidate_qualities.append(assess_image_quality(image))
            candidate_scene_scores.append(frame_change_score(previous_candidate, image))
            previous_candidate = image
            last_sample_seconds = source_seconds
        frame_index += 1
    source_frames_read = frame_index

    (
        frames,
        source_frame_indexes,
        frame_qualities,
        scene_change_scores,
        frame_fingerprints,
        selected_frame_hash_distances,
        scene_segment_count,
        selected_scene_segment_ids,
        near_duplicate_candidate_count,
    ) = select_frames_from_candidates(
        candidate_images,
        candidate_source_indexes,
        candidate_qualities,
        candidate_scene_scores,
        batch_size,
    )
    seconds_by_index = dict(zip(candidate_source_indexes, candidate_source_seconds, strict=False))
    meta = {
        "decode_backend": "opencv",
        "source_frame_indexes": source_frame_indexes,
        "source_seconds": [seconds_by_index[index] for index in source_frame_indexes],
        "source_frames_read": source_frames_read,
        "source_frame_count": frame_count,
        "source_width": int(width),
        "source_height": int(height),
        "extracted_frames": len(frames),
        "fps": fps,
        "sample_interval_seconds": float(sample_interval_seconds),
        "batch_size": batch_size,
        "sampling_strategy": "media_timeline_quality_scene_diverse",
        "candidate_frames_considered": len(candidate_source_indexes),
        "scene_segment_count": scene_segment_count,
        "selected_scene_segment_count": len(set(selected_scene_segment_ids)),
        "selected_scene_segment_ids": selected_scene_segment_ids,
        "near_duplicate_candidate_count": near_duplicate_candidate_count,
        "frame_qualities": frame_qualities,
        "scene_change_scores": scene_change_scores,
        "frame_fingerprints": frame_fingerprints,
        "selected_frame_hash_distances": selected_frame_hash_distances,
        "decode_seconds": now() - start,
    }
    return frames, meta


def iter_video_frame_batches(
    source: str,
    sample_interval_seconds: float,
    batch_size: int,
    read_timeout_seconds: int | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> Generator[VideoFrameBatch, None, None]:
    """顺序 decode 整个视频，按秒采样，每 batch_size 帧 yield 一批。

    Yields: (images, source_frame_indexes, source_seconds, fps, total_frame_count)
    不截断长视频——调用方决定何时停止迭代。
    """
    revalidate_stream_url(source)
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise ValueError("打开视频源失败")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        total_frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        interval_seconds = max(0.01, float(sample_interval_seconds))
        if read_timeout_seconds is not None and hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(max(1, read_timeout_seconds) * 1000))
        batch_images: list[Image.Image] = []
        batch_indexes: list[int] = []
        batch_seconds: list[float] = []
        frame_index = 0
        last_sample_seconds: float | None = None
        previous_seconds = 0.0
        fallback_start = now()
        while True:
            if stop_requested is not None and stop_requested():
                break
            ok, frame = capture.read()
            if not ok:
                break
            source_seconds = video_frame_timestamp_seconds(
                capture,
                frame_index,
                fps,
                previous_seconds,
                fallback_seconds=now() - fallback_start,
            )
            previous_seconds = source_seconds
            if last_sample_seconds is None or source_seconds - last_sample_seconds + 1e-9 >= interval_seconds:
                batch_images.append(cv_frame_to_image(frame))
                batch_indexes.append(frame_index)
                batch_seconds.append(round(source_seconds, 6))
                last_sample_seconds = source_seconds
                if len(batch_images) >= batch_size:
                    yield batch_images, batch_indexes, batch_seconds, fps, total_frame_count
                    batch_images = []
                    batch_indexes = []
                    batch_seconds = []
            frame_index += 1
        if batch_images:
            yield batch_images, batch_indexes, batch_seconds, fps, total_frame_count
    finally:
        capture.release()


async def aiter_video_frame_batches(
    source: str,
    sample_interval_seconds: float,
    batch_size: int,
    read_timeout_seconds: int | None = None,
) -> AsyncGenerator[VideoFrameBatch, None]:
    """Bridge the blocking decoder to asyncio with one batch of read-ahead."""
    batch_queue: queue.Queue[VideoFrameBatch | BaseException | None] = queue.Queue(maxsize=1)
    stop_event = threading.Event()

    def put(item: VideoFrameBatch | BaseException | None) -> bool:
        while not stop_event.is_set():
            try:
                batch_queue.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def produce() -> None:
        try:
            for batch in iter_video_frame_batches(
                source, sample_interval_seconds, batch_size, read_timeout_seconds, stop_event.is_set
            ):
                if not put(batch):
                    return
        except BaseException as exc:
            put(exc)
        finally:
            put(None)

    producer = threading.Thread(target=produce, name="video-frame-batch-reader", daemon=True)
    producer.start()
    try:
        while True:
            try:
                item = await asyncio.to_thread(batch_queue.get, True, 0.25)
            except queue.Empty:
                if not producer.is_alive() and batch_queue.empty():
                    break
                continue
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop_event.set()
        producer.join(timeout=0.25)


def extract_video_frames_from_path(
    source: str,
    sample_interval_seconds: float,
    batch_size: int,
    read_timeout_seconds: int | None = None,
) -> tuple[list[Image.Image], dict[str, Any]]:
    # 在连接前立即重新校验远程流 URL，以缓解先前校验与本次拉流之间的 DNS rebinding
    #（对本地路径为空操作）。
    revalidate_stream_url(source)
    capture = cv2.VideoCapture(source)
    try:
        return extract_video_frames_from_capture(capture, sample_interval_seconds, batch_size, read_timeout_seconds, source)
    finally:
        capture.release()


async def extract_video_frames_from_upload(
    file: UploadFile,
    sample_interval_seconds: float,
    batch_size: int,
) -> tuple[list[Image.Image], dict[str, Any]]:
    suffix = validate_video_filename(file.filename) or ".mp4"
    temp_path = ""
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
        await file.seek(0)
        total, prefix = await asyncio.to_thread(_copy_video_upload, file.file, Path(temp_path))
        if total <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传视频为空")
        validate_video_content(prefix, file.filename)
        frames, meta = await asyncio.to_thread(
            extract_video_frames_from_path,
            temp_path,
            sample_interval_seconds,
            batch_size,
            None,
        )
        meta["video_bytes"] = total
        return frames, meta
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                logger.warning("删除临时视频文件失败")


__all__ = [
    "SENSITIVE_VIDEO_METADATA_KEYS",
    "SUPPORTED_VIDEO_EXTENSIONS",
    "VIDEO_EXTENSION_CONTAINERS",
    "aiter_video_frame_batches",
    "candidate_analysis_image",
    "collect_frame_candidates",
    "consecutive_hash_distances",
    "count_near_duplicate_fingerprints",
    "cv_frame_to_image",
    "delete_video_job_input",
    "derive_scene_segments",
    "extract_video_frames_from_capture",
    "extract_video_frames_from_path",
    "extract_video_frames_from_upload",
    "frame_change_score",
    "frame_diversity_score",
    "frame_hash_distance",
    "frame_relevance_score",
    "frame_scene_coverage_score",
    "frame_temporal_coverage_score",
    "is_near_duplicate_frame",
    "iter_video_frame_batches",
    "public_video_metadata",
    "read_frames_at_indexes",
    "read_frames_at_indexes_with_backend",
    "read_video_file",
    "resolve_video_job_input",
    "sample_candidate_indexes",
    "scene_change_at",
    "select_frames_from_candidates",
    "select_quality_diverse_positions",
    "sniff_video_container",
    "stage_video_upload",
    "validate_stream_url",
    "validate_video_content",
    "validate_video_filename",
    "video_frame_timestamp_seconds",
]
