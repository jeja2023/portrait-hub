from __future__ import annotations

import json
import math
from contextlib import contextmanager
from typing import Any, Iterator

from app.observability import logger, trace_span
from app.portrait_crypto import decrypt_bytes, encrypt_bytes
from app.portrait_response import HEALTH_CHECK_FAILED, exception_log_summary
from app.settings import POSTGRES_CONNECT_TIMEOUT_SECONDS, POSTGRES_DSN, POSTGRES_POOL_MAX_SIZE, POSTGRES_POOL_MIN_SIZE

try:  # pragma: no cover - optional production dependency
    import psycopg  # type: ignore[import-not-found]  # optional, from requirements-prod-optional.txt
    from psycopg.rows import dict_row  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised when dependency is absent
    psycopg = None
    dict_row = None

try:  # pragma: no cover - optional production dependency
    from psycopg_pool import ConnectionPool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised when dependency is absent
    ConnectionPool = None


class PostgresUnavailable(RuntimeError):
    pass


POSTGRES_POOL: Any | None = None


def postgres_configured() -> bool:
    return bool(POSTGRES_DSN.strip())


def postgres_driver_available() -> bool:
    return psycopg is not None


def postgres_pool_available() -> bool:
    return ConnectionPool is not None


def require_postgres() -> None:
    if not postgres_configured():
        raise PostgresUnavailable("POSTGRES_DSN is not configured")
    if psycopg is None:
        raise PostgresUnavailable("psycopg is not installed; install requirements-prod-optional.txt")


