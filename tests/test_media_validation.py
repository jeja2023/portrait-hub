import asyncio
import ipaddress
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app import video_io
from app.image_io import read_image_file as read_legacy_image_file
from app.media import stream_decode
from app.media.image_decode import decode_image_bytes, read_limited_upload, validate_image_content
from app.media.stream_decode import mask_stream_url
from app.video_io import (
    aiter_video_frame_batches,
    consecutive_hash_distances,
    count_near_duplicate_fingerprints,
    delete_video_job_input,
    extract_video_frames_from_upload,
    frame_hash_distance,
    public_video_metadata,
    read_video_file,
    resolve_video_job_input,
    stage_video_upload,
    validate_stream_url,
    validate_video_content,
    video_frame_timestamp_seconds,
)


def test_video_validation_rejects_extension_container_mismatch() -> None:
    avi_bytes = b"RIFF\x20\x00\x00\x00AVI " + b"\x00" * 32

    with pytest.raises(HTTPException) as exc_info:
        validate_video_content(avi_bytes, "secret-token.mp4")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "视频扩展名与检测到的内容不匹配"
    assert "secret-token" not in exc_info.value.detail
    assert "avi" not in exc_info.value.detail


def test_image_validation_uses_supported_content_despite_filename_extension(caplog) -> None:
    buffer = BytesIO()
    Image.new("RGBA", (2, 3), (255, 0, 0, 128)).save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    assert validate_image_content(png_bytes, "secret-token.evil") == "PNG"
    assert validate_image_content(png_bytes, "secret-token.jpg") == "PNG"

    decoded = decode_image_bytes(png_bytes, "secret-token.jpg")

    assert decoded.format == "png"
    assert decoded.image.mode == "RGB"
    assert decoded.image.size == (2, 3)
    assert "image filename format mismatch ignored" in caplog.text
    assert "secret-token" not in caplog.text


def test_image_validation_rejects_unsupported_content_despite_filename() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_image_content(b"not-an-image", "sample.jpg")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "上传文件包含不支持的图片内容"


def test_video_validation_redacts_extension_and_detected_container() -> None:
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32

    with pytest.raises(HTTPException) as unsupported:
        validate_video_content(mp4_bytes, "secret-token.evil")

    assert unsupported.value.status_code == 400
    assert unsupported.value.detail == "不支持的视频扩展名"
    assert "secret-token" not in unsupported.value.detail
    assert ".evil" not in unsupported.value.detail


@pytest.mark.asyncio
async def test_upload_validation_errors_do_not_echo_filename() -> None:
    for reader, filename in [
        (read_legacy_image_file, "secret-token-legacy.png"),
        (read_limited_upload, "secret-token-image.png"),
        (read_video_file, "secret-token-video.mp4"),
    ]:
        upload = UploadFile(filename=filename, file=BytesIO(b""))
        with pytest.raises(HTTPException) as exc_info:
            await reader(upload)

        assert exc_info.value.status_code == 400
        assert "secret-token" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_video_upload_extraction_streams_to_a_temporary_file(monkeypatch) -> None:
    payload = b"\x00\x00\x00\x18ftypmp42" + b"x" * (128 * 1024)
    read_sizes: list[int] = []
    observed_path: Path | None = None

    class TrackingBytesIO(BytesIO):
        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return super().read(size)

    def fake_extract(source: str, sample_interval_seconds: float, batch_size: int, timeout: int | None):
        nonlocal observed_path
        observed_path = Path(source)
        assert observed_path.read_bytes() == payload
        return [], {"source_frame_indexes": []}

    monkeypatch.setattr(video_io, "extract_video_frames_from_path", fake_extract)
    upload = UploadFile(filename="clip.mp4", file=TrackingBytesIO(payload))

    frames, metadata = await extract_video_frames_from_upload(upload, 1.0, 16)

    assert frames == []
    assert metadata["video_bytes"] == len(payload)
    assert read_sizes and all(size > 0 for size in read_sizes)
    assert observed_path is not None and observed_path.exists() is False


@pytest.mark.asyncio
async def test_upload_too_large_errors_do_not_echo_actual_size() -> None:
    upload = UploadFile(filename="secret-token-image.png", file=BytesIO(b"secret-token"))

    with pytest.raises(HTTPException) as exc_info:
        await read_limited_upload(upload, max_bytes=4)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "上传文件过大：最大 4 字节"
    assert "12" not in exc_info.value.detail
    assert "secret-token" not in exc_info.value.detail


def test_video_validation_accepts_iso_bmff_for_mp4() -> None:
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32

    assert validate_video_content(mp4_bytes, "sample.mp4") == "iso_bmff"


def test_video_frame_timestamp_prefers_media_timeline_over_nominal_fps() -> None:
    class FakeCapture:
        def get(self, prop):
            assert prop == video_io.cv2.CAP_PROP_POS_MSEC
            return 1750.0

    assert video_frame_timestamp_seconds(FakeCapture(), 90, 30.0, 1.0) == 1.75


