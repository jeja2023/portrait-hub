from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import portrait_access, portrait_auth, portrait_console_access, routes_portrait_access, security
from app.portrait_console_access import clear_console_ws_tickets
from app.portrait_gallery import GALLERY, gallery_key
from app.portrait_gallery_records import FeatureRecord, PersonRecord
from app.portrait_jobs import VIDEO_JOBS, JobStatus, VideoJob, job_key
from app.portrait_streams import STREAMS, StreamRecord, stream_key
from app.settings import CONSOLE_WS_TICKET_TTL_SECONDS
from main import app


def test_jobs_collection_is_tenant_scoped_filterable_and_cursor_paginated() -> None:
    snapshot = deepcopy(VIDEO_JOBS)
    VIDEO_JOBS.clear()
    try:
        jobs = [
            VideoJob(
                job_id="job_old",
                tenant_id="tenant-a",
                filename=None,
                status=JobStatus.COMPLETED,
                created_at=10.0,
                updated_at=12.0,
            ),
            VideoJob(
                job_id="batch_new",
                tenant_id="tenant-a",
                filename=None,
                status=JobStatus.RUNNING,
                created_at=20.0,
                updated_at=21.0,
            ),
            VideoJob(
                job_id="job_other",
                tenant_id="tenant-b",
                filename=None,
                status=JobStatus.QUEUED,
                created_at=30.0,
                updated_at=30.0,
            ),
        ]
        for job in jobs:
            VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job

        client = TestClient(app)
        first = client.get("/v1/jobs?limit=1", headers={"x-tenant-id": "tenant-a"})
        assert first.status_code == 200
        payload = first.json()["data"]
        assert [item["job_id"] for item in payload["items"]] == ["batch_new"]
        assert payload["items"][0]["kind"] == "batch"
        assert payload["total"] == 2
        assert payload["has_more"] is True
        assert payload["next_cursor"]

        second = client.get(
            "/v1/jobs",
            params={"limit": 1, "cursor": payload["next_cursor"]},
            headers={"x-tenant-id": "tenant-a"},
        )
        assert [item["job_id"] for item in second.json()["data"]["items"]] == ["job_old"]

        filtered = client.get(
            "/v1/jobs?kind=video&status=completed&created_since=5&created_until=15",
            headers={"x-tenant-id": "tenant-a"},
        )
        assert [item["job_id"] for item in filtered.json()["data"]["items"]] == ["job_old"]
        assert "job_other" not in filtered.text
        assert "filename" not in filtered.text
    finally:
        VIDEO_JOBS.clear()
        VIDEO_JOBS.update(snapshot)


def test_gallery_collection_returns_redacted_summaries_and_supports_search() -> None:
    snapshot = deepcopy(GALLERY)
    GALLERY.clear()
    try:
        body_feature = FeatureRecord(
            feature_id="feature-body",
            modality="body",
            embedding=[0.1, 0.2],
            embedding_dim=2,
            model_id="model-body",
            model_version="v1",
            quality_score=0.91,
            source_id="source-body",
            created_at=10.0,
            object_info={"thumbnail": "data:image/jpeg;base64,AAAA"},
        )
        person = PersonRecord(
            tenant_id="tenant-a",
            person_id="person-001",
            display_name="测试人员",
            metadata={"department": "qa", "api_token": "must-not-leak"},
            features=[body_feature],
            created_at=10.0,
            updated_at=20.0,
        )
        other = PersonRecord(
            tenant_id="tenant-b",
            person_id="person-other",
            display_name="Other",
            metadata={},
            features=[],
            created_at=30.0,
            updated_at=30.0,
        )
        GALLERY[gallery_key(person.tenant_id, person.person_id)] = person
        GALLERY[gallery_key(other.tenant_id, other.person_id)] = other

        client = TestClient(app)
        response = client.get(
            "/v1/gallery",
            params={"query": "测试", "modality": "body", "limit": 10},
            headers={"x-tenant-id": "tenant-a"},
        )
        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["count"] == 1
        assert payload["items"][0]["person_id"] == "person-001"
        assert payload["items"][0]["feature_count"] == 1
        assert payload["items"][0]["modalities"] == ["body"]
        assert payload["items"][0]["thumbnail"].startswith("data:image/jpeg;base64,")
        assert payload["items"][0]["metadata"]["api_token"] == "<redacted>"
        assert "embedding" not in response.text
        assert "must-not-leak" not in response.text
        assert "person-other" not in response.text
    finally:
        GALLERY.clear()
        GALLERY.update(snapshot)


