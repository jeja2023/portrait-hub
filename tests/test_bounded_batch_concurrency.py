import asyncio
from io import BytesIO
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile, status
from PIL import Image

from app.media.image_decode import decode_upload_images
from app.portrait_async import gather_limited
from app.portrait_gallery_orchestration import gallery_search_batch_results
from app.routes_portrait_compare import compare_batch_results


def png_upload(name: str, color: tuple[int, int, int]) -> UploadFile:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color=color).save(buffer, format="PNG")
    buffer.seek(0)
    return UploadFile(filename=f"{name}.png", file=buffer)


@pytest.mark.asyncio
async def test_gather_limited_preserves_order_and_bounds_concurrency() -> None:
    running = 0
    max_running = 0

    async def worker(_index: int, value: int) -> int:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        try:
            await asyncio.sleep(0.01 * (4 - value))
            return value * 10
        finally:
            running -= 1

    results = await gather_limited([0, 1, 2, 3], worker, limit=2)

    assert results == [0, 10, 20, 30]
    assert max_running == 2


@pytest.mark.asyncio
async def test_gather_limited_cancels_pending_work_on_failure() -> None:
    cancelled = 0
    started: list[int] = []

    async def worker(_index: int, value: int) -> int:
        nonlocal cancelled
        started.append(value)
        if value == 1:
            raise RuntimeError("模拟异常")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled += 1
            raise
        return value

    with pytest.raises(RuntimeError, match="模拟异常"):
        await gather_limited([0, 1, 2], worker, limit=2)

    assert started == [0, 1]
    assert cancelled == 1


@pytest.mark.asyncio
async def test_decode_upload_images_uses_bounded_concurrency_and_order(monkeypatch) -> None:
    import app.media.image_decode as image_decode

    running = 0
    max_running = 0

    async def fake_decode(file: UploadFile, source_id: str | None = None) -> Any:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        try:
            await asyncio.sleep(0.01)
            return image_decode.decode_image_bytes(await file.read(), file.filename, source_id)
        finally:
            running -= 1

    monkeypatch.setattr(image_decode, "MAX_IMAGE_DECODE_CONCURRENCY", 2)
    monkeypatch.setattr(image_decode, "decode_upload_image", fake_decode)

    decoded = await decode_upload_images(
        [
            png_upload("first", (10, 20, 30)),
            png_upload("second", (40, 50, 60)),
            png_upload("third", (70, 80, 90)),
        ]
    )

    assert [item.frame.filename for item in decoded] == ["first.png", "second.png", "third.png"]
    assert max_running == 2


@pytest.mark.asyncio
async def test_gallery_batch_progress_is_serial_and_results_are_ordered(monkeypatch) -> None:
    import app.portrait_gallery_orchestration as orchestration

    async def fake_extract(_image: Any, _modality: str) -> tuple[list[float], float, str, str]:
        await asyncio.sleep(0)
        return [1.0, 0.0], 0.8, "model", "v1"

    def fake_search(embedding, **kwargs):
        return [{"person_id": f"p_{embedding[0]}", "similarity": 1.0}]

    async def fake_run_blocking_io(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    progress: list[float] = []

    async def progress_callback(value: float) -> None:
        await asyncio.sleep(0)
        progress.append(value)

    monkeypatch.setattr(orchestration, "MAX_GALLERY_SEARCH_BATCH_CONCURRENCY", 2)
    monkeypatch.setattr(orchestration, "extract_gallery_embedding", fake_extract)
    monkeypatch.setattr(orchestration, "search_gallery", fake_search)
    monkeypatch.setattr(orchestration, "run_blocking_io", fake_run_blocking_io)

    results = await gallery_search_batch_results(
        [png_upload("b", (20, 20, 20)), png_upload("a", (10, 10, 10)), png_upload("c", (30, 30, 30))],
        modality="body",
        top_k=3,
        threshold_profile="normal",
        tenant_id="default",
        progress_callback=progress_callback,
    )

    assert [item["index"] for item in results] == [0, 1, 2]
    assert progress == sorted(progress)
    assert progress[-1] == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_compare_batch_cancels_other_pairs_after_failure(monkeypatch) -> None:
    import app.routes_portrait_compare as compare_routes

    calls = 0

    async def fake_decode(file: UploadFile, source_id: str | None = None) -> Any:
        nonlocal calls
        calls += 1
        if file.filename == "bad.png":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片无效")
        await asyncio.sleep(10)
        return None

    monkeypatch.setattr(compare_routes, "MAX_COMPARE_BATCH_CONCURRENCY", 2)
    monkeypatch.setattr(compare_routes, "decode_upload_image", fake_decode)

    with pytest.raises(HTTPException) as exc_info:
        await compare_batch_results(
            [png_upload("slow-a", (1, 1, 1)), UploadFile(filename="bad.png", file=BytesIO(b"bad"))],
            [png_upload("slow-b", (2, 2, 2)), png_upload("unused", (3, 3, 3))],
            modality_key="body",
            threshold_profile="normal",
            include_vectors=False,
        )

    assert exc_info.value.status_code == 400
    assert calls == 2
