from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import oidc_auth, portrait_access
from main import app


@pytest.fixture
def configured_oidc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oidc_auth, "OIDC_ENABLED", True)
    monkeypatch.setattr(oidc_auth, "OIDC_ISSUER", "https://identity.example.com")
    monkeypatch.setattr(oidc_auth, "OIDC_CLIENT_ID", "portrait-console")
    monkeypatch.setattr(oidc_auth, "OIDC_SESSION_SECRET", "s" * 48)
    monkeypatch.setattr(oidc_auth, "OIDC_SESSION_COOKIE_NAME", "portrait_oidc_session")
    monkeypatch.setattr(oidc_auth, "OIDC_ROLE_CLAIM", "roles")
    monkeypatch.setattr(oidc_auth, "OIDC_GROUPS_CLAIM", "groups")
    monkeypatch.setattr(oidc_auth, "OIDC_TENANT_CLAIM", "tenant_id")
    monkeypatch.setattr(
        oidc_auth,
        "OIDC_ROLE_MAPPING",
        '{"portrait-admin":"admin","portrait-operator":"operator"}',
    )
    monkeypatch.setattr(oidc_auth, "OIDC_DEFAULT_ROLE", "")
    monkeypatch.setattr(oidc_auth, "OIDC_DEFAULT_TENANT_ID", "")
    monkeypatch.setattr(oidc_auth, "OIDC_IDENTITY_ADMIN_URL", "https://identity.example.com/admin")


def session_cookie(
    *,
    roles: list[str],
    tenant_id: str = "tenant-a",
    csrf: str = "csrf-test",
    managed_member_id: str | None = None,
    phone_number: str | None = None,
) -> str:
    payload = {
        "purpose": "session",
        "sub": "user-01",
        "name": "Test User",
        "email": "user@example.com",
        "tenant_id": tenant_id,
        "roles": roles,
        "csrf": csrf,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    if managed_member_id is not None:
        payload["managed_member_id"] = managed_member_id
    if phone_number is not None:
        payload["phone_number"] = phone_number
        payload["phone_number_verified"] = True
    return oidc_auth._signed_payload(payload)


def test_local_admin_login_uses_secure_browser_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_ENABLED", True)
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_USERNAME", "admin")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_PASSWORD", "123456")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_TENANT_ID", "default")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_SESSION_SECRET", "l" * 48)
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(oidc_auth, "_production_profile", lambda: False)
    client = TestClient(app)

    wrong = client.post(
        "/v1/auth/local/login",
        json={"username": "admin", "password": "wrong"},
    )
    login = client.post(
        "/v1/auth/local/login",
        json={"username": "admin", "password": "123456"},
    )

    assert wrong.status_code == 401
    assert login.status_code == 200
    session_header = next(
        header for header in login.headers.get_list("set-cookie") if header.startswith("portrait_local_session=")
    )
    assert "HttpOnly" in session_header
    assert "SameSite=lax" in session_header
    assert "123456" not in session_header

    me = client.get("/v1/console/me")
    assert me.status_code == 200
    principal = me.json()["data"]
    assert principal["auth_kind"] == "local"
    assert principal["subject"] == "admin"
    assert principal["roles"] == ["admin"]
    assert principal["permissions"] == ["*"]
    assert principal["tenant_id"] == "default"

    missing_csrf = client.post("/v1/gallery/reindex?dry_run=true")
    csrf = client.cookies.get("portrait_csrf")
    accepted = client.post(
        "/v1/gallery/reindex?dry_run=true",
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_csrf.status_code == 403
    assert accepted.status_code == 200

    logout = client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    assert client.get("/v1/console/me").json()["data"]["auth_kind"] == "development_anonymous"


@pytest.mark.asyncio
async def test_local_default_account_rejects_remote_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_ENABLED", True)
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_ALLOW_REMOTE", False)
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_USERNAME", "admin")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_PASSWORD", "123456")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_TENANT_ID", "default")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_SESSION_SECRET", "l" * 48)
    monkeypatch.setattr(oidc_auth, "_production_profile", lambda: False)
    request = type(
        "RemoteRequest",
        (),
        {
            "client": type("Client", (), {"host": "203.0.113.10"})(),
            "cookies": {},
        },
    )()

    assert oidc_auth._request_is_loopback(request) is False
    with pytest.raises(HTTPException) as exc_info:
        await oidc_auth.local_login(
            oidc_auth.LocalLoginRequest(username="admin", password="123456"),
            request,
        )
    assert exc_info.value.status_code == 403


def test_remote_login_requires_non_default_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_ENABLED", True)
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_ALLOW_REMOTE", True)
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_USERNAME", "admin")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_PASSWORD", "123456")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_TENANT_ID", "default")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_SESSION_SECRET", "l" * 48)
    monkeypatch.setattr(oidc_auth, "_production_profile", lambda: False)

    assert oidc_auth.local_auth_is_configured() is False