def test_console_me_returns_principal_capabilities() -> None:
    response = TestClient(app).get("/v1/console/me", headers={"x-tenant-id": "tenant-a"})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.json()["data"]
    assert payload["tenant_id"] == "tenant-a"
    assert payload["auth_kind"] == "development_anonymous"
    assert payload["permissions"] == ["*"]
    assert "features" not in payload

def test_console_ws_ticket_is_resource_bound_and_single_use() -> None:
    snapshot = deepcopy(VIDEO_JOBS)
    VIDEO_JOBS.clear()
    clear_console_ws_tickets()
    try:
        job = VideoJob(job_id="job-ticket", tenant_id="tenant-a", filename=None)
        VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
        client = TestClient(app)
        issued = client.post(
            "/v1/console/ws-ticket",
            headers={"x-tenant-id": "tenant-a"},
            json={"resource_type": "job", "resource_id": job.job_id},
        )
        assert issued.status_code == 200
        assert issued.headers["Cache-Control"] == "no-store"
        ticket_payload = issued.json()["data"]
        assert ticket_payload["ticket"].startswith("cwt_")
        assert ticket_payload["websocket_path"] == f"/ws/jobs/{job.job_id}"

        ws_url = f"/ws/jobs/{job.job_id}?tenant_id=tenant-a&ticket={ticket_payload['ticket']}"
        with client.websocket_connect(ws_url) as websocket:
            snapshot_payload = websocket.receive_json()
        assert snapshot_payload["job"]["job_id"] == job.job_id

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(ws_url):
                pass
        assert exc_info.value.code == 1008
    finally:
        clear_console_ws_tickets()
        VIDEO_JOBS.clear()
        VIDEO_JOBS.update(snapshot)


def test_console_next_shell_and_hashed_assets_are_public() -> None:
    client = TestClient(app)

    home = client.get("/")
    canonical = client.get("/console")
    shell = client.get("/console/next")

    assert home.status_code == 200
    assert canonical.status_code == 200
    assert shell.status_code == 200
    assert home.text == shell.text
    assert canonical.text == shell.text
    assert shell.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
    csp = shell.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp
    assert "/assets/console-next/assets/" in shell.text

    asset_url = shell.text.split('src="', 1)[1].split('"', 1)[0]
    asset = client.get(asset_url)
    assert asset.status_code == 200
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert asset.headers["X-Content-Type-Options"] == "nosniff"

def test_console_next_asset_route_hides_internal_files_and_traversal() -> None:
    client = TestClient(app)

    assert client.get("/assets/console-next/index.html").status_code == 404
    assert client.get("/assets/console-next/.vite/manifest.json").status_code == 404
    assert client.get("/assets/console-next/assets/missing.js.map").status_code == 404
    assert client.get("/assets/console-next/%2E%2E/index.html").status_code == 404


def test_console_collections_reject_invalid_cursors() -> None:
    client = TestClient(app)

    jobs = client.get("/v1/jobs", params={"cursor": "not-a-valid-cursor"})
    gallery = client.get("/v1/gallery", params={"cursor": "not-a-valid-cursor"})

    assert jobs.status_code == 422
    assert gallery.status_code == 422
    assert "游标无效" in jobs.text
    assert "游标无效" in gallery.text


def test_public_console_shell_does_not_expose_tenant_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", False)
    monkeypatch.setattr(portrait_auth, "AUTH_REQUIRED", True)
    monkeypatch.setattr(portrait_auth, "API_TOKEN", None)
    client = TestClient(app)

    assert client.get("/console/next").status_code == 200
    assert client.get("/v1/console/me").status_code == 401
    assert client.get("/v1/jobs").status_code == 401
    assert client.get("/v1/gallery").status_code == 401


