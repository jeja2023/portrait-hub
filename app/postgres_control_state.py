from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.postgres_core import jsonb, postgres_connection


class ControlStateConflict(RuntimeError):
    pass


CONTROL_ENTITY_IDS = {
    "commercial": {
        "commercial_profiles": "profile_id",
        "entitlements": "entitlement_id",
        "sla_definitions": "sla_definition_id",
        "sla_reports": "sla_report_id",
        "incidents": "incident_id",
        "compliance_records": "compliance_record_id",
        "rights_requests": "rights_request_id",
        "evidence_packages": "evidence_package_id",
        "template_applications": "template_application_id",
        "support_cases": "support_case_id",
    },
    "feedback": {
        "review_samples": "sample_id",
        "annotation_exports": "export_id",
        "annotation_imports": "import_id",
        "dataset_manifests": "dataset_id",
    },
    "model_registry": {
        "models": "model_id",
        "versions": "model_version_id",
        "evaluations": "evaluation_id",
        "approvals": "approval_id",
        "release_events": "release_event_id",
    },
}


def _datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return fallback


def control_entity_rows(state_key: str, payload: dict[str, Any], actor: str) -> list[tuple[Any, ...]]:
    collections = CONTROL_ENTITY_IDS.get(state_key, {})
    rows: list[tuple[Any, ...]] = []
    current = datetime.now(UTC)
    for collection_name, id_field in collections.items():
        records = payload.get(collection_name, [])
        if not isinstance(records, list):
            raise ValueError(f"control state collection is not an array: {state_key}.{collection_name}")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"control state entity is not an object: {state_key}.{collection_name}")
            entity_id = str(record.get(id_field) or "").strip()
            if not entity_id:
                raise ValueError(f"control state entity is missing {id_field}: {state_key}.{collection_name}")
            tenant_id = str(record.get("tenant_id") or "__platform__")
            project_id = str(record.get("project_id") or "default")
            created_at = _datetime(record.get("created_at"), current)
            effective_at = _datetime(record.get("effective_at", record.get("starts_at")), created_at)
            expires_at = _datetime(record["expires_at"], current) if record.get("expires_at") is not None else None
            updated_at = _datetime(record.get("updated_at"), created_at)
            raw_version = record.get("version_counter", record.get("version", 1))
            entity_version = int(raw_version) if isinstance(raw_version, int) and not isinstance(raw_version, bool) else 1
            status_value = record.get("status", record.get("commercial_status", "recorded"))
            rows.append(
                (
                    state_key,
                    collection_name,
                    tenant_id,
                    project_id,
                    entity_id,
                    max(1, entity_version),
                    str(status_value or "recorded")[:128],
                    str(record.get("classification") or "internal")[:128],
                    effective_at,
                    expires_at,
                    str(record.get("request_id") or f"control-state:{state_key}")[:256],
                    str(record["audit_event_id"])[:256] if record.get("audit_event_id") else None,
                    created_at,
                    str(record.get("created_by") or actor)[:256],
                    updated_at,
                    str(record.get("updated_by") or record.get("created_by") or actor)[:256],
                    jsonb(record),
                )
            )
    return rows


def _sync_control_entities(cursor: Any, state_key: str, payload: dict[str, Any], actor: str) -> None:
    if state_key not in CONTROL_ENTITY_IDS:
        return
    cursor.execute("DELETE FROM portrait_control_entities WHERE state_key = %s", (state_key,))
    rows = control_entity_rows(state_key, payload, actor)
    if not rows:
        return
    cursor.executemany(
        """
        INSERT INTO portrait_control_entities
          (state_key, collection_name, tenant_id, project_id, entity_id, entity_version,
           status, classification, effective_at, expires_at, request_id, audit_event_id,
           created_at, created_by, updated_at, updated_by, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        rows,
    )


def load_control_snapshot(state_key: str) -> tuple[dict[str, Any] | None, int]:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload, revision FROM portrait_control_state WHERE state_key = %s",
                (state_key,),
            )
            row = cursor.fetchone()
    if row is None:
        return None, 0
    payload = row[0]
    return (dict(payload) if isinstance(payload, dict) else None), int(row[1])


def save_control_snapshot(
    state_key: str,
    payload: dict[str, Any],
    expected_revision: int,
    *,
    actor: str = "portrait-api",
) -> int:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            if expected_revision == 0:
                cursor.execute(
                    """
                    INSERT INTO portrait_control_state
                      (state_key, revision, payload, definition_version, updated_at, updated_by)
                    VALUES (%s, 1, %s::jsonb, '1.0', now(), %s)
                    ON CONFLICT (state_key) DO NOTHING
                    RETURNING revision
                    """,
                    (state_key, jsonb(payload), actor[:256]),
                )
            else:
                cursor.execute(
                    """
                    UPDATE portrait_control_state
                    SET revision = revision + 1,
                        payload = %s::jsonb,
                        updated_at = now(),
                        updated_by = %s
                    WHERE state_key = %s AND revision = %s
                    RETURNING revision
                    """,
                    (jsonb(payload), actor[:256], state_key, expected_revision),
                )
            row = cursor.fetchone()
            if row is None:
                raise ControlStateConflict(f"control state changed concurrently: {state_key}")
            _sync_control_entities(cursor, state_key, payload, actor)
            return int(row[0])


__all__ = [
    "CONTROL_ENTITY_IDS",
    "ControlStateConflict",
    "control_entity_rows",
    "load_control_snapshot",
    "save_control_snapshot",
]
