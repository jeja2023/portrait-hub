from tools.service_smoke_test import SmokeReport, check_openapi, request_json


class FakeHTTPResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self):
        return b'{"status":"healthy"}'


def test_request_json_sends_bearer_auth_by_default(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr("tools.service_smoke_test.urlopen", fake_urlopen)

    status, payload = request_json("http://testserver", "/health", "token", 3.0, tenant_id="tenant-a")

    assert status == 200
    assert payload == {"status": "healthy"}
    assert captured["headers"]["X-tenant-id"] == "tenant-a"
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert "X-api-key" not in captured["headers"]
    assert captured["timeout"] == 3.0


def test_request_json_can_send_application_api_key(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        return FakeHTTPResponse()

    monkeypatch.setattr("tools.service_smoke_test.urlopen", fake_urlopen)

    status, payload = request_json("http://testserver", "/health", "phk_secret", 3.0, tenant_id="tenant-a", auth_scheme="api-key")

    assert status == 200
    assert payload == {"status": "healthy"}
    assert captured["headers"]["X-api-key"] == "phk_secret"
    assert captured["headers"]["X-tenant-id"] == "tenant-a"
    assert "Authorization" not in captured["headers"]


def test_smoke_report_redacts_sensitive_detail() -> None:
    report = SmokeReport()

    report.add("failed", False, {"api_key": "secret-key", "nested": {"token": "secret-token"}, "safe": "visible"})

    detail = report.checks[0]["detail"]
    assert detail["api_key"] == "<redacted>"
    assert detail["nested"]["token"] == "<redacted>"
    assert detail["safe"] == "visible"


def test_openapi_smoke_allows_disabled_docs_by_default(monkeypatch) -> None:
    monkeypatch.setattr("tools.service_smoke_test.request_json", lambda *args: (404, {"detail": "Not Found"}))
    report = SmokeReport()

    check_openapi(report, "http://testserver", None, 1.0, required=False)

    assert report.ok
    assert report.checks[0]["name"] == "openapi"
    assert report.checks[0]["detail"]["status"] == 404
    assert report.checks[1]["name"] == "openapi_optional"


def test_openapi_smoke_strict_mode_requires_document(monkeypatch) -> None:
    monkeypatch.setattr("tools.service_smoke_test.request_json", lambda *args: (404, {"detail": "Not Found"}))
    report = SmokeReport()

    check_openapi(report, "http://testserver", None, 1.0, required=True)

    assert not report.ok
    assert report.checks[0]["name"] == "openapi"
    assert report.checks[0]["ok"] is False
    assert report.checks[1]["name"] == "openapi_required_paths"
    assert report.checks[1]["ok"] is False


def test_openapi_smoke_strict_mode_checks_core_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.service_smoke_test.request_json",
        lambda *args: (200, {"paths": {"/health": {}, "/ready": {}}}),
    )
    report = SmokeReport()

    check_openapi(report, "http://testserver", None, 1.0, required=True)

    assert not report.ok
    assert report.checks[0]["ok"] is True
    assert "/predict" in report.checks[1]["detail"]["missing"]
