from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.admin_configuration import COMPOSE_CONFIGURATION_KEYS
from app.config_overrides import CONFIG_STATE_VERSION

_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")


def read_compose_overrides(path: Path) -> dict[str, str]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != CONFIG_STATE_VERSION:
        raise ValueError("配置中心状态版本或格式无效")
    raw_values = payload.get("values")
    if not isinstance(raw_values, dict):
        raise ValueError("配置中心状态内容无效")
    values: dict[str, str] = {}
    for key in sorted(COMPOSE_CONFIGURATION_KEYS):
        value = raw_values.get(key)
        if isinstance(value, str):
            if "\x00" in value or "\n" in value or "\r" in value:
                raise ValueError(f"编排配置 {key} 包含不支持的控制字符")
            values[key] = value
    return values


def encode_env_value(value: str) -> str:
    if not value or re.fullmatch(r"[A-Za-z0-9_./,:+-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def merge_env_text(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    lines = text.splitlines(keepends=True)
    remaining = dict(values)
    changed: list[str] = []
    output: list[str] = []
    for line in lines:
        match = _ASSIGNMENT_PATTERN.match(line)
        key = match.group(1) if match else ""
        if key not in remaining:
            output.append(line)
            continue
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        replacement = f"{key}={encode_env_value(remaining.pop(key))}{newline}"
        output.append(replacement)
        if replacement != line:
            changed.append(key)
    if remaining:
        if output and output[-1] and not output[-1].endswith(("\n", "\r")):
            output[-1] += os.linesep
        output.append(os.linesep + "# 由配置中心生成的 Docker 编排覆盖" + os.linesep)
        for key, value in sorted(remaining.items()):
            output.append(f"{key}={encode_env_value(value)}{os.linesep}")
            changed.append(key)
    return "".join(output), sorted(set(changed))


def write_env_atomically(path: Path, text: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(text, encoding="utf-8", newline="")
    try:
        temp_path.chmod(0o600)
    except OSError:
        pass
    os.replace(temp_path, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="将配置中心保存的 Docker 编排项合并到宿主机 .env。")
    parser.add_argument("--state", type=Path, default=Path("runtime-state/admin-configuration.json"))
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--check", action="store_true", help="只显示将修改的配置键，不写文件。")
    args = parser.parse_args()

    if not args.state.is_file():
        parser.error(f"配置中心状态文件不存在：{args.state}")
    if not args.env.is_file():
        parser.error(f"环境文件不存在：{args.env}")

    overrides = read_compose_overrides(args.state)
    merged, changed = merge_env_text(args.env.read_text(encoding="utf-8"), overrides)
    if not changed:
        print("没有需要同步到 .env 的 Docker 编排配置。")
        return 0
    print("将同步以下配置：" + ", ".join(changed))
    if args.check:
        return 0

    backup = args.env.with_name(f"{args.env.name}.before-admin-config-{int(time.time())}")
    shutil.copy2(args.env, backup)
    try:
        backup.chmod(0o600)
    except OSError:
        pass
    write_env_atomically(args.env, merged)
    print(f"已更新 {args.env}；备份位于 {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
