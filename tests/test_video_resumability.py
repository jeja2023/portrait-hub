import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import (
    portrait_audit,
    portrait_jobs,
    portrait_task_queue,
    portrait_video_uploads,
    routes_portrait_jobs,
    video_io,
)
from app.portrait_jobs import (
    VIDEO_JOBS,
    configure_video_job_task,
    create_video_job,
    request_pause_video_job,
    resume_video_job,
    run_video_job,
)
from app.portrait_task_queue import LocalTaskQueue, RedisTaskQueue
from app.server import app


class CapturingQueue:
    def __init__(self) -> None:
        self.messages = []

    def enqueue(self, queue, payload):
        message = portrait_task_queue.QueueMessage(
            message_id=f"msg_{len(self.messages) + 1:016x}",
            queue=queue,
            payload=dict(payload),
            priority=int(payload.get("priority", 0)),
        )
        self.messages.append(message)
        return message

    def remove(self, message):
        self.messages.remove(message)


@pytest.fixture
def video_client(workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, CapturingQueue]:
    monkeypatch.setattr(
        portrait_video_uploads,
        "VIDEO_UPLOAD_SESSION_STATE_PATH",
        workspace_tmp_path / "upload-sessions.json",
    )
    monkeypatch.setattr(
        portrait_video_uploads,
        "VIDEO_UPLOAD_PART_DIR",
        workspace_tmp_path / "upload-parts",
    )
    monkeypatch.setattr(video_io, "VIDEO_JOB_INPUT_DIR", workspace_tmp_path / "video-inputs")
    monkeypatch.setattr(
        portrait_jobs,
        "PORTRAIT_JOBS_STATE_PATH",
        workspace_tmp_path / "video-jobs.json",
    )
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", workspace_tmp_path / "audit.jsonl")
    queue = CapturingQueue()
    monkeypatch.setattr(routes_portrait_jobs, "TASK_QUEUE", queue)
    monkeypatch.setattr(routes_portrait_jobs, "audit_event", lambda *args, **kwargs: None)
    portrait_video_uploads.reset_video_upload_state()
    VIDEO_JOBS.clear()
    return TestClient(app, raise_server_exceptions=False), queue


def headers(tenant: str = "tenant-a") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Project-ID": "default"}


def video_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + b"portrait-hub-video-payload"


