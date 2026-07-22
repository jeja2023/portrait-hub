from __future__ import annotations

import threading
from typing import Any

from app import postgres_access as _access
from app import postgres_analysis_archive as _analysis_archive
from app import postgres_audit as _audit
from app import postgres_call_logs as _call_logs
from app import postgres_core as _core
from app import postgres_gallery as _gallery
from app import postgres_jobs as _jobs
from app import postgres_objects as _objects
from app import postgres_streams as _streams
from app import postgres_thresholds as _thresholds
from app.postgres_core import (  # 重新导出以实现向后兼容的导入
    PostgresUnavailable,
    embedding_bytes,
    get_postgres_pool,
    jsonb,
    normalized_embedding,
    postgres_configured,
    postgres_connection,
    postgres_driver_available,
    postgres_pool_available,
    require_postgres,
    vector_literal,
)

POSTGRES_DSN = _core.POSTGRES_DSN
POSTGRES_CONNECT_TIMEOUT_SECONDS = _core.POSTGRES_CONNECT_TIMEOUT_SECONDS
POSTGRES_POOL_MIN_SIZE = _core.POSTGRES_POOL_MIN_SIZE
POSTGRES_POOL_MAX_SIZE = _core.POSTGRES_POOL_MAX_SIZE
POSTGRES_POOL = _core.POSTGRES_POOL
psycopg = _core.psycopg
dict_row = _core.dict_row
ConnectionPool = _core.ConnectionPool
_CORE_DEPENDENCY_LOCK = threading.RLock()


def _core_dependencies_in_sync() -> bool:
    return (
        _core.POSTGRES_DSN is POSTGRES_DSN
        and _core.POSTGRES_CONNECT_TIMEOUT_SECONDS is POSTGRES_CONNECT_TIMEOUT_SECONDS
        and _core.POSTGRES_POOL_MIN_SIZE is POSTGRES_POOL_MIN_SIZE
        and _core.POSTGRES_POOL_MAX_SIZE is POSTGRES_POOL_MAX_SIZE
        and _core.psycopg is psycopg
        and _core.dict_row is dict_row
        and _core.ConnectionPool is ConnectionPool
    )


def _sync_core_dependencies() -> None:
    # 快路径：门面属性未被外部改写（测试 monkeypatch / 配置热更新）时直接返回，
    # 不抢全局锁，避免把连接池的并发能力串行化到一把进程级锁上。
    if _core_dependencies_in_sync():
        return
    with _CORE_DEPENDENCY_LOCK:
        if _core_dependencies_in_sync():
            return
        _core.POSTGRES_DSN = POSTGRES_DSN
        _core.POSTGRES_CONNECT_TIMEOUT_SECONDS = POSTGRES_CONNECT_TIMEOUT_SECONDS
        _core.POSTGRES_POOL_MIN_SIZE = POSTGRES_POOL_MIN_SIZE
        _core.POSTGRES_POOL_MAX_SIZE = POSTGRES_POOL_MAX_SIZE
        # 门面配置变化意味着连接目标变了，重置核心缓存的连接池让其按新配置重建
        _core.POSTGRES_POOL = POSTGRES_POOL
        _core.psycopg = psycopg
        _core.dict_row = dict_row
        _core.ConnectionPool = ConnectionPool


def postgres_health() -> dict[str, object]:
    _sync_core_dependencies()
    return _core.postgres_health()


def consume_application_daily_quota(
    tenant_id: str,
    application_id: str,
    quota_date: str,
    daily_quota: int,
) -> int | None:
    _sync_core_dependencies()
    return _access.consume_application_daily_quota(
        tenant_id,
        application_id,
        quota_date,
        daily_quota,
    )


