import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException, status

from app.constants import COCO_CLASSES
from app.model_config import config_section, config_value, configured_sha256, model_config, model_task
from app.observability import logger
from app.portrait_response import exception_log_summary
from app.settings import MODELS_ROOT


def resolve_model_artifact_path(cache_key_value: str, project_name: str, model_name: str) -> Path:
    artifact_path = config_section(model_config(cache_key_value), "artifact").get("path")
    if isinstance(artifact_path, str) and artifact_path.strip():
        candidate = Path(artifact_path.strip())
        if candidate.is_absolute():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="模型 artifact.path 必须相对于 models 目录",
            )
        return (MODELS_ROOT / candidate).resolve()
    return (MODELS_ROOT / project_name / model_name).resolve()


def get_model_path(project_name: str, model_name: str) -> Path:
    cache_key_value = f"{project_name}/{model_name}"
    model_path = resolve_model_artifact_path(cache_key_value, project_name, model_name)
    try:
        model_path.relative_to(MODELS_ROOT)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="模型路径必须位于 models 目录内",
        ) from exc

    if not model_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型构件不存在",
        )
    return model_path


def model_hash(model_path: Path) -> str:
    digest = hashlib.sha256()
    with model_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_sidecar_path(model_path: Path, relative_path: str) -> Path:
    model_dir = model_path.parent.resolve()
    sidecar_path = (model_dir / relative_path).resolve()
    try:
        sidecar_path.relative_to(model_dir)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="模型附属文件路径必须位于模型项目目录内",
        ) from exc
    return sidecar_path


def sidecar_path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def load_yaml_sidecar(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.is_file():
        if required:
            logger.error("必需的模型附属 YAML 不存在: sidecar_path_hash=%s", sidecar_path_fingerprint(path))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="模型附属 YAML 不存在",
            )
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}
    except Exception as exc:
        logger.warning(
            "读取模型附属 YAML 失败: sidecar_path_hash=%s error=%s",
            sidecar_path_fingerprint(path),
            exception_log_summary(exc),
        )
        if required:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="读取模型附属 YAML 失败",
            ) from exc
        return {}
    if not isinstance(raw, dict):
        if required:
            logger.error(
                "模型附属 YAML 根节点必须是映射: sidecar_path_hash=%s",
                sidecar_path_fingerprint(path),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="模型附属 YAML 根节点必须是映射",
            )
        return {}
    return raw


def load_text_labels(path: Path, *, required: bool = False) -> list[str]:
    if not path.is_file():
        if required:
            logger.error("必需的模型标签文件不存在: sidecar_path_hash=%s", sidecar_path_fingerprint(path))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="模型标签文件不存在",
            )
        return []
    try:
        with path.open("r", encoding="utf-8") as file:
            labels = [line.strip() for line in file if line.strip() and not line.lstrip().startswith("#")]
    except Exception as exc:
        logger.warning(
            "读取模型标签失败: sidecar_path_hash=%s error=%s",
            sidecar_path_fingerprint(path),
            exception_log_summary(exc),
        )
        if required:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="读取模型标签失败",
            ) from exc
        return []
    if required and not labels:
        logger.error("模型标签文件为空: sidecar_path_hash=%s", sidecar_path_fingerprint(path))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="模型标签文件为空",
        )
    return labels


def labels_from_config(config: Mapping[str, Any], model_path: Path | None = None) -> list[str]:
    labels_path = config_section(config, "artifact").get("labels")
    if model_path is not None and isinstance(labels_path, str) and labels_path.strip():
        return load_text_labels(safe_sidecar_path(model_path, labels_path.strip()), required=True)

    raw_classes = config_value(config, "output", "classes")
    if isinstance(raw_classes, list):
        return [str(item) for item in raw_classes]
    if isinstance(raw_classes, str) and raw_classes.lower() == "coco":
        return COCO_CLASSES

    if model_path is not None:
        default_labels_path = model_path.with_suffix(".labels.txt")
        labels = load_text_labels(default_labels_path)
        if labels:
            return labels
    return []


