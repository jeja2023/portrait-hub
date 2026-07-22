from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app import postgres_access


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any] | None]) -> None:
        self.rows = rows
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[Any, ...] | None = None) -> None:
        self.executions.append((statement, parameters))

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows.pop(0)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> FakeCursor:
        return self._cursor


def install_connection(monkeypatch: pytest.MonkeyPatch, cursor: FakeCursor) -> None:
    @contextmanager
    def fake_connection(*, row_factory: Any = None):
        assert row_factory is postgres_access._core.dict_row
        yield FakeConnection(cursor)

    monkeypatch.setattr(postgres_access._core, "postgres_connection", fake_connection)


def test_load_access_snapshot_returns_empty_version_for_uninitialized_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor([None])
    install_connection(monkeypatch, cursor)

    payload, revision = postgres_access.load_access_snapshot()

    assert revision == 0
    assert payload == {
        "tenants": [],
        "projects": [],
        "members": [],
        "applications": [],
        "webhooks": [],
    }
    assert "FROM portrait_access_state" in cursor.executions[0][0]


def test_load_access_snapshot_returns_database_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"tenants": [{"tenant_id": "tenant-a"}], "projects": []}
    cursor = FakeCursor([{"payload": expected, "revision": 7}])
    install_connection(monkeypatch, cursor)

    payload, revision = postgres_access.load_access_snapshot()

    assert payload == expected
    assert revision == 7


@pytest.mark.parametrize(
    ("expected_revision", "returned_revision", "statement_marker", "parameters"),
    [
        (0, 1, "INSERT INTO portrait_access_state", (1, "encoded")),
        (7, 8, "UPDATE portrait_access_state", (8, "encoded", 7)),
    ],
)
def test_save_access_snapshot_uses_compare_and_swap(
    monkeypatch: pytest.MonkeyPatch,
    expected_revision: int,
    returned_revision: int,
    statement_marker: str,
    parameters: tuple[Any, ...],
) -> None:
    cursor = FakeCursor([{"revision": returned_revision}])
    install_connection(monkeypatch, cursor)
    monkeypatch.setattr(postgres_access._core, "jsonb", lambda _payload: "encoded")

    revision = postgres_access.save_access_snapshot({"projects": []}, expected_revision)

    assert revision == returned_revision
    statement, actual_parameters = cursor.executions[0]
    assert statement_marker in statement
    assert actual_parameters == parameters


def test_save_access_snapshot_rejects_stale_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor([None])
    install_connection(monkeypatch, cursor)
    monkeypatch.setattr(postgres_access._core, "jsonb", lambda _payload: "encoded")

    with pytest.raises(postgres_access.AccessStateConflict):
        postgres_access.save_access_snapshot({"projects": []}, 3)


@pytest.mark.parametrize(
    ("row", "expected"),
    [({"daily_quota_used": 2}, 2), (None, None)],
)
def test_consume_application_daily_quota_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
    row: dict[str, Any] | None,
    expected: int | None,
) -> None:
    cursor = FakeCursor([row])
    install_connection(monkeypatch, cursor)

    used = postgres_access.consume_application_daily_quota(
        "tenant-a",
        "app-a",
        "2026-07-22",
        10,
    )

    assert used == expected
    statement, parameters = cursor.executions[0]
    assert "ON CONFLICT (tenant_id, application_id, quota_date)" in statement
    assert "daily_quota_used < %s" in statement
    assert parameters == ("tenant-a", "app-a", "2026-07-22", 10)
