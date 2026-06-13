import hashlib
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import HTTPException, status

from app.model_config_resolver import alias_target
from app.model_refs import INVALID_ALIAS_NAME_DETAIL, validate_model_target, validate_path_name
from app.observability import logger
from app.portrait_response import exception_log_summary
from app.rollout_audit import write_rollout_audit
from app.settings import MODEL_CONFIG_PATH


def model_config_path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def load_raw_model_config() -> dict[str, Any]:
    if not MODEL_CONFIG_PATH.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="model config file not found",
        )
    try:
        with MODEL_CONFIG_PATH.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}
    except Exception as exc:
        logger.warning(
            "failed to read model config file: config_path_hash=%s error=%s",
            model_config_path_fingerprint(MODEL_CONFIG_PATH),
            exception_log_summary(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to read model config file",
        ) from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model config root must be a mapping")
    return raw


def write_raw_model_config(raw: dict[str, Any]) -> None:
    temp_path: Path | None = None
    try:
        MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = MODEL_CONFIG_PATH.with_name(f".{MODEL_CONFIG_PATH.name}.{uuid4().hex}.tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(raw, file, allow_unicode=True, sort_keys=False)
        try:
            os.replace(temp_path, MODEL_CONFIG_PATH)
        except OSError:
            with MODEL_CONFIG_PATH.open("w", encoding="utf-8") as file:
                yaml.safe_dump(raw, file, allow_unicode=True, sort_keys=False)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                logger.warning("failed to cleanup model config temp file: %s", exception_log_summary(cleanup_exc))
    except Exception as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        logger.warning(
            "failed to write model config file: config_path_hash=%s error=%s",
            model_config_path_fingerprint(MODEL_CONFIG_PATH),
            exception_log_summary(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to write model config file",
        ) from exc


def commit_model_config_with_audit(raw: dict[str, Any], previous_raw: dict[str, Any], event: str, result: dict[str, Any]) -> None:
    write_raw_model_config(raw)
    try:
        write_rollout_audit(event, result)
    except Exception as exc:
        logger.warning(
            "failed to write rollout audit; rolling back model config: error=%s",
            exception_log_summary(exc),
        )
        try:
            write_raw_model_config(previous_raw)
        except Exception as rollback_exc:
            logger.warning(
                "failed to rollback model config after rollout audit failure: error=%s",
                exception_log_summary(rollback_exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "message": "failed to write rollout audit and rollback model config",
                    "rolled_back": False,
                    "rollback_failed": True,
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "failed to write rollout audit; model config was rolled back",
                "rolled_back": True,
            },
        ) from exc


def models_mapping(raw: dict[str, Any]) -> dict[str, Any]:
    models = raw.get("models", raw)
    if not isinstance(models, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models must be a mapping")
    return models


def aliases_mapping(raw: dict[str, Any]) -> dict[str, Any]:
    aliases = raw.get("aliases")
    if aliases is None:
        aliases = {}
        raw["aliases"] = aliases
    if not isinstance(aliases, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="aliases must be a mapping")
    return aliases


def current_alias_target(alias_name: str, alias_config: Any) -> str:
    try:
        return alias_target(alias_name, alias_config)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="failed to resolve alias",
        ) from exc


def validate_alias_name(alias_name: str) -> str:
    try:
        return validate_path_name(alias_name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_ALIAS_NAME_DETAIL) from exc


def validate_configured_target(target_model_id: str, models: dict[str, Any]) -> str:
    target = validate_model_target(target_model_id)
    if target not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="target model is not configured in models.yml",
        )
    return target


def rollout_weight(value: Any) -> int:
    try:
        weight = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target weights must be integers") from exc
    if weight < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target weights must be >= 0")
    return weight


def switch_alias_target(
    alias_name: str,
    target_model_id: str,
    expected_current_target: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    alias_name = validate_alias_name(alias_name)
    raw = load_raw_model_config()
    previous_raw = yaml.safe_load(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)) or {}
    models = models_mapping(raw)
    aliases = aliases_mapping(raw)
    target_model_id = validate_configured_target(target_model_id, models)
    expected_current_target = validate_model_target(expected_current_target) if expected_current_target is not None else None

    old_config = aliases.get(alias_name)
    old_target = current_alias_target(alias_name, old_config) if old_config is not None else None
    if expected_current_target is not None and old_target != expected_current_target:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "alias current target does not match expected_current_target",
            },
        )

    if isinstance(old_config, dict):
        next_config = dict(old_config)
    else:
        next_config = {}
    next_config["target"] = target_model_id
    if old_target and old_target != target_model_id:
        next_config["previous_target"] = old_target
    aliases[alias_name] = next_config

    result = {
        "alias": alias_name,
        "old_target": old_target,
        "new_target": target_model_id,
        "dry_run": dry_run,
        "config_loaded": True,
        "would_write": dry_run,
        "written": not dry_run,
    }

    if not dry_run:
        commit_model_config_with_audit(raw, previous_raw, "alias_switch", result)

    return result


