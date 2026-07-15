from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

from app.observability import logger
from app.runtime_defaults import parse_env_file

ENV_PATH = Path(os.getenv("ENV_PATH", ".env"))


def apply_env_file(path: Path | None = None) -> dict[str, Any]:
    env_path = path or ENV_PATH
    values = parse_env_file(env_path)
    changed_keys: list[str] = []
    for key, value in values.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
            changed_keys.append(key)
    return {
        "path": env_path,
        "loaded": env_path.is_file(),
        "key_count": len(values),
        "changed_keys": sorted(changed_keys),
    }


def reload_settings_modules() -> None:
    import app.settings as settings

    importlib.reload(settings)
    setting_names = {
        name
        for name in dir(settings)
        if name.isupper() and not name.startswith("_")
    }
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("app.") or module is settings:
            continue
        for name in setting_names:
            if hasattr(module, name):
                setattr(module, name, getattr(settings, name))


def audit_config_reload(source: str, result: dict[str, Any]) -> None:
    try:
        from app.portrait_audit import audit_event

        audit_event(
            "config_hot_reload",
            request_id=source,
            tenant_id="system",
            outcome="success",
            source=source,
            env_loaded=bool(result.get("env_loaded", False)),
            env_key_count=int(result.get("env_key_count", 0) or 0),
            env_changed_key_count=int(result.get("env_changed_key_count", 0) or 0),
            model_config_reloaded=bool(result.get("model_config_reloaded", False)),
            model_capabilities_reloaded=bool(result.get("model_capabilities_reloaded", False)),
        )
    except Exception as exc:
        logger.warning("configuration hot reload audit failed: %s", exc)


def reload_runtime_config(*, source: str = "manual", include_env: bool = True) -> dict[str, Any]:
    env_result: dict[str, Any] = {"loaded": False, "key_count": 0, "changed_keys": []}
    if include_env:
        env_result = apply_env_file()
        reload_settings_modules()

    from app.model_config_state import reload_model_config_state
    from app.portrait_model_capabilities import reload_model_capabilities

    reload_model_config_state()
    reload_model_capabilities()
    result = {
        "source": source,
        "env_loaded": bool(env_result.get("loaded", False)),
        "env_key_count": int(env_result.get("key_count", 0) or 0),
        "env_changed_key_count": len(env_result.get("changed_keys", [])),
        "model_config_reloaded": True,
        "model_capabilities_reloaded": True,
    }
    audit_config_reload(source, result)
    return result
