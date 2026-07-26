from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.portrait_postgres_migrate import MigrationError, migration_status

SAFE_REPAIR_CONFIRMATION = "APPLY-SAFE-REPAIRS"
INVARIANT_QUERIES = {
    "control_state_snapshot_missing": """
        SELECT COUNT(*)
        FROM (VALUES ('commercial'), ('feedback'), ('model_registry')) AS required(state_key)
        WHERE NOT EXISTS (
          SELECT 1 FROM portrait_control_state state WHERE state.state_key = required.state_key
        )
    """,
    "control_state_projection_mismatch": """
        WITH mapped(state_key, collection_name) AS (
          VALUES
            ('commercial', 'commercial_profiles'), ('commercial', 'entitlements'),
            ('commercial', 'sla_definitions'), ('commercial', 'sla_reports'),
            ('commercial', 'incidents'), ('commercial', 'compliance_records'),
            ('commercial', 'rights_requests'), ('commercial', 'evidence_packages'),
            ('commercial', 'template_applications'), ('commercial', 'support_cases'),
            ('feedback', 'review_samples'), ('feedback', 'annotation_exports'),
            ('feedback', 'annotation_imports'), ('feedback', 'dataset_manifests'),
            ('model_registry', 'models'), ('model_registry', 'versions'),
            ('model_registry', 'evaluations'), ('model_registry', 'approvals'),
            ('model_registry', 'release_events')
        ), expected AS (
          SELECT COALESCE(SUM(jsonb_array_length(COALESCE(state.payload -> mapped.collection_name, '[]'::jsonb))), 0) AS count
          FROM mapped
          JOIN portrait_control_state state ON state.state_key = mapped.state_key
        ), actual AS (
          SELECT COUNT(*) AS count FROM portrait_control_entities
        )
        SELECT ABS(expected.count - actual.count) FROM expected CROSS JOIN actual
    """,
    "profile_entitlement_missing_or_inactive": """
        SELECT COUNT(*)
        FROM portrait_control_entities profile
        LEFT JOIN portrait_control_entities entitlement
          ON entitlement.tenant_id = profile.tenant_id
         AND entitlement.project_id = profile.project_id
         AND entitlement.collection_name = 'entitlements'
         AND entitlement.entity_id = profile.payload ->> 'current_entitlement_id'
        WHERE profile.collection_name = 'commercial_profiles'
          AND profile.payload ->> 'current_entitlement_id' IS NOT NULL
          AND (entitlement.entity_id IS NULL OR entitlement.status <> 'active')
    """,
    "active_entitlement_profile_mismatch": """
        SELECT COUNT(*)
        FROM portrait_control_entities entitlement
        LEFT JOIN portrait_control_entities profile
          ON profile.tenant_id = entitlement.tenant_id
         AND profile.project_id = entitlement.project_id
         AND profile.collection_name = 'commercial_profiles'
        WHERE entitlement.collection_name = 'entitlements'
          AND entitlement.status = 'active'
          AND (profile.entity_id IS NULL OR profile.payload ->> 'current_entitlement_id' IS DISTINCT FROM entitlement.entity_id)
    """,
    "stale_delivering_outbox": """
        SELECT COUNT(*)
        FROM portrait_control_outbox
        WHERE status = 'delivering' AND available_at < now() - interval '15 minutes'
    """,
    "dead_letter_outbox": """
        SELECT COUNT(*) FROM portrait_control_outbox WHERE status = 'dead_letter'
    """,
    "expired_active_entitlements": """
        SELECT COUNT(*)
        FROM portrait_control_entities
        WHERE collection_name = 'entitlements' AND status = 'active'
          AND expires_at IS NOT NULL AND expires_at <= now()
    """,
    "expired_approved_compliance": """
        SELECT COUNT(*)
        FROM portrait_control_entities
        WHERE collection_name = 'compliance_records' AND status = 'approved'
          AND expires_at IS NOT NULL AND expires_at <= now()
    """,
}


def _driver() -> Any:
    try:
        import psycopg
    except Exception as exc:
        raise MigrationError(f"psycopg is unavailable: {type(exc).__name__}") from exc
    return psycopg


