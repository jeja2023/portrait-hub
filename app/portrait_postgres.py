from __future__ import annotations

import threading

from app import postgres_audit as _audit
from app import postgres_core as _core
from app import postgres_gallery as _gallery
from app import postgres_image_results as _image_results
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


def _sync_core_dependencies() -> None:
    _core.POSTGRES_DSN = POSTGRES_DSN
    _core.POSTGRES_CONNECT_TIMEOUT_SECONDS = POSTGRES_CONNECT_TIMEOUT_SECONDS
    _core.POSTGRES_POOL_MIN_SIZE = POSTGRES_POOL_MIN_SIZE
    _core.POSTGRES_POOL_MAX_SIZE = POSTGRES_POOL_MAX_SIZE
    _core.POSTGRES_POOL = POSTGRES_POOL
    _core.psycopg = psycopg
    _core.dict_row = dict_row
    _core.ConnectionPool = ConnectionPool


def postgres_health() -> dict[str, object]:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        return _core.postgres_health()


def load_gallery_snapshot() -> dict[str, object]:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        return _gallery.load_gallery_snapshot()


def upsert_gallery_person(person: dict[str, object]) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _gallery.upsert_gallery_person(person)


def upsert_gallery_feature(tenant_id: str, person_id: str, feature: dict[str, object]) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _gallery.upsert_gallery_feature(tenant_id, person_id, feature)


def delete_gallery_person(tenant_id: str, person_id: str) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _gallery.delete_gallery_person(tenant_id, person_id)


def replace_gallery_snapshot(snapshot: dict[str, object]) -> None:
    with _CORE_DEPENDENCY_LOCK:
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
    with _CORE_DEPENDENCY_LOCK:
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
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        return _thresholds.load_threshold_snapshot()


def save_threshold_snapshot(thresholds: dict[str, object]) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _thresholds.save_threshold_snapshot(thresholds)


def insert_audit_event(payload: dict[str, object]) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _audit.insert_audit_event(payload)


def upsert_image_analysis_result(
    payload: dict[str, object], *, max_results: int
) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _image_results.upsert_image_analysis_result(
            payload, max_results=max_results
        )


def load_image_analysis_results_snapshot() -> list[dict[str, object]]:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        return _image_results.load_image_analysis_results_snapshot()


def upsert_video_job(payload: dict[str, object]) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _jobs.upsert_video_job(payload)


def delete_video_job(tenant_id: str, job_id: str) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _jobs.delete_video_job(tenant_id, job_id)


def load_video_jobs_snapshot() -> list[dict[str, object]]:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        return _jobs.load_video_jobs_snapshot()


def upsert_stream(payload: dict[str, object]) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _streams.upsert_stream(payload)


def delete_stream(tenant_id: str, stream_id: str) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _streams.delete_stream(tenant_id, stream_id)


def load_streams_snapshot() -> list[dict[str, object]]:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        return _streams.load_streams_snapshot()


def insert_object_record(tenant_id: str, info: dict[str, object], metadata: dict[str, object] | None = None) -> None:
    with _CORE_DEPENDENCY_LOCK:
        _sync_core_dependencies()
        _objects.insert_object_record(tenant_id, info, metadata)


__all__ = [
    "ConnectionPool",
    "PostgresUnavailable",
    "POSTGRES_CONNECT_TIMEOUT_SECONDS",
    "POSTGRES_DSN",
    "POSTGRES_POOL",
    "POSTGRES_POOL_MAX_SIZE",
    "POSTGRES_POOL_MIN_SIZE",
    "delete_gallery_person",
    "delete_stream",
    "delete_video_job",
    "dict_row",
    "embedding_bytes",
    "get_postgres_pool",
    "insert_audit_event",
    "insert_object_record",
    "jsonb",
    "load_gallery_snapshot",
    "load_image_analysis_results_snapshot",
    "load_streams_snapshot",
    "load_threshold_snapshot",
    "load_video_jobs_snapshot",
    "normalized_embedding",
    "postgres_configured",
    "postgres_connection",
    "postgres_driver_available",
    "postgres_health",
    "postgres_pool_available",
    "replace_gallery_snapshot",
    "require_postgres",
    "save_threshold_snapshot",
    "search_pgvector",
    "upsert_gallery_feature",
    "upsert_gallery_person",
    "upsert_image_analysis_result",
    "upsert_stream",
    "upsert_video_job",
    "vector_literal",
]
