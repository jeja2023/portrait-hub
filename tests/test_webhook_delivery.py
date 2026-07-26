from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from app import portrait_access, portrait_webhook_delivery, routes_portrait_access
from main import app
from sdk.python.portrait_hub_client import PortraitHubClient


class _Response:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self, limit: int = -1) -> bytes:
        return b""


def _create_webhook() -> str:
    portrait_access.create_application(
        "tenant-a",
        app_id="app-a",
        name="App A",
        owner="integration",
        status_value="active",
        scopes=["jobs"],
    )
    _, secret = portrait_access.create_webhook(
        "tenant-a",
        webhook_id="jobs-primary",
        name="Jobs",
        application_id="app-a",
        url="https://hooks.example.test/jobs",
        status_value="active",
        events=["job.completed"],
        retry_limit=2,
        timeout_seconds=5,
    )
    return secret


@pytest.fixture(autouse=True)
def isolated_delivery_state(monkeypatch: pytest.MonkeyPatch, workspace_tmp_path: Path):
    portrait_access.clear_access_state()
    portrait_webhook_delivery.reset_webhook_delivery_state()
    monkeypatch.setattr(portrait_access, "save_access_state", lambda: None)
    monkeypatch.setattr(
        portrait_access,
        "validate_webhook_url",
        lambda value, *, required: str(value or "") if value or not required else "",
    )
    monkeypatch.setattr(
        portrait_webhook_delivery,
        "validate_webhook_url",
        lambda value, *, required: str(value or "") if value or not required else "",
    )
    monkeypatch.setattr(
        portrait_webhook_delivery,
        "WEBHOOK_DELIVERY_STATE_PATH",
        workspace_tmp_path / "webhook-deliveries.json",
    )
    monkeypatch.setattr(routes_portrait_access, "audit_event", lambda *args, **kwargs: None)
    yield
    portrait_access.clear_access_state()
    portrait_webhook_delivery.reset_webhook_delivery_state()


def test_webhook_delivery_retries_with_stable_id_and_verifiable_signature(monkeypatch) -> None:
    secret = _create_webhook()
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 503, "unavailable", {}, BytesIO(b"retry"))
        return _Response()

    monkeypatch.setattr(portrait_webhook_delivery.urllib_request, "urlopen", fake_urlopen)
    sleeps: list[float] = []

    def capture_retry(delay: float) -> None:
        sleeps.append(delay)
        scheduled = portrait_webhook_delivery.list_webhook_deliveries("tenant-a", "default")[0]
        assert scheduled["status"] == "retrying"
        assert scheduled["next_retry_at"] is not None

    result = portrait_webhook_delivery.deliver_webhook_event(
        tenant_id="tenant-a",
        project_id="default",
        webhook_id="jobs-primary",
        event="job.completed",
        resource_id="job_123",
        request_id="req_123",
        data={"resource_id": "job_123", "status": "completed"},
        sleep=capture_retry,
    )

    assert result["status"] == "delivered"
    assert result["attempt_count"] == 2
    assert result["signature_status"] == "self_verified"
    assert result["next_retry_at"] is None
    assert all(attempt["signature_status"] == "self_verified" for attempt in result["attempts"])
    assert sleeps == [0.25]
    first_headers = {key.lower(): value for key, value in calls[0].header_items()}
    second_headers = {key.lower(): value for key, value in calls[1].header_items()}
    assert first_headers["idempotency-key"] == second_headers["idempotency-key"] == result["delivery_id"]
    assert PortraitHubClient.verify_webhook_signature(
        calls[1].data,
        second_headers["x-portraithub-signature"],
        secret,
        timestamp=second_headers["x-portraithub-timestamp"],
        now=int(second_headers["x-portraithub-timestamp"]),
    )

    replay = portrait_webhook_delivery.deliver_webhook_event(
        tenant_id="tenant-a",
        project_id="default",
        webhook_id="jobs-primary",
        event="job.completed",
        resource_id="job_123",
        request_id="req_123",
        data={"resource_id": "job_123", "status": "completed"},
        sleep=sleeps.append,
    )
    assert replay["idempotent_replay"] is True
    assert len(calls) == 2


