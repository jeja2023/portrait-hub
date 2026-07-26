import os
from pathlib import Path
from uuid import uuid4

import pytest

from tools import portrait_postgres_migrate as migrations
from tools.portrait_release_preflight import REQUIRED_CONTROL_TABLES

ROOT = Path(__file__).resolve().parents[1]


def test_commercial_control_plane_migration_covers_release_tables() -> None:
    discovered = migrations.discover_migrations()

    assert [item.version for item in discovered] == [1, 2]
    assert discovered[0].name == "commercial_control_plane"
    assert discovered[1].name == "control_entity_projection"
    assert all(len(item.sha256) == 64 for item in discovered)

    sql = "\n".join(item.path.read_text(encoding="utf-8") for item in discovered)
    for table in REQUIRED_CONTROL_TABLES - {"portrait_schema_migrations"}:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "portrait_control_outbox" in sql
    assert "portrait_entitlements_one_active_idx" in sql
    projection_sql = discovered[1].path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS portrait_control_entities" in projection_sql
    assert "tenant_id TEXT NOT NULL" in projection_sql
    assert "payload JSONB NOT NULL" in projection_sql


def test_migration_discovery_rejects_duplicate_versions(tmp_path: Path) -> None:
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_second.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(migrations.MigrationError, match="duplicate migration version"):
        migrations.discover_migrations(tmp_path)


def test_migration_history_rejects_checksum_drift() -> None:
    discovered = migrations.discover_migrations()
    applied = {
        1: {
            "version": 1,
            "name": discovered[0].name,
            "sha256": "0" * 64,
        }
    }

    with pytest.raises(migrations.MigrationError, match="drift"):
        migrations._validate_history(discovered, applied)


def test_migration_history_rejects_unknown_database_version() -> None:
    discovered = migrations.discover_migrations()

    with pytest.raises(migrations.MigrationError, match="absent from this release"):
        migrations._validate_history(
            discovered,
            {9999: {"version": 9999, "name": "future", "sha256": "f" * 64}},
        )


@pytest.mark.skipif(
    not os.getenv("PORTRAIT_TEST_POSTGRES_DSN"),
    reason="PORTRAIT_TEST_POSTGRES_DSN is required for real PostgreSQL control-state validation",
)
def test_control_state_snapshot_round_trip_against_real_postgres(monkeypatch) -> None:
    from app import postgres_core
    from app.postgres_control_state import ControlStateConflict, load_control_snapshot, save_control_snapshot

    dsn = os.environ["PORTRAIT_TEST_POSTGRES_DSN"]
    migrations.apply_migrations(dsn, applied_by="pytest-control-state")
    monkeypatch.setattr(postgres_core, "POSTGRES_DSN", dsn)
    monkeypatch.setattr(postgres_core, "POSTGRES_POOL", None)
    state_key = f"pytest-{uuid4().hex}"
    try:
        assert load_control_snapshot(state_key) == (None, 0)
        payload = {"revision": 1, "items": [{"id": "one"}]}
        assert save_control_snapshot(state_key, payload, 0, actor="pytest") == 1
        assert load_control_snapshot(state_key) == (payload, 1)
        updated = {"revision": 2, "items": [{"id": "two"}]}
        assert save_control_snapshot(state_key, updated, 1, actor="pytest") == 2
        with pytest.raises(ControlStateConflict):
            save_control_snapshot(state_key, payload, 1, actor="stale-pytest")
    finally:
        with postgres_core.postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM portrait_control_state WHERE state_key = %s", (state_key,))