def get_postgres_pool() -> Any | None:
    global POSTGRES_POOL
    if not postgres_configured() or psycopg is None or ConnectionPool is None:
        return None
    if POSTGRES_POOL is None:
        POSTGRES_POOL = ConnectionPool(
            conninfo=POSTGRES_DSN,
            min_size=max(0, int(POSTGRES_POOL_MIN_SIZE)),
            max_size=max(1, int(POSTGRES_POOL_MAX_SIZE)),
            timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
            kwargs={"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS},
            open=False,
        )
    return POSTGRES_POOL


@contextmanager
def postgres_connection(row_factory: Any = None) -> Iterator[Any]:
    require_postgres()
    pool = get_postgres_pool()
    if pool is not None:
        with trace_span("portrait.postgres.connection", pooled=True):
            with pool.connection() as connection:
                previous_row_factory = getattr(connection, "row_factory", None)
                if row_factory is not None:
                    connection.row_factory = row_factory
                try:
                    yield connection
                finally:
                    if row_factory is not None:
                        connection.row_factory = previous_row_factory
        return

    kwargs: dict[str, Any] = {"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS}
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    with trace_span("portrait.postgres.connection", pooled=False):
        with psycopg.connect(POSTGRES_DSN, **kwargs) as connection:
            yield connection


def postgres_health() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "configured": postgres_configured(),
        "driver_available": postgres_driver_available(),
        "pool_driver_available": postgres_pool_available(),
        "pool_enabled": postgres_pool_available() and postgres_configured(),
        "pool_min_size": POSTGRES_POOL_MIN_SIZE,
        "pool_max_size": POSTGRES_POOL_MAX_SIZE,
        "connect_timeout_seconds": POSTGRES_CONNECT_TIMEOUT_SECONDS,
    }
    if not postgres_configured() or psycopg is None:
        return {**payload, "status": "not_ready"}
    try:
        with trace_span("portrait.postgres.health"):
            with postgres_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
        return {**payload, "status": "ready"}
    except Exception as exc:  # pragma: no cover - requires external database
        logger.warning("postgres health check failed: %s", exception_log_summary(exc))
        return {**payload, "status": "error", "error": HEALTH_CHECK_FAILED}


def jsonb(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def normalized_embedding(values: list[float]) -> list[float]:
    normalized: list[float] = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            number = 0.0
        normalized.append(number)
    return normalized


def embedding_bytes(values: list[float]) -> bytes:
    return json.dumps(normalized_embedding(values), separators=(",", ":")).encode("utf-8")


def vector_literal(values: list[float]) -> str:
    numbers = normalized_embedding(values)
    return "[" + ",".join(format(value, ".8g") for value in numbers) + "]"


def load_gallery_snapshot() -> dict[str, Any]:
    if not postgres_configured() or psycopg is None:
        logger.warning("postgres gallery load skipped because PostgreSQL is not ready")
        return {"people": []}

    people: dict[tuple[str, str], dict[str, Any]] = {}
    query = """
        SELECT
          p.tenant_id,
          p.person_id,
          p.display_name,
          p.metadata,
          EXTRACT(EPOCH FROM p.created_at)::double precision AS person_created_at,
          EXTRACT(EPOCH FROM p.updated_at)::double precision AS person_updated_at,
          f.feature_id,
          f.modality,
          f.model_id,
          f.model_version,
          f.embedding_dim,
          f.embedding_json,
          f.quality_score,
          f.source_id,
          f.object_info,
          EXTRACT(EPOCH FROM f.created_at)::double precision AS feature_created_at
        FROM portrait_people p
        LEFT JOIN portrait_features f
          ON f.tenant_id = p.tenant_id AND f.person_id = p.person_id
        ORDER BY p.tenant_id, p.person_id, f.created_at, f.feature_id
    """
    try:
        with trace_span("portrait.postgres.load_gallery_snapshot"):
            with postgres_connection(row_factory=dict_row) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query)
                    rows = cursor.fetchall()
    except Exception as exc:  # pragma: no cover - requires external database
        logger.warning("postgres gallery load failed: %s", exception_log_summary(exc))
        return {"people": []}

    for row in rows:
        key = (str(row["tenant_id"]), str(row["person_id"]))
        person = people.setdefault(
            key,
            {
                "tenant_id": row["tenant_id"],
                "person_id": row["person_id"],
                "display_name": row["display_name"],
                "metadata": row["metadata"] if isinstance(row["metadata"], dict) else {},
                "created_at": float(row["person_created_at"] or 0.0),
                "updated_at": float(row["person_updated_at"] or 0.0),
                "features": [],
            },
        )
        if row["feature_id"] is None:
            continue
        embedding = row["embedding_json"] if isinstance(row["embedding_json"], list) else []
        person["features"].append(
            {
                "feature_id": row["feature_id"],
                "modality": row["modality"],
                "embedding": [float(value) for value in embedding],
                "embedding_dim": int(row["embedding_dim"] or len(embedding)),
                "model_id": row["model_id"],
                "model_version": row["model_version"],
                "quality_score": float(row["quality_score"] or 0.0),
                "source_id": row["source_id"],
                "object_info": row["object_info"] if isinstance(row["object_info"], dict) else {},
                "created_at": float(row["feature_created_at"] or 0.0),
            }
        )
    return {"version": 1, "people": list(people.values())}


UPSERT_PERSON_SQL = """
        INSERT INTO portrait_people (
          tenant_id, person_id, display_name, metadata, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s::jsonb, to_timestamp(%s), to_timestamp(%s))
        ON CONFLICT (tenant_id, person_id) DO UPDATE SET
          display_name = EXCLUDED.display_name,
          metadata = EXCLUDED.metadata,
          updated_at = EXCLUDED.updated_at
    """


UPSERT_FEATURE_SQL = """
        INSERT INTO portrait_features (
          tenant_id, person_id, feature_id, modality, model_id, model_version,
          embedding_dim, embedding, embedding_json, embedding_vector,
          quality_score, source_id, object_info, created_at
        )
        VALUES (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s::jsonb, %s::vector,
          %s, %s, %s::jsonb, to_timestamp(%s)
        )
        ON CONFLICT (tenant_id, feature_id) DO UPDATE SET
          person_id = EXCLUDED.person_id,
          modality = EXCLUDED.modality,
          model_id = EXCLUDED.model_id,
          model_version = EXCLUDED.model_version,
          embedding_dim = EXCLUDED.embedding_dim,
          embedding = EXCLUDED.embedding,
          embedding_json = EXCLUDED.embedding_json,
          embedding_vector = EXCLUDED.embedding_vector,
          quality_score = EXCLUDED.quality_score,
          source_id = EXCLUDED.source_id,
          object_info = EXCLUDED.object_info
    """


def _execute_upsert_gallery_person(cursor: Any, person: dict[str, Any]) -> None:
    cursor.execute(
        UPSERT_PERSON_SQL,
        (
            person["tenant_id"],
            person["person_id"],
            person.get("display_name"),
            jsonb(person.get("metadata") or {}),
            float(person.get("created_at") or 0.0),
            float(person.get("updated_at") or 0.0),
        ),
    )


def _execute_upsert_gallery_feature(cursor: Any, tenant_id: str, person_id: str, feature: dict[str, Any]) -> None:
    embedding = normalized_embedding(feature.get("embedding") or [])
    cursor.execute(
        UPSERT_FEATURE_SQL,
        (
            tenant_id,
            person_id,
            feature["feature_id"],
            feature["modality"],
            feature["model_id"],
            feature["model_version"],
            int(feature.get("embedding_dim") or len(embedding)),
            embedding_bytes(embedding),
            json.dumps(embedding, separators=(",", ":")),
            vector_literal(embedding),
            float(feature.get("quality_score") or 0.0),
            feature.get("source_id") or "",
            jsonb(feature.get("object_info") if isinstance(feature.get("object_info"), dict) else {}),
            float(feature.get("created_at") or 0.0),
        ),
    )


def upsert_gallery_person(person: dict[str, Any]) -> None:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            _execute_upsert_gallery_person(cursor, person)


def upsert_gallery_feature(tenant_id: str, person_id: str, feature: dict[str, Any]) -> None:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            _execute_upsert_gallery_feature(cursor, tenant_id, person_id, feature)


def delete_gallery_person(tenant_id: str, person_id: str) -> None:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM portrait_people WHERE tenant_id = %s AND person_id = %s", (tenant_id, person_id))


def replace_gallery_snapshot(snapshot: dict[str, Any]) -> None:
    people = snapshot.get("people") if isinstance(snapshot, dict) else []
    if not isinstance(people, list):
        people = []
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM portrait_features")
            cursor.execute("DELETE FROM portrait_people")
            for person in people:
                if not isinstance(person, dict):
                    continue
                _execute_upsert_gallery_person(cursor, person)
                for feature in person.get("features", []):
                    if isinstance(feature, dict):
                        _execute_upsert_gallery_feature(cursor, str(person["tenant_id"]), str(person["person_id"]), feature)


def load_threshold_snapshot() -> dict[str, Any]:
    if not postgres_configured() or psycopg is None:
        logger.warning("postgres threshold load skipped because PostgreSQL is not ready")
        return {}
    query = "SELECT profile, modality, threshold FROM portrait_thresholds ORDER BY modality, profile"
    try:
        with postgres_connection(row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
    except Exception as exc:  # pragma: no cover - requires external database
        logger.warning("postgres threshold load failed: %s", exception_log_summary(exc))
        return {}

    thresholds: dict[str, dict[str, float]] = {}
    for row in rows:
        thresholds.setdefault(str(row["modality"]), {})[str(row["profile"])] = float(row["threshold"])
    return {"version": 1, "thresholds": thresholds}


def save_threshold_snapshot(thresholds: dict[str, Any]) -> None:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            for modality, profiles in thresholds.items():
                if not isinstance(profiles, dict):
                    continue
                for profile, value in profiles.items():
                    cursor.execute(
                        """
                        INSERT INTO portrait_thresholds (profile, modality, threshold, updated_at)
                        VALUES (%s, %s, %s, now())
                        ON CONFLICT (profile, modality) DO UPDATE SET
                          threshold = EXCLUDED.threshold,
                          updated_at = EXCLUDED.updated_at
                        """,
                        (str(profile), str(modality), float(value)),
                    )


def insert_audit_event(payload: dict[str, Any]) -> None:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO portrait_audit_events (
                  tenant_id, request_id, event, outcome,
                  audit_prev_hash, audit_hash, audit_hash_algorithm,
                  payload, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, to_timestamp(%s))
                """,
                (
                    payload.get("tenant_id") or "default",
                    payload.get("request_id") or "",
                    payload.get("event") or "",
                    payload.get("outcome") or "success",
                    payload.get("audit_prev_hash"),
                    payload.get("audit_hash") or "",
                    payload.get("audit_hash_algorithm") or "",
                    jsonb(payload),
                    float(payload.get("created_at") or 0.0),
                ),
            )


def upsert_video_job(payload: dict[str, Any]) -> None:
    with postgres_connection() as connection:
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
                    jsonb(
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
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM portrait_video_jobs WHERE tenant_id = %s AND job_id = %s",
                (tenant_id, job_id),
            )


def load_video_jobs_snapshot() -> list[dict[str, Any]]:
    if not postgres_configured() or psycopg is None:
        return []
    try:
        with postgres_connection(row_factory=dict_row) as connection:
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


def encode_stream_url(stream_url: str) -> bytes:
    return json.dumps(encrypt_bytes(stream_url.encode("utf-8")), ensure_ascii=False, sort_keys=True).encode("utf-8")


def decode_stream_url(payload: bytes) -> str:
    return decrypt_bytes(json.loads(payload.decode("utf-8"))).decode("utf-8")


def upsert_stream(payload: dict[str, Any]) -> None:
    with postgres_connection() as connection:
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
                    jsonb(payload.get("settings") or {}),
                    jsonb(payload.get("metadata") or {}),
                    payload.get("status") or "registered",
                    float(payload.get("created_at") or 0.0),
                    float(payload.get("updated_at") or 0.0),
                ),
            )
            cursor.execute(
                "DELETE FROM portrait_stream_events WHERE tenant_id = %s AND stream_id = %s",
                (tenant_id, stream_id),
            )
            for event in payload.get("events", []):
                if isinstance(event, dict):
                    cursor.execute(
                        """
                        INSERT INTO portrait_stream_events (
                          tenant_id, stream_id, event_id, type, message, payload, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, to_timestamp(%s))
                        ON CONFLICT (tenant_id, stream_id, event_id) DO NOTHING
                        """,
                        (
                            tenant_id,
                            stream_id,
                            event["event_id"],
                            event.get("type") or "",
                            event.get("message") or "",
                            jsonb(event.get("payload") or {}),
                            float(event.get("created_at") or 0.0),
                        ),
                    )


def delete_stream(tenant_id: str, stream_id: str) -> None:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM portrait_streams WHERE tenant_id = %s AND stream_id = %s",
                (tenant_id, stream_id),
            )


def insert_object_record(tenant_id: str, info: dict[str, Any], metadata: dict[str, Any] | None = None) -> None:
    with postgres_connection() as connection:
        with connection.cursor() as cursor:
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
                    jsonb(metadata or {}),
                ),
            )


def load_streams_snapshot() -> list[dict[str, Any]]:
    if not postgres_configured() or psycopg is None:
        return []
    try:
        with postgres_connection(row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT tenant_id, stream_id, stream_url_ciphertext, name, settings, metadata, status,
                           EXTRACT(EPOCH FROM created_at)::double precision AS created_at,
                           EXTRACT(EPOCH FROM updated_at)::double precision AS updated_at
                    FROM portrait_streams
                    ORDER BY created_at
                    """
                )
                stream_rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT tenant_id, stream_id, event_id, type, message, payload,
                           EXTRACT(EPOCH FROM created_at)::double precision AS created_at
                    FROM portrait_stream_events
                    ORDER BY created_at
                    """
                )
                event_rows = cursor.fetchall()
    except Exception as exc:  # pragma: no cover - requires external database
        logger.warning("postgres stream load failed: %s", exception_log_summary(exc))
        return []

    events_by_stream: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in event_rows:
        events_by_stream.setdefault((row["tenant_id"], row["stream_id"]), []).append(
            {
                "event_id": row["event_id"],
                "type": row["type"],
                "message": row["message"],
                "payload": row["payload"] if isinstance(row["payload"], dict) else {},
                "created_at": float(row["created_at"] or 0.0),
            }
        )

    streams = []
    for row in stream_rows:
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
    return streams


def search_pgvector(
    query_embedding: list[float],
    *,
    modality: str,
    threshold: float,
    threshold_profile: str,
    top_k: int,
    tenant_id: str,
) -> list[dict[str, Any]]:
    embedding = normalized_embedding(query_embedding)
    dimension = len(embedding)
    if dimension in {64, 128, 256, 512, 1024, 2048}:
        distance_expr = f"(f.embedding_vector::vector({dimension}) <=> %s::vector({dimension}))"
    else:
        distance_expr = "(f.embedding_vector <=> %s::vector)"
    query = f"""
        SELECT
          p.tenant_id,
          p.person_id,
          p.display_name,
          f.feature_id,
          f.modality,
          f.model_id,
          f.model_version,
          f.embedding_dim,
          f.quality_score,
          f.source_id,
          EXTRACT(EPOCH FROM f.created_at)::double precision AS feature_created_at,
          {distance_expr} AS cosine_distance
        FROM portrait_features f
        JOIN portrait_people p
          ON p.tenant_id = f.tenant_id AND p.person_id = f.person_id
        WHERE f.tenant_id = %s
          AND f.modality = %s
          AND f.embedding_dim = %s
        ORDER BY {distance_expr}
        LIMIT %s
    """
    with postgres_connection(row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            literal = vector_literal(embedding)
            cursor.execute(query, (literal, tenant_id, modality, len(embedding), literal, int(top_k)))
            rows = cursor.fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        cosine_distance = float(row["cosine_distance"] or 0.0)
        similarity = 1.0 - cosine_distance
        candidates.append(
            {
                "tenant_id": row["tenant_id"],
                "person_id": row["person_id"],
                "display_name": row["display_name"],
                "feature": {
                    "feature_id": row["feature_id"],
                    "modality": row["modality"],
                    "embedding_dim": row["embedding_dim"],
                    "model_id": row["model_id"],
                    "model_version": row["model_version"],
                    "quality_score": row["quality_score"],
                    "source_id": row["source_id"],
                    "created_at": row["feature_created_at"],
                },
                "modality": modality,
                "similarity": round(similarity, 6),
                "distance": round(cosine_distance, 6),
                "threshold": threshold,
                "threshold_profile": threshold_profile,
                "passed": similarity >= threshold,
            }
        )
    return candidates
