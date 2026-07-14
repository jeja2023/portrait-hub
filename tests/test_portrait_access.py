from collections.abc import Iterator

from fastapi.testclient import TestClient
import pytest

from app import portrait_access, portrait_auth, portrait_security, routes_portrait_access, security
from main import app


@pytest.fixture(autouse=True)
def isolated_access_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    portrait_access.clear_access_state()
    monkeypatch.setattr(portrait_access, "save_access_state", lambda: None)
    monkeypatch.setattr(routes_portrait_access, "audit_event", lambda *args, **kwargs: None)
    yield
    portrait_access.clear_access_state()


def tenant_headers(tenant_id: str = "tenant-a") -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id}


def create_application(
    client: TestClient,
    app_id: str = "demo-app",
    tenant_id: str = "tenant-a",
    scopes: list[str] | None = None,
    extra: dict[str, object] | None = None,
) -> tuple[dict[str, object], str]:
    payload: dict[str, object] = {
        "app_id": app_id,
        "name": "Demo App",
        "owner": "integration-team",
        "status": "active",
        "scopes": scopes or ["infer", "compare", "gallery:read"],
        "jwt_issuer": "portrait-demo",
        "jwt_audience": "portrait-api",
    }
    if extra:
        payload.update(extra)
    response = client.post(
        "/v1/access/applications",
        headers=tenant_headers(tenant_id),
        json=payload,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return data["application"], data["one_time_secret"]

def test_access_tenant_catalog_creates_default_application() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/access/tenants",
        json={"name": "客户 A", "application_name": "客户 A 业务系统", "daily_quota": 500},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    tenant = data["tenant"]
    application = data["application"]
    secret = data["one_time_secret"]
    assert tenant["name"] == "客户 A"
    assert tenant["tenant_id"].startswith("tenant-")
    assert tenant["application_count"] == 1
    assert application["tenant_id"] == tenant["tenant_id"]
    assert application["name"] == "客户 A 业务系统"
    assert application["daily_quota"] == 500
    assert "tenants:read" not in application["scopes"]
    assert "tenants:write" not in application["scopes"]
    assert secret.startswith("phk_")
    assert portrait_access.application_key_matches_any_tenant(secret)["tenant_id"] == tenant["tenant_id"]

    listed = client.get("/v1/access/tenants").json()["data"]
    assert listed["count"] == 1
    assert listed["tenants"][0]["tenant_id"] == tenant["tenant_id"]
    assert listed["tenants"][0]["application_count"] == 1

def test_access_tenant_catalog_rejects_duplicate_names() -> None:
    client = TestClient(app)

    first = client.post("/v1/access/tenants", json={"name": "重复客户", "create_default_application": False})
    duplicate = client.post("/v1/access/tenants", json={"name": "重复客户", "create_default_application": False})

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 409
    tenants = client.get("/v1/access/tenants").json()["data"]
    assert tenants["count"] == 1


def test_access_application_creation_backfills_tenant_catalog() -> None:
    client = TestClient(app)

    create_application(client, tenant_id="legacy-tenant")

    tenants = portrait_access.list_tenants()
    assert [item["tenant_id"] for item in tenants] == ["legacy-tenant"]
    assert tenants[0]["application_count"] == 1


def test_default_tenant_application_cannot_manage_tenant_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    created = client.post("/v1/access/tenants", json={"name": "业务客户"})
    secret = created.json()["data"]["one_time_secret"]
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)

    response = client.get("/v1/access/tenants", headers={"X-API-Key": secret})

    assert response.status_code == 403
    assert "tenants:read" in response.text


def test_access_applications_are_tenant_scoped_and_hide_secret_hashes() -> None:
    client = TestClient(app)

    application, secret = create_application(client)

    assert secret.startswith("phk_")
    assert application["app_id"] == "demo-app"
    assert application["api_key_preview"].startswith("phk_")
    assert "api_key_hash" not in application
    assert "previous_api_key_hashes" not in application

    same_tenant = client.get("/v1/access/applications", headers=tenant_headers()).json()["data"]
    other_tenant = client.get("/v1/access/applications", headers=tenant_headers("tenant-b")).json()["data"]
    assert same_tenant["count"] == 1
    assert same_tenant["applications"][0]["app_id"] == "demo-app"
    assert other_tenant["applications"] == []

    patched = client.patch(
        "/v1/access/applications/demo-app",
        headers=tenant_headers(),
        json={"status": "disabled", "scopes": ["infer"]},
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["application"]["status"] == "disabled"
    assert portrait_access.application_key_matches("tenant-a", secret) is None


def test_access_application_limits_can_be_created_and_cleared() -> None:
    client = TestClient(app)

    application, _ = create_application(
        client,
        extra={"rate_limit_per_minute": 120, "rate_limit_burst": 30, "daily_quota": 1000},
    )

    assert application["rate_limit_per_minute"] == 120
    assert application["rate_limit_burst"] == 30
    assert application["daily_quota"] == 1000
    assert application["daily_quota_used"] == 0

    cleared = client.patch(
        "/v1/access/applications/demo-app",
        headers=tenant_headers(),
        json={"rate_limit_per_minute": None, "rate_limit_burst": None, "daily_quota": None},
    )
    assert cleared.status_code == 200
    cleared_app = cleared.json()["data"]["application"]
    assert cleared_app["rate_limit_per_minute"] is None
    assert cleared_app["rate_limit_burst"] is None
    assert cleared_app["daily_quota"] is None
    assert cleared_app["daily_quota_used"] == 0


def test_access_application_rotation_keeps_old_key_in_grace_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portrait_access, "PORTRAIT_ACCESS_KEY_ROTATION_GRACE_SECONDS", 60.0)
    client = TestClient(app)
    _, old_secret = create_application(client)

    rotated = client.post("/v1/access/applications/demo-app/rotate", headers=tenant_headers())

    assert rotated.status_code == 200
    data = rotated.json()["data"]
    new_secret = data["one_time_secret"]
    assert new_secret.startswith("phk_")
    assert new_secret != old_secret
    assert "api_key_hash" not in data["application"]
    assert portrait_access.application_key_matches("tenant-a", old_secret) is not None
    assert portrait_access.application_key_matches("tenant-a", new_secret) is not None



