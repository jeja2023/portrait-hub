from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_REQUIREMENTS = ["requirements.txt", "requirements/prod-optional.txt"]
DEFAULT_CACHE_DIR = Path(".codex-tmp") / "pip-audit-cache"
DEFAULT_TMP_DIR = Path(".codex-tmp") / "pip-audit-tmp"
TEMP_ENV_FAILURE_MARKERS = (
    "Couldn't execute in a temporary directory",
    "VirtualEnvError",
    "PermissionError",
    "TemporaryDirectory",
)


def build_command(requirements: list[str], output_format: str | None, cache_dir: str | None = None) -> list[str]:
    command = [sys.executable, "-m", "pip_audit", "--progress-spinner", "off"]
    for requirement in requirements:
        command.extend(["-r", requirement])
    if cache_dir:
        command.extend(["--cache-dir", cache_dir])
    if output_format:
        command.extend(["-f", output_format])
    return command


def build_local_command(output_format: str | None, cache_dir: str | None = None) -> list[str]:
    command = [sys.executable, "-m", "pip_audit", "--progress-spinner", "off", "--local"]
    if cache_dir:
        command.extend(["--cache-dir", cache_dir])
    if output_format:
        command.extend(["-f", output_format])
    return command


def is_temp_env_failure(output: str) -> bool:
    return any(marker in output for marker in TEMP_ENV_FAILURE_MARKERS)


def run_audit_command(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(command, env=env, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode, f"{completed.stdout}\n{completed.stderr}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pip-audit against PortraitHub dependency manifests.")
    parser.add_argument("-r", "--requirement", action="append", dest="requirements", help="Requirement file to audit.")
    parser.add_argument("-f", "--format", choices=["columns", "json", "cyclonedx-json", "cyclonedx-xml"], default=None)
    parser.add_argument(
        "--mode",
        choices=["auto", "requirements", "local"],
        default="auto",
        help=(
            "Audit mode. 'auto' audits requirement files first and falls back to the current environment only "
            "when pip-audit cannot create its temporary resolver venv."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help="pip-audit cache directory. Defaults inside the workspace to avoid OS cache permission issues.",
    )
    parser.add_argument(
        "--tmp-dir",
        default=str(DEFAULT_TMP_DIR),
        help="Temporary directory for pip-audit resolver environments.",
    )
    args = parser.parse_args()

    requirements = args.requirements or DEFAULT_REQUIREMENTS
    if args.mode != "local":
        missing = [item for item in requirements if not Path(item).exists()]
        if missing:
            print(f"missing requirement files: {', '.join(missing)}", file=sys.stderr)
            return 2
    if importlib.util.find_spec("pip_audit") is None:
        print("pip-audit is not installed; run `python -m pip install pip-audit`.", file=sys.stderr)
        return 2
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        env = os.environ.copy()
        env.update({"TMPDIR": str(tmp_dir.resolve()), "TEMP": str(tmp_dir.resolve()), "TMP": str(tmp_dir.resolve())})
        if args.mode == "local":
            return run_audit_command(build_local_command(args.format, str(cache_dir)), env)[0]
        code, output = run_audit_command(build_command(requirements, args.format, str(cache_dir)), env)
        if args.mode == "auto" and code != 0 and is_temp_env_failure(output):
            print(
                "pip-audit could not create a temporary resolver environment; "
                "falling back to --local audit for the current Python environment.",
                file=sys.stderr,
            )
            return run_audit_command(build_local_command(args.format, str(cache_dir)), env)[0]
        return code
    except FileNotFoundError:
        print("pip-audit is not installed; install requirements/prod-optional.txt or run `python -m pip install pip-audit`.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
