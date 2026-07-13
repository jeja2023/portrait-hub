import asyncio
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageOps, UnidentifiedImageError

from app.media.fingerprint import hamming_hex, perceptual_hash_payload
from app.media.media_schema import DecodedImage, MediaFrame
from app.media.quality import assess_image_quality
from app.portrait_async import gather_limited
from app.settings import MAX_IMAGE_BYTES, MAX_IMAGE_DECODE_CONCURRENCY, MAX_IMAGE_PIXELS


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SUPPORTED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "BMP"}
IMAGE_EXTENSION_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".bmp": "BMP",
}


def validate_image_filename(filename: str | None) -> None:
    if not filename:
        return
    suffix = Path(filename).suffix.lower()
    if suffix and suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持的图片扩展名",
        )


def expected_format_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    return IMAGE_EXTENSION_FORMATS.get(Path(filename).suffix.lower())


def sniff_image_format(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"BM"):
        return "BMP"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def validate_image_content(data: bytes, filename: str | None = None) -> str:
    validate_image_filename(filename)
    detected = sniff_image_format(data)
    if detected is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="上传文件包含不支持的图片内容",
        )
    expected = expected_format_from_filename(filename)
    if expected is not None and expected != detected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片扩展名与检测到的内容不匹配",
        )
    return detected


async def read_limited_upload(file: UploadFile, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    read_size = max(0, max_bytes) + 1
    data = await file.read(read_size)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="上传文件为空",
        )
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"上传文件过大：最大 {max_bytes} 字节",
        )
    return data


def decode_image_bytes(data: bytes, filename: str | None = None, source_id: str | None = None) -> DecodedImage:
    detected_format = validate_image_content(data, filename)
    try:
        with Image.open(BytesIO(data)) as opened:
            image_format = opened.format or "UNKNOWN"
            if image_format not in SUPPORTED_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="不支持的图片格式",
                )
            if image_format != detected_format:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="图片内容与解码出的图片格式不匹配",
                )
            opened_width, opened_height = opened.size
            if opened_width * opened_height > MAX_IMAGE_PIXELS:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"图片像素过多：最大 {MAX_IMAGE_PIXELS}",
                )
            transposed = ImageOps.exif_transpose(opened)
            if transposed is None:
                transposed = opened
            image = transposed.convert("RGB")
    except HTTPException:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="上传文件不是有效图片",
        ) from exc

    width, height = image.size
    if width * height > MAX_IMAGE_PIXELS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"图片像素过多：最大 {MAX_IMAGE_PIXELS}",
        )

    quality = assess_image_quality(image)
    fingerprint = perceptual_hash_payload(image, data)
    frame = MediaFrame(
        source_type="image",
        source_id=source_id or f"src_{uuid4().hex}",
        frame_index=0,
        pts_ms=0,
        width=width,
        height=height,
        filename=filename,
        quality=quality,
        fingerprint=fingerprint,
    )
    return DecodedImage(image=image, frame=frame, format=image_format.lower(), bytes_count=len(data), data=data)


async def decode_upload_image(file: UploadFile, source_id: str | None = None) -> DecodedImage:
    data = await read_limited_upload(file)
    return await asyncio.to_thread(decode_image_bytes, data, file.filename, source_id)


async def decode_upload_images(files: list[UploadFile]) -> list[DecodedImage]:
    decoded = await gather_limited(
        files,
        lambda _index, file: decode_upload_image(file),
        limit=MAX_IMAGE_DECODE_CONCURRENCY,
    )
    mark_near_duplicates(decoded)
    return decoded


def duplicate_distance(left: dict[str, str | None], right: dict[str, str | None]) -> int | None:
    if left.get("sha256") and left.get("sha256") == right.get("sha256"):
        return 0
    distances = [
        distance
        for distance in [
            hamming_hex(left.get("average_hash"), right.get("average_hash")),
            hamming_hex(left.get("difference_hash"), right.get("difference_hash")),
        ]
        if distance is not None
    ]
    return min(distances) if distances else None


def mark_near_duplicates(decoded: list[DecodedImage], max_hash_distance: int = 4) -> None:
    seen: list[DecodedImage] = []
    for item in decoded:
        fingerprint = item.frame.fingerprint or {}
        for previous in seen:
            distance = duplicate_distance(fingerprint, previous.frame.fingerprint or {})
            if distance is not None and distance <= max_hash_distance:
                item.frame.duplicate_of = previous.frame.source_id
                item.frame.duplicate_distance = distance
                break
        seen.append(item)


__all__ = [
    "SUPPORTED_IMAGE_EXTENSIONS",
    "SUPPORTED_IMAGE_FORMATS",
    "IMAGE_EXTENSION_FORMATS",
    "validate_image_filename",
    "expected_format_from_filename",
    "sniff_image_format",
    "validate_image_content",
    "read_limited_upload",
    "decode_image_bytes",
    "decode_upload_image",
    "decode_upload_images",
    "duplicate_distance",
    "mark_near_duplicates",
]
