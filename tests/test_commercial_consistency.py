from __future__ import annotations

from tools.portrait_commercial_consistency import INVARIANT_QUERIES, consistency_result


def test_commercial_consistency_passes_only_with_zero_counts_and_ready_dependencies() -> None:
    counts = {name: 0 for name in INVARIANT_QUERIES}
    result = consistency_result(
        counts,
        [
            {"name": "object_storage", "ok": True},
            {"name": "vector_store", "ok": True},
            {"name": "task_queue", "ok": True},
        ],
    )

    assert result["ok"] is True
    assert result["violations"] == {}


def test_commercial_consistency_fail_closes_on_dead_letter_or_dependency_failure() -> None:
    counts = {name: 0 for name in INVARIANT_QUERIES}
    counts["dead_letter_outbox"] = 2
    result = consistency_result(counts, [{"name": "vector_store", "ok": False}])

    assert result["ok"] is False
    assert result["violations"] == {"dead_letter_outbox": 2}


def test_consistency_queries_cover_snapshots_projection_expiry_and_outbox() -> None:
    joined = "\n".join(INVARIANT_QUERIES.values())

    assert "portrait_control_state" in joined
    assert "portrait_control_entities" in joined
    assert "commercial_profiles" in joined
    assert "entitlements" in joined
    assert "compliance_records" in joined
    assert "portrait_control_outbox" in joined
    assert "dead_letter" in joined
