"""平台架构与工程质量门禁：模块拆分真实性、类型检查、热更新、可观测性、生产外部化。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.sources import load_sources


def check_platform_quality(root: Path) -> list[dict[str, Any]]:
    src = load_sources(root)
    settings = src["settings"]
    security = src["security"]
    core = src["core"]
    route_modules = src["route_modules"]
    pyproject = src["pyproject"]
    dev_requirements = src["dev_requirements"]
    ci_workflow = src["ci_workflow"]
    type_check_tool = src["type_check_tool"]
    portrait_tracking = src["portrait_tracking"]
    tracking_state = src["tracking_state"]
    tracking_association = src["tracking_association"]
    runtime_face = src["runtime_face"]
    runtime_body = src["runtime_body"]
    runtime_pose = src["runtime_pose"]
    runtime_gait = src["runtime_gait"]
    runtime_appearance = src["runtime_appearance"]
    server = src["server"]
    config_hot_reload = src["config_hot_reload"]
    observability = src["observability"]
    runtime_execution = src["runtime_execution"]
    portrait_vector_store = src["portrait_vector_store"]
    postgres_core = src["postgres_core"]
    console_next_package = src["console_next_package"]
    console_next_vite = src["console_next_vite"]
    console_next_session = src["console_next_session"]
    console_next_ws = (
        (root / "frontend" / "console-next" / "src" / "api" / "ws.ts").read_text(encoding="utf-8")
        if (root / "frontend" / "console-next" / "src" / "api" / "ws.ts").is_file()
        else ""
    )
    console_next_manifest = src["console_next_manifest"]
    portrait_console_routes = src["portrait_console_routes"]
    portrait_gallery_orchestration = src["portrait_gallery_orchestration"]
    portrait_gallery_routes = src["portrait_gallery_routes"]
    portrait_runtime_store = src["portrait_runtime_store"]
    portrait_admin_routes = src["portrait_admin_routes"]
    portrait_job_routes = src["portrait_job_routes"]
    portrait_task_queue = src["portrait_task_queue"]
    production_gates = src["production_gates"]
    compose = src["compose"]
    env_example = src["env_example"]
    console_module_sources = src["console_module_sources"]
    legacy_console_flag_sources = "\n".join([settings, security, env_example, compose, ci_workflow, portrait_console_routes])
    removed_console_flags = (
        "CONSOLE_DEFAULT_VERSION",
        "CONSOLE_WORKBENCH_V2",
        "CONSOLE_DEVELOPER_V2",
        "CONSOLE_ADMIN_V2",
    )
    return [
        {
            "name": "security:auth_required_setting",
            "ok": "AUTH_REQUIRED" in settings and "AUTH_REQUIRED" in security,
        },
        {
            "name": "quality:core_explicit_imports",
            "ok": ("__all__" in core and "import *" not in core and "from app.core import *" not in route_modules),
        },
        {
            "name": "quality:strict_type_check_gate",
            "ok": (
                "[tool.mypy]" in pyproject
                and "strict = true" in pyproject
                and 'requires-python = ">=3.12"' in pyproject
                and "mypy==" in dev_requirements
                and "python tools/type_check.py" in ci_workflow
                and "discover_default_targets" in type_check_tool
                and "DEFAULT_TARGET_ROOTS" in type_check_tool
                and "--fallback-ok" in type_check_tool
                and "mypy is not installed; install requirements/dev.txt" in type_check_tool
                and '"app"' in type_check_tool
                and '"tools"' in type_check_tool
                and '"sdk"' in type_check_tool
                and 'rglob("*.py")' in type_check_tool
            ),
        },
        {
            "name": "quality:tracking_split_is_real",
            "ok": (
                "from app.tracking_association import" in portrait_tracking
                and "from app.tracking_state import" in portrait_tracking
                and "from app.portrait_tracking import" not in tracking_state
                and "from app.portrait_tracking import" not in tracking_association
                and "class TrackState" in tracking_state
                and "def associate_person_tracks" in tracking_association
            ),
        },
        {
            "name": "quality:runtime_split_is_real",
            "ok": (
                "from app.portrait_model_runtime import" not in runtime_face
                and "from app.portrait_model_runtime import" not in runtime_body
                and "from app.portrait_model_runtime import" not in runtime_pose
                and "from app.portrait_model_runtime import" not in runtime_gait
                and "from app.portrait_model_runtime import" not in runtime_appearance
                and "def run_scrfd_face_detection" in runtime_face
                and "def run_reid_body_embedding" in runtime_body
                and "def run_rtmpose" in runtime_pose
                and "def run_opengait" in runtime_gait
                and "def run_attribute_reid_appearance" in runtime_appearance
            ),
        },
        {
            "name": "ops:config_sighup_hot_reload",
            "ok": (
                "def install_config_reload_signal_handler" in server
                and 'getattr(signal, "SIGHUP"' in server
                and 'reload_runtime_config(source="sighup"' in server
                and "ENV_PATH" in server
                and "def reload_runtime_config" in config_hot_reload
                and "audit_event(" in config_hot_reload
            ),
        },
        {
            "name": "observability:explicit_opentelemetry_spans",
            "ok": (
                "def trace_span" in observability
                and "portrait.inference.run_session" in runtime_execution
                and "portrait.vector.pgvector.search" in portrait_vector_store
                and "portrait.vector.qdrant.search" in portrait_vector_store
                and "portrait.postgres.connection" in postgres_core
            ),
        },
        {
            "name": "frontend:legacy_console_removed",
            "ok": (
                not (root / "frontend" / "console").exists()
                and "CONSOLE_DEFAULT_VERSION" not in settings
                and '"/console/legacy"' not in portrait_console_routes
                and '"/assets/console/{asset_path:path}"' not in portrait_console_routes
                and '"/assets/console.js"' not in portrait_console_routes
                and '"/assets/console.css"' not in portrait_console_routes
                and '"/assets/console.config.js"' not in portrait_console_routes
                and all(flag not in legacy_console_flag_sources for flag in removed_console_flags)
                and "/assets/console/" not in console_module_sources
                and "data-view=" not in console_module_sources
                and "PortraitConsoleModules" not in console_module_sources
            ),
        },
        {
            "name": "frontend:console_next_production_chain",
            "ok": (
                '"vue": "3.5.40"' in console_next_package
                and '"element-plus": "2.14.3"' in console_next_package
                and 'base: "/assets/console-next/"' in console_next_vite
                and "window.sessionStorage" in console_next_session
                and "window.localStorage" not in console_next_session
                and "index.html" in console_next_manifest
                and '"/"' in portrait_console_routes
                and '"/console"' in portrait_console_routes
                and '"/console/next"' in portrait_console_routes
                and '"/assets/console-next/{asset_path:path}"' in portrait_console_routes
                and "def next_console_csp" in portrait_console_routes
                and "script-src 'self'" in portrait_console_routes
                and "unsafe-eval" not in portrait_console_routes
                and 'node-version: "22"' in ci_workflow
                and "npm run console:generate" in ci_workflow
                and "npm run console:build" in ci_workflow
            ),
        },
        {
            "name": "backend:gallery_orchestration_split",
            "ok": (
                "def enroll_gallery_person" in portrait_gallery_orchestration
                and "def search_gallery_image" in portrait_gallery_orchestration
                and "def create_async_gallery_search_batch" in portrait_gallery_orchestration
                and "decode_upload_images" in portrait_gallery_orchestration
                and "rollback_gallery_mutation" in portrait_gallery_orchestration
                and "enroll_gallery_person" in portrait_gallery_routes
                and "search_gallery_batch" in portrait_gallery_routes
                and "decode_upload_image" not in portrait_gallery_routes
                and "create_batch_job" not in portrait_gallery_routes
            ),
        },
        {
            "name": "backend:runtime_store_lifecycle",
            "ok": (
                "class RuntimeStateStore" in portrait_runtime_store
                and "gallery_people_snapshots" in portrait_runtime_store
                and "video_jobs_snapshots" in portrait_runtime_store
                and "task_message_snapshots" in portrait_runtime_store
                and "video_jobs_snapshots(tenant_id)" in portrait_admin_routes
                and "video_jobs_snapshots(tenant_id)" in portrait_job_routes
                and (
                    "restore_video_job_in_store(job)" in portrait_job_routes
                    or "persist_video_job(job)" in portrait_job_routes
                )
                and "TASK_MESSAGE_STORE" in portrait_task_queue
                and "TASK_MESSAGE_STORE.remove(message)" in portrait_task_queue
            ),
        },
        {
            "name": "runtime:production_externalization_gate",
            "ok": (
                "PORTRAIT_RUNTIME_PROFILE" in settings
                and "PRODUCTION_EXTERNAL_SERVICES_REQUIRED" in settings
                and "OTEL_EXPORTER_OTLP_ENDPOINT" in settings
                and "def production_externalization_failures" in production_gates
                and "def validate_production_externalization" in production_gates
                and "validate_production_externalization()" in server
                and "PORTRAIT_RUNTIME_PROFILE: ${PORTRAIT_RUNTIME_PROFILE:-development}" in compose
                and "PRODUCTION_EXTERNAL_SERVICES_REQUIRED: ${PRODUCTION_EXTERNAL_SERVICES_REQUIRED:-true}" in compose
                and "PORTRAIT_STORAGE_BACKEND: ${PORTRAIT_STORAGE_BACKEND:-json}" in compose
                and "PORTRAIT_VECTOR_BACKEND: ${PORTRAIT_VECTOR_BACKEND:-local}" in compose
                and "PORTRAIT_OBJECT_STORAGE_BACKEND: ${PORTRAIT_OBJECT_STORAGE_BACKEND:-local}" in compose
                and "TASK_QUEUE_BACKEND: ${TASK_QUEUE_BACKEND:-local}" in compose
                and "OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-}" in compose
                and "PORTRAIT_RUNTIME_PROFILE=development" in env_example
                and "PRODUCTION_EXTERNAL_SERVICES_REQUIRED=true" in env_example
                and "PORTRAIT_RUNTIME_PROFILE=production"
                in (root / "ops" / "production.env.example").read_text(encoding="utf-8")
                and "OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces"
                in (root / "ops" / "production.env.example").read_text(encoding="utf-8")
            ),
        },
        {
            "name": "frontend:websocket_console_subscription",
            "ok": (
                "new WebSocket" in console_next_ws
                and '"/v1/console/ws-ticket"' in console_next_ws
                and "issued.websocket_path" in console_next_ws
                and "ticket" in console_next_ws
            ),
        },
    ]
