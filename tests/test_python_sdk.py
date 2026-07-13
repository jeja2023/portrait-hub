import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from sdk.python.portrait_hub_client import PortraitHubClient, PortraitHubHTTPError


class DummyHeaders:
    def items(self):
        return [("Retry-After", "2")]

class FakeOKResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self):
        return b'{"status":"ok"}'

def test_python_sdk_keeps_bearer_auth_default(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return FakeOKResponse()

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    client = PortraitHubClient("http://testserver", api_token="token", tenant_id="tenant-a")

    assert client.health() == {"status": "ok"}
    assert captured["headers"]["authorization"] == "Bearer token"
    assert captured["headers"]["x-tenant-id"] == "tenant-a"
    assert "x-api-key" not in captured["headers"]


def test_python_sdk_can_send_application_api_key_header(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return FakeOKResponse()

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    client = PortraitHubClient("http://testserver", api_token="phk_secret", auth_scheme="api_key", tenant_id="tenant-a")

    assert client.health() == {"status": "ok"}
    assert captured["headers"]["x-api-key"] == "phk_secret"
    assert captured["headers"]["x-tenant-id"] == "tenant-a"
    assert "authorization" not in captured["headers"]


def test_python_sdk_rejects_unknown_auth_scheme() -> None:
    with pytest.raises(ValueError, match="auth_scheme"):
        PortraitHubClient("http://testserver", auth_scheme="basic")


def test_python_sdk_raises_structured_http_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            DummyHeaders(),
            BytesIO(json.dumps({"detail": "已超过限流阈值"}).encode("utf-8")),
        )

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    client = PortraitHubClient("http://testserver", api_token="token", tenant_id="tenant-a")

    with pytest.raises(PortraitHubHTTPError) as exc_info:
        client.health()

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"detail": "已超过限流阈值"}
    assert exc_info.value.headers["Retry-After"] == "2"


def test_python_sdk_rejects_non_json_success_payload(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self):
            return b"plain text"

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", lambda *args, **kwargs: FakeResponse())
    client = PortraitHubClient("http://testserver")

    with pytest.raises(PortraitHubHTTPError) as exc_info:
        client.health()

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "plain text"


def test_python_sdk_encodes_dynamic_path_segments(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self):
            return b'{"status":"ok"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    client = PortraitHubClient("http://testserver")

    assert client.update_thresholds("Normal/Profile #1", {"body": 0.5}) == {"status": "ok"}
    assert captured["url"] == "http://testserver/v1/thresholds/Normal%2FProfile%20%231"


def test_python_sdk_jobs_streams_and_models_use_encoded_paths(monkeypatch) -> None:
    captured = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self):
            return b'{"status":"ok"}'

    def fake_urlopen(request, timeout):
        captured.append((request.method, request.full_url))
        return FakeResponse()

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    client = PortraitHubClient("http://testserver")

    assert client.get_job("job/a #1") == {"status": "ok"}
    assert client.stream_events("str/a #1", limit=2, cursor="abc/123") == {"status": "ok"}
    assert client.load_model("portrait_hub/yolov8n.onnx") == {"status": "ok"}

    assert captured == [
        ("GET", "http://testserver/v1/jobs/job%2Fa%20%231"),
        ("GET", "http://testserver/v1/streams/str%2Fa%20%231/events?limit=2&cursor=abc%2F123"),
        ("POST", "http://testserver/v1/models/portrait_hub%2Fyolov8n.onnx/load"),
    ]


def test_python_sdk_reindex_query_serializes_booleans(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self):
            return b'{"status":"ok"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return FakeResponse()

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    client = PortraitHubClient("http://testserver")

    assert client.reindex_gallery(modality="body", model_id="arc/face", dry_run=True) == {"status": "ok"}
    assert captured["url"] == "http://testserver/v1/gallery/reindex?modality=body&model_id=arc%2Fface&dry_run=true"


def test_python_sdk_gallery_and_compare_batch_methods_use_multipart_fields(monkeypatch) -> None:
    calls = []
    client = PortraitHubClient("http://testserver", api_token="token", tenant_id="tenant-a", auth_scheme="api_key")

    def fake_multipart(path, fields=None, files=None):
        calls.append({"path": path, "fields": fields, "files": files})
        return {"status": "ok"}

    monkeypatch.setattr(client, "_multipart", fake_multipart)

    assert client.search(Path("query.jpg"), modality="face", top_k=3, threshold_profile="strict") == {"status": "ok"}
    assert calls[-1] == {
        "path": "/v1/gallery/search",
        "fields": {"modality": "face", "top_k": 3, "threshold_profile": "strict"},
        "files": [("file", Path("query.jpg"))],
    }

    assert client.search_batch(
        [Path("query-a.jpg"), Path("query-b.jpg")],
        modality="body",
        top_k=10,
        threshold_profile="normal",
        async_mode=True,
    ) == {"status": "ok"}
    assert calls[-1] == {
        "path": "/v1/gallery/search/batch",
        "fields": {"modality": "body", "top_k": 10, "threshold_profile": "normal", "async_mode": True},
        "files": [("files", Path("query-a.jpg")), ("files", Path("query-b.jpg"))],
    }

    assert client.compare_batch(
        [Path("a1.jpg"), Path("a2.jpg")],
        [Path("b1.jpg"), Path("b2.jpg")],
        modality="appearance",
        threshold_profile="loose",
        include_vectors=True,
        async_mode=True,
    ) == {"status": "ok"}
    assert calls[-1] == {
        "path": "/v1/compare/batch",
        "fields": {
            "modality": "appearance",
            "threshold_profile": "loose",
            "include_vectors": True,
            "async_mode": True,
        },
        "files": [
            ("image_a", Path("a1.jpg")),
            ("image_a", Path("a2.jpg")),
            ("image_b", Path("b1.jpg")),
            ("image_b", Path("b2.jpg")),
        ],
    }

def test_python_sdk_escapes_multipart_header_values(monkeypatch, workspace_tmp_path: Path) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self):
            return b'{"status":"ok"}'

    def fake_urlopen(request, timeout):
        captured["body"] = request.data.decode("utf-8")
        return FakeResponse()

    image = workspace_tmp_path / "image.jpg"
    image.write_bytes(b"fake")
    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    client = PortraitHubClient("http://testserver")

    assert client._multipart("/upload", fields={'field"\r\nBad: yes': "value"}, files=[('file"\nBad: yes', image)]) == {"status": "ok"}

    header_blocks = [block.split("\r\n\r\n", 1)[0] for block in captured["body"].split("--portrait-hub-") if "Content-Disposition" in block]
    assert all("\r\nBad:" not in block for block in header_blocks)
    assert client._multipart_header_value('evil"\r\nX-Injected: yes.jpg') == 'evil\\"  X-Injected: yes.jpg'
    assert 'name="file\\" Bad: yes"' in captured["body"]