def consistency_result(counts: dict[str, int], dependencies: list[dict[str, Any]]) -> dict[str, Any]:
    violations = {name: count for name, count in counts.items() if count != 0}
    dependency_failures = [item for item in dependencies if item.get("ok") is not True]
    return {
        "ok": not violations and not dependency_failures,
        "invariants": counts,
        "violations": violations,
        "dependencies": dependencies,
        "repairable": {
            "active_entitlement_profile_mismatch": 0,
            "stale_delivering_outbox": counts.get("stale_delivering_outbox", 0),
        },
    }


def dependency_health() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    dependencies: list[tuple[str, Any]] = []
    try:
        from app.portrait_object_storage import OBJECT_STORE

        dependencies.append(("object_storage", OBJECT_STORE))
    except Exception as exc:
        checks.append({"name": "object_storage", "ok": False, "error": type(exc).__name__})
    try:
        from app.portrait_task_queue import TASK_QUEUE

        dependencies.append(("task_queue", TASK_QUEUE))
    except Exception as exc:
        checks.append({"name": "task_queue", "ok": False, "error": type(exc).__name__})
    try:
        from app.portrait_vector_store import VECTOR_STORE

        dependencies.append(("vector_store", VECTOR_STORE))
    except Exception as exc:
        checks.append({"name": "vector_store", "ok": False, "error": type(exc).__name__})
    for name, dependency in dependencies:
        try:
            health = dependency.health()
            checks.append({"name": name, "ok": health.get("status") == "ready", "health": health})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "error": type(exc).__name__})
    return checks


def check_consistency(dsn: str, *, check_external_dependencies: bool = True) -> dict[str, Any]:
    if not dsn.strip():
        return {"name": "commercial_consistency", "ok": False, "error": "POSTGRES_DSN is required"}
    try:
        migrations = migration_status(dsn)
        psycopg = _driver()
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                counts: dict[str, int] = {}
                for name, query in INVARIANT_QUERIES.items():
                    cursor.execute(query)
                    counts[name] = int(cursor.fetchone()[0])
        dependencies = dependency_health() if check_external_dependencies else []
    except (Exception, MigrationError) as exc:
        return {
            "name": "commercial_consistency",
            "ok": False,
            "error": f"consistency check failed: {type(exc).__name__}",
        }
    result = consistency_result(counts, dependencies)
    return {"name": "commercial_consistency", "migration_history": migrations, **result}


def apply_safe_repairs(dsn: str, *, actor: str, confirmation: str) -> dict[str, Any]:
    if confirmation != SAFE_REPAIR_CONFIRMATION:
        raise ValueError(f"confirmation must equal {SAFE_REPAIR_CONFIRMATION}")
    psycopg = _driver()
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE portrait_control_outbox
                    SET status = 'failed',
                        available_at = now(),
                        last_error = 'requeued by commercial consistency repair'
                    WHERE status = 'delivering' AND available_at < now() - interval '15 minutes'
                    """
                )
                requeued_outbox = cursor.rowcount
    return {
        "ok": True,
        "repaired_profiles": 0,
        "requeued_outbox": requeued_outbox,
        "actor": actor,
        "note": "authoritative control snapshots are repaired through the control-plane API, not by editing projections",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check and safely repair commercial control-plane consistency.")
    parser.add_argument("--dsn", default=os.getenv("POSTGRES_DSN", ""))
    parser.add_argument("--skip-external-dependencies", action="store_true")
    parser.add_argument("--apply-safe-repairs", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--actor", default=os.getenv("PORTRAIT_RELEASE_ACTOR", "consistency-tool"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        repairs = (
            apply_safe_repairs(args.dsn, actor=args.actor, confirmation=args.confirmation)
            if args.apply_safe_repairs
            else None
        )
        result = check_consistency(
            args.dsn,
            check_external_dependencies=not args.skip_external_dependencies,
        )
        if repairs is not None:
            result["repairs"] = repairs
    except (MigrationError, ValueError) as exc:
        result = {"name": "commercial_consistency", "ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
