import ipaddress
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from app.image_io import read_image_file as read_legacy_image_file
from app.media import stream_decode
from app.media.image_decode import read_limited_upload, validate_image_content
from app.media.stream_decode import mask_stream_url
from app.video_io import (
    consecutive_hash_distances,
    count_near_duplicate_fingerprints,
    frame_hash_distance,
    public_video_metadata,
    read_video_file,
    validate_stream_url,
    validate_video_content,
)


def test_video_validation_rejects_extension_container_mismatch() -> None:
    avi_bytes = b"RIFF\x20\x00\x00\x00AVI " + b"\x00" * 32

    with pytest.raises(HTTPException) as exc_info:
        validate_video_content(avi_bytes, "secret-token.mp4")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "video extension does not match detected content"
    assert "secret-token" not in exc_info.value.detail
    assert "avi" not in exc_info.value.detail


def test_image_validation_redacts_extension_and_detected_format() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    with pytest.raises(HTTPException) as unsupported:
        validate_image_content(png_bytes, "secret-token.evil")
    with pytest.raises(HTTPException) as mismatch:
        validate_image_content(png_bytes, "secret-token.jpg")

    assert unsupported.value.status_code == 400
    assert unsupported.value.detail == "unsupported image extension"
    assert "secret-token" not in unsupported.value.detail
    assert ".evil" not in unsupported.value.detail
    assert mismatch.value.status_code == 400
    assert mismatch.value.detail == "image extension does not match detected content"
    assert "secret-token" not in mismatch.value.detail
    assert "png" not in mismatch.value.detail.lower()
    assert "jpg" not in mismatch.value.detail.lower()


def test_video_validation_redacts_extension_and_detected_container() -> None:
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32

    with pytest.raises(HTTPException) as unsupported:
        validate_video_content(mp4_bytes, "secret-token.evil")

    assert unsupported.value.status_code == 400
    assert unsupported.value.detail == "unsupported video extension"
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
async def test_upload_too_large_errors_do_not_echo_actual_size() -> None:
    upload = UploadFile(filename="secret-token-image.png", file=BytesIO(b"secret-token"))

    with pytest.raises(HTTPException) as exc_info:
        await read_limited_upload(upload, max_bytes=4)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "uploaded file is too large: max 4 bytes"
    assert "12" not in exc_info.value.detail
    assert "secret-token" not in exc_info.value.detail


def test_video_validation_accepts_iso_bmff_for_mp4() -> None:
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32

    assert validate_video_content(mp4_bytes, "sample.mp4") == "iso_bmff"


def test_legacy_stream_validation_uses_ssrf_rules_and_masks_credentials() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_stream_url("rtsp://127.0.0.1/live")

    assert exc_info.value.status_code == 400
    assert "SSRF" in exc_info.value.detail
    assert mask_stream_url("rtsp://user:secret@example.com/live") == "rtsp://***:***@example.com/live"
    assert mask_stream_url("rtsp://example.com/live?token=secret#frag") == "rtsp://example.com/live?<redacted>#<redacted>"


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
