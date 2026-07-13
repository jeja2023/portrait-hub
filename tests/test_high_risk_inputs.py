from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import Image

from app import routes_health
from app.media import stream_decode
from app.media.image_decode import decode_image_bytes
from app.video_io import validate_stream_url
from main import app


def _png_bytes(width: int = 2, height: int = 2) -> bytes:
    image = Image.new("RGB", (width, height), color=(255, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_stream_validation_rejects_ipv6_loopback_literal() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_stream_url("rtsp://[::1]/live")

    assert exc_info.value.status_code == 400
    assert "SSRF" in str(exc_info.value.detail)


def test_stream_validation_rejects_lookalike_suffix_hosts(monkeypatch) -> None:
    monkeypatch.setattr(stream_decode, "STREAM_ALLOWED_HOSTS", ["example.com"])

    with pytest.raises(HTTPException) as exc_info:
        validate_stream_url("rtsp://example.com.evil/live")

    assert exc_info.value.status_code == 400
    assert "STREAM_ALLOWED_HOSTS" in str(exc_info.value.detail)


def test_decode_image_rejects_too_many_pixels_before_full_decode(monkeypatch) -> None:
    monkeypatch.setattr("app.media.image_decode.MAX_IMAGE_PIXELS", 1)

    with pytest.raises(HTTPException) as exc_info:
        decode_image_bytes(_png_bytes(), filename="sample.png")

    assert exc_info.value.status_code == 413
    assert "图片像素过多" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_ready_redacts_dependency_details_without_auth(monkeypatch) -> None:
    async def fake_run_blocking_io(func, *args):
        return func(*args)

    monkeypatch.setattr(routes_health, "run_blocking_io", fake_run_blocking_io)
    monkeypatch.setattr(routes_health, "READY_CHECK_DEPENDENCIES", True)
    monkeypatch.setattr(routes_health, "runtime_provider_status", lambda available: {"ready": True})
    monkeypatch.setattr(
        routes_health,
        "readiness_dependency_checks",
        lambda: {"postgres": {"status": "error", "error": "secret-dsn"}},
    )
    client = TestClient(app)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {"status": "not_ready"}
