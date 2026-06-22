"""Shared production-readiness checks for model artifacts and capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


PRODUCTION_CAPABILITY_STATUSES = {"model_backed", "ready", "production"}


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    return payload if isinstance(payload, dict) else {}


def configured_model_path(model_id: str, config: Any, models_root: Path) -> tuple[Path | None, str | None]:
    artifact = config.get("artifact") if isinstance(config, dict) else {}
    artifact_path = artifact.get("path") if isinstance(artifact, dict) else None
    if isinstance(artifact_path, str) and artifact_path.strip():
        candidate = Path(artifact_path.strip())
        if candidate.is_absolute():
            return None, "artifact.path must be relative to models root"
        path = (models_root / candidate).resolve()
    else:
        path = (models_root / model_id).resolve()
    try:
        path.relative_to(models_root.resolve())
    except ValueError:
        return None, "model artifact path escapes models root"
    return path, None


def model_config_entries(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = load_yaml(root / "models.yml")
    models = payload.get("models", payload)
    aliases = payload.get("aliases", {})
    return (
        models if isinstance(models, dict) else {},
        aliases if isinstance(aliases, dict) else {},
    )


def resolve_configured_model_id(model_id: Any, models: dict[str, Any], aliases: dict[str, Any]) -> tuple[str | None, str | None]:
    if not isinstance(model_id, str) or not model_id.strip():
        return None, "model_id is required"
    candidate = model_id.strip()
    if candidate in models:
        return candidate, None
    alias = aliases.get(candidate)
    if isinstance(alias, str) and alias in models:
        return alias, None
    if isinstance(alias, dict):
        target = alias.get("target")
        if isinstance(target, str) and target in models:
            return target, None
    return None, "model_id is not configured in models.yml or aliases"


def check_capabilities(root: Path) -> list[dict[str, Any]]:
    payload = load_yaml(root / "model-capabilities.yml")
    capabilities = payload.get("capabilities", payload)
    models, aliases = model_config_entries(root)
    checks = []
    if not isinstance(capabilities, dict):
        return [{"name": "capability:config", "ok": False, "status": "invalid", "detail": "capabilities root must be a mapping"}]
    for name, item in sorted(capabilities.items()):
        status = item.get("status") if isinstance(item, dict) else None
        model_id = item.get("model_id") if isinstance(item, dict) else None
        fallback_model_id = item.get("fallback_model_id") if isinstance(item, dict) else None
        resolved_model_id, model_error = resolve_configured_model_id(model_id, models, aliases)
        is_model_backed = (
            status in PRODUCTION_CAPABILITY_STATUSES
            and isinstance(model_id, str)
            and bool(model_id.strip())
            and model_id != fallback_model_id
            and resolved_model_id is not None
        )
        checks.append(
            {
                "name": f"capability:{name}",
                "ok": is_model_backed,
                "status": status,
                "model_configured": resolved_model_id is not None,
                "resolved_model_id": resolved_model_id,
                "model_error": model_error,
                "detail": item,
            }
        )
    return checks


def check_model_files(root: Path, models_root: Path) -> list[dict[str, Any]]:
    models, _ = model_config_entries(root)
    checks = []
    for model_id, config in sorted(models.items()):
        path, error = configured_model_path(str(model_id), config, models_root)
        checks.append(
            {
                "name": f"model_file:{model_id}",
                "ok": bool(path and path.is_file() and not error),
                "path": str(path) if path else None,
                "error": error,
            }
        )
    return checks
