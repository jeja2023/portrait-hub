from __future__ import annotations

import json
from typing import Any

from app.observability import logger
from app.portrait_crypto import decrypt_bytes, encrypt_bytes
from app.portrait_response import exception_log_summary
from app import postgres_core as _core


def encode_stream_url(stream_url: str) -> bytes:
    return json.dumps(encrypt_bytes(stream_url.encode("utf-8")), ensure_ascii=False, sort_keys=True).encode("utf-8")


def decode_stream_url(payload: bytes) -> str:
    return decrypt_bytes(json.loads(payload.decode("utf-8"))).decode("utf-8")


def upsert_stream(payload: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            tenant_id = payload.get("tenant_id") or "default"
            stream_id = payload["stream_id"]
            cursor.execute(
                """
                INSERT INTO portrait_streams (
                  tenant_id, stream_id, stream_url_ciphertext, name, settings, metadata,
                  status, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, to_timestamp(%s), to_timestamp(%s))
                ON CONFLICT (tenant_id, stream_id) DO UPDATE SET
                  stream_url_ciphertext = EXCLUDED.stream_url_ciphertext,
                  name = EXCLUDED.name,
                  settings = EXCLUDED.settings,
                  metadata = EXCLUDED.metadata,
                  status = EXCLUDED.status,
                  updated_at = EXCLUDED.updated_at
                """,
                (
                    tenant_id,
                    stream_id,
                    encode_stream_url(str(payload.get("stream_url") or "")),
                    payload.get("name"),
                    _core.jsonb(payload.get("settings") or {}),
                    _core.jsonb(payload.get("metadata") or {}),
                    payload.get("status") or "registered",
                    float(payload.get("created_at") or 0.0),
                    float(payload.get("updated_at") or 0.0),
                ),
            )
            events = [event for event in payload.get("events", []) if isinstance(event, dict)]
            kept_event_ids = [event["event_id"] for event in events]
            # 只删除已从（裁剪后的）内存集合中移出的事件，而不是每次追加都删光再重插全部
            # 事件。配合下方批量 ON CONFLICT DO NOTHING 插入，把原先的“删全部 + N 次插入”
            # 变为两条语句，并保持未变更的行不动。
            cursor.execute(
                "DELETE FROM portrait_stream_events WHERE tenant_id = %s AND stream_id = %s AND event_id <> ALL(%s)",
                (tenant_id, stream_id, kept_event_ids),
            )
            if events:
                cursor.executemany(
                    """
                    INSERT INTO portrait_stream_events (
                      tenant_id, stream_id, event_id, type, message, payload, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, to_timestamp(%s))
                    ON CONFLICT (tenant_id, stream_id, event_id) DO NOTHING
                    """,
                    [
                        (
                            tenant_id,
                            stream_id,
                            event["event_id"],
                            event.get("type") or "",
                            event.get("message") or "",
                            _core.jsonb(event.get("payload") or {}),
                            float(event.get("created_at") or 0.0),
                        )
                        for event in events
                    ],
                )


def delete_stream(tenant_id: str, stream_id: str) -> None:
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM portrait_streams WHERE tenant_id = %s AND stream_id = %s",
                (tenant_id, stream_id),
            )


def load_streams_snapshot() -> list[dict[str, Any]]:
    if not _core.postgres_configured() or _core.psycopg is None:
        return []
    events_by_stream: dict[tuple[str, str], list[dict[str, Any]]] = {}
    streams = []
    try:
        with _core.postgres_connection(row_factory=_core.dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT tenant_id, stream_id, event_id, type, message, payload,
                           EXTRACT(EPOCH FROM created_at)::double precision AS created_at
                    FROM portrait_stream_events
                    ORDER BY created_at
                    """
                )
                for row in cursor:
                    events_by_stream.setdefault((row["tenant_id"], row["stream_id"]), []).append(
                        {
                            "event_id": row["event_id"],
                            "type": row["type"],
                            "message": row["message"],
                            "payload": row["payload"] if isinstance(row["payload"], dict) else {},
                            "created_at": float(row["created_at"] or 0.0),
                        }
                    )
                cursor.execute(
                    """
                    SELECT tenant_id, stream_id, stream_url_ciphertext, name, settings, metadata, status,
                           EXTRACT(EPOCH FROM created_at)::double precision AS created_at,
                           EXTRACT(EPOCH FROM updated_at)::double precision AS updated_at
                    FROM portrait_streams
                    ORDER BY created_at
                    """
                )
                for row in cursor:
                    try:
                        stream_url = decode_stream_url(bytes(row["stream_url_ciphertext"]))
                    except Exception:
                        stream_url = ""
                    streams.append(
                        {
                            "tenant_id": row["tenant_id"],
                            "stream_id": row["stream_id"],
                            "stream_url": stream_url,
                            "name": row["name"],
                            "settings": row["settings"] if isinstance(row["settings"], dict) else {},
                            "metadata": row["metadata"] if isinstance(row["metadata"], dict) else {},
                            "status": row["status"],
                            "created_at": float(row["created_at"] or 0.0),
                            "updated_at": float(row["updated_at"] or 0.0),
                            "events": events_by_stream.get((row["tenant_id"], row["stream_id"]), []),
                        }
                    )
    except Exception as exc:  # pragma: no cover - requires external database
        logger.warning("postgres stream load failed: %s", exception_log_summary(exc))
        return []
    return streams