def test_console_ws_tickets_reject_wrong_binding_and_expiration() -> None:
    clear_console_ws_tickets()
    try:
        wrong_tenant, _ = portrait_console_access.issue_console_ws_ticket(
            tenant_id="tenant-a",
            resource_type="job",
            resource_id="job-a",
            permission="jobs:read",
            now=100.0,
        )
        assert not portrait_console_access.consume_console_ws_ticket(
            wrong_tenant,
            tenant_id="tenant-b",
            resource_type="job",
            resource_id="job-a",
            permission="jobs:read",
            now=101.0,
        )

        wrong_resource, _ = portrait_console_access.issue_console_ws_ticket(
            tenant_id="tenant-a",
            resource_type="job",
            resource_id="job-a",
            permission="jobs:read",
            now=100.0,
        )
        assert not portrait_console_access.consume_console_ws_ticket(
            wrong_resource,
            tenant_id="tenant-a",
            resource_type="job",
            resource_id="job-b",
            permission="jobs:read",
            now=101.0,
        )

        expired, record = portrait_console_access.issue_console_ws_ticket(
            tenant_id="tenant-a",
            resource_type="job",
            resource_id="job-a",
            permission="jobs:read",
            now=100.0,
        )
        assert not portrait_console_access.consume_console_ws_ticket(
            expired,
            tenant_id="tenant-a",
            resource_type="job",
            resource_id="job-a",
            permission="jobs:read",
            now=record.expires_at + 0.001,
        )
    finally:
        clear_console_ws_tickets()


def test_console_ws_ticket_ttl_is_capped_at_sixty_seconds() -> None:
    # 契约（方案 §8.1）：票据 TTL 不得超过 60 秒；settings clamp 上限锁定 60。
    assert CONSOLE_WS_TICKET_TTL_SECONDS <= 60
    clear_console_ws_tickets()
    try:
        _, record = portrait_console_access.issue_console_ws_ticket(
            tenant_id="tenant-a",
            resource_type="job",
            resource_id="job-a",
            permission="jobs:read",
            now=100.0,
        )
        assert record.expires_at - 100.0 <= 60.0
    finally:
        clear_console_ws_tickets()


