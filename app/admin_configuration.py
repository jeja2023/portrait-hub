from __future__ import annotations

import os
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app import config_overrides
from app.config_overrides import CONFIG_STATE_VERSION
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import ADMIN_CONFIG_STATE_PATH

CONFIG_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / ".env.example"
MAX_CONFIGURATION_CHANGES = 100
MAX_CONFIGURATION_VALUE_LENGTH = 65_536
_CONFIG_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_INTEGER_PATTERN = re.compile(r"^-?[0-9]+$")
_NUMBER_PATTERN = re.compile(r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")
_STATE_LOCK = threading.RLock()

SENSITIVE_CONFIGURATION_KEYS = {
    "API_TOKEN",
    "ENCRYPTION_KEY",
    "ENCRYPTION_KEYRING",
    "JWT_PUBLIC_KEY",
    "JWT_PUBLIC_KEYRING",
    "JWT_SECRET",
    "JWT_SECRET_KEYRING",
    "LOCAL_AUTH_PASSWORD",
    "LOCAL_AUTH_SESSION_SECRET",
    "OIDC_CLIENT_SECRET",
    "OIDC_SESSION_SECRET",
    "POSTGRES_DSN",
    "QDRANT_API_KEY",
    "REDIS_URL",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
}

COMPOSE_CONFIGURATION_KEYS = {
    "CPU_TRUSTED_HOSTS",
    "GPU_WORKER_0_DEVICE",
    "GPU_WORKER_1_DEVICE",
    "INSTALL_PROD_OPTIONAL",
    "MODEL_CONFIG_HOST_FILE",
    "MODELS_HOST_DIR",
    "RUNTIME_STATE_HOST_DIR",
    "STREAM_WORKER_FORCE_CPU",
    "STREAM_WORKER_GPU_DEVICES",
    "VIDEO_JOB_WORKER_GPU_DEVICES",
}

LOCKED_CONFIGURATION_KEYS = {
    "ADMIN_CONFIG_STATE_PATH",
    "RUNTIME_STATE_DIR",
}

NETWORK_POLICY_CONFIGURATION_KEYS = {
    "ALLOW_PRIVATE_STREAM_HOSTS",
    "ALLOW_PRIVATE_WEBHOOK_HOSTS",
    "STREAM_ALLOWED_CIDRS",
    "STREAM_ALLOWED_HOSTS",
    "WEBHOOK_ALLOWED_CIDRS",
    "WEBHOOK_ALLOWED_HOSTS",
}


def _category_for_key(key: str) -> str:
    if key in NETWORK_POLICY_CONFIGURATION_KEYS or key in {
        "ALLOW_STREAM_URLS",
        "TRUSTED_HOSTS",
        "CPU_TRUSTED_HOSTS",
        "CONTENT_SECURITY_POLICY",
        "HSTS_ENABLED",
        "HSTS_INCLUDE_SUBDOMAINS",
        "HSTS_MAX_AGE_SECONDS",
        "HSTS_PRELOAD",
        "SECURITY_HEADERS_ENABLED",
        "RATE_LIMIT_TRUST_FORWARDED_FOR",
    }:
        return "安全与网络"
    if any(marker in key for marker in ("AUTH", "TOKEN", "JWT", "OIDC", "RBAC", "TENANT")):
        return "身份与鉴权"
    if any(marker in key for marker in ("POSTGRES", "PGVECTOR", "QDRANT", "S3_", "STORAGE", "REDIS")):
        return "数据与存储"
    if any(marker in key for marker in ("MODEL", "TENSOR", "DETECT", "REID", "INFERENCE", "WARMUP")):
        return "模型与推理"
    if any(marker in key for marker in ("GPU", "CPU", "CUDA", "TENSORRT", "WORKER")):
        return "GPU与工作进程"
    if any(marker in key for marker in ("VIDEO", "STREAM", "FRAME", "IMAGE")):
        return "图片、视频与流"
    if any(marker in key for marker in ("QUEUE", "JOB", "RETRY", "POLL", "LEASE")):
        return "任务与队列"
    if any(marker in key for marker in ("OTEL", "LOG", "METRIC", "AUDIT", "PROMETHEUS")):
        return "日志与可观测性"
    if key.endswith("_PATH") or key.endswith("_DIR") or "HOST_DIR" in key or "HOST_FILE" in key:
        return "路径与文件"
    if any(marker in key for marker in ("MAX_", "LIMIT", "TIMEOUT", "INTERVAL", "RETENTION", "CONCURRENCY")):
        return "容量与性能"
    return "基础运行"


def _value_type(key: str, default: str) -> str:
    lowered = default.strip().lower()
    if lowered in {"true", "false"}:
        return "boolean"
    if key.endswith(("_HOSTS", "_CIDRS", "_DEVICES")) or key in {"GPU_DEVICE_IDS", "WARMUP_MODELS"}:
        return "list"
    if _INTEGER_PATTERN.fullmatch(default.strip()):
        return "integer"
    if _NUMBER_PATTERN.fullmatch(default.strip()):
        return "number"
    if key.endswith("_PATH") or key.endswith("_DIR") or key.endswith("_FILE"):
        return "path"
    return "string"


def parse_configuration_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or CONFIG_TEMPLATE_PATH
    if not target.is_file():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="配置模板不可用")
    comments: list[str] = []
    items: list[dict[str, Any]] = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="配置模板不可用") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            comments = []
            continue
        if line.startswith("#"):
            comment = line[1:].strip()
            if comment:
                comments.append(comment)
            continue
        if "=" not in line:
            comments = []
            continue
        key, default = line.split("=", 1)
        key = key.strip()
        if not _CONFIG_KEY_PATTERN.fullmatch(key):
            comments = []
            continue
        default = default.strip()
        if len(default) >= 2 and default[0] == default[-1] and default[0] in {"'", '"'}:
            default = default[1:-1]
        sensitive = key in SENSITIVE_CONFIGURATION_KEYS
        managed_by = "network_policy" if key in NETWORK_POLICY_CONFIGURATION_KEYS else "configuration"
        apply_mode = "compose_recreate" if key in COMPOSE_CONFIGURATION_KEYS else "service_restart"
        editable = key not in LOCKED_CONFIGURATION_KEYS and managed_by == "configuration"
        items.append(
            {
                "key": key,
                "description": " ".join(comments),
                "category": _category_for_key(key),
                "value_type": _value_type(key, default),
                "sensitive": sensitive,
                "editable": editable,
                "managed_by": managed_by,
                "apply_mode": apply_mode,
                "default": None if sensitive else default,
            }
        )
        comments = []
    return items


