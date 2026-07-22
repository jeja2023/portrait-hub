"""仓库结构与数据栈门禁：必备构件清单与生产可选依赖检查。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def check_templates(root: Path) -> list[dict[str, Any]]:
    required = [
        "app/portrait_errors.py",
        "app/portrait_runtime_store.py",
        "app/portrait_review.py",
        "app/portrait_gallery_orchestration.py",
        "app/portrait_access.py",
        "app/admin_configuration.py",
        "app/config_overrides.py",
        "app/network_access_policy.py",
        "app/routes_admin_configuration.py",
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
        "frontend/console-next/package.json",
        "frontend/console-next/vite.config.ts",
        "frontend/console-next/src/main.ts",
        "frontend/console-next/src/auth/session.ts",
        "frontend/console-next/src/api/generated.ts",
        "frontend/console-next/src/views/admin/ConfigurationView.vue",
        "frontend/console-next/dist/index.html",
        "frontend/console-next/dist/.vite/manifest.json",
        "tools/deploy_check.py",
        "tools/apply_admin_configuration.py",
        "tools/deploy_checks/__init__.py",
        "tools/deploy_checks/common.py",
        "tools/deploy_checks/containers.py",
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
        "package-lock.json",
        "sdk/python/portrait_hub_client.py",
        "sdk/node/portraitHubClient.js",
    ]
    return [{"name": f"template:{item}", "ok": (root / item).is_file()} for item in required]


def check_data_stack(root: Path) -> list[dict[str, Any]]:
    optional_path = root / "requirements" / "prod-optional.txt"
    optional = optional_path.read_text(encoding="utf-8") if optional_path.is_file() else ""
    schema = (
        (root / "tools" / "portrait_postgres_schema.sql").read_text(encoding="utf-8")
        if (root / "tools" / "portrait_postgres_schema.sql").is_file()
        else ""
    )
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8") if (root / "Dockerfile").is_file() else ""
    compose = (
        (root / "docker-compose.yml").read_text(encoding="utf-8") if (root / "docker-compose.yml").is_file() else ""
    )
    checks = [
        {
            "name": "data_stack:postgres_driver",
            "ok": "psycopg" in optional,
        },
        {
            "name": "data_stack:pgvector_driver",
            "ok": "pgvector" in optional and "CREATE EXTENSION IF NOT EXISTS vector" in schema,
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
