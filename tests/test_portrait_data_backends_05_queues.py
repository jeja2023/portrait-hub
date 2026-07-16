import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import (
    portrait_audit,
    portrait_video_job_worker,
)
from app.portrait_task_queue import (
    TASK_MESSAGES,
    LocalTaskQueue,
    QueueMessage,
    RedisTaskQueue,
)


def test_audit_chain_verifier_detects_tampering(workspace_tmp_path) -> None:
    audit_path = workspace_tmp_path / "audit.jsonl"
    first = {"event": "one", "audit_prev_hash": None}
    first_hash = portrait_audit.audit_payload_hash(first)
    first["audit_hash"] = first_hash
    second = {"event": "two", "audit_prev_hash": first_hash}
    second["audit_hash"] = portrait_audit.audit_payload_hash(second)
    audit_path.write_text(
        json.dumps(first) + "\n" + json.dumps(second | {"event": "tampered"}) + "\n",
        encoding="utf-8",
    )

    result = portrait_audit.verify_audit_chain(audit_path)

    assert result["ok"] is False
    assert result["record_count"] == 2
    assert result["error_count"] == 1
    assert result["errors"][0]["reason"] == "audit_hash_mismatch"


def test_local_task_queue_rolls_back_message_when_state_write_fails(
    monkeypatch, workspace_tmp_path
) -> None:
    TASK_MESSAGES.clear()
    monkeypatch.setattr(
        "app.portrait_task_queue.TASK_QUEUE_STATE_PATH",
        workspace_tmp_path / "queue.jsonl",
    )
    monkeypatch.setattr(
        "app.portrait_task_queue.TASK_QUEUE_DIR", workspace_tmp_path / "queue-spool"
    )

    def fail_append(path, payload, *, fail_closed=False):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr("app.portrait_task_queue.append_jsonl", fail_append)

    with pytest.raises(HTTPException):
        LocalTaskQueue().enqueue("video_jobs", {"job_id": "job_failed"})

    assert TASK_MESSAGES == []


def test_redis_task_queue_fails_closed_when_enqueue_fails(
    monkeypatch, workspace_tmp_path, caplog
) -> None:
    caplog.set_level("WARNING")
    TASK_MESSAGES.clear()
    monkeypatch.setattr(
        "app.portrait_task_queue.REDIS_URL", "redis://:secret-token@redis.internal/0"
    )
    monkeypatch.setattr(
        "app.portrait_task_queue.TASK_QUEUE_STATE_PATH",
        workspace_tmp_path / "queue.jsonl",
    )

    class FailingRedisClient:
        def xgroup_create(self, *args, **kwargs):
            return True

        def xadd(self, *args, **kwargs):
            raise RuntimeError("redis://:secret-token@redis.internal/0 unavailable")

    class FailingRedisModule:
        class Redis:
            @staticmethod
            def from_url(*args, **kwargs):
                return FailingRedisClient()

    monkeypatch.setattr("app.portrait_task_queue.redis", FailingRedisModule())

    with pytest.raises(RuntimeError):
        RedisTaskQueue().enqueue("video_jobs", {"job_id": "job_failed"})

    assert TASK_MESSAGES == []
    assert "RuntimeError" in caplog.text
    for secret in ["secret-token", "redis.internal"]:
        assert secret not in caplog.text


