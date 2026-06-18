from __future__ import annotations

from typing import Any

from app import postgres_core as _core


def insert_audit_event(payload: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO portrait_audit_events (
                  tenant_id, request_id, event, outcome,
                  audit_prev_hash, audit_hash, audit_hash_algorithm,
                  payload, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, to_timestamp(%s))
                """,
                (
                    payload.get("tenant_id") or "default",
                    payload.get("request_id") or "",
                    payload.get("event") or "",
                    payload.get("outcome") or "success",
                    payload.get("audit_prev_hash"),
                    payload.get("audit_hash") or "",
                    payload.get("audit_hash_algorithm") or "",
                    _core.jsonb(payload),
                    float(payload.get("created_at") or 0.0),
                ),
            )

