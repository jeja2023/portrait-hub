from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

# 除直接/可选清单外，还审计完全钉版的锁文件（传递依赖的版本实际存在于此，例如
# python-multipart），这样即使某个有漏洞的传递依赖从不出现在 requirements.txt 中也能被发现。
DEFAULT_REQUIREMENTS = ["requirements.lock", "requirements.txt", "requirements/prod-optional.txt"]
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
    parser = argparse.ArgumentParser(description="针对 PortraitHub 依赖清单运行 pip-audit。")
    parser.add_argument("-r", "--requirement", action="append", dest="requirements", help="要审计的依赖文件。")
    parser.add_argument("-f", "--format", choices=["columns", "json", "cyclonedx-json", "cyclonedx-xml"], default=None)
    parser.add_argument(
        "--mode",
        choices=["auto", "requirements", "local"],
        default="auto",
        help="审计模式。auto 会先审计依赖文件；只有当 pip-audit 无法创建临时解析虚拟环境时，才会回退到当前 Python 环境。",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help="pip-audit 缓存目录。默认位于工作区内，以避免操作系统缓存权限问题。",
    )
    parser.add_argument(
        "--tmp-dir",
        default=str(DEFAULT_TMP_DIR),
        help="pip-audit 解析环境的临时目录。",
    )
    args = parser.parse_args()

    requirements = args.requirements or DEFAULT_REQUIREMENTS
    if args.mode != "local":
        missing = [item for item in requirements if not Path(item).exists()]
        if missing:
            print(f"缺少依赖文件: {', '.join(missing)}", file=sys.stderr)
            return 2
    if importlib.util.find_spec("pip_audit") is None:
        print("未安装 pip-audit；请运行 `python -m pip install pip-audit`。", file=sys.stderr)
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
            print("pip-audit 无法创建临时解析环境；将回退到当前 Python 环境的 --local 审计。", file=sys.stderr)
            return run_audit_command(build_local_command(args.format, str(cache_dir)), env)[0]
        return code
    except FileNotFoundError:
        print("未安装 pip-audit；请安装 requirements/prod-optional.txt 或运行 `python -m pip install pip-audit`。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
