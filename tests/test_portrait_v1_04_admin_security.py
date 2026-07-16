from io import BytesIO

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app import (
    routes_portrait_admin,
)
from app.portrait_gallery import GALLERY
from app.portrait_streams import STREAMS, create_stream
from main import app


def image_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (96, 80), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(
    name: str, color: tuple[int, int, int]
) -> tuple[str, tuple[str, bytes, str]]:
    return name, (f"{name}.png", image_bytes(color), "image/png")


def v1_error_message(response) -> str:
    return response.json()["error"]["message"]


def v1_error_details(response):
    return response.json()["error"].get("details", {})


def v1_validation_issues(response) -> list[dict[str, object]]:
    return v1_error_details(response)["issues"]


def test_security_headers_and_admin_export_contract() -> None:
    client = TestClient(app)
    gallery_snapshot = dict(GALLERY)
    GALLERY.clear()

    try:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.headers["X-Content-Type-Options"] == "nosniff"
        assert health.headers["X-Frame-Options"] == "DENY"
        assert health.headers["Referrer-Policy"] == "no-referrer"
        assert health.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert health.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert health.headers["X-Permitted-Cross-Domain-Policies"] == "none"
        assert health.headers["Content-Security-Policy"].startswith("default-src")

        export = client.get("/v1/admin/export")
        assert export.status_code == 200
        assert "people" in export.json()["data"]
        cleanup = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})
        assert cleanup.status_code == 200
        assert "removed_jobs" in cleanup.json()["data"]
        assert "removed_gallery_people" in cleanup.json()["data"]
    finally:
        GALLERY.clear()
        GALLERY.update(gallery_snapshot)


def test_admin_backup_writes_redacted_object(monkeypatch, workspace_tmp_path) -> None:
    from app import portrait_object_storage

    monkeypatch.setattr(
        portrait_object_storage, "OBJECT_STORAGE_DIR", workspace_tmp_path / "objects"
    )
    client = TestClient(app)

    response = client.post("/v1/admin/backup", json={"confirm": "backup"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["backup"]["stored"] is True
    assert data["bytes"] > 0


def test_hsts_header_is_explicitly_gated_for_https_deployments(monkeypatch) -> None:
    from app import security_headers

    monkeypatch.setattr(security_headers, "HSTS_ENABLED", True)
    monkeypatch.setattr(security_headers, "HSTS_MAX_AGE_SECONDS", 123)
    monkeypatch.setattr(security_headers, "HSTS_INCLUDE_SUBDOMAINS", True)
    monkeypatch.setattr(security_headers, "HSTS_PRELOAD", True)
    client = TestClient(app)

    response = client.get("/health")

    assert (
        response.headers["Strict-Transport-Security"]
        == "max-age=123; includeSubDomains; preload"
    )


def test_admin_export_is_redacted_for_sensitive_fields() -> None:
    client = TestClient(app)
    GALLERY.clear()
    snapshot = dict(STREAMS)
    STREAMS.clear()
    try:
        stream = create_stream("http://example.com/live")
        stream.add_event(
            "debug",
            "payload with sensitive fields",
            {
                "token": "secret-123",
                "access_key": "key-456",
                "embedding": [1.0, 2.0, 3.0],
            },
        )

        export = client.get("/v1/admin/export")

        assert export.status_code == 200
        data = export.json()["data"]
        exported_streams = data["streams"]
        assert exported_streams
        payload = exported_streams[0]["events"][-1]["payload"]
        assert payload["token"] == "<redacted>"
        assert payload["access_key"] == "<redacted>"
        assert payload["embedding"] == "<redacted>"
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_admin_export_is_audited_without_exported_payload(monkeypatch) -> None:
    client = TestClient(app)
    GALLERY.clear()
    snapshot = dict(STREAMS)
    STREAMS.clear()
    events = []
    monkeypatch.setattr(
        routes_portrait_admin,
        "audit_event",
        lambda event, **fields: events.append((event, fields)),
    )
    try:
        stream = create_stream("http://example.com/live")
        stream.add_event("debug", "event", {"token": "secret"})

        response = client.get("/v1/admin/export?streams_limit=1&stream_events_limit=1")

        assert response.status_code == 200
        assert events
        event, fields = events[0]
        assert event == "admin_export"
        assert fields["streams_count"] == 1
        assert fields["stream_events_count"] == 1
        assert fields["stream_events_limit"] == 1
        assert "people" not in fields
        assert "streams" not in fields
        assert "thresholds" not in fields
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_admin_export_fails_closed_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_admin, "audit_event", fail_audit)

    response = client.get("/v1/admin/export")

    assert response.status_code == 503
    assert "状态写入失败" in v1_error_message(response)


def test_stream_lists_and_admin_export_are_paginated() -> None:
    client = TestClient(app)
    snapshot = dict(STREAMS)
    STREAMS.clear()
    try:
        streams = [
            create_stream(f"http://example.com/live-{index}") for index in range(3)
        ]
        for index in range(5):
            streams[0].add_event("debug", f"event {index}")

        listed = client.get("/v1/streams?limit=2&offset=1")
        assert listed.status_code == 200
        data = listed.json()["data"]
        assert data["count"] == 2
        assert data["total"] == 3
        assert data["limit"] == 2
        assert data["offset"] == 1
        assert data["next_offset"] is None

        events = client.get(
            f"/v1/streams/{streams[0].stream_id}/events?limit=2&offset=1"
        )
        assert events.status_code == 200
        event_data = events.json()["data"]
        assert event_data["count"] == 2
        assert event_data["total"] == len(streams[0].events)
        assert event_data["next_offset"] == 3

        export = client.get("/v1/admin/export?streams_limit=1&stream_events_limit=2")
        assert export.status_code == 200
        export_data = export.json()["data"]
        assert len(export_data["streams"]) == 1
        exported_stream = export_data["streams"][0]
        assert len(exported_stream["events"]) <= 2
        assert export_data["pagination"]["streams"]["total"] == 3
        assert exported_stream["events_pagination"]["limit"] == 2
        assert exported_stream["events_pagination"]["count"] == len(
            exported_stream["events"]
        )
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_list_limit_rejects_unbounded_requests() -> None:
    client = TestClient(app)

    streams = client.get("/v1/streams?limit=501")
    events = client.get("/v1/streams/str_missing/events?limit=201")
    export = client.get("/v1/admin/export?people_limit=501")

    assert streams.status_code == 422
    assert events.status_code == 422
    assert export.status_code == 422


