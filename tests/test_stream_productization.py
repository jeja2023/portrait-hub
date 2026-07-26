from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from app import portrait_stream_worker
from app.portrait_streams import StreamRecord


def test_stream_profile_normalizes_roi_targets_and_privacy() -> None:
    parameters = portrait_stream_worker.stream_analysis_parameters(
        {
            "profile": "privacy_first",
            "roi": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.6},
            "target_classes": ["person"],
        }
    )

    assert parameters["profile"] == "privacy_first"
    assert parameters["include_embeddings"] is False
    assert parameters["privacy_mask"] == "persons"
    assert parameters["roi"] == {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.6}
    assert parameters["target_classes"] == ["person"]

    with pytest.raises(HTTPException, match="roi"):
        portrait_stream_worker.stream_analysis_parameters(
            {"roi": {"x": 0.8, "y": 0.0, "width": 0.4, "height": 1.0}}
        )


@pytest.mark.asyncio
async def test_stream_roi_filters_before_tracking_and_masks_archived_images(monkeypatch, workspace_tmp_path: Path) -> None:
    image = Image.new("RGB", (100, 100), "white")
    for x in range(40, 60):
        for y in range(20, 80):
            image.putpixel((x, y), (0, 0, 0) if (x + y) % 2 else (255, 0, 0))
    original = image.tobytes()
    images = [image]
    stream = StreamRecord(
        stream_id="stream_1",
        tenant_id="tenant-a:default",
        stream_url="rtsp://example.test/live",
        name="Entrance",
        settings={
            "roi": {"x": 0.25, "y": 0.0, "width": 0.5, "height": 1.0},
            "target_classes": ["person"],
            "privacy_mask": "persons",
        },
        metadata={},
    )

    async def fake_infer(images, filenames, *args, person_filter=None, **kwargs):
        persons = [
            {"track_id": "track_inside", "class_name": "person", "box": [40, 20, 60, 80]},
            {"track_id": "track_outside", "class_name": "person", "box": [80, 20, 98, 80]},
        ]
        kept = [person for person in persons if person_filter({}, person, images[0])]
        return {
            "frames": [{"frame_index": 0, "persons": kept, "person_count": len(kept)}],
            "tracks": [{"track_id": person["track_id"]} for person in kept],
            "tracker": {"algorithm": "test"},
            "person_count": len(kept),
            "track_count": len(kept),
            "embedding_count": 0,
            "detector_key": "detector/model",
            "reid_key": "reid/model",
        }

    monkeypatch.setattr(portrait_stream_worker, "infer_tracks_for_images", fake_infer)
    result = await portrait_stream_worker.analyze_stream_frames(
        stream,
        images,
        {"source_frame_indexes": [10], "source_seconds": [1.0], "fps": 10.0},
    )

    assert [person["track_id"] for person in result["frames"][0]["persons"]] == ["track_inside"]
    assert result["track_count"] == 1
    assert result["roi"]["x"] == 0.25
    assert result["privacy_mask"] == "persons"
    assert images[0].tobytes() != original


@pytest.mark.asyncio
async def test_stream_reconnect_budget_resets_after_successful_output(monkeypatch) -> None:
    stream = StreamRecord(
        stream_id="stream_long_running",
        tenant_id="tenant-a:default",
        stream_url="rtsp://example.test/live",
        name="Long running stream",
        settings={},
        metadata={},
        status="running",
    )
    portrait_stream_worker.STREAM_WORKER_SESSIONS.clear()
    pull_count = 0
    analysis_count = 0

    async def intermittent_batches(*args, **kwargs):
        nonlocal pull_count
        pull_count += 1
        yield [Image.new("RGB", (8, 8), "white")], [pull_count], [float(pull_count)], 1.0, None

    async def successful_analysis(*args, **kwargs):
        nonlocal analysis_count
        analysis_count += 1
        if analysis_count == 5:
            stream.status = "stopped"
        return {
            "analysis_mode": "person_tracks",
            "frames": [],
            "tracks": [],
            "person_count": 0,
            "track_count": 0,
        }

    async def no_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(portrait_stream_worker, "validate_media_stream_url", lambda value: None)
    monkeypatch.setattr(portrait_stream_worker, "aiter_video_frame_batches", intermittent_batches)
    monkeypatch.setattr(portrait_stream_worker, "analyze_stream_frames", successful_analysis)
    monkeypatch.setattr(portrait_stream_worker, "create_analysis_archive", lambda **kwargs: None)
    monkeypatch.setattr(portrait_stream_worker, "emit_stream_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(portrait_stream_worker.asyncio, "sleep", no_delay)

    report = await portrait_stream_worker.run_stream_worker_session(stream, max_reconnects=1)

    assert pull_count == 5
    assert analysis_count == 5
    assert report["restart_count"] == 4
    assert stream.status == "stopped"
