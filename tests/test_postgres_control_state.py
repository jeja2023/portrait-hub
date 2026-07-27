from __future__ import annotations

from app import postgres_control_state
from app.postgres_control_state import control_entity_rows


def test_control_entity_rows_preserve_scope_cas_and_audit_fields() -> None:
    rows = control_entity_rows(
        "commercial",
        {
            "commercial_profiles": [
                {
                    "profile_id": "profile-1",
                    "tenant_id": "tenant-a",
                    "project_id": "project-a",
                    "commercial_status": "active",
                    "version": 3,
                    "request_id": "request-1",
                    "audit_event_id": "audit-1",
                    "created_at": 1_700_000_000,
                    "created_by": "owner-a",
                    "updated_at": 1_700_000_100,
                    "updated_by": "owner-b",
                }
            ]
        },
        "fallback-owner",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row[0:8] == (
        "commercial",
        "commercial_profiles",
        "tenant-a",
        "project-a",
        "profile-1",
        3,
        "active",
        "internal",
    )
    assert row[10:12] == ("request-1", "audit-1")
    assert row[13] == "owner-a"
    assert row[15] == "owner-b"


def test_control_entity_rows_reject_missing_entity_identity() -> None:
    try:
        control_entity_rows("feedback", {"review_samples": [{}]}, "owner")
    except ValueError as exc:
        assert "sample_id" in str(exc)
    else:
        raise AssertionError("missing entity identity must fail closed")


def test_control_entity_sync_uses_conditional_upsert_and_prunes_missing_rows() -> None:
    class RecordingCursor:
        def __init__(self) -> None:
            self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
            self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

        def executemany(self, sql: str, rows: list[tuple[object, ...]]) -> None:
            self.executemany_calls.append((sql, rows))

        def execute(self, sql: str, params: tuple[object, ...]) -> None:
            self.execute_calls.append((sql, params))

    cursor = RecordingCursor()
    payload = {
        "review_samples": [
            {"sample_id": "sample-1", "tenant_id": "tenant-a", "project_id": "project-a"},
            {"sample_id": "sample-2", "tenant_id": "tenant-b", "project_id": "project-b"},
        ]
    }

    postgres_control_state._sync_control_entities(cursor, "feedback", payload, "owner")

    upsert_sql, rows = cursor.executemany_calls[0]
    assert "ON CONFLICT" in upsert_sql
    assert "IS DISTINCT FROM" in upsert_sql
    assert len(rows) == 2
    prune_sql, prune_params = cursor.execute_calls[0]
    assert "unnest" in prune_sql
    assert prune_params == (
        "feedback",
        ["review_samples", "review_samples"],
        ["tenant-a", "tenant-b"],
        ["project-a", "project-b"],
        ["sample-1", "sample-2"],
    )


def test_control_entity_sync_removes_all_projected_rows_for_empty_snapshot() -> None:
    class RecordingCursor:
        def __init__(self) -> None:
            self.call: tuple[str, tuple[str, ...]] | None = None

        def execute(self, sql: str, params: tuple[str, ...]) -> None:
            self.call = sql, params

    cursor = RecordingCursor()

    postgres_control_state._sync_control_entities(cursor, "feedback", {}, "owner")

    assert cursor.call == (
        "DELETE FROM portrait_control_entities WHERE state_key = %s",
        ("feedback",),
    )
