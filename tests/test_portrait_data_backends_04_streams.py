import asyncio
import json

import pytest
from fastapi import HTTPException
from PIL import Image

from app import (
    portrait_crypto,
    portrait_stream_worker,
    portrait_stream_worker_daemon,
    portrait_streams,
)
from app.portrait_streams import (
    STREAMS,
    create_stream,
    start_stream,
    stop_stream,
    stream_key,
)
from tools import portrait_stream_worker_health


async def _single_stream_batch(source, sample_interval_seconds, batch_size, read_timeout_seconds=None):
    yield [Image.new("RGB", (32, 48), "white")], [7], [0.28], 25.0, 8


def test_stream_create_rolls_back_memory_when_persist_fails(monkeypatch) -> None:
    STREAMS.clear()

    def fail_persist(stream):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_streams, "persist_stream", fail_persist)

    with pytest.raises(HTTPException):
        create_stream("http://example.com/live", tenant_id="tenant-a")

    assert STREAMS == {}


def test_stream_start_and_stop_roll_back_memory_when_persist_fails(monkeypatch) -> None:
    STREAMS.clear()
    stream = create_stream("http://example.com/live", tenant_id="tenant-a")
    initial_event_count = len(stream.events)

    def fail_persist(updated_stream):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(portrait_streams, "ALLOW_STREAM_URLS", True)
    monkeypatch.setattr(portrait_streams, "persist_stream", fail_persist)

    with pytest.raises(HTTPException):
        start_stream(stream)

    stored = STREAMS[stream_key("tenant-a", stream.stream_id)]
    assert stored.status == "registered"
    assert len(stored.events) == initial_event_count

    with pytest.raises(HTTPException):
        stop_stream(stream)

    stored = STREAMS[stream_key("tenant-a", stream.stream_id)]
    assert stored.status == "registered"
    assert len(stored.events) == initial_event_count


def test_streams_json_state_round_trip(monkeypatch, workspace_tmp_path) -> None:
    state_path = workspace_tmp_path / "streams.json"
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STREAMS_STATE_PATH", state_path)
    STREAMS.clear()

    created = create_stream(
        "http://example.com/live", tenant_id="tenant-a", name="Lobby"
    )
    STREAMS.clear()
    portrait_streams.load_streams_state()

    stored = STREAMS[stream_key("tenant-a", created.stream_id)]
    assert stored.name == "Lobby"
    assert stored.stream_url == "http://example.com/live"


def test_streams_json_state_protects_stream_url(
    monkeypatch, workspace_tmp_path
) -> None:
    state_path = workspace_tmp_path / "streams.json"
    stream_url = "rtsp://user:secret@example.com/live?token=query-secret"
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STREAMS_STATE_PATH", state_path)
    STREAMS.clear()

    created = create_stream(stream_url, tenant_id="tenant-a", name="Lobby")
    raw_state = state_path.read_text(encoding="utf-8")

    assert "stream_url_protected" in raw_state
    assert "stream_url" not in json.loads(raw_state)["streams"][0]
    assert "secret" not in raw_state
    assert "query-secret" not in raw_state

    STREAMS.clear()
    portrait_streams.load_streams_state()

    stored = STREAMS[stream_key("tenant-a", created.stream_id)]
    assert stored.stream_url == stream_url


def test_stream_sensitive_settings_and_metadata_are_protected_at_rest(
    monkeypatch, workspace_tmp_path
) -> None:
    state_path = workspace_tmp_path / "streams-sensitive.json"
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STREAMS_STATE_PATH", state_path)
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY", "stream-state-secret")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEY_ID", "stream-v1")
    monkeypatch.setattr(portrait_crypto, "ENCRYPTION_KEYRING", "")
    monkeypatch.setattr(portrait_crypto, "REQUIRE_ENCRYPTION", True)
    STREAMS.clear()

    created = create_stream(
        "http://example.com/live",
        tenant_id="tenant-a",
        settings={"api_key": "stream-secret-key", "safe": "visible"},
        metadata={"nested": {"token": "stream-secret-token"}, "label": "Lobby"},
    )
    raw_state = state_path.read_text(encoding="utf-8")

    assert "stream-secret-key" not in raw_state
    assert "stream-secret-token" not in raw_state
    payload = json.loads(raw_state)
    stream_state = payload["streams"][0]
    assert stream_state["settings"]["api_key"]["__portrait_protected_value__"] is True
    assert (
        stream_state["metadata"]["nested"]["token"]["__portrait_protected_value__"]
        is True
    )
    assert stream_state["settings"]["safe"] == "visible"

    STREAMS.clear()
    portrait_streams.load_streams_state()

    stored = STREAMS[stream_key("tenant-a", created.stream_id)]
    assert stored.settings["api_key"] == "stream-secret-key"
    assert stored.settings["safe"] == "visible"
    assert stored.metadata["nested"]["token"] == "stream-secret-token"
    assert stored.metadata["label"] == "Lobby"
    assert stored.public_dict()["settings"]["api_key"] == "<redacted>"
    assert stored.public_dict()["metadata"]["nested"]["token"] == "<redacted>"