def test_webhook_delivery_records_terminal_failure(monkeypatch) -> None:
    _create_webhook()

    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 400, "bad request", {}, BytesIO(b"bad"))

    monkeypatch.setattr(portrait_webhook_delivery.urllib_request, "urlopen", fake_urlopen)
    result = portrait_webhook_delivery.deliver_webhook_event(
        tenant_id="tenant-a",
        project_id="default",
        webhook_id="jobs-primary",
        event="job.completed",
        resource_id="job_failed",
        request_id="req_failed",
        data={"resource_id": "job_failed", "status": "failed"},
        sleep=lambda _: None,
    )

    assert result["status"] == "dead_letter"
    assert result["attempt_count"] == 3
    assert result["dead_letter"] is True
    assert result["dead_letter_reason"] == "retry_exhausted"
    assert result["dead_lettered_at"] is not None
    rows = portrait_webhook_delivery.list_webhook_deliveries("tenant-a", "default", status="dead_letter")
    assert [row["delivery_id"] for row in rows] == [result["delivery_id"]]


def test_webhook_delivery_manual_retry_preserves_identity_and_project_isolation(monkeypatch) -> None:
    _create_webhook()

    def always_fail(request, timeout):
        raise HTTPError(request.full_url, 503, "unavailable", {}, BytesIO(b"retry later"))

    monkeypatch.setattr(portrait_webhook_delivery.urllib_request, "urlopen", always_fail)
    failed = portrait_webhook_delivery.deliver_webhook_event(
        tenant_id="tenant-a",
        project_id="default",
        webhook_id="jobs-primary",
        event="job.completed",
        resource_id="job_manual_retry",
        request_id="req_original",
        data={"resource_id": "job_manual_retry", "status": "failed"},
        sleep=lambda _: None,
    )
    assert failed["status"] == "dead_letter"

    with pytest.raises(portrait_webhook_delivery.WebhookDeliveryNotFoundError):
        portrait_webhook_delivery.retry_webhook_delivery(
            "tenant-a", "another-project", failed["delivery_id"], sleep=lambda _: None
        )

    monkeypatch.setattr(portrait_webhook_delivery.urllib_request, "urlopen", lambda request, timeout: _Response())
    audits: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        routes_portrait_access,
        "audit_event",
        lambda *args, **kwargs: audits.append((args, kwargs)),
    )
    client = TestClient(app)
    response = client.post(
        f"/v1/access/webhook-deliveries/{failed['delivery_id']}/retry",
        headers={"X-Tenant-ID": "tenant-a", "X-Project-ID": "default"},
    )

    assert response.status_code == 200, response.text
    delivery = response.json()["data"]["delivery"]
    assert delivery["delivery_id"] == failed["delivery_id"]
    assert delivery["request_id"] == "req_original"
    assert delivery["status"] == "delivered"
    assert delivery["attempt_count"] == 4
    assert delivery["attempts"][-1]["trigger"] == "manual_retry"
    assert delivery["manual_retry_count"] == 1
    assert "_event_data" not in response.text
    assert audits[0][0] == ("access_webhook_delivery_retry_requested",)
    assert audits[0][1]["delivery_id"] == failed["delivery_id"]
    assert audits[0][1]["original_request_id"] == "req_original"

    repeated = client.post(
        f"/v1/access/webhook-deliveries/{failed['delivery_id']}/retry",
        headers={"X-Tenant-ID": "tenant-a", "X-Project-ID": "default"},
    )
    assert repeated.status_code == 409

    hidden = client.post(
        f"/v1/access/webhook-deliveries/{failed['delivery_id']}/retry",
        headers={"X-Tenant-ID": "tenant-a", "X-Project-ID": "another-project"},
    )
    assert hidden.status_code == 403
