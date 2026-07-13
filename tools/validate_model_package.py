"""在服务中加载已配置的模型包之前，对其进行有效性验证的脚本。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_yaml(path: Path, result: CheckResult) -> dict[str, Any]:
    if not path.is_file():
        result.error(f"配置文件不存在：{path}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}
    except Exception as exc:
        result.error(f"读取 YAML 失败 {path}：{exc}")
        return {}
    if not isinstance(raw, dict):
        result.error(f"yaml 根节点必须是映射: {path}")
        return {}
    return raw


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model_key_part(value: str, key: str, field: str, result: CheckResult) -> bool:
    if value.strip() != value or value in {".", ".."} or "/" in value or "\\" in value:
        result.error(f"模型键 {field} 不能包含路径分隔符、首尾空白或相对路径片段：{key}")
        return False
    return True


def split_model_key(key: str, result: CheckResult) -> tuple[str, str] | None:
    if "/" not in key:
        result.error(f"模型键必须是 project/model.onnx：{key}")
        return None
    project, model = key.split("/", 1)
    if not project or not model:
        result.error(f"模型键必须包含项目和模型名称：{key}")
        return None
    if not validate_model_key_part(project, key, "project", result):
        return None
    if not validate_model_key_part(model, key, "model", result):
        return None
    return project, model


def safe_sidecar(model_path: Path, relative_path: str, result: CheckResult) -> Path | None:
    sidecar = (model_path.parent / relative_path).resolve()
    try:
        sidecar.relative_to(model_path.parent.resolve())
    except ValueError:
        result.error(f"sidecar 路径逃逸模型目录: {relative_path}")
        return None
    return sidecar


def model_artifact_path(key: str, config: dict[str, Any], models_root: Path, project: str, model: str, result: CheckResult) -> Path | None:
    artifact = section(config, "artifact")
    raw_path = artifact.get("path")
    if isinstance(raw_path, str) and raw_path.strip():
        configured_path = Path(raw_path.strip())
        if configured_path.is_absolute():
            result.error(f"{key}: artifact.path 必须相对于模型根目录")
            return None
        model_path = (models_root / configured_path).resolve()
    else:
        model_path = (models_root / project / model).resolve()
    try:
        model_path.relative_to(models_root.resolve())
    except ValueError:
        result.error(f"{key}: 模型路径逃逸模型根目录")
        return None
    return model_path


def alias_weight(alias_name: str, raw_weight: Any, result: CheckResult) -> int | None:
    try:
        weight = int(raw_weight or 0)
    except (TypeError, ValueError):
        result.error(f"别名灰度权重必须是整数: {alias_name}")
        return None
    if weight < 0:
        result.error(f"别名灰度权重必须大于等于 0: {alias_name}")
        return None
    return weight


def alias_targets(alias_name: str, alias_config: Any, result: CheckResult) -> list[str]:
    def validated_target(target: str) -> str | None:
        if not target:
            return None
        return target if split_model_key(target, result) is not None else None

    if isinstance(alias_config, str):
        target = validated_target(alias_config)
        return [target] if target else []
    if not isinstance(alias_config, dict):
        result.error(f"别名配置必须是字符串或映射：{alias_name}")
        return []

    target = alias_config.get("target")
    if isinstance(target, str) and target.strip():
        target_value = validated_target(target)
        return [target_value] if target_value else []

    project_name = alias_config.get("project_name")
    model_name = alias_config.get("model_name")
    if isinstance(project_name, str) and isinstance(model_name, str):
        target_value = validated_target(f"{project_name}/{model_name}")
        return [target_value] if target_value else []

    rollout = alias_config.get("rollout")
    if isinstance(rollout, dict):
        rollout = rollout.get("targets") or rollout.get("candidates")
    if isinstance(rollout, list) and rollout:
        candidates = []
        for item in rollout:
            if not isinstance(item, dict) or not isinstance(item.get("target"), str):
                continue
            weight = alias_weight(alias_name, item.get("weight", 0), result)
            if weight is None:
                continue
            target = validated_target(item["target"])
            if target:
                candidates.append((weight, target))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            return [target for _, target in candidates]

    result.error(f"别名没有目标模型: {alias_name}")
    return []


def alias_target(alias_name: str, alias_config: Any, result: CheckResult) -> str | None:
    targets = alias_targets(alias_name, alias_config, result)
    return targets[0] if targets else None


def section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def list_labels(labels_path: Path) -> list[str]:
    with labels_path.open("r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip() and not line.lstrip().startswith("#")]


def validate_sidecars(
    key: str,
    config: dict[str, Any],
    model_path: Path,
    result: CheckResult,
    strict_sidecars: bool,
) -> dict[str, Any]:
    artifact = section(config, "artifact")
    output = section(config, "output")
    sidecar_info: dict[str, Any] = {"model_card": None, "labels": None}

    card_name = artifact.get("model_card")
    card_path = safe_sidecar(model_path, card_name.strip(), result) if isinstance(card_name, str) and card_name.strip() else model_path.with_suffix(".model-card.yml")
    if card_path and card_path.is_file():
        sidecar_info["model_card"] = str(card_path)
        card = load_yaml(card_path, result)
        if not section(card, "model").get("version") and not config.get("version"):
            result.warn(f"{key}: 模型卡或配置应包含 model.version")
        if not section(card, "evaluation") and not section(card, "metrics"):
            result.warn(f"{key}: 模型卡应包含 evaluation 或 metrics")
    elif strict_sidecars:
        result.error(f"{key}: 模型卡不存在：{card_path}")
    else:
        result.warn(f"{key}: 模型卡不存在：{card_path}")

    labels_name = artifact.get("labels")
    labels_path = safe_sidecar(model_path, labels_name.strip(), result) if isinstance(labels_name, str) and labels_name.strip() else model_path.with_suffix(".labels.txt")
    classes = output.get("classes")
    has_inline_labels = isinstance(classes, list) or (isinstance(classes, str) and classes.lower() == "coco")
    if labels_path and labels_path.is_file():
        labels = list_labels(labels_path)
        sidecar_info["labels"] = {"path": str(labels_path), "count": len(labels)}
        if not labels:
            result.error(f"{key}: 标签文件为空: {labels_path}")
    elif not has_inline_labels and config.get("task") in {"detection", "classification"}:
        if strict_sidecars:
            result.error(f"{key}: 标签文件不存在且未提供 output.classes：{labels_path}")
        else:
            result.warn(f"{key}: 标签文件不存在且未提供 output.classes：{labels_path}")

    return sidecar_info


def validate_model(
    key: str,
    config: dict[str, Any],
    models_root: Path,
    strict_hash: bool,
    strict_sidecars: bool,
    result: CheckResult,
) -> dict[str, Any]:
    model_info: dict[str, Any] = {"key": key, "exists": False}
    split = split_model_key(key, result)
    if split is None:
        return model_info

    project, model = split
    model_path = model_artifact_path(key, config, models_root, project, model, result)
    if model_path is None:
        return model_info

    model_info["path"] = str(model_path)
    if not model_path.is_file():
        result.error(f"{key}: 模型文件不存在：{model_path}")
        return model_info

    model_info["exists"] = True
    model_info["bytes"] = model_path.stat().st_size

    task = str(config.get("task") or config.get("type") or "").lower()
    if task not in {"detection", "classification", "reid", "yolo"}:
        result.warn(f"{key}: task/type 不是已知的一阶段任务: {task or '<missing>'}")

    input_config = section(config, "input")
    output_config = section(config, "output")
    if not input_config.get("size") and not config.get("input_size"):
        result.warn(f"{key}: 缺少 input.size；服务将从 ONNX shape 推断")
    if not output_config and task in {"detection", "classification", "reid", "yolo"}:
        result.warn(f"{key}: 缺少 output 章节")

    expected_sha = str(section(config, "artifact").get("sha256") or config.get("sha256") or "").strip().lower()
    digest = sha256_file(model_path)
    model_info["sha256"] = digest
    if expected_sha:
        model_info["expected_sha256"] = expected_sha
        if expected_sha != digest:
            result.error(f"{key}: sha256 不匹配：期望 {expected_sha}，实际 {digest}")
    elif strict_hash:
        result.error(f"{key}: 严格模式下 artifact.sha256 为必填项")
    else:
        result.warn(f"{key}: artifact.sha256 为空")

    model_info["sidecars"] = validate_sidecars(key, config, model_path, result, strict_sidecars)
    return model_info


def validate_config(args: argparse.Namespace) -> dict[str, Any]:
    result = CheckResult()
    config_path = Path(args.config).resolve()
    models_root = Path(args.models_root or os.getenv("MODELS_ROOT", "models")).resolve()
    raw = load_yaml(config_path, result)
    models = raw.get("models", raw)
    aliases = raw.get("aliases", {})
    if not isinstance(models, dict):
        result.error("models 必须是映射")
        models = {}
    if not isinstance(aliases, dict):
        result.error("aliases 必须是映射")
        aliases = {}

    selected = set(args.model_id or [])
    model_items = {str(key): value for key, value in models.items() if isinstance(value, dict)}
    if selected:
        resolved: set[str] = set()
        for item in selected:
            if item in aliases:
                target = alias_target(item, aliases[item], result)
                if target:
                    resolved.add(target)
            else:
                resolved.add(item)
        model_items = {key: value for key, value in model_items.items() if key in resolved}
        missing = sorted(resolved - set(model_items))
        for key in missing:
            result.error(f"所选模型未配置: {key}")

    alias_info = {}
    for name, config in aliases.items():
        targets = alias_targets(str(name), config, result)
        target = targets[0] if targets else None
        alias_info[str(name)] = target
        for item in targets:
            if item not in models:
                result.error(f"别名目标不在 models 映射中: {name} -> {item}")

    checked_models = [
        validate_model(
            key,
            config,
            models_root,
            strict_hash=args.strict_hash,
            strict_sidecars=args.strict_sidecars,
            result=result,
        )
        for key, config in sorted(model_items.items())
    ]

    return {
        "ok": result.ok,
        "config_path": str(config_path),
        "models_root": str(models_root),
        "model_count": len(checked_models),
        "aliases": alias_info,
        "models": checked_models,
        "warnings": result.warnings,
        "errors": result.errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 models.yml 引用的模型包文件。")
    parser.add_argument("--config", default="models.yml", help="models.yml 路径。")
    parser.add_argument("--models-root", default=None, help="模型根目录。默认使用 MODELS_ROOT 或 models。")
    parser.add_argument("--model-id", action="append", help="只校验此模型键或别名。可重复传入。")
    parser.add_argument("--strict-hash", action="store_true", help="要求 artifact.sha256 并进行校验。")
    parser.add_argument("--strict-sidecars", action="store_true", help="在适用位置要求模型卡和标签。")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    args = parser.parse_args()

    report = validate_config(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "通过" if report["ok"] else "失败"
        print(f"模型包校验：{status}")
        print(f"配置: {report['config_path']}")
        print(f"模型根目录: {report['models_root']}")
        print(f"已检查模型数: {report['model_count']}")
        for warning in report["warnings"]:
            print(f"警告: {warning}")
        for error in report["errors"]:
            print(f"错误: {error}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
