from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from app.observability import logger, wall_time
from app.portrait_object_storage import OBJECT_STORE
from app.portrait_pagination import decode_cursor, encode_cursor
from app.portrait_response import exception_log_summary
from app.settings import (
    ANALYSIS_ARCHIVE_ENABLED,
    ANALYSIS_ARCHIVE_PREVIEW_MAX_SIDE,
    PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH,
    PORTRAIT_STORAGE_BACKEND,
)

ARCHIVE_SOURCE_TYPES = {"image", "video", "stream"}
ARCHIVE_IMAGE_FIELDS = ("thumbnail", "image", "preview")


@dataclass
class AnalysisArtifact:
    artifact_id: str
    role: str
    media_type: str
    object_info: dict[str, Any]
    preview_object_info: dict[str, Any]
    frame_index: int
    width: int
    height: int

    def state_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "role": self.role,
            "media_type": self.media_type,
            "object_info": deepcopy(self.object_info),
            "preview_object_info": deepcopy(self.preview_object_info),
            "frame_index": self.frame_index,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> AnalysisArtifact:
        return cls(
            artifact_id=str(payload["artifact_id"]),
            role=str(payload.get("role") or "result_image"),
            media_type=str(payload.get("media_type") or "image/jpeg"),
            object_info=deepcopy(payload.get("object_info") or {}),
            preview_object_info=deepcopy(payload.get("preview_object_info") or {}),
            frame_index=max(0, int(payload.get("frame_index") or 0)),
            width=max(1, int(payload.get("width") or 1)),
            height=max(1, int(payload.get("height") or 1)),
        )