def test_local_default_password_is_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_ENABLED", True)
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_USERNAME", "admin")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_PASSWORD", "123456")
    monkeypatch.setattr(oidc_auth, "LOCAL_AUTH_TENANT_ID", "default")
    monkeypatch.setattr(
        oidc_auth,
        "LOCAL_AUTH_SESSION_SECRET",
        oidc_auth._DEFAULT_LOCAL_SESSION_SECRET,
    )
    monkeypatch.setattr(oidc_auth, "_production_profile", lambda: True)

    assert oidc_auth.local_auth_is_configured() is False
    response = TestClient(app).post(
        "/v1/auth/local/login",
        json={"username": "admin", "password": "123456"},
    )
    assert response.status_code == 503


def test_oidc_public_config_is_safe_when_disabled() -> None:
    response = TestClient(app).get("/v1/auth/oidc/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["schema_version"] == "1.0"
    assert payload["request_id"]
    assert payload["data"] == {
        "enabled": False,
        "provider_name": oidc_auth.OIDC_PROVIDER_NAME,
        "credential_login_available": True,
    }


def test_oidc_role_mapping_accepts_groups_and_rejects_unassigned_users(
    configured_oidc: None,
) -> None:
    assert oidc_auth._local_roles({"groups": ["portrait-operator"]}) == ["operator"]

    with pytest.raises(HTTPException) as exc_info:
        oidc_auth._local_roles({"groups": ["unrelated-group"]})

    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException):
        oidc_auth._local_roles({"roles": ["admin"]})


def test_oidc_signed_session_rejects_tampering_and_expiration(configured_oidc: None) -> None:
    valid = session_cookie(roles=["viewer"])
    assert oidc_auth._read_signed_payload(valid, purpose="session")["sub"] == "user-01"
    assert oidc_auth._read_signed_payload(valid + "x", purpose="session") is None
    assert (
        oidc_auth._read_signed_payload(
            oidc_auth._signed_payload(
                {
                    "purpose": "session",
                    "sub": "expired",
                    "exp": time.time() - 1,
                }
            ),
            purpose="session",
        )
        is None
    )


def test_oidc_session_drives_console_identity_and_role_permissions(
    configured_oidc: None,
) -> None:
    client = TestClient(app)
    client.cookies.set("portrait_oidc_session", session_cookie(roles=["viewer"]))

    response = client.get("/v1/console/me")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["auth_kind"] == "oidc"
    assert payload["subject"] == "user-01"
    assert payload["display_name"] == "Test User"
    assert payload["tenant_id"] == "tenant-a"
    assert payload["roles"] == ["viewer"]
    assert "gallery:read" in payload["permissions"]

    assert payload["identity"]["provider_name"] == oidc_auth.OIDC_PROVIDER_NAME
    assert payload["identity"]["identity_admin_url"] == ""


def test_oidc_session_enforces_tenant_and_permission_boundaries(
    configured_oidc: None,
) -> None:
    client = TestClient(app)
    client.cookies.set("portrait_oidc_session", session_cookie(roles=["viewer"]))

    wrong_tenant = client.get("/v1/console/me", headers={"X-Tenant-ID": "tenant-b"})
    denied = client.get("/v1/admin/status")
    identity_denied = client.get("/v1/admin/identity")

    assert wrong_tenant.status_code == 403
    assert denied.status_code == 403
    assert "admin:status" in denied.text
    assert identity_denied.status_code == 403
    assert "admin:identity" in identity_denied.text


def test_oidc_session_requires_csrf_for_writes(configured_oidc: None) -> None:
    client = TestClient(app)
    client.cookies.set("portrait_oidc_session", session_cookie(roles=["operator"]))
    client.cookies.set("portrait_csrf", "csrf-test")

    missing = client.post("/v1/console/ws-ticket", json={"resource_type": "job", "resource_id": "job-any"})
    accepted_auth = client.post(
        "/v1/console/ws-ticket",
        headers={"X-CSRF-Token": "csrf-test"},
        json={"resource_type": "job", "resource_id": "job-any"},
    )

    assert missing.status_code == 403
    assert accepted_auth.status_code == 404
    permission_only_missing = client.post("/v1/gallery/reindex?dry_run=true")
    permission_only_accepted = client.post(
        "/v1/gallery/reindex?dry_run=true",
        headers={"X-CSRF-Token": "csrf-test"},
    )
    assert permission_only_missing.status_code == 403
    assert "CSRF validation failed" in permission_only_missing.text
    assert permission_only_accepted.status_code == 200


