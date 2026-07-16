import asyncio
import json

import pytest
from fastapi import HTTPException
from PIL import Image

from app import (
    portrait_jobs,
)
from app.portrait_jobs import (
    VIDEO_JOBS,
    VideoJob,
    create_video_job,
    job_key,
    request_cancel_video_job,
    run_video_job,
)


def test_video_job_create_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    VIDEO_JOBS.clear()

    def fail_persist(job):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_jobs, "persist_video_job", fail_persist)

    with pytest.raises(HTTPException):
        create_video_job("video.mp4", tenant_id="tenant-a")

    assert VIDEO_JOBS == {}


def test_video_job_cancel_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    VIDEO_JOBS.clear()
    job = VideoJob(
        job_id="job_cancel", tenant_id="tenant-a", filename="video.mp4", status="queued"
    )
    VIDEO_JOBS[job_key("tenant-a", "job_cancel")] = job

    def fail_persist(updated_job):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_jobs, "persist_video_job", fail_persist)

    with pytest.raises(HTTPException):
        request_cancel_video_job("job_cancel", tenant_id="tenant-a")

    stored = VIDEO_JOBS[job_key("tenant-a", "job_cancel")]
    assert stored.status == "queued"
    assert stored.cancel_requested is False


def test_video_jobs_json_state_round_trip_omits_source_filename(
    monkeypatch, workspace_tmp_path
) -> None:
    state_path = workspace_tmp_path / "jobs.json"
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_JOBS_STATE_PATH", state_path)
    VIDEO_JOBS.clear()

    created = create_video_job("secret-person-name.mp4", tenant_id="tenant-a")
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    VIDEO_JOBS.clear()
    portrait_jobs.load_video_jobs_state()

    assert VIDEO_JOBS[job_key("tenant-a", created.job_id)].filename is None
    assert "filename" not in persisted["jobs"][0]
    assert "secret-person-name" not in json.dumps(persisted, ensure_ascii=False)


def test_video_job_public_error_redacts_legacy_state() -> None:
    job = VideoJob(
        job_id="job_legacy_error",
        tenant_id="tenant-a",
        filename="video.mp4",
        status="failed",
        error="secret-token leaked from old exception",
    )

    assert job.public_dict()["error"] == "视频任务失败"
    assert job.state_dict()["error"] == "视频任务失败"

    restored = VideoJob.from_state(
        job.state_dict() | {"error": "secret-token from old state"}
    )
    assert restored.error == "视频任务失败"
    assert restored.public_dict()["error"] == "视频任务失败"


def test_video_job_public_and_state_payloads_do_not_include_filename() -> None:
    job = VideoJob(
        job_id="job_filename_redaction",
        tenant_id="tenant-a",
        filename="secret-person-name.mp4",
        status="completed",
        result={
            "metadata": {"filename": "secret-person-name.mp4", "frame_count": 1},
            "frames": [],
            "frame_count": 0,
        },
    )

    public_payload = job.public_dict(include_result=True)
    state_payload = job.state_dict()
    restored = VideoJob.from_state(
        {
            "job_id": "job_legacy_filename",
            "tenant_id": "tenant-a",
            "filename": "legacy-secret-person-name.mp4",
            "result": {"metadata": {"filename": "legacy-secret-person-name.mp4"}},
        }
    )

    assert "filename" not in job.public_dict()
    assert "filename" not in public_payload["result"]["metadata"]
    assert "filename" not in state_payload
    assert "filename" not in state_payload["result"]["metadata"]
    assert "legacy-secret-person-name" not in json.dumps(
        restored.public_dict(include_result=True), ensure_ascii=False
    )
    assert "legacy-secret-person-name" not in json.dumps(
        restored.state_dict(), ensure_ascii=False
    )


async def fake_video_track_analysis(images, filenames, *args, **kwargs):
    frames = [
        {
            "frame_index": index,
            "width": image.width,
            "height": image.height,
            "persons": [{"embedding_dim": 64, "track_id": "track_0001"}],
            "person_count": 1,
        }
        for index, image in enumerate(images)
    ]
    timing = {
        "preprocess_seconds": 0.0,
        "queue_seconds": 0.0,
        "inference_seconds": 0.0,
        "postprocess_seconds": 0.0,
    }
    return {
        "detector_key": "portrait_hub/yolov8n.onnx",
        "reid_key": "portrait_hub/osnet_ibn_x1_0.onnx",
        "detector_load_seconds": 0.0,
        "reid_load_seconds": 0.0,
        "detector_meta": {"timing": timing},
        "embedding_meta": {"timing": timing},
        "frames": frames,
        "tracks": [{"track_id": "track_0001", "frame_count": len(frames)}],
        "tracker": {"algorithm": "test"},
        "person_count": len(frames),
        "track_count": 1 if frames else 0,
        "embedding_count": len(frames),
    }


