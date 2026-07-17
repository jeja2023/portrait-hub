from pathlib import Path

from tools.deploy_check import (
    DeployReport,
    check_ci_workflows,
    check_dependency_lock,
    check_docker_files,
    check_import_app,
    check_source_encoding,
    onnxruntime_version,
)


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


def test_deploy_check_enforces_single_stream_api() -> None:
    report = DeployReport()

    check_import_app(Path("."), report)

    checks = {item["name"]: item for item in report.checks}
    assert checks["app_required_routes"]["ok"] is True
    assert checks["app_removed_routes"]["ok"] is True


def test_deploy_check_tracks_stream_worker_service() -> None:
    report = DeployReport()

    check_docker_files(Path("."), report)

    checks = {item["name"]: item for item in report.checks}
    assert checks["compose_stream_worker_service"]["ok"] is True
    assert checks["dockerfile_copies_frontend"]["ok"] is True


def test_deploy_check_tracks_cpu_only_compose_contract() -> None:
    report = DeployReport()

    check_docker_files(Path("."), report)

    checks = {item["name"]: item for item in report.checks}
    assert checks["cpu_dockerfile_uses_cpu_runtime"]["ok"] is True
    assert checks["cpu_compose_services"]["ok"] is True
    assert checks["cpu_compose_force_cpu_is_literal"]["ok"] is True
    assert checks["cpu_compose_trusted_hosts_isolated"]["ok"] is True
    assert checks["cpu_compose_has_no_gpu_reservation"]["ok"] is True
    assert checks["cpu_compose_uses_cpu_dockerfile"]["ok"] is True


def test_deploy_check_tracks_ci_workflows() -> None:
    report = DeployReport()

    check_ci_workflows(Path("."), report)

    checks = {item["name"]: item for item in report.checks}
    assert checks["ci_python_node_deploy_checks"]["ok"] is True
    assert checks["ci_security_audit_scheduled"]["ok"] is True


def test_deploy_check_enforces_cpu_lock_and_runtime_parity() -> None:
    # CPU-only 部署清单/锁文件必须精确钉版，且 CPU 与 GPU 运行时锁定在同一 onnxruntime 版本。
    report = DeployReport()

    check_dependency_lock(Path("."), report)

    checks = {item["name"]: item for item in report.checks}
    assert checks["cpu_dependency_lock_exact"]["ok"] is True
    assert checks["cpu_gpu_runtime_parity"]["ok"] is True
    detail = checks["cpu_gpu_runtime_parity"]["detail"]
    assert detail["gpu_runtime"] == detail["cpu_runtime"]
    assert detail["gpu_runtime"] == detail["gpu_lock_runtime"] == detail["cpu_lock_runtime"]


def test_onnxruntime_version_distinguishes_cpu_and_gpu_packages() -> None:
    gpu_text = "onnxruntime-gpu==1.20.1\nnumpy==1.26.4\n"
    cpu_text = "onnxruntime==1.20.1\nnumpy==1.26.4\n"

    assert onnxruntime_version(gpu_text, "onnxruntime-gpu") == "1.20.1"
    # 裸包名查找不能误匹配 `-gpu` 包，避免子串陷阱。
    assert onnxruntime_version(gpu_text, "onnxruntime") is None
    assert onnxruntime_version(cpu_text, "onnxruntime") == "1.20.1"
    assert onnxruntime_version(cpu_text, "onnxruntime-gpu") is None


def test_security_audit_script_has_clear_missing_dependency_message() -> None:
    content = Path("tools/security_audit.py").read_text(encoding="utf-8")

    assert "importlib.util.find_spec" in content
    assert "未安装 pip-audit" in content
    assert "TMPDIR" in content


def test_deploy_check_reports_utf8_bom_files(workspace_tmp_path) -> None:
    source_dir = workspace_tmp_path / "app"
    source_dir.mkdir()
    (source_dir / "bad.py").write_bytes(b"\xef\xbb\xbfprint('bad')\n")
    (source_dir / "good.py").write_text("print('good')\n", encoding="utf-8")
    report = DeployReport()

    check_source_encoding(workspace_tmp_path, report)

    check = report.checks[0]
    assert check["name"] == "source_files_utf8_no_bom"
    assert check["ok"] is False
    assert check["detail"]["bom_files"] == ["app/bad.py"]
