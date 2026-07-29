import hashlib
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import HTTPException, status

from app.model_config_loader import configured_alias_targets
from app.model_config_resolver import alias_target
from app.model_refs import INVALID_ALIAS_NAME_DETAIL, validate_model_target, validate_path_name
from app.observability import logger
from app.portrait_response import exception_log_summary
from app.rollout_audit import write_rollout_audit
from app.settings import MODEL_CONFIG_HISTORY_DIR, MODEL_CONFIG_PATH


def model_config_path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def model_config_document_fingerprint(raw: dict[str, Any]) -> str:
    canonical = yaml.safe_dump(raw, allow_unicode=True, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_model_config_document(raw: dict[str, Any]) -> dict[str, Any]:
    models = models_mapping(raw)
    aliases = raw.get("aliases", {})
    if not isinstance(aliases, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="aliases must be a mapping")
    for model_id, config in models.items():
        if not isinstance(model_id, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model ids must be strings")
        validate_model_target(model_id)
        if not isinstance(config, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model configs must be mappings")
    for alias_name, alias_config in aliases.items():
        if not isinstance(alias_name, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="alias names must be strings")
        validate_alias_name(alias_name)
        try:
            targets = configured_alias_targets(alias_name, alias_config)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="alias config is invalid") from exc
        missing = [target for target in targets if target not in models]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="alias target is not configured",
            )
    return raw


def model_config_diff(before: Any, after: Any, path: str = "$") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after), key=str):
            child_path = f"{path}.{key}"
            if key not in before:
                changes.append({"path": child_path, "change": "added", "after": after[key]})
            elif key not in after:
                changes.append({"path": child_path, "change": "removed", "before": before[key]})
            else:
                changes.extend(model_config_diff(before[key], after[key], child_path))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        if before == after:
            return []
        return [{"path": path, "change": "changed", "before": before, "after": after}]
    if before != after:
        return [{"path": path, "change": "changed", "before": before, "after": after}]
    return []


def save_model_config_snapshot(raw: dict[str, Any]) -> str:
    fingerprint = model_config_document_fingerprint(raw)
    MODEL_CONFIG_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = MODEL_CONFIG_HISTORY_DIR / f"{fingerprint}.yml"
    if not snapshot_path.exists():
        temp_path = snapshot_path.with_name(f".{snapshot_path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("x", encoding="utf-8") as file:
                yaml.safe_dump(raw, file, allow_unicode=True, sort_keys=False)
            os.replace(temp_path, snapshot_path)
        finally:
            temp_path.unlink(missing_ok=True)
    return fingerprint


def load_model_config_snapshot(fingerprint: str) -> dict[str, Any]:
    normalized = fingerprint.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="config fingerprint is invalid")
    snapshot_path = MODEL_CONFIG_HISTORY_DIR / f"{normalized}.yml"
    if not snapshot_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="config snapshot was not found")
    try:
        raw = yaml.safe_load(snapshot_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="config snapshot could not be read",
        ) from exc
    if not isinstance(raw, dict) or model_config_document_fingerprint(raw) != normalized:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="config snapshot digest mismatch")
    return validate_model_config_document(raw)


def preview_model_config_change(candidate: dict[str, Any]) -> dict[str, Any]:
    current = load_raw_model_config()
    validate_model_config_document(candidate)
    return {
        "current_fingerprint": model_config_document_fingerprint(current),
        "candidate_fingerprint": model_config_document_fingerprint(candidate),
        "changes": model_config_diff(current, candidate),
    }


def apply_model_config_document(
    candidate: dict[str, Any],
    *,
    expected_current_fingerprint: str,
) -> dict[str, Any]:
    current = load_raw_model_config()
    current_fingerprint = model_config_document_fingerprint(current)
    if current_fingerprint != expected_current_fingerprint.strip().lower():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model config changed concurrently")
    preview = preview_model_config_change(candidate)
    previous_fingerprint = save_model_config_snapshot(current)
    write_raw_model_config(candidate)
    candidate_fingerprint = save_model_config_snapshot(candidate)
    return {
        **preview,
        "previous_fingerprint": previous_fingerprint,
        "applied_fingerprint": candidate_fingerprint,
        "written": True,
    }


