import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

import cv2
import numpy as np
import numpy.typing as npt
from fastapi import HTTPException, UploadFile, status
from PIL import Image

from app.media.fingerprint import hamming_hex, perceptual_hash_payload
from app.media.frame_sampler import hybrid_sample_indexes
from app.media.video_backends import decode_frames_at_indexes
from app.media.quality import assess_image_quality, clamp01
from app.media.stream_decode import revalidate_stream_url, validate_media_stream_url
from app.observability import logger, now
from app.settings import MAX_VIDEO_BYTES


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
            detail="unsupported video extension",
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
            detail="uploaded video has unsupported container content",
        )
    if suffix and container not in VIDEO_EXTENSION_CONTAINERS.get(suffix, set()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="video extension does not match detected content",
        )
    return container


async def read_video_file(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="uploaded video is empty",
        )
    if len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"uploaded video is too large: max {MAX_VIDEO_BYTES} bytes",
        )
    validate_video_content(data, file.filename)
    return data


def cv_frame_to_image(frame: Array) -> Image.Image:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return cast(Image.Image, Image.fromarray(rgb))  # type: ignore[no-untyped-call]


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
    return [frame_hash_distance(left, right) for left, right in zip(fingerprints, fingerprints[1:])]


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
    frame_interval: int,
    max_frames: int,
    read_timeout_seconds: int | None = None,
    source: str | None = None,
) -> tuple[list[Image.Image], dict[str, Any]]:
    if not capture.isOpened():
        raise ValueError("failed to open video source")

    start = now()
    frame_interval = max(1, frame_interval)
    max_frames = max(1, max_frames)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = capture.get(cv2.CAP_PROP_FPS) or 0
    width = capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
    height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0

    candidate_indexes = sample_candidate_indexes(frame_count, frame_interval, max_frames, read_timeout_seconds)
    if not candidate_indexes:
        candidate_indexes = []
    source_frames_read = max(candidate_indexes) + 1 if candidate_indexes else 0

    can_reread_source_frames = source is not None and read_timeout_seconds is None and frame_count > 0
    candidate_images, candidate_source_indexes, candidate_qualities, candidate_scene_scores = collect_frame_candidates(
        capture,
        candidate_indexes,
        keep_original_images=not can_reread_source_frames,
    )

    if not candidate_images:
        # 对没有准确帧数的视频流或容器的回退处理。
        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        frame_index = 0
        candidate_limit = max_frames * 3
        previous_candidate: Image.Image | None = None
        while len(candidate_images) < candidate_limit:
            if read_timeout_seconds is not None and now() - start > read_timeout_seconds:
                break
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % frame_interval == 0:
                image = cv_frame_to_image(frame)
                candidate_images.append(image)
                candidate_source_indexes.append(frame_index)
                candidate_qualities.append(assess_image_quality(image))
                candidate_scene_scores.append(frame_change_score(previous_candidate, image))
                previous_candidate = image
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
        max_frames,
    )
    decode_backend = "opencv"
    if can_reread_source_frames and source is not None:
        frames, decode_backend = read_frames_at_indexes_with_backend(source, source_frame_indexes, frames)
    meta = {
        "decode_backend": decode_backend,
        "source_frame_indexes": source_frame_indexes,
        "source_seconds": [round(index / fps, 6) for index in source_frame_indexes] if fps else [],
        "source_frames_read": source_frames_read,
        "source_frame_count": frame_count,
        "source_width": int(width),
        "source_height": int(height),
        "extracted_frames": len(frames),
        "fps": fps,
        "frame_interval": frame_interval,
        "max_frames": max_frames,
        "sampling_strategy": "hybrid_quality_scene_diverse" if candidate_indexes else "sequential_quality_scene_diverse",
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


def extract_video_frames_from_path(
    source: str,
    frame_interval: int,
    max_frames: int,
    read_timeout_seconds: int | None = None,
) -> tuple[list[Image.Image], dict[str, Any]]:
    # 在连接前立即重新校验远程流 URL，以缓解先前校验与本次拉流之间的 DNS rebinding
    #（对本地路径为空操作）。
    revalidate_stream_url(source)
    capture = cv2.VideoCapture(source)
    try:
        return extract_video_frames_from_capture(capture, frame_interval, max_frames, read_timeout_seconds, source)
    finally:
        capture.release()


async def extract_video_frames_from_upload(
    file: UploadFile,
    frame_interval: int,
    max_frames: int,
) -> tuple[list[Image.Image], dict[str, Any]]:
    data = await read_video_file(file)
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    temp_path = ""
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(data)
            temp_path = temp_file.name
        frames, meta = await asyncio.to_thread(
            extract_video_frames_from_path,
            temp_path,
            frame_interval,
            max_frames,
            None,
        )
        meta["video_bytes"] = len(data)
        return frames, meta
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                logger.warning("failed to remove temp video file")


__all__ = [
    "SUPPORTED_VIDEO_EXTENSIONS",
    "SENSITIVE_VIDEO_METADATA_KEYS",
    "VIDEO_EXTENSION_CONTAINERS",
    "public_video_metadata",
    "validate_video_filename",
    "sniff_video_container",
    "validate_video_content",
    "read_video_file",
    "cv_frame_to_image",
    "frame_change_score",
    "frame_hash_distance",
    "scene_change_at",
    "frame_relevance_score",
    "frame_diversity_score",
    "frame_temporal_coverage_score",
    "derive_scene_segments",
    "frame_scene_coverage_score",
    "is_near_duplicate_frame",
    "count_near_duplicate_fingerprints",
    "consecutive_hash_distances",
    "select_quality_diverse_positions",
    "validate_stream_url",
    "sample_candidate_indexes",
    "candidate_analysis_image",
    "collect_frame_candidates",
    "read_frames_at_indexes",
    "read_frames_at_indexes_with_backend",
    "select_frames_from_candidates",
    "extract_video_frames_from_capture",
    "extract_video_frames_from_path",
    "extract_video_frames_from_upload",
]
