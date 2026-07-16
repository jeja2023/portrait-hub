from io import BytesIO

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app import (
    portrait_gallery,
    routes_portrait_admin,
)
from app.portrait_gallery import GALLERY, gallery_key
from app.portrait_jobs import VIDEO_JOBS, VideoJob, get_video_job, job_key
from app.portrait_streams import STREAMS, StreamEvent, create_stream, stream_key
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


def test_retention_cleanup_persists_trimmed_streams(monkeypatch) -> None:
    client = TestClient(app)
    snapshot = dict(STREAMS)
    STREAMS.clear()
    persisted = []
    monkeypatch.setattr(
        "app.routes_portrait_admin.persist_stream",
        lambda stream: persisted.append(stream.stream_id),
    )
    try:
        stream = create_stream("http://example.com/live")
        stream.events.append(
            StreamEvent(event_id="evt_old", type="old", message="old", created_at=0.0)
        )

        cleanup = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})

        assert cleanup.status_code == 200
        assert cleanup.json()["data"]["trimmed_stream_events"] >= 1
        assert stream.stream_id in persisted
    finally:
        STREAMS.clear()
        STREAMS.update(snapshot)


def test_retention_cleanup_removes_expired_gallery_people_and_objects(
    monkeypatch,
) -> None:
    client = TestClient(app)
    gallery_snapshot = dict(GALLERY)
    GALLERY.clear()
    deleted_objects = []
    audit_events = []
    old_person = portrait_gallery.PersonRecord(
        tenant_id="default",
        person_id="p_retention_old",
        display_name=None,
        metadata={},
        created_at=0.0,
        updated_at=0.0,
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_retention_object",
                modality="body",
                embedding=[1.0, 0.0],
                embedding_dim=2,
                model_id="model",
                model_version="v1",
                quality_score=1.0,
                source_id="source",
                created_at=0.0,
                object_info={
                    "backend": "local_file",
                    "object_key": "default/gallery-image/old-secret.json",
                },
            )
        ],
    )
    other_tenant = portrait_gallery.PersonRecord(
        tenant_id="tenant-b",
        person_id="p_retention_other_tenant",
        display_name=None,
        metadata={},
        created_at=0.0,
        updated_at=0.0,
    )
    GALLERY[gallery_key("default", old_person.person_id)] = old_person
    GALLERY[gallery_key("tenant-b", other_tenant.person_id)] = other_tenant

    class TrackingObjectStore:
        def delete_object(self, info):
            deleted_objects.append(info["object_key"])
            return {"backend": "local_file", "deleted": True}

        def health(self):
            return {"backend": "local_file", "status": "ready"}

    monkeypatch.setattr(routes_portrait_admin, "OBJECT_STORE", TrackingObjectStore())
    monkeypatch.setattr(
        routes_portrait_admin,
        "audit_event",
        lambda event, **fields: audit_events.append((event, fields)),
    )
    monkeypatch.setattr(
        portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None
    )
    try:
        cleanup = client.post("/v1/admin/retention/cleanup", json={"retention_days": 0})

        assert cleanup.status_code == 200
        data = cleanup.json()["data"]
        assert data["removed_gallery_people"] == 1
        assert data["deleted_gallery_objects"] == 1
        assert deleted_objects == ["default/gallery-image/old-secret.json"]
        assert gallery_key("default", "p_retention_old") not in GALLERY
        assert gallery_key("tenant-b", "p_retention_other_tenant") in GALLERY
        event_name, event_payload = audit_events[0]
        assert event_name == "retention_cleanup"
        assert event_payload["outcome"] == "started"
        assert event_payload["candidate_gallery_people"] == 1
        assert event_payload["candidate_gallery_feature_count"] == 1
        assert event_payload["candidate_gallery_object_reference_count"] == 1
    finally:
        GALLERY.clear()
        GALLERY.update(gallery_snapshot)


def test_retention_cleanup_rolls_back_gallery_person_when_object_cleanup_fails(
    monkeypatch,
) -> None:
    client = TestClient(app)
    gallery_snapshot = dict(GALLERY)
    GALLERY.clear()
    person = portrait_gallery.PersonRecord(
        tenant_id="default",
        person_id="p_retention_object_fail",
        display_name="Retain Me",
        metadata={},
        created_at=0.0,
        updated_at=0.0,
        features=[
            portrait_gallery.FeatureRecord(
                feature_id="f_retention_fail",
                modality="body",
                embedding=[1.0, 0.0],
                embedding_dim=2,
                model_id="model",
                model_version="v1",
                quality_score=1.0,
                source_id="source",
                created_at=0.0,
                object_info={
                    "backend": "local_file",
                    "object_key": "default/gallery-image/secret-object.json",
                },
            )
        ],
    )
    GALLERY[gallery_key("default", person.person_id)] = person

    class FailingObjectStore:
        def delete_object(self, info):
            return {
                "backend": "local_file",
                "deleted": False,
                "reason": "secret 对象删除失败",
                "object_key": info["object_key"],
            }

        def health(self):
            return {"backend": "local_file", "status": "ready"}

    monkeypatch.setattr(routes_portrait_admin, "OBJECT_STORE", FailingObjectStore())
    monkeypatch.setattr(
        routes_portrait_admin, "audit_event", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        portrait_gallery, "persist_person_delete", lambda tenant_id, person_id: None
    )
    monkeypatch.setattr(
        routes_portrait_admin, "persist_person", lambda restored_person: None
    )
    monkeypatch.setattr(
        routes_portrait_admin, "persist_feature", lambda restored_person, feature: None
    )
    try:
        response = client.post(
            "/v1/admin/retention/cleanup", json={"retention_days": 0}
        )

        assert response.status_code == 503
        assert v1_error_message(response) == "对象清理失败"
        assert gallery_key("default", "p_retention_object_fail") in GALLERY
        restored = GALLERY[gallery_key("default", "p_retention_object_fail")]
        assert restored.display_name == "Retain Me"
        assert (
            restored.features[0]
            .object_info["object_key"]
            .endswith("secret-object.json")
        )
        assert "secret-object" not in response.text
        assert "object_key" not in response.text
    finally:
        GALLERY.clear()
        GALLERY.update(gallery_snapshot)


