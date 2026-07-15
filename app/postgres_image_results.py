from __future__ import annotations

from typing import Any

from app import postgres_core as _core
from app.observability import logger
from app.portrait_response import exception_log_summary


def upsert_image_analysis_result(
    payload: dict[str, Any], *, max_results: int
) -> None:
    with _core.postgres_connection() as connection, connection.cursor() as cursor:
        tenant_id = str(payload.get("tenant_id") or "default")
        cursor.execute(
            """
                INSERT INTO portrait_image_results (
                  tenant_id, result_id, request_id, mode, endpoint, payload,
                  previews, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, to_timestamp(%s))
                ON CONFLICT (tenant_id, result_id) DO UPDATE SET
                  request_id = EXCLUDED.request_id,
                  mode = EXCLUDED.mode,
                  endpoint = EXCLUDED.endpoint,
                  payload = EXCLUDED.payload,
                  previews = EXCLUDED.previews,
                  created_at = EXCLUDED.created_at
                """,
            (
                tenant_id,
                payload["result_id"],
                payload.get("request_id") or "",
                payload.get("mode") or "image",
                payload.get("endpoint") or "",
                _core.jsonb(payload.get("payload") or {}),
                _core.jsonb(payload.get("previews") or []),
                float(payload.get("created_at") or 0.0),
            ),
        )
        cursor.execute(
            """
                DELETE FROM portrait_image_results
                WHERE tenant_id = %s
                  AND result_id NOT IN (
                    SELECT result_id
                    FROM portrait_image_results
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC, result_id
                    LIMIT %s
                  )
                """,
            (tenant_id, tenant_id, max(1, int(max_results))),
        )


def load_image_analysis_results_snapshot() -> list[dict[str, Any]]:
    if not _core.postgres_configured() or _core.psycopg is None:
        return []
    results: list[dict[str, Any]] = []
    try:
        with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    SELECT tenant_id, result_id, request_id, mode, endpoint, payload,
                           previews,
                           EXTRACT(EPOCH FROM created_at)::double precision AS created_at
                    FROM portrait_image_results
                    ORDER BY created_at DESC, result_id
                    """
            )
            for row in cursor:
                results.append(
                    {
                        "tenant_id": row["tenant_id"],
                        "result_id": row["result_id"],
                        "request_id": row["request_id"],
                        "mode": row["mode"],
                        "endpoint": row["endpoint"],
                        "payload": (
                            row["payload"]
                            if isinstance(row["payload"], dict)
                            else {}
                        ),
                        "previews": (
                            row["previews"]
                            if isinstance(row["previews"], list)
                            else []
                        ),
                        "created_at": float(row["created_at"] or 0.0),
                    }
                )
    except Exception as exc:  # pragma: no cover - requires an external database
        logger.warning(
            "postgres image analysis result load failed: %s",
            exception_log_summary(exc),
        )
        return []
    return results


__all__ = [
    "load_image_analysis_results_snapshot",
    "upsert_image_analysis_result",
]
