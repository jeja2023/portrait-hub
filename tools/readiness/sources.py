"""就绪度检查的源码文本装载器。

一次性读入所有被静态契约检查引用的源码文本与派生切片，
供各 checks_* 分组模块按名取用。缺失文件一律降级为空串，
与拆分前单体实现的行为一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.readiness.common import text_between


def load_sources(root: Path) -> dict[str, Any]:
    settings = (
        (root / "app" / "settings.py").read_text(encoding="utf-8") if (root / "app" / "settings.py").is_file() else ""
    )
    security = (
        (root / "app" / "security.py").read_text(encoding="utf-8") if (root / "app" / "security.py").is_file() else ""
    )
    portrait_auth = (
        (root / "app" / "portrait_auth.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_auth.py").is_file()
        else ""
    )
    portrait_bootstrap = (
        (root / "app" / "portrait_bootstrap.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_bootstrap.py").is_file()
        else ""
    )
    portrait_access_routes = (
        (root / "app" / "routes_portrait_access.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_access.py").is_file()
        else ""
    )
    portrait_access = (
        (root / "app" / "portrait_access.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_access.py").is_file()
        else ""
    )
    portrait_call_logs = (
        (root / "app" / "portrait_call_logs.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_call_logs.py").is_file()
        else ""
    )
    portrait_errors = (
        (root / "app" / "portrait_errors.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_errors.py").is_file()
        else ""
    )
    portrait_review = (
        (root / "app" / "portrait_review.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_review.py").is_file()
        else ""
    )
    portrait_review_routes = (
        (root / "app" / "routes_portrait_review.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_review.py").is_file()
        else ""
    )
    debug_routes = (
        (root / "app" / "routes_debug.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_debug.py").is_file()
        else ""
    )
    health_routes = (
        (root / "app" / "routes_health.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_health.py").is_file()
        else ""
    )
    server = (root / "app" / "server.py").read_text(encoding="utf-8") if (root / "app" / "server.py").is_file() else ""
    observability = (
        (root / "app" / "observability.py").read_text(encoding="utf-8")
        if (root / "app" / "observability.py").is_file()
        else ""
    )
    core = (root / "app" / "core.py").read_text(encoding="utf-8") if (root / "app" / "core.py").is_file() else ""
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8") if (root / "pyproject.toml").is_file() else ""
    dev_requirements = (
        (root / "requirements" / "dev.txt").read_text(encoding="utf-8")
        if (root / "requirements" / "dev.txt").is_file()
        else ""
    )
    ci_workflow = (
        (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        if (root / ".github" / "workflows" / "ci.yml").is_file()
        else ""
    )
    type_check_tool = (
        (root / "tools" / "type_check.py").read_text(encoding="utf-8")
        if (root / "tools" / "type_check.py").is_file()
        else ""
    )
    route_modules = "\n".join(path.read_text(encoding="utf-8") for path in sorted((root / "app").glob("routes*.py")))
    model_lifecycle_routes = (
        (root / "app" / "routes_model_lifecycle.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_model_lifecycle.py").is_file()
        else ""
    )
    model_query_routes = (
        (root / "app" / "routes_model_query.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_model_query.py").is_file()
        else ""
    )
    model_config_writer = (
        (root / "app" / "model_config_writer.py").read_text(encoding="utf-8")
        if (root / "app" / "model_config_writer.py").is_file()
        else ""
    )
    rollout_audit = (
        (root / "app" / "rollout_audit.py").read_text(encoding="utf-8")
        if (root / "app" / "rollout_audit.py").is_file()
        else ""
    )
    model_config_resolver = (
        (root / "app" / "model_config_resolver.py").read_text(encoding="utf-8")
        if (root / "app" / "model_config_resolver.py").is_file()
        else ""
    )
    model_config_loader = (
        (root / "app" / "model_config_loader.py").read_text(encoding="utf-8")
        if (root / "app" / "model_config_loader.py").is_file()
        else ""
    )
    model_package = (
        (root / "app" / "model_package.py").read_text(encoding="utf-8")
        if (root / "app" / "model_package.py").is_file()
        else ""
    )
    model_refs = (
        (root / "app" / "model_refs.py").read_text(encoding="utf-8")
        if (root / "app" / "model_refs.py").is_file()
        else ""
    )
    rollout_routes = (
        (root / "app" / "routes_rollout.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_rollout.py").is_file()
        else ""
    )
    predict_routes = (
        (root / "app" / "routes_predict.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_predict.py").is_file()
        else ""
    )
    image_io = (
        (root / "app" / "image_io.py").read_text(encoding="utf-8") if (root / "app" / "image_io.py").is_file() else ""
    )
    schemas = (
        (root / "app" / "schemas.py").read_text(encoding="utf-8") if (root / "app" / "schemas.py").is_file() else ""
    )
    person_tracks_routes = (
        (root / "app" / "routes_person_tracks.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_person_tracks.py").is_file()
        else ""
    )
    vision_routes = (
        (root / "app" / "routes_vision.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_vision.py").is_file()
        else ""
    )
    runtime_execution = (
        (root / "app" / "runtime_execution.py").read_text(encoding="utf-8")
        if (root / "app" / "runtime_execution.py").is_file()
        else ""
    )
    routes_inference_common = (
        (root / "app" / "routes_inference_common.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_inference_common.py").is_file()
        else ""
    )
    runtime_face = (
        (root / "app" / "runtime_face.py").read_text(encoding="utf-8")
        if (root / "app" / "runtime_face.py").is_file()
        else ""
    )
    runtime_body = (
        (root / "app" / "runtime_body.py").read_text(encoding="utf-8")
        if (root / "app" / "runtime_body.py").is_file()
        else ""
    )
    runtime_pose = (
        (root / "app" / "runtime_pose.py").read_text(encoding="utf-8")
        if (root / "app" / "runtime_pose.py").is_file()
        else ""
    )
    runtime_gait = (
        (root / "app" / "runtime_gait.py").read_text(encoding="utf-8")
        if (root / "app" / "runtime_gait.py").is_file()
        else ""
    )
    runtime_appearance = (
        (root / "app" / "runtime_appearance.py").read_text(encoding="utf-8")
        if (root / "app" / "runtime_appearance.py").is_file()
        else ""
    )
    video_io = (
        (root / "app" / "video_io.py").read_text(encoding="utf-8") if (root / "app" / "video_io.py").is_file() else ""
    )
    media_image_decode = (
        (root / "app" / "media" / "image_decode.py").read_text(encoding="utf-8")
        if (root / "app" / "media" / "image_decode.py").is_file()
        else ""
    )
    media_schema = (
        (root / "app" / "media" / "media_schema.py").read_text(encoding="utf-8")
        if (root / "app" / "media" / "media_schema.py").is_file()
        else ""
    )
    media_video_decode = (
        (root / "app" / "media" / "video_decode.py").read_text(encoding="utf-8")
        if (root / "app" / "media" / "video_decode.py").is_file()
        else ""
    )
    inference_classification = (
        (root / "app" / "inference_classification.py").read_text(encoding="utf-8")
        if (root / "app" / "inference_classification.py").is_file()
        else ""
    )
    inference_detection = (
        (root / "app" / "inference_detection.py").read_text(encoding="utf-8")
        if (root / "app" / "inference_detection.py").is_file()
        else ""
    )
    portrait_object_storage = (
        (root / "app" / "portrait_object_storage.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_object_storage.py").is_file()
        else ""
    )
    portrait_security = (
        (root / "app" / "portrait_security.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_security.py").is_file()
        else ""
    )
    portrait_gallery = (
        (root / "app" / "portrait_gallery.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_gallery.py").is_file()
        else ""
    )
    gallery_state = (
        (root / "app" / "gallery_state.py").read_text(encoding="utf-8")
        if (root / "app" / "gallery_state.py").is_file()
        else ""
    )
    gallery_search = (
        (root / "app" / "gallery_search.py").read_text(encoding="utf-8")
        if (root / "app" / "gallery_search.py").is_file()
        else ""
    )
    portrait_gallery_records = (
        (root / "app" / "portrait_gallery_records.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_gallery_records.py").is_file()
        else ""
    )
    portrait_gallery_routes = (
        (root / "app" / "routes_portrait_gallery.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_gallery.py").is_file()
        else ""
    )
    portrait_gallery_mutations = (
        (root / "app" / "portrait_gallery_mutations.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_gallery_mutations.py").is_file()
        else ""
    )
    portrait_jobs = (
        (root / "app" / "portrait_jobs.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_jobs.py").is_file()
        else ""
    )
    portrait_request_validation = (
        (root / "app" / "portrait_request_validation.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_request_validation.py").is_file()
        else ""
    )
    portrait_model_routes = (
        (root / "app" / "routes_portrait_models.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_models.py").is_file()
        else ""
    )
    portrait_compare_routes = (
        (root / "app" / "routes_portrait_compare.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_compare.py").is_file()
        else ""
    )
    portrait_admin_routes = (
        (root / "app" / "routes_portrait_admin.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_admin.py").is_file()
        else ""
    )
    portrait_console_routes = (
        (root / "app" / "routes_portrait_console.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_console.py").is_file()
        else ""
    )
    portrait_ws_routes = (
        (root / "app" / "routes_portrait_ws.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_ws.py").is_file()
        else ""
    )
    portrait_response = (
        (root / "app" / "portrait_response.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_response.py").is_file()
        else ""
    )
    portrait_audit = (
        (root / "app" / "portrait_audit.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_audit.py").is_file()
        else ""
    )
    portrait_pagination = (
        (root / "app" / "portrait_pagination.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_pagination.py").is_file()
        else ""
    )
    portrait_stream_worker = (
        (root / "app" / "portrait_stream_worker.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_stream_worker.py").is_file()
        else ""
    )
    portrait_streams = (
        (root / "app" / "portrait_streams.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_streams.py").is_file()
        else ""
    )
    portrait_stream_routes = (
        (root / "app" / "routes_portrait_streams.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_streams.py").is_file()
        else ""
    )
    portrait_postgres = (
        (root / "app" / "portrait_postgres.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_postgres.py").is_file()
        else ""
    )
    postgres_core = (
        (root / "app" / "postgres_core.py").read_text(encoding="utf-8")
        if (root / "app" / "postgres_core.py").is_file()
        else ""
    )
    postgres_gallery = (
        (root / "app" / "postgres_gallery.py").read_text(encoding="utf-8")
        if (root / "app" / "postgres_gallery.py").is_file()
        else ""
    )
    postgres_streams = (
        (root / "app" / "postgres_streams.py").read_text(encoding="utf-8")
        if (root / "app" / "postgres_streams.py").is_file()
        else ""
    )
    postgres_audit = (
        (root / "app" / "postgres_audit.py").read_text(encoding="utf-8")
        if (root / "app" / "postgres_audit.py").is_file()
        else ""
    )
    postgres_jobs = (
        (root / "app" / "postgres_jobs.py").read_text(encoding="utf-8")
        if (root / "app" / "postgres_jobs.py").is_file()
        else ""
    )
    postgres_thresholds = (
        (root / "app" / "postgres_thresholds.py").read_text(encoding="utf-8")
        if (root / "app" / "postgres_thresholds.py").is_file()
        else ""
    )
    config_hot_reload = (
        (root / "app" / "config_hot_reload.py").read_text(encoding="utf-8")
        if (root / "app" / "config_hot_reload.py").is_file()
        else ""
    )
    portrait_postgres_schema = (
        (root / "tools" / "portrait_postgres_schema.sql").read_text(encoding="utf-8")
        if (root / "tools" / "portrait_postgres_schema.sql").is_file()
        else ""
    )
    stream_decode = (
        (root / "app" / "media" / "stream_decode.py").read_text(encoding="utf-8")
        if (root / "app" / "media" / "stream_decode.py").is_file()
        else ""
    )
    portrait_job_routes = (
        (root / "app" / "routes_portrait_jobs.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_portrait_jobs.py").is_file()
        else ""
    )
    portrait_task_queue = (
        (root / "app" / "portrait_task_queue.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_task_queue.py").is_file()
        else ""
    )
    portrait_video_job_worker = (
        (root / "app" / "portrait_video_job_worker.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_video_job_worker.py").is_file()
        else ""
    )
    portrait_runtime_store = (
        (root / "app" / "portrait_runtime_store.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_runtime_store.py").is_file()
        else ""
    )
    portrait_gallery_orchestration = (
        (root / "app" / "portrait_gallery_orchestration.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_gallery_orchestration.py").is_file()
        else ""
    )
    production_gates = (
        (root / "app" / "production_gates.py").read_text(encoding="utf-8")
        if (root / "app" / "production_gates.py").is_file()
        else ""
    )
    portrait_vector_store = (
        (root / "app" / "portrait_vector_store.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_vector_store.py").is_file()
        else ""
    )
    portrait_tracking = (
        (root / "app" / "portrait_tracking.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_tracking.py").is_file()
        else ""
    )
    tracking_state = (
        (root / "app" / "tracking_state.py").read_text(encoding="utf-8")
        if (root / "app" / "tracking_state.py").is_file()
        else ""
    )
    tracking_association = (
        (root / "app" / "tracking_association.py").read_text(encoding="utf-8")
        if (root / "app" / "tracking_association.py").is_file()
        else ""
    )
    portrait_thresholds = (
        (root / "app" / "portrait_thresholds.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_thresholds.py").is_file()
        else ""
    )
    portrait_state = (
        (root / "app" / "portrait_state.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_state.py").is_file()
        else ""
    )
    portrait_crypto = (
        (root / "app" / "portrait_crypto.py").read_text(encoding="utf-8")
        if (root / "app" / "portrait_crypto.py").is_file()
        else ""
    )
    runtime_registry = (
        (root / "app" / "runtime_registry.py").read_text(encoding="utf-8")
        if (root / "app" / "runtime_registry.py").is_file()
        else ""
    )
    rate_limit = (
        (root / "app" / "rate_limit.py").read_text(encoding="utf-8")
        if (root / "app" / "rate_limit.py").is_file()
        else ""
    )
    security_headers = (
        (root / "app" / "security_headers.py").read_text(encoding="utf-8")
        if (root / "app" / "security_headers.py").is_file()
        else ""
    )
    service_smoke = (
        (root / "tools" / "service_smoke_test.py").read_text(encoding="utf-8")
        if (root / "tools" / "service_smoke_test.py").is_file()
        else ""
    )
    regression_check = (
        (root / "tools" / "regression_check.py").read_text(encoding="utf-8")
        if (root / "tools" / "regression_check.py").is_file()
        else ""
    )
    worker_control = (
        (root / "tools" / "worker_control.py").read_text(encoding="utf-8")
        if (root / "tools" / "worker_control.py").is_file()
        else ""
    )
    validate_model_package = (
        (root / "tools" / "validate_model_package.py").read_text(encoding="utf-8")
        if (root / "tools" / "validate_model_package.py").is_file()
        else ""
    )
    report_redaction = (
        (root / "tools" / "report_redaction.py").read_text(encoding="utf-8")
        if (root / "tools" / "report_redaction.py").is_file()
        else ""
    )
    deploy_check = "\n".join(
        (root / item).read_text(encoding="utf-8")
        for item in [
            "tools/deploy_check.py",
            "tools/deploy_checks/common.py",
            "tools/deploy_checks/containers.py",
        ]
        if (root / item).is_file()
    )
    python_sdk = (
        (root / "sdk" / "python" / "portrait_hub_client.py").read_text(encoding="utf-8")
        if (root / "sdk" / "python" / "portrait_hub_client.py").is_file()
        else ""
    )
    node_sdk = (
        (root / "sdk" / "node" / "portraitHubClient.js").read_text(encoding="utf-8")
        if (root / "sdk" / "node" / "portraitHubClient.js").is_file()
        else ""
    )
    compose = (
        (root / "docker-compose.yml").read_text(encoding="utf-8") if (root / "docker-compose.yml").is_file() else ""
    )
    cpu_compose = (
        (root / "docker-compose.cpu.yml").read_text(encoding="utf-8")
        if (root / "docker-compose.cpu.yml").is_file()
        else ""
    )
    cpu_dockerfile = (
        (root / "Dockerfile.cpu").read_text(encoding="utf-8") if (root / "Dockerfile.cpu").is_file() else ""
    )
    env_example = (root / ".env.example").read_text(encoding="utf-8") if (root / ".env.example").is_file() else ""
    readme = (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").is_file() else ""
    deploy_ubuntu_path = root / "docs" / "deployment" / "DEPLOY_UBUNTU.md"
    model_training_plan_path = root / "docs" / "plans" / "MODEL_RND_TRAINING_PLAN.md"
    inference_upgrade_plan_path = root / "docs" / "plans" / "INFERENCE_SERVICE_UPGRADE_PLAN.md"
    deploy_ubuntu = deploy_ubuntu_path.read_text(encoding="utf-8") if deploy_ubuntu_path.is_file() else ""
    model_training_plan = (
        model_training_plan_path.read_text(encoding="utf-8") if model_training_plan_path.is_file() else ""
    )
    inference_upgrade_plan = (
        inference_upgrade_plan_path.read_text(encoding="utf-8") if inference_upgrade_plan_path.is_file() else ""
    )
    project_docs = "\n".join([readme, deploy_ubuntu, model_training_plan, inference_upgrade_plan])
    legacy_cross_camera_namespace = "cross_camera" + "_tracking"
    legacy_parent_models_path = "../" + "models"
    requirements = (
        (root / "requirements.txt").read_text(encoding="utf-8") if (root / "requirements.txt").is_file() else ""
    )
    base_lock = (
        (root / "requirements" / "base.lock").read_text(encoding="utf-8")
        if (root / "requirements" / "base.lock").is_file()
        else ""
    )
    requirements_lock = (
        (root / "requirements.lock").read_text(encoding="utf-8") if (root / "requirements.lock").is_file() else ""
    )
    base_in = (
        (root / "requirements" / "base.in").read_text(encoding="utf-8")
        if (root / "requirements" / "base.in").is_file()
        else ""
    )
    console_html = (
        (root / "frontend" / "console" / "console.html").read_text(encoding="utf-8")
        if (root / "frontend" / "console" / "console.html").is_file()
        else ""
    )
    console_js = (
        (root / "frontend" / "console" / "console.js").read_text(encoding="utf-8")
        if (root / "frontend" / "console" / "console.js").is_file()
        else ""
    )
    console_config_js = (
        (root / "frontend" / "console" / "console.config.js").read_text(encoding="utf-8")
        if (root / "frontend" / "console" / "console.config.js").is_file()
        else ""
    )
    console_runtime_js = "\n".join(
        (root / "frontend" / "console" / item).read_text(encoding="utf-8")
        for item in ["views/app.js", "runtime/formatting.js", "runtime/network.js"]
        if (root / "frontend" / "console" / item).is_file()
    )
    console_module_sources = "\n".join(
        (root / "frontend" / "console" / item).read_text(encoding="utf-8")
        for item in [
            "api/client.js",
            "state/store.js",
            "views/navigation.js",
            "templates/core.js",
            "templates/access.js",
            "templates/governance.js",
            "templates/index.js",
            "runtime/formatting.js",
            "runtime/network.js",
            "views/analysis.js",
            "views/gallery.js",
            "views/operations.js",
            "views/access.js",
            "views/observability.js",
            "views/governance.js",
            "views/results.js",
            "views/dashboard.js",
            "views/app.js",
            "renderers/data-viewer.js",
            "visuals/previews.js",
            "visuals/results.js",
        ]
        if (root / "frontend" / "console" / item).is_file()
    )
    console_next_package = (
        (root / "frontend" / "console-next" / "package.json").read_text(encoding="utf-8")
        if (root / "frontend" / "console-next" / "package.json").is_file()
        else ""
    )
    console_next_vite = (
        (root / "frontend" / "console-next" / "vite.config.ts").read_text(encoding="utf-8")
        if (root / "frontend" / "console-next" / "vite.config.ts").is_file()
        else ""
    )
    console_next_session = (
        (root / "frontend" / "console-next" / "src" / "auth" / "session.ts").read_text(encoding="utf-8")
        if (root / "frontend" / "console-next" / "src" / "auth" / "session.ts").is_file()
        else ""
    )
    console_next_manifest = (
        (root / "frontend" / "console-next" / "dist" / ".vite" / "manifest.json").read_text(encoding="utf-8")
        if (root / "frontend" / "console-next" / "dist" / ".vite" / "manifest.json").is_file()
        else ""
    )
    regression_open_case_files = ""
    if "def open_case_files" in regression_check:
        regression_open_case_files = regression_check.split("def open_case_files", 1)[1].split("def run_case", 1)[0]
    ready_deep_section = text_between(health_routes, '@router.get("/ready/deep"', '@router.get("/metrics"')
    portrait_gallery_impl = "\n".join([portrait_gallery, gallery_state, gallery_search])
    portrait_gallery_mutation_text = "\n".join([portrait_gallery_routes, portrait_gallery_mutations])
    portrait_gallery_route_orchestration = "\n".join([portrait_gallery_routes, portrait_gallery_orchestration])
    portrait_admin_runtime_text = "\n".join([portrait_admin_routes, portrait_runtime_store])
    portrait_postgres_impl = "\n".join(
        [
            portrait_postgres,
            postgres_core,
            postgres_gallery,
            postgres_streams,
            postgres_audit,
            postgres_jobs,
            postgres_thresholds,
        ]
    )
    postgres_health_section = text_between(postgres_core, "def postgres_health", "def jsonb")
    redis_health_section = text_between(portrait_task_queue, "class RedisTaskQueue", "def configured_task_queue")
    local_object_store_section = text_between(portrait_object_storage, "class LocalObjectStore", "class S3ObjectStore")
    s3_object_store_section = text_between(
        portrait_object_storage, "class S3ObjectStore", "def configured_object_store"
    )
    local_object_delete_section = text_between(local_object_store_section, "def delete_object", "def health")
    s3_object_delete_section = text_between(s3_object_store_section, "def delete_object", "def health")
    object_delete_sections = "\n".join([local_object_delete_section, s3_object_delete_section])
    local_object_health_section = text_between(local_object_store_section, "def health", "")
    s3_health_section = text_between(s3_object_store_section, "def health", "")
    rollback_route_text = "\n".join(
        [
            portrait_gallery_routes,
            portrait_gallery_mutations,
            portrait_job_routes,
            portrait_stream_routes,
            portrait_admin_routes,
            portrait_model_routes,
        ]
    )
    return {key: value for key, value in locals().items() if key != "root"}
