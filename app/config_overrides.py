from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_STATE_VERSION = 1
APPLIED_CONFIGURATION_OVERRIDES: dict[str, str] = {}
BASE_CONFIGURATION_ENVIRONMENT: dict[str, str | None] = {}


def configuration_state_path() -> Path:
    explicit = os.getenv("ADMIN_CONFIG_STATE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    runtime_state_dir = os.getenv("RUNTIME_STATE_DIR", "runtime-state").strip() or "runtime-state"
    return Path(runtime_state_dir) / "admin-configuration.json"


def read_configuration_override_values(path: Path | None = None) -> dict[str, str]:
    target = path or configuration_state_path()
    if not target.is_file():
        return {}
    try:
        payload: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        if os.getenv("STATE_READ_FAIL_CLOSED", "true").strip().lower() in {"1", "true", "yes", "on"}:
            raise RuntimeError("管理员配置状态读取失败") from exc
        return {}
    if not isinstance(payload, dict) or payload.get("version") != CONFIG_STATE_VERSION:
        if os.getenv("STATE_READ_FAIL_CLOSED", "true").strip().lower() in {"1", "true", "yes", "on"}:
            raise RuntimeError("管理员配置状态版本或格式无效")
        return {}
    raw_values = payload.get("values")
    if not isinstance(raw_values, dict):
        if os.getenv("STATE_READ_FAIL_CLOSED", "true").strip().lower() in {"1", "true", "yes", "on"}:
            raise RuntimeError("管理员配置状态内容无效")
        return {}
    values: dict[str, str] = {}
    for raw_key, raw_value in raw_values.items():
        key = str(raw_key).strip()
        if not key or not key.replace("_", "").isalnum() or key.upper() != key:
            continue
        if isinstance(raw_value, str):
            values[key] = raw_value
    return values


def apply_configuration_overrides(path: Path | None = None) -> dict[str, str]:
    values = read_configuration_override_values(path)
    APPLIED_CONFIGURATION_OVERRIDES.clear()
    for key, value in values.items():
        BASE_CONFIGURATION_ENVIRONMENT.setdefault(key, os.environ.get(key))
        os.environ[key] = value
        APPLIED_CONFIGURATION_OVERRIDES[key] = value
    return values


__all__ = [
    "APPLIED_CONFIGURATION_OVERRIDES",
    "BASE_CONFIGURATION_ENVIRONMENT",
    "CONFIG_STATE_VERSION",
    "apply_configuration_overrides",
    "configuration_state_path",
    "read_configuration_override_values",
]
