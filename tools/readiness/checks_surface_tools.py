"""暴露面与运维工具门禁：WebSocket 鉴权、依赖锁、调试/文档端点、工具入参校验。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.sources import load_sources


def check_surface_and_tools(root: Path) -> list[dict[str, Any]]:
    src = load_sources(root)
    portrait_ws_routes = src["portrait_ws_routes"]
    base_in = src["base_in"]
    base_lock = src["base_lock"]
    requirements_lock = src["requirements_lock"]
    requirements = src["requirements"]
    settings = src["settings"]
    debug_routes = src["debug_routes"]
    server = src["server"]
    compose = src["compose"]
    env_example = src["env_example"]
    service_smoke = src["service_smoke"]
    regression_check = src["regression_check"]
    worker_control = src["worker_control"]
    validate_model_package = src["validate_model_package"]
    model_refs = src["model_refs"]
    model_config_resolver = src["model_config_resolver"]
    return [
        {
            "name": "security:websocket_auth_gate",
            "ok": (
                "def require_websocket_permission" in portrait_ws_routes
                and "status.WS_1008_POLICY_VIOLATION" in portrait_ws_routes
                and "except HTTPException" in portrait_ws_routes
                and '"jobs:read"' in portrait_ws_routes
                and '"streams:read"' in portrait_ws_routes
            ),
        },
        {
            "name": "dependencies:runtime_lock_exact",
            "ok": (
                "cryptography>=48.0.1,<49.0.0" in base_in
                and "cryptography==48.0.1" in base_lock
                and "cryptography==48.0.1" in requirements_lock
                and "cryptography==48.0.1" in requirements
                and ">=" not in base_lock
                and ">=" not in requirements_lock
                and "<" not in base_lock
                and "<" not in requirements_lock
                and ">=" not in requirements
                and "<" not in requirements
            ),
        },
        {
            "name": "security:debug_endpoint_gate",
            "ok": "DEBUG_ENDPOINTS_ENABLED" in settings
            and "require_debug_endpoints_enabled" in debug_routes,
        },
        {
            "name": "security:api_docs_disabled_by_default",
            "ok": (
                "ENABLE_API_DOCS" in settings
                and 'docs_url="/docs" if ENABLE_API_DOCS else None' in server
                and 'redoc_url="/redoc" if ENABLE_API_DOCS else None' in server
                and 'openapi_url="/openapi.json" if ENABLE_API_DOCS else None' in server
                and "ENABLE_API_DOCS: ${ENABLE_API_DOCS:-false}" in compose
                and "ENABLE_API_DOCS=false" in env_example
            ),
        },
        {
            "name": "security:smoke_test_openapi_optional",
            "ok": (
                "def check_openapi" in service_smoke
                and "expected_status = {200} if required else {200, 404}"
                in service_smoke
                and "--check-openapi" in service_smoke
                and "openapi_optional" in service_smoke
                and "REQUIRED_OPENAPI_PATHS" in service_smoke
            ),
        },
        {
            "name": "tools:tenant_header_defaults",
            "ok": (
                '"X-Tenant-ID": tenant_id' in service_smoke
                and '"--tenant-id"' in service_smoke
                and 'default="default"' in service_smoke
                and 'headers.setdefault("X-Tenant-ID", tenant_id)' in regression_check
                and 'tenant_id = str(manifest.get("tenant_id", args.tenant_id))'
                in regression_check
                and '"--tenant-id"' in regression_check
                and 'headers = {"X-Tenant-ID": tenant_id}' in worker_control
                and '"--tenant-id"' in worker_control
            ),
        },
        {
            "name": "tools:worker_model_id_validation",
            "ok": (
                "def split_model_id" in worker_control
                and 'project_name, model_name = model_id.split("/", 1)'
                in worker_control
                and "part.strip() != part" in worker_control
                and 'part in {".", ".."}' in worker_control
                and '"/" in part' in worker_control
                and '"\\\\" in part' in worker_control
                and "模型项目和模型名称不能包含路径分隔符" in worker_control
            ),
        },
        {
            "name": "tools:model_package_key_validation",
            "ok": (
                "def validate_model_key_part" in validate_model_package
                and "value.strip() != value" in validate_model_package
                and 'value in {".", ".."}' in validate_model_package
                and '"/" in value' in validate_model_package
                and '"\\\\" in value' in validate_model_package
                and "validate_model_key_part(project" in validate_model_package
                and "validate_model_key_part(model" in validate_model_package
            ),
        },
        {
            "name": "tools:model_alias_target_validation",
            "ok": (
                "def alias_targets" in validate_model_package
                and "def validated_target" in validate_model_package
                and "split_model_key(target, result)" in validate_model_package
                and "normalized = target.strip()" not in validate_model_package
                and "def alias_weight" in validate_model_package
                and "别名灰度权重必须是整数" in validate_model_package
                and "别名灰度权重必须大于等于 0" in validate_model_package
                and 'rollout = rollout.get("targets") or rollout.get("candidates")'
                in validate_model_package
                and "return [target for _, target in candidates]"
                in validate_model_package
                and "def alias_target" in validate_model_package
                and "targets = alias_targets(alias_name, alias_config, result)"
                in validate_model_package
                and "for item in targets:" in validate_model_package
                and 'result.error(f"别名目标不在 models 映射中'
                in validate_model_package
                and 'result.warn(f"别名目标不在 models 映射中'
                not in validate_model_package
                and "def validate_model_target" in model_refs
                and 'INVALID_MODEL_REFERENCE_DETAIL = "模型引用无效"' in model_refs
                and 'INVALID_ALIAS_NAME_DETAIL = "别名名称无效"' in model_refs
                and "def validate_model_reference_parts" in model_refs
                and "def validate_alias_name" in model_refs
                and "detail=INVALID_MODEL_REFERENCE_DETAIL" in model_refs
                and "detail=INVALID_ALIAS_NAME_DETAIL" in model_refs
                and "split_cache_key(value)" in model_refs
                and "split_cache_key(value.strip())" not in model_config_resolver
                and "validate_model_reference_parts" in model_config_resolver
                and "validate_path_name" not in model_config_resolver
                and "validate_model_target(alias_config)" in model_config_resolver
                and "validate_model_target(target)" in model_config_resolver
                and "target_value = validate_model_target(target)"
                in model_config_resolver
                and "detail=str(exc)" not in model_config_resolver
                and 'detail="别名灰度发布没有正权重"' in model_config_resolver
                and 'detail="别名配置无效"' in model_config_resolver
                and 'detail="别名没有目标模型"' in model_config_resolver
                and 'detail=f"别名灰度发布没有正权重: {alias_name}"'
                not in model_config_resolver
                and 'detail=f"别名配置无效: {alias_name}"' not in model_config_resolver
                and 'detail=f"别名没有目标模型: {alias_name}"'
                not in model_config_resolver
            ),
        },
    ]
