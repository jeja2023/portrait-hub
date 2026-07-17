from io import BytesIO

from PIL import Image

from app.media.image_decode import decode_image_bytes, mark_near_duplicates


def png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_decode_image_adds_content_and_perceptual_fingerprint() -> None:
    decoded = decode_image_bytes(png_bytes((20, 40, 80)), "sample.png", source_id="a")

    fingerprint = decoded.frame.fingerprint
    assert fingerprint is not None
    assert len(fingerprint["sha256"]) == 64
    assert len(fingerprint["average_hash"]) == 16
    assert len(fingerprint["difference_hash"]) == 16


def test_mark_near_duplicates_uses_first_seen_source() -> None:
    first = decode_image_bytes(png_bytes((10, 20, 30)), "a.png", source_id="first")
    second = decode_image_bytes(png_bytes((10, 20, 30)), "b.png", source_id="second")

    mark_near_duplicates([first, second])

    assert first.frame.duplicate_of is None
    assert second.frame.duplicate_of == "first"
    assert second.frame.duplicate_distance == 0


def test_decode_image_accepts_extension_content_mismatch() -> None:
    decoded = decode_image_bytes(png_bytes((20, 40, 80)), "secret-token.jpg")

    assert decoded.format == "png"
    assert decoded.image.mode == "RGB"
