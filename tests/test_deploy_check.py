from pathlib import Path

from tools.deploy_check import DeployReport, check_ci_workflows, check_docker_files


def test_deploy_report_redacts_sensitive_details() -> None:
    report = DeployReport()

    report.add(
        "app_import",
        False,
        {
            "error": "failed with token=secret-token and Authorization: Bearer bearer-secret",
            "nested": {"api_key": "secret-key", "safe": "visible"},
        },
    )

    encoded = str(report.checks)
    assert "secret-token" not in encoded
    assert "bearer-secret" not in encoded
    assert "secret-key" not in encoded
    assert "<redacted>" in encoded
    assert report.checks[0]["detail"]["nested"]["safe"] == "visible"


def test_deploy_check_tracks_stream_worker_service() -> None:
    report = DeployReport()

    check_docker_files(Path("."), report)

    checks = {item["name"]: item for item in report.checks}
    assert checks["compose_stream_worker_service"]["ok"] is True


def test_deploy_check_tracks_ci_workflows() -> None:
    report = DeployReport()

    check_ci_workflows(Path("."), report)

    checks = {item["name"]: item for item in report.checks}
    assert checks["ci_python_node_deploy_checks"]["ok"] is True
    assert checks["ci_security_audit_scheduled"]["ok"] is True


def test_security_audit_script_has_clear_missing_dependency_message() -> None:
    content = Path("tools/security_audit.py").read_text(encoding="utf-8")

    assert "importlib.util.find_spec" in content
    assert "pip-audit is not installed" in content
    assert "TMPDIR" in content