def load_raw_model_config() -> dict[str, Any]:
    if not MODEL_CONFIG_PATH.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型配置文件不存在",
        )
    try:
        with MODEL_CONFIG_PATH.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}
    except Exception as exc:
        logger.warning(
            "读取模型配置文件失败: config_path_hash=%s error=%s",
            model_config_path_fingerprint(MODEL_CONFIG_PATH),
            exception_log_summary(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="读取模型配置文件失败",
        ) from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型配置根节点必须是映射")
    return raw


def write_raw_model_config(raw: dict[str, Any]) -> None:
    temp_path: Path | None = None
    try:
        MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = MODEL_CONFIG_PATH.with_name(f".{MODEL_CONFIG_PATH.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as file:
                yaml.safe_dump(raw, file, allow_unicode=True, sort_keys=False)
        except PermissionError:
            # A writable bind-mounted file may have a read-only container parent.
            with MODEL_CONFIG_PATH.open("w", encoding="utf-8") as file:
                yaml.safe_dump(raw, file, allow_unicode=True, sort_keys=False)
            return
        try:
            os.replace(temp_path, MODEL_CONFIG_PATH)
        except OSError:
            with MODEL_CONFIG_PATH.open("w", encoding="utf-8") as file:
                yaml.safe_dump(raw, file, allow_unicode=True, sort_keys=False)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                logger.warning("清理模型配置临时文件失败: %s", exception_log_summary(cleanup_exc))
    except Exception as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        logger.warning(
            "写入模型配置文件失败: config_path_hash=%s error=%s",
            model_config_path_fingerprint(MODEL_CONFIG_PATH),
            exception_log_summary(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="写入模型配置文件失败",
        ) from exc


def commit_model_config_with_audit(raw: dict[str, Any], previous_raw: dict[str, Any], event: str, result: dict[str, Any]) -> None:
    write_raw_model_config(raw)
    try:
        write_rollout_audit(event, result)
    except Exception as exc:
        logger.warning(
            "写入发布审计失败，正在回滚模型配置: error=%s",
            exception_log_summary(exc),
        )
        try:
            write_raw_model_config(previous_raw)
        except Exception as rollback_exc:
            logger.warning(
                "发布审计失败后回滚模型配置失败: error=%s",
                exception_log_summary(rollback_exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "message": "写入发布审计失败，且模型配置回滚失败",
                    "rolled_back": False,
                    "rollback_failed": True,
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "写入发布审计失败；模型配置已回滚",
                "rolled_back": True,
            },
        ) from exc


def models_mapping(raw: dict[str, Any]) -> dict[str, Any]:
    models = raw.get("models", raw)
    if not isinstance(models, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models 必须是映射")
    return models


def aliases_mapping(raw: dict[str, Any]) -> dict[str, Any]:
    aliases = raw.get("aliases")
    if aliases is None:
        aliases = {}
        raw["aliases"] = aliases
    if not isinstance(aliases, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="aliases 必须是映射")
    return aliases


def configured_model_device_id(config: dict[str, Any]) -> int | None:
    runtime = config.get("runtime")
    raw_device = runtime.get("device_id") if isinstance(runtime, dict) else config.get("device_id")
    if isinstance(raw_device, bool) or not isinstance(raw_device, (int, str)):
        return None
    try:
        return int(raw_device)
    except (TypeError, ValueError):
        return None


def configure_model_gpu_device(
    model_id: str,
    device_id: int | None,
    allowed_device_ids: list[int],
) -> dict[str, Any]:
    model_id = validate_model_target(model_id)
    allowed_devices = sorted({int(item) for item in allowed_device_ids})
    if device_id is not None and device_id not in allowed_devices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "GPU device is not available to this worker",
                "allowed_device_ids": allowed_devices,
            },
        )

    raw = load_raw_model_config()
    models = models_mapping(raw)
    config = models.get(model_id)
    if not isinstance(config, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型未配置")

    previous_device_id = configured_model_device_id(config)
    runtime = config.get("runtime")
    config.pop("device_id", None)
    if isinstance(runtime, dict):
        runtime.pop("device_id", None)
        if device_id is not None:
            runtime["device_id"] = device_id
    elif device_id is not None:
        config["device_id"] = device_id

    write_raw_model_config(raw)
    return {
        "model_id": model_id,
        "previous_device_id": previous_device_id,
        "device_id": device_id,
        "assignment": "fixed" if device_id is not None else "automatic",
    }


def current_alias_target(alias_name: str, alias_config: Any) -> str:
    try:
        return alias_target(alias_name, alias_config)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="解析别名失败",
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
            detail="目标模型未在 models.yml 中配置",
        )
    return target


def rollout_weight(value: Any) -> int:
    try:
        weight = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="目标权重必须是整数") from exc
    if weight < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="目标权重必须大于等于 0")
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
                "message": "别名当前目标与 expected_current_target 不一致",
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="targets 不能为空")
    expected_current_target = validate_model_target(expected_current_target) if expected_current_target is not None else None

    rollout_targets = []
    total_weight = 0
    for item in targets:
        if not isinstance(item, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="targets 必须是映射")
        target_model_id = str(item.get("target_model_id") or item.get("target") or "")
        weight = rollout_weight(item.get("weight", 0))
        target_model_id = validate_configured_target(target_model_id, models)
        total_weight += weight
        rollout_item: dict[str, Any] = {"target": target_model_id, "weight": weight}
        if item.get("status"):
            rollout_item["status"] = item["status"]
        rollout_targets.append(rollout_item)
    if total_weight <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="发布总权重必须大于 0")

    old_config = aliases.get(alias_name)
    old_target = current_alias_target(alias_name, old_config) if old_config is not None else None
    if expected_current_target is not None and old_target != expected_current_target:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "别名当前目标与 expected_current_target 不一致",
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