def test_retention_cleanup_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    job_snapshot = dict(VIDEO_JOBS)
    stream_snapshot = dict(STREAMS)
    VIDEO_JOBS.clear()
    STREAMS.clear()
    monkeypatch.setattr(
        "app.portrait_jobs.delete_video_job", lambda tenant_id, job_id: None
    )
    monkeypatch.setattr(routes_portrait_admin, "persist_stream", lambda stream: None)
    monkeypatch.setattr(routes_portrait_admin, "persist_video_job", lambda job: None)

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_admin, "audit_event", fail_audit)
    try:
        job = VideoJob(
            job_id="job_retention_old",
            tenant_id="default",
            filename="old.mp4",
            updated_at=0.0,
        )
        VIDEO_JOBS[job_key("default", job.job_id)] = job
        stream = create_stream("http://example.com/rollback-audit")
        stream.events = [
            StreamEvent(event_id="evt_old", type="old", message="old", created_at=0.0),
            StreamEvent(
                event_id="evt_keep",
                type="keep",
                message="keep",
                created_at=999999999999.0,
            ),
        ]

        response = client.post(
            "/v1/admin/retention/cleanup", json={"retention_days": 0}
        )

        assert response.status_code == 503
        assert VIDEO_JOBS[job_key("default", job.job_id)].filename == "old.mp4"
        assert [
            event.event_id
            for event in STREAMS[stream_key("default", stream.stream_id)].events
        ] == [
            "evt_old",
            "evt_keep",
        ]
    finally:
        VIDEO_JOBS.clear()
        VIDEO_JOBS.update(job_snapshot)
        STREAMS.clear()
        STREAMS.update(stream_snapshot)


def test_retention_cleanup_rolls_back_when_stream_persist_fails(monkeypatch) -> None:
    client = TestClient(app)
    job_snapshot = dict(VIDEO_JOBS)
    stream_snapshot = dict(STREAMS)
    VIDEO_JOBS.clear()
    STREAMS.clear()
    monkeypatch.setattr(
        "app.portrait_jobs.delete_video_job", lambda tenant_id, job_id: None
    )
    monkeypatch.setattr(routes_portrait_admin, "persist_video_job", lambda job: None)

    def fail_persist_stream(stream):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_admin, "persist_stream", fail_persist_stream)
    try:
        job = VideoJob(
            job_id="job_retention_persist",
            tenant_id="default",
            filename="old.mp4",
            updated_at=0.0,
        )
        VIDEO_JOBS[job_key("default", job.job_id)] = job
        stream = create_stream("http://example.com/rollback-persist")
        stream.events = [
            StreamEvent(event_id="evt_old", type="old", message="old", created_at=0.0),
            StreamEvent(
                event_id="evt_keep",
                type="keep",
                message="keep",
                created_at=999999999999.0,
            ),
        ]

        response = client.post(
            "/v1/admin/retention/cleanup", json={"retention_days": 0}
        )

        assert response.status_code == 500
        assert v1_error_message(response) == "保留清理失败，且回滚持久化失败"
        assert v1_error_details(response) == {
            "rollback_failed": True,
            "rollback_error_count": 1,
        }
        assert "状态写入失败" not in response.text
        assert VIDEO_JOBS[job_key("default", job.job_id)].filename == "old.mp4"
        assert [
            event.event_id
            for event in STREAMS[stream_key("default", stream.stream_id)].events
        ] == [
            "evt_old",
            "evt_keep",
        ]
    finally:
        VIDEO_JOBS.clear()
        VIDEO_JOBS.update(job_snapshot)
        STREAMS.clear()
        STREAMS.update(stream_snapshot)


def test_v1_job_and_stream_state_use_tenant_scoped_keys() -> None:
    job_snapshot = dict(VIDEO_JOBS)
    stream_snapshot = dict(STREAMS)
    VIDEO_JOBS.clear()
    STREAMS.clear()
    try:
        VIDEO_JOBS[job_key("tenant-a", "job_same")] = VideoJob(
            job_id="job_same", tenant_id="tenant-a", filename="a.mp4"
        )
        VIDEO_JOBS[job_key("tenant-b", "job_same")] = VideoJob(
            job_id="job_same", tenant_id="tenant-b", filename="b.mp4"
        )

        assert get_video_job("job_same") is None
        assert get_video_job("job_same", tenant_id="tenant-a").filename == "a.mp4"
        assert get_video_job("job_same", tenant_id="tenant-b").filename == "b.mp4"

        stream_a = create_stream("http://example.com/a", tenant_id="tenant-a")
        stream_b = create_stream("http://example.com/b", tenant_id="tenant-b")
        stream_b.stream_id = stream_a.stream_id
        STREAMS.clear()
        STREAMS[stream_key("tenant-a", stream_a.stream_id)] = stream_a
        STREAMS[stream_key("tenant-b", stream_b.stream_id)] = stream_b

        assert (
            STREAMS[stream_key("tenant-a", stream_a.stream_id)].tenant_id == "tenant-a"
        )
        assert (
            STREAMS[stream_key("tenant-b", stream_a.stream_id)].tenant_id == "tenant-b"
        )
    finally:
        VIDEO_JOBS.clear()
        VIDEO_JOBS.update(job_snapshot)
        STREAMS.clear()
        STREAMS.update(stream_snapshot)
