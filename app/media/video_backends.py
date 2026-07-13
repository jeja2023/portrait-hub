"""可插拔的视频解码后端：按帧号取帧。

- opencv（默认，无新依赖）：单次打开 capture，对升序帧号做“前向 grab 跳帧 + 仅在目标帧 retrieve”，
  避免对每个目标帧反复 `set(CAP_PROP_POS_FRAMES)` 触发的关键帧重定位（OpenCV 上既慢又可能不精确）。
- pyav（可选，requirements/prod-optional.txt 的 `av`）：单遍顺序解码，帧精确；未安装或出错时回退 opencv。

两个后端都返回 BGR ndarray（与 cv2 一致）或 None（该帧读取失败），顺序与传入帧号一致。
"""

from __future__ import annotations

import importlib.util
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from app.metrics import record_video_decode_backend
from app.observability import logger
from app.portrait_response import exception_log_summary
from app.settings import VIDEO_DECODE_BACKEND

Array = npt.NDArray[Any]


def pyav_available() -> bool:
    return importlib.util.find_spec("av") is not None


def resolve_decode_backend(preferred: str | None = None) -> str:
    backend = (preferred or VIDEO_DECODE_BACKEND or "auto").strip().lower()
    if backend in {"auto", "pyav"}:
        return "pyav" if pyav_available() else "opencv"
    return "opencv"


def _decode_opencv(source: str, sorted_indexes: list[int]) -> list[Array | None]:
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        return [None] * len(sorted_indexes)
    frames: list[Array | None] = []
    position = 0
    try:
        for target in sorted_indexes:
            if target < position:
                # 非单调（理论上不会发生，selection 已升序）：退化为一次精确定位。
                capture.set(cv2.CAP_PROP_POS_FRAMES, target)
                position = target
            while position < target:
                if not capture.grab():  # grab 只解复用/跳帧，不做完整解码与色彩转换，比 read 便宜
                    break
                position += 1
            ok, frame = capture.read()
            position += 1
            frames.append(frame if ok else None)
    finally:
        capture.release()
    return frames


def _decode_pyav(source: str, sorted_indexes: list[int]) -> list[Array | None] | None:
    try:
        import av
    except Exception:
        return None
    try:
        targets = set(sorted_indexes)
        last_target = sorted_indexes[-1]
        collected: dict[int, Array] = {}
        container = av.open(source)
        try:
            index = 0
            for frame in container.decode(video=0):
                if index in targets:
                    rgb = frame.to_ndarray(format="rgb24")
                    collected[index] = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                if index >= last_target:
                    break
                index += 1
        finally:
            container.close()
        return [collected.get(target) for target in sorted_indexes]
    except Exception as exc:
        logger.warning("PyAV 解码失败，回退到 OpenCV: %s", exception_log_summary(exc))
        return None


def decode_frames_at_indexes(source: str, sorted_indexes: list[int], *, backend: str | None = None) -> tuple[list[Array | None], str]:
    """按升序帧号取帧，返回 (frames_bgr_or_none, backend_used)。记录所用后端指标。"""
    if not sorted_indexes:
        return [], "none"
    chosen = resolve_decode_backend(backend)
    if chosen == "pyav":
        frames = _decode_pyav(source, sorted_indexes)
        if frames is not None:
            record_video_decode_backend("pyav")
            return frames, "pyav"
        # PyAV 不可用或失败：回退 opencv。
    record_video_decode_backend("opencv")
    return _decode_opencv(source, sorted_indexes), "opencv"


__all__ = [
    "pyav_available",
    "resolve_decode_backend",
    "decode_frames_at_indexes",
]
