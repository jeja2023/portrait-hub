"""gpu-services 静态部署校验脚本。"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.report_redaction import redact_for_report  # noqa: E402


@dataclass
class DeployReport:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: Any = None) -> None:
        self.checks.append({"name": name, "ok": ok, "detail": redact_for_report(detail)})

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.checks)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")

SOURCE_ENCODING_ROOTS = ("app", "tools", "sdk", "tests", "frontend", ".github", "deploy", "docs", "ops", "requirements", "package.json")
SOURCE_ENCODING_SUFFIXES = (".bat", ".css", ".html", ".in", ".js", ".json", ".lock", ".md", ".py", ".sql", ".txt", ".yaml", ".yml", ".example")


def source_files_for_encoding(root: Path) -> list[Path]:
    files: list[Path] = []
    for root_name in SOURCE_ENCODING_ROOTS:
        path = root / root_name
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = list(path.rglob("*"))
        else:
            continue
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() in SOURCE_ENCODING_SUFFIXES:
                files.append(candidate)
    return sorted(dict.fromkeys(files))


def check_source_encoding(root: Path, report: DeployReport) -> None:
    bom_files: list[str] = []
    source_files = source_files_for_encoding(root)
    for path in source_files:
        try:
            prefix = path.read_bytes()[:3]
        except OSError as exc:
            bom_files.append(f"{path.relative_to(root)}: read failed: {exc.__class__.__name__}")
            continue
        if prefix == b"\xef\xbb\xbf":
            bom_files.append(str(path.relative_to(root)).replace("\\", "/"))
    report.add("source_files_utf8_no_bom", not bom_files, {"bom_files": bom_files, "file_count": len(source_files)})


def check_required_files(root: Path, report: DeployReport) -> None:
    required = [
        "main.py",
        "Dockerfile",
        "Dockerfile.cpu",
        "docker-compose.yml",
        "docker-compose.cpu.yml",
        "requirements.txt",
        "package.json",
        "requirements/prod-optional.txt",
        "requirements/dev.txt",
        "requirements/base.in",
        "requirements/base.lock",
        "requirements.lock",
        "requirements-cpu.txt",
        "requirements-cpu.lock",
        "models.yml",
        "model-capabilities.yml",
        "app/server.py",
        "app/routes.py",
        "app/runtime.py",
        "app/inference.py",
        "app/vision.py",
        "app/portrait_postgres.py",
        "app/portrait_tracking.py",
        "app/runtime_face.py",
        "app/runtime_body.py",
        "app/runtime_pose.py",
        "app/runtime_gait.py",
        "app/runtime_appearance.py",
        "app/runtime_common.py",
        "app/tracking_state.py",
        "app/tracking_association.py",
        "app/portrait_errors.py",
        "app/portrait_runtime_store.py",
        "app/portrait_gallery_orchestration.py",
        "app/production_gates.py",
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
        "tools/validate_model_package.py",
        "tools/service_smoke_test.py",
        "tools/regression_check.py",
        "tools/worker_control.py",
        "tools/portrait_production_readiness.py",
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
        "deploy/portrait-stream-worker.service",
        "deploy/k8s-stream-worker.yaml",
        "deploy/portrait-governance-scheduler.service",
        "deploy/portrait-governance-scheduler.timer",
        "deploy/k8s-governance-cronjob.yaml",
        ".github/workflows/ci.yml",
        ".github/workflows/integration-matrix.yml",
        ".github/workflows/console-acceptance.yml",
        ".github/workflows/security-audit.yml",
    ]
    missing = [item for item in required if not (root / item).is_file()]
    report.add("required_files", not missing, {"missing": missing})


def check_python_syntax(root: Path, report: DeployReport) -> None:
    errors = []
    for path in [root / "main.py", *sorted((root / "app").glob("*.py")), *sorted((root / "tools").glob("*.py"))]:
        try:
            ast.parse(read_text(path), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path}: {exc}")
    report.add("python_syntax", not errors, {"errors": errors, "file_count": len(list((root / "app").glob("*.py"))) + len(list((root / "tools").glob("*.py"))) + 1})


def check_code_quality(root: Path, report: DeployReport) -> None:
    core = read_text(root / "app" / "core.py")
    route_modules = "\n".join(read_text(path) for path in sorted((root / "app").glob("routes*.py")))
    pyproject = read_text(root / "pyproject.toml")
    dev_requirements = read_text(root / "requirements" / "dev.txt")
    ci = read_text(root / ".github" / "workflows" / "ci.yml")
    type_check = read_text(root / "tools" / "type_check.py")
    portrait_tracking = read_text(root / "app" / "portrait_tracking.py")
    tracking_state = read_text(root / "app" / "tracking_state.py")
    tracking_association = read_text(root / "app" / "tracking_association.py")
    server = read_text(root / "app" / "server.py")
    observability = read_text(root / "app" / "observability.py")
    runtime_execution = read_text(root / "app" / "runtime_execution.py")
    runtime_face = read_text(root / "app" / "runtime_face.py")
    runtime_body = read_text(root / "app" / "runtime_body.py")
    runtime_pose = read_text(root / "app" / "runtime_pose.py")
    runtime_gait = read_text(root / "app" / "runtime_gait.py")
    runtime_appearance = read_text(root / "app" / "runtime_appearance.py")
    vector_store = read_text(root / "app" / "portrait_vector_store.py")
    postgres_core = read_text(root / "app" / "postgres_core.py")
    config_hot_reload = read_text(root / "app" / "config_hot_reload.py")
    websocket_routes = read_text(root / "app" / "routes_portrait_ws.py")
    console_html = read_text(root / "frontend" / "console" / "console.html")
    console_js = read_text(root / "frontend" / "console" / "console.js")
    console_config_js = read_text(root / "frontend" / "console" / "console.config.js")
    console_runtime_js = read_text(root / "frontend" / "console" / "views" / "app.js")
    console_module_sources = "\n".join(
        read_text(root / "frontend" / "console" / item)
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
    )
    production_gates = read_text(root / "app" / "production_gates.py")
    report.add(
        "core_explicit_imports",
        "import *" not in core and "__all__" in core,
        None,
    )
    report.add(
        "routes_do_not_wildcard_import_core",
        "from app.core import *" not in route_modules,
        None,
    )
    report.add(
        "strict_type_check_gate",
        "[tool.mypy]" in pyproject
        and "strict = true" in pyproject
        and 'requires-python = ">=3.12"' in pyproject
        and "mypy==" in dev_requirements
        and "python tools/type_check.py" in ci
        and "discover_default_targets" in type_check
        and "DEFAULT_TARGET_ROOTS" in type_check
        and "--fallback-ok" in type_check
        and "mypy is not installed; install requirements/dev.txt" in type_check,
        None,
    )
    report.add(
        "expanded_type_check_targets",
        all(item in type_check for item in ["\"app\"", "\"tools\"", "\"sdk\"", "main.py"])
        and "rglob(\"*.py\")" in type_check,
        None,
    )
    report.add(
        "tracking_split_is_real",
        "from app.tracking_association import" in portrait_tracking
        and "from app.tracking_state import" in portrait_tracking
        and "from app.portrait_tracking import" not in tracking_state
        and "from app.portrait_tracking import" not in tracking_association
        and "def associate_person_tracks" in tracking_association
        and "class TrackState" in tracking_state,
        None,
    )
    report.add(
        "runtime_split_is_real",
        "from app.portrait_model_runtime import" not in runtime_face
        and "from app.portrait_model_runtime import" not in runtime_body
        and "from app.portrait_model_runtime import" not in runtime_pose
        and "from app.portrait_model_runtime import" not in runtime_gait
        and "from app.portrait_model_runtime import" not in runtime_appearance
        and "def run_scrfd_face_detection" in runtime_face
        and "def run_reid_body_embedding" in runtime_body
        and "def run_rtmpose" in runtime_pose
        and "def run_opengait" in runtime_gait
        and "def run_attribute_reid_appearance" in runtime_appearance,
        None,
    )
    report.add(
        "config_sighup_hot_reload",
        "def install_config_reload_signal_handler" in server
        and 'getattr(signal, "SIGHUP"' in server
        and "reload_runtime_config(source=\"sighup\"" in server
        and "ENV_PATH" in server
        and "def reload_runtime_config" in config_hot_reload
        and "audit_event(" in config_hot_reload,
        None,
    )
    report.add(
        "explicit_opentelemetry_spans",
        "def trace_span" in observability
        and "portrait.inference.run_session" in runtime_execution
        and "portrait.vector.pgvector.search" in vector_store
        and "portrait.vector.qdrant.search" in vector_store
        and "portrait.postgres.connection" in postgres_core,
        None,
    )
    report.add(
        "websocket_auth_and_console",
        "def require_websocket_permission" in websocket_routes
        and "status.WS_1008_POLICY_VIOLATION" in websocket_routes
        and "except HTTPException" in websocket_routes
        and '"jobs:read"' in websocket_routes
        and '"streams:read"' in websocket_routes
        and "new WebSocket" in console_runtime_js
        and "/ws/jobs/" in console_runtime_js
        and "/ws/streams/" in console_runtime_js
        and "window.PortraitConsoleConfig" in console_config_js
        and "endpointMap" in console_config_js
        and "const endpointMap = consoleConfig.endpointMap" in console_runtime_js
        and "PortraitConsoleRuntime" in console_js
        and "runtime.init" in console_js
        and len(console_js) < 2000
        and "window.PortraitConsoleRuntime = { init }" in console_runtime_js,
        None,
    )


def requirement_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def has_version_range(line: str) -> bool:
    if line.startswith("-"):
        return False
    return any(operator in line for operator in [">=", "<=", "~=", ">", "<", "!="])


def onnxruntime_version(text: str, package: str) -> str | None:
    """从某个 requirements 文本中提取 `<package>==<version>` 的固定版本号（无则返回 None）。"""
    for line in requirement_lines(text):
        name, _, version = line.partition("==")
        if name.strip() == package and version.strip():
            return version.strip()
    return None


def check_dependency_lock(root: Path, report: DeployReport) -> None:
    requirements = read_text(root / "requirements.txt")
    base_in = read_text(root / "requirements" / "base.in")
    base_lock = read_text(root / "requirements" / "base.lock")
    requirements_lock = read_text(root / "requirements.lock")
    requirements_cpu = read_text(root / "requirements-cpu.txt")
    requirements_cpu_lock = read_text(root / "requirements-cpu.lock")
    lock_ranges = [line for line in requirement_lines(base_lock) if has_version_range(line) or "==" not in line]
    root_lock_ranges = [line for line in requirement_lines(requirements_lock) if has_version_range(line) or "==" not in line]
    runtime_ranges = [line for line in requirement_lines(requirements) if has_version_range(line) or "==" not in line]
    cpu_ranges = [line for line in requirement_lines(requirements_cpu) if has_version_range(line) or "==" not in line]
    cpu_lock_ranges = [line for line in requirement_lines(requirements_cpu_lock) if has_version_range(line) or "==" not in line]
    report.add(
        "dependency_lock_exact",
        not lock_ranges
        and not root_lock_ranges
        and not runtime_ranges
        and "cryptography==45.0.6" in base_lock
        and "cryptography==45.0.6" in requirements_lock
        and "cryptography==45.0.6" in requirements,
        {
            "base_lock_ranges": lock_ranges,
            "requirements_lock_ranges": root_lock_ranges,
            "requirements_ranges": runtime_ranges,
        },
    )
    report.add(
        "dependency_input_keeps_compatibility_range",
        "cryptography>=42.0.0,<46.0.0" in base_in,
        None,
    )
    # CPU-only 部署清单与其锁文件同样必须全量精确钉版（无任何版本范围）。
    report.add(
        "cpu_dependency_lock_exact",
        not cpu_ranges
        and not cpu_lock_ranges
        and "cryptography==45.0.6" in requirements_cpu
        and "cryptography==45.0.6" in requirements_cpu_lock,
        {
            "requirements_cpu_ranges": cpu_ranges,
            "requirements_cpu_lock_ranges": cpu_lock_ranges,
        },
    )
    # CPU 与 GPU 运行时必须显式区分（onnxruntime vs onnxruntime-gpu），且锁定在同一版本，
    # 避免两套部署的推理内核版本漂移。同时 CPU 清单不得混入 GPU 包，反之亦然。
    gpu_version = onnxruntime_version(requirements, "onnxruntime-gpu")
    gpu_lock_version = onnxruntime_version(requirements_lock, "onnxruntime-gpu")
    cpu_version = onnxruntime_version(requirements_cpu, "onnxruntime")
    cpu_lock_version = onnxruntime_version(requirements_cpu_lock, "onnxruntime")
    versions = {gpu_version, gpu_lock_version, cpu_version, cpu_lock_version}
    report.add(
        "cpu_gpu_runtime_parity",
        None not in versions
        and len(versions) == 1
        and "onnxruntime-gpu==" not in requirements_cpu
        and "onnxruntime-gpu==" not in requirements_cpu_lock
        and onnxruntime_version(requirements, "onnxruntime") is None,
        {
            "gpu_runtime": gpu_version,
            "gpu_lock_runtime": gpu_lock_version,
            "cpu_runtime": cpu_version,
            "cpu_lock_runtime": cpu_lock_version,
        },
    )


def check_models_config(root: Path, report: DeployReport) -> None:
    path = root / "models.yml"
    try:
        raw = yaml.safe_load(read_text(path)) or {}
    except Exception as exc:
        report.add("models_yml_parse", False, str(exc))
        return
    if not isinstance(raw, dict):
        report.add("models_yml_parse", False, "root must be a mapping")
        return
    models = raw.get("models", raw)
    aliases = raw.get("aliases", {})
    model_ok = isinstance(models, dict) and bool(models)
    alias_ok = isinstance(aliases, dict)
    report.add("models_yml_models", model_ok, {"model_count": len(models) if isinstance(models, dict) else 0})
    report.add("models_yml_aliases", alias_ok, {"alias_count": len(aliases) if isinstance(aliases, dict) else 0})
    if isinstance(models, dict):
        missing_task = [str(key) for key, value in models.items() if isinstance(value, dict) and not (value.get("task") or value.get("type"))]
        report.add("models_yml_task_fields", not missing_task, {"missing_task": missing_task})


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
    report.add("dockerfile_copies_frontend", "COPY frontend /workspace/frontend" in dockerfile, None)
    report.add("dockerfile_copies_main", "COPY main.py /workspace/main.py" in dockerfile, None)
    report.add("dockerfile_copies_capabilities", "COPY model-capabilities.yml /workspace/model-capabilities.yml" in dockerfile, None)
    report.add("dockerfile_copies_prod_optional", "COPY requirements/prod-optional.txt" in dockerfile, None)
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
        and stream_worker.get("healthcheck", {}).get("disable") is True,
        {"service": stream_worker},
    )
    gpu_like = [
        name
        for name, service in services.items()
        if isinstance(service, dict)
        and ("NVIDIA_VISIBLE_DEVICES" in str(service.get("environment", "")) or "gpus" in service)
    ] if isinstance(services, dict) else []
    report.add("compose_gpu_configuration", bool(gpu_like), {"gpu_services": gpu_like})
    cpu_env_text = json.dumps(
        [service.get("environment", {}) for service in cpu_services.values() if isinstance(service, dict)],
        ensure_ascii=False,
    )
    cpu_has_gpu_reservation = any(
        isinstance(service, dict)
        and (
            "deploy" in service
            or "gpus" in service
            or "driver: nvidia" in str(yaml.safe_dump(service, sort_keys=True))
            or "capabilities: [gpu]" in str(yaml.safe_dump(service, sort_keys=True))
        )
        for service in cpu_services.values()
    ) if isinstance(cpu_services, dict) else True
    report.add(
        "cpu_compose_services",
        cpu_service_names == ["cpu-worker-0", "portrait-stream-worker"],
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
        not cpu_has_gpu_reservation
        and '"NVIDIA_VISIBLE_DEVICES": "none"' in cpu_env_text,
        {"services": cpu_service_names},
    )
    report.add(
        "cpu_compose_uses_cpu_dockerfile",
        all(isinstance(service, dict) and service.get("build", {}).get("dockerfile") == "Dockerfile.cpu" for service in cpu_services.values())
        if isinstance(cpu_services, dict) and cpu_services
        else False,
        {"services": cpu_service_names},
    )
    volumes = [
        volume
        for service in services.values()
        if isinstance(service, dict)
        for volume in service.get("volumes", [])
    ] if isinstance(services, dict) else []
    volume_targets = [
        str(volume.get("target"))
        if isinstance(volume, dict)
        else str(volume).split(":")[1] if ":" in str(volume) else str(volume)
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
        "TRUSTED_HOSTS: ${TRUSTED_HOSTS:-127.0.0.1,localhost,gpu-worker-0,gpu-worker-1}" in read_text(root / "docker-compose.yml"),
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
        "MAX_REQUEST_BODY_BYTES: ${MAX_REQUEST_BODY_BYTES:-805306368}" in read_text(root / "docker-compose.yml"),
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
        "MODEL_CONFIG_READ_FAIL_CLOSED: ${MODEL_CONFIG_READ_FAIL_CLOSED:-true}" in read_text(root / "docker-compose.yml"),
        None,
    )
    ready_healthchecks = [
        name
        for name, service in services.items()
        if isinstance(service, dict) and "/ready" in str(service.get("healthcheck", ""))
    ] if isinstance(services, dict) else []
    report.add("compose_ready_healthcheck", bool(ready_healthchecks), {"services": ready_healthchecks})


def check_ci_workflows(root: Path, report: DeployReport) -> None:
    ci_path = root / ".github" / "workflows" / "ci.yml"
    audit_path = root / ".github" / "workflows" / "security-audit.yml"
    ci = read_text(ci_path) if ci_path.is_file() else ""
    audit = read_text(audit_path) if audit_path.is_file() else ""
    report.add(
        "ci_python_node_deploy_checks",
        all(
            item in ci
            for item in [
                'python-version: "3.12"',
                "python -m pytest -q",
                "python tools/deploy_check.py --json",
                "python tools/portrait_model_regression.py",
            ]
        )
        and ("node tests/test_node_sdk.js" in ci or "npm run check" in ci),
        {"path": str(ci_path)},
    )
    report.add(
        "ci_security_audit_scheduled",
        "pip-audit" in audit and "python tools/security_audit.py" in audit and "cron:" in audit,
        {"path": str(audit_path)},
    )


def check_import_app(root: Path, report: DeployReport) -> None:
    try:
        sys.path.insert(0, str(root))
        import main

        paths = {path for route in main.app.routes if isinstance((path := getattr(route, "path", None)), str)}
        required = {
            "/health",
            "/ready",
            "/models",
            "/predict",
            "/vision/infer",
            "/vision/batch-infer",
            "/rollout/aliases",
            "/rollout/aliases/preview",
            "/rollout/aliases/switch",
            "/rollout/aliases/weighted",
            "/rollout/aliases/rollback",
        }
        missing = sorted(required - paths)
        report.add("app_import", True, {"route_count": len(paths)})
        report.add("app_required_routes", not missing, {"missing": missing})
    except Exception as exc:
        report.add("app_import", False, str(exc))


def check_production_integrations(root: Path, report: DeployReport) -> None:
    optional = read_text(root / "requirements" / "prod-optional.txt")
    required_packages = ["psycopg", "pgvector", "qdrant-client", "boto3", "redis"]
    missing_packages = [item for item in required_packages if item not in optional]
    report.add("prod_optional_dependencies", not missing_packages, {"missing": missing_packages})

    schema = read_text(root / "tools" / "portrait_postgres_schema.sql")
    schema_required = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "embedding_vector vector",
        "portrait_thresholds",
        "portrait_objects",
        "portrait_stream_events",
        "portrait_task_messages",
        "object_info JSONB",
        "audit_hash TEXT NOT NULL",
        "audit_prev_hash TEXT",
    ]
    missing_schema = [item for item in schema_required if item not in schema]
    report.add("postgres_pgvector_schema", not missing_schema, {"missing": missing_schema})


def check_node_sdk_tests(root: Path, report: DeployReport) -> None:
    test_path = root / "tests" / "test_node_sdk.js"
    if not test_path.is_file():
        report.add("node_sdk_contract_tests", False, {"missing": str(test_path)})
        return
    node = shutil.which("node")
    if not node:
        report.add("node_sdk_contract_tests", True, {"skipped": "node executable not found"})
        return
    completed = subprocess.run(
        [node, str(test_path)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    report.add(
        "node_sdk_contract_tests",
        completed.returncode == 0,
        {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        },
    )


def run_checks(args: argparse.Namespace) -> DeployReport:
    root = Path(args.root).resolve()
    report = DeployReport()
    check_required_files(root, report)
    check_python_syntax(root, report)
    check_source_encoding(root, report)
    check_code_quality(root, report)
    check_dependency_lock(root, report)
    check_models_config(root, report)
    check_docker_files(root, report)
    check_ci_workflows(root, report)
    check_production_integrations(root, report)
    if not args.skip_node:
        check_node_sdk_tests(root, report)
    if args.import_app:
        check_import_app(root, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run static deployment checks for gpu-services.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--import-app", action="store_true", help="Import main.app and verify key routes.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--skip-node", action="store_true", help="Skip Node.js contract checks.")
    args = parser.parse_args()

    report = run_checks(args)
    output = {"ok": report.ok, "checks": report.checks}
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"deploy check: {'OK' if report.ok else 'FAILED'}")
        for item in report.checks:
            marker = "ok" if item["ok"] else "fail"
            print(f"{marker}: {item['name']}")
            if not item["ok"]:
                print(f"  detail: {item['detail']}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
