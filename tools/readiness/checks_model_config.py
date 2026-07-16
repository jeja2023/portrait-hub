"""模型配置供应链门禁：加载器键校验、fail-closed、sidecar、回归清单沙箱与报告脱敏。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.sources import load_sources


def check_model_config_supply(root: Path) -> list[dict[str, Any]]:
    src = load_sources(root)
    model_config_loader = src["model_config_loader"]
    settings = src["settings"]
    compose = src["compose"]
    env_example = src["env_example"]
    model_config_writer = src["model_config_writer"]
    model_package = src["model_package"]
    regression_check = src["regression_check"]
    regression_open_case_files = src["regression_open_case_files"]
    report_redaction = src["report_redaction"]
    service_smoke = src["service_smoke"]
    deploy_check = src["deploy_check"]
    worker_control = src["worker_control"]
    readme = src["readme"]
    deploy_ubuntu = src["deploy_ubuntu"]
    legacy_cross_camera_namespace = src["legacy_cross_camera_namespace"]
    project_docs = src["project_docs"]
    legacy_parent_models_path = src["legacy_parent_models_path"]
    return [
        {
            "name": "security:model_config_loader_key_validation",
            "ok": (
                "from app.model_refs import validate_model_target, validate_path_name"
                in model_config_loader
                and "from fastapi import HTTPException" in model_config_loader
                and "def config_value_fingerprint" in model_config_loader
                and "def configured_model_entries" in model_config_loader
                and "key = validate_model_target(raw_key)" in model_config_loader
                and "模型配置键必须是字符串，已跳过" in model_config_loader
                and "模型配置条目必须是映射，已跳过" in model_config_loader
                and "def configured_alias_targets" in model_config_loader
                and "return [target for _, target in candidates]" in model_config_loader
                and "def configured_alias_target" in model_config_loader
                and "def configured_alias_weight" in model_config_loader
                and "except (HTTPException, TypeError, ValueError) as exc"
                in model_config_loader
                and "别名灰度权重必须是整数" in model_config_loader
                and "模型别名目标未配置，已跳过" in model_config_loader
                and "missing_targets = [target for target in targets if target not in models]"
                in model_config_loader
                and "def configured_alias_entries" in model_config_loader
                and "alias_name = validate_path_name(raw_key)" in model_config_loader
                and "模型别名键必须是字符串，已跳过" in model_config_loader
                and "return model_entries, configured_alias_entries(aliases, model_entries)"
                in model_config_loader
            ),
        },
        {
            "name": "security:model_config_read_fail_closed",
            "ok": (
                "MODEL_CONFIG_READ_FAIL_CLOSED" in settings
                and 'MODEL_CONFIG_READ_FAIL_CLOSED = parse_bool_env("MODEL_CONFIG_READ_FAIL_CLOSED", True)'
                in settings
                and "from app.settings import MODEL_CONFIG_PATH, MODEL_CONFIG_READ_FAIL_CLOSED"
                in model_config_loader
                and "def empty_model_config_or_raise" in model_config_loader
                and "if MODEL_CONFIG_READ_FAIL_CLOSED:" in model_config_loader
                and "raise RuntimeError(message) from None" in model_config_loader
                and "模型配置文件不存在" in model_config_loader
                and "读取模型配置文件失败" in model_config_loader
                and "模型配置文件根节点必须是映射" in model_config_loader
                and "MODEL_CONFIG_READ_FAIL_CLOSED: ${MODEL_CONFIG_READ_FAIL_CLOSED:-true}"
                in compose
                and "MODEL_CONFIG_READ_FAIL_CLOSED=true" in env_example
                and "using built-in defaults" not in model_config_loader
            ),
        },
        {
            "name": "security:model_config_log_minimal_disclosure",
            "ok": (
                "def model_config_path_fingerprint" in model_config_loader
                and "def model_config_path_fingerprint" in model_config_writer
                and "config_path_hash=%s" in model_config_loader
                and "config_path_hash=%s" in model_config_writer
                and "exception_log_summary(exc)" in model_config_loader
                and model_config_writer.count("exception_log_summary(exc)") >= 3
                and "exception_log_summary(rollback_exc)" in model_config_writer
                and "key_hash=%s" in model_config_loader
                and "alias_hash=%s" in model_config_loader
                and "unconfigured_target_count=%s" in model_config_loader
                and "logger.exception(message)" not in model_config_loader
                and "模型配置键必须是字符串，已跳过: %r" not in model_config_loader
                and "已跳过无效模型配置键: %r (%s)" not in model_config_loader
                and "模型配置条目必须是映射，已跳过: %s" not in model_config_loader
                and "模型别名键必须是字符串，已跳过: %r" not in model_config_loader
                and "已跳过无效模型别名键: %r (%s)" not in model_config_loader
                and "已跳过无效模型别名配置: %s (%s)" not in model_config_loader
                and "模型别名目标未配置，已跳过: %s -> %s" not in model_config_loader
                and "模型配置缺少 task/type，需要显式任务路由: %s"
                not in model_config_loader
                and 'logger.exception("读取模型配置文件失败: %s", MODEL_CONFIG_PATH)'
                not in model_config_writer
                and 'logger.exception("写入模型配置文件失败: %s", MODEL_CONFIG_PATH)'
                not in model_config_writer
                and 'logger.exception("写入发布审计失败，正在回滚模型配置")'
                not in model_config_writer
                and 'logger.exception("发布审计失败后回滚模型配置失败")'
                not in model_config_writer
                and "模型配置文件不存在: {MODEL_CONFIG_PATH}" not in model_config_loader
                and "读取模型配置文件失败: {MODEL_CONFIG_PATH}"
                not in model_config_loader
                and "模型配置文件根节点必须是映射: {MODEL_CONFIG_PATH}"
                not in model_config_loader
                and '模型配置文件缺少 models 映射: %s", MODEL_CONFIG_PATH'
                not in model_config_loader
                and 'model config aliases 必须是映射: %s", MODEL_CONFIG_PATH'
                not in model_config_loader
            ),
        },
        {
            "name": "security:model_config_writer_target_validation",
            "ok": (
                "from app.model_config_resolver import alias_target"
                in model_config_writer
                and "INVALID_ALIAS_NAME_DETAIL" in model_config_writer
                and "from app.model_refs import INVALID_ALIAS_NAME_DETAIL, validate_model_target, validate_path_name"
                in model_config_writer
                and "def validate_alias_name" in model_config_writer
                and "detail=INVALID_ALIAS_NAME_DETAIL" in model_config_writer
                and "def validate_configured_target" in model_config_writer
                and "target = validate_model_target(target_model_id)"
                in model_config_writer
                and "return target" in model_config_writer
                and "expected_current_target = validate_model_target(expected_current_target)"
                in model_config_writer
                and "target_model_id = validate_configured_target(target_model_id, models)"
                in model_config_writer
                and 'rollback_target = validate_configured_target(alias_config["previous_target"], models)'
                in model_config_writer
                and "def rollout_weight" in model_config_writer
                and "targets 必须是映射" in model_config_writer
                and 'detail="目标模型未在 models.yml 中配置"' in model_config_writer
                and 'detail="解析别名失败"' in model_config_writer
                and 'detail="别名不存在"' in model_config_writer
                and 'detail="别名没有 previous_target"' in model_config_writer
                and 'detail=f"目标模型未在 models.yml 中配置: {target}"'
                not in model_config_writer
                and 'detail=f"解析别名失败 {alias_name}: {exc}"'
                not in model_config_writer
                and 'detail=f"别名不存在: {alias_name}"' not in model_config_writer
                and 'detail=f"别名没有 previous_target: {alias_name}"'
                not in model_config_writer
                and '"expected_current_target": expected_current_target'
                not in model_config_writer
                and '"actual_current_target": old_target' not in model_config_writer
            ),
        },
        {
            "name": "security:explicit_model_sidecars_fail_closed",
            "ok": (
                "def load_yaml_sidecar(path: Path, *, required: bool = False)"
                in model_package
                and "def load_text_labels(path: Path, *, required: bool = False)"
                in model_package
                and "模型附属 YAML 不存在" in model_package
                and "模型附属 YAML 根节点必须是映射" in model_package
                and "模型标签文件不存在" in model_package
                and "模型标签文件为空" in model_package
                and "def sidecar_path_fingerprint" in model_package
                and "sidecar_path_hash=%s" in model_package
                and "exception_log_summary(exc)" in model_package
                and 'logger.error("必需的模型附属 YAML 不存在: %s", path)'
                not in model_package
                and 'logger.error("必需的模型标签文件不存在: %s", path)'
                not in model_package
                and 'logger.exception("读取模型附属 YAML 失败: %s", path)'
                not in model_package
                and 'logger.exception("读取模型标签失败: %s", path)'
                not in model_package
                and 'logger.error("模型附属 YAML 根节点必须是映射: %s", path)'
                not in model_package
                and 'logger.error("模型标签文件为空: %s", path)' not in model_package
                and 'detail=f"模型附属 YAML 不存在: {path.name}"' not in model_package
                and 'detail=f"读取模型附属 YAML 失败: {path.name}"' not in model_package
                and 'detail=f"模型附属 YAML 根节点必须是映射: {path.name}"'
                not in model_package
                and 'detail=f"模型标签文件不存在: {path.name}"' not in model_package
                and 'detail=f"读取模型标签失败: {path.name}"' not in model_package
                and 'detail=f"模型标签文件为空: {path.name}"' not in model_package
                and "load_text_labels(safe_sidecar_path(model_path, labels_path.strip()), required=True)"
                in model_package
                and "load_yaml_sidecar(safe_sidecar_path(model_path, card_path.strip()), required=True)"
                in model_package
            ),
        },
        {
            "name": "tools:regression_manifest_path_sandbox",
            "ok": (
                "def manifest_relative_path" in regression_check
                and "candidate.is_absolute()" in regression_check
                and "resolved.relative_to(base)" in regression_check
                and 'manifest_relative_path(base_dir, expected_path, "case.expected_path")'
                in regression_check
                and 'manifest_relative_path(base_dir, raw_path, f"case.files.{field}")'
                in regression_check
                and "except Exception:" in regression_open_case_files
                and "handle.close()" in regression_open_case_files
            ),
        },
        {
            "name": "tools:report_output_redaction",
            "ok": (
                "def redact_for_report" in report_redaction
                and "def safe_report_repr" in report_redaction
                and '"api_key"' in report_redaction
                and '"authorization"' in report_redaction
                and '"token"' in report_redaction
                and "redact_for_report(detail)" in service_smoke
                and "redact_for_report(detail)" in deploy_check
                and "redact_for_report(payload)" in worker_control
                and "redact_for_report(str(exc))" in worker_control
                and "safe_report_repr(expected, path)" in regression_check
                and "safe_report_repr(actual, path)" in regression_check
            ),
        },
        {
            "name": "docs:portrait_hub_model_paths",
            "ok": (
                "portrait_hub/yolov8n.onnx" in readme
                and "portrait_hub/osnet_ibn_x1_0.onnx" in readme
                and "MODELS_HOST_DIR=./models" in deploy_ubuntu
                and "portrait_hub/yolov8n.onnx" in deploy_ubuntu
                and "portrait_hub/osnet_ibn_x1_0.onnx" in deploy_ubuntu
                and legacy_cross_camera_namespace not in project_docs
                and "person_service" not in project_docs
                and legacy_parent_models_path not in project_docs
            ),
        },
    ]