def configure_weighted_alias_rollout(
    alias_name: str,
    targets: list[dict[str, Any]],
    expected_current_target: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    alias_name = validate_alias_name(alias_name)
    raw = load_raw_model_config()
    previous_raw = yaml.safe_load(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)) or {}
    models = models_mapping(raw)
    aliases = aliases_mapping(raw)
    if not targets:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="targets must not be empty")
    expected_current_target = validate_model_target(expected_current_target) if expected_current_target is not None else None

    rollout_targets = []
    total_weight = 0
    for item in targets:
        if not isinstance(item, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="targets must be mappings")
        target_model_id = str(item.get("target_model_id") or item.get("target") or "")
        weight = rollout_weight(item.get("weight", 0))
        target_model_id = validate_configured_target(target_model_id, models)
        total_weight += weight
        rollout_item: dict[str, Any] = {"target": target_model_id, "weight": weight}
        if item.get("status"):
            rollout_item["status"] = item["status"]
        rollout_targets.append(rollout_item)
    if total_weight <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="total rollout weight must be > 0")

    old_config = aliases.get(alias_name)
    old_target = current_alias_target(alias_name, old_config) if old_config is not None else None
    if expected_current_target is not None and old_target != expected_current_target:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "alias current target does not match expected_current_target",
            },
        )

    next_config = dict(old_config) if isinstance(old_config, dict) else {}
    next_config.pop("target", None)
    next_config["rollout"] = rollout_targets
    if old_target:
        next_config["previous_target"] = old_target
    aliases[alias_name] = next_config

    result = {
        "alias": alias_name,
        "old_target": old_target,
        "rollout": rollout_targets,
        "total_weight": total_weight,
        "dry_run": dry_run,
        "config_loaded": True,
        "would_write": dry_run,
        "written": not dry_run,
    }

    if not dry_run:
        commit_model_config_with_audit(raw, previous_raw, "alias_weighted_rollout", result)

    return result


def rollback_alias_target(alias_name: str, dry_run: bool = False) -> dict[str, Any]:
    alias_name = validate_alias_name(alias_name)
    raw = load_raw_model_config()
    previous_raw = yaml.safe_load(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)) or {}
    models = models_mapping(raw)
    aliases = aliases_mapping(raw)
    if alias_name not in aliases:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alias not found")
    alias_config = aliases[alias_name]
    current_target = current_alias_target(alias_name, alias_config)
    if not isinstance(alias_config, dict) or not isinstance(alias_config.get("previous_target"), str):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="alias has no previous_target")

    rollback_target = validate_configured_target(alias_config["previous_target"], models)
    alias_config["target"] = rollback_target
    alias_config["previous_target"] = current_target
    aliases[alias_name] = alias_config

    result = {
        "alias": alias_name,
        "old_target": current_target,
        "new_target": rollback_target,
        "dry_run": dry_run,
        "config_loaded": True,
        "would_write": dry_run,
        "written": not dry_run,
    }

    if not dry_run:
        commit_model_config_with_audit(raw, previous_raw, "alias_rollback", result)

    return result
