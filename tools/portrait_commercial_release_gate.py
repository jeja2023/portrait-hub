from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.portrait_commercial_consistency import check_consistency
from tools.portrait_evidence_package import verify_evidence_package
from tools.portrait_release_preflight import check_migrations
from tools.portrait_support_matrix import load_matrix, matrix_status

CommandRunner = Callable[[str, list[str], Path, int], dict[str, Any]]


def existing_gate_commands() -> list[tuple[str, list[str], int]]:
    return [
        (
            "requirement_traceability",
            [sys.executable, "tools/portrait_upgrade_traceability.py", "--release"],
            60,
        ),
        ("deploy_check", [sys.executable, "tools/deploy_check.py", "--json", "--import-app"], 180),
        (
            "production_readiness",
            [sys.executable, "tools/portrait_production_readiness.py", "--strict"],
            180,
        ),
        ("governance", [sys.executable, "tools/governance_check.py", "--json"], 120),
        (
            "model_governance",
            [
                sys.executable,
                "tools/model_governance_check.py",
                "--strict-governance",
                "--strict-hash",
                "--strict-sidecars",
                "--allow-missing-artifacts",
                "--json",
            ],
            180,
        ),
        ("security_audit", [sys.executable, "tools/security_audit.py", "--format", "json"], 900),
    ]


def run_command(name: str, command: list[str], root: Path, timeout_seconds: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    detail: Any = None
    if stdout:
        try:
            detail = json.loads(stdout)
        except json.JSONDecodeError:
            detail = stdout[-4000:]
    return {
        "name": name,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "detail": detail,
        "stderr": stderr[-2000:] if stderr else "",
    }


def evaluate_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    blockers = [
        {
            "name": str(check.get("name") or "unknown"),
            "errors": check.get("errors") or check.get("blockers") or check.get("error") or check.get("detail"),
        }
        for check in checks
        if check.get("ok") is not True
    ]
    return {
        "ok": bool(checks) and not blockers,
        "decision": "release" if checks and not blockers else "block",
        "checks": checks,
        "blockers": blockers,
    }


def run_release_gate(
    *,
    root: Path,
    matrix_path: Path,
    profile: str,
    environment_id: str,
    evidence_package: Path,
    public_key: Path,
    command_runner: CommandRunner = run_command,
) -> dict[str, Any]:
    support = matrix_status(load_matrix(matrix_path), target_profile=profile, root=root)
    checks: list[dict[str, Any]] = [
        {
            "name": "support_matrix",
            "ok": bool(support.get("commercial_ready")),
            "structurally_valid": support.get("ok"),
            "support_level": support.get("support_level"),
            "commercial_ready": support.get("commercial_ready"),
            "blockers": support.get("blockers") or support.get("errors") or [],
            "sha256": support.get("sha256"),
        }
    ]
    evidence = verify_evidence_package(
        evidence_package,
        public_key,
        expected_environment=environment_id,
        expected_profile=profile,
    )
    checks.append({"name": "evidence_package", **evidence})
    checks.append(check_migrations())
    checks.append(check_consistency(os.getenv("POSTGRES_DSN", ""), check_external_dependencies=True))
    for name, command, timeout_seconds in existing_gate_commands():
        checks.append(command_runner(name, command, root, timeout_seconds))
    return {
        "schema_version": "1.0",
        "profile": profile,
        "environment_id": environment_id,
        **evaluate_checks(checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fail-closed PortraitHub commercial production release gate.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--matrix", type=Path, default=Path("deploy/support-matrix.json"))
    parser.add_argument("--profile", required=True, choices=["private_standard", "private_ha", "platform_api"])
    parser.add_argument("--environment", required=True)
    parser.add_argument("--evidence-package", required=True, type=Path)
    parser.add_argument("--public-key", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    result = run_release_gate(
        root=root,
        matrix_path=(root / args.matrix).resolve() if not args.matrix.is_absolute() else args.matrix.resolve(),
        profile=args.profile,
        environment_id=args.environment,
        evidence_package=args.evidence_package.resolve(),
        public_key=args.public_key.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
