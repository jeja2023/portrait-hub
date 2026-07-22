from __future__ import annotations

from typing import Any

from app import postgres_core as _core


def insert_call_log(payload: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                INSERT INTO portrait_call_logs (
                  request_id, tenant_id, project_id, application_id,
                  method, path, status, http_status, error_code,
                  latency_ms, model_version, worker, created_at
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, to_timestamp(%s)
                )
            """,
            (
                payload.get("request_id") or "",
                payload.get("tenant_id") or "default",
                payload.get("project_id") or "default",
                payload.get("application_id") or "--",
                payload.get("method") or "",
                payload.get("path") or "",
                payload.get("status") or "error",
                int(payload.get("http_status") or 0),
                payload.get("error_code"),
                max(0, int(payload.get("latency_ms") or 0)),
                payload.get("model_version") or "--",
                payload.get("worker") or "--",
                float(payload.get("created_at") or 0.0),
            ),
        )


def _filters(
    tenant_id: str,
    *,
    request_id: str | None = None,
    project_id: str | None = None,
    endpoint: str | None = None,
    status_text: str | None = None,
    application_id: str | None = None,
    error_code: str | None = None,
    created_since: float | None = None,
    created_until: float | None = None,
) -> tuple[str, list[Any]]:
    clauses = ["tenant_id = %s"]
    parameters: list[Any] = [tenant_id]
    if project_id is not None:
        clauses.append("project_id = %s")
        parameters.append(project_id)
    if request_id:
        clauses.append("request_id ILIKE %s")
        parameters.append(f"%{request_id.strip()}%")
    if endpoint:
        clauses.append("(method || ' ' || path) ILIKE %s")
        parameters.append(f"%{endpoint.strip()}%")
    if status_text:
        clauses.append("status = %s")
        parameters.append(status_text.strip().lower())
    if application_id:
        clauses.append("application_id ILIKE %s")
        parameters.append(f"%{application_id.strip()}%")
    if error_code:
        clauses.append("COALESCE(error_code, '') ILIKE %s")
        parameters.append(f"%{error_code.strip()}%")
    if created_since is not None:
        clauses.append("created_at >= to_timestamp(%s)")
        parameters.append(float(created_since))
    if created_until is not None:
        clauses.append("created_at <= to_timestamp(%s)")
        parameters.append(float(created_until))
    return " AND ".join(clauses), parameters


def query_call_logs(
    tenant_id: str,
    *,
    request_id: str | None = None,
    project_id: str | None = None,
    endpoint: str | None = None,
    status_text: str | None = None,
    application_id: str | None = None,
    error_code: str | None = None,
    created_since: float | None = None,
    created_until: float | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where, parameters = _filters(
        tenant_id,
        request_id=request_id,
        project_id=project_id,
        endpoint=endpoint,
        status_text=status_text,
        application_id=application_id,
        error_code=error_code,
        created_since=created_since,
        created_until=created_until,
    )
    with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
                SELECT request_id, tenant_id, project_id, application_id,
                       method, path, path AS endpoint, status, http_status,
                       error_code, latency_ms, model_version, worker,
                       EXTRACT(EPOCH FROM created_at)::double precision AS created_at
                FROM portrait_call_logs
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT %s
            """,
            [*parameters, max(1, min(int(limit), 500))],
        )
        return [dict(row) for row in cursor]


def summarize_call_logs(
    tenant_id: str,
    *,
    request_id: str | None = None,
    project_id: str | None = None,
    endpoint: str | None = None,
    status_text: str | None = None,
    application_id: str | None = None,
    error_code: str | None = None,
    created_since: float | None = None,
    created_until: float | None = None,
) -> dict[str, Any]:
    where, parameters = _filters(
        tenant_id,
        request_id=request_id,
        project_id=project_id,
        endpoint=endpoint,
        status_text=status_text,
        application_id=application_id,
        error_code=error_code,
        created_since=created_since,
        created_until=created_until,
    )
    with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
                SELECT COUNT(*)::bigint AS request_count,
                       COUNT(*) FILTER (WHERE status = 'success')::bigint AS success_count,
                       COUNT(*) FILTER (WHERE status <> 'success')::bigint AS error_count,
                       EXTRACT(EPOCH FROM MIN(created_at))::double precision AS oldest_created_at,
                       EXTRACT(EPOCH FROM MAX(created_at))::double precision AS newest_created_at
                FROM portrait_call_logs
                WHERE {where}
            """,
            parameters,
        )
        row = cursor.fetchone() or {}
    request_count = int(row.get("request_count") or 0)
    success_count = int(row.get("success_count") or 0)
    error_count = int(row.get("error_count") or 0)
    return {
        "request_count": request_count,
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": success_count / request_count if request_count else 1.0,
        "oldest_created_at": row.get("oldest_created_at"),
        "newest_created_at": row.get("newest_created_at"),
        "complete": True,
        "retained_count": request_count,
        "retained_limit": None,
    }


def application_usage_summaries(
    tenant_id: str,
    *,
    project_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    clauses = ["tenant_id = %s", "application_id <> '--'"]
    parameters: list[Any] = [tenant_id]
    if project_id is not None:
        clauses.append("project_id = %s")
        parameters.append(project_id)
    where = " AND ".join(clauses)
    with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
                SELECT application_id,
                       COUNT(*)::bigint AS call_count,
                       COUNT(*) FILTER (WHERE status <> 'success')::bigint AS error_count,
                       EXTRACT(EPOCH FROM MAX(created_at))::double precision AS last_called_at,
                       EXTRACT(
                         EPOCH FROM MAX(created_at) FILTER (WHERE status <> 'success')
                       )::double precision AS last_error_at
                FROM portrait_call_logs
                WHERE {where}
                GROUP BY application_id
            """,
            parameters,
        )
        rows = list(cursor)
    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        call_count = int(row["call_count"] or 0)
        error_count = int(row["error_count"] or 0)
        summaries[str(row["application_id"])] = {
            "call_count": call_count,
            "error_count": error_count,
            "error_rate": round(error_count / call_count, 6) if call_count else 0.0,
            "last_called_at": row.get("last_called_at"),
            "last_error_at": row.get("last_error_at"),
        }
    return summaries


__all__ = [
    "application_usage_summaries",
    "insert_call_log",
    "query_call_logs",
    "summarize_call_logs",
]
