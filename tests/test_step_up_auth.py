from __future__ import annotations

import time

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import oidc_auth, portrait_auth
from app.server import app


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/admin/export",
            "raw_path": b"/v1/admin/export",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
        }
    )


@pytest.mark.asyncio
async def test_step_up_requires_recent_interactive_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portrait_auth, "AUTH_REQUIRED", True)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    monkeypatch.setattr(portrait_auth, "API_TOKEN", "platform-token")
    monkeypatch.setattr(oidc_auth, "require_browser_session_csrf", lambda *args: None)
    monkeypatch.setattr(
        oidc_auth,
        "browser_session_claims",
        lambda request: {"auth_kind": "local", "auth_time": time.time(), "iat": time.time()},
    )

    await portrait_auth.require_step_up_authentication(_request())

    monkeypatch.setattr(
        oidc_auth,
        "browser_session_claims",
        lambda request: {"auth_kind": "local", "auth_time": time.time() - 3600},
    )
    with pytest.raises(HTTPException) as expired:
        await portrait_auth.require_step_up_authentication(_request())
    assert expired.value.status_code == 403
    assert expired.value.detail["code"] == "step_up_authentication_required"


@pytest.mark.asyncio
async def test_step_up_rejects_platform_and_application_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portrait_auth, "AUTH_REQUIRED", True)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    monkeypatch.setattr(portrait_auth, "API_TOKEN", "platform-token")

    with pytest.raises(HTTPException) as platform:
        await portrait_auth.require_step_up_authentication(
            _request(), authorization="Bearer platform-token"
        )
    assert platform.value.status_code == 403

    with pytest.raises(HTTPException) as application:
        await portrait_auth.require_step_up_authentication(_request(), x_api_key="application-key")
    assert application.value.status_code == 403


def test_local_step_up_refreshes_recent_authentication() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    login = client.post("/v1/auth/local/login", json={"username": "admin", "password": "123456"})
    assert login.status_code == 200
    csrf = client.cookies.get("portrait_csrf")
    assert csrf

    wrong = client.post(
        "/v1/auth/local/step-up",
        headers={"X-CSRF-Token": csrf},
        json={"password": "wrong"},
    )
    assert wrong.status_code == 401

    refreshed = client.post(
        "/v1/auth/local/step-up",
        headers={"X-CSRF-Token": csrf},
        json={"password": "123456"},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["data"]["authenticated"] is True
    status_response = client.get("/v1/auth/step-up/status")
    assert status_response.status_code == 200
    assert status_response.json()["data"]["recent"] is True


def test_high_risk_route_rejects_expired_browser_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "l" * 48
    monkeypatch.setattr(portrait_auth, "AUTH_REQUIRED", True)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    monkeypatch.setattr(portrait_auth, "API_TOKEN", "")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_ENABLED", True)
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_USERNAME", "admin")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_PASSWORD", "strong-test-password")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_SESSION_SECRET", secret)
    csrf = "expired-session-csrf"
    session = oidc_auth._signed_payload(
        {
            "purpose": "local-session",
            "auth_kind": "local",
            "sub": "admin",
            "tenant_id": "default",
            "roles": ["admin"],
            "csrf": csrf,
            "auth_time": int(time.time()) - 3600,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        secret=secret,
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("portrait_local_session", session)
    client.cookies.set("portrait_csrf", csrf)

    response = client.delete(
        "/v1/gallery/missing-person",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 403
    assert response.headers["X-PortraitHub-Step-Up"] == "required"
    assert response.json()["error"]["code"] == "step_up_authentication_required"
