from __future__ import annotations

from typing import Any

from app import postgres_core as _core


def insert_object_record(tenant_id: str, info: dict[str, Any], metadata: dict[str, Any] | None = None) -> None:
    with _core.postgres_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                INSERT INTO portrait_objects (
                  tenant_id, object_key, backend, bucket, sha256, bytes, encrypted, metadata, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (tenant_id, object_key) DO UPDATE SET
                  backend = EXCLUDED.backend,
                  bucket = EXCLUDED.bucket,
                  sha256 = EXCLUDED.sha256,
                  bytes = EXCLUDED.bytes,
                  encrypted = EXCLUDED.encrypted,
                  metadata = EXCLUDED.metadata
                """,
            (
                tenant_id,
                info.get("object_key") or "",
                info.get("backend") or "",
                info.get("bucket"),
                info.get("sha256") or "",
                int(info.get("bytes") or 0),
                bool(info.get("encrypted", False)),
                _core.jsonb(metadata or {}),
            ),
        )