def test_chunked_upload_supports_out_of_order_replay_and_idempotent_complete(
    video_client: tuple[TestClient, CapturingQueue],
) -> None:
    client, queue = video_client
    content = video_bytes()
    created = client.post(
        "/v1/uploads/video",
        headers=headers(),
        json={
            "filename": "sample.mp4",
            "content_type": "video/mp4",
            "total_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    assert created.status_code == 200, created.text
    upload_id = created.json()["data"]["upload"]["upload_id"]
    split = 12
    parts = [(2, split, content[split:]), (1, 0, content[:split])]
    for part_number, offset, part in parts:
        response = client.put(
            f"/v1/uploads/video/{upload_id}/parts/{part_number}",
            headers={
                **headers(),
                "X-Chunk-Offset": str(offset),
                "X-Chunk-SHA256": hashlib.sha256(part).hexdigest(),
                "Content-Type": "application/octet-stream",
            },
            content=part,
        )
        assert response.status_code == 200, response.text

    replay = client.put(
        f"/v1/uploads/video/{upload_id}/parts/1",
        headers={
            **headers(),
            "X-Chunk-Offset": "0",
            "X-Chunk-SHA256": hashlib.sha256(content[:split]).hexdigest(),
        },
        content=content[:split],
    )
    assert replay.json()["data"]["upload"]["idempotent_replay"] is True

    completed = client.post(
        f"/v1/uploads/video/{upload_id}/complete",
        headers=headers(),
        json={"priority": 10},
    )
    assert completed.status_code == 200, completed.text
    data = completed.json()["data"]
    assert data["upload"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert data["job"]["priority"] == 10
    assert len(queue.messages) == 1
    assert queue.messages[0].priority == 10

    repeated = client.post(
        f"/v1/uploads/video/{upload_id}/complete",
        headers=headers(),
        json={"priority": 10},
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["idempotent_replay"] is True
    assert repeated.json()["data"]["job"]["job_id"] == data["job"]["job_id"]
    assert len(queue.messages) == 1


def test_chunked_upload_rejects_digest_overlap_and_cross_tenant_access(
    video_client: tuple[TestClient, CapturingQueue],
) -> None:
    client, _ = video_client
    content = video_bytes()
    upload = client.post(
        "/v1/uploads/video",
        headers=headers(),
        json={
            "filename": "sample.mp4",
            "total_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    ).json()["data"]["upload"]
    upload_id = upload["upload_id"]
    bad_digest = client.put(
        f"/v1/uploads/video/{upload_id}/parts/1",
        headers={**headers(), "X-Chunk-Offset": "0", "X-Chunk-SHA256": "0" * 64},
        content=content[:16],
    )
    assert bad_digest.status_code == 409
    first = content[:16]
    assert client.put(
        f"/v1/uploads/video/{upload_id}/parts/1",
        headers={
            **headers(),
            "X-Chunk-Offset": "0",
            "X-Chunk-SHA256": hashlib.sha256(first).hexdigest(),
        },
        content=first,
    ).status_code == 200
    overlap = content[8:20]
    conflict = client.put(
        f"/v1/uploads/video/{upload_id}/parts/2",
        headers={
            **headers(),
            "X-Chunk-Offset": "8",
            "X-Chunk-SHA256": hashlib.sha256(overlap).hexdigest(),
        },
        content=overlap,
    )
    assert conflict.status_code == 409
    assert client.get(f"/v1/uploads/video/{upload_id}", headers=headers("tenant-b")).status_code == 404


def test_pause_resume_preserves_checkpoint_payload_and_priority(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        portrait_jobs,
        "PORTRAIT_JOBS_STATE_PATH",
        workspace_tmp_path / "video-jobs.json",
    )
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_STORAGE_BACKEND", "json")
    VIDEO_JOBS.clear()
    job = create_video_job(None, tenant_id="tenant-a:default")
    configure_video_job_task(
        job,
        {"job_id": job.job_id, "tenant_id": job.tenant_id, "input_ref": "ref/video.mp4"},
        priority=5,
    )
    job.checkpoint = {"next_source_frame_index": 120, "processed_frames": 8}
    portrait_jobs.persist_video_job(job)

    paused = request_pause_video_job(job.job_id, job.tenant_id)
    assert paused is not None and paused.status == "paused"
    resumed = resume_video_job(job.job_id, job.tenant_id, priority=20)

    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.priority == 20
    assert resumed.task_payload["input_ref"] == "ref/video.mp4"
    assert resumed.checkpoint["next_source_frame_index"] == 120
    assert [item["event"] for item in resumed.timeline][-2:] == ["paused", "resumed"]


def test_local_queue_claims_higher_priority_before_fifo(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(portrait_task_queue, "TASK_QUEUE_DIR", workspace_tmp_path / "queue")
    monkeypatch.setattr(
        portrait_task_queue,
        "TASK_QUEUE_STATE_PATH",
        workspace_tmp_path / "queue-events.jsonl",
    )
    queue = LocalTaskQueue()
    low = queue.enqueue("video_jobs", {"job_id": "low", "priority": -5})
    high = queue.enqueue("video_jobs", {"job_id": "high", "priority": 50})

    claimed_high = queue.claim("video_jobs", "worker")
    assert claimed_high is not None and claimed_high.message_id == high.message_id
    queue.ack(claimed_high)
    claimed_low = queue.claim("video_jobs", "worker")
    assert claimed_low is not None and claimed_low.message_id == low.message_id


class FakeRedisStreams:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.claimed: set[tuple[str, str]] = set()
        self.counter = 0

    def xgroup_create(self, stream, group, id="0-0", mkstream=True):
        self.streams.setdefault(stream, [])

    def xadd(self, stream, fields):
        self.counter += 1
        entry_id = f"{self.counter}-0"
        self.streams.setdefault(stream, []).append((entry_id, fields))
        return entry_id

    def xautoclaim(self, *args, **kwargs):
        return ["0-0", []]

    def xreadgroup(self, *, streams, **kwargs):
        stream = next(iter(streams))
        for entry in self.streams.get(stream, []):
            identity = (stream, entry[0])
            if identity not in self.claimed:
                self.claimed.add(identity)
                return [(stream, [entry])]
        return []

    def xack(self, stream, group, entry_id):
        return 1

    def xdel(self, stream, entry_id):
        self.streams[stream] = [entry for entry in self.streams.get(stream, []) if entry[0] != entry_id]
        return 1


def test_redis_queue_claims_priority_streams_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedisStreams()
    queue = RedisTaskQueue()
    monkeypatch.setattr(queue, "_client", lambda: fake)
    monkeypatch.setattr(portrait_task_queue, "append_task_queue_state", lambda *args, **kwargs: None)

    low = queue.enqueue("video_jobs", {"job_id": "low", "priority": -10})
    normal = queue.enqueue("video_jobs", {"job_id": "normal", "priority": 0})
    high = queue.enqueue("video_jobs", {"job_id": "high", "priority": 10})

    claimed = [queue.claim("video_jobs", "worker") for _ in range(3)]
    assert [message.message_id for message in claimed if message is not None] == [
        high.message_id,
        normal.message_id,
        low.message_id,
    ]
    for message in claimed:
        assert message is not None
        queue.ack(message)


@pytest.mark.asyncio
async def test_video_job_resume_passes_checkpoint_to_decoder(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_JOBS_STATE_PATH", workspace_tmp_path / "jobs.json")
    monkeypatch.setattr(portrait_jobs, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_jobs, "persist_video_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(portrait_task_queue.TASK_QUEUE, "is_cancelled", lambda *args, **kwargs: False)
    captured: list[int] = []

    async def fake_batches(source, sample_interval_seconds, batch_size, *, start_frame_index=0):
        captured.append(start_frame_index)
        if False:
            yield source, sample_interval_seconds, batch_size

    monkeypatch.setattr(portrait_jobs, "aiter_video_frame_batches", fake_batches)
    VIDEO_JOBS.clear()
    job = create_video_job(None, tenant_id="tenant-a:default")
    job.max_retries = 0
    job.checkpoint = {"next_source_frame_index": 120, "completed_batches": 3}

    await run_video_job(job.job_id, job.tenant_id, b"video", "resume.mp4", 1.0, 2)

    assert captured == [120]
