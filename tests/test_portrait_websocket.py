from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import portrait_streams, routes_portrait_ws
from app.portrait_jobs import VIDEO_JOBS, VideoJob, job_key
from app.portrait_streams import STREAMS, create_stream, stream_key
from main import app


def test_job_websocket_sends_snapshot_without_auth_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_portrait_ws, "RBAC_ENABLED", False)
    monkeypatch.setattr(routes_portrait_ws, "AUTH_REQUIRED", False)
    monkeypatch.setattr(routes_portrait_ws, "API_TOKEN", None)
    client = TestClient(app)
    VIDEO_JOBS.clear()

    with client.websocket_connect("/ws/jobs/job_missing?tenant_id=default") as websocket:
        payload = websocket.receive_json()

    assert payload == {"status": "not_found", "job_id": "job_missing", "tenant_id": "default"}



def test_job_websocket_includes_completed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_portrait_ws, "RBAC_ENABLED", False)
    monkeypatch.setattr(routes_portrait_ws, "AUTH_REQUIRED", False)
    monkeypatch.setattr(routes_portrait_ws, "API_TOKEN", None)
    client = TestClient(app)
    VIDEO_JOBS.clear()
    job = VideoJob(job_id="job_done", tenant_id="default", filename="video.mp4", status="completed")
    job.result = {
        "metadata": {"filename": "video.mp4"},
        "frame_count": 1,
        "frames": [
            {
                "frame_index": 0,
                "source_frame_index": 0,
                "width": 64,
                "height": 64,
                "thumbnail": "data:image/jpeg;base64,abcd",
            }
        ],
    }
    VIDEO_JOBS[job_key("default", job.job_id)] = job

    with client.websocket_connect("/ws/jobs/job_done?tenant_id=default") as websocket:
        payload = websocket.receive_json()

    assert payload["job"]["job_id"] == "job_done"
    assert payload["job"]["result"]["frames"][0]["thumbnail"].startswith("data:image/jpeg;base64,")


def test_job_websocket_requires_api_token_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_portrait_ws, "RBAC_ENABLED", False)
    monkeypatch.setattr(routes_portrait_ws, "AUTH_REQUIRED", True)
    monkeypatch.setattr(routes_portrait_ws, "API_TOKEN", "secret-token")
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/jobs/job_missing?tenant_id=default"):
            pass

    assert exc_info.value.code == 1008

    # 主凭证禁止经 query 参数传递（方案 §8.4），只接受请求头
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/jobs/job_missing?tenant_id=default&token=secret-token"):
            pass

    assert exc_info.value.code == 1008

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/jobs/job_missing?tenant_id=default&access_token=secret-token"):
            pass

    assert exc_info.value.code == 1008

    with client.websocket_connect(
        "/ws/jobs/job_missing?tenant_id=default",
        headers={"x-api-key": "secret-token"},
    ) as websocket:
        payload = websocket.receive_json()

    assert payload["status"] == "not_found"


def test_stream_websocket_refreshes_worker_events_from_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
    workspace_tmp_path,
) -> None:
    monkeypatch.setattr(routes_portrait_ws, "RBAC_ENABLED", False)
    monkeypatch.setattr(routes_portrait_ws, "AUTH_REQUIRED", False)
    monkeypatch.setattr(routes_portrait_ws, "API_TOKEN", None)
    monkeypatch.setattr(portrait_streams, "PORTRAIT_STORAGE_BACKEND", "json")
    monkeypatch.setattr(
        portrait_streams,
        "PORTRAIT_STREAMS_STATE_PATH",
        workspace_tmp_path / "streams-websocket.json",
    )
    snapshot = dict(STREAMS)
    STREAMS.clear()

    try:
        stream = create_stream("http://example.com/live", tenant_id="default")
        stale_api_snapshot = deepcopy(stream)
        worker_snapshot = deepcopy(stream)
        worker_snapshot.add_event(
            "stream_analysis_completed",
            "stream analysis completed",
            {
                "frame_count": 1,
                "person_count": 1,
                "track_count": 1,
                "frames": [{"thumbnail": "data:image/jpeg;base64,abcd"}],
            },
        )
        portrait_streams.persist_stream(worker_snapshot)
        STREAMS[stream_key("default", stream.stream_id)] = stale_api_snapshot

        client = TestClient(app)
        with client.websocket_connect(f"/ws/streams/{stream.stream_id}?tenant_id=default") as websocket:
            payload = websocket.receive_json()

        analysis_events = [event for event in payload["events"] if event["type"] == "stream_analysis_completed"]
        assert analysis_events
        assert analysis_events[-1]["payload"]["frame_count"] == 1
        assert "thumbnail" not in analysis_events[-1]["payload"]["frames"][0]
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)
