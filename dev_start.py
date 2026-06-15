# -*- coding: utf-8 -*-
"""
本地开发环境一键启动脚本
支持自动创建虚拟环境、安装依赖、初始化配置文件并启动 FastAPI 服务。
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path


def log_info(msg: str):
    print(f"\033[1;32m[提示] {msg}\033[0m")


def log_warn(msg: str):
    print(f"\033[1;33m[警告] {msg}\033[0m")


def log_error(msg: str):
    print(f"\033[1;31m[错误] {msg}\033[0m")


def run_command(args: list[str], shell: bool = False) -> bool:
    try:
        result = subprocess.run(args, check=True, shell=shell)
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        log_error(f"执行命令失败: {' '.join(args)}。错误信息: {e}")
        return False


def main():
    log_info("正在检查并初始化本地开发环境...")

    root_dir = Path(__file__).resolve().parent

    # 1. 检查并复制环境变量配置文件
    env_file = root_dir / ".env"
    env_example = root_dir / ".env.example"
    if not env_file.exists():
        if env_example.exists():
            log_info("未检测到 .env 配置文件，正在复制 .env.example 模板...")
            shutil.copy(env_example, env_file)
        else:
            log_warn("未检测到 .env.example 模板文件，请确保项目结构完整。")
    else:
        log_info(".env 配置文件已存在。")

    # 2. 检查并创建共享模型目录
    models_dir = root_dir / "models"
    if not models_dir.exists():
        log_info("正在创建模型共享目录 ./models ...")
        models_dir.mkdir(parents=True, exist_ok=True)
    else:
        log_info("模型共享目录 ./models 已存在。")

    # 3. 检查并准备虚拟环境
    venv_dir = root_dir / ".venv"
    is_windows = sys.platform.startswith("win")

    # 定义虚拟环境内的 Python 和 Pip 执行路径
    if is_windows:
        python_exe = venv_dir / "Scripts" / "python.exe"
        pip_exe = venv_dir / "Scripts" / "pip.exe"
    else:
        python_exe = venv_dir / "bin" / "python"
        pip_exe = venv_dir / "bin" / "pip"

    if not venv_dir.exists() or not python_exe.exists():
        log_info("未检测到有效虚拟环境，正在创建虚拟环境 (python -m venv .venv)...")
        # 使用当前运行本脚本的 python 解释器来创建 venv
        if not run_command([sys.executable, "-m", "venv", ".venv"]):
            log_error("虚拟环境创建失败，请确保系统已正确安装 python 并加入环境变量。")
            sys.exit(1)
        log_info("虚拟环境创建成功。")
    else:
        log_info("虚拟环境检测正常。")

    # 4. 升级 pip 并安装依赖
    log_info("正在检查并安装依赖包（使用清华镜像源加速）...")
    # 升级 pip
    run_command([str(python_exe), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "-q", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
    
    # 安装 requirements.txt
    req_file = root_dir / "requirements.txt"
    if req_file.exists():
        log_info("正在安装 requirements.txt 依赖...")
        run_command([str(pip_exe), "install", "-q", "-r", str(req_file), "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
    
    # 安装 requirements/dev.txt 开发依赖
    req_dev_file = root_dir / "requirements" / "dev.txt"
    if req_dev_file.exists():
        log_info("正在安装 requirements/dev.txt 开发依赖...")
        run_command([str(pip_exe), "install", "-q", "-r", str(req_dev_file), "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])

    # 5. 启动 FastAPI 服务
    log_info("开发环境初始化完成！正在启动 FastAPI 推理服务 (以热重载模式)...")
    
    # 构造 uvicorn 启动命令
    startup_args = [
        str(python_exe),
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--reload"
    ]
    
    try:
        # 使用 subprocess 运行以确保能优雅处理 Ctrl+C 中断
        subprocess.run(startup_args, check=True)
    except KeyboardInterrupt:
        log_info("收到退出信号，服务已停止运行。")
    except Exception as e:
        log_error(f"启动服务失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