@dataclass
class AnalysisArchiveRecord:
    archive_id: str
    tenant_id: str
    request_id: str
    source_type: str
    source_ref: str
    mode: str
    endpoint: str
    payload: dict[str, Any]
    artifacts: list[AnalysisArtifact] = field(default_factory=list)
    created_at: float = field(default_factory=wall_time)

    def state_dict(self) -> dict[str, Any]:
        return {
            "archive_id": self.archive_id,
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "mode": self.mode,
            "endpoint": self.endpoint,
            "payload": deepcopy(self.payload),
            "artifacts": [artifact.state_dict() for artifact in self.artifacts],
            "created_at": self.created_at,
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> AnalysisArchiveRecord:
        raw_artifacts = payload.get("artifacts")
        return cls(
            archive_id=str(payload["archive_id"]),
            tenant_id=str(payload.get("tenant_id") or "default"),
            request_id=str(payload.get("request_id") or ""),
            source_type=str(payload.get("source_type") or "image"),
            source_ref=str(payload.get("source_ref") or ""),
            mode=str(payload.get("mode") or "analysis"),
            endpoint=str(payload.get("endpoint") or ""),
            payload=deepcopy(payload.get("payload") or {}),
            artifacts=[
                AnalysisArtifact.from_state(item)
                for item in (raw_artifacts if isinstance(raw_artifacts, list) else [])
                if isinstance(item, dict) and item.get("artifact_id")
            ],
            created_at=float(payload.get("created_at") or wall_time()),
        )


def postgres_archive_enabled() -> bool:
    return PORTRAIT_STORAGE_BACKEND == "postgres"


@contextmanager
def local_archive_connection() -> Iterator[sqlite3.Connection]:
    PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        ensure_local_archive_schema(connection)
        yield connection
        connection.commit()
    finally:
        connection.close()


def ensure_local_archive_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    if connection is None:
        PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(PORTRAIT_ANALYSIS_ARCHIVE_DB_PATH, timeout=30.0)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS analysis_archives (
              tenant_id TEXT NOT NULL,
              archive_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              source_type TEXT NOT NULL,
              source_ref TEXT NOT NULL,
              mode TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              artifacts_json TEXT NOT NULL,
              created_at REAL NOT NULL,
              PRIMARY KEY (tenant_id, archive_id)
            );
            CREATE INDEX IF NOT EXISTS analysis_archives_tenant_source_created_idx
              ON analysis_archives (tenant_id, source_type, created_at DESC, archive_id);
            CREATE INDEX IF NOT EXISTS analysis_archives_tenant_mode_created_idx
              ON analysis_archives (tenant_id, mode, created_at DESC, archive_id);
            """
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _record_from_sqlite_row(row: sqlite3.Row) -> AnalysisArchiveRecord:
    return AnalysisArchiveRecord.from_state(
        {
            "tenant_id": row["tenant_id"],
            "archive_id": row["archive_id"],
            "request_id": row["request_id"],
            "source_type": row["source_type"],
            "source_ref": row["source_ref"],
            "mode": row["mode"],
            "endpoint": row["endpoint"],
            "payload": json.loads(row["payload_json"]),
            "artifacts": json.loads(row["artifacts_json"]),
            "created_at": float(row["created_at"]),
        }
    )


def _image_bytes(image: Image.Image, *, max_side: int | None, quality: int) -> bytes:
    output = image.convert("RGB")
    if max_side is not None:
        output.thumbnail((max(1, max_side), max(1, max_side)))
    buffer = BytesIO()
    output.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _data_url_bytes(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value.startswith("data:image/") or "," not in value:
        return None
    try:
        return base64.b64decode(value.split(",", 1)[1], validate=True)
    except Exception:
        return None


def _image_from_bytes(data: bytes) -> Image.Image | None:
    try:
        image = Image.open(BytesIO(data))
        image.load()
        return image
    except Exception:
        return None


def _payload_images(payload: dict[str, Any]) -> list[tuple[int, Image.Image]]:
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return []
    images: list[tuple[int, Image.Image]] = []
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            continue
        data = next(
            (
                decoded
                for key in ARCHIVE_IMAGE_FIELDS
                for decoded in [_data_url_bytes(frame.get(key))]
                if decoded is not None
            ),
            None,
        )
        image = _image_from_bytes(data) if data is not None else None
        if image is not None:
            images.append((index, image))
    return images


def payload_without_embedded_images(payload: dict[str, Any]) -> dict[str, Any]:
    archived = deepcopy(payload)
    frames = archived.get("frames")
    if isinstance(frames, list):
        for frame in frames:
            if isinstance(frame, dict):
                for key in ARCHIVE_IMAGE_FIELDS:
                    frame.pop(key, None)
    return archived


def _store_artifact(
    tenant_id: str,
    archive_id: str,
    image: Image.Image,
    frame_index: int,
) -> AnalysisArtifact:
    full_bytes = _image_bytes(image, max_side=None, quality=90)
    preview_bytes = _image_bytes(
        image,
        max_side=max(1, int(ANALYSIS_ARCHIVE_PREVIEW_MAX_SIDE)),
        quality=78,
    )
    filename = f"{archive_id}-{frame_index + 1}.jpg"
    object_info = OBJECT_STORE.put_bytes(
        tenant_id, "analysis-result-image", filename, full_bytes
    )
    preview_object_info = OBJECT_STORE.put_bytes(
        tenant_id, "analysis-result-preview", filename, preview_bytes
    )
    return AnalysisArtifact(
        artifact_id=f"artifact_{uuid4().hex[:16]}",
        role="result_image",
        media_type="image/jpeg",
        object_info=object_info,
        preview_object_info=preview_object_info,
        frame_index=frame_index,
        width=image.width,
        height=image.height,
    )


def _store_source_artifact(
    tenant_id: str,
    archive_id: str,
    data: bytes,
    filename: str | None,
    media_type: str,
) -> AnalysisArtifact:
    object_info = OBJECT_STORE.put_bytes(
        tenant_id,
        "analysis-source",
        filename or f"{archive_id}.bin",
        data,
    )
    return AnalysisArtifact(
        artifact_id=f"artifact_{uuid4().hex[:16]}",
        role="source",
        media_type=media_type,
        object_info=object_info,
        preview_object_info={},
        frame_index=0,
        width=1,
        height=1,
    )


def store_analysis_source_file(
    tenant_id: str,
    archive_id: str,
    path: str | Path,
    filename: str | None,
    media_type: str,
) -> AnalysisArtifact:
    return _store_source_artifact(
        tenant_id,
        archive_id,
        Path(path).read_bytes(),
        filename,
        media_type,
    )


def persist_analysis_archive(record: AnalysisArchiveRecord) -> None:
    if postgres_archive_enabled():
        from app.portrait_postgres import upsert_analysis_archive

        upsert_analysis_archive(record.state_dict())
        return
    with local_archive_connection() as connection:
        connection.execute(
            """
            INSERT INTO analysis_archives (
              tenant_id, archive_id, request_id, source_type, source_ref,
              mode, endpoint, payload_json, artifacts_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, archive_id) DO UPDATE SET
              request_id = excluded.request_id,
              source_type = excluded.source_type,
              source_ref = excluded.source_ref,
              mode = excluded.mode,
              endpoint = excluded.endpoint,
              payload_json = excluded.payload_json,
              artifacts_json = excluded.artifacts_json,
              created_at = excluded.created_at
            """,
            (
                record.tenant_id,
                record.archive_id,
                record.request_id,
                record.source_type,
                record.source_ref,
                record.mode,
                record.endpoint,
                json.dumps(record.payload, ensure_ascii=False, separators=(",", ":")),
                json.dumps(
                    [artifact.state_dict() for artifact in record.artifacts],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                record.created_at,
            ),
        )


def create_analysis_archive(
    *,
    tenant_id: str,
    request_id: str,
    source_type: str,
    source_ref: str,
    mode: str,
    endpoint: str,
    payload: dict[str, Any],
    images: list[Any] | None = None,
    source_artifacts: list[AnalysisArtifact] | None = None,
    archive_id: str | None = None,
) -> AnalysisArchiveRecord | None:
    if not ANALYSIS_ARCHIVE_ENABLED:
        return None
    normalized_source_type = str(source_type).strip().lower()
    if normalized_source_type not in ARCHIVE_SOURCE_TYPES:
        raise ValueError("不支持的解析档案来源类型")
    record_id = archive_id or f"archive_{uuid4().hex[:16]}"
    source_images = [
        (index, image)
        for index, image in enumerate(images or [])
        if isinstance(image, Image.Image)
    ]
    if not source_images:
        source_images = _payload_images(payload)
    artifacts = [deepcopy(artifact) for artifact in source_artifacts or []]
    artifacts.extend(
        _store_artifact(tenant_id, record_id, image, frame_index)
        for frame_index, image in source_images
    )
    record = AnalysisArchiveRecord(
        archive_id=record_id,
        tenant_id=str(tenant_id),
        request_id=str(request_id),
        source_type=normalized_source_type,
        source_ref=str(source_ref),
        mode=str(mode),
        endpoint=str(endpoint),
        payload=payload_without_embedded_images(payload),
        artifacts=artifacts,
    )
    if postgres_archive_enabled():
        persist_analysis_archive(record)
        return deepcopy(record)
    persist_analysis_archive(record)
    return deepcopy(record)


def load_analysis_archives_state() -> None:
    if ANALYSIS_ARCHIVE_ENABLED and not postgres_archive_enabled():
        ensure_local_archive_schema()


def list_analysis_archives(
    tenant_id: str,
    *,
    source_type: str | None,
    mode: str | None,
    limit: int,
    offset: int,
    cursor: str | None,
) -> tuple[list[AnalysisArchiveRecord], dict[str, Any]]:
    if postgres_archive_enabled():
        from app.portrait_postgres import query_analysis_archives

        records, pagination = query_analysis_archives(
            tenant_id,
            source_type=source_type,
            mode=mode,
            limit=limit,
            offset=offset,
            cursor_values=decode_cursor(cursor),
        )
        pagination["cursor"] = cursor
        has_more = bool(pagination.get("has_more", False))
        if has_more and records:
            last = records[-1]
            pagination["next_cursor"] = encode_cursor(
                [-float(last.get("created_at") or 0.0), str(last.get("archive_id") or "")]
            )
        else:
            pagination["next_cursor"] = None
        pagination["has_more"] = has_more
        return [AnalysisArchiveRecord.from_state(item) for item in records], pagination
    cursor_values = decode_cursor(cursor)
    filters = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if source_type is not None:
        filters.append("source_type = ?")
        params.append(source_type)
    if mode is not None:
        filters.append("mode = ?")
        params.append(mode)
    count_where = " AND ".join(filters)
    query_filters = list(filters)
    query_params = list(params)
    if cursor_values is not None:
        if len(cursor_values) != 2:
            raise ValueError("解析档案游标无效")
        created_at = -float(cursor_values[0])
        archive_id = str(cursor_values[1])
        query_filters.append(
            "(created_at < ? OR (created_at = ? AND archive_id > ?))"
        )
        query_params.extend([created_at, created_at, archive_id])
    query_where = " AND ".join(query_filters)
    with local_archive_connection() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM analysis_archives WHERE {count_where}",
                params,
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT tenant_id, archive_id, request_id, source_type, source_ref,
                   mode, endpoint, payload_json, artifacts_json, created_at
            FROM analysis_archives
            WHERE {query_where}
            ORDER BY created_at DESC, archive_id
            LIMIT ? OFFSET ?
            """,
            [*query_params, limit + 1, offset],
        ).fetchall()
    has_more = len(rows) > limit
    records = [_record_from_sqlite_row(row) for row in rows[:limit]]
    next_cursor = (
        encode_cursor([-records[-1].created_at, records[-1].archive_id])
        if has_more and records
        else None
    )
    return records, {
        "count": len(records),
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": None if cursor is not None or not has_more else offset + len(records),
        "cursor": cursor,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def _preview_data_url(artifact: AnalysisArtifact) -> str | None:
    try:
        data = OBJECT_STORE.get_bytes(artifact.preview_object_info)
    except Exception as exc:
        logger.warning(
            "analysis archive preview read failed: %s", exception_log_summary(exc)
        )
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{artifact.media_type};base64,{encoded}"


def public_analysis_archive(record: AnalysisArchiveRecord) -> dict[str, Any]:
    payload = deepcopy(record.payload)
    previews: list[dict[str, Any]] = []
    preview_by_frame: dict[int, str] = {}
    for artifact in sorted(record.artifacts, key=lambda item: item.frame_index):
        if artifact.role != "result_image":
            continue
        source = _preview_data_url(artifact)
        if source is None:
            continue
        preview_by_frame[artifact.frame_index] = source
        previews.append(
            {
                "artifact_id": artifact.artifact_id,
                "name": f"image-{artifact.frame_index + 1}",
                "label": f"{artifact.frame_index + 1}. image-{artifact.frame_index + 1}",
                "src": source,
                "width": artifact.width,
                "height": artifact.height,
                "content_url": f"/v1/analysis/artifacts/{record.archive_id}/{artifact.artifact_id}",
            }
        )
    frames = payload.get("frames")
    if isinstance(frames, list):
        for index, frame in enumerate(frames):
            if isinstance(frame, dict) and index in preview_by_frame:
                frame["thumbnail"] = preview_by_frame[index]
    source_artifacts = [
        {
            "artifact_id": artifact.artifact_id,
            "role": artifact.role,
            "media_type": artifact.media_type,
            "content_url": f"/v1/analysis/artifacts/{record.archive_id}/{artifact.artifact_id}",
        }
        for artifact in record.artifacts
        if artifact.role == "source"
    ]
    return {
        "archive_id": record.archive_id,
        "result_id": record.archive_id,
        "request_id": record.request_id,
        "source_type": record.source_type,
        "source_ref": record.source_ref,
        "mode": record.mode,
        "endpoint": record.endpoint,
        "payload": payload,
        "previews": previews,
        "artifact_count": len(record.artifacts),
        "source_artifacts": source_artifacts,
        "created_at": record.created_at,
    }


def get_analysis_artifact(
    tenant_id: str, archive_id: str, artifact_id: str
) -> AnalysisArtifact | None:
    if postgres_archive_enabled():
        from app.portrait_postgres import get_analysis_archive

        payload = get_analysis_archive(tenant_id, archive_id)
        record = AnalysisArchiveRecord.from_state(payload) if payload else None
    else:
        with local_archive_connection() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, archive_id, request_id, source_type, source_ref,
                       mode, endpoint, payload_json, artifacts_json, created_at
                FROM analysis_archives
                WHERE tenant_id = ? AND archive_id = ?
                """,
                (tenant_id, archive_id),
            ).fetchone()
        record = _record_from_sqlite_row(row) if row is not None else None
    if record is None:
        return None
    return next(
        (artifact for artifact in record.artifacts if artifact.artifact_id == artifact_id),
        None,
    )


__all__ = [
    "AnalysisArchiveRecord",
    "AnalysisArtifact",
    "create_analysis_archive",
    "get_analysis_artifact",
    "list_analysis_archives",
    "load_analysis_archives_state",
    "payload_without_embedded_images",
    "public_analysis_archive",
    "store_analysis_source_file",
]
