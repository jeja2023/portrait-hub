from __future__ import annotations

import json
from typing import Any

from app import postgres_core as _core
from app.observability import logger
from app.portrait_response import exception_log_summary


def upsert_video_job(payload: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                INSERT INTO portrait_video_jobs (
                  tenant_id, job_id, status, progress, payload, result, error, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, to_timestamp(%s), to_timestamp(%s))
                ON CONFLICT (tenant_id, job_id) DO UPDATE SET
                  status = EXCLUDED.status,
                  progress = EXCLUDED.progress,
                  payload = EXCLUDED.payload,
                  result = EXCLUDED.result,
                  error = EXCLUDED.error,
                  updated_at = EXCLUDED.updated_at
                """,
            (
                payload.get("tenant_id") or "default",
                payload["job_id"],
                payload.get("status") or "queued",
                float(payload.get("progress") or 0.0),
                _core.jsonb(
                    {
                        "cancel_requested": bool(payload.get("cancel_requested", False)),
                    }
                ),
                json.dumps(payload.get("result"), ensure_ascii=False, sort_keys=True),
                payload.get("error"),
                float(payload.get("created_at") or 0.0),
                float(payload.get("updated_at") or 0.0),
            ),
        )


def delete_video_job(tenant_id: str, job_id: str) -> None:
    with _core.postgres_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM portrait_video_jobs WHERE tenant_id = %s AND job_id = %s",
            (tenant_id, job_id),
        )


def load_video_jobs_snapshot() -> list[dict[str, Any]]:
    if not _core.postgres_configured() or _core.psycopg is None:
        return []
    jobs = []
    try:
        with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    SELECT tenant_id, job_id, status, progress, payload, result, error,
                           EXTRACT(EPOCH FROM created_at)::double precision AS created_at,
                           EXTRACT(EPOCH FROM updated_at)::double precision AS updated_at
                    FROM portrait_video_jobs
                    ORDER BY created_at
                    """
            )
            for row in cursor:
                jobs.append(_job_row_to_dict(row))
    except Exception as exc:  # pragma: no cover - 需要外部数据库支持
        logger.warning("postgres video job load failed: %s", exception_log_summary(exc))
        return []
    return jobs


def load_video_job_record(tenant_id: str, job_id: str) -> dict[str, Any] | None:
    """按 (tenant_id, job_id) 加载单条任务记录，避免 worker 每条消息拉全表。"""
    if not _core.postgres_configured() or _core.psycopg is None:
        return None
    try:
        with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    SELECT tenant_id, job_id, status, progress, payload, result, error,
                           EXTRACT(EPOCH FROM created_at)::double precision AS created_at,
                           EXTRACT(EPOCH FROM updated_at)::double precision AS updated_at
                    FROM portrait_video_jobs
                    WHERE tenant_id = %s AND job_id = %s
                    """,
                (tenant_id, job_id),
            )
            row = cursor.fetchone()
    except Exception as exc:  # pragma: no cover - 需要外部数据库支持
        logger.warning("postgres video job load failed: %s", exception_log_summary(exc))
        return None
    if row is None:
        return None
    return _job_row_to_dict(row)


def _job_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    return {
        "tenant_id": row["tenant_id"],
        "job_id": row["job_id"],
        "filename": None,
        "status": row["status"],
        "progress": float(row["progress"] or 0.0),
        "created_at": float(row["created_at"] or 0.0),
        "updated_at": float(row["updated_at"] or 0.0),
        "error": row["error"],
        "result": row["result"],
        "cancel_requested": bool(payload.get("cancel_requested", False)),
    }
