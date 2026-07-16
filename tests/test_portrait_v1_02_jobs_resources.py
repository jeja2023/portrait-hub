from io import BytesIO

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app import (
    routes_portrait_jobs,
)
from app.portrait_gallery import GALLERY
from app.portrait_jobs import VIDEO_JOBS, VideoJob, job_key
from app.portrait_streams import STREAMS
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


def test_v1_video_job_create_rolls_back_job_when_queue_fails(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()

    class FailingTaskQueue:
        def enqueue(self, queue, payload):
            raise HTTPException(status_code=503, detail="任务队列写入失败")

    async def fake_stage_video_upload(file, tenant_id, job_id):
        return f"test/{job_id}.mp4"

    monkeypatch.setattr(routes_portrait_jobs, "TASK_QUEUE", FailingTaskQueue())
    monkeypatch.setattr(
        routes_portrait_jobs, "stage_video_upload", fake_stage_video_upload
    )

    response = client.post(
        "/v1/jobs/video",
        files={"file": ("video.mp4", b"fake", "video/mp4")},
    )

    assert response.status_code == 503
    assert VIDEO_JOBS == {}


def test_v1_video_job_create_rolls_back_job_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()

    class CapturingTaskQueue:
        def enqueue(self, queue, payload):
            class Message:
                def public_dict(self):
                    return {
                        "message_id": "msg_test",
                        "queue": queue,
                        "payload": payload,
                        "status": "queued",
                    }

            return Message()

    async def fake_stage_video_upload(file, tenant_id, job_id):
        return f"test/{job_id}.mp4"

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_jobs, "TASK_QUEUE", CapturingTaskQueue())
    monkeypatch.setattr(
        routes_portrait_jobs, "stage_video_upload", fake_stage_video_upload
    )
    monkeypatch.setattr(routes_portrait_jobs, "audit_event", fail_audit)
    monkeypatch.setattr(
        "app.portrait_jobs.delete_video_job", lambda tenant_id, job_id: None
    )

    response = client.post(
        "/v1/jobs/video",
        files={"file": ("video.mp4", b"fake", "video/mp4")},
    )

    assert response.status_code == 503
    assert VIDEO_JOBS == {}


