from __future__ import annotations

from typing import Any

from app import postgres_core as _core


class AccessStateConflict(RuntimeError):
    pass


_EMPTY_ACCESS_STATE: dict[str, list[dict[str, Any]]] = {
    "tenants": [],
    "projects": [],
    "members": [],
    "applications": [],
    "webhooks": [],
}


def empty_access_state() -> dict[str, list[dict[str, Any]]]:
    return {key: [] for key in _EMPTY_ACCESS_STATE}


def consume_application_daily_quota(
    tenant_id: str,
    application_id: str,
    quota_date: str,
    daily_quota: int,
) -> int | None:
    with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                INSERT INTO portrait_application_daily_usage (
                  tenant_id, application_id, quota_date, daily_quota_used, updated_at
                )
                VALUES (%s, %s, CAST(%s AS date), 1, now())
                ON CONFLICT (tenant_id, application_id, quota_date) DO UPDATE SET
                  daily_quota_used = portrait_application_daily_usage.daily_quota_used + 1,
                  updated_at = now()
                WHERE portrait_application_daily_usage.daily_quota_used < %s
                RETURNING daily_quota_used
            """,
            (tenant_id, application_id, quota_date, max(1, int(daily_quota))),
        )
        row = cursor.fetchone()
    return int(row["daily_quota_used"]) if row is not None else None


def load_access_snapshot() -> tuple[dict[str, Any], int]:
    with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                SELECT payload, revision
                FROM portrait_access_state
                WHERE state_key = 'global'
            """
        )
        row = cursor.fetchone()
    if row is None:
        return empty_access_state(), 0
    payload = row["payload"]
    if not isinstance(payload, dict):
        raise ValueError("portrait_access_state payload must be an object")
    return payload, int(row["revision"])


def save_access_snapshot(payload: dict[str, Any], expected_revision: int) -> int:
    next_revision = max(0, int(expected_revision)) + 1
    with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
        if expected_revision <= 0:
            cursor.execute(
                """
                    INSERT INTO portrait_access_state (
                      state_key, revision, payload, updated_at
                    )
                    VALUES ('global', %s, %s::jsonb, now())
                    ON CONFLICT (state_key) DO NOTHING
                    RETURNING revision
                """,
                (next_revision, _core.jsonb(payload)),
            )
        else:
            cursor.execute(
                """
                    UPDATE portrait_access_state
                    SET revision = %s, payload = %s::jsonb, updated_at = now()
                    WHERE state_key = 'global' AND revision = %s
                    RETURNING revision
                """,
                (next_revision, _core.jsonb(payload), int(expected_revision)),
            )
        row = cursor.fetchone()
        if row is None:
            raise AccessStateConflict("access state changed concurrently")
        return int(row["revision"])


__all__ = [
    "AccessStateConflict",
    "consume_application_daily_quota",
    "empty_access_state",
    "load_access_snapshot",
    "save_access_snapshot",
]
