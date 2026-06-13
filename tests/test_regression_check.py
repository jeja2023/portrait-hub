from pathlib import Path

import pytest

from tools.regression_check import CompareResult, compare_values, load_expected, open_case_files, run_case


def test_compare_values_accepts_subset_with_float_tolerance() -> None:
    result = CompareResult()

    compare_values(
        actual={"status": "success", "score": 0.901, "items": [{"name": "person", "box": [1.0, 2.0]}]},
        expected={"status": "success", "score": 0.9, "items": [{"name": "person"}]},
        result=result,
        tolerance=0.01,
    )

    assert result.ok


def test_compare_values_reports_missing_key() -> None:
    result = CompareResult()

    compare_values(actual={"status": "success"}, expected={"missing": True}, result=result)

    assert not result.ok
    assert "$.missing: missing key" in result.errors


def test_compare_values_redacts_sensitive_values_in_errors() -> None:
    result = CompareResult()

    compare_values(
        actual={"metadata": {"token": "actual-secret", "note": "actual-visible"}},
        expected={"metadata": {"token": "expected-secret", "note": "expected-visible"}},
        result=result,
    )

    assert not result.ok
    error_text = "\n".join(result.errors)
    assert "$.metadata.token" in error_text
    assert "actual-secret" not in error_text
    assert "expected-secret" not in error_text
    assert "<redacted>" in error_text


class FakeRegressionResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"status": "ok"}


class FakeRegressionClient:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return FakeRegressionResponse()


def test_run_case_sends_default_tenant_and_token_headers() -> None:
    client = FakeRegressionClient()

    actual = run_case(client, "http://testserver", "token", "tenant-a", {"path": "/v1/admin/status"}, Path("."))

    assert actual == {"status_code": 200, "payload": {"status": "ok"}}
    headers = client.calls[0]["headers"]
    assert headers["X-Tenant-ID"] == "tenant-a"
    assert headers["Authorization"] == "Bearer token"
    assert headers["X-API-Key"] == "token"


def test_run_case_preserves_explicit_tenant_header() -> None:
    client = FakeRegressionClient()

    run_case(
        client,
        "http://testserver",
        None,
        "tenant-a",
        {"path": "/v1/admin/status", "headers": {"X-Tenant-ID": "tenant-b"}},
        Path("."),
    )

    assert client.calls[0]["headers"]["X-Tenant-ID"] == "tenant-b"


def test_load_expected_rejects_paths_outside_manifest_dir(workspace_tmp_path: Path) -> None:
    base_dir = workspace_tmp_path / "manifest"
    base_dir.mkdir()

    with pytest.raises(ValueError, match="case.expected_path must stay within"):
        load_expected({"expected_path": "../outside.json"}, base_dir)


def test_open_case_files_rejects_paths_outside_manifest_dir(workspace_tmp_path: Path) -> None:
    base_dir = workspace_tmp_path / "manifest"
    base_dir.mkdir()

    with pytest.raises(ValueError, match="case.files.files must stay within"):
        open_case_files({"files": {"files": "../outside.jpg"}}, base_dir)


def test_open_case_files_closes_handles_when_later_file_fails(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_dir = workspace_tmp_path / "manifest"
    base_dir.mkdir()
    opened = []

    class TrackingHandle:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    original_open = Path.open

    def fake_open(self: Path, mode: str = "r", *args, **kwargs):
        if self.name == "inside.jpg":
            handle = TrackingHandle()
            opened.append(handle)
            return handle
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)

    with pytest.raises(ValueError, match="case.files.files must stay within"):
        open_case_files({"files": {"files": ["inside.jpg", "../outside.jpg"]}}, base_dir)

    assert len(opened) == 1
    assert opened[0].closed


def test_load_expected_accepts_manifest_relative_file(workspace_tmp_path: Path) -> None:
    base_dir = workspace_tmp_path / "manifest"
    base_dir.mkdir()
    expected = base_dir / "expected.json"
    expected.write_text('{"status":"ok"}', encoding="utf-8")

    assert load_expected({"expected_path": "expected.json"}, base_dir) == {"status": "ok"}
