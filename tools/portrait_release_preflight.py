from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model_config import MODEL_CONFIGS
from app.model_package import get_model_path
from app.model_refs import split_cache_key
from app.runtime_registry import get_or_load_model, prewarm_model_bundle
from tools.portrait_kubernetes_release import (
    ReleaseManifestError,
    load_release_resources,
    validate_release,
)
from tools.portrait_postgres_migrate import MigrationError, apply_migrations, migration_status
from tools.portrait_production_readiness import (
    check_capabilities,
    check_commercial_delivery,
    check_data_stack,
    check_model_files,
    check_security_controls,
    check_templates,
)

REQUIRED_CONTROL_TABLES = {
    "portrait_schema_migrations",
    "portrait_model_registry",
    "portrait_model_artifacts",
    "portrait_model_evaluations",
    "portrait_model_approvals",
    "portrait_model_release_events",
    "portrait_review_samples",
    "portrait_dataset_manifests",
    "portrait_customer_profiles",
    "portrait_entitlements",
    "portrait_sla_definitions",
    "portrait_sla_reports",
    "portrait_incidents",
    "portrait_compliance_records",
    "portrait_evidence_packages",
    "portrait_rights_requests",
    "portrait_usage_daily_summary",
    "portrait_cost_attribution",
    "portrait_annotation_exports",
    "portrait_annotation_imports",
    "portrait_industry_template_applications",
    "portrait_support_cases",
    "portrait_control_outbox",
    "portrait_control_entities",
}


def apply_database_migrations() -> dict[str, Any]:
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    try:
        result = apply_migrations(
            dsn,
            applied_by=os.getenv("PORTRAIT_RELEASE_ACTOR", "kubernetes-preflight"),
        )
    except MigrationError as exc:
        return {"name": "database_migration_apply", "ok": False, "detail": str(exc)}
    return {"name": "database_migration_apply", **result}


def check_migrations() -> dict[str, Any]:
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        return {"name": "database_migrations", "ok": False, "detail": "POSTGRES_DSN is required"}
    try:
        import psycopg
    except Exception as exc:
        return {
            "name": "database_migrations",
            "ok": False,
            "detail": f"psycopg is unavailable: {type(exc).__name__}",
        }
    try:
        history = migration_status(dsn)
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = current_schema()"
                )
                present = {str(row[0]) for row in cursor.fetchall()}
                missing = sorted(REQUIRED_CONTROL_TABLES - present)
                cursor.execute("SHOW server_version_num")
                version_row = cursor.fetchone()
                if version_row is None:
                    raise RuntimeError("PostgreSQL did not return server_version_num")
                server_version_num = int(version_row[0])
    except (Exception, MigrationError) as exc:
        return {
            "name": "database_migrations",
            "ok": False,
            "detail": f"database migration check failed: {type(exc).__name__}",
        }
    return {
        "name": "database_migrations",
        "ok": not missing and server_version_num >= 150000 and history["ok"],
        "missing_tables": missing,
        "server_version_num": server_version_num,
        "migration_history": history,
    }


async def prewarm_models() -> dict[str, Any]:
    results = []
    for model_id in sorted(MODEL_CONFIGS):
        try:
            project_name, model_name = split_cache_key(model_id)
            bundle, cold_loaded, load_seconds = await get_or_load_model(
                model_id, get_model_path(project_name, model_name)
            )
            prewarm = await prewarm_model_bundle(model_id, bundle)
            results.append(
                {
                    "model_id": model_id,
                    "ok": True,
                    "cold_loaded": cold_loaded,
                    "load_seconds": load_seconds,
                    "model_fingerprint": bundle.get("model_fingerprint"),
                    "prewarm": prewarm,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "model_id": model_id,
                    "ok": False,
                    "error_type": type(exc).__name__,
                }
            )
    return {"name": "model_prewarm", "ok": bool(results) and all(item["ok"] for item in results), "models": results}


def readiness_checks(root: Path, models_root: Path) -> list[dict[str, Any]]:
    return [
        *check_templates(root),
        *check_data_stack(root),
        *check_security_controls(root),
        *check_commercial_delivery(root),
        *check_capabilities(root),
        *check_model_files(root, models_root),
    ]


def check_kubernetes_manifest(path: Path) -> dict[str, Any]:
    try:
        errors = validate_release(load_release_resources(path))
    except ReleaseManifestError as exc:
        errors = [str(exc)]
    return {
        "name": "kubernetes_release_manifest",
        "ok": not errors,
        "path": str(path),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run immutable release gates before Kubernetes rollout.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--models-root", default=os.getenv("MODELS_ROOT", "models"))
    parser.add_argument("--apply-migrations", action="store_true")
    parser.add_argument("--check-migrations", action="store_true")
    parser.add_argument("--check-models", action="store_true")
    parser.add_argument("--prewarm", action="store_true")
    parser.add_argument("--kubernetes-manifest", type=Path)
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    if args.apply_migrations:
        checks.append(apply_database_migrations())
    if args.check_migrations:
        checks.append(check_migrations())
    if args.check_models:
        checks.extend(readiness_checks(Path(args.root).resolve(), Path(args.models_root).resolve()))
    if args.prewarm:
        checks.append(asyncio.run(prewarm_models()))
    if args.kubernetes_manifest is not None:
        checks.append(check_kubernetes_manifest(args.kubernetes_manifest.resolve()))
    output = {"ok": bool(checks) and all(item.get("ok") for item in checks), "checks": checks}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
