from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app import portrait_call_logs, portrait_postgres, postgres_call_logs


class FakeCursor:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.one = one
        self.rows = rows or []
        self.executions: list[tuple[str, Any]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self):
        return iter(self.rows)

    def execute(self, statement: str, parameters: Any = None) -> None:
        self.executions.append((statement, parameters))

    def fetchone(self) -> dict[str, Any] | None:
        return self.one


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> FakeCursor:
        return self._cursor


def install_connection(monkeypatch: pytest.MonkeyPatch, cursor: FakeCursor) -> None:
    @contextmanager
    def fake_connection(*, row_factory: Any = None):
        yield FakeConnection(cursor)

    monkeypatch.setattr(postgres_call_logs._core, "postgres_connection", fake_connection)


def test_insert_call_log_persists_project_and_stable_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    install_connection(monkeypatch, cursor)

    postgres_call_logs.insert_call_log(
        {
            "request_id": "req-1",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "application_id": "app-a",
            "method": "POST",
            "path": "/v1/infer/faces",
            "status": "error",
            "http_status": 503,
            "error_code": "model_unavailable",
            "latency_ms": 42,
            "created_at": 100.0,
        }
    )

    statement, parameters = cursor.executions[0]
    assert "INSERT INTO portrait_call_logs" in statement
    assert parameters[1:4] == ("tenant-a", "project-a", "app-a")
    assert parameters[8] == "model_unavailable"


def test_query_call_logs_applies_project_and_time_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = [{"request_id": "req-1", "project_id": "project-a"}]
    cursor = FakeCursor(rows=expected)
    install_connection(monkeypatch, cursor)

    rows = postgres_call_logs.query_call_logs(
        "tenant-a",
        project_id="project-a",
        error_code="model",
        created_since=90.0,
        created_until=110.0,
        limit=25,
    )

    assert rows == expected
    statement, parameters = cursor.executions[0]
    assert "project_id = %s" in statement
    assert "created_at >= to_timestamp(%s)" in statement
    assert parameters == ["tenant-a", "project-a", "%model%", 90.0, 110.0, 25]


def test_summarize_call_logs_returns_complete_database_window(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(
        one={
            "request_count": 4,
            "success_count": 3,
            "error_count": 1,
            "oldest_created_at": 10.0,
            "newest_created_at": 20.0,
        }
    )
    install_connection(monkeypatch, cursor)

    summary = postgres_call_logs.summarize_call_logs("tenant-a", project_id="project-a")

    assert summary["request_count"] == 4
    assert summary["success_rate"] == 0.75
    assert summary["complete"] is True
    assert summary["retained_limit"] is None


def test_application_usage_summaries_are_derived_from_durable_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(
        rows=[
            {
                "application_id": "app-a",
                "call_count": 10,
                "error_count": 2,
                "last_called_at": 20.0,
                "last_error_at": 19.0,
            }
        ]
    )
    install_connection(monkeypatch, cursor)

    summaries = postgres_call_logs.application_usage_summaries(
        "tenant-a",
        project_id="project-a",
    )

    assert summaries["app-a"]["error_rate"] == 0.2
    assert summaries["app-a"]["last_error_at"] == 19.0


def test_portrait_call_log_service_uses_postgres_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    inserted: list[dict[str, Any]] = []
    monkeypatch.setattr(portrait_call_logs, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(portrait_postgres, "insert_call_log", lambda payload: inserted.append(payload))
    monkeypatch.setattr(
        portrait_postgres,
        "query_call_logs",
        lambda tenant_id, **filters: [
            {
                "tenant_id": tenant_id,
                "project_id": filters["project_id"],
                "request_id": "req-db",
            }
        ],
    )
    portrait_call_logs.clear_call_logs()

    portrait_call_logs.record_call_log(
        request_id="req-db",
        tenant_id="tenant-a",
        project_id="project-a",
        application_id="app-a",
        method="GET",
        path="/v1/gallery",
        status_code=200,
        latency_ms=5,
        created_at=100.0,
    )
    rows = portrait_call_logs.list_call_logs("tenant-a", project_id="project-a")

    assert inserted[0]["project_id"] == "project-a"
    assert rows == [
        {
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "request_id": "req-db",
        }
    ]