def fake_batch_analysis(frames, embedding_count=0):
    timing = {
        "preprocess_seconds": 0.0,
        "queue_seconds": 0.0,
        "inference_seconds": 0.0,
        "postprocess_seconds": 0.0,
    }
    return {
        "detector_key": "portrait_hub/yolov8n.onnx",
        "reid_key": "portrait_hub/osnet_ibn_x1_0.onnx",
        "detector_load_seconds": 0.0,
        "reid_load_seconds": 0.0,
        "detector_meta": {"timing": timing},
        "embedding_meta": {"timing": timing},
        "frames": frames,
        "person_count": sum(frame.get("person_count", 0) for frame in frames),
        "embedding_count": embedding_count,
    }


@pytest.mark.asyncio
async def test_run_video_job_is_tenant_scoped(monkeypatch) -> None:
    VIDEO_JOBS.clear()
    VIDEO_JOBS[job_key("tenant-a", "job_same")] = VideoJob(job_id="job_same", tenant_id="tenant-a", filename="a.mp4")
    VIDEO_JOBS[job_key("tenant-b", "job_same")] = VideoJob(job_id="job_same", tenant_id="tenant-b", filename="b.mp4")
    fake_image = Image.new("RGB", (8, 8), (20, 40, 60))

    async def fake_iter_batches(source, sample_interval_seconds, batch_size):
        yield [fake_image], [0], [0.0], 25.0, 1

    async def fake_infer_detections(images, filenames, *args, frame_index_offset=0, **kwargs):
        frames = [{"frame_index": frame_index_offset, "person_count": 1, "persons": [
            {"score": 0.9, "box": [0, 0, 4, 4], "embedding_dim": 64, "embedding_index": 0, "_tracking_embedding": [0.1]*64}
        ]}]
        return fake_batch_analysis(frames, 1)

    monkeypatch.setattr("app.portrait_jobs.aiter_video_frame_batches", fake_iter_batches)
    monkeypatch.setattr("app.portrait_jobs.infer_detections_and_embeddings", fake_infer_detections)
    monkeypatch.setattr("app.portrait_jobs.assess_image_quality", lambda image: {"score": 0.9})
    monkeypatch.setattr("app.portrait_jobs.persist_video_job", lambda job, **kwargs: None)

    await run_video_job("job_same", "tenant-b", b"video", "b.mp4", 1.0, 1)

    assert VIDEO_JOBS[job_key("tenant-a", "job_same")].status == "queued"
    assert VIDEO_JOBS[job_key("tenant-b", "job_same")].status == "completed"
    frame = VIDEO_JOBS[job_key("tenant-b", "job_same")].result["frames"][0]
    assert frame["persons"][0]["embedding_dim"] == 64
    assert frame["thumbnail"].startswith("data:image/jpeg;base64,")
    assert "embedding" not in frame["persons"][0]
    assert VIDEO_JOBS[job_key("tenant-b", "job_same")].result["analysis_mode"] == "person_tracks"
    assert VIDEO_JOBS[job_key("tenant-b", "job_same")].result["track_count"] >= 0