def test_access_error_code_catalog_is_stable_redacted_and_tenant_scoped() -> None:
    client = TestClient(app)

    response = client.get("/v1/access/error-codes", headers=tenant_headers())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    rows = data["error_codes"]
    assert data["tenant_id"] == "tenant-a"
    assert data["count"] == len(rows)
    assert rows

    expected_fields = {"code", "http_status", "retryable", "category", "description", "operator_action"}
    assert all(set(row) == expected_fields for row in rows)
    by_code = {row["code"]: row for row in rows}
    assert by_code["validation_error"]["http_status"] == 422
    assert by_code["validation_error"]["retryable"] is False
    assert by_code["rate_limited"]["http_status"] == 429
    assert by_code["rate_limited"]["retryable"] is True
    assert by_code["storage_error"]["category"] == "dependency"
    assert by_code["batch_job_error"]["category"] == "job"
    assert by_code["migration_error"]["retryable"] is False

    body = response.text
    assert "api_key_hash" not in body
    assert "previous_api_key_hashes" not in body
    assert "signing_secret_hash" not in body
    assert "phk_" not in body
    assert "whsec_" not in body
def test_access_webhooks_validate_urls_hide_hashes_and_generate_sample_delivery() -> None:
    client = TestClient(app)
    create_application(client)

    created = client.post(
        "/v1/access/webhooks",
        headers=tenant_headers(),
        json={
            "webhook_id": "events-primary",
            "name": "Primary events",
            "application_id": "demo-app",
            "url": "https://hooks.example.com/portrait",
            "status": "active",
            "events": ["job.completed", "model.rollout"],
            "retry_limit": 4,
            "timeout_seconds": 7,
        },
    )

    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["one_time_secret"].startswith("whsec_")
    webhook = data["webhook"]
    assert webhook["webhook_id"] == "events-primary"
    assert webhook["status"] == "active"
    assert "signing_secret_hash" not in webhook

    missing_app = client.post(
        "/v1/access/webhooks",
        headers=tenant_headers(),
        json={"name": "Missing app", "application_id": "missing-app", "events": ["job.completed"]},
    )
    assert missing_app.status_code == 404

    invalid_url = client.post(
        "/v1/access/webhooks",
        headers=tenant_headers(),
        json={
            "name": "Bad URL",
            "application_id": "demo-app",
            "url": "https://user:pass@example.com/hook",
            "status": "active",
            "events": ["job.completed"],
        },
    )
    assert invalid_url.status_code == 422

    sample = client.post("/v1/access/webhooks/events-primary/sample", headers=tenant_headers())
    assert sample.status_code == 200
    delivery = sample.json()["data"]["sample_delivery"]
    assert delivery["delivery_status"] == "dry_run"
    assert delivery["headers"]["X-PortraitHub-Signature"].startswith("sha256=")
    assert delivery["body"]["tenant_id"] == "tenant-a"


def test_access_application_api_key_authenticates_with_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    _, secret = create_application(client, scopes=["models:read"])
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)

    allowed = client.get("/v1/models", headers={**tenant_headers(), "X-API-Key": secret})

    assert allowed.status_code == 200, allowed.text
    assert security.authenticated_request_identity(None, secret, "tenant-a") == "access-app:tenant-a:demo-app"


def test_access_application_api_key_rejects_missing_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    _, secret = create_application(client, scopes=["infer"])
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)

    denied = client.get("/v1/models", headers={**tenant_headers(), "X-API-Key": secret})

    assert denied.status_code == 403
    assert "缺少权限" in denied.text

def test_access_application_api_key_infers_tenant_without_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    _, secret = create_application(client, scopes=["access:read"])
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_security, "TENANT_HEADER_REQUIRED", True)

    response = client.get("/v1/access/error-codes", headers={"X-API-Key": secret})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["tenant_id"] == "tenant-a"
    assert security.authenticated_request_identity(None, secret, None) == "access-app:tenant-a:demo-app"
    assert portrait_access.application_key_matches_any_tenant(secret)["tenant_id"] == "tenant-a"


def test_access_application_api_key_rejects_wrong_explicit_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    _, secret = create_application(client, scopes=["access:read"])
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_security, "TENANT_HEADER_REQUIRED", True)

    response = client.get("/v1/access/error-codes", headers={"X-Tenant-ID": "tenant-b", "X-API-Key": secret})

    assert response.status_code in {401, 403}
