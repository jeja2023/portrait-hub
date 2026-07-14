from collections.abc import Iterator

from fastapi.testclient import TestClient
import pytest

from app import portrait_access
from app.portrait_call_logs import clear_call_logs, list_call_logs, record_call_log
from main import app


@pytest.fixture(autouse=True)
def isolated_call_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    clear_call_logs()
    portrait_access.clear_access_state()
    monkeypatch.setattr(portrait_access, "save_access_state", lambda: None)
    yield
    clear_call_logs()
    portrait_access.clear_access_state()


def test_call_logs_route_filters_by_tenant_request_and_status() -> None:
    record_call_log(
        request_id="req-a",
        tenant_id="tenant-a",
        application_id="demo-app",
        method="GET",
        path="/v1/models",
        status_code=200,
        latency_ms=12,
        created_at=100.0,
    )
    record_call_log(
        request_id="req-b",
        tenant_id="tenant-b",
        application_id="other-app",
        method="POST",
        path="/v1/gallery/search",
        status_code=500,
        latency_ms=34,
        created_at=101.0,
    )
    client = TestClient(app)

    response = client.get("/v1/access/call-logs?request_id=req-a&status=success", headers={"X-Tenant-ID": "tenant-a"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] == 1
    row = data["logs"][0]
    assert row["request_id"] == "req-a"
    assert row["tenant_id"] == "tenant-a"
    assert row["application_id"] == "demo-app"
    assert row["path"] == "/v1/models"




def test_call_logs_filter_by_error_code_and_created_window() -> None:
    record_call_log(
        request_id="req-old",
        tenant_id="tenant-a",
        application_id="demo-app",
        method="GET",
        path="/v1/models",
        status_code=503,
        latency_ms=20,
        created_at=100.0,
        error_code="storage_error",
    )
    record_call_log(
        request_id="req-match",
        tenant_id="tenant-a",
        application_id="demo-app",
        method="POST",
        path="/v1/gallery/search",
        status_code=429,
        latency_ms=30,
        created_at=150.0,
        error_code="rate_limited",
    )
    record_call_log(
        request_id="req-late",
        tenant_id="tenant-a",
        application_id="demo-app",
        method="POST",
        path="/v1/gallery/enroll",
        status_code=422,
        latency_ms=40,
        created_at=220.0,
        error_code="validation_error",
    )
    client = TestClient(app)

    response = client.get(
        "/v1/access/call-logs?error_code=rate_limited&created_since=120&created_until=180",
        headers={"X-Tenant-ID": "tenant-a"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] == 1
    assert data["logs"][0]["request_id"] == "req-match"
    assert data["logs"][0]["error_code"] == "rate_limited"

    direct_rows = list_call_logs("tenant-a", error_code="storage", created_since=90.0, created_until=110.0)
    assert [row["request_id"] for row in direct_rows] == ["req-old"]

    invalid = client.get("/v1/access/call-logs?created_since=-1", headers={"X-Tenant-ID": "tenant-a"})
    assert invalid.status_code == 422
    validation_rows = list_call_logs("tenant-a", endpoint="/v1/access/call-logs", error_code="validation_error")
    assert validation_rows
    assert validation_rows[0]["http_status"] == 422


def test_request_middleware_records_call_log_with_access_application() -> None:
    _, secret = portrait_access.create_application(
        "tenant-a",
        app_id="demo-app",
        name="Demo App",
        owner="integration-team",
        status_value="active",
        scopes=["models:read"],
    )
    client = TestClient(app)

    response = client.get("/v1/models", headers={"X-Tenant-ID": "tenant-a", "X-API-Key": secret, "X-Request-ID": "req-middleware"})

    assert response.status_code == 200
    rows = list_call_logs("tenant-a", request_id="req-middleware")
    assert rows
    assert rows[0]["application_id"] == "demo-app"
    assert rows[0]["path"] == "/v1/models"
    assert rows[0]["status"] == "success"


def test_call_logs_update_access_application_usage_summary() -> None:
    portrait_access.create_application(
        "tenant-a",
        app_id="demo-app",
        name="Demo App",
        owner="integration-team",
        status_value="active",
        scopes=["models:read"],
    )

    record_call_log(
        request_id="req-ok",
        tenant_id="tenant-a",
        application_id="demo-app",
        method="GET",
        path="/v1/models",
        status_code=200,
        latency_ms=11,
        created_at=100.0,
    )
    record_call_log(
        request_id="req-error",
        tenant_id="tenant-a",
        application_id="demo-app",
        method="POST",
        path="/v1/gallery/search",
        status_code=500,
        latency_ms=21,
        created_at=101.0,
    )

    application = portrait_access.list_applications("tenant-a")[0]
    assert application["call_count"] == 2
    assert application["error_count"] == 1
    assert application["error_rate"] == 0.5
    assert application["last_called_at"] == 101.0
    assert application["last_error_at"] == 101.0


def test_request_middleware_records_call_log_when_api_key_infers_tenant() -> None:
    _, secret = portrait_access.create_application(
        "tenant-a",
        app_id="demo-app",
        name="Demo App",
        owner="integration-team",
        status_value="active",
        scopes=["models:read"],
    )
    client = TestClient(app)

    response = client.get("/v1/models", headers={"X-API-Key": secret, "X-Request-ID": "req-inferred-tenant"})

    assert response.status_code == 200
    rows = list_call_logs("tenant-a", request_id="req-inferred-tenant")
    assert rows
    assert rows[0]["tenant_id"] == "tenant-a"
    assert rows[0]["application_id"] == "demo-app"