import hashlib
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import HTTPException

from app.model_refs import validate_model_target, validate_path_name
from app.observability import logger
from app.portrait_response import exception_log_summary
from app.schemas import ModelConfig
from app.settings import MODEL_CONFIG_PATH, MODEL_CONFIG_READ_FAIL_CLOSED


def config_value_fingerprint(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:16]


def configured_model_entries(models: dict[Any, Any]) -> dict[str, ModelConfig]:
    valid_models: dict[str, ModelConfig] = {}
    for raw_key, value in models.items():
        if not isinstance(raw_key, str):
            logger.warning(
                "模型配置键必须是字符串，已跳过: key_hash=%s key_type=%s",
                config_value_fingerprint(raw_key),
                type(raw_key).__name__,
            )
            continue
        try:
            key = validate_model_target(raw_key)
        except Exception as exc:
            logger.warning(
                "已跳过无效模型配置键: key_hash=%s error=%s",
                config_value_fingerprint(raw_key),
                exception_log_summary(exc),
            )
            continue
        if not isinstance(value, dict):
            logger.warning("模型配置条目必须是映射，已跳过: key_hash=%s", config_value_fingerprint(key))
            continue
        valid_models[key] = normalize_model_config(key, value)
    return valid_models


def configured_alias_weight(alias_name: str, raw_weight: Any) -> int:
    try:
        weight = int(raw_weight or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"别名灰度权重必须是整数: {alias_name}") from exc
    if weight < 0:
        raise ValueError(f"别名灰度权重必须大于等于 0: {alias_name}")
    return weight


def configured_alias_targets(alias_name: str, value: Any) -> list[str]:
    if isinstance(value, str):
        return [validate_model_target(value)]
    if not isinstance(value, dict):
        raise ValueError(f"别名配置必须是字符串或映射：{alias_name}")

    target = value.get("target")
    if isinstance(target, str) and target:
        return [validate_model_target(target)]

    project_name = value.get("project_name")
    model_name = value.get("model_name")
    if isinstance(project_name, str) and isinstance(model_name, str):
        return [validate_model_target(f"{project_name}/{model_name}")]

    rollout = value.get("rollout")
    if isinstance(rollout, dict):
        rollout = rollout.get("targets") or rollout.get("candidates")
    if isinstance(rollout, list):
        candidates: list[tuple[int, str]] = []
        for item in rollout:
            if not isinstance(item, dict) or not isinstance(item.get("target"), str):
                continue
            weight = configured_alias_weight(alias_name, item.get("weight", 0))
            target_value = validate_model_target(item["target"])
            candidates.append((weight, target_value))
        if candidates:
            total_weight = sum(weight for weight, _ in candidates)
            if total_weight <= 0:
                raise ValueError(f"别名灰度发布没有正权重: {alias_name}")
            return [target for _, target in candidates]

    raise ValueError(f"别名没有目标模型: {alias_name}")


def configured_alias_target(alias_name: str, value: Any) -> str:
    targets = configured_alias_targets(alias_name, value)
    if len(targets) == 1:
        return targets[0]
    rollout = value.get("rollout") if isinstance(value, dict) else None
    if isinstance(rollout, dict):
        rollout = rollout.get("targets") or rollout.get("candidates")
    candidates: list[tuple[int, str]] = []
    if isinstance(rollout, list):
        for item in rollout:
            if not isinstance(item, dict) or not isinstance(item.get("target"), str):
                continue
            weight = configured_alias_weight(alias_name, item.get("weight", 0))
            target_value = validate_model_target(item["target"])
            candidates.append((weight, target_value))
    return max(candidates, key=lambda item: item[0])[1] if candidates else targets[0]


def configured_alias_entries(aliases: dict[Any, Any], models: dict[str, ModelConfig]) -> dict[str, Any]:
    valid_aliases: dict[str, Any] = {}
    for raw_key, value in aliases.items():
        if not isinstance(raw_key, str):
            logger.warning(
                "模型别名键必须是字符串，已跳过: alias_hash=%s key_type=%s",
                config_value_fingerprint(raw_key),
                type(raw_key).__name__,
            )
            continue
        try:
            alias_name = validate_path_name(raw_key)
        except ValueError as exc:
            logger.warning(
                "已跳过无效模型别名键: alias_hash=%s error=%s",
                config_value_fingerprint(raw_key),
                exception_log_summary(exc),
            )
            continue
        try:
            targets = configured_alias_targets(alias_name, value)
        except (HTTPException, TypeError, ValueError) as exc:
            logger.warning(
                "已跳过无效模型别名配置: alias_hash=%s error=%s",
                config_value_fingerprint(alias_name),
                exception_log_summary(exc),
            )
            continue
        missing_targets = [target for target in targets if target not in models]
        if missing_targets:
            logger.warning(
                "模型别名目标未配置，已跳过: alias_hash=%s unconfigured_target_count=%s",
                config_value_fingerprint(alias_name),
                len(set(missing_targets)),
            )
            continue
        valid_aliases[alias_name] = value
    return valid_aliases