def configure_alias_shadow(
    alias_name: str,
    target_model_id: str | None,
    *,
    traffic_percentage: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    alias_name = validate_alias_name(alias_name)
    raw = load_raw_model_config()
    previous_raw = yaml.safe_load(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)) or {}
    models = models_mapping(raw)
    aliases = aliases_mapping(raw)
    alias_config = aliases.get(alias_name)
    if alias_config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alias was not found")
    current_target = current_alias_target(alias_name, alias_config)
    next_config = dict(alias_config) if isinstance(alias_config, dict) else {"target": current_target}
    if target_model_id is None:
        next_config.pop("shadow_target", None)
        next_config.pop("shadow_percentage", None)
    else:
        target_model_id = validate_configured_target(target_model_id, models)
        if target_model_id == current_target:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="shadow target must differ from active target")
        next_config["shadow_target"] = target_model_id
        next_config["shadow_percentage"] = max(1, min(100, int(traffic_percentage)))
    aliases[alias_name] = next_config
    result = {
        "alias": alias_name,
        "active_target": current_target,
        "shadow_target": target_model_id,
        "shadow_percentage": next_config.get("shadow_percentage", 0),
        "dry_run": dry_run,
        "written": not dry_run,
    }
    if not dry_run:
        commit_model_config_with_audit(raw, previous_raw, "alias_shadow_configured", result)
    return result


def rollback_alias_target(alias_name: str, dry_run: bool = False) -> dict[str, Any]:
    alias_name = validate_alias_name(alias_name)
    raw = load_raw_model_config()
    previous_raw = yaml.safe_load(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)) or {}
    models = models_mapping(raw)
    aliases = aliases_mapping(raw)
    if alias_name not in aliases:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="别名不存在")
    alias_config = aliases[alias_name]
    current_target = current_alias_target(alias_name, alias_config)
    if not isinstance(alias_config, dict) or not isinstance(alias_config.get("previous_target"), str):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="别名没有 previous_target")

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
