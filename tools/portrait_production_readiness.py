"""PortraitHub 生产环境就绪度报告脚本。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.readiness_checks import (
    check_capabilities,
    check_model_files,
    configured_model_path,
    load_yaml,
)


def text_between(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    tail = text.split(start, 1)[1]
    if not end:
        return tail
    if end not in tail:
        return tail
    return tail.split(end, 1)[0]


def check_templates(root: Path) -> list[dict[str, Any]]:
    required = [
        "app/portrait_errors.py",
        "app/portrait_runtime_store.py",
        "app/portrait_review.py",
        "app/portrait_gallery_orchestration.py",
        "app/portrait_access.py",
        "app/portrait_call_logs.py",
        "app/rollout_audit.py",
        "app/routes_portrait_access.py",
        "app/routes_portrait_review.py",
        "app/production_gates.py",
        "app/portrait_video_job_worker.py",
        "app/tracking_state.py",
        "app/tracking_association.py",
        "app/runtime_face.py",
        "app/runtime_body.py",
        "app/runtime_pose.py",
        "app/runtime_gait.py",
        "app/runtime_appearance.py",
        "app/runtime_common.py",
        "frontend/console/console.html",
        "frontend/console/console.css",
        "frontend/console/console.config.js",
        "frontend/console/console.js",
        "frontend/console/api/client.js",
        "frontend/console/state/store.js",
        "frontend/console/views/analysis.js",
        "frontend/console/views/gallery.js",
        "frontend/console/views/operations.js",
        "frontend/console/views/app.js",
        "frontend/console/renderers/data-viewer.js",
        "frontend/console/visuals/previews.js",
        "tools/portrait_algorithm_eval.py",
        "tools/portrait_model_regression.py",
        "tools/portrait_cutover_check.py",
        "tools/portrait_stream_worker_health.py",
        "tools/portrait_migrate.py",
        "tools/portrait_backup_scheduler.py",
        "tools/portrait_governance_scheduler.py",
        "tools/console_screenshot_acceptance.py",
        "tools/load_test.py",
        "tools/type_check.py",
        "tools/workspace_hygiene.py",
        "tools/portrait_postgres_schema.sql",
        "tools/qdrant_collections.json",
        "examples/portrait-model-regression.example.yml",
        "examples/portrait-model-ab-shadow.example.yml",
        "examples/production-models.example.yml",
        "examples/production-model-capabilities.example.yml",
        "examples/demo-clients/README.md",
        "examples/demo-clients/python_demo_client.py",
        "examples/demo-clients/node_demo_client.js",
        "deploy/portrait-stream-worker.service",
        "deploy/k8s-stream-worker.yaml",
        "deploy/portrait-video-job-worker.service",
        "deploy/k8s-video-job-worker.yaml",
        "deploy/portrait-governance-scheduler.service",
        "deploy/portrait-governance-scheduler.timer",
        "deploy/k8s-governance-cronjob.yaml",
        ".github/workflows/ci.yml",
        ".github/workflows/integration-matrix.yml",
        ".github/workflows/console-acceptance.yml",
        ".github/workflows/security-audit.yml",
        "requirements/prod-optional.txt",
        "package.json",
        "sdk/python/portrait_hub_client.py",
        "sdk/node/portraitHubClient.js",
    ]
    return [
        {"name": f"template:{item}", "ok": (root / item).is_file()} for item in required
    ]


def check_data_stack(root: Path) -> list[dict[str, Any]]:
    optional_path = root / "requirements" / "prod-optional.txt"
    optional = (
        optional_path.read_text(encoding="utf-8") if optional_path.is_file() else ""
    )
    schema = (
        (root / "tools" / "portrait_postgres_schema.sql").read_text(encoding="utf-8")
        if (root / "tools" / "portrait_postgres_schema.sql").is_file()
        else ""
    )
    dockerfile = (
        (root / "Dockerfile").read_text(encoding="utf-8")
        if (root / "Dockerfile").is_file()
        else ""
    )
    compose = (
        (root / "docker-compose.yml").read_text(encoding="utf-8")
        if (root / "docker-compose.yml").is_file()
        else ""
    )
    checks = [
        {
            "name": "data_stack:postgres_driver",
            "ok": "psycopg" in optional,
        },
        {
            "name": "data_stack:pgvector_driver",
            "ok": "pgvector" in optional
            and "CREATE EXTENSION IF NOT EXISTS vector" in schema,
        },
        {
            "name": "data_stack:qdrant_driver",
            "ok": "qdrant-client" in optional,
        },
        {
            "name": "data_stack:s3_driver",
            "ok": "boto3" in optional and "S3_REGION" in compose,
        },
        {
            "name": "data_stack:redis_driver",
            "ok": "redis" in optional and "REDIS_URL" in compose,
        },
        {
            "name": "data_stack:docker_optional_install",
            "ok": "INSTALL_PROD_OPTIONAL" in dockerfile,
        },
    ]
    return checks


def check_security_controls(root: Path) -> list[dict[str, Any]]:
    settings = (
        (root / "app" / "settings.py").read_text(encoding="utf-8")
        if (root / "app" / "settings.py").is_file()
        else ""
    )
    security = (
        (root / "app" / "security.py").read_text(encoding="utf-8")
        if (root / "app" / "security.py").is_file()
        else ""
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
    server = (
        (root / "app" / "server.py").read_text(encoding="utf-8")
        if (root / "app" / "server.py").is_file()
        else ""
    )
    observability = (
        (root / "app" / "observability.py").read_text(encoding="utf-8")
        if (root / "app" / "observability.py").is_file()
        else ""
    )
    core = (
        (root / "app" / "core.py").read_text(encoding="utf-8")
        if (root / "app" / "core.py").is_file()
        else ""
    )
    pyproject = (
        (root / "pyproject.toml").read_text(encoding="utf-8")
        if (root / "pyproject.toml").is_file()
        else ""
    )
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
    route_modules = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / "app").glob("routes*.py"))
    )
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
        (root / "app" / "image_io.py").read_text(encoding="utf-8")
        if (root / "app" / "image_io.py").is_file()
        else ""
    )
    schemas = (
        (root / "app" / "schemas.py").read_text(encoding="utf-8")
        if (root / "app" / "schemas.py").is_file()
        else ""
    )
    person_detection_routes = (
        (root / "app" / "routes_person_detection.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_person_detection.py").is_file()
        else ""
    )
    person_embeddings_routes = (
        (root / "app" / "routes_person_embeddings.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_person_embeddings.py").is_file()
        else ""
    )
    person_tracks_routes = (
        (root / "app" / "routes_person_tracks.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_person_tracks.py").is_file()
        else ""
    )
    person_video_routes = (
        (root / "app" / "routes_person_video.py").read_text(encoding="utf-8")
        if (root / "app" / "routes_person_video.py").is_file()
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
        (root / "app" / "video_io.py").read_text(encoding="utf-8")
        if (root / "app" / "video_io.py").is_file()
        else ""
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
    deploy_check = (
        (root / "tools" / "deploy_check.py").read_text(encoding="utf-8")
        if (root / "tools" / "deploy_check.py").is_file()
        else ""
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
        (root / "docker-compose.yml").read_text(encoding="utf-8")
        if (root / "docker-compose.yml").is_file()
        else ""
    )
    cpu_compose = (
        (root / "docker-compose.cpu.yml").read_text(encoding="utf-8")
        if (root / "docker-compose.cpu.yml").is_file()
        else ""
    )
    cpu_dockerfile = (
        (root / "Dockerfile.cpu").read_text(encoding="utf-8")
        if (root / "Dockerfile.cpu").is_file()
        else ""
    )
    env_example = (
        (root / ".env.example").read_text(encoding="utf-8")
        if (root / ".env.example").is_file()
        else ""
    )
    readme = (
        (root / "README.md").read_text(encoding="utf-8")
        if (root / "README.md").is_file()
        else ""
    )
    deploy_ubuntu_path = root / "docs" / "deployment" / "DEPLOY_UBUNTU.md"
    model_training_plan_path = root / "docs" / "plans" / "MODEL_RND_TRAINING_PLAN.md"
    inference_upgrade_plan_path = (
        root / "docs" / "plans" / "INFERENCE_SERVICE_UPGRADE_PLAN.md"
    )
    deploy_ubuntu = (
        deploy_ubuntu_path.read_text(encoding="utf-8")
        if deploy_ubuntu_path.is_file()
        else ""
    )
    model_training_plan = (
        model_training_plan_path.read_text(encoding="utf-8")
        if model_training_plan_path.is_file()
        else ""
    )
    inference_upgrade_plan = (
        inference_upgrade_plan_path.read_text(encoding="utf-8")
        if inference_upgrade_plan_path.is_file()
        else ""
    )
    project_docs = "\n".join(
        [readme, deploy_ubuntu, model_training_plan, inference_upgrade_plan]
    )
    legacy_cross_camera_namespace = "cross_camera" + "_tracking"
    legacy_parent_models_path = "../" + "models"
    requirements = (
        (root / "requirements.txt").read_text(encoding="utf-8")
        if (root / "requirements.txt").is_file()
        else ""
    )
    base_lock = (
        (root / "requirements" / "base.lock").read_text(encoding="utf-8")
        if (root / "requirements" / "base.lock").is_file()
        else ""
    )
    requirements_lock = (
        (root / "requirements.lock").read_text(encoding="utf-8")
        if (root / "requirements.lock").is_file()
        else ""
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
        (root / "frontend" / "console" / "console.config.js").read_text(
            encoding="utf-8"
        )
        if (root / "frontend" / "console" / "console.config.js").is_file()
        else ""
    )
    console_runtime_js = (
        (root / "frontend" / "console" / "views" / "app.js").read_text(encoding="utf-8")
        if (root / "frontend" / "console" / "views" / "app.js").is_file()
        else ""
    )
    console_module_sources = "\n".join(
        (root / "frontend" / "console" / item).read_text(encoding="utf-8")
        for item in [
            "api/client.js",
            "state/store.js",
            "views/analysis.js",
            "views/gallery.js",
            "views/operations.js",
            "views/app.js",
            "renderers/data-viewer.js",
            "visuals/previews.js",
        ]
        if (root / "frontend" / "console" / item).is_file()
    )
    regression_open_case_files = ""
    if "def open_case_files" in regression_check:
        regression_open_case_files = regression_check.split("def open_case_files", 1)[
            1
        ].split("def run_case", 1)[0]
    ready_deep_section = text_between(
        health_routes, '@router.get("/ready/deep"', '@router.get("/metrics"'
    )
    portrait_gallery_impl = "\n".join([portrait_gallery, gallery_state, gallery_search])
    portrait_gallery_mutation_text = "\n".join(
        [portrait_gallery_routes, portrait_gallery_mutations]
    )
    portrait_gallery_route_orchestration = "\n".join(
        [portrait_gallery_routes, portrait_gallery_orchestration]
    )
    portrait_admin_runtime_text = "\n".join(
        [portrait_admin_routes, portrait_runtime_store]
    )
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
    postgres_health_section = text_between(
        postgres_core, "def postgres_health", "def jsonb"
    )
    redis_health_section = text_between(
        portrait_task_queue, "class RedisTaskQueue", "def configured_task_queue"
    )
    local_object_store_section = text_between(
        portrait_object_storage, "class LocalObjectStore", "class S3ObjectStore"
    )
    s3_object_store_section = text_between(
        portrait_object_storage, "class S3ObjectStore", "def configured_object_store"
    )
    local_object_delete_section = text_between(
        local_object_store_section, "def delete_object", "def health"
    )
    s3_object_delete_section = text_between(
        s3_object_store_section, "def delete_object", "def health"
    )
    object_delete_sections = "\n".join(
        [local_object_delete_section, s3_object_delete_section]
    )
    local_object_health_section = text_between(
        local_object_store_section, "def health", ""
    )
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
    return [
        {
            "name": "security:auth_required_setting",
            "ok": "AUTH_REQUIRED" in settings and "AUTH_REQUIRED" in security,
        },
        {
            "name": "quality:core_explicit_imports",
            "ok": (
                "__all__" in core
                and "import *" not in core
                and "from app.core import *" not in route_modules
            ),
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
                and "mypy is not installed; install requirements/dev.txt"
                in type_check_tool
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
            "name": "frontend:console_config_externalized",
            "ok": (
                "window.PortraitConsoleConfig" in console_config_js
                and "endpointMap" in console_config_js
                and "alertDefaults" in console_config_js
                and "window.PortraitConsoleConfig" in console_runtime_js
                and "const endpointMap = consoleConfig.endpointMap"
                in console_runtime_js
            ),
        },
        {
            "name": "frontend:console_modules_split",
            "ok": (
                "/assets/console/api/client.js" in console_html
                and "/assets/console/state/store.js" in console_html
                and "/assets/console/views/gallery.js" in console_html
                and "/assets/console/views/app.js" in console_html
                and console_html.index("/assets/console/views/app.js")
                < console_html.index("/assets/console.js")
                and "PortraitConsoleRuntime" in console_js
                and "runtime.init" in console_js
                and len(console_js) < 2000
                and "function init()" in console_runtime_js
                and "window.PortraitConsoleRuntime = { init }" in console_runtime_js
                and "PortraitConsoleModules" in console_module_sources
                and "modules.api" in console_module_sources
                and "modules.state" in console_module_sources
                and "modules.views" in console_module_sources
                and "modules.renderers" in console_module_sources
                and "modules.visuals" in console_module_sources
                and "def console_asset_path" in portrait_console_routes
                and '"/assets/console/{asset_path:path}"' in portrait_console_routes
            ),
        },
        {
            "name": "backend:gallery_orchestration_split",
            "ok": (
                "def enroll_gallery_person" in portrait_gallery_orchestration
                and "def search_gallery_image" in portrait_gallery_orchestration
                and "def create_async_gallery_search_batch"
                in portrait_gallery_orchestration
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
                and "PORTRAIT_RUNTIME_PROFILE: ${PORTRAIT_RUNTIME_PROFILE:-development}"
                in compose
                and "PRODUCTION_EXTERNAL_SERVICES_REQUIRED: ${PRODUCTION_EXTERNAL_SERVICES_REQUIRED:-true}"
                in compose
                and "PORTRAIT_STORAGE_BACKEND: ${PORTRAIT_STORAGE_BACKEND:-json}"
                in compose
                and "PORTRAIT_VECTOR_BACKEND: ${PORTRAIT_VECTOR_BACKEND:-local}"
                in compose
                and "PORTRAIT_OBJECT_STORAGE_BACKEND: ${PORTRAIT_OBJECT_STORAGE_BACKEND:-local}"
                in compose
                and "TASK_QUEUE_BACKEND: ${TASK_QUEUE_BACKEND:-local}" in compose
                and "OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-}"
                in compose
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
                "new WebSocket" in console_runtime_js
                and "/ws/jobs/" in console_runtime_js
                and "/ws/streams/" in console_runtime_js
                and "access_token" in console_runtime_js
                and "token" in console_runtime_js
            ),
        },
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
        {
            "name": "sdk:http_error_contract",
            "ok": (
                "class PortraitHubHTTPError" in python_sdk
                and "except HTTPError as exc" in python_sdk
                and "raise PortraitHubHTTPError(exc.code" in python_sdk
                and "class PortraitHubHTTPError extends Error" in node_sdk
                and "async decodeBody(response)" in node_sdk
                and "JSON.parse(text)" in node_sdk
                and "return text;" in node_sdk
                and "if (!response.ok)" in node_sdk
                and "module.exports = { PortraitHubClient, PortraitHubHTTPError }"
                in node_sdk
            ),
        },
        {
            "name": "sdk:path_segment_encoding",
            "ok": (
                "from urllib.parse import quote" in python_sdk
                and "def _path_segment" in python_sdk
                and 'quote(str(value), safe="")' in python_sdk
                and 'f"/v1/thresholds/{self._path_segment(profile)}"' in python_sdk
                and "pathSegment(value)" in node_sdk
                and "encodeURIComponent(String(value))" in node_sdk
                and "updateThresholds(profile, thresholds)" in node_sdk
            ),
        },
        {
            "name": "sdk:multipart_header_escaping",
            "ok": (
                "def _multipart_header_value" in python_sdk
                and "self._multipart_header_value(key)" in python_sdk
                and "safe_field_name = self._multipart_header_value(field_name)"
                in python_sdk
                and "safe_filename = self._multipart_header_value(path_obj.name)"
                in python_sdk
                and 'name="{safe_field_name}"; ' in python_sdk
                and 'filename="{safe_filename}"' in python_sdk
                and 'name="{field_name}"; ' not in python_sdk
                and 'filename="{path_obj.name}"' not in python_sdk
            ),
        },
        {
            "name": "sdk:node_contract_deploy_check",
            "ok": (
                "def check_node_sdk_tests" in deploy_check
                and 'tests" / "test_node_sdk.js' in deploy_check
                and 'shutil.which("node")' in deploy_check
                and "subprocess.run(" in deploy_check
                and "node_sdk_contract_tests" in deploy_check
            ),
        },
        {
            "name": "sdk:batch_async_and_video_examples",
            "ok": (
                "def search_batch" in python_sdk
                and "def compare_batch" in python_sdk
                and '"/v1/gallery/search/batch"' in python_sdk
                and '"/v1/compare/batch"' in python_sdk
                and "searchBatch(images" in node_sdk
                and "compareBatch(" in node_sdk
                and '"/v1/gallery/search/batch"' in node_sdk
                and '"/v1/compare/batch"' in node_sdk
                and "asyncMode" in node_sdk
                and "sdk-batch-code" in console_module_sources
                and "sdk-video-code" in console_module_sources
                and "sdk-batch-copy-button" in console_module_sources
                and "sdk-video-copy-button" in console_module_sources
                and "client.search_batch" in console_module_sources
                and "async_mode=True" in console_module_sources
                and "createVideoJob" in console_module_sources
                and "client.jobResult" in console_module_sources
            ),
        },
        {
            "name": "frontend:api_playground_stage_two_coverage",
            "ok": (
                'value="/v1/gallery/search/batch" data-method="POST"'
                in console_module_sources
                and 'value="/v1/compare/batch" data-method="POST"'
                in console_module_sources
                and 'value="/v1/streams" data-method="POST"' in console_module_sources
                and 'value="/v1/streams" data-method="GET"' in console_module_sources
                and 'value="/v1/streams/{stream_id}/events" data-method="GET"'
                in console_module_sources
                and "playground-stream-id-input" in console_module_sources
                and "playground-stream-url-input" in console_module_sources
                and "playground-async-mode-input" in console_module_sources
                and "function apiRaw" in console_module_sources
                and "function playgroundSelection" in console_module_sources
                and 'appendFiles(form, "files"' in console_module_sources
                and "endpoint_template" in console_module_sources
                and "http_status" in console_module_sources
                and "error_code" in console_module_sources
                and "controlled_use" in console_module_sources
            ),
        },
        {
            "name": "frontend:slo_panel_operational_contract",
            "ok": (
                "function summarizeSloCallLogs" in console_module_sources
                and "call_logs_30d" in console_module_sources
                and "/v1/access/call-logs?limit=500&created_since="
                in console_module_sources
                and "queue_p95_seconds" in console_module_sources
                and "queue_p99_seconds" in console_module_sources
                and "gpu_queue_depth" in console_module_sources
                and "gpu_device_queue_depths" in console_module_sources
                and "error_budget_burn_rate" in console_module_sources
                and "success_rate_source" in console_module_sources
                and "call_log_window_seconds" in console_module_sources
                and "gpu_worker_queue_seconds" in console_module_sources
                and "gpu_worker_gpu_device_queue_depth" in console_module_sources
            ),
        },
        {
            "name": "security:trusted_host_allowlist",
            "ok": (
                "TRUSTED_HOSTS" in settings
                and "HotReloadTrustedHostMiddleware" in server
                and "allowed_hosts_getter=lambda: TRUSTED_HOSTS" in server
                and "www_redirect=False" in server
                and "TRUSTED_HOSTS: ${TRUSTED_HOSTS:-127.0.0.1,localhost,gpu-worker-0,gpu-worker-1}"
                in compose
                and "TRUSTED_HOSTS=127.0.0.1,localhost,gpu-worker-0,gpu-worker-1"
                in env_example
            ),
        },
        {
            "name": "security:compose_auth_required_default",
            "ok": "AUTH_REQUIRED: ${AUTH_REQUIRED:-true}" in compose,
        },
        {
            "name": "security:compose_debug_disabled_default",
            "ok": "DEBUG_ENDPOINTS_ENABLED: ${DEBUG_ENDPOINTS_ENABLED:-false}"
            in compose,
        },
        {
            "name": "security:env_example_fail_closed_defaults",
            "ok": (
                "AUTH_REQUIRED=true" in env_example
                and "DEBUG_ENDPOINTS_ENABLED=false" in env_example
                and "ENABLE_API_DOCS=false" in env_example
                and "TENANT_HEADER_REQUIRED=true" in env_example
                and "ENCRYPTION_KEY_ID=primary" in env_example
                and "REQUIRE_ENCRYPTION=true" in env_example
                and "AUDIT_WRITE_FAIL_CLOSED=true" in env_example
                and "MODEL_CONFIG_READ_FAIL_CLOSED=true" in env_example
                and "STATE_READ_FAIL_CLOSED=true" in env_example
                and "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES=false"
                in env_example
                and "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES: ${PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES:-false}"
                in compose
            ),
        },
        {
            "name": "security:jwt_claim_contract",
            "ok": (
                "JWT_REQUIRE_EXP" in settings
                and "JWT_REQUIRE_ISS" in settings
                and "JWT_REQUIRE_AUD" in settings
                and "JWT_REQUIRE_TENANT" in settings
                and "JWT_AUDIENCE" in settings
                and "JWT_SECRET_ID" in settings
                and "JWT_SECRET_KEYRING" in settings
                and "def parse_jwt_secret_keyring" in portrait_auth
                and "def candidate_jwt_secrets" in portrait_auth
                and 'header.get("kid")' in portrait_auth
                and "missing JWT expiration" in portrait_auth
                and "missing JWT issuer" in portrait_auth
                and "missing JWT audience" in portrait_auth
                and "invalid JWT audience" in portrait_auth
                and "JWT_AUDIENCE: ${JWT_AUDIENCE:-portrait-hub-api}" in compose
                and "JWT_SECRET_ID: ${JWT_SECRET_ID:-primary}" in compose
                and "JWT_SECRET_KEYRING: ${JWT_SECRET_KEYRING:-}" in compose
                and "JWT_REQUIRE_EXP: ${JWT_REQUIRE_EXP:-true}" in compose
                and "JWT_REQUIRE_ISS: ${JWT_REQUIRE_ISS:-true}" in compose
                and "JWT_REQUIRE_AUD: ${JWT_REQUIRE_AUD:-true}" in compose
                and "JWT_REQUIRE_TENANT: ${JWT_REQUIRE_TENANT:-true}" in compose
                and "JWT_AUDIENCE=portrait-hub-api" in env_example
                and "JWT_SECRET_ID=primary" in env_example
                and "JWT_SECRET_KEYRING=" in env_example
                and "JWT_REQUIRE_EXP=true" in env_example
                and "JWT_REQUIRE_ISS=true" in env_example
                and "JWT_REQUIRE_AUD=true" in env_example
                and "JWT_REQUIRE_TENANT=true" in env_example
                and '"jwt_secret_id_configured": bool(JWT_SECRET_ID)'
                in portrait_admin_routes
                and '"jwt_secret_keyring_configured": bool(JWT_SECRET_KEYRING)'
                in portrait_admin_routes
            ),
        },
        {
            "name": "security:least_privilege_rbac_roles",
            "ok": (
                '"admin": {"*"}' in portrait_auth
                and '"operator": {"infer", "compare", "gallery:read", "gallery:write", "jobs", "streams", "models:read", "admin:status", "metrics:read", "access:read"}'
                in portrait_auth
                and '"algorithm": {"infer", "compare", "models:read", "models:write", "thresholds:write"}'
                in portrait_auth
                and '"auditor": {"gallery:read", "jobs:read", "streams:read", "models:read", "admin:status", "admin:export", "metrics:read", "access:read"}'
                in portrait_auth
                and '"viewer": {"gallery:read", "jobs:read", "streams:read", "models:read"}'
                in portrait_auth
                and '"viewer": {"infer"' not in portrait_auth
                and 'permission_dependency("models:write")' in debug_routes
                and 'permission_dependency("models:read")' not in debug_routes
                and '@router.get("/metrics", dependencies=[Depends(require_api_token), Depends(permission_dependency("metrics:read"))])'
                in health_routes
                and 'permission_dependency("admin:status")' in portrait_admin_routes
                and 'permission_dependency("admin:export")' in portrait_admin_routes
                and 'permission_dependency("admin:retention")' in portrait_admin_routes
                and 'permission_dependency("models:read")' not in portrait_admin_routes
                and 'permission_dependency("models:write")' not in portrait_admin_routes
                and 'permission_dependency("admin:status")' in portrait_console_routes
                and 'permission_dependency("access:read")' in portrait_access_routes
                and 'permission_dependency("access:write")' in portrait_access_routes
                and "application_scopes_allow_permission" in portrait_auth
                and "x_api_key" in portrait_auth
            ),
        },
        {
            "name": "security:access_center_state_safety",
            "ok": (
                "_ACCESS_LOCK = threading.RLock()" in portrait_access
                and "with _ACCESS_LOCK:" in portrait_access
                and "def record_application_call" in portrait_access
                and "call_count" in portrait_access
                and "error_count" in portrait_access
                and "last_error_at" in portrait_access
                and "error_rate" in portrait_access
                and "record_application_call(tenant_id, application_id, status_code, created_at)"
                in portrait_call_logs
                and "error_code: str | None = None" in portrait_call_logs
                and "created_since: float | None = None" in portrait_call_logs
                and "created_until: float | None = None" in portrait_call_logs
                and "error_code: str | None = Query" in portrait_access_routes
                and "created_since: float | None = Query" in portrait_access_routes
                and "created_until: float | None = Query" in portrait_access_routes
                and "call-log-error-code-input" in console_module_sources
                and "call-log-created-since-input" in console_module_sources
                and "call-log-created-until-input" in console_module_sources
                and "created_since" in console_module_sources
                and "created_until" in console_module_sources
                and "error_code=logged_error_code" in server
                and "portrait_error_code" in server
                and "portrait_application_id" in rate_limit
                and "portrait_application_id" in server
                and "flush_access_call_stats" in portrait_access
                and "_ACCESS_STATS_DIRTY" in portrait_access
            ),
        },
        {
            "name": "security:access_error_code_catalog",
            "ok": (
                "ERROR_CODE_CATALOG" in portrait_errors
                and "def error_code_catalog" in portrait_errors
                and "validation_error" in portrait_errors
                and "rate_limited" in portrait_errors
                and "storage_error" in portrait_errors
                and "batch_job_error" in portrait_errors
                and "migration_error" in portrait_errors
                and "from app.portrait_errors import error_code_catalog"
                in portrait_access_routes
                and '@router.get("/v1/access/error-codes", dependencies=[Depends(permission_dependency("access:read"))])'
                in portrait_access_routes
                and '"error_codes": error_codes' in portrait_access_routes
                and "/v1/access/error-codes" in console_module_sources
                and 'view: "error-codes"' in console_module_sources
                and "error-codes-table" in console_module_sources
                and "error-codes-json" in console_module_sources
                and "renderErrorCodes" in console_module_sources
            ),
        },
        {
            "name": "security:track_review_annotation_pool",
            "ok": (
                "_REVIEW_LOCK = threading.RLock()" in portrait_review
                and "PORTRAIT_REVIEW_STATE_PATH" in settings
                and "def create_review_annotation" in portrait_review
                and "def list_review_annotations" in portrait_review
                and "def review_annotation_summary" in portrait_review
                and "def list_review_datasets" in portrait_review
                and "def review_threshold_recommendations" in portrait_review
                and "review_annotation_heuristic" in portrait_review
                and '"auto_apply": False' in portrait_review
                and 'record.get("tenant_id") != tenant_id' in portrait_review
                and "不支持的审阅标签" in portrait_review
                and "restore_review_state" in portrait_review_routes
                and 'permission_dependency("jobs:read")' in portrait_review_routes
                and 'permission_dependency("jobs")' in portrait_review_routes
                and '"track_review_annotation_created"' in portrait_review_routes
                and "load_review_state()" in portrait_bootstrap
                and "track-review-annotation-form" in console_module_sources
                and "/v1/evaluation/datasets" in console_module_sources
                and "/v1/evaluation/threshold-recommendations" in portrait_review_routes
                and "/v1/evaluation/threshold-recommendations" in console_module_sources
                and "evaluation-dataset-table" in console_module_sources
                and "evaluation-threshold-table" in console_module_sources
                and "renderEvaluationThresholdRecommendations" in console_module_sources
                and "/v1/evaluation/track-reviews" in console_module_sources
                and "/v1/evaluation/track-reviews/summary" in console_module_sources
                and "evaluation-review-summary" in console_module_sources
            ),
        },
        {
            "name": "security:public_health_minimal_disclosure",
            "ok": (
                '"status": "healthy"' in health_routes
                and '"version": APP_VERSION' in health_routes
                and '"models_root"' not in health_routes
                and '"loaded_models"' not in health_routes
                and '"available_providers": available'
                not in health_routes.split('@router.get("/ready/deep"')[0]
                and 'detail={"status": "not_ready"}' in health_routes
                and 'return {"status": "ready"}' in health_routes
                and "runtime_provider_status(available)" in health_routes
            ),
        },
        {
            "name": "security:diagnostic_health_minimal_disclosure",
            "ok": (
                "HEALTH_CHECK_FAILED" in portrait_response
                and "MODEL_READINESS_CHECK_FAILED" in portrait_response
                and "MODEL_READINESS_CHECK_FAILED" in ready_deep_section
                and '"path": str(model_path)' not in ready_deep_section
                and "str(exc)" not in ready_deep_section
                and "HEALTH_CHECK_FAILED" in postgres_health_section
                and '"error": HEALTH_CHECK_FAILED' in postgres_health_section
                and "str(exc)" not in postgres_health_section
                and "HEALTH_CHECK_FAILED" in redis_health_section
                and '"error": HEALTH_CHECK_FAILED' in redis_health_section
                and "str(exc)" not in redis_health_section
                and '"storage_dir_configured": bool(OBJECT_STORAGE_DIR)'
                in local_object_health_section
                and '"path": str(OBJECT_STORAGE_DIR)' not in local_object_health_section
                and '"bucket_configured": bool(S3_BUCKET)' in s3_health_section
                and '"endpoint_configured": bool(S3_ENDPOINT_URL)' in s3_health_section
                and '"region_configured": bool(S3_REGION)' in s3_health_section
                and '"bucket": S3_BUCKET' not in s3_health_section
                and '"endpoint": S3_ENDPOINT_URL' not in s3_health_section
                and '"region": S3_REGION' not in s3_health_section
            ),
        },
        {
            "name": "security:backend_failure_log_minimal_disclosure",
            "ok": (
                "exception_log_summary(exc)" in portrait_audit
                and portrait_postgres_impl.count("exception_log_summary(exc)") >= 5
                and "exception_log_summary(exc)" in portrait_task_queue
                and portrait_vector_store.count("exception_log_summary(exc)") >= 2
                and 'logger.warning("postgres audit write failed: %s", exc)'
                not in portrait_audit
                and 'logger.warning("postgres 健康检查失败: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("postgres gallery 加载失败ed: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("postgres threshold 加载失败ed: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("postgres video job 加载失败ed: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("postgres stream 加载失败ed: %s", exc)'
                not in portrait_postgres_impl
                and 'logger.warning("redis task queue 健康检查失败: %s", exc)'
                not in portrait_task_queue
                and 'logger.warning("pgvector 检索失败，回退到本地向量扫描: %s", exc)'
                not in portrait_vector_store
                and 'logger.warning("Qdrant 检索失败，回退到本地向量扫描: %s", exc)'
                not in portrait_vector_store
            ),
        },
        {
            "name": "security:structured_log_context",
            "ok": (
                "class JsonLogFormatter" in observability
                and "ContextVar" in observability
                and "REQUEST_ID_CONTEXT" in observability
                and "TENANT_ID_CONTEXT" in observability
                and "TRACEPARENT_CONTEXT" in observability
                and "def set_log_context" in observability
                and "def reset_log_context" in observability
                and 'payload["request_id"] = request_id' in observability
                and 'payload["tenant_id"] = tenant_id' in observability
                and "context_tokens = set_log_context(" in server
                and "reset_log_context(context_tokens)" in server
            ),
        },
        {
            "name": "security:runtime_failure_log_minimal_disclosure",
            "ok": (
                portrait_gallery_impl.count("exception_log_summary(exc)") >= 3
                and portrait_jobs.count("exception_log_summary(exc)") >= 2
                and portrait_streams.count("exception_log_summary(exc)") >= 2
                and "exception_log_summary(exc)" in health_routes
                and "exception_log_summary(exc)" in runtime_execution
                and "def model_path_fingerprint" in runtime_registry
                and "model_path_fingerprint(model_path)" in runtime_registry
                and "exception_log_summary(exc)" in runtime_registry
                and 'logger.warning("删除临时视频文件失败")' in video_io
                and 'logger.warning("删除临时视频文件失败")' in media_video_decode
                and 'logger.warning("已跳过无效人员库人员状态: %s", exc)'
                not in portrait_gallery_impl
                and 'logger.warning("向量写入失败: %s", exc)'
                not in portrait_gallery_impl
                and 'logger.warning("向量删除失败: %s", exc)'
                not in portrait_gallery_impl
                and 'logger.warning("已跳过无效视频任务状态: %s", exc)'
                not in portrait_jobs
                and 'logger.warning("skipping stream with unreadable protected URL: %s", exc)'
                not in portrait_streams
                and 'logger.warning("已跳过无效视频流状态: %s", exc)'
                not in portrait_streams
                and 'logger.warning("深度就绪模型检查失败 %s: %s", key, exc)'
                not in health_routes
                and 'logger.warning("批量推理失败，回退到逐帧推理: %s", exc)'
                not in runtime_execution
                and 'logger.info("loading model: %s from %s", cache_key_value, model_path)'
                not in runtime_registry
                and 'logger.exception("加载模型失败: %s", cache_key_value)'
                not in runtime_registry
                and 'logger.warning("删除临时视频文件失败: %s", temp_path)'
                not in video_io
                and 'logger.warning("删除临时视频文件失败: %s", temp_path)'
                not in media_video_decode
            ),
        },
        {
            "name": "security:model_management_minimal_disclosure",
            "ok": (
                '"path": bundle["path"]' not in runtime_registry
                and '"artifact_resolved": bool(bundle.get("path"))' in runtime_registry
                and '"config_path": str(MODEL_CONFIG_PATH)' not in model_query_routes
                and '"config_path": str(MODEL_CONFIG_PATH)' not in portrait_model_routes
                and '"config_path": str(MODEL_CONFIG_PATH)' not in rollout_routes
                and '"config_path": str(MODEL_CONFIG_PATH)' not in model_config_writer
                and '"path": str(model_path)' not in model_query_routes
                and "public_model_config" in model_query_routes
                and "public_model_config" in portrait_model_routes
                and '"model_card": artifact.get("model_card")' not in model_package
                and '"labels": artifact.get("labels")' not in model_package
                and '"path_configured": bool(artifact.get("path"))' in model_package
                and 'detail="模型配置文件不存在"' in model_config_writer
                and 'detail="模型构件不存在"' in model_package
                and 'detail=f"模型配置文件不存在: {MODEL_CONFIG_PATH}"'
                not in model_config_writer
                and "detail=f\"model '{model_name}' was not found under project '{project_name}'\""
                not in model_package
            ),
        },
        {
            "name": "security:request_id_normalization",
            "ok": (
                "REQUEST_ID_PATTERN" in observability
                and "def normalize_request_id" in observability
                and 'request.headers.get("x-request-id")' in observability
                and "request.state.request_id" in observability
                and "str(uuid.uuid4())" in observability
                and 'response.headers["X-Request-ID"] = request_id' in server
            ),
        },
        {
            "name": "security:validation_error_redaction",
            "ok": (
                "RequestValidationError" in server
                and "def validation_error_payload" in server
                and "def validation_error_loc" in server
                and 'loc[-1] = "extra_field"' in server
                and "@app.exception_handler(RequestValidationError)" in server
                and '"input"'
                not in server.split("def validation_error_payload", 1)[1].split(
                    "def create_app", 1
                )[0]
                and '"ctx"'
                not in server.split("def validation_error_payload", 1)[1].split(
                    "def create_app", 1
                )[0]
                and '"url"'
                not in server.split("def validation_error_payload", 1)[1].split(
                    "def create_app", 1
                )[0]
                and 'detail="不支持的视觉任务"' in vision_routes
                and "不支持的视觉任务: {task_name}" not in vision_routes
            ),
        },
        {
            "name": "security:upload_validation_error_minimal_disclosure",
            "ok": (
                'detail="上传文件为空"' in image_io
                and 'detail=f"上传文件过大：最大 {MAX_IMAGE_BYTES} 字节"' in image_io
                and 'detail="不支持的图片扩展名"' in media_image_decode
                and 'detail="上传文件包含不支持的图片内容"' in media_image_decode
                and 'detail="图片扩展名与检测到的内容不匹配"' in media_image_decode
                and 'detail="不支持的图片格式"' in media_image_decode
                and 'detail="图片内容与解码出的图片格式不匹配"' in media_image_decode
                and 'detail="上传文件不是有效图片"' in media_image_decode
                and 'detail=f"上传文件过大：最大 {max_bytes} 字节"'
                in media_image_decode
                and 'detail=f"图片像素过多：最大 {MAX_IMAGE_PIXELS}"'
                in media_image_decode
                and 'detail="不支持的视频扩展名"' in video_io
                and 'detail="上传视频包含不支持的容器内容"' in video_io
                and 'detail="视频扩展名与检测到的内容不匹配"' in video_io
                and 'detail="上传视频为空"' in video_io
                and 'detail=f"上传视频过大：最大 {MAX_VIDEO_BYTES} 字节"' in video_io
                and "不支持的图片扩展名 '{suffix}'" not in media_image_decode
                and "image extension does not match detected {detected.lower()} content"
                not in media_image_decode
                and "不支持的图片格式 '{image_format}'" not in media_image_decode
                and "image content sniffed as {detected_format.lower()}"
                not in media_image_decode
                and "uploaded file is too large: {len(data)} bytes" not in image_io
                and "uploaded file is too large: {len(data)} bytes"
                not in media_image_decode
                and "图片像素过多: {width * height}" not in media_image_decode
                and "不支持的视频扩展名 '{suffix}'" not in video_io
                and "video extension does not match detected {container} content"
                not in video_io
                and "uploaded video is too large: {len(data)} bytes" not in video_io
                and "uploaded file '{file.filename}'" not in image_io
                and "uploaded file '{file.filename}'" not in media_image_decode
                and "uploaded file '{filename" not in media_image_decode
                and "image extension for '{filename}'" not in media_image_decode
                and "uploaded video '{file.filename}'" not in video_io
                and "uploaded video '{filename" not in video_io
                and "video extension for '{filename}'" not in video_io
            ),
        },
        {
            "name": "security:biometric_vector_default_minimal_disclosure",
            "ok": (
                "image_fingerprint_embedding" not in portrait_jobs
                and "include_embeddings: bool = Form(False)" in person_tracks_routes
                and "include_embeddings: bool = False" in portrait_jobs
                and '"include_embeddings": _bool_setting(settings, "include_embeddings", False)'
                in portrait_stream_worker
                and "include_vectors: bool = Form(False)" in vision_routes
                and "if include_vectors:" in vision_routes
            ),
        },
        {
            "name": "security:unhandled_error_redaction",
            "ok": (
                "def internal_error_payload" in server
                and '"internal_error"' in server
                and '"request_id": request_id' in server
                and "v1_contract=uses_v1_contract(request)" in server
                and 'response.headers["X-Request-ID"] = request_id' in server
                and "apply_security_headers(response)" in server
                and "raise"
                not in server.split("except Exception:", 1)[1].split(
                    "duration = now() - start", 1
                )[0]
            ),
        },
        {
            "name": "security:runtime_error_response_redaction",
            "ok": (
                "def raise_internal_error" in portrait_response
                and '"message": detail' in portrait_response
                and '"request_id": request_id' in portrait_response
                and "def inference_error_boundary" in routes_inference_common
                and "except HTTPException:" in routes_inference_common
                and "raise_internal_error(request_id, internal_message)"
                in routes_inference_common
                and "internal_message=" in vision_routes
                and "internal_message=" in person_tracks_routes
                and "inference_error_boundary(" in vision_routes
                and "inference_error_boundary(" in person_tracks_routes
                and "raise_internal_error(request_id" in predict_routes
                and "raise_internal_error(request_id" in debug_routes
                and 'detail="加载模型运行时失败"' in runtime_registry
                and "运行时错误: {exc}"
                not in "\n".join(
                    [
                        predict_routes,
                        vision_routes,
                        person_tracks_routes,
                        portrait_jobs,
                        portrait_stream_worker,
                        debug_routes,
                        routes_inference_common,
                    ]
                )
            ),
        },
        {
            "name": "security:runtime_error_log_minimal_disclosure",
            "ok": (
                'logger.warning("%s: request_id=%s error=%s", log_label, request_id, exception_log_summary(exc))'
                in routes_inference_common
                and 'log_label="vision inference failed"' in vision_routes
                and 'log_label="person track inference failed"' in person_tracks_routes
                and "exception_log_summary(exc)" in portrait_jobs
                and "exception_log_summary(error)" in portrait_stream_worker
                and "exception_log_summary(exc)" in predict_routes
                and "exception_log_summary(exc)" in debug_routes
                and "logger.exception("
                not in "\n".join(
                    [
                        predict_routes,
                        vision_routes,
                        person_tracks_routes,
                        portrait_jobs,
                        portrait_stream_worker,
                        debug_routes,
                        routes_inference_common,
                    ]
                )
            ),
        },
        {
            "name": "security:rollback_failure_response_redaction",
            "ok": (
                "def raise_rollback_failure" in portrait_response
                and "def exception_log_summary" in portrait_response
                and "exception_log_summary(original_error)" in portrait_response
                and '"rollback_failed": True' in portrait_response
                and '"rollback_error_count": len(rollback_errors)' in portrait_response
                and '"rollback_errors": rollback_errors' not in portrait_response
                and 'getattr(original_error, "detail", original_error)'
                not in portrait_response
                and '"error": str(getattr(original_error' not in rollback_route_text
                and '"rollback_errors": rollback_errors' not in rollback_route_text
                and rollback_route_text.count("raise_rollback_failure(") >= 5
            ),
        },
        {
            "name": "security:http_security_headers",
            "ok": (
                "SECURITY_HEADERS_ENABLED" in settings
                and "CONTENT_SECURITY_POLICY" in settings
                and "HSTS_ENABLED" in settings
                and "HSTS_MAX_AGE_SECONDS" in settings
                and 'response.headers.setdefault("Content-Security-Policy"'
                in security_headers
                and 'response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")'
                in security_headers
                and 'response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")'
                in security_headers
                and 'response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")'
                in security_headers
                and 'response.headers.setdefault("X-Download-Options", "noopen")'
                in security_headers
                and 'response.headers.setdefault("Strict-Transport-Security", hsts_header_value())'
                in security_headers
                and "CONTENT_SECURITY_POLICY:" in compose
                and "HSTS_ENABLED: ${HSTS_ENABLED:-true}" in compose
                and "HSTS_MAX_AGE_SECONDS: ${HSTS_MAX_AGE_SECONDS:-31536000}" in compose
                and "CONTENT_SECURITY_POLICY=" in env_example
                and "HSTS_ENABLED=true" in env_example
            ),
        },
        {
            "name": "security:http_exception_protocol_headers",
            "ok": (
                "headers=exc.headers" in server
                and ("def unauthorized" in security or "unauthorized" in security)
                and "def unauthorized" in portrait_auth
                and '"WWW-Authenticate": "Bearer"' in portrait_auth
                and "Retry-After" in rate_limit
                and "def retry_after_seconds" in rate_limit
            ),
        },
        {
            "name": "security:sensitive_payload_authenticated_encryption",
            "ok": (
                "cryptography" in requirements
                and "from cryptography.hazmat.primitives.ciphers.aead import AESGCM"
                in portrait_crypto
                and "REQUIRE_ENCRYPTION" in settings
                and "ENCRYPTION_KEY_ID" in settings
                and "ENCRYPTION_KEYRING" in settings
                and "def encryption_required" in portrait_crypto
                and "def current_encryption_key_id" in portrait_crypto
                and "def parse_encryption_keyring" in portrait_crypto
                and "def candidate_decryption_keys" in portrait_crypto
                and (
                    "if encryption_required()" in portrait_crypto
                    or "if encryption_required():" in portrait_crypto
                )
                and "当 REQUIRE_ENCRYPTION=true 时，ENCRYPTION_KEY 为必填项"
                in portrait_crypto
                and 'AES_GCM_ALGORITHM = "aes-256-gcm"' in portrait_crypto
                and "AES_GCM_NONCE_BYTES = 12" in portrait_crypto
                and "os.urandom(AES_GCM_NONCE_BYTES)" in portrait_crypto
                and "AESGCM(key).encrypt(nonce, data, None)" in portrait_crypto
                and "AESGCM(key).decrypt(nonce, data, None)" in portrait_crypto
                and "InvalidTag" in portrait_crypto
                and "加密载荷认证失败" in portrait_crypto
                and "LEGACY_XOR_ALGORITHM" in portrait_crypto
                and '"key_id": key_id' in portrait_crypto
                and "candidate_decryption_keys(key_id, kdf=kdf_name" in portrait_crypto
                and "candidate_decryption_keys(key_id, kdf=LEGACY_SHA256_KDF)"
                in portrait_crypto
                and "ENCRYPTION_KEY_ID: ${ENCRYPTION_KEY_ID:-primary}" in compose
                and "ENCRYPTION_KEYRING: ${ENCRYPTION_KEYRING:-}" in compose
                and "REQUIRE_ENCRYPTION: ${REQUIRE_ENCRYPTION:-true}" in compose
                and "ENCRYPTION_KEY_ID=primary" in env_example
                and "ENCRYPTION_KEYRING=" in env_example
                and "REQUIRE_ENCRYPTION=true" in env_example
                and '"require_encryption": REQUIRE_ENCRYPTION' in portrait_admin_routes
                and '"encryption_key_id_configured": bool(ENCRYPTION_KEY_ID)'
                in portrait_admin_routes
                and '"encryption_keyring_configured": bool(ENCRYPTION_KEYRING)'
                in portrait_admin_routes
                and '"algorithm": AES_GCM_ALGORITHM' in portrait_crypto
                and '"algorithm": LEGACY_XOR_ALGORITHM' not in portrait_crypto
            ),
        },
        {
            "name": "security:legacy_model_management_rbac",
            "ok": (
                'permission_dependency("models:read")' in rollout_routes
                and 'permission_dependency("models:write")' in rollout_routes
                and model_lifecycle_routes.count(
                    'permission_dependency("models:write")'
                )
                >= 2
                and 'permission_dependency("models:write")' in model_query_routes
                and 'permission_dependency("models:read")' in portrait_model_routes
                and 'permission_dependency("models:write")' in portrait_model_routes
                and 'permission_dependency("models:write")' in debug_routes
                and 'permission_dependency("models:read")' not in debug_routes
            ),
        },
        {
            "name": "security:rollout_preview_validation_4xx",
            "ok": (
                "async def rollout_alias_preview" in rollout_routes
                and "validate_alias_name(alias_name)"
                in rollout_routes.split("async def rollout_alias_preview", 1)[1].split(
                    "@router.post", 1
                )[0]
                and "detail=str(exc)"
                not in rollout_routes.split("async def rollout_alias_preview", 1)[
                    1
                ].split("@router.post", 1)[0]
                and 'detail="别名不存在"' in rollout_routes
                and 'detail=f"别名不存在: {alias_name}"' not in rollout_routes
            ),
        },
        {
            "name": "security:legacy_model_reference_error_redaction",
            "ok": (
                "resolve_model_reference(" in vision_routes
                and "validate_model_reference_parts(" in person_tracks_routes
                and "validate_model_reference_parts(" in portrait_jobs
                and "validate_model_reference_parts(" in portrait_stream_worker
                and "resolve_model_reference(" in portrait_model_routes
                and "validate_model_reference_parts(" in debug_routes
                and "detail=str(exc)"
                not in "\n".join(
                    [
                        vision_routes,
                        person_tracks_routes,
                        portrait_jobs,
                        portrait_stream_worker,
                        portrait_model_routes,
                        debug_routes,
                    ]
                )
            ),
        },
        {
            "name": "security:legacy_inference_rbac",
            "ok": (
                'permission_dependency("models:read")' in health_routes
                and 'permission_dependency("infer")' in predict_routes
                and 'permission_dependency("infer")' in person_tracks_routes
                and 'permission_dependency("jobs")' in portrait_job_routes
                and 'permission_dependency("streams")' in portrait_stream_routes
                and 'permission_dependency("infer")' in vision_routes
            ),
        },
        {
            "name": "security:rollout_audit_rollback",
            "ok": (
                "def commit_model_config_with_audit" in model_config_writer
                and "write_raw_model_config(raw)" in model_config_writer
                and "write_rollout_audit(event, result)" in model_config_writer
                and "write_raw_model_config(previous_raw)" in model_config_writer
                and "写入发布审计失败；模型配置已回滚" in model_config_writer
                and '"rolled_back": True' in model_config_writer
                and '"rollback_failed": True' in model_config_writer
                and '"audit_error"' not in model_config_writer
                and '"rollback_error"' not in model_config_writer
                and "os.replace(temp_path, MODEL_CONFIG_PATH)" in model_config_writer
                and "except OSError:" in model_config_writer
            ),
        },
        {
            "name": "security:rollout_audit_readback",
            "ok": (
                "def read_rollout_audit" in rollout_audit
                and "public_rollout_audit_record" in rollout_audit
                and "MAX_ROLLOUT_AUDIT_LIMIT" in rollout_audit
                and "ROLLOUT_AUDIT_FIELDS" in rollout_audit
                and "ROLLOUT_TARGET_FIELDS" in rollout_audit
                and "malformed_count" in rollout_audit
                and "rollout_audit_entries" in rollout_routes
                and 'permission_dependency("models:read")' in rollout_routes
                and "read_rollout_audit(limit)" in rollout_routes
            ),
        },
        {
            "name": "security:management_mutation_audit",
            "ok": (
                '"model_warmup"' in model_lifecycle_routes
                and '"model_reload"' in model_lifecycle_routes
                and '"model_config_reloaded"' in model_query_routes
                and '"model_loaded"' in portrait_model_routes
                and '"model_unloaded"' in portrait_model_routes
                and '"admin_export"' in portrait_admin_routes
                and "stream_events_count=sum" in portrait_admin_routes
                and '"retention_cleanup"' in portrait_admin_routes
            ),
        },
        {
            "name": "security:model_management_audit_compensation",
            "ok": (
                "def model_registry_snapshot" in portrait_model_routes
                and "def restore_model_registry_snapshot" in portrait_model_routes
                and "def model_load_locks_snapshot" in portrait_model_routes
                and "previous_registry = model_registry_snapshot()"
                in portrait_model_routes
                and "previous_locks = model_load_locks_snapshot()"
                in portrait_model_routes
                and "restore_model_registry_snapshot(previous_registry, previous_locks)"
                in portrait_model_routes
                and "previous_thresholds = threshold_snapshot()"
                in portrait_model_routes
                and "def restore_threshold_snapshot" in portrait_model_routes
                and "save_threshold_state()" in portrait_model_routes
                and "模型管理变更失败，且回滚持久化失败" in portrait_model_routes
                and "def model_registry_snapshot" in model_lifecycle_routes
                and "def restore_model_registry_snapshot" in model_lifecycle_routes
                and "def model_load_locks_snapshot" in model_lifecycle_routes
                and "previous_registry = model_registry_snapshot()"
                in model_lifecycle_routes
                and "previous_locks = model_load_locks_snapshot()"
                in model_lifecycle_routes
                and "restore_model_registry_snapshot(previous_registry, previous_locks)"
                in model_lifecycle_routes
            ),
        },
        {
            "name": "security:model_config_reload_audit_compensation",
            "ok": (
                "previous_configs = deepcopy(MODEL_CONFIGS)" in model_query_routes
                and "previous_aliases = deepcopy(MODEL_ALIASES)" in model_query_routes
                and "MODEL_CONFIGS.update(previous_configs)" in model_query_routes
                and "MODEL_ALIASES.update(previous_aliases)" in model_query_routes
                and '"model_config_reloaded"' in model_query_routes
            ),
        },
        {
            "name": "security:audit_payload_limits",
            "ok": (
                "def build_audit_payload" in portrait_audit
                and "def sanitize_audit_value" in portrait_audit
                and "AUDIT_CHAIN_FIELDS" in portrait_audit
                and "AUDIT_HASH_ALGORITHM" in portrait_audit
                and "def audit_payload_hash" in portrait_audit
                and "def seal_audit_payload" in portrait_audit
                and "def last_audit_hash" in portrait_audit
                and "payload = seal_audit_payload(payload, audit_chain_previous_hash())"
                in portrait_audit
                and '"audit_prev_hash"' in portrait_audit
                and '"audit_hash"' in portrait_audit
                and "audit_hash TEXT NOT NULL"
                in (root / "tools" / "portrait_postgres_schema.sql").read_text(
                    encoding="utf-8"
                )
                and "audit_prev_hash TEXT"
                in (root / "tools" / "portrait_postgres_schema.sql").read_text(
                    encoding="utf-8"
                )
                and 'payload.get("audit_hash")' in portrait_postgres_impl
                and "AUDIT_WRITE_FAIL_CLOSED" in settings
                and "fail_closed=AUDIT_WRITE_FAIL_CLOSED" in portrait_audit
                and "if AUDIT_WRITE_FAIL_CLOSED:" in portrait_audit
                and "AUDIT_WRITE_FAIL_CLOSED: ${AUDIT_WRITE_FAIL_CLOSED:-true}"
                in compose
                and "AUDIT_WRITE_FAIL_CLOSED=true" in env_example
                and '"audit_write_fail_closed": AUDIT_WRITE_FAIL_CLOSED'
                in portrait_admin_routes
                and "MAX_AUDIT_PAYLOAD_BYTES" in settings
                and "MAX_AUDIT_DEPTH" in settings
                and "MAX_AUDIT_KEYS" in settings
                and "MAX_AUDIT_LIST_ITEMS" in settings
                and "MAX_AUDIT_STRING_LENGTH" in settings
                and "MAX_AUDIT_PAYLOAD_BYTES" in portrait_audit
                and "MAX_AUDIT_LIST_ITEMS" in portrait_audit
                and "is_sensitive_field(raw_key_text)" in portrait_audit
                and "RESERVED_AUDIT_FIELDS" in portrait_audit
                and 'key = f"field_{key}"' in portrait_audit
                and "audit_omitted_fields" in portrait_audit
                and "audit_omitted_items" in portrait_audit
                and "MAX_AUDIT_PAYLOAD_BYTES: ${MAX_AUDIT_PAYLOAD_BYTES:-32768}"
                in compose
                and "MAX_AUDIT_DEPTH: ${MAX_AUDIT_DEPTH:-6}" in compose
                and "MAX_AUDIT_KEYS: ${MAX_AUDIT_KEYS:-128}" in compose
                and "MAX_AUDIT_LIST_ITEMS: ${MAX_AUDIT_LIST_ITEMS:-64}" in compose
                and "MAX_AUDIT_STRING_LENGTH: ${MAX_AUDIT_STRING_LENGTH:-2048}"
                in compose
                and "MAX_AUDIT_PAYLOAD_BYTES=32768" in env_example
                and "MAX_AUDIT_DEPTH=6" in env_example
                and "MAX_AUDIT_KEYS=128" in env_example
                and "MAX_AUDIT_LIST_ITEMS=64" in env_example
                and "MAX_AUDIT_STRING_LENGTH=2048" in env_example
            ),
        },
        {
            "name": "security:audit_chain_console_verification",
            "ok": (
                "def public_audit_chain_verification" in portrait_audit
                and "state_path_fingerprint(audit_path)" in portrait_audit
                and "verify_audit_chain(audit_path)" in portrait_audit
                and '"/v1/admin/audit/verify"' in portrait_admin_routes
                and "public_audit_chain_verification" in portrait_admin_routes
                and 'permission_dependency("admin:status")' in portrait_admin_routes
                and '"audit_chain": audit_chain' in portrait_admin_routes
                and "/v1/admin/audit/verify" in console_module_sources
                and "auditVerificationPayload" in console_module_sources
                and "auditChainErrorCount" in console_module_sources
                and "audit_chain" in console_module_sources
                and "path_hash" in console_module_sources
            ),
        },
        {
            "name": "security:audit_event_readback",
            "ok": (
                "def read_public_audit_events" in portrait_audit
                and "def audit_event_matches_filters" in portrait_audit
                and "def audit_event_category" in portrait_audit
                and "category_counts" in portrait_audit
                and "outcome_counts" in portrait_audit
                and "def public_audit_event_record" in portrait_audit
                and "PUBLIC_AUDIT_EVENT_FIELDS" in portrait_audit
                and "MAX_PUBLIC_AUDIT_EVENT_LIMIT" in portrait_audit
                and "created_since" in portrait_audit
                and "created_until" in portrait_audit
                and 'payload.get("tenant_id") != tenant_id' in portrait_audit
                and '"api_key"'
                not in portrait_audit.split("PUBLIC_AUDIT_EVENT_FIELDS", 1)[1].split(
                    "def", 1
                )[0]
                and '"/v1/admin/audit/events"' in portrait_admin_routes
                and "read_public_audit_events" in portrait_admin_routes
                and "created_until 必须大于等于 created_since" in portrait_admin_routes
                and "event: str | None = Query" in portrait_admin_routes
                and "outcome: str | None = Query" in portrait_admin_routes
                and "category: str | None = Query" in portrait_admin_routes
                and "不支持的审计事件类别" in portrait_admin_routes
                and 'permission_dependency("admin:status")' in portrait_admin_routes
                and "MAX_PUBLIC_AUDIT_EVENT_LIMIT" in portrait_admin_routes
                and "/v1/admin/audit/events?${auditEventQueryParams().toString()}"
                in console_module_sources
                and "function auditEventQueryParams" in console_module_sources
                and "audit-event-filter-button" in console_module_sources
                and "audit-category-filter-input" in console_module_sources
                and 'params.set("category", categoryFilter)' in console_module_sources
                and "audit-event-table" in console_module_sources
                and "function renderAuditEventRows" in console_module_sources
                and "auditEventsPayload" in console_module_sources
                and "audit_events" in console_module_sources
            ),
        },
        {
            "name": "security:backup_snapshot_readback",
            "ok": (
                "def public_backup_snapshot_record" in portrait_audit
                and "def read_public_backup_snapshots" in portrait_audit
                and "PUBLIC_BACKUP_SNAPSHOT_FIELDS" in portrait_audit
                and "MAX_PUBLIC_BACKUP_SNAPSHOT_LIMIT" in portrait_audit
                and 'record["snapshot_id"] = audit_hash' in portrait_audit
                and 'payload.get("tenant_id") != tenant_id or payload.get("event") != "admin_backup"'
                in portrait_audit
                and '"object_key"'
                not in portrait_audit.split("PUBLIC_BACKUP_SNAPSHOT_FIELDS", 1)[
                    1
                ].split("PUBLIC_AUDIT_EVENT_FIELDS", 1)[0]
                and '"bucket"'
                not in portrait_audit.split("PUBLIC_BACKUP_SNAPSHOT_FIELDS", 1)[
                    1
                ].split("PUBLIC_AUDIT_EVENT_FIELDS", 1)[0]
                and '"sha256"'
                not in portrait_audit.split("PUBLIC_BACKUP_SNAPSHOT_FIELDS", 1)[
                    1
                ].split("PUBLIC_AUDIT_EVENT_FIELDS", 1)[0]
                and '"/v1/admin/backups"' in portrait_admin_routes
                and "read_public_backup_snapshots" in portrait_admin_routes
                and 'permission_dependency("admin:export")' in portrait_admin_routes
                and "MAX_PUBLIC_BACKUP_SNAPSHOT_LIMIT" in portrait_admin_routes
                and "/v1/admin/backups?limit=20" in console_module_sources
                and "backup-snapshot-summary" in console_module_sources
                and "backup-snapshot-table" in console_module_sources
                and "backup-snapshot-refresh-button" in console_module_sources
                and "function renderBackupSnapshots" in console_module_sources
                and "async function refreshAdminData" in console_module_sources
                and "backup_snapshots" in console_module_sources
            ),
        },
        {
            "name": "security:bounded_api_list_responses",
            "ok": (
                "API_LIST_DEFAULT_LIMIT" in settings
                and "MAX_API_LIST_LIMIT" in settings
                and "STREAM_EVENT_LIST_DEFAULT_LIMIT" in settings
                and "MAX_STREAM_EVENT_LIST_LIMIT" in settings
                and "def normalize_list_pagination" in portrait_pagination
                and "def normalize_stream_event_pagination" in portrait_pagination
                and "def page_items" in portrait_pagination
                and "def page_items_keyset" in portrait_pagination
                and '"next_offset"' in portrait_pagination
                and '"next_cursor"' in portrait_pagination
                and "normalize_list_pagination" in portrait_stream_routes
                and "normalize_stream_event_pagination" in portrait_stream_routes
                and "page_items_keyset(" in portrait_stream_routes
                and 'key_fields=["stream_id"]' in portrait_stream_routes
                and 'key_fields=["created_at", "event_id"]' in portrait_stream_routes
                and "normalize_list_pagination" in portrait_admin_routes
                and "normalize_stream_event_pagination" in portrait_admin_routes
                and "page_items_keyset(" in portrait_admin_routes
                and '"pagination"' in portrait_admin_routes
                and '"events_pagination"' in portrait_admin_routes
                and "API_LIST_DEFAULT_LIMIT: ${API_LIST_DEFAULT_LIMIT:-100}" in compose
                and "MAX_API_LIST_LIMIT: ${MAX_API_LIST_LIMIT:-500}" in compose
                and "STREAM_EVENT_LIST_DEFAULT_LIMIT: ${STREAM_EVENT_LIST_DEFAULT_LIMIT:-100}"
                in compose
                and "MAX_STREAM_EVENT_LIST_LIMIT: ${MAX_STREAM_EVENT_LIST_LIMIT:-200}"
                in compose
                and "API_LIST_DEFAULT_LIMIT=100" in env_example
                and "MAX_API_LIST_LIMIT=500" in env_example
                and "STREAM_EVENT_LIST_DEFAULT_LIMIT=100" in env_example
                and "MAX_STREAM_EVENT_LIST_LIMIT=200" in env_example
            ),
        },
        {
            "name": "security:explicit_numeric_parameter_bounds",
            "ok": (
                "def validate_int_range" in portrait_request_validation
                and "isinstance(value, bool)" in portrait_request_validation
                and 'validate_int_range("top_k", top_k, minimum=1, maximum=100)'
                in portrait_gallery_routes
                and 'validate_int_range("frame_interval"' in portrait_job_routes
                and "max_frames = validate_int_range(" in portrait_job_routes
                and "MAX_VIDEO_FRAMES" in portrait_job_routes
                and "validate_detection_parameters(" in portrait_job_routes
                and "top_k = max(1, min(100" not in portrait_gallery_routes
                and "max(1, min(int(max_frames)" not in portrait_job_routes
            ),
        },
        {
            "name": "security:tenant_header_contract",
            "ok": (
                "TENANT_HEADER_REQUIRED" in settings
                and "缺少 x-tenant-id 请求头" in portrait_security
                and 'request.url.path.startswith("/v1/")' in portrait_security
                and "TENANT_HEADER_REQUIRED: ${TENANT_HEADER_REQUIRED:-true}" in compose
                and "TENANT_HEADER_REQUIRED=true" in env_example
            ),
        },
        {
            "name": "security:tenant_person_id_validation",
            "ok": (
                "TENANT_PATTERN" in portrait_security
                and "PERSON_ID_PATTERN" in portrait_security
                and "RESOURCE_ID_PATTERN" in portrait_security
                and "def validate_job_id" in portrait_security
                and "def validate_stream_id" in portrait_security
                and "validate_job_id(job_id)" in portrait_job_routes
                and "validate_stream_id(stream_id)" in portrait_stream_routes
            ),
        },
        {
            "name": "security:gallery_structured_tenant_key",
            "ok": (
                (
                    "GalleryKey = tuple[str, str]" in portrait_gallery
                    or "GalleryKey = tuple[str, str]" in portrait_gallery_records
                )
                and (
                    "def gallery_key" in portrait_gallery
                    or "def gallery_key" in portrait_gallery_records
                )
            ),
        },
        {
            "name": "security:job_stream_structured_tenant_keys",
            "ok": (
                "JobKey = tuple[str, str]" in portrait_jobs
                and "def job_key" in portrait_jobs
                and "StreamKey = tuple[str, str]" in portrait_streams
                and "def stream_key" in portrait_streams
            ),
        },
        {
            "name": "security:public_response_redaction",
            "ok": (
                "redact_sensitive_fields(self.metadata)"
                in (portrait_gallery + portrait_gallery_records)
                and "redact_sensitive_fields(self.settings)" in portrait_streams
                and "redact_sensitive_fields(self.payload)" in portrait_streams
                and "redact_sensitive_fields(self.payload)" in portrait_task_queue
                and '"filename"' in portrait_security
                and "def to_dict(self, include_filename: bool = False, include_fingerprint: bool = False)"
                in media_schema
                and "if include_filename and self.filename is not None:" in media_schema
                and "if include_fingerprint and self.fingerprint is not None:"
                in media_schema
                and "if self.fingerprint is not None:" not in media_schema
                and 'SENSITIVE_VIDEO_METADATA_KEYS = {"filename", "video_bytes", "frame_fingerprints"}'
                in video_io
                and "def public_video_metadata" in video_io
                and "public_video_metadata(metadata)" in portrait_stream_worker
                and "public_video_job_result" in portrait_jobs
                and '"filename": filenames[index]' not in inference_classification
                and '"filename": filenames[index]' not in inference_detection
                and '"filename": filename' not in vision_routes
                and "object_record_metadata(object_type, filename)"
                in portrait_object_storage
                and "def public_object_info" in portrait_object_storage
            ),
        },
        {
            "name": "security:stream_event_state_redaction",
            "ok": (
                "from app.portrait_security import redact_sensitive_fields"
                in portrait_stream_worker
                and "persisted_payload = redact_sensitive_fields(payload or {})"
                in portrait_stream_worker
                and '"payload": persisted_payload' in portrait_stream_worker
                and '"payload": payload or {}' not in portrait_stream_worker
            ),
        },
        {
            "name": "security:stream_sensitive_state_protection",
            "ok": (
                "PROTECTED_STATE_VALUE_MARKER" in portrait_streams
                and "def protect_sensitive_state_fields" in portrait_streams
                and "def reveal_sensitive_state_fields" in portrait_streams
                and "is_sensitive_field(key)" in portrait_streams
                and "encrypt_bytes(raw)" in portrait_streams
                and "decrypt_bytes(protected_payload)" in portrait_streams
                and '"settings": protect_sensitive_state_fields(self.settings)'
                in portrait_streams
                and '"metadata": protect_sensitive_state_fields(self.metadata)'
                in portrait_streams
                and 'settings=reveal_sensitive_state_fields(payload.get("settings"))'
                in portrait_streams
                and 'metadata=reveal_sensitive_state_fields(payload.get("metadata"))'
                in portrait_streams
            ),
        },
        {
            "name": "security:stream_url_ssrf_and_secret_protection",
            "ok": (
                "import socket" in stream_decode
                and "def resolve_stream_host_addresses" in stream_decode
                and "socket.getaddrinfo" in stream_decode
                and "def reject_private_resolved_addresses" in stream_decode
                and "reject_private_resolved_addresses(parsed.hostname)"
                in stream_decode
                and "parsed.query" in stream_decode
                and "parsed.fragment" in stream_decode
                and "stream_url_protected" in portrait_streams
                and "def protect_stream_url" in portrait_streams
                and "def reveal_stream_url" in portrait_streams
                and 'payload.pop("stream_url", None)' in portrait_streams
                and "protect_stream_url(stream.stream_url)" in portrait_streams
                and "reveal_stream_url(protected_url)" in portrait_streams
            ),
        },
        {
            "name": "security:metadata_input_limits",
            "ok": (
                "normalize_public_metadata" in portrait_security
                and "MAX_PUBLIC_METADATA_BYTES" in settings
                and "MAX_PUBLIC_METADATA_BYTES" in compose
                and "MAX_PUBLIC_METADATA_BYTES=16384" in env_example
                and "normalize_public_metadata(parsed"
                in portrait_gallery_route_orchestration
                and "normalize_public_metadata(payload.settings"
                in portrait_stream_routes
            ),
        },
        {
            "name": "security:threshold_control_contract",
            "ok": (
                "SUPPORTED_THRESHOLD_PROFILES" in portrait_thresholds
                and "def validate_threshold_profile" in portrait_thresholds
                and 'detail="不支持的阈值方案"' in portrait_thresholds
                and "不支持的阈值方案: {profile}" not in portrait_thresholds
                and "validate_threshold_profile(threshold_profile)"
                in portrait_compare_routes
                and "validate_threshold_profile(threshold_profile)"
                in portrait_gallery_route_orchestration
                and "SUPPORTED_GALLERY_MODALITIES"
                in portrait_gallery_route_orchestration
                and "def validate_gallery_modality"
                in portrait_gallery_route_orchestration
                and (
                    "modality = validate_gallery_modality(modality)"
                    in portrait_gallery_route_orchestration
                    or "modality_key = validate_gallery_modality(modality)"
                    in portrait_gallery_route_orchestration
                )
                and 'detail="不支持的模态"' in portrait_gallery_route_orchestration
                and 'profile=result["profile"]' in portrait_model_routes
                and "def validate_threshold_modality" in portrait_thresholds
                and 'detail="不支持的模态"' in portrait_thresholds
                and "不支持的模态: {modality}" not in portrait_thresholds
                and "def validate_threshold_value" in portrait_thresholds
                and "isinstance(raw_value, bool)" in portrait_thresholds
                and "math.isfinite" in portrait_thresholds
            ),
        },
        {
            "name": "security:strict_mutation_request_schemas",
            "ok": (
                'ConfigDict(extra="forbid", protected_namespaces=())' in schemas
                and "class InferenceRequest(BaseModel)" in schemas
                and "class ModelRequest(BaseModel)" in schemas
                and "class WarmupRequest(BaseModel)" in schemas
                and "class AliasSwitchRequest(BaseModel)" in schemas
                and "class AliasRollbackRequest(BaseModel)" in schemas
                and "class AliasRolloutTarget(BaseModel)" in schemas
                and "class AliasWeightedRolloutRequest(BaseModel)" in schemas
                and schemas.count('ConfigDict(extra="forbid", protected_namespaces=())')
                >= 3
                and schemas.count('ConfigDict(extra="forbid")') >= 4
                and "class GalleryPatchRequest(BaseModel)" in portrait_gallery_routes
                and 'ConfigDict(extra="forbid")' in portrait_gallery_routes
                and "display_name: str | None = Field(default=None, max_length=256)"
                in portrait_gallery_routes
                and "payload: GalleryPatchRequest" in portrait_gallery_routes
                and "补丁请求体不能为空" in portrait_gallery_routes
                and "class ThresholdUpdateRequest(BaseModel)" in portrait_model_routes
                and 'ConfigDict(extra="forbid")' in portrait_model_routes
                and "reject_boolean_thresholds" in portrait_model_routes
                and "payload: ThresholdUpdateRequest" in portrait_model_routes
                and "class StreamCreateRequest(BaseModel)" in portrait_stream_routes
                and 'ConfigDict(extra="forbid")' in portrait_stream_routes
                and "class RetentionCleanupRequest(BaseModel)" in portrait_admin_routes
                and 'ConfigDict(extra="forbid")' in portrait_admin_routes
            ),
        },
        {
            "name": "security:rate_limit_bucket_bounds",
            "ok": (
                "RATE_LIMIT_MAX_BUCKETS" in settings
                and "RATE_LIMIT_PER_MINUTE" in settings
                and "RATE_LIMIT_BURST" in settings
                and "RATE_LIMIT_BUCKET_TTL_SECONDS" in settings
                and "def cleanup_idle_buckets" in rate_limit
                and "def ensure_bucket_capacity" in rate_limit
                and "限流桶容量已耗尽" in rate_limit
                and "RATE_LIMIT_PER_MINUTE: ${RATE_LIMIT_PER_MINUTE:-120}" in compose
                and "RATE_LIMIT_BURST: ${RATE_LIMIT_BURST:-240}" in compose
                and "RATE_LIMIT_MAX_BUCKETS: ${RATE_LIMIT_MAX_BUCKETS:-10000}"
                in compose
                and "RATE_LIMIT_BUCKET_TTL_SECONDS: ${RATE_LIMIT_BUCKET_TTL_SECONDS:-3600}"
                in compose
                and "RATE_LIMIT_PER_MINUTE=120" in env_example
                and "RATE_LIMIT_BURST=240" in env_example
                and "RATE_LIMIT_MAX_BUCKETS=10000" in env_example
                and "RATE_LIMIT_BUCKET_TTL_SECONDS=3600" in env_example
            ),
        },
        {
            "name": "security:shared_state_locking",
            "ok": (
                "GALLERY_LOCK = threading.RLock()" in portrait_gallery_impl
                and "VIDEO_JOBS_LOCK = threading.RLock()" in portrait_jobs
                and "STREAMS_LOCK = threading.RLock()" in portrait_streams
                and "THRESHOLD_PROFILES_LOCK = threading.RLock()" in portrait_thresholds
                and "METRICS_LOCK = threading.RLock()"
                in (root / "app" / "metrics.py").read_text(encoding="utf-8")
                and "BUCKETS_LOCK = threading.RLock()" in rate_limit
                and "def stream_records_snapshot" in portrait_streams
                and "stream_records_snapshot()" in portrait_admin_routes
                and "stream_records_snapshot()" in portrait_stream_routes
            ),
        },
        {
            "name": "security:global_request_body_limit",
            "ok": (
                "MAX_REQUEST_BODY_BYTES" in settings
                and "def limit_request_body" in server
                and 'request.headers.get("content-length")' in server
                and "request.receive" in server
                and "HTTPException(status_code=413" in server
                and "MAX_REQUEST_BODY_BYTES: ${MAX_REQUEST_BODY_BYTES:-805306368}"
                in compose
                and "MAX_REQUEST_BODY_BYTES=805306368" in env_example
                and 'CPU_FALLBACK_ENABLED = parse_bool_env("CPU_FALLBACK_ENABLED", True)'
                in settings
                and "CPU_FALLBACK_ENABLED: ${CPU_FALLBACK_ENABLED:-true}" in compose
                and "CPU_FALLBACK_ENABLED=true" in env_example
                and 'FORCE_CPU = parse_bool_env("FORCE_CPU", False)' in settings
                and "FORCE_CPU=true" in cpu_dockerfile
                and 'FORCE_CPU: "true"' in cpu_compose
                and "FORCE_CPU: ${FORCE_CPU" not in cpu_compose
                and "CPU_TRUSTED_HOSTS" in cpu_compose
                and "cpu-worker-0" in cpu_compose
                and "gpu-worker-0" not in cpu_compose
                and "CPU_TRUSTED_HOSTS=127.0.0.1,localhost,cpu-worker-0,portrait-stream-worker"
                in env_example
            ),
        },
        {
            "name": "security:state_write_fail_closed",
            "ok": (
                "STATE_WRITE_FAIL_CLOSED" in settings
                and "handle_state_write_error" in portrait_state
                and "状态写入失败" in portrait_state
                and "HTTP_503_SERVICE_UNAVAILABLE" in portrait_state
                and "STATE_WRITE_FAIL_CLOSED: ${STATE_WRITE_FAIL_CLOSED:-true}"
                in compose
                and "STATE_WRITE_FAIL_CLOSED=true" in env_example
            ),
        },
        {
            "name": "security:state_read_fail_closed",
            "ok": (
                "STATE_READ_FAIL_CLOSED" in settings
                and 'STATE_READ_FAIL_CLOSED = parse_bool_env("STATE_READ_FAIL_CLOSED", True)'
                in settings
                and "from app.settings import STATE_READ_FAIL_CLOSED, STATE_WRITE_FAIL_CLOSED"
                in portrait_state
                and "if STATE_READ_FAIL_CLOSED:" in portrait_state
                and 'detail="状态读取失败"' in portrait_state
                and "def handle_state_read_error" in portrait_state
                and "gallery state 根节点必须是映射" in portrait_gallery_impl
                and "video jobs state 根节点必须是映射" in portrait_jobs
                and "streams state 根节点必须是映射" in portrait_streams
                and "threshold state 根节点必须是映射" in portrait_thresholds
                and "STATE_READ_FAIL_CLOSED: ${STATE_READ_FAIL_CLOSED:-true}" in compose
                and "STATE_READ_FAIL_CLOSED=true" in env_example
            ),
        },
        {
            "name": "security:state_file_log_minimal_disclosure",
            "ok": (
                "def state_path_fingerprint" in portrait_state
                and 'hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]'
                in portrait_state
                and "exception_log_summary(exc)" in portrait_state
                and "exception_log_summary(replace_exc)" in portrait_state
                and "path_hash=%s" in portrait_state
                and "读取状态文件失败 %s: %s" not in portrait_state
                and "写入状态文件失败 %s: %s" not in portrait_state
                and "atomic state 替换失败 for %s: %s" not in portrait_state
                and "追加审计文件失败 %s: %s" not in portrait_state
            ),
        },
        {
            "name": "security:job_stream_state_fail_closed",
            "ok": (
                "PORTRAIT_JOBS_STATE_PATH" in settings
                and "PORTRAIT_STREAMS_STATE_PATH" in settings
                and "read_json_state(PORTRAIT_JOBS_STATE_PATH" in portrait_jobs
                and "write_json_state(" in portrait_jobs
                and "PORTRAIT_JOBS_STATE_PATH," in portrait_jobs
                and "read_json_state(PORTRAIT_STREAMS_STATE_PATH" in portrait_streams
                and "write_json_state(PORTRAIT_STREAMS_STATE_PATH" in portrait_streams
                and "restore_video_job(job, previous_job)" in portrait_jobs
                and "restore_stream(stream, previous_stream)" in portrait_streams
                and "PORTRAIT_JOBS_STATE_PATH" in compose
                and "PORTRAIT_STREAMS_STATE_PATH" in compose
                and "PORTRAIT_JOBS_STATE_PATH=/workspace/runtime-state/portrait-jobs.json"
                in env_example
                and "PORTRAIT_STREAMS_STATE_PATH=/workspace/runtime-state/portrait-streams.json"
                in env_example
            ),
        },
        {
            "name": "security:task_queue_enqueue_compensation",
            "ok": (
                "def append_task_queue_state" in portrait_task_queue
                and "fail_closed=required" in portrait_task_queue
                and "TASK_MESSAGE_STORE.remove(message)" in portrait_task_queue
                and (
                    "remove_video_job(job.job_id, tenant_id)" in portrait_job_routes
                    or "run_blocking_io(remove_video_job, job.job_id, tenant_id)"
                    in portrait_job_routes
                )
                and "TASK_QUEUE.enqueue" in portrait_job_routes
                and '"input_ref": input_ref' in portrait_job_routes
                and "TASK_QUEUE.remove" in portrait_job_routes
                and (
                    'audit_event("video_job_created"' in portrait_job_routes
                    or '"video_job_created"' in portrait_job_routes
                )
            ),
        },
        {
            "name": "security:job_audit_compensation",
            "ok": (
                "def rollback_video_job_snapshot" in portrait_job_routes
                and (
                    "remove_video_job(job.job_id, tenant_id)" in portrait_job_routes
                    or "run_blocking_io(remove_video_job, job.job_id, tenant_id)"
                    in portrait_job_routes
                )
                and "restore_video_job(job, previous_job)" in portrait_job_routes
                and (
                    "restore_video_job_in_store(job)" in portrait_job_routes
                    or "persist_video_job(job)" in portrait_job_routes
                )
                and "视频任务变更失败，且回滚持久化失败" in portrait_job_routes
            ),
        },
        {
            "name": "security:tenant_scoped_background_jobs",
            "ok": (
                '"tenant_id": tenant_id' in portrait_job_routes
                and '"input_ref": input_ref' in portrait_job_routes
                and "stage_video_upload" in portrait_job_routes
                and "background_tasks.add_task(" not in portrait_job_routes
                and "portrait-video-job-worker:" in compose
                and "class RedisTaskQueue" in portrait_task_queue
                and "xreadgroup" in portrait_task_queue
                and "xautoclaim" in portrait_task_queue
                and "async def process_video_job_message" in portrait_video_job_worker
                and "validate_video_job_message" in portrait_video_job_worker
                and "input_ref=task[" in portrait_video_job_worker
                and "job = get_video_job(job_id, tenant_id=tenant_id)" in portrait_jobs
                and "tenant_hash=%s job_hash=%s attempt=%s error=%s" in portrait_jobs
            ),
        },
        {
            "name": "security:video_job_error_redaction",
            "ok": (
                'VIDEO_JOB_ERROR_MESSAGE = "视频任务失败"' in portrait_jobs
                and "def public_video_job_error" in portrait_jobs
                and '"error": public_video_job_error(self.error)' in portrait_jobs
                and 'error=public_video_job_error(payload.get("error"))'
                in portrait_jobs
                and "job.error = VIDEO_JOB_ERROR_MESSAGE" in portrait_jobs
                and "exception_log_summary(exc)" in portrait_jobs
                and "video_job_identifier_fingerprint(tenant_id)" in portrait_jobs
                and 'logger.exception("视频任务失败' not in portrait_jobs
                and "job.error = str(exc)" not in portrait_jobs
                and 'detail="任务不存在"' in portrait_job_routes
                and 'detail=f"任务不存在: {job_id}"' not in portrait_job_routes
            ),
        },
        {
            "name": "security:resource_not_found_minimal_disclosure",
            "ok": (
                'detail="人员不存在"' in portrait_gallery_impl
                and 'detail="人员不存在"' in portrait_gallery_routes
                and 'detail="视频流不存在"' in portrait_stream_routes
                and 'detail="任务不存在"' in portrait_job_routes
                and 'detail=f"人员不存在: {resolved_id}"' not in portrait_gallery_impl
                and 'detail=f"人员不存在: {person_id}"' not in portrait_gallery_routes
                and 'detail=f"视频流不存在: {stream_id}"' not in portrait_stream_routes
                and 'detail=f"任务不存在: {job_id}"' not in portrait_job_routes
            ),
        },
        {
            "name": "security:state_mutation_rollback",
            "ok": (
                "previous_person = deepcopy(person)" in portrait_gallery_impl
                and "GALLERY.pop(key, None)" in portrait_gallery_impl
                and "GALLERY[key] = previous_person" in portrait_gallery_impl
                and "person.features = previous_person.features"
                in portrait_gallery_impl
                and "previous_thresholds = threshold_snapshot()" in portrait_thresholds
                and "THRESHOLD_PROFILES.clear()" in portrait_thresholds
                and "THRESHOLD_PROFILES.update(deepcopy(previous_thresholds))"
                in portrait_thresholds
            ),
        },
        {
            "name": "security:gallery_audit_compensation",
            "ok": (
                "def rollback_gallery_mutation" in portrait_gallery_mutations
                and "def restore_gallery_person_snapshot" in portrait_gallery_mutations
                and "created_object_infos: list[dict[str, Any]]"
                in portrait_gallery_mutations
                and "cleanup_object_after_failed_feature(object_info, object_store=object_store)"
                in portrait_gallery_mutations
                and "persist_delete_hook(tenant_id, person_id)"
                in portrait_gallery_mutations
                and 'errors.append("恢复前删除已变更人员失败")'
                in portrait_gallery_mutations
                and "persist_person_hook(restored_person)" in portrait_gallery_mutations
                and "persist_feature_hook(restored_person, feature)"
                in portrait_gallery_mutations
                and "人员库变更失败，且回滚持久化失败" in portrait_gallery_mutations
            ),
        },
        {
            "name": "security:stream_audit_compensation",
            "ok": (
                "def rollback_stream_snapshot" in portrait_stream_routes
                and (
                    "remove_stream(stream.stream_id, tenant_id)"
                    in portrait_stream_routes
                    or "run_blocking_io(remove_stream, stream.stream_id, tenant_id)"
                    in portrait_stream_routes
                )
                and "restore_stream(stream, previous_stream)" in portrait_stream_routes
                and "persist_stream(stream)" in portrait_stream_routes
                and "视频流变更失败，且回滚持久化失败" in portrait_stream_routes
                and "def remove_stream" in portrait_streams
                and "def delete_stream_state" in portrait_streams
            ),
        },
        {
            "name": "security:postgres_stream_event_snapshot_sync",
            "ok": (
                "DELETE FROM portrait_stream_events WHERE tenant_id = %s AND stream_id = %s"
                in portrait_postgres_impl
                and "def delete_stream" in portrait_postgres_impl
                and "DELETE FROM portrait_streams WHERE tenant_id = %s AND stream_id = %s"
                in portrait_postgres_impl
            ),
        },
        {
            "name": "security:retention_cleanup_compensation",
            "ok": (
                "def rollback_retention_cleanup" in portrait_admin_routes
                and "removed_job_snapshots: list[VideoJob]" in portrait_admin_routes
                and "trimmed_stream_snapshots: list[tuple[StreamRecord, StreamRecord]]"
                in portrait_admin_routes
                and "removed_gallery_snapshots: list[PersonRecord]"
                in portrait_admin_routes
                and "def cleanup_retained_gallery_feature_objects"
                in portrait_admin_routes
                and "delete_gallery_person(previous_person.person_id, tenant_id=tenant_id)"
                in portrait_admin_routes
                and "feature_object_infos(person)" in portrait_admin_routes
                and "candidate_gallery_object_reference_count" in portrait_admin_routes
                and "removed_gallery_people" in portrait_admin_routes
                and "deleted_gallery_objects" in portrait_admin_routes
                and "restore_stream(stream, previous_stream)" in portrait_admin_routes
                and (
                    "restore_gallery_person(person)" in portrait_admin_routes
                    or "GALLERY[gallery_key(restored_person.tenant_id, restored_person.person_id)]"
                    in portrait_admin_routes
                )
                and "persist_feature(restored_person, feature)"
                in portrait_admin_runtime_text
                and (
                    "restore_video_job_in_store(job)" in portrait_admin_routes
                    or "persist_video_job(restored_job)" in portrait_admin_routes
                )
                and "persist_stream(stream)" in portrait_admin_routes
                and "OBJECT_CLEANUP_FAILED" in portrait_admin_routes
                and "保留清理失败，且回滚持久化失败" in portrait_admin_routes
            ),
        },
        {
            "name": "security:object_write_compensation",
            "ok": (
                "def delete_object" in portrait_object_storage
                and "OBJECT_DELETE_FAILED" in portrait_object_storage
                and "OBJECT_CLEANUP_FAILED" in portrait_gallery_mutation_text
                and "def object_key_fingerprint" in portrait_object_storage
                and "target.unlink(missing_ok=True)" in portrait_object_storage
                and "delete_object(Bucket=S3_BUCKET, Key=object_key)"
                in portrait_object_storage
                and object_delete_sections.count("exception_log_summary(exc)") >= 2
                and '"object_key": object_key' not in object_delete_sections
                and '"bucket": S3_BUCKET' not in object_delete_sections
                and '"error": str(exc)' not in object_delete_sections
                and "return str(exc)" not in portrait_gallery_mutation_text
                and "def cleanup_object_after_failed_feature"
                in portrait_gallery_mutations
                and "cleanup_object_after_failed_feature(object_info, object_store=object_store)"
                in portrait_gallery_mutations
            ),
        },
        {
            "name": "security:gallery_delete_object_cleanup",
            "ok": (
                "object_info: dict[str, Any] | None = None"
                in (portrait_gallery + portrait_gallery_records)
                and "def feature_object_infos"
                in (portrait_gallery + portrait_gallery_records)
                and 'payload["object_info"] = deepcopy(self.object_info)'
                in (portrait_gallery + portrait_gallery_records)
                and "object_info=deepcopy(object_info) if object_info else None"
                in (portrait_gallery + portrait_gallery_records)
                and "object_info=object_info" in portrait_gallery_route_orchestration
                and "def cleanup_gallery_feature_objects" in portrait_gallery_mutations
                and '"gallery_delete_person_requested"' in portrait_gallery_routes
                and 'outcome="started"' in portrait_gallery_routes
                and "object_reference_count=len(feature_object_infos(previous_person))"
                in portrait_gallery_routes
                and "cleanup_gallery_feature_objects," in portrait_gallery_routes
                and "object_store=OBJECT_STORE" in portrait_gallery_routes
                and "restore_gallery_person_snapshot," in portrait_gallery_routes
                and "persist_delete_hook=persist_person_delete"
                in portrait_gallery_routes
                and "persist_person_hook=persist_person" in portrait_gallery_routes
                and "persist_feature_hook=persist_feature" in portrait_gallery_routes
                and "OBJECT_CLEANUP_FAILED" in portrait_gallery_mutation_text
                and "deleted_object_count" in portrait_gallery_routes
                and "object_info JSONB NOT NULL DEFAULT '{}'::jsonb"
                in portrait_postgres_schema
                and "f.object_info" in portrait_postgres_impl
                and "object_info = EXCLUDED.object_info" in portrait_postgres_impl
                and 'jsonb(feature.get("object_info") if isinstance(feature.get("object_info"), dict) else {})'
                in portrait_postgres_impl
            ),
        },
        {
            "name": "security:local_object_atomic_write",
            "ok": (
                "def write_local_object_payload" in portrait_object_storage
                and "temp_path = target.with_name" in portrait_object_storage
                and "os.replace(temp_path, target)" in portrait_object_storage
                and "except OSError:" in portrait_object_storage
                and "dump(target)" in portrait_object_storage
                and "temp_path.unlink(missing_ok=True)" in portrait_object_storage
                and "write_local_object_payload(target, payload)"
                in portrait_object_storage
            ),
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 PortraitHub 生产就绪状态。")
    parser.add_argument("--root", default=".")
    parser.add_argument("--models-root", default="models")
    parser.add_argument(
        "--scope",
        choices=["all", "platform"],
        default="all",
        help="使用 platform 跳过真实模型能力状态检查，同时保留构件和契约检查。",
    )
    parser.add_argument(
        "--strict", action="store_true", help="遇到回退能力或缺失模型文件时失败。"
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    models_root = Path(args.models_root).resolve()
    checks = [
        *check_templates(root),
        *check_data_stack(root),
        *check_security_controls(root),
    ]
    skipped: list[dict[str, Any]] = []
    if args.scope == "all":
        checks.extend(check_capabilities(root))
    else:
        skipped.append(
            {
                "name": "capabilities",
                "reason": "scope=platform skips real model capability status",
            }
        )
    checks.extend(check_model_files(root, models_root))
    strict_failures = [item for item in checks if not item["ok"]]
    output = {
        "ok": not strict_failures if args.strict else True,
        "strict": args.strict,
        "scope": args.scope,
        "models_root": str(models_root),
        "checks": checks,
        "skipped": skipped,
        "strict_failure_count": len(strict_failures),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if args.strict and strict_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
