from __future__ import annotations

from subprocess import CompletedProcess

import tools.security_audit as security_audit
from tools.security_audit import build_command, build_local_command, is_temp_env_failure


def test_security_audit_builds_pip_audit_command() -> None:
    command = build_command(["requirements.txt", "requirements-prod-optional.txt"], "json", ".cache")

    assert command[1:3] == ["-m", "pip_audit"]
    assert "--progress-spinner" in command
    assert command.count("-r") == 2
    assert "--cache-dir" in command
    assert ".cache" in command
    assert command[-2:] == ["-f", "json"]


def test_security_audit_builds_local_fallback_command() -> None:
    command = build_local_command("json", ".cache")

    assert command[1:3] == ["-m", "pip_audit"]
    assert "--local" in command
    assert "--cache-dir" in command
    assert ".cache" in command
    assert command[-2:] == ["-f", "json"]


def test_security_audit_detects_temp_env_failures() -> None:
    assert is_temp_env_failure("VirtualEnvError: Couldn't execute in a temporary directory")
    assert is_temp_env_failure("PermissionError: [WinError 5]")
    assert not is_temp_env_failure("Found 1 known vulnerability")


def test_security_audit_auto_falls_back_to_local_on_temp_env_failure(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "--local" in command:
            return CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")
        return CompletedProcess(
            command,
            1,
            stdout="",
            stderr="VirtualEnvError: Couldn't execute in a temporary directory",
        )

    monkeypatch.setattr(security_audit.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(security_audit.subprocess, "run", fake_run)
    monkeypatch.setattr(security_audit.sys, "argv", ["security_audit.py"])

    assert security_audit.main() == 0
    assert any("--local" in command for command in calls)