def test_video_frame_timestamp_uses_monotonic_fallback_without_fps() -> None:
    class FakeCapture:
        def get(self, prop):
            assert prop == video_io.cv2.CAP_PROP_POS_MSEC
            return 0.0

    assert video_frame_timestamp_seconds(FakeCapture(), 90, 0.0, 1.0, 1.25) == 1.25


@pytest.mark.asyncio
async def test_async_video_batches_yield_before_decoder_reaches_eof(monkeypatch) -> None:
    allow_second_batch = threading.Event()

    def fake_batches(source, sample_interval_seconds, batch_size, read_timeout_seconds, stop_requested):
        yield ["first"], [0], [0.0], 25.0, 100
        while not allow_second_batch.is_set() and not stop_requested():
            time.sleep(0.01)
        if not stop_requested():
            yield ["second"], [25], [1.0], 25.0, 100

    monkeypatch.setattr(video_io, "iter_video_frame_batches", fake_batches)
    batches = aiter_video_frame_batches("source", 1.0, 1)
    try:
        first = await asyncio.wait_for(anext(batches), timeout=0.5)
        assert first[0] == ["first"]
    finally:
        allow_second_batch.set()
        await batches.aclose()


def test_legacy_stream_validation_uses_ssrf_rules_and_masks_credentials() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_stream_url("rtsp://127.0.0.1/live")

    assert exc_info.value.status_code == 400
    assert "SSRF" in exc_info.value.detail
    assert mask_stream_url("rtsp://user:secret@example.com/live") == "rtsp://***:***@example.com/live"
    assert (
        mask_stream_url("rtsp://example.com/live?token=secret#frag") == "rtsp://example.com/live?<redacted>#<redacted>"
    )


def test_stream_validation_rejects_hosts_resolving_to_private_addresses(monkeypatch) -> None:
    monkeypatch.setattr(
        stream_decode,
        "resolve_stream_host_addresses",
        lambda hostname: [ipaddress.ip_address("10.0.0.25")],
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_stream_url("rtsp://camera.example.com/live")

    assert exc_info.value.status_code == 400
    assert "SSRF" in exc_info.value.detail


def test_stream_validation_accepts_allowed_public_resolution(monkeypatch) -> None:
    monkeypatch.setattr(
        stream_decode,
        "resolve_stream_host_addresses",
        lambda hostname: [ipaddress.ip_address("93.184.216.34")],
    )

    assert validate_stream_url("rtsp://camera.example.com/live") == "rtsp://camera.example.com/live"


def test_video_frame_hash_helpers_count_near_duplicates() -> None:
    fingerprints = [
        {"average_hash": "0000000000000000", "difference_hash": "0000000000000000"},
        {"average_hash": "0000000000000000", "difference_hash": "0000000000000000"},
        {"average_hash": "ffffffffffffffff", "difference_hash": "ffffffffffffffff"},
    ]

    assert frame_hash_distance(fingerprints[0], fingerprints[1]) == 0
    assert count_near_duplicate_fingerprints(fingerprints) == 1
    assert consecutive_hash_distances(fingerprints) == [0, 64]


def test_public_video_metadata_omits_stable_fingerprints_and_source_file_details() -> None:
    public = public_video_metadata(
        {
            "filename": "secret-person-name.mp4",
            "video_bytes": 12345,
            "frame_fingerprints": [{"sha256": "secret-sha", "average_hash": "secret-average"}],
            "source_frame_indexes": [0],
            "selected_frame_hash_distances": [0],
        }
    )

    assert public == {"source_frame_indexes": [0], "selected_frame_hash_distances": [0]}


@pytest.mark.asyncio
async def test_video_job_upload_is_streamed_to_private_staging(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.setattr(video_io, "VIDEO_JOB_INPUT_DIR", workspace_tmp_path / "video-inputs")
    monkeypatch.setattr(video_io, "VIDEO_UPLOAD_CHUNK_BYTES", 8)
    payload = b"\x00\x00\x00\x18ftyp" + b"\x00" * 40
    upload = UploadFile(filename="secret-person-name.mp4", file=BytesIO(payload))

    input_ref = await stage_video_upload(upload, "tenant-a", "job_0123456789abcdef")

    assert "secret-person-name" not in input_ref
    assert resolve_video_job_input(input_ref).read_bytes() == payload
    delete_video_job_input(input_ref)
    assert not resolve_video_job_input(input_ref).exists()


@pytest.mark.asyncio
async def test_video_job_upload_rejects_limit_without_leaving_partial_file(monkeypatch, workspace_tmp_path) -> None:
    input_dir = workspace_tmp_path / "video-inputs"
    monkeypatch.setattr(video_io, "VIDEO_JOB_INPUT_DIR", input_dir)
    monkeypatch.setattr(video_io, "VIDEO_UPLOAD_CHUNK_BYTES", 8)
    monkeypatch.setattr(video_io, "MAX_VIDEO_BYTES", 16)
    upload = UploadFile(filename="sample.mp4", file=BytesIO(b"\x00\x00\x00\x18ftyp" + b"\x00" * 40))

    with pytest.raises(HTTPException) as exc_info:
        await stage_video_upload(upload, "tenant-a", "job_0123456789abcdef")

    assert exc_info.value.status_code == 413
    assert list(input_dir.rglob("*.part")) == []
