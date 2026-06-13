import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from sdk.python.portrait_hub_client import PortraitHubClient, PortraitHubHTTPError


class DummyHeaders:
    def items(self):
        return [("Retry-After", "2")]


def test_python_sdk_raises_structured_http_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            DummyHeaders(),
            BytesIO(json.dumps({"detail": "rate limit exceeded"}).encode("utf-8")),
        )

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    client = PortraitHubClient("http://testserver", api_token="token", tenant_id="tenant-a")

    with pytest.raises(PortraitHubHTTPError) as exc_info:
        client.health()

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"detail": "rate limit exceeded"}
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