def _empty_state() -> dict[str, Any]:
    return {"version": CONFIG_STATE_VERSION, "revision": 0, "updated_at": None, "values": {}}


def configuration_state_snapshot() -> dict[str, Any]:
    with _STATE_LOCK:
        raw = read_json_state(ADMIN_CONFIG_STATE_PATH, None)
    if raw is None:
        return _empty_state()
    if not isinstance(raw, dict) or raw.get("version") != CONFIG_STATE_VERSION:
        handle_state_read_error("管理员配置状态版本或格式无效")
        return _empty_state()
    values = raw.get("values")
    if not isinstance(values, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
        handle_state_read_error("管理员配置状态内容无效")
        return _empty_state()
    try:
        revision = max(0, int(raw.get("revision", 0)))
    except (TypeError, ValueError):
        handle_state_read_error("管理员配置状态修订号无效")
        return _empty_state()
    return {
        "version": CONFIG_STATE_VERSION,
        "revision": revision,
        "updated_at": raw.get("updated_at"),
        "values": dict(values),
    }


def _effective_value(item: dict[str, Any]) -> tuple[str, str]:
    key = str(item["key"])
    raw = os.getenv(key)
    if raw is not None:
        source = "override" if config_overrides.APPLIED_CONFIGURATION_OVERRIDES.get(key) == raw else "environment"
        return raw, source
    return str(item.get("default") or ""), "default"


def configuration_catalog_snapshot() -> dict[str, Any]:
    state = configuration_state_snapshot()
    overrides: dict[str, str] = state["values"]
    items: list[dict[str, Any]] = []
    for spec in parse_configuration_catalog():
        item = dict(spec)
        key = str(item["key"])
        effective, source = _effective_value(item)
        overridden = key in overrides
        desired = overrides.get(key)
        desired_after_restart = desired
        removed_loaded_override = not overridden and key in config_overrides.APPLIED_CONFIGURATION_OVERRIDES
        if removed_loaded_override:
            base_environment_value = config_overrides.BASE_CONFIGURATION_ENVIRONMENT.get(key)
            desired_after_restart = (
                base_environment_value if base_environment_value is not None else str(item.get("default") or "")
            )
        sensitive = bool(item["sensitive"])
        item.update(
            {
                "value": None if sensitive else effective,
                "desired_value": None if sensitive or (not overridden and not removed_loaded_override) else desired_after_restart,
                "configured": bool(effective),
                "override_configured": overridden and bool(desired),
                "overridden": overridden,
                "pending": (
                    (item["apply_mode"] == "compose_recreate" and overridden)
                    or (overridden and desired != effective)
                    or (removed_loaded_override and desired_after_restart != effective)
                ),
                "source": source,
            }
        )
        items.append(item)
    categories = sorted({str(item["category"]) for item in items})
    return {
        "revision": state["revision"],
        "updated_at": state["updated_at"],
        "categories": categories,
        "items": items,
        "summary": {
            "total": len(items),
            "overridden": sum(1 for item in items if item["overridden"]),
            "pending": sum(1 for item in items if item["pending"]),
            "sensitive": sum(1 for item in items if item["sensitive"]),
        },
    }


def _normalize_configuration_value(spec: dict[str, Any], value: str) -> str:
    if len(value) > MAX_CONFIGURATION_VALUE_LENGTH or "\x00" in value:
        raise ValueError("配置值过长或包含无效字符")
    value_type = spec.get("value_type")
    stripped = value.strip()
    if value_type == "boolean":
        if stripped.lower() not in {"true", "false"}:
            raise ValueError("布尔配置只能填写 true 或 false")
        return stripped.lower()
    if value_type == "integer" and stripped:
        if not _INTEGER_PATTERN.fullmatch(stripped):
            raise ValueError("配置值必须是整数")
        return str(int(stripped))
    if value_type == "number" and stripped:
        if not _NUMBER_PATTERN.fullmatch(stripped):
            raise ValueError("配置值必须是数字")
        return stripped
    if value_type == "list":
        return ",".join(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    return value


def update_configuration_state(
    *,
    changes: list[dict[str, Any]],
    expected_revision: int,
    updated_at: float,
) -> tuple[dict[str, Any], list[str]]:
    if not changes or len(changes) > MAX_CONFIGURATION_CHANGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"单次必须修改 1-{MAX_CONFIGURATION_CHANGES} 个配置项",
        )
    catalog = {str(item["key"]): item for item in parse_configuration_catalog()}
    with _STATE_LOCK:
        current = configuration_state_snapshot()
        if int(current["revision"]) != expected_revision:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="配置已被其他管理员修改，请刷新后重试")
        values: dict[str, str] = dict(current["values"])
        changed_keys: list[str] = []
        for change in changes:
            key = str(change.get("key") or "").strip()
            spec = catalog.get(key)
            if spec is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"不支持的配置项：{key}")
            if not spec["editable"]:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"配置项不可在此页面修改：{key}")
            raw_value = change.get("value")
            if raw_value is None:
                if key in values:
                    values.pop(key, None)
                    changed_keys.append(key)
                continue
            if not isinstance(raw_value, str):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"配置值必须是字符串：{key}")
            try:
                normalized = _normalize_configuration_value(spec, raw_value)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{key}：{exc}") from exc
            if values.get(key) != normalized:
                values[key] = normalized
                changed_keys.append(key)
        if not changed_keys:
            return deepcopy(current), []
        payload = {
            "version": CONFIG_STATE_VERSION,
            "revision": int(current["revision"]) + 1,
            "updated_at": float(updated_at),
            "values": values,
        }
        write_json_state(ADMIN_CONFIG_STATE_PATH, payload)
        try:
            ADMIN_CONFIG_STATE_PATH.chmod(0o600)
        except OSError:
            pass
        return deepcopy(payload), sorted(set(changed_keys))


def restore_configuration_state(payload: dict[str, Any]) -> None:
    with _STATE_LOCK:
        if int(payload.get("revision", 0)) == 0 and not payload.get("values"):
            try:
                ADMIN_CONFIG_STATE_PATH.unlink(missing_ok=True)
            except OSError:
                pass
            return
        write_json_state(ADMIN_CONFIG_STATE_PATH, payload)


__all__ = [
    "ADMIN_CONFIG_STATE_PATH",
    "CONFIG_TEMPLATE_PATH",
    "configuration_catalog_snapshot",
    "configuration_state_snapshot",
    "parse_configuration_catalog",
    "restore_configuration_state",
    "update_configuration_state",
]
