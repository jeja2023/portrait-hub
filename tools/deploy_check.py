"""Static deployment checks for gpu-services."""

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

from tools.report_redaction import redact_for_report


@dataclass
class DeployReport:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: Any = None) -> None:
        self.checks.append({"name": name, "ok": ok, "detail": redact_for_report(detail)})

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.checks)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_required_files(root: Path, report: DeployReport) -> None:
    required = [
        "main.py",
        "Dockerfile",
        "docker-compose.yml",
        "requirements.txt",
        "requirements-prod-optional.txt",
        "models.yml",
        "model-capabilities.yml",
        "app/server.py",
        "app/routes.py",
        "app/runtime.py",
        "app/inference.py",
        "app/vision.py",
        "app/portrait_postgres.py",
        "app/portrait_tracking.py",
        "tools/validate_model_package.py",
        "tools/service_smoke_test.py",
        "tools/regression_check.py",
        "tools/worker_control.py",
        "tools/portrait_production_readiness.py",
        "tools/portrait_algorithm_eval.py",
        "tools/portrait_model_regression.py",
        "tools/portrait_cutover_check.py",
        "tools/portrait_stream_worker_health.py",
        "tools/portrait_postgres_schema.sql",
        "tools/qdrant_collections.json",
        "examples/portrait-model-regression.example.yml",
        "examples/production-models.example.yml",
        "examples/production-model-capabilities.example.yml",
        "deploy/portrait-stream-worker.service",
        "deploy/k8s-stream-worker.yaml",
        ".github/workflows/ci.yml",
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
    compose = yaml.safe_load(read_text(root / "docker-compose.yml")) or {}
    services = compose.get("services", {}) if isinstance(compose, dict) else {}
    service_names = sorted(services) if isinstance(services, dict) else []
    report.add("dockerfile_copies_app", "COPY app /workspace/app" in dockerfile, None)
    report.add("dockerfile_copies_main", "COPY main.py /workspace/main.py" in dockerfile, None)
    report.add("dockerfile_copies_capabilities", "COPY model-capabilities.yml /workspace/model-capabilities.yml" in dockerfile, None)
    report.add("dockerfile_copies_prod_optional", "COPY requirements-prod-optional.txt" in dockerfile, None)
    report.add("dockerfile_prod_optional_arg", "INSTALL_PROD_OPTIONAL" in dockerfile, None)
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
                "python -m pytest -q",
                "node tests/test_node_sdk.js",
                "python tools/deploy_check.py --json",
                "python tools/portrait_model_regression.py",
            ]
        ),
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

        paths = {route.path for route in main.app.routes}
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
    optional = read_text(root / "requirements-prod-optional.txt")
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
    check_models_config(root, report)
    check_docker_files(root, report)
    check_ci_workflows(root, report)
    check_production_integrations(root, report)
    check_node_sdk_tests(root, report)
    if args.import_app:
        check_import_app(root, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run static deployment checks for gpu-services.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--import-app", action="store_true", help="Import main.app and verify key routes.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
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
