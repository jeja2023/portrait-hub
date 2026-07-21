"""Dockerfile and Compose deployment checks."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tools.deploy_checks.common import DeployReport, read_text


def check_docker_files(root: Path, report: DeployReport) -> None:
    dockerfile = read_text(root / "Dockerfile")
    cpu_dockerfile = read_text(root / "Dockerfile.cpu")
    compose = yaml.safe_load(read_text(root / "docker-compose.yml")) or {}
    cpu_compose_text = read_text(root / "docker-compose.cpu.yml")
    cpu_compose = yaml.safe_load(cpu_compose_text) or {}
    services = compose.get("services", {}) if isinstance(compose, dict) else {}
    cpu_services = cpu_compose.get("services", {}) if isinstance(cpu_compose, dict) else {}
    service_names = sorted(services) if isinstance(services, dict) else []
    cpu_service_names = sorted(cpu_services) if isinstance(cpu_services, dict) else []
    report.add("dockerfile_copies_app", "COPY app /workspace/app" in dockerfile, None)
    report.add(
        "dockerfile_copies_frontend",
        all(
            marker in dockerfile and marker in cpu_dockerfile
            for marker in [
                "FROM node:22.14.0-bookworm-slim AS console-builder",
                "RUN npm ci",
                "RUN npm run console:build",
                "COPY --from=console-builder /build/frontend/console-next/dist /workspace/frontend/console-next/dist",
            ]
        ),
        None,
    )
    report.add("dockerfile_copies_main", "COPY main.py /workspace/main.py" in dockerfile, None)
    report.add(
        "dockerfile_copies_capabilities",
        "COPY model-capabilities.yml /workspace/model-capabilities.yml" in dockerfile,
        None,
    )
    report.add(
        "dockerfile_copies_prod_optional",
        "COPY requirements/prod-optional.txt" in dockerfile,
        None,
    )
    report.add("dockerfile_prod_optional_arg", "INSTALL_PROD_OPTIONAL" in dockerfile, None)
    report.add(
        "cpu_dockerfile_uses_cpu_runtime",
        "FROM python:3.12-slim-bookworm" in cpu_dockerfile
        and "requirements-cpu.txt" in cpu_dockerfile
        and "FORCE_CPU=true" in cpu_dockerfile
        and "nvidia/cuda" not in cpu_dockerfile,
        None,
    )
    report.add("compose_services", bool(service_names), {"services": service_names})
    stream_worker = services.get("portrait-stream-worker") if isinstance(services, dict) else None
    report.add(
        "compose_stream_worker_service",
        isinstance(stream_worker, dict)
        and "app.portrait_stream_worker_daemon" in str(stream_worker.get("command", ""))
        and stream_worker.get("healthcheck", {}).get("disable") is True
        and stream_worker.get("runtime") == "nvidia"
        and str(stream_worker.get("environment", {}).get("FORCE_CPU", "")).lower()
        == "${stream_worker_force_cpu:-true}",
        {"service": stream_worker},
    )
    video_job_worker = services.get("portrait-video-job-worker") if isinstance(services, dict) else None
    report.add(
        "compose_video_job_worker_service",
        isinstance(video_job_worker, dict)
        and "app.portrait_video_job_worker" in str(video_job_worker.get("command", ""))
        and str(video_job_worker.get("environment", {}).get("VIDEO_JOB_WORKER_IN_PROCESS", "")).lower() == "false"
        and video_job_worker.get("runtime") == "nvidia",
        {"service": video_job_worker},
    )
    gpu_like = (
        [
            name
            for name, service in services.items()
            if isinstance(service, dict)
            and ("NVIDIA_VISIBLE_DEVICES" in str(service.get("environment", "")) or "gpus" in service)
        ]
        if isinstance(services, dict)
        else []
    )
    report.add("compose_gpu_configuration", bool(gpu_like), {"gpu_services": gpu_like})
    cpu_env_text = json.dumps(
        [service.get("environment", {}) for service in cpu_services.values() if isinstance(service, dict)],
        ensure_ascii=False,
    )
    cpu_has_gpu_reservation = (
        any(
            isinstance(service, dict)
            and (
                "deploy" in service
                or "gpus" in service
                or "driver: nvidia" in str(yaml.safe_dump(service, sort_keys=True))
                or "capabilities: [gpu]" in str(yaml.safe_dump(service, sort_keys=True))
            )
            for service in cpu_services.values()
        )
        if isinstance(cpu_services, dict)
        else True
    )
    report.add(
        "cpu_compose_services",
        cpu_service_names == ["cpu-worker-0", "portrait-stream-worker", "portrait-video-job-worker"],
        {"services": cpu_service_names},
    )
    report.add(
        "cpu_compose_force_cpu_is_literal",
        'FORCE_CPU: "true"' in cpu_compose_text
        and "FORCE_CPU: ${FORCE_CPU" not in cpu_compose_text
        and '"FORCE_CPU": "true"' in cpu_env_text,
        {"environment": cpu_env_text},
    )
    report.add(
        "cpu_compose_trusted_hosts_isolated",
        "CPU_TRUSTED_HOSTS" in cpu_compose_text
        and "cpu-worker-0" in cpu_compose_text
        and "gpu-worker-0" not in cpu_compose_text
        and "gpu-worker-1" not in cpu_compose_text,
        None,
    )
    report.add(
        "cpu_compose_has_no_gpu_reservation",
        not cpu_has_gpu_reservation and '"NVIDIA_VISIBLE_DEVICES": "none"' in cpu_env_text,
        {"services": cpu_service_names},
    )
    report.add(
        "cpu_compose_uses_cpu_dockerfile",
        all(
            isinstance(service, dict) and service.get("build", {}).get("dockerfile") == "Dockerfile.cpu"
            for service in cpu_services.values()
        )
        if isinstance(cpu_services, dict) and cpu_services
        else False,
        {"services": cpu_service_names},
    )
    volumes = (
        [volume for service in services.values() if isinstance(service, dict) for volume in service.get("volumes", [])]
        if isinstance(services, dict)
        else []
    )
    volume_targets = [
        str(volume.get("target"))
        if isinstance(volume, dict)
        else str(volume).split(":")[1]
        if ":" in str(volume)
        else str(volume)
        for volume in volumes
    ]
    report.add(
        "compose_model_config_mount",
        "/workspace/models.yml" in volume_targets,
        {"volumes": volumes},
    )
    report.add(
        "compose_model_config_no_autocreate",
        any(
            isinstance(volume, dict)
            and volume.get("target") == "/workspace/models.yml"
            and isinstance(volume.get("bind"), dict)
            and volume["bind"].get("create_host_path") is False
            for volume in volumes
        ),
        {"volumes": volumes},
    )
    model_config_sources = [
        str(volume.get("source"))
        for volume in volumes
        if isinstance(volume, dict) and volume.get("target") == "/workspace/models.yml"
    ]
    cpu_model_config_sources = [
        str(volume.get("source"))
        for service in cpu_services.values()
        if isinstance(service, dict)
        for volume in service.get("volumes", [])
        if isinstance(volume, dict) and volume.get("target") == "/workspace/models.yml"
    ]
    report.add(
        "compose_model_config_outside_git_worktree",
        bool(model_config_sources)
        and bool(cpu_model_config_sources)
        and all("runtime-state/models.yml" in source for source in model_config_sources + cpu_model_config_sources)
        and "MODEL_CONFIG_HOST_FILE=./runtime-state/models.yml" in read_text(root / ".env.example"),
        {
            "gpu_sources": model_config_sources,
            "cpu_sources": cpu_model_config_sources,
        },
    )
    report.add(
        "compose_runtime_state_mount",
        "/workspace/runtime-state" in volume_targets,
        {"volumes": volumes},
    )
    env_text = json.dumps(
        [service.get("environment", {}) for service in services.values() if isinstance(service, dict)],
        ensure_ascii=False,
    )
    report.add(
        "compose_production_backend_env",
        all(
            item in env_text
            for item in [
                "POSTGRES_DSN",
                "POSTGRES_CONNECT_TIMEOUT_SECONDS",
                "S3_REGION",
                "REDIS_URL",
                "RATE_LIMIT_PER_MINUTE",
                "RATE_LIMIT_BURST",
                "RATE_LIMIT_MAX_BUCKETS",
                "RATE_LIMIT_BUCKET_TTL_SECONDS",
                "MAX_REQUEST_BODY_BYTES",
                "CONTENT_SECURITY_POLICY",
                "HSTS_ENABLED",
                "HSTS_MAX_AGE_SECONDS",
                "HSTS_INCLUDE_SUBDOMAINS",
                "HSTS_PRELOAD",
                "STATE_READ_FAIL_CLOSED",
                "STATE_WRITE_FAIL_CLOSED",
                "PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES",
                "MODEL_CONFIG_READ_FAIL_CLOSED",
                "AUDIT_WRITE_FAIL_CLOSED",
                "PORTRAIT_JOBS_STATE_PATH",
                "PORTRAIT_STREAMS_STATE_PATH",
                "MAX_AUDIT_PAYLOAD_BYTES",
                "MAX_AUDIT_DEPTH",
                "MAX_AUDIT_KEYS",
                "MAX_AUDIT_LIST_ITEMS",
                "MAX_AUDIT_STRING_LENGTH",
                "API_LIST_DEFAULT_LIMIT",
                "MAX_API_LIST_LIMIT",
                "STREAM_EVENT_LIST_DEFAULT_LIMIT",
                "MAX_STREAM_EVENT_LIST_LIMIT",
            ]
        ),
        None,
    )
    report.add(
        "compose_security_env",
        all(
            item in env_text
            for item in [
                "AUTH_REQUIRED",
                "DEBUG_ENDPOINTS_ENABLED",
                "ENABLE_API_DOCS",
                "TRUSTED_HOSTS",
                "TENANT_HEADER_REQUIRED",
                "API_TOKEN",
                "RBAC_ENABLED",
                "JWT_SECRET",
                "JWT_SECRET_ID",
                "JWT_SECRET_KEYRING",
                "JWT_AUDIENCE",
                "JWT_REQUIRE_EXP",
                "JWT_REQUIRE_ISS",
                "JWT_REQUIRE_AUD",
                "JWT_REQUIRE_TENANT",
                "ENCRYPTION_KEY",
                "ENCRYPTION_KEY_ID",
                "ENCRYPTION_KEYRING",
                "REQUIRE_ENCRYPTION",
            ]
        ),
        None,
    )
    report.add(
        "compose_local_auth_env",
        all(
            item in env_text
            for item in [
                "LOCAL_AUTH_ENABLED",
                "LOCAL_AUTH_ALLOW_REMOTE",
                "LOCAL_AUTH_USERNAME",
                "LOCAL_AUTH_PASSWORD",
                "LOCAL_AUTH_TENANT_ID",
                "LOCAL_AUTH_SESSION_SECRET",
                "LOCAL_AUTH_COOKIE_SECURE",
            ]
        )
        and all(
            item in cpu_env_text
            for item in [
                "LOCAL_AUTH_ENABLED",
                "LOCAL_AUTH_ALLOW_REMOTE",
                "LOCAL_AUTH_USERNAME",
                "LOCAL_AUTH_PASSWORD",
                "LOCAL_AUTH_TENANT_ID",
                "LOCAL_AUTH_SESSION_SECRET",
                "LOCAL_AUTH_COOKIE_SECURE",
            ]
        ),
        None,
    )
    report.add(
        "compose_auth_required_default",
        "AUTH_REQUIRED: ${AUTH_REQUIRED:-true}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_debug_disabled_default",
        "DEBUG_ENDPOINTS_ENABLED: ${DEBUG_ENDPOINTS_ENABLED:-false}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_api_docs_disabled_default",
        "ENABLE_API_DOCS: ${ENABLE_API_DOCS:-false}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_trusted_hosts_default",
        "TRUSTED_HOSTS: ${TRUSTED_HOSTS:-127.0.0.1,localhost,gpu-worker-0,gpu-worker-1}"
        in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_rate_limit_enabled_default",
        "RATE_LIMIT_PER_MINUTE: ${RATE_LIMIT_PER_MINUTE:-120}" in read_text(root / "docker-compose.yml")
        and "RATE_LIMIT_BURST: ${RATE_LIMIT_BURST:-240}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_request_body_limit_default",
        "MAX_REQUEST_BODY_BYTES: ${MAX_REQUEST_BODY_BYTES:-117440512}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_security_headers_defaults",
        all(
            item in read_text(root / "docker-compose.yml")
            for item in [
                "SECURITY_HEADERS_ENABLED: ${SECURITY_HEADERS_ENABLED:-true}",
                "CONTENT_SECURITY_POLICY:",
                "HSTS_ENABLED: ${HSTS_ENABLED:-true}",
                "HSTS_MAX_AGE_SECONDS: ${HSTS_MAX_AGE_SECONDS:-31536000}",
                "HSTS_INCLUDE_SUBDOMAINS: ${HSTS_INCLUDE_SUBDOMAINS:-true}",
                "HSTS_PRELOAD: ${HSTS_PRELOAD:-false}",
            ]
        ),
        None,
    )
    report.add(
        "compose_tenant_header_required_default",
        "TENANT_HEADER_REQUIRED: ${TENANT_HEADER_REQUIRED:-true}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_jwt_claim_defaults",
        all(
            item in read_text(root / "docker-compose.yml")
            for item in [
                "JWT_AUDIENCE: ${JWT_AUDIENCE:-portrait-hub-api}",
                "JWT_SECRET_ID: ${JWT_SECRET_ID:-primary}",
                "JWT_SECRET_KEYRING: ${JWT_SECRET_KEYRING:-}",
                "JWT_REQUIRE_EXP: ${JWT_REQUIRE_EXP:-true}",
                "JWT_REQUIRE_ISS: ${JWT_REQUIRE_ISS:-true}",
                "JWT_REQUIRE_AUD: ${JWT_REQUIRE_AUD:-true}",
                "JWT_REQUIRE_TENANT: ${JWT_REQUIRE_TENANT:-true}",
            ]
        ),
        None,
    )
    report.add(
        "compose_require_encryption_default",
        "REQUIRE_ENCRYPTION: ${REQUIRE_ENCRYPTION:-true}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_audit_fail_closed_default",
        "AUDIT_WRITE_FAIL_CLOSED: ${AUDIT_WRITE_FAIL_CLOSED:-true}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_state_read_fail_closed_default",
        "STATE_READ_FAIL_CLOSED: ${STATE_READ_FAIL_CLOSED:-true}" in read_text(root / "docker-compose.yml"),
        None,
    )
    report.add(
        "compose_model_config_read_fail_closed_default",
        "MODEL_CONFIG_READ_FAIL_CLOSED: ${MODEL_CONFIG_READ_FAIL_CLOSED:-true}"
        in read_text(root / "docker-compose.yml"),
        None,
    )
    ready_healthchecks = (
        [
            name
            for name, service in services.items()
            if isinstance(service, dict) and "/ready" in str(service.get("healthcheck", ""))
        ]
        if isinstance(services, dict)
        else []
    )
    report.add(
        "compose_ready_healthcheck",
        bool(ready_healthchecks),
        {"services": ready_healthchecks},
    )