def test_stream_event_jsonl_payload_is_redacted(monkeypatch) -> None:
    STREAMS.clear()
    stream = create_stream("http://example.com/live", tenant_id="tenant-a")
    appended = []

    def capture_append(path, payload, *, fail_closed=False):
        appended.append(payload)

    monkeypatch.setattr(portrait_stream_worker, "append_jsonl", capture_append)
    monkeypatch.setattr(portrait_stream_worker, "persist_stream", lambda stream: None)

    portrait_stream_worker.emit_stream_event(
        stream,
        "debug",
        "sensitive payload",
        {"token": "secret-token", "access_key": "secret-key", "embedding": [1.0, 2.0]},
    )

    assert appended
    payload = appended[0]["payload"]
    assert payload["token"] == "<redacted>"
    assert payload["access_key"] == "<redacted>"
    assert payload["embedding"] == "<redacted>"


def test_stream_worker_session_tracks_heartbeat_and_backpressure(monkeypatch) -> None:
    STREAMS.clear()
    portrait_stream_worker.STREAM_WORKER_SESSIONS.clear()
    monkeypatch.setattr(
        portrait_stream_worker,
        "append_jsonl",
        lambda path, payload, fail_closed=False: None,
    )
    monkeypatch.setattr(portrait_stream_worker, "persist_stream", lambda stream: None)
    stream = create_stream("http://example.com/session", tenant_id="tenant-a")

    session = portrait_stream_worker.start_stream_worker_session(stream)
    heartbeat = portrait_stream_worker.heartbeat_stream_worker_session(
        stream, frame_buffer_depth=1, frames_sampled=2
    )
    dropped = portrait_stream_worker.record_stream_backpressure_drop(stream, count=3)
    status = portrait_stream_worker.stream_worker_status()

    assert session["status"] == "running"
    assert heartbeat["frames_sampled"] == 2
    assert dropped["backpressure_drops"] == 3
    assert status["active_sessions"] == 1
    assert status["daemon_entrypoint"] == "python -m app.portrait_stream_worker_daemon"


def test_stream_worker_health_detects_stale_sessions(monkeypatch) -> None:
    portrait_stream_worker.STREAM_WORKER_SESSIONS.clear()
    monkeypatch.setattr(
        portrait_stream_worker,
        "STREAM_WORKER_SESSIONS",
        {
            ("tenant-a", "stream-stale"): {
                "tenant_id": "tenant-a",
                "stream_id": "stream-stale",
                "status": "running",
                "last_heartbeat_at": 1.0,
            }
        },
    )

    report = portrait_stream_worker_health.evaluate_stream_worker_health(
        max_heartbeat_age_seconds=0.001
    )

    assert report["ok"] is False
    assert report["active_sessions"] == 1
    assert report["stale_session_count"] == 1


@pytest.mark.asyncio
async def test_stream_worker_daemon_once_runs_running_streams(monkeypatch) -> None:
    STREAMS.clear()
    monkeypatch.setattr(
        portrait_stream_worker_daemon, "load_streams_state", lambda: None
    )
    stream = create_stream("http://example.com/daemon", tenant_id="tenant-a")
    stream.status = "running"
    calls = []

    class FakeProcessLock:
        released = False

        def release(self):
            self.released = True

    process_lock = FakeProcessLock()

    async def fake_run_stream_worker_session(stream, *, max_reconnects=3):
        calls.append((stream.tenant_id, stream.stream_id, max_reconnects))
        return {
            "tenant_id": stream.tenant_id,
            "stream_id": stream.stream_id,
            "status": "running",
        }

    monkeypatch.setattr(
        portrait_stream_worker_daemon,
        "acquire_stream_process_lock",
        lambda stream, owner_id: process_lock,
    )
    monkeypatch.setattr(
        portrait_stream_worker_daemon,
        "run_stream_worker_session",
        fake_run_stream_worker_session,
    )

    report = await portrait_stream_worker_daemon.run_daemon_once(max_reconnects=2)

    assert report["status"] == "processed"
    assert report["selected_count"] == 1
    assert report["processed_count"] == 1
    assert calls == [("tenant-a", stream.stream_id, 2)]
    assert process_lock.released is True