def insert_call_log(payload: dict[str, Any]) -> None:
    _sync_core_dependencies()
    _call_logs.insert_call_log(payload)


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
    _sync_core_dependencies()
    return _call_logs.query_call_logs(
        tenant_id,
        request_id=request_id,
        project_id=project_id,
        endpoint=endpoint,
        status_text=status_text,
        application_id=application_id,
        error_code=error_code,
        created_since=created_since,
        created_until=created_until,
        limit=limit,
    )


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
    _sync_core_dependencies()
    return _call_logs.summarize_call_logs(
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


def application_usage_summaries(
    tenant_id: str,
    *,
    project_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    _sync_core_dependencies()
    return _call_logs.application_usage_summaries(tenant_id, project_id=project_id)


def load_access_snapshot() -> tuple[dict[str, Any], int]:
    _sync_core_dependencies()
    return _access.load_access_snapshot()


def save_access_snapshot(payload: dict[str, Any], expected_revision: int) -> int:
    _sync_core_dependencies()
    return _access.save_access_snapshot(payload, expected_revision)


def load_gallery_snapshot() -> dict[str, object]:
    _sync_core_dependencies()
    return _gallery.load_gallery_snapshot()


def upsert_gallery_person(person: dict[str, object]) -> None:
    _sync_core_dependencies()
    _gallery.upsert_gallery_person(person)


def upsert_gallery_feature(tenant_id: str, person_id: str, feature: dict[str, object]) -> None:
    _sync_core_dependencies()
    _gallery.upsert_gallery_feature(tenant_id, person_id, feature)


def delete_gallery_person(tenant_id: str, person_id: str) -> None:
    _sync_core_dependencies()
    _gallery.delete_gallery_person(tenant_id, person_id)


def replace_gallery_snapshot(snapshot: dict[str, object]) -> None:
    _sync_core_dependencies()
    _gallery.replace_gallery_snapshot(snapshot)


def search_pgvector(
    query_embedding: list[float],
    *,
    modality: str,
    threshold: float,
    threshold_profile: str,
    top_k: int,
    tenant_id: str,
) -> list[dict[str, object]]:
    _sync_core_dependencies()
    return _gallery.search_pgvector(
        query_embedding,
        modality=modality,
        threshold=threshold,
        threshold_profile=threshold_profile,
        top_k=top_k,
        tenant_id=tenant_id,
    )


def load_threshold_snapshot() -> dict[str, object]:
    _sync_core_dependencies()
    return _thresholds.load_threshold_snapshot()


def save_threshold_snapshot(thresholds: dict[str, object]) -> None:
    _sync_core_dependencies()
    _thresholds.save_threshold_snapshot(thresholds)


def insert_audit_event(payload: dict[str, object]) -> None:
    _sync_core_dependencies()
    _audit.insert_audit_event(payload)


def upsert_analysis_archive(payload: dict[str, object]) -> None:
    _sync_core_dependencies()
    _analysis_archive.upsert_analysis_archive(payload)


def query_analysis_archives(
    tenant_id: str,
    *,
    source_type: str | None,
    mode: str | None,
    limit: int,
    offset: int,
    cursor_values: list[object] | None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    _sync_core_dependencies()
    return _analysis_archive.query_analysis_archives(
        tenant_id,
        source_type=source_type,
        mode=mode,
        limit=limit,
        offset=offset,
        cursor_values=cursor_values,
    )


def get_analysis_archive(tenant_id: str, archive_id: str) -> dict[str, object] | None:
    _sync_core_dependencies()
    return _analysis_archive.get_analysis_archive(tenant_id, archive_id)


def upsert_video_job(payload: dict[str, object]) -> None:
    _sync_core_dependencies()
    _jobs.upsert_video_job(payload)


def delete_video_job(tenant_id: str, job_id: str) -> None:
    _sync_core_dependencies()
    _jobs.delete_video_job(tenant_id, job_id)


def load_video_jobs_snapshot() -> list[dict[str, object]]:
    _sync_core_dependencies()
    return _jobs.load_video_jobs_snapshot()


def load_video_job_record(tenant_id: str, job_id: str) -> dict[str, object] | None:
    _sync_core_dependencies()
    return _jobs.load_video_job_record(tenant_id, job_id)


def upsert_stream(payload: dict[str, object]) -> None:
    _sync_core_dependencies()
    _streams.upsert_stream(payload)


def delete_stream(tenant_id: str, stream_id: str) -> None:
    _sync_core_dependencies()
    _streams.delete_stream(tenant_id, stream_id)


def load_streams_snapshot() -> list[dict[str, object]]:
    _sync_core_dependencies()
    return _streams.load_streams_snapshot()


def insert_object_record(tenant_id: str, info: dict[str, object], metadata: dict[str, object] | None = None) -> None:
    _sync_core_dependencies()
    _objects.insert_object_record(tenant_id, info, metadata)


__all__ = [
    "POSTGRES_CONNECT_TIMEOUT_SECONDS",
    "POSTGRES_DSN",
    "POSTGRES_POOL",
    "POSTGRES_POOL_MAX_SIZE",
    "POSTGRES_POOL_MIN_SIZE",
    "ConnectionPool",
    "PostgresUnavailable",
    "application_usage_summaries",
    "consume_application_daily_quota",
    "delete_gallery_person",
    "delete_stream",
    "delete_video_job",
    "dict_row",
    "embedding_bytes",
    "get_analysis_archive",
    "get_postgres_pool",
    "insert_audit_event",
    "insert_call_log",
    "insert_object_record",
    "jsonb",
    "load_access_snapshot",
    "load_gallery_snapshot",
    "load_streams_snapshot",
    "load_threshold_snapshot",
    "load_video_job_record",
    "load_video_jobs_snapshot",
    "normalized_embedding",
    "postgres_configured",
    "postgres_connection",
    "postgres_driver_available",
    "postgres_health",
    "postgres_pool_available",
    "query_analysis_archives",
    "query_call_logs",
    "replace_gallery_snapshot",
    "require_postgres",
    "save_access_snapshot",
    "save_threshold_snapshot",
    "search_pgvector",
    "summarize_call_logs",
    "upsert_analysis_archive",
    "upsert_gallery_feature",
    "upsert_gallery_person",
    "upsert_stream",
    "upsert_video_job",
    "vector_literal",
]