def class_name(class_id: int, labels: list[str] | None = None) -> str:
    if labels and 0 <= class_id < len(labels):
        return labels[class_id]
    return str(class_id)


def parse_class_filter(raw_filter: Any, labels: list[str]) -> set[int] | None:
    if raw_filter is None or raw_filter == "":
        return None
    if not isinstance(raw_filter, list):
        raw_filter = [raw_filter]

    label_to_id = {label.lower(): index for index, label in enumerate(labels)}
    class_ids: set[int] = set()
    for item in raw_filter:
        if isinstance(item, int):
            class_ids.add(item)
        elif isinstance(item, str):
            value = item.strip()
            if value.isdigit():
                class_ids.add(int(value))
            elif value.lower() in label_to_id:
                class_ids.add(label_to_id[value.lower()])
            elif value.lower() == "person":
                class_ids.add(0)
    return class_ids or None


def model_card_for_path(config: Mapping[str, Any], model_path: Path) -> dict[str, Any]:
    card_path = config_section(config, "artifact").get("model_card")
    if isinstance(card_path, str) and card_path.strip():
        return load_yaml_sidecar(safe_sidecar_path(model_path, card_path.strip()), required=True)
    default_card_path = model_path.with_suffix(".model-card.yml")
    return load_yaml_sidecar(default_card_path)


def model_package_info(cache_key_value: str, model_path: Path, digest: str | None = None) -> dict[str, Any]:
    config = model_config(cache_key_value)
    labels = labels_from_config(config, model_path)
    card = model_card_for_path(config, model_path)
    expected_sha256 = configured_sha256(config)
    artifact = config_section(config, "artifact")
    return {
        "model": cache_key_value,
        "task": model_task(config),
        "type": config.get("type"),
        "runtime": config.get("runtime", "onnxruntime"),
        "version": config.get("version") or config_section(card, "model").get("version"),
        "precision": config.get("precision") or config_section(card, "model").get("precision"),
        "artifact": {
            "path_configured": bool(artifact.get("path")),
            "model_card_configured": bool(artifact.get("model_card")),
            "labels_configured": bool(artifact.get("labels")),
            "sha256": expected_sha256,
            "sha256_match": None if not expected_sha256 or not digest else expected_sha256 == digest.lower(),
        },
        "labels": {
            "count": len(labels),
            "items": labels,
        },
        "model_card": card,
    }


def public_model_config(cache_key_value: str, config: Mapping[str, Any], *, loaded: bool = False) -> dict[str, Any]:
    artifact = config_section(config, "artifact")
    raw_device_id = config_value(config, "runtime", "device_id", config.get("device_id"))
    try:
        gpu_device_id = None if isinstance(raw_device_id, bool) else int(raw_device_id)
    except (TypeError, ValueError):
        gpu_device_id = None
    return {
        "model_id": cache_key_value,
        "task": config.get("task"),
        "type": config.get("type"),
        "runtime": config.get("runtime"),
        "version": config.get("version"),
        "precision": config.get("precision"),
        "gpu_device_id": gpu_device_id,
        "rollout": config.get("rollout", {}),
        "thresholds": config.get("thresholds", {}),
        "artifact": {
            "path_configured": bool(artifact.get("path")),
            "model_card_configured": bool(artifact.get("model_card")),
            "labels_configured": bool(artifact.get("labels")),
            "sha256_configured": bool(configured_sha256(config)),
        },
        "loaded": loaded,
    }


def validate_model_hash(cache_key_value: str, digest: str) -> None:
    expected = configured_sha256(model_config(cache_key_value))
    if expected and expected != digest.lower():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "model sha256 does not match configured artifact hash",
                "model": cache_key_value,
                "expected_sha256": expected,
                "actual_sha256": digest,
            },
        )