@pytest.mark.asyncio
async def test_run_video_job_failure_error_is_redacted(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")
    VIDEO_JOBS.clear()
    job = VideoJob(
        job_id="job_failed", tenant_id="tenant-secret", filename="secret-video.mp4"
    )
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
    persisted = []

    async def fail_iter_batches(source, sample_interval_seconds, batch_size):
        raise RuntimeError("secret-token leaked through video decode for secret-video.mp4")
        yield

    monkeypatch.setattr("app.portrait_jobs.aiter_video_frame_batches", fail_iter_batches)
    monkeypatch.setattr(
        "app.portrait_jobs.persist_video_job",
        lambda persisted_job, **kwargs: persisted.append(persisted_job.state_dict()),
    )

    await run_video_job(job.job_id, job.tenant_id, b"video", "secret-video.mp4", 1.0, 1)

    assert job.status == "failed"
    assert job.error == "视频任务失败"
    assert persisted[-1]["status"] == "failed"
    assert persisted[-1]["error"] == "视频任务失败"
    assert "RuntimeError" in caplog.text
    for secret in ["secret-token", "secret-video", "tenant-secret", "job_failed"]:
        assert secret not in caplog.text
        assert secret not in str(job.public_dict()["error"])
        assert secret not in str(persisted[-1]["error"])


@pytest.mark.asyncio
async def test_run_video_job_retries_and_persists_progress(monkeypatch) -> None:
    VIDEO_JOBS.clear()
    job = VideoJob(
        job_id="job_retry", tenant_id="tenant-a", filename=None, max_retries=1
    )
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
    calls = {"count": 0}
    persisted = []

    fake_image = Image.new("RGB", (8, 8), (20, 40, 60))

    async def flaky_iter_batches(source, sample_interval_seconds, batch_size):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("临时解码失败")
        yield [fake_image], [0], [0.0], 25.0, 1

    async def fake_infer_detections(images, filenames, *args, frame_index_offset=0, **kwargs):
        return fake_batch_analysis(
            [{"frame_index": frame_index_offset, "person_count": 0, "persons": []}]
        )

    monkeypatch.setattr("app.portrait_jobs.VIDEO_JOB_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("app.portrait_jobs.aiter_video_frame_batches", flaky_iter_batches)
    monkeypatch.setattr("app.portrait_jobs.infer_detections_and_embeddings", fake_infer_detections)
    monkeypatch.setattr("app.portrait_jobs.assess_image_quality", lambda image: {"score": 0.9})
    monkeypatch.setattr(
        "app.portrait_jobs.persist_video_job",
        lambda persisted_job, **kwargs: persisted.append(persisted_job.state_dict()),
    )

    await run_video_job(job.job_id, job.tenant_id, b"video", "video.mp4", 1.0, 1)

    assert calls["count"] == 2
    assert job.status == "completed"
    assert job.attempts == 2
    assert any(
        item["status"] == "queued" and item["next_retry_at"] is not None
        for item in persisted
    )
    assert persisted[-1]["progress"] == 1.0


@pytest.mark.asyncio
async def test_run_video_job_progress_persistence_stays_lightweight(
    monkeypatch,
) -> None:
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_light_progress", tenant_id="tenant-a", filename=None)
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
    persisted = []
    public_snapshots = []

    images = [Image.new("RGB", (8, 8), (20, 40, 60)), Image.new("RGB", (8, 8), (30, 50, 70))]

    async def fake_iter_batches(source, sample_interval_seconds, batch_size):
        yield images, [0, 1], [0.0, 0.04], 25.0, 2

    async def fake_infer_detections(imgs, filenames, *args, frame_index_offset=0, **kwargs):
        frames = [
            {"frame_index": frame_index_offset + i, "person_count": 0, "persons": []}
            for i in range(len(imgs))
        ]
        return fake_batch_analysis(frames)

    def capture_persist(persisted_job, *, lightweight_result=False):
        public_snapshots.append(persisted_job.public_dict(include_result=True))
        persisted.append(persisted_job.state_dict(lightweight_result=lightweight_result))

    monkeypatch.setattr("app.portrait_jobs.VIDEO_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.portrait_jobs.aiter_video_frame_batches", fake_iter_batches)
    monkeypatch.setattr("app.portrait_jobs.infer_detections_and_embeddings", fake_infer_detections)
    monkeypatch.setattr("app.portrait_jobs.assess_image_quality", lambda image: {"score": 0.9})
    monkeypatch.setattr("app.portrait_jobs.persist_video_job", capture_persist)

    await run_video_job(job.job_id, job.tenant_id, b"video", "video.mp4", 1.0, 2)

    assert persisted[-1]["status"] == "completed"
    assert len(persisted[-1]["result"]["frames"]) == 2
    assert len(job.result["frames"]) == 2
    assert job.result["frames"][0]["thumbnail"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_run_video_job_consumes_batches_incrementally_and_associates_once(monkeypatch) -> None:
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_cross_batch", tenant_id="tenant-a", filename=None)
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
    first_inferred = asyncio.Event()
    association_calls = []

    async def batches(source, sample_interval_seconds, batch_size):
        yield [Image.new("RGB", (8, 8), "white")], [0], [0.0], 25.0, 50
        assert first_inferred.is_set()
        yield [Image.new("RGB", (8, 8), "white")], [25], [1.0], 25.0, 50

    async def infer(images, filenames, *args, frame_index_offset=0, embedding_index_offset=0, **kwargs):
        frame = {
            "frame_index": frame_index_offset,
            "person_count": 1,
            "persons": [{
                "score": 0.95,
                "box": [0, 0, 6, 6],
                "embedding_dim": 2,
                "embedding_index": embedding_index_offset,
                "_tracking_embedding": [1.0, 0.0],
            }],
        }
        first_inferred.set()
        return fake_batch_analysis([frame], 1)

    from app import portrait_tracking

    real_associate = portrait_tracking.associate_person_tracks

    def associate(frames, **kwargs):
        association_calls.append(len(frames))
        return real_associate(frames, **kwargs)

    monkeypatch.setattr("app.portrait_jobs.aiter_video_frame_batches", batches)
    monkeypatch.setattr("app.portrait_jobs.infer_detections_and_embeddings", infer)
    monkeypatch.setattr("app.portrait_jobs.associate_person_tracks", associate, raising=False)
    monkeypatch.setattr("app.portrait_jobs.persist_video_job", lambda job, **kwargs: None)
    monkeypatch.setattr("app.portrait_tracking.associate_person_tracks", associate)

    await run_video_job(job.job_id, job.tenant_id, b"video", "video.mp4", 1.0, 1)

    assert job.status == "completed"
    assert association_calls == [2]
    assert [frame["persons"][0]["embedding_index"] for frame in job.result["frames"]] == [0, 1]
    assert job.result["models"]["detector"] == "portrait_hub/yolov8n.onnx"
    assert "detector" in job.result["timing"]


