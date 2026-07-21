"""PortraitHub 静态部署校验脚本。"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.deploy_checks.common import DeployReport, read_text  # noqa: E402
from tools.deploy_checks.containers import check_docker_files  # noqa: E402

SOURCE_ENCODING_ROOTS = (
    "app",
    "tools",
    "sdk",
    "tests",
    "frontend",
    ".github",
    "deploy",
    "docs",
    "ops",
    "requirements",
    "examples",
    "package.json",
)
SOURCE_ENCODING_SUFFIXES = (
    ".bat",
    ".css",
    ".html",
    ".in",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".sql",
    ".txt",
    ".yaml",
    ".yml",
    ".example",
)


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
            bom_files.append(f"{path.relative_to(root)}: 读取失败：{exc.__class__.__name__}")
            continue
        if prefix == b"\xef\xbb\xbf":
            bom_files.append(str(path.relative_to(root)).replace("\\", "/"))
    report.add(
        "source_files_utf8_no_bom",
        not bom_files,
        {"bom_files": bom_files, "file_count": len(source_files)},
    )


def check_required_files(root: Path, report: DeployReport) -> None:
    required = [
        "main.py",
        "Dockerfile",
        "Dockerfile.cpu",
        "docker-compose.yml",
        "docker-compose.cpu.yml",
        "requirements.txt",
        "package.json",
        "package-lock.json",
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
        "app/portrait_review.py",
        "app/portrait_gallery_orchestration.py",
        "app/production_gates.py",
        "app/portrait_video_job_worker.py",
        "app/rollout_audit.py",
        "frontend/console-next/package.json",
        "frontend/console-next/vite.config.ts",
        "frontend/console-next/src/main.ts",
        "frontend/console-next/src/auth/session.ts",
        "frontend/console-next/src/api/generated.ts",
        "frontend/console-next/dist/index.html",
        "frontend/console-next/dist/.vite/manifest.json",
        "tools/validate_model_package.py",
        "tools/service_smoke_test.py",
        "tools/regression_check.py",
        "tools/worker_control.py",
        "tools/deploy_check.py",
        "tools/deploy_checks/__init__.py",
        "tools/deploy_checks/common.py",
        "tools/deploy_checks/containers.py",
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
    ]
    missing = [item for item in required if not (root / item).is_file()]
    report.add("required_files", not missing, {"missing": missing})


def check_python_syntax(root: Path, report: DeployReport) -> None:
    errors = []
    for path in [
        root / "main.py",
        *sorted((root / "app").glob("*.py")),
        *sorted((root / "tools").glob("*.py")),
        *sorted((root / "examples").rglob("*.py")),
    ]:
        try:
            ast.parse(read_text(path), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path}: {exc}")
    report.add(
        "python_syntax",
        not errors,
        {
            "errors": errors,
            "file_count": len(list((root / "app").glob("*.py")))
            + len(list((root / "tools").glob("*.py")))
            + len(list((root / "examples").rglob("*.py")))
            + 1,
        },
    )


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
    portrait_console_routes = read_text(root / "app" / "routes_portrait_console.py")
    console_next_ws = read_text(root / "frontend" / "console-next" / "src" / "api" / "ws.ts")
    console_next_session = read_text(root / "frontend" / "console-next" / "src" / "auth" / "session.ts")

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
        and 'requires-python = "=3.12"' not in pyproject
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
        all(item in type_check for item in ['"app"', '"tools"', '"sdk"', "main.py"]) and 'rglob("*.py")' in type_check,
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
        and 'reload_runtime_config(source="sighup"' in server
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
        and "new WebSocket" in console_next_ws
        and '"/v1/console/ws-ticket"' in console_next_ws
        and "issued.websocket_path" in console_next_ws
        and "window.sessionStorage" in console_next_session
        and "window.localStorage" not in console_next_session
        and '"/assets/console-next/{asset_path:path}"' in portrait_console_routes
        and '"/console/legacy"' not in portrait_console_routes
        and "def next_console_csp" in portrait_console_routes
        and "script-src 'self'" in portrait_console_routes,
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
    root_lock_ranges = [
        line for line in requirement_lines(requirements_lock) if has_version_range(line) or "==" not in line
    ]
    runtime_ranges = [line for line in requirement_lines(requirements) if has_version_range(line) or "==" not in line]
    cpu_ranges = [line for line in requirement_lines(requirements_cpu) if has_version_range(line) or "==" not in line]
    cpu_lock_ranges = [
        line for line in requirement_lines(requirements_cpu_lock) if has_version_range(line) or "==" not in line
    ]
    report.add(
        "dependency_lock_exact",
        not lock_ranges
        and not root_lock_ranges
        and not runtime_ranges
        and "cryptography==48.0.1" in base_lock
        and "cryptography==48.0.1" in requirements_lock
        and "cryptography==48.0.1" in requirements,
        {
            "base_lock_ranges": lock_ranges,
            "requirements_lock_ranges": root_lock_ranges,
            "requirements_ranges": runtime_ranges,
        },
    )
    report.add(
        "dependency_input_keeps_compatibility_range",
        "cryptography>=48.0.1,<49.0.0" in base_in,
        None,
    )
    # CPU-only 部署清单与其锁文件同样必须全量精确钉版（无任何版本范围）。
    report.add(
        "cpu_dependency_lock_exact",
        not cpu_ranges
        and not cpu_lock_ranges
        and "cryptography==48.0.1" in requirements_cpu
        and "cryptography==48.0.1" in requirements_cpu_lock,
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
        report.add("models_yml_parse", False, "根节点必须是映射")
        return
    models = raw.get("models", raw)
    aliases = raw.get("aliases", {})
    model_ok = isinstance(models, dict) and bool(models)
    alias_ok = isinstance(aliases, dict)
    report.add(
        "models_yml_models",
        model_ok,
        {"model_count": len(models) if isinstance(models, dict) else 0},
    )
    report.add(
        "models_yml_aliases",
        alias_ok,
        {"alias_count": len(aliases) if isinstance(aliases, dict) else 0},
    )
    if isinstance(models, dict):
        missing_task = [
            str(key)
            for key, value in models.items()
            if isinstance(value, dict) and not (value.get("task") or value.get("type"))
        ]
        report.add("models_yml_task_fields", not missing_task, {"missing_task": missing_task})


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
                'node-version: "22"',
                "npm ci",
                "npm run console:generate",
                "git diff --exit-code -- frontend/console-next/src/api/generated.ts",
                "npm run console:lint",
                "npm run console:test",
                "npm run console:typecheck",
                "npm run console:build",
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

        paths = set(main.app.openapi().get("paths", {}))
        required = {
            "/health",
            "/ready",
            "/predict",
            "/v1/vision/infer",
            "/v1/infer/tracks",
            "/v1/jobs/video",
            "/v1/streams",
            "/v1/models",
            "/v1/admin/models/warmup",
            "/v1/admin/models/reload",
            "/v1/admin/models/reload-config",
            "/v1/admin/models/rollout/aliases",
            "/v1/admin/models/rollout/aliases/preview",
            "/v1/admin/models/rollout/aliases/switch",
            "/v1/admin/models/rollout/aliases/weighted",
            "/v1/admin/models/rollout/aliases/rollback",
            "/v1/evaluation/datasets",
            "/v1/evaluation/threshold-recommendations",
            "/v1/evaluation/track-reviews",
            "/v1/evaluation/track-reviews/summary",
            "/v1/access/error-codes",
            "/v1/admin/audit/verify",
            "/v1/admin/backups",
        }
        removed = {
            "/infer/stream/person-tracks",
            "/infer/persons",
            "/infer/person-embeddings",
            "/infer/person-tracks",
            "/infer/video/person-tracks",
            "/vision/infer",
            "/vision/batch-infer",
            "/models",
            "/model-configs",
            "/model-info",
            "/model-package",
            "/warmup",
            "/reload",
            "/unload",
            "/reload-config",
            "/rollout/aliases",
        }
        missing = sorted(required - paths)
        removed_routes_present = sorted(removed & paths)
        report.add("app_import", True, {"route_count": len(paths)})
        report.add("app_required_routes", not missing, {"missing": missing})
        report.add(
            "app_removed_routes",
            not removed_routes_present,
            {"present": removed_routes_present},
        )
        legacy_console_dir = root / "frontend" / "console"
        report.add(
            "legacy_console_source_removed",
            not legacy_console_dir.exists(),
            {"path": str(legacy_console_dir.relative_to(root))},
        )
        console_next_src_dir = root / "frontend" / "console-next" / "src"
        console_next_files = [
            path
            for path in sorted(console_next_src_dir.rglob("*"))
            if path.is_file() and path.suffix in {".ts", ".vue", ".css"}
        ]
        console_source = "\n".join(path.read_text(encoding="utf-8") for path in console_next_files)
        duplicate_v1_prefixes = sorted(set(re.findall(r"/v1(?:/[a-z0-9_-]+)*/v1/", console_source)))
        report.add(
            "console_no_duplicate_v1_prefixes",
            bool(console_next_files) and not duplicate_v1_prefixes,
            {"present": duplicate_v1_prefixes, "source_file_count": len(console_next_files)},
        )
    except Exception as exc:
        report.add("app_import", False, str(exc))


def check_production_integrations(root: Path, report: DeployReport) -> None:
    optional = read_text(root / "requirements" / "prod-optional.txt")
    required_packages = ["psycopg", "pgvector", "qdrant-client", "boto3", "redis"]
    missing_packages = [item for item in required_packages if item not in optional]
    report.add(
        "prod_optional_dependencies",
        not missing_packages,
        {"missing": missing_packages},
    )

    schema = read_text(root / "tools" / "portrait_postgres_schema.sql")
    schema_required = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "embedding_vector vector",
        "portrait_thresholds",
        "portrait_objects",
        "portrait_analysis_archives",
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
        report.add("node_sdk_contract_tests", True, {"skipped": "未找到 node 可执行文件"})
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
    parser = argparse.ArgumentParser(description="运行 PortraitHub 静态部署检查。")
    parser.add_argument("--root", default=".", help="项目根目录。")
    parser.add_argument("--import-app", action="store_true", help="导入 main.app 并校验关键路由。")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    parser.add_argument("--skip-node", action="store_true", help="跳过 Node.js 契约检查。")
    args = parser.parse_args()

    report = run_checks(args)
    output = {"ok": report.ok, "checks": report.checks}
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"部署检查：{'通过' if report.ok else '失败'}")
        for item in report.checks:
            marker = "通过" if item["ok"] else "失败"
            print(f"{marker}: {item['name']}")
            if not item["ok"]:
                print(f"  详情: {item['detail']}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
