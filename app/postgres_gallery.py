from __future__ import annotations

import json
from typing import Any

from app.observability import logger, trace_span
from app.portrait_response import exception_log_summary
from app.settings import PGVECTOR_HNSW_EF_SEARCH
from app import postgres_core as _core


def load_gallery_snapshot() -> dict[str, Any]:
    if not _core.postgres_configured() or _core.psycopg is None:
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
            with _core.postgres_connection(row_factory=_core.dict_row) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query)
                    for row in cursor:
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
    except Exception as exc:  # pragma: no cover - 需要外部数据库支持
        logger.warning("postgres gallery load failed: %s", exception_log_summary(exc))
        return {"people": []}
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
            _core.jsonb(person.get("metadata") or {}),
            float(person.get("created_at") or 0.0),
            float(person.get("updated_at") or 0.0),
        ),
    )


def _execute_upsert_gallery_feature(cursor: Any, tenant_id: str, person_id: str, feature: dict[str, Any]) -> None:
    embedding = _core.normalized_embedding(feature.get("embedding") or [])
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
            _core.embedding_bytes(embedding),
            json.dumps(embedding, separators=(",", ":")),
            _core.vector_literal(embedding),
            float(feature.get("quality_score") or 0.0),
            feature.get("source_id") or "",
            _core.jsonb(feature.get("object_info") if isinstance(feature.get("object_info"), dict) else {}),
            float(feature.get("created_at") or 0.0),
        ),
    )


def upsert_gallery_person(person: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            _execute_upsert_gallery_person(cursor, person)


def upsert_gallery_feature(tenant_id: str, person_id: str, feature: dict[str, Any]) -> None:
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            _execute_upsert_gallery_feature(cursor, tenant_id, person_id, feature)


def delete_gallery_person(tenant_id: str, person_id: str) -> None:
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM portrait_people WHERE tenant_id = %s AND person_id = %s", (tenant_id, person_id))


def replace_gallery_snapshot(snapshot: dict[str, Any]) -> None:
    people = snapshot.get("people") if isinstance(snapshot, dict) else []
    if not isinstance(people, list):
        people = []
    person_rows: list[tuple[Any, ...]] = []
    feature_rows: list[tuple[Any, ...]] = []
    for person in people:
        if not isinstance(person, dict):
            continue
        person_rows.append(
            (
                person["tenant_id"],
                person["person_id"],
                person.get("display_name"),
                _core.jsonb(person.get("metadata") or {}),
                float(person.get("created_at") or 0.0),
                float(person.get("updated_at") or 0.0),
            )
        )
        for feature in person.get("features", []):
            if not isinstance(feature, dict):
                continue
            embedding = _core.normalized_embedding(feature.get("embedding") or [])
            feature_rows.append(
                (
                    str(person["tenant_id"]),
                    str(person["person_id"]),
                    feature["feature_id"],
                    feature["modality"],
                    feature["model_id"],
                    feature["model_version"],
                    int(feature.get("embedding_dim") or len(embedding)),
                    _core.embedding_bytes(embedding),
                    json.dumps(embedding, separators=(",", ":")),
                    _core.vector_literal(embedding),
                    float(feature.get("quality_score") or 0.0),
                    feature.get("source_id") or "",
                    _core.jsonb(feature.get("object_info") if isinstance(feature.get("object_info"), dict) else {}),
                    float(feature.get("created_at") or 0.0),
                )
            )
    with _core.postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM portrait_features")
            cursor.execute("DELETE FROM portrait_people")
            if person_rows:
                cursor.executemany(UPSERT_PERSON_SQL, person_rows)
            if feature_rows:
                cursor.executemany(UPSERT_FEATURE_SQL, feature_rows)


def search_pgvector(
    query_embedding: list[float],
    *,
    modality: str,
    threshold: float,
    threshold_profile: str,
    top_k: int,
    tenant_id: str,
) -> list[dict[str, Any]]:
    embedding = _core.normalized_embedding(query_embedding)
    dimension = len(embedding)
    if dimension in {64, 128, 256, 512, 1024, 2048}:
        distance_expr = f"(f.embedding_vector::vector({dimension}) <=> %s::vector({dimension}))"
    else:
        distance_expr = "(f.embedding_vector <=> %s::vector)"
    # 按 SELECT 别名排序，使距离表达式（及其向量字面量）只绑定并求值一次而非两次；
    # PostgreSQL 仍会把别名解析回被索引的表达式，因此 HNSW 索引依然可用。
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
        ORDER BY cosine_distance
        LIMIT %s
    """
    # 放宽 HNSW 候选列表，使 tenant_id/modality 等值过滤（施加在 ANN 候选之上）
    # 不会让结果少于 top_k。
    ef_search = max(int(PGVECTOR_HNSW_EF_SEARCH), int(top_k))
    with _core.postgres_connection(row_factory=_core.dict_row) as connection:
        with connection.cursor() as cursor:
            try:
                cursor.execute(f"SET LOCAL hnsw.ef_search = {ef_search}")
            except Exception as exc:  # pragma: no cover - 无 pgvector HNSW 时该 GUC 不存在
                logger.warning("could not set hnsw.ef_search, using server default: %s", exception_log_summary(exc))
            literal = _core.vector_literal(embedding)
            cursor.execute(query, (literal, tenant_id, modality, len(embedding), int(top_k)))
            rows = list(cursor)

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