def test_v1_video_job_create_response_does_not_echo_source_filename(
    monkeypatch,
) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    captured_payloads = []

    class CapturingTaskQueue:
        def enqueue(self, queue, payload):
            captured_payloads.append(payload)

            class Message:
                def public_dict(self):
                    return {
                        "message_id": "msg_test",
                        "queue": queue,
                        "payload": payload,
                        "status": "queued",
                    }

            return Message()

    async def fake_stage_video_upload(file, tenant_id, job_id):
        return f"test/{job_id}.mp4"

    monkeypatch.setattr(routes_portrait_jobs, "TASK_QUEUE", CapturingTaskQueue())
    monkeypatch.setattr(
        routes_portrait_jobs, "stage_video_upload", fake_stage_video_upload
    )
    monkeypatch.setattr(
        routes_portrait_jobs, "audit_event", lambda *args, **kwargs: None
    )

    response = client.post(
        "/v1/jobs/video",
        files={"file": ("secret-person-name.mp4", b"fake", "video/mp4")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "filename" not in data["job"]
    assert "filename" not in data["queue_message"]["payload"]
    assert "filename" not in captured_payloads[0]
    assert "secret-person-name" not in response.text
    assert next(iter(VIDEO_JOBS.values())).filename is None


def test_v1_video_job_cancel_rolls_back_when_audit_fails(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    job = VideoJob(
        job_id="job_cancel_audit",
        tenant_id="default",
        filename="video.mp4",
        status="queued",
    )
    VIDEO_JOBS[job_key("default", job.job_id)] = job

    def fail_audit(*args, **kwargs):
        raise HTTPException(status_code=503, detail="状态写入失败")

    monkeypatch.setattr(routes_portrait_jobs, "audit_event", fail_audit)
    monkeypatch.setattr(
        routes_portrait_jobs, "persist_video_job", lambda restored_job: None
    )

    response = client.post(f"/v1/jobs/{job.job_id}/cancel")

    assert response.status_code == 503
    stored = VIDEO_JOBS[job_key("default", job.job_id)]
    assert stored.status == "queued"
    assert stored.cancel_requested is False


def test_v1_video_job_results_lists_jobs_with_available_thumbnails(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    completed = VideoJob(
        job_id="job_video_done",
        tenant_id="default",
        filename="video.mp4",
        status="completed",
    )
    completed.result = {
        "metadata": {"filename": "video.mp4"},
        "frame_count": 1,
        "analysis_mode": "async_media_fallback",
        "frames": [
            {
                "frame_index": 0,
                "source_frame_index": 0,
                "width": 64,
                "height": 64,
                "thumbnail": "data:image/jpeg;base64,abcd",
                "quality": {"score": 0.9},
                "appearance": {"quality": {"score": 0.9}},
                "embedding_dim": 64,
            }
        ],
    }
    running = VideoJob(
        job_id="job_video_running",
        tenant_id="default",
        filename="video.mp4",
        status="running",
        updated_at=completed.updated_at + 1,
    )
    running.result = {
        "metadata": {"filename": "video.mp4"},
        "frame_count": 1,
        "frames_available": 1,
        "partial": True,
        "analysis_mode": "person_tracks",
        "frames": [
            {
                "frame_index": 0,
                "source_frame_index": 3,
                "width": 64,
                "height": 64,
                "thumbnail": "data:image/jpeg;base64,efgh",
            }
        ],
    }
    VIDEO_JOBS[job_key(completed.tenant_id, completed.job_id)] = completed
    VIDEO_JOBS[job_key(running.tenant_id, running.job_id)] = running

    response = client.get("/v1/jobs/video/results")

    assert response.status_code == 200
    data = response.json()["data"]
    ids = [item["job"]["job_id"] for item in data["results"]]
    assert ids[:2] == ["job_video_running", "job_video_done"]
    assert data["results"][0]["result"]["partial"] is True
    assert data["results"][0]["result"]["frames"][0]["thumbnail"].startswith(
        "data:image/jpeg;base64,"
    )


def test_v1_video_job_not_found_does_not_echo_job_id() -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    secret_job_id = "job_secret_token"

    responses = [
        client.get(f"/v1/jobs/{secret_job_id}"),
        client.get(f"/v1/jobs/{secret_job_id}/result"),
        client.post(f"/v1/jobs/{secret_job_id}/cancel"),
    ]

    for response in responses:
        assert response.status_code == 404
        assert v1_error_message(response) == "任务不存在"
        assert secret_job_id not in response.text


def test_v1_gallery_and_stream_not_found_do_not_echo_resource_ids() -> None:
    client = TestClient(app)
    GALLERY.clear()
    STREAMS.clear()
    secret_person_id = "person_secret_token"
    secret_stream_id = "stream_secret_token"

    responses = [
        (client.get(f"/v1/gallery/{secret_person_id}"), "人员不存在", secret_person_id),
        (
            client.delete(f"/v1/gallery/{secret_person_id}"),
            "人员不存在",
            secret_person_id,
        ),
        (
            client.get(f"/v1/streams/{secret_stream_id}"),
            "视频流不存在",
            secret_stream_id,
        ),
        (
            client.post(f"/v1/streams/{secret_stream_id}/start"),
            "视频流不存在",
            secret_stream_id,
        ),
        (
            client.post(f"/v1/streams/{secret_stream_id}/stop"),
            "视频流不存在",
            secret_stream_id,
        ),
        (
            client.get(f"/v1/streams/{secret_stream_id}/status"),
            "视频流不存在",
            secret_stream_id,
        ),
        (
            client.get(f"/v1/streams/{secret_stream_id}/events"),
            "视频流不存在",
            secret_stream_id,
        ),
    ]

    for response, detail, secret in responses:
        assert response.status_code == 404
        assert v1_error_message(response) == detail
        assert secret not in response.text


def test_v1_job_and_stream_reject_invalid_resource_ids_without_echo() -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()
    STREAMS.clear()
    invalid_id = "bad id"
    encoded_id = "bad%20id"

    responses = [
        (client.get(f"/v1/jobs/{encoded_id}"), "job_id"),
        (client.get(f"/v1/jobs/{encoded_id}/result"), "job_id"),
        (client.post(f"/v1/jobs/{encoded_id}/cancel"), "job_id"),
        (client.get(f"/v1/streams/{encoded_id}"), "stream_id"),
        (client.post(f"/v1/streams/{encoded_id}/start"), "stream_id"),
        (client.post(f"/v1/streams/{encoded_id}/stop"), "stream_id"),
        (client.get(f"/v1/streams/{encoded_id}/status"), "stream_id"),
        (client.get(f"/v1/streams/{encoded_id}/events"), "stream_id"),
    ]

    for response, field_name in responses:
        assert response.status_code == 400
        assert field_name in v1_error_message(response)
        assert invalid_id not in response.text


def test_v1_video_job_rejects_out_of_range_numeric_controls(monkeypatch) -> None:
    client = TestClient(app)
    VIDEO_JOBS.clear()

    async def fake_stage_video_upload(file, tenant_id, job_id):
        return f"test/{job_id}.mp4"

    monkeypatch.setattr(
        routes_portrait_jobs, "stage_video_upload", fake_stage_video_upload
    )

    bad_interval = client.post(
        "/v1/jobs/video",
        files={"file": ("video.mp4", b"fake", "video/mp4")},
        data={"sample_interval_seconds": "-1"},
    )
    too_many_frames = client.post(
        "/v1/jobs/video",
        files={"file": ("video.mp4", b"fake", "video/mp4")},
        data={"batch_size": "999999"},
    )

    assert bad_interval.status_code == 400
    assert "sample_interval_seconds" in v1_error_message(bad_interval)
    assert too_many_frames.status_code == 400
    assert "batch_size 必须介于 1" in v1_error_message(too_many_frames)
    assert VIDEO_JOBS == {}


def test_v1_gallery_is_tenant_isolated() -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        headers={"x-tenant-id": "tenant:shared"},
        files=[upload("files", (210, 90, 20))],
        data={"person_id": "a:p", "modality": "body"},
    )
    assert enroll.status_code == 200

    collision = client.post(
        "/v1/gallery/enroll",
        headers={"x-tenant-id": "tenant"},
        files=[upload("files", (10, 30, 210))],
        data={"person_id": "shared:a:p", "modality": "body"},
    )
    assert collision.status_code == 200

    hidden = client.post(
        "/v1/gallery/search",
        headers={"x-tenant-id": "tenant-b"},
        files=[upload("file", (210, 90, 20))],
        data={"modality": "body"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["data"]["candidates"] == []

    visible = client.post(
        "/v1/gallery/search",
        headers={"x-tenant-id": "tenant:shared"},
        files=[upload("file", (210, 90, 20))],
        data={"modality": "body"},
    )
    assert visible.status_code == 200
    assert visible.json()["data"]["candidates"][0]["tenant_id"] == "tenant:shared"
    assert visible.json()["data"]["candidates"][0]["person_id"] == "a:p"


def test_v1_gallery_rejects_invalid_person_id() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 40, 60))],
        data={"person_id": "bad/person", "modality": "body"},
    )

    assert response.status_code == 400
    assert "person_id 必须" in v1_error_message(response)


def test_v1_gallery_public_response_redacts_metadata() -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 120, 60))],
        data={
            "person_id": "p_sensitive_metadata",
            "modality": "body",
            "metadata": '{"note":"ok","api_key":"secret-key","nested":{"token":"secret-token"}}',
        },
    )

    assert enroll.status_code == 200
    metadata = enroll.json()["data"]["person"]["metadata"]
    assert metadata["note"] == "ok"
    assert metadata["api_key"] == "<redacted>"
    assert metadata["nested"]["token"] == "<redacted>"


def test_v1_gallery_rejects_oversized_metadata() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 120, 60))],
        data={
            "person_id": "p_oversized_metadata",
            "modality": "body",
            "metadata": '{"note":"' + ("x" * 3000) + '"}',
        },
    )

    assert response.status_code == 400
    assert "metadata 字符串值过长" in v1_error_message(response)


def test_v1_gallery_patch_has_strict_schema() -> None:
    client = TestClient(app)
    GALLERY.clear()

    enroll = client.post(
        "/v1/gallery/enroll",
        files=[upload("files", (20, 120, 60))],
        data={"person_id": "p_patch_schema", "modality": "body"},
    )
    assert enroll.status_code == 200

    unknown = client.patch("/v1/gallery/p_patch_schema", json={"role": "admin"})
    empty = client.patch("/v1/gallery/p_patch_schema", json={})
    long_name = client.patch(
        "/v1/gallery/p_patch_schema", json={"display_name": "x" * 300}
    )

    assert unknown.status_code == 422
    assert empty.status_code == 400
    assert long_name.status_code == 422