def test_explicit_api_key_takes_precedence_over_oidc_cookie(configured_oidc: None) -> None:
    client = TestClient(app)
    client.cookies.set("portrait_oidc_session", session_cookie(roles=["admin"]))

    response = client.get("/v1/console/me", headers={"X-API-Key": "invalid-key"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_oidc_callback_validates_rsa_token_and_sets_session(
    configured_oidc: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oidc_auth, "OIDC_ALLOW_INSECURE_HTTP", True)
    monkeypatch.setattr(oidc_auth, "OIDC_COOKIE_SECURE", False)
    monkeypatch.setattr(oidc_auth, "OIDC_REDIRECT_URI", "http://testserver/auth/oidc/callback")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "alg": "RS256",
        "n": oidc_auth._b64url_encode(public_numbers.n.to_bytes(256, "big")),
        "e": oidc_auth._b64url_encode(public_numbers.e.to_bytes(3, "big")),
    }
    flow = {
        "purpose": "flow",
        "state": "state-value",
        "nonce": "nonce-value",
        "verifier": "verifier-value",
        "return_to": "/admin/identity",
        "exp": int(time.time()) + 300,
    }
    claims = {
        "iss": "https://identity.example.com",
        "aud": "portrait-console",
        "sub": "enterprise-user",
        "name": "Enterprise User",
        "email": "enterprise@example.com",
        "tenant_id": "tenant-a",
        "groups": ["portrait-admin"],
        "projects": ["default", "alpha"],
        "nonce": "nonce-value",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    header = {"alg": "RS256", "kid": "test-key", "typ": "JWT"}
    signing_input = (
        oidc_auth._b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + oidc_auth._b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    )
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    id_token = signing_input + "." + oidc_auth._b64url_encode(signature)

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str, **_kwargs):
            if url.endswith("openid-configuration"):
                return FakeResponse(
                    {
                        "issuer": "https://identity.example.com",
                        "authorization_endpoint": "https://identity.example.com/authorize",
                        "token_endpoint": "https://identity.example.com/token",
                        "jwks_uri": "https://identity.example.com/jwks",
                    }
                )
            return FakeResponse({"keys": [jwk]})

        async def post(self, _url: str, **_kwargs):
            return FakeResponse({"id_token": id_token})

    monkeypatch.setattr(oidc_auth.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(oidc_auth, "_DISCOVERY_CACHE", None)
    client = TestClient(app)
    client.cookies.set("portrait_oidc_flow", oidc_auth._signed_payload(flow))

    response = client.get(
        "/auth/oidc/callback?code=code-value&state=state-value",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith("/?oidc=success")
    session_header = next(
        header for header in response.headers.get_list("set-cookie") if header.startswith("portrait_oidc_session=")
    )
    assert "HttpOnly" in session_header
    assert "SameSite=lax" in session_header
    signed_session = oidc_auth._read_signed_payload(response.cookies.get("portrait_oidc_session"), purpose="session")
    assert signed_session is not None
    assert signed_session["projects"] == ["alpha", "default"]
    claims_response = client.get("/v1/console/me")
    assert claims_response.status_code == 200
    assert claims_response.json()["data"]["subject"] == "enterprise-user"
    assert claims_response.json()["data"]["roles"] == ["admin"]
    identity_response = client.get("/v1/admin/identity")
    assert identity_response.status_code == 200
    identity_payload = identity_response.json()["data"]
    assert identity_payload["identity"]["identity_admin_url"] == "https://identity.example.com/admin"
    assert any(item["role"] == "admin" for item in identity_payload["roles"])


def test_managed_oidc_session_uses_member_roles_and_reacts_to_status(
    configured_oidc: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portrait_access.clear_access_state()
    monkeypatch.setattr(portrait_access, "save_access_state", lambda: None)
    try:
        portrait_access.create_tenant("企业租户", tenant_id="tenant-a")
        member = portrait_access.create_member(
            "tenant-a",
            phone="13800138005",
            display_name="企业操作员",
            roles=["operator"],
        )
        claims = {
            "sub": "user-01",
            "phone_number": "+8613800138005",
            "phone_number_verified": False,
            "tenant_id": "tenant-a",
            "roles": [],
        }

        with pytest.raises(HTTPException):
            oidc_auth._oidc_session_access(claims, "tenant-a")
        claims["phone_number"] = "13800138005"
        claims["phone_number_verified"] = True
        roles, managed_member_id = oidc_auth._oidc_session_access(claims, "tenant-a")
        assert roles == ["operator"]
        assert managed_member_id == member["member_id"]
        assert portrait_access.find_member(member["member_id"])["subject"] == "user-01"

        client = TestClient(app)
        client.cookies.set(
            "portrait_oidc_session",
            session_cookie(
                roles=["operator"],
                managed_member_id=member["member_id"],
                phone_number="13800138005",
            ),
        )
        me = client.get("/v1/console/me")
        assert me.status_code == 200, me.text
        assert me.json()["data"]["roles"] == ["operator"]
        assert client.get("/v1/admin/identity").status_code == 403

        portrait_access.update_member(member["member_id"], {"status": "disabled"})
        disabled_me = client.get("/v1/console/me")
        assert disabled_me.status_code == 200, disabled_me.text
        assert disabled_me.json()["data"]["roles"] == []

        portrait_access.update_member(member["member_id"], {"status": "active"})
        portrait_access.delete_member(member["member_id"])
        removed_me = client.get("/v1/console/me")
        assert removed_me.status_code == 200, removed_me.text
        assert removed_me.json()["data"]["roles"] == []
    finally:
        portrait_access.clear_access_state()
