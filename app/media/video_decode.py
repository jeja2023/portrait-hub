import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import cv2

from app.observability import logger
from app.video_io import SUPPORTED_VIDEO_EXTENSIONS, extract_video_frames_from_path


def probe_video_file(path: str) -> dict[str, Any]:
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError("打开视频源失败")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fourcc = int(capture.get(cv2.CAP_PROP_FOURCC) or 0)
        codec = "".join(chr((fourcc >> 8 * index) & 0xFF) for index in range(4)).strip()
        duration_ms = int((frame_count / fps) * 1000) if fps > 0 and frame_count > 0 else 0
        return {
            "width": width,
            "height": height,
            "fps": fps,
            "frame_count": frame_count,
            "duration_ms": duration_ms,
            "codec": codec,
        }
    finally:
        capture.release()


async def extract_video_frames_from_bytes(
    data: bytes,
    filename: str | None,
    sample_interval_seconds: float,
    batch_size: int,
) -> tuple[list[Any], dict[str, Any]]:
    suffix = Path(filename or "video.mp4").suffix.lower() or ".mp4"
    if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        suffix = ".mp4"

    temp_path = ""
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(data)
            temp_path = temp_file.name

        metadata = await asyncio.to_thread(probe_video_file, temp_path)
        frames, extract_meta = await asyncio.to_thread(
            extract_video_frames_from_path,
            temp_path,
            sample_interval_seconds,
            batch_size,
            None,
        )
        metadata.update(extract_meta)
        metadata["video_bytes"] = len(data)
        return frames, metadata
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                logger.warning("删除临时视频文件失败")