def _encode_jwt_segment(payload: dict | bytes) -> str:
    data = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _hs256_token(payload: dict, secret: str = "test-secret") -> str:
    header = _encode_jwt_segment({"alg": "HS256", "typ": "JWT"})
    body = _encode_jwt_segment(payload)
    signature = hmac.new(secret.encode("utf-8"), f"{header}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{body}.{_encode_jwt_segment(signature)}"


def test_console_me_contract_for_global_api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", "global-secret")
    monkeypatch.setattr(security, "API_TOKEN_TENANT_ID", "tenant-a")
    monkeypatch.setattr(security, "API_TOKEN_ALLOW_TENANT_OVERRIDE", False)
    monkeypatch.setattr(portrait_console_access, "API_TOKEN", "global-secret")
    client = TestClient(app)

    response = client.get(
        "/v1/console/me",
        headers={"x-tenant-id": "tenant-a", "Authorization": "Bearer global-secret"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["tenant_id"] == "tenant-a"
    assert payload["auth_kind"] == "global_api_token"
    assert payload["permissions"] == ["*"]
    assert payload["expires_at"] is None
    assert "features" not in payload


def test_console_me_contract_for_application_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    portrait_access.clear_access_state()
    monkeypatch.setattr(portrait_access, "save_access_state", lambda: None)
    monkeypatch.setattr(routes_portrait_access, "audit_event", lambda *args, **kwargs: None)
    client = TestClient(app)
    created = client.post(
        "/v1/access/applications",
        headers={"X-Tenant-ID": "tenant-a"},
        json={
            "app_id": "console-me-app",
            "name": "Console Me App",
            "owner": "qa",
            "status": "active",
            "scopes": ["infer", "jobs:read"],
        },
    )
    assert created.status_code == 200, created.text
    secret = created.json()["data"]["one_time_secret"]
    try:
        monkeypatch.setattr(security, "RBAC_ENABLED", True)
        monkeypatch.setattr(security, "AUTH_REQUIRED", True)
        monkeypatch.setattr(security, "API_TOKEN", None)
        monkeypatch.setattr(portrait_console_access, "API_TOKEN", None)

        response = client.get(
            "/v1/console/me",
            headers={"X-Tenant-ID": "tenant-a", "X-API-Key": secret},
        )

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["auth_kind"] == "application_api_key"
        assert payload["subject"] == "console-me-app"
        assert payload["permissions"] == ["infer", "jobs:read"]
        assert payload["scopes"] == ["infer", "jobs:read"]
        assert "features" not in payload
    finally:
        portrait_access.clear_access_state()


def test_console_me_contract_for_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_console_access, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_console_access, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    expires = int(time.time()) + 300
    token = _hs256_token(
        {
            "iss": "portrait-hub",
            "aud": "portrait-hub-api",
            "sub": "auditor-01",
            "roles": ["viewer"],
            "tenant_id": "tenant-a",
            "exp": expires,
        }
    )
    client = TestClient(app)

    response = client.get(
        "/v1/console/me",
        headers={"X-Tenant-ID": "tenant-a", "Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["auth_kind"] == "jwt"
    assert payload["subject"] == "auditor-01"
    assert payload["roles"] == ["viewer"]
    assert "jobs:read" in payload["permissions"]
    assert payload["expires_at"] == float(expires)
    assert "features" not in payload


def test_console_collections_reject_authenticated_principal_without_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 已认证但缺少 jobs:read/gallery:read/streams:read -> 403（方案 §12.3）。
    monkeypatch.setattr(security, "RBAC_ENABLED", True)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    monkeypatch.setattr(portrait_auth, "RBAC_ENABLED", True)
    monkeypatch.setattr(portrait_auth, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(portrait_auth, "JWT_ISSUER", "portrait-hub")
    monkeypatch.setattr(portrait_auth, "JWT_AUDIENCE", "portrait-hub-api")
    token = _hs256_token(
        {
            "iss": "portrait-hub",
            "aud": "portrait-hub-api",
            "roles": ["algorithm"],  # 无 jobs:read / gallery:read / streams:read
            "tenant_id": "tenant-a",
            "exp": int(time.time()) + 300,
        }
    )
    headers = {"X-Tenant-ID": "tenant-a", "Authorization": f"Bearer {token}"}
    client = TestClient(app)

    assert client.get("/v1/jobs", headers=headers).status_code == 403
    assert client.get("/v1/gallery", headers=headers).status_code == 403
    denied_ticket = client.post(
        "/v1/console/ws-ticket",
        headers=headers,
        json={"resource_type": "job", "resource_id": "job-any"},
    )
    assert denied_ticket.status_code == 403


def test_console_ws_ticket_requires_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "RBAC_ENABLED", False)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "API_TOKEN", None)
    client = TestClient(app)

    anonymous = client.post(
        "/v1/console/ws-ticket",
        headers={"x-tenant-id": "tenant-a"},
        json={"resource_type": "job", "resource_id": "job-any"},
    )

    assert anonymous.status_code == 401


def test_console_ws_ticket_supports_stream_resources() -> None:
    snapshot = dict(STREAMS)
    STREAMS.clear()
    clear_console_ws_tickets()
    try:
        stream = StreamRecord(
            stream_id="stream_ticket",
            tenant_id="tenant-a",
            stream_url="rtsp://example.com/live",
            name="ticket-stream",
            settings={},
            metadata={},
        )
        STREAMS[stream_key(stream.tenant_id, stream.stream_id)] = stream
        client = TestClient(app)

        issued = client.post(
            "/v1/console/ws-ticket",
            headers={"x-tenant-id": "tenant-a"},
            json={"resource_type": "stream", "resource_id": stream.stream_id},
        )
        assert issued.status_code == 200
        ticket_payload = issued.json()["data"]
        assert ticket_payload["websocket_path"] == f"/ws/streams/{stream.stream_id}"

        ws_url = f"/ws/streams/{stream.stream_id}?tenant_id=tenant-a&ticket={ticket_payload['ticket']}"
        with client.websocket_connect(ws_url) as websocket:
            payload = websocket.receive_json()
        assert payload["status"] == "success"
        assert payload["stream"]["stream_id"] == stream.stream_id

        # 单次消费：复用同一 ticket 必须被拒绝
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(ws_url):
                pass
        assert exc_info.value.code == 1008
    finally:
        clear_console_ws_tickets()
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_gallery_empty_collection_returns_stable_response() -> None:
    snapshot = deepcopy(GALLERY)
    GALLERY.clear()
    try:
        response = TestClient(app).get("/v1/gallery", headers={"x-tenant-id": "tenant-empty"})

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["items"] == []
        assert payload["count"] == 0
        assert payload["total"] == 0
        assert payload["has_more"] is False
        assert payload["next_cursor"] is None
    finally:
        GALLERY.clear()
        GALLERY.update(snapshot)


def test_v1_authenticated_responses_default_to_no_store() -> None:
    # 方案 §8.2.5：敏感 API 与鉴权对象响应统一 no-store。
    client = TestClient(app)

    jobs = client.get("/v1/jobs", headers={"x-tenant-id": "tenant-a"})
    gallery = client.get("/v1/gallery", headers={"x-tenant-id": "tenant-a"})

    assert jobs.headers["Cache-Control"] == "no-store"
    assert gallery.headers["Cache-Control"] == "no-store"
