from __future__ import annotations

import hashlib
import hmac
import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from sdk.python.portrait_hub_client import (
    SDK_VERSION,
    PortraitHubClient,
    PortraitHubServerError,
)


class _Headers:
    def items(self):
        return []


class _Response:
    headers = _Headers()

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _server_error(request) -> HTTPError:
    return HTTPError(
        request.full_url,
        503,
        "unavailable",
        _Headers(),
        BytesIO(b'{"error":{"code":"unavailable"}}'),
    )


def test_sdk_retries_safe_requests_but_not_unkeyed_writes(monkeypatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.get_method())
        if len(calls) == 1:
            raise _server_error(request)
        return _Response({"status": "ok"})

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    monkeypatch.setattr("sdk.python.portrait_hub_client.time.sleep", lambda _: None)
    client = PortraitHubClient("http://testserver", max_retries=2, retry_backoff_seconds=0)

    assert client.health() == {"status": "ok"}
    assert calls == ["GET", "GET"]

    calls.clear()
    with pytest.raises(PortraitHubServerError):
        client._json("POST", "/v1/example", {"value": 1})
    assert calls == ["POST"]


def test_sdk_retries_keyed_writes_with_same_request_identity(monkeypatch) -> None:
    captured: list[dict[str, str]] = []

    def fake_urlopen(request, timeout):
        captured.append({key.lower(): value for key, value in request.header_items()})
        if len(captured) == 1:
            raise _server_error(request)
        return _Response({"status": "ok"})

    monkeypatch.setattr("sdk.python.portrait_hub_client.urllib_request.urlopen", fake_urlopen)
    monkeypatch.setattr("sdk.python.portrait_hub_client.time.sleep", lambda _: None)
    client = PortraitHubClient("http://testserver", max_retries=1, retry_backoff_seconds=0)

    assert client._json("POST", "/v1/example", {"value": 1}, idempotency_key="idem-1") == {"status": "ok"}
    assert len(captured) == 2
    assert captured[0]["idempotency-key"] == "idem-1"
    assert captured[0]["x-request-id"] == captured[1]["x-request-id"]
    assert captured[0]["x-portraithub-sdk-version"] == SDK_VERSION


def test_resumable_upload_skips_matching_parts_and_completes(monkeypatch, workspace_tmp_path: Path) -> None:
    video = workspace_tmp_path / "sample.mp4"
    video.write_bytes(b"abcdefghij")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    client = PortraitHubClient("http://testserver")
    uploaded: list[tuple[str, bytes, dict[str, str], str | None]] = []

    def fake_get(path, params=None):
        assert path == "/v1/uploads/video/upl_1"
        return {
            "data": {
                "upload": {
                    "upload_id": "upl_1",
                    "total_bytes": 10,
                    "sha256": digest,
                    "parts": [
                        {
                            "offset": 0,
                            "size": 4,
                            "sha256": hashlib.sha256(b"abcd").hexdigest(),
                        }
                    ],
                }
            }
        }

    def fake_bytes(method, path, data, *, headers=None, idempotency_key=None):
        uploaded.append((path, data, headers or {}, idempotency_key))
        return {"data": {"upload": {"upload_id": "upl_1", "total_bytes": 10, "sha256": digest}}}

    def fake_json(method, path, payload=None, *, idempotency_key=None):
        assert path == "/v1/uploads/video/upl_1/complete"
        assert payload == {"priority": 10, "include_embeddings": False}
        assert idempotency_key == "resume-key-complete"
        return {
            "data": {
                "upload": {"upload_id": "upl_1", "status": "completed"},
                "job": {"job_id": "job_1", "status": "queued"},
                "idempotent_replay": False,
            }
        }

    monkeypatch.setattr(client, "_get", fake_get)
    monkeypatch.setattr(client, "_bytes", fake_bytes)
    monkeypatch.setattr(client, "_json", fake_json)

    result = client.upload_video_resumable(
        video,
        chunk_size=4,
        upload_id="upl_1",
        idempotency_key="resume-key",
        priority=10,
    )

    assert result["job"]["job_id"] == "job_1"
    assert [item[1] for item in uploaded] == [b"efgh", b"ij"]
    assert [item[2]["X-Chunk-Offset"] for item in uploaded] == ["4", "8"]
    assert [item[3] for item in uploaded] == ["resume-key-part-2", "resume-key-part-3"]


def test_job_iterator_and_waiter_handle_wrapped_contract(monkeypatch) -> None:
    client = PortraitHubClient("http://testserver")

    def fake_list_jobs(**kwargs):
        if kwargs.get("cursor") is None:
            return {"data": {"items": [{"job_id": "job_1", "status": "queued"}], "next_cursor": "next"}}
        return {"data": {"items": [{"job_id": "job_2", "status": "completed"}], "next_cursor": None}}

    states = iter(["running", "completed"])
    monkeypatch.setattr(client, "list_jobs", fake_list_jobs)
    monkeypatch.setattr(
        client,
        "get_job",
        lambda job_id: {"data": {"job": {"job_id": job_id, "status": next(states)}}},
    )
    monkeypatch.setattr("sdk.python.portrait_hub_client.time.sleep", lambda _: None)

    assert [job["job_id"] for job in client.iter_jobs(page_size=2)] == ["job_1", "job_2"]
    assert client.wait_for_job("job_1", timeout=1, poll_interval=0)["status"] == "completed"


def test_webhook_signature_verification_enforces_time_window() -> None:
    payload = b'{"event":"video.completed"}'
    secret = "whsec_test"
    timestamp = 1_700_000_000
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        str(timestamp).encode("ascii") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()

    assert PortraitHubClient.verify_webhook_signature(
        payload,
        signature,
        secret,
        timestamp=timestamp,
        now=timestamp + 30,
    )
    assert not PortraitHubClient.verify_webhook_signature(
        payload,
        signature,
        secret,
        timestamp=timestamp,
        now=timestamp + 301,
    )
    assert not PortraitHubClient.verify_webhook_signature(
        payload + b" ",
        signature,
        secret,
        timestamp=timestamp,
        now=timestamp,
    )


def test_compatibility_check_returns_range_and_warns(monkeypatch) -> None:
    client = PortraitHubClient("http://testserver")
    monkeypatch.setattr(
        client,
        "api_metadata",
        lambda: {
            "data": {
                "api_contract": "v1",
                "service_version": "0.18.0",
                "supported_sdks": {
                    "python": {"minimum_version": "0.14.0", "maximum_version_exclusive": "1.0.0"}
                },
                "deprecations": [{"capability": "legacy", "message": "legacy is deprecated"}],
            }
        },
    )

    with pytest.warns(DeprecationWarning, match="legacy is deprecated"):
        result = client.check_compatibility()
    assert result["compatible"] is True
    assert result["api_contract"] == "v1"
