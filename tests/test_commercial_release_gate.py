from __future__ import annotations

import sys
from pathlib import Path

from tools.portrait_commercial_release_gate import evaluate_checks, existing_gate_commands, run_release_gate

ROOT = Path(__file__).resolve().parents[1]


def test_release_gate_fail_closes_on_any_check() -> None:
    result = evaluate_checks(
        [
            {"name": "one", "ok": True},
            {"name": "two", "ok": False, "errors": ["missing evidence"]},
        ]
    )

    assert result["ok"] is False
    assert result["decision"] == "block"
    assert result["blockers"] == [{"name": "two", "errors": ["missing evidence"]}]


def test_release_gate_command_set_matches_commercial_plan() -> None:
    commands = {name: command for name, command, _ in existing_gate_commands()}

    assert commands["requirement_traceability"] == [
        sys.executable,
        "tools/portrait_upgrade_traceability.py",
        "--release",
    ]
    assert commands["deploy_check"] == [sys.executable, "tools/deploy_check.py", "--json", "--import-app"]
    assert commands["production_readiness"][-1] == "--strict"
    assert "--strict-governance" in commands["model_governance"]
    assert "--strict-hash" in commands["model_governance"]
    assert "--strict-sidecars" in commands["model_governance"]
    assert commands["security_audit"][-2:] == ["--format", "json"]


def test_release_gate_blocks_current_unqualified_private_standard(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "tools.portrait_commercial_release_gate.verify_evidence_package",
        lambda *args, **kwargs: {"ok": True, "package_id": "evp-test"},
    )
    monkeypatch.setattr(
        "tools.portrait_commercial_release_gate.check_migrations",
        lambda: {"name": "database_migrations", "ok": True},
    )
    monkeypatch.setattr(
        "tools.portrait_commercial_release_gate.check_consistency",
        lambda *args, **kwargs: {"name": "commercial_consistency", "ok": True},
    )

    def passing_runner(name: str, command: list[str], root: Path, timeout: int) -> dict:
        return {"name": name, "ok": True}

    result = run_release_gate(
        root=ROOT,
        matrix_path=ROOT / "deploy" / "support-matrix.json",
        profile="private_standard",
        environment_id="production-a",
        evidence_package=tmp_path / "evidence.zip",
        public_key=tmp_path / "public.pem",
        command_runner=passing_runner,
    )

    assert result["ok"] is False
    assert result["decision"] == "block"
    assert result["checks"][0]["name"] == "support_matrix"
    assert result["checks"][0]["structurally_valid"] is True
    assert result["checks"][0]["commercial_ready"] is False
    assert result["checks"][0]["blockers"]