@pytest.mark.asyncio
async def test_stream_worker_daemon_renews_lease_and_process_lock(monkeypatch) -> None:
    stream = create_stream("http://example.com/lease-heartbeat", tenant_id="tenant-a")
    stream.status = "running"
    lease_renewals = []

    class FakeProcessLock:
        heartbeat_count = 0

        def heartbeat(self):
            self.heartbeat_count += 1
            return True

        def release(self):
            pass

    process_lock = FakeProcessLock()

    async def long_session(stream, *, max_reconnects=3):
        await asyncio.sleep(0.08)
        return {"status": "running"}

    monkeypatch.setattr(portrait_stream_worker_daemon, "STREAM_WORKER_LEASE_TTL_SECONDS", 0.03)
    monkeypatch.setattr(portrait_stream_worker_daemon, "run_stream_worker_session", long_session)
    monkeypatch.setattr(
        portrait_stream_worker_daemon,
        "renew_stream_worker_lease",
        lambda stream, owner_id, ttl: lease_renewals.append((owner_id, ttl)) or True,
    )
    monkeypatch.setattr(portrait_stream_worker_daemon, "release_stream_worker_lease", lambda *args: True)

    result = await portrait_stream_worker_daemon.run_leased_stream_worker_session(
        stream,
        owner_id="owner-a",
        max_reconnects=0,
        process_lock=process_lock,
    )

    assert result["status"] == "running"
    assert lease_renewals
    assert process_lock.heartbeat_count >= 1


@pytest.mark.asyncio
async def test_stream_worker_daemon_process_lock_skips_duplicate_process(
    monkeypatch,
) -> None:
    STREAMS.clear()
    monkeypatch.setattr(
        portrait_stream_worker_daemon, "load_streams_state", lambda: None
    )
    stream = create_stream("http://example.com/daemon-lock", tenant_id="tenant-a")
    stream.status = "running"
    calls = []

    def fake_acquire_process_lock(stream, owner_id):
        calls.append((stream.tenant_id, stream.stream_id, owner_id))
        return None

    def fail_acquire_lease(*args, **kwargs):
        raise AssertionError(
            "state lease should not be attempted without the process lock"
        )

    monkeypatch.setattr(
        portrait_stream_worker_daemon,
        "acquire_stream_process_lock",
        fake_acquire_process_lock,
    )
    monkeypatch.setattr(
        portrait_stream_worker_daemon, "acquire_stream_worker_lease", fail_acquire_lease
    )

    report = await portrait_stream_worker_daemon.run_daemon_once(max_reconnects=1)

    assert report["status"] == "idle"
    assert report["selected_count"] == 1
    assert report["processed_count"] == 0
    assert calls == [
        (
            "tenant-a",
            stream.stream_id,
            portrait_stream_worker_daemon.STREAM_WORKER_OWNER_ID,
        )
    ]


def test_stream_worker_process_lock_retries_after_stale_lock_cleanup(
    monkeypatch, workspace_tmp_path
) -> None:
    STREAMS.clear()
    lock_dir = workspace_tmp_path / "stream-locks"
    monkeypatch.setattr(
        portrait_stream_worker_daemon, "STREAM_WORKER_LOCK_DIR", lock_dir
    )
    stream = create_stream("http://example.com/daemon-stale-lock", tenant_id="tenant-a")
    attempts = []
    stale_checks = []

    def fake_create_lock_file(lock, owner_id):
        attempts.append((lock.path, owner_id, lock.token))
        if len(attempts) == 1:
            raise FileExistsError(lock.path)

    def fake_remove_stale_lock(path):
        stale_checks.append(path)
        return True

    monkeypatch.setattr(
        portrait_stream_worker_daemon,
        "create_stream_process_lock_file",
        fake_create_lock_file,
    )
    monkeypatch.setattr(
        portrait_stream_worker_daemon,
        "remove_stale_stream_process_lock",
        fake_remove_stale_lock,
    )

    lock = portrait_stream_worker_daemon.acquire_stream_process_lock(stream, "owner-a")

    assert lock is not None
    assert lock.path == portrait_stream_worker_daemon.stream_process_lock_path(stream)
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0] == lock.path
    assert stale_checks == [lock.path]
    assert lock.token == attempts[1][2]


