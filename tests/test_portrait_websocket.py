from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import routes_portrait_ws
from app.portrait_jobs import VIDEO_JOBS
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


def test_job_websocket_requires_api_token_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_portrait_ws, "RBAC_ENABLED", False)
    monkeypatch.setattr(routes_portrait_ws, "AUTH_REQUIRED", True)
    monkeypatch.setattr(routes_portrait_ws, "API_TOKEN", "secret-token")
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/jobs/job_missing?tenant_id=default"):
            pass

    assert exc_info.value.code == 1008

    with client.websocket_connect("/ws/jobs/job_missing?tenant_id=default&token=secret-token") as websocket:
        payload = websocket.receive_json()

    assert payload["status"] == "not_found"
