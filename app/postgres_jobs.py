from __future__ import annotations

import json
from typing import Any

from app.observability import logger
from app.portrait_response import exception_log_summary
from app import postgres_core as _core


def upsert_video_job(payload: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
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
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM portrait_video_jobs WHERE tenant_id = %s AND job_id = %s",
                (tenant_id, job_id),
            )


def load_video_jobs_snapshot() -> list[dict[str, Any]]:
    if not _core.postgres_configured() or _core.psycopg is None:
        return []
    try:
        with _core.postgres_connection(row_factory=_core.dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT tenant_id, job_id, status, progress, payload, result, error,
                           EXTRACT(EPOCH FROM created_at)::double precision AS created_at,
                           EXTRACT(EPOCH FROM updated_at)::double precision AS updated_at
                    FROM portrait_video_jobs
                    ORDER BY created_at
                    """
                )
                rows = cursor.fetchall()
    except Exception as exc:  # pragma: no cover - requires external database
        logger.warning("postgres video job load failed: %s", exception_log_summary(exc))
        return []
    jobs = []
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        jobs.append(
            {
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
        )
    return jobs
