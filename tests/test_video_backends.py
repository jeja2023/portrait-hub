import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.media.video_backends import decode_frames_at_indexes, pyav_available, resolve_decode_backend


@pytest.fixture
def synthetic_video() -> Path:
    """Create a 12-frame test video with unique frame content (each frame = grayscale value i*20)."""
    tmp = Path(tempfile.gettempdir()) / "test_decode_backends.mp4"
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 48))
    for i in range(12):
        frame = np.ones((48, 64, 3), dtype=np.uint8) * (i * 20)
        writer.write(frame)
    writer.release()
    yield tmp
    tmp.unlink(missing_ok=True)


def test_resolve_decode_backend_auto_fallback() -> None:
    # 自动模式：可用时使用 pyav，否则使用 opencv
    resolved = resolve_decode_backend("auto")
    assert resolved in {"opencv", "pyav"}
    if pyav_available():
        assert resolved == "pyav"
    else:
        assert resolved == "opencv"


def test_resolve_decode_backend_explicit_opencv() -> None:
    assert resolve_decode_backend("opencv") == "opencv"


def test_opencv_backend_decodes_sorted_indexes(synthetic_video: Path) -> None:
    frames, backend = decode_frames_at_indexes(str(synthetic_video), [0, 3, 7, 11], backend="opencv")
    assert backend == "opencv"
    assert len(frames) == 4
    # 第 0 帧对应灰度 0；第 3 帧对应灰度 60；第 7 帧对应灰度 140；第 11 帧对应灰度 220
    assert frames[0] is not None and np.mean(frames[0]) < 10
    assert frames[1] is not None and 55 < np.mean(frames[1]) < 65
    assert frames[2] is not None and 135 < np.mean(frames[2]) < 145
    assert frames[3] is not None and 215 < np.mean(frames[3]) < 225


def test_opencv_backend_handles_nonexistent_file() -> None:
    frames, backend = decode_frames_at_indexes("/nonexistent.mp4", [0, 1], backend="opencv")
    assert backend == "opencv"
    assert frames == [None, None]


def test_pyav_backend_graceful_fallback_when_unavailable(synthetic_video: Path) -> None:
    # 即使请求 pyav 但它不可用（或文件/解码失败），也会回退到 opencv。
    frames, backend = decode_frames_at_indexes(str(synthetic_video), [0, 5], backend="pyav")
    assert backend in {"opencv", "pyav"}
    assert len(frames) == 2
    # 如果 pyav 可用，backend="pyav"；否则 backend="opencv"（回退）。
    # 两者都应正确解码帧。
    if backend == "pyav":
        assert frames[0] is not None
        assert frames[1] is not None
