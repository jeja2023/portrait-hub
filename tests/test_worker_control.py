import pytest

from tools.worker_control import auth_headers, request_worker, split_model_id


def test_split_model_id_accepts_project_model() -> None:
    assert split_model_id("project/model.onnx") == {
        "project_name": "project",
        "model_name": "model.onnx",
    }


def test_split_model_id_rejects_invalid_value() -> None:
    with pytest.raises(ValueError):
        split_model_id("model.onnx")


def test_split_model_id_rejects_path_like_segments() -> None:
    for value in [
        "project/nested/model.onnx",
        "../model.onnx",
        "project/..",
        "project/.",
        "project\\name/model.onnx",
        " project/model.onnx",
        "project/model.onnx ",
    ]:
        with pytest.raises(ValueError):
            split_model_id(value)


def test_auth_headers_include_default_tenant_without_token() -> None:
    assert auth_headers(None) == {"X-Tenant-ID": "default"}


def test_auth_headers_include_bearer_token_and_custom_tenant() -> None:
    assert auth_headers("token", "tenant-a") == {
        "X-Tenant-ID": "tenant-a",
        "Authorization": "Bearer token",
    }



def test_auth_headers_can_send_application_api_key() -> None:
    assert auth_headers("phk_secret", "tenant-a", "api-key") == {
        "X-Tenant-ID": "tenant-a",
        "X-API-Key": "phk_secret",
    }


def test_request_worker_redacts_sensitive_payload(monkeypatch) -> None:
    class FakeResponse:
        status_code = 500
        text = ""

        def json(self):
            return {"error": {"api_key": "secret-key", "token": "secret-token"}, "safe": "visible"}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, url, headers):
            return FakeResponse()

    class FakeHttpx:
        Client = FakeClient

    monkeypatch.setitem(__import__("sys").modules, "httpx", FakeHttpx)

    args = type(
        "Args",
        (),
        {"action": "health", "token": "token", "tenant_id": "tenant-a", "auth_scheme": "bearer", "timeout": 1.0, "model": []},
    )()
    result = request_worker("http://testserver", args)

    assert result["payload"]["error"]["api_key"] == "<redacted>"
    assert result["payload"]["error"]["token"] == "<redacted>"
    assert result["payload"]["safe"] == "visible"
