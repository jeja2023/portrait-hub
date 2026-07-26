from __future__ import annotations

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
