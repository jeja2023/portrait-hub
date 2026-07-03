# -*- coding: utf-8 -*-
"""PortraitHub 本地开发启动引导脚本。"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.runtime_defaults import local_dev_env_overrides, parse_env_file


PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"


def log_info(msg: str) -> None:
    print(f"\033[1;32m[info] {msg}\033[0m")


def log_warn(msg: str) -> None:
    print(f"\033[1;33m[warn] {msg}\033[0m")


def log_error(msg: str) -> None:
    print(f"\033[1;31m[error] {msg}\033[0m")


def write_local_dev_env(root_dir: Path, env_file: Path) -> tuple[Path, dict[str, str]]:
    runtime_state_dir = root_dir / "runtime-state"
    runtime_state_dir.mkdir(parents=True, exist_ok=True)
    values = parse_env_file(env_file)
    overrides = local_dev_env_overrides(root_dir)
    values.update(overrides)
    local_env_file = runtime_state_dir / ".dev_start.env"
    lines = [
        "# 由 dev_start.py 生成，用于本地 Windows/macOS/Linux 运行。",
        "# 保持已提交的 .env 文件对 Docker 友好；该文件将运行时路径映射到当前的检出目录。",
    ]
    lines.extend(f"{key}={value}" for key, value in sorted(values.items()))
    local_env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return local_env_file, overrides


def run_command(args: list[str], cwd: Path | None = None) -> bool:
    try:
        subprocess.run(args, check=True, cwd=str(cwd) if cwd else None)
        return True
    except subprocess.CalledProcessError as exc:
        log_error(f"命令执行失败: {' '.join(args)} ({exc})")
        return False


def install_dependencies(root_dir: Path, python_exe: Path, pip_exe: Path) -> bool:
    log_info("正在安装 Python 依赖")
    if not run_command(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "setuptools",
            "wheel",
            "-q",
            "-i",
            PIP_INDEX_URL,
        ],
        cwd=root_dir,
    ):
        return False

    req_file = root_dir / "requirements.txt"
    if req_file.exists():
        log_info("正在安装 requirements.txt 中的依赖")
        if not run_command([str(pip_exe), "install", "-q", "-r", str(req_file), "-i", PIP_INDEX_URL], cwd=root_dir):
            return False

    req_dev_file = root_dir / "requirements" / "dev.txt"
    if req_dev_file.exists():
        log_info("正在安装 requirements/dev.txt 中的开发依赖")
        if not run_command([str(pip_exe), "install", "-q", "-r", str(req_dev_file), "-i", PIP_INDEX_URL], cwd=root_dir):
            return False

    return True


def main() -> None:
    log_info("正在检查本地开发环境")
    root_dir = Path(__file__).resolve().parent

    env_file = root_dir / ".env"
    env_example = root_dir / ".env.example"
    if not env_file.exists():
        if env_example.exists():
            log_info("正在从 .env.example 创建 .env 文件")
            shutil.copy(env_example, env_file)
        else:
            log_warn("未找到 .env.example 文件；请检查项目布局结构")
    else:
        log_info(".env 文件已存在")

    models_dir = root_dir / "models"
    if not models_dir.exists():
        log_info("正在创建 ./models 目录")
        models_dir.mkdir(parents=True, exist_ok=True)
    else:
        log_info("./models 目录已存在")

    venv_dir = root_dir / ".venv"
    is_windows = sys.platform.startswith("win")
    python_exe = venv_dir / ("Scripts/python.exe" if is_windows else "bin/python")
    pip_exe = venv_dir / ("Scripts/pip.exe" if is_windows else "bin/pip")

    if not venv_dir.exists() or not python_exe.exists():
        log_info("正在创建虚拟环境")
        if not run_command([sys.executable, "-m", "venv", str(venv_dir)], cwd=root_dir):
            log_error("创建虚拟环境失败")
            sys.exit(1)
    else:
        log_info("虚拟环境已就绪")

    if not install_dependencies(root_dir, python_exe, pip_exe):
        log_error("依赖安装失败，正在放弃启动")
        sys.exit(1)

    local_env_file, local_overrides = write_local_dev_env(root_dir, env_file)
    log_info(f"已生成本地环境变量文件: {local_env_file}")
    log_info("正在启动支持热重载的 FastAPI 服务")

    startup_args = [
        str(python_exe),
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--reload",
    ]
    startup_env = os.environ.copy()
    startup_env.update(local_overrides)
    startup_env["ENV_PATH"] = str(local_env_file)
    startup_env["PYTHONPATH"] = str(root_dir)

    try:
        subprocess.run(startup_args, check=True, cwd=str(root_dir), env=startup_env)
    except KeyboardInterrupt:
        log_info("服务已停止")
    except Exception as exc:
        log_error(f"启动服务失败: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