def test_stream_worker_process_lock_removes_stale_malformed_file(
    monkeypatch, workspace_tmp_path
) -> None:
    lock_path = workspace_tmp_path / "stream-locks" / "bad.lock"
    removed = []

    monkeypatch.setattr(type(lock_path), "exists", lambda self: True)
    monkeypatch.setattr(
        type(lock_path), "unlink", lambda self, missing_ok=False: removed.append(self)
    )
    monkeypatch.setattr(
        portrait_stream_worker_daemon,
        "read_stream_process_lock_payload",
        lambda path: None,
    )
    monkeypatch.setattr(
        portrait_stream_worker_daemon,
        "stream_process_lock_created_at",
        lambda path, payload: 1.0,
    )
    monkeypatch.setattr(portrait_stream_worker_daemon, "wall_time", lambda: 100.0)
    monkeypatch.setattr(
        portrait_stream_worker_daemon, "STREAM_WORKER_PROCESS_LOCK_STALE_SECONDS", 1.0
    )

    assert (
        portrait_stream_worker_daemon.remove_stale_stream_process_lock(lock_path)
        is True
    )
    assert removed == [lock_path]


@pytest.mark.asyncio
async def test_stream_worker_runs_analysis_and_emits_result(monkeypatch) -> None:
    STREAMS.clear()
    portrait_stream_worker.STREAM_WORKER_SESSIONS.clear()
    stream = create_stream(
        "http://example.com/analysis",
        tenant_id="tenant-a",
        settings={"sample_interval_seconds": 1.0, "batch_size": 2},
    )
    stream.status = "running"
    events = []
    monkeypatch.setattr(
        portrait_stream_worker, "validate_media_stream_url", lambda url: None
    )
    monkeypatch.setattr(
        portrait_stream_worker,
        "append_jsonl",
        lambda path, payload, fail_closed=False: None,
    )
    monkeypatch.setattr(portrait_stream_worker, "persist_stream", lambda stream: None)
    monkeypatch.setattr(
        portrait_stream_worker,
        "aiter_video_frame_batches",
        _single_stream_batch,
    )

    async def fake_infer(*args, **kwargs):
        return {
            "detector_key": "portrait_hub/yolov8n.onnx",
            "reid_key": "portrait_hub/osnet_ibn_x1_0.onnx",
            "frames": [{"frame_index": 0, "person_count": 1, "persons": []}],
            "tracks": [{"track_id": "track-1"}],
            "tracker": {"algorithm": "test"},
            "person_count": 1,
            "track_count": 1,
            "embedding_count": 0,
        }

    monkeypatch.setattr(portrait_stream_worker, "infer_tracks_for_images", fake_infer)
    monkeypatch.setattr(
        portrait_stream_worker,
        "observe_video_sampling_metrics",
        lambda metadata: None,
    )

    def capture_emit(stream, event_type, message, payload=None):
        events.append((event_type, payload or {}))
        stream.add_event(event_type, message, payload or {})

    monkeypatch.setattr(portrait_stream_worker, "emit_stream_event", capture_emit)

    report = await portrait_stream_worker.run_stream_worker_session(
        stream, max_reconnects=0
    )

    assert report["frames_processed"] == 1
    assert report["last_person_count"] == 1
    analysis = next(
        payload
        for event_type, payload in events
        if event_type == "stream_analysis_completed"
    )
    assert analysis["frame_count"] == 1
    assert analysis["person_count"] == 1
    assert analysis["track_count"] == 1
    assert analysis["frames"][0]["source_frame_index"] == 7
    assert analysis["frames"][0]["source_seconds"] == 0.28
    assert analysis["frames"][0]["thumbnail"].startswith("data:image/jpeg;base64,")
    assert analysis["frames"][0]["quality"]["score"] >= 0


async def test_stream_worker_revalidates_url_before_pull(monkeypatch) -> None:
    STREAMS.clear()
    stream = create_stream("http://example.com/rebind", tenant_id="tenant-a")
    stream.status = "running"
    calls = []

    def fail_validation(stream_url):
        calls.append(stream_url)
        raise HTTPException(
            status_code=400, detail="stream_url 主机被 SSRF 防护策略拒绝"
        )

    async def fail_if_pulled(*args, **kwargs):
        raise AssertionError("stream pull should not run after validation failure")
        yield

    monkeypatch.setattr(
        portrait_stream_worker, "validate_media_stream_url", fail_validation
    )
    monkeypatch.setattr(
        portrait_stream_worker, "aiter_video_frame_batches", fail_if_pulled
    )

    report = await portrait_stream_worker.run_stream_worker_session(
        stream, max_reconnects=0
    )

    assert calls == ["http://example.com/rebind"]
    assert report["status"] in {"failed", "reconnecting"}