def test_local_task_queue_claim_ack_and_release_are_durable(
    monkeypatch, workspace_tmp_path
) -> None:
    TASK_MESSAGES.clear()
    queue_dir = workspace_tmp_path / "queue-spool"
    monkeypatch.setattr("app.portrait_task_queue.TASK_QUEUE_DIR", queue_dir)
    monkeypatch.setattr(
        "app.portrait_task_queue.TASK_QUEUE_STATE_PATH",
        workspace_tmp_path / "queue.jsonl",
    )

    producer = LocalTaskQueue()
    first = producer.enqueue(
        "video_jobs", {"job_id": "job_0123456789abcdef", "tenant_id": "tenant-a"}
    )
    claimed = LocalTaskQueue().claim("video_jobs", "worker-a")

    assert claimed is not None
    assert claimed.message_id == first.message_id
    assert claimed.payload["tenant_id"] == "tenant-a"
    lease_path = Path(str(claimed.receipt))
    os.utime(lease_path, (1, 1))
    LocalTaskQueue().heartbeat(claimed, "worker-a")
    assert lease_path.stat().st_mtime > 1
    LocalTaskQueue().release(claimed)

    reclaimed = LocalTaskQueue().claim("video_jobs", "worker-b")
    assert reclaimed is not None
    assert reclaimed.message_id == first.message_id
    LocalTaskQueue().ack(reclaimed)
    assert list(queue_dir.rglob("msg_*.json")) == []


def test_local_task_queue_claim_throttles_stale_requeue_checks(monkeypatch, workspace_tmp_path) -> None:
    queue_dir = workspace_tmp_path / "queue-spool"
    monkeypatch.setattr("app.portrait_task_queue.TASK_QUEUE_DIR", queue_dir)
    monkeypatch.setattr(
        "app.portrait_task_queue.TASK_QUEUE_STATE_PATH",
        workspace_tmp_path / "queue.jsonl",
    )
    monkeypatch.setattr("app.portrait_task_queue.TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS", 10.0)
    calls = 0
    ticks = iter([0.0, 0.0, 0.2, 1.1, 1.2, 1.8, 1.9])

    def fake_monotonic() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 2.0

    def fake_sleep(_seconds: float) -> None:
        return None

    def count_requeue(self, queue: str) -> None:
        nonlocal calls
        del self, queue
        calls += 1

    monkeypatch.setattr("app.portrait_task_queue.time.monotonic", fake_monotonic)
    monkeypatch.setattr("app.portrait_task_queue.time.sleep", fake_sleep)
    monkeypatch.setattr(LocalTaskQueue, "_requeue_stale", count_requeue)

    assert LocalTaskQueue().claim("video_jobs", "worker-a", block_seconds=1.5) is None
    assert calls == 1


@pytest.mark.asyncio
async def test_video_worker_acknowledges_invalid_queue_messages(monkeypatch) -> None:
    message = QueueMessage(
        message_id="msg_0123456789abcdef",
        queue="video_jobs",
        payload={"tenant_id": "tenant-a"},
    )

    class FakeQueue:
        acknowledged = False
        released = False

        def claim(self, queue: str, consumer_id: str, block_seconds: float):
            return message

        def ack(self, claimed: QueueMessage) -> None:
            assert claimed is message
            self.acknowledged = True

        def release(self, claimed: QueueMessage) -> None:
            self.released = True

    queue = FakeQueue()
    monkeypatch.setattr(portrait_video_job_worker, "TASK_QUEUE", queue)

    result = await portrait_video_job_worker.run_worker_once()

    assert result["status"] == "discarded"
    assert result["processed_count"] == 1
    assert queue.acknowledged is True
    assert queue.released is False


def test_local_task_queue_cancellation_marker_is_durable(
    monkeypatch, workspace_tmp_path
) -> None:
    queue_dir = workspace_tmp_path / "queue-spool"
    monkeypatch.setattr("app.portrait_task_queue.TASK_QUEUE_DIR", queue_dir)

    LocalTaskQueue().mark_cancelled("video_jobs", "tenant-a", "job_0123456789abcdef")

    assert (
        LocalTaskQueue().is_cancelled("video_jobs", "tenant-a", "job_0123456789abcdef")
        is True
    )
    assert (
        LocalTaskQueue().is_cancelled("video_jobs", "tenant-b", "job_0123456789abcdef")
        is False
    )
    LocalTaskQueue().clear_cancelled("video_jobs", "tenant-a", "job_0123456789abcdef")
    assert (
        LocalTaskQueue().is_cancelled("video_jobs", "tenant-a", "job_0123456789abcdef")
        is False
    )
