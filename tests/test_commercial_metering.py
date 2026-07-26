from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import portrait_audit, portrait_commercial, portrait_metering
from app.server import app


@pytest.fixture
def metering_client(workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(
        portrait_metering,
        "PORTRAIT_METERING_STATE_PATH",
        workspace_tmp_path / "metering.json",
    )
    monkeypatch.setattr(
        portrait_commercial,
        "PORTRAIT_COMMERCIAL_STATE_PATH",
        workspace_tmp_path / "commercial.json",
    )
    monkeypatch.setattr(portrait_audit, "PORTRAIT_AUDIT_PATH", workspace_tmp_path / "audit.jsonl")
    portrait_metering.reset_metering_state()
    portrait_commercial.reset_commercial_state()
    client = TestClient(app)
    yield client
    portrait_metering.reset_metering_state()
    portrait_commercial.reset_commercial_state()


def headers(tenant_id: str = "tenant-a") -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id, "X-Project-ID": "default"}


def data(response: Any) -> dict[str, Any]:
    assert response.status_code < 400, response.text
    return response.json()["data"]


def cost_model_payload(version: str = "2026-07") -> dict[str, Any]:
    return {
        "version": version,
        "currency": "CNY",
        "effective_at": 1_700_000_000,
        "request_unit_cost": 0.1,
        "image_unit_cost": 0.2,
        "video_second_cost": 0.01,
        "gpu_second_cost": 0.5,
        "network_gb_cost": 1.0,
        "third_party_unit_cost": 0.3,
        "reason": "approved delivery cost assumptions",
        "approved_by": "finance-owner",
    }


def test_usage_events_are_idempotent_versioned_costed_and_reversed(metering_client: TestClient) -> None:
    cost_model = data(
        metering_client.post("/v1/access/cost-models", headers=headers(), json=cost_model_payload())
    )["cost_model"]
    assert len(cost_model["model_sha256"]) == 64

    event_payload = {
        "idempotency_key": "job-42-attempt-1",
        "request_id": "request-42",
        "application_id": "app-1",
        "capability": "video_analysis",
        "model_version": "model-v3",
        "endpoint": "POST /v1/portrait/videos",
        "resource_type": "video",
        "outcome_category": "success",
        "delivery_kind": "original",
        "event_time": 1_700_000_100,
        "request_count": 1,
        "image_count": 1,
        "video_seconds": 60,
        "gpu_seconds": 2,
        "network_egress_bytes": 1024**3,
        "third_party_units": 2,
        "reason": "worker completion",
    }
    first = portrait_metering.record_usage_event("tenant-a", "default", event_payload, actor="worker-1")
    replay = portrait_metering.record_usage_event("tenant-a", "default", event_payload, actor="worker-1")
    assert replay["usage_event_id"] == first["usage_event_id"]

    summary = data(metering_client.get("/v1/access/usage/summary", headers=headers()))["usage_summary"]
    assert summary["event_count"] == 1
    assert summary["quantities"]["video_seconds"] == 60
    assert summary["outcomes"] == {"success": 1.0}
    assert summary["cost"]["amount"] == pytest.approx(3.5)
    assert summary["cost"]["status"] == "priced"

    reversal = data(
        metering_client.post(
            f"/v1/access/usage/events/{first['usage_event_id']}/reversal",
            headers=headers(),
            json={"reason": "duplicate upstream settlement event"},
        )
    )["usage_reversal"]
    assert reversal["reverses_event_id"] == first["usage_event_id"]
    assert reversal["event_sha256"] != first["event_sha256"]

    reversed_summary = data(metering_client.get("/v1/access/usage/summary", headers=headers()))[
        "usage_summary"
    ]
    assert reversed_summary["event_count"] == 2
    assert reversed_summary["request_count"] == 0
    assert reversed_summary["cost"]["amount"] == 0
    events = data(metering_client.get("/v1/access/usage/events", headers=headers()))
    assert events["total"] == 2


def test_metering_handles_late_events_timezone_months_and_delivery_categories(
    metering_client: TestClient,
) -> None:
    portrait_metering.create_cost_model(
        "tenant-a",
        "default",
        cost_model_payload(),
        actor="finance-owner",
    )
    base = {
        "application_id": "app-1",
        "capability": "face_detection",
        "model_version": "face-v2",
        "endpoint": "POST /v1/portrait/analyze",
        "resource_type": "image",
        "request_count": 1,
        "image_count": 1,
    }
    later_received = portrait_metering.record_usage_event(
        "tenant-a",
        "default",
        {
            **base,
            "idempotency_key": "late-event",
            "event_time": 1_706_719_800,
            "outcome_category": "business_rejection",
            "delivery_kind": "retry",
        },
        actor="delayed-worker",
    )
    portrait_metering.record_usage_event(
        "tenant-a",
        "default",
        {
            **base,
            "idempotency_key": "newer-event",
            "event_time": 1_706_806_200,
            "outcome_category": "system_failure",
            "delivery_kind": "duplicate",
        },
        actor="api",
    )
    assert later_received["event_time"] < 1_706_806_200

    daily = data(
        metering_client.get(
            "/v1/access/usage/timeseries",
            headers=headers(),
            params={"timezone": "Asia/Shanghai", "granularity": "day"},
        )
    )
    monthly = data(
        metering_client.get(
            "/v1/access/usage/timeseries",
            headers=headers(),
            params={"timezone": "Asia/Shanghai", "granularity": "month"},
        )
    )
    assert len(daily["timeseries"]) == 2
    assert [item["period"] for item in monthly["timeseries"]] == ["2024-02"]

    summary = data(metering_client.get("/v1/access/usage/summary", headers=headers()))["usage_summary"]
    assert summary["outcomes"] == {"business_rejection": 1.0, "system_failure": 1.0}
    assert summary["delivery_kinds"] == {"duplicate": 1.0, "retry": 1.0}
    assert summary["by_capability"][0]["capability"] == "face_detection"


def test_cost_models_and_usage_events_are_project_scoped_and_immutable(metering_client: TestClient) -> None:
    created = metering_client.post("/v1/access/cost-models", headers=headers(), json=cost_model_payload())
    duplicate = metering_client.post("/v1/access/cost-models", headers=headers(), json=cost_model_payload())
    assert created.status_code == 200
    assert duplicate.status_code == 409
    assert data(metering_client.get("/v1/access/cost-models", headers=headers("tenant-b")))["count"] == 0

    portrait_metering.record_usage_event(
        "tenant-a",
        "default",
        {
            "idempotency_key": "isolated-event",
            "event_time": 1_700_000_100,
            "request_count": 1,
            "outcome_category": "success",
            "delivery_kind": "original",
        },
        actor="runtime",
    )
    assert data(metering_client.get("/v1/access/usage/events", headers=headers()))["total"] == 1
    assert data(metering_client.get("/v1/access/usage/events", headers=headers("tenant-b")))["total"] == 0