def model_config_path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def empty_model_config_or_raise(message: str, exc: Exception | None = None) -> tuple[dict[str, ModelConfig], dict[str, Any]]:
    if exc is None:
        logger.warning(
            "%s: config_path_hash=%s",
            message,
            model_config_path_fingerprint(MODEL_CONFIG_PATH),
        )
    else:
        logger.warning(
            "%s: config_path_hash=%s error=%s",
            message,
            model_config_path_fingerprint(MODEL_CONFIG_PATH),
            exception_log_summary(exc),
        )
    if MODEL_CONFIG_READ_FAIL_CLOSED:
        raise RuntimeError(message) from None
    return {}, {}


def load_model_config_document() -> tuple[dict[str, ModelConfig], dict[str, Any]]:
    if not MODEL_CONFIG_PATH.is_file():
        return empty_model_config_or_raise("模型配置文件不存在")
    try:
        with MODEL_CONFIG_PATH.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}
    except Exception as exc:
        return empty_model_config_or_raise("读取模型配置文件失败", exc)

    if not isinstance(raw, dict):
        return empty_model_config_or_raise("模型配置文件根节点必须是映射")

    models = raw.get("models", raw)
    if not isinstance(models, dict):
        logger.warning(
            "模型配置文件缺少 models 映射: config_path_hash=%s",
            model_config_path_fingerprint(MODEL_CONFIG_PATH),
        )
        models = {}
    aliases = raw.get("aliases", {})
    if not isinstance(aliases, dict):
        logger.warning(
            "模型配置 aliases 必须是映射: config_path_hash=%s",
            model_config_path_fingerprint(MODEL_CONFIG_PATH),
        )
        aliases = {}
    model_entries = configured_model_entries(models)
    return model_entries, configured_alias_entries(aliases, model_entries)


def normalize_model_config(cache_key_value: str, raw_config: dict[str, Any]) -> ModelConfig:
    config = cast(ModelConfig, dict(raw_config))
    model_type = str(config.get("type") or config.get("task") or "").strip().lower()
    if model_type in {"yolo", "yolov8", "detector"}:
        config.setdefault("task", "detection")
        config.setdefault("type", "yolo")
    elif model_type in {"classification", "classifier", "image_classification"}:
        config.setdefault("task", "classification")
        config.setdefault("type", "classification")
    elif model_type in {"reid", "embedding", "embeddings"}:
        config.setdefault("task", "reid")
        config.setdefault("type", "reid")

    input_section = config.get("input")
    if not isinstance(input_section, dict):
        input_section = {}
        config["input"] = input_section
    output_section = config.get("output")
    if not isinstance(output_section, dict):
        output_section = {}
        config["output"] = output_section

    if "input_size" in config and "size" not in input_section:
        input_section["size"] = config["input_size"]
    if "confidence" in config and "confidence" not in output_section:
        output_section["confidence"] = config["confidence"]
    if "iou" in config and "iou" not in output_section:
        output_section["iou"] = config["iou"]
    if "classes" in config and "classes" not in output_section:
        output_section["classes"] = config["classes"]
    if "normalize" in config and "normalize" not in input_section:
        input_section["normalize"] = config["normalize"]

    artifact_section = config.get("artifact")
    if not isinstance(artifact_section, dict):
        config["artifact"] = {}
    rollout_section = config.get("rollout")
    if not isinstance(rollout_section, dict):
        config["rollout"] = {}

    if not config.get("task"):
        logger.warning(
            "模型配置缺少 task/type，需要显式任务路由: key_hash=%s",
            config_value_fingerprint(cache_key_value),
        )
    return config


__all__ = [
    "config_value_fingerprint",
    "configured_model_entries",
    "configured_alias_weight",
    "configured_alias_targets",
    "configured_alias_target",
    "configured_alias_entries",
    "model_config_path_fingerprint",
    "empty_model_config_or_raise",
    "load_model_config_document",
    "normalize_model_config",
]
