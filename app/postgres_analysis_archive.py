from __future__ import annotations

from typing import Any

from app import postgres_core as _core


def _record_from_row(row: Any) -> dict[str, Any]:
    return {
        "tenant_id": row["tenant_id"],
        "archive_id": row["archive_id"],
        "request_id": row["request_id"],
        "source_type": row["source_type"],
        "source_ref": row["source_ref"],
        "mode": row["mode"],
        "endpoint": row["endpoint"],
        "payload": row["payload"] if isinstance(row["payload"], dict) else {},
        "artifacts": row["artifacts"] if isinstance(row["artifacts"], list) else [],
        "created_at": float(row["created_at"] or 0.0),
    }


def upsert_analysis_archive(payload: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                INSERT INTO portrait_analysis_archives (
                  tenant_id, archive_id, request_id, source_type, source_ref,
                  mode, endpoint, payload, artifacts, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, to_timestamp(%s))
                ON CONFLICT (tenant_id, archive_id) DO UPDATE SET
                  request_id = EXCLUDED.request_id,
                  source_type = EXCLUDED.source_type,
                  source_ref = EXCLUDED.source_ref,
                  mode = EXCLUDED.mode,
                  endpoint = EXCLUDED.endpoint,
                  payload = EXCLUDED.payload,
                  artifacts = EXCLUDED.artifacts,
                  created_at = EXCLUDED.created_at
            """,
            (
                payload.get("tenant_id") or "default",
                payload["archive_id"],
                payload.get("request_id") or "",
                payload.get("source_type") or "image",
                payload.get("source_ref") or "",
                payload.get("mode") or "analysis",
                payload.get("endpoint") or "",
                _core.jsonb(payload.get("payload") or {}),
                _core.jsonb(payload.get("artifacts") or []),
                float(payload.get("created_at") or 0.0),
            ),
        )


def query_analysis_archives(
    tenant_id: str,
    *,
    source_type: str | None,
    mode: str | None,
    limit: int,
    offset: int,
    cursor_values: list[Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_filters = ["tenant_id = %s"]
    base_params: list[Any] = [tenant_id]
    if source_type is not None:
        base_filters.append("source_type = %s")
        base_params.append(source_type)
    if mode is not None:
        base_filters.append("mode = %s")
        base_params.append(mode)
    filters = list(base_filters)
    params = list(base_params)
    if cursor_values is not None:
        if len(cursor_values) != 2:
            raise ValueError("解析档案游标无效")
        cursor_created_at = -float(cursor_values[0])
        cursor_archive_id = str(cursor_values[1])
        filters.append(
            "(created_at < to_timestamp(%s) OR (created_at = to_timestamp(%s) AND archive_id > %s))"
        )
        params.extend([cursor_created_at, cursor_created_at, cursor_archive_id])
    base_where = " AND ".join(base_filters)
    where = " AND ".join(filters)
    with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) AS total FROM portrait_analysis_archives WHERE {base_where}",
            base_params,
        )
        total = int(cursor.fetchone()["total"])
        cursor.execute(
            f"""
                SELECT tenant_id, archive_id, request_id, source_type, source_ref,
                       mode, endpoint, payload, artifacts,
                       EXTRACT(EPOCH FROM created_at)::double precision AS created_at
                FROM portrait_analysis_archives
                WHERE {where}
                ORDER BY created_at DESC, archive_id
                LIMIT %s OFFSET %s
            """,
            [*params, limit + 1, offset],
        )
        rows = list(cursor)
    has_more = len(rows) > limit
    records = [_record_from_row(row) for row in rows[:limit]]
    return records, {
        "count": len(records),
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": None if cursor_values is not None or not has_more else offset + len(records),
        "has_more": has_more,
    }


def get_analysis_archive(tenant_id: str, archive_id: str) -> dict[str, Any] | None:
    with _core.postgres_connection(row_factory=_core.dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                SELECT tenant_id, archive_id, request_id, source_type, source_ref,
                       mode, endpoint, payload, artifacts,
                       EXTRACT(EPOCH FROM created_at)::double precision AS created_at
                FROM portrait_analysis_archives
                WHERE tenant_id = %s AND archive_id = %s
            """,
            (tenant_id, archive_id),
        )
        row = cursor.fetchone()
    return _record_from_row(row) if row is not None else None


__all__ = ["get_analysis_archive", "query_analysis_archives", "upsert_analysis_archive"]
