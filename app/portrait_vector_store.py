from threading import Lock
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import numpy.typing as npt

from app.observability import logger, trace_span
from app.portrait_response import exception_log_summary
from app.portrait_thresholds import get_threshold, validate_threshold_profile
from app.settings import (
    PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND,
    PORTRAIT_VECTOR_BACKEND,
    QDRANT_API_KEY,
    QDRANT_PREFER_GRPC,
    QDRANT_URL,
)

try:  # pragma: no cover - 可选的生产环境依赖
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qdrant_models
except Exception:  # pragma: no cover - 当依赖不存在时执行
    QdrantClient = None
    qdrant_models = None


QDRANT_COLLECTIONS = {
    "face": "portrait_face_vectors",
    "body": "portrait_body_vectors",
    "gait": "portrait_gait_vectors",
    "appearance": "portrait_appearance_vectors",
}


def _gallery_scan_records(modality: str, tenant_id: str) -> list[dict[str, Any]]:
    # 为 DB 后端不可用时的本地扫描回退，懒构建内存图库快照。正常路径下 search_gallery
    # 传入空 records（存储有自己的索引），因此回退在此重建。延迟导入以避免与
    # gallery_search 的循环导入。
    try:
        from app.gallery_search import gallery_records_snapshot
        from app.portrait_thresholds import normalize_modality

        return gallery_records_snapshot(tenant_id, normalize_modality(modality))
    except Exception as exc:  # pragma: no cover - 防御性：绝不让回退本身失败
        logger.warning("构建本地扫描回退记录失败: %s", exception_log_summary(exc))
        return []

FloatArray = npt.NDArray[np.float32]


# ------------------------------------------------------------------
# 本地向量缓存 —— 避免每次查询重建归一化矩阵
# 键 = (tenant_id, modality, embedding_dim)，值 = (matrix, record_metas)
# upsert/delete 时调用 invalidate_local_vector_cache() 清除对应条目。
# ------------------------------------------------------------------
_LOCAL_VECTOR_CACHE: dict[tuple[str, str, int], tuple[FloatArray, list[dict[str, Any]]]] = {}
_LOCAL_VECTOR_CACHE_LOCK = Lock()


def _local_cache_key(tenant_id: str, modality: str, dimension: int) -> tuple[str, str, int]:
    return (tenant_id, modality, dimension)


def invalidate_local_vector_cache(tenant_id: str | None = None, modality: str | None = None) -> None:
    """使本地向量矩阵缓存失效。不传参数时清空全部缓存。"""
    with _LOCAL_VECTOR_CACHE_LOCK:
        if tenant_id is None:
            _LOCAL_VECTOR_CACHE.clear()
            return
        keys_to_drop = [
            k for k in _LOCAL_VECTOR_CACHE
            if k[0] == tenant_id and (modality is None or k[1] == modality)
        ]
        for k in keys_to_drop:
            del _LOCAL_VECTOR_CACHE[k]


def _require_or_fallback_allowed(error: Exception, backend_name: str) -> None:
    if PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND:
        raise RuntimeError(f"{backend_name} 向量后端失败，且本地回退已禁用") from error


def _qdrant_not_found_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code == 404:
        return True
    message = str(error).lower()
    return "not found" in message or "does not exist" in message or "doesn't exist" in message


class VectorStore(Protocol):
    backend_name: str

    def health(self) -> dict[str, Any]:
        ...

    def upsert_feature(self, person: dict[str, Any], feature: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_person(self, tenant_id: str, person_id: str) -> dict[str, Any]:
        ...

    def search(
        self,
        query_embedding: list[float],
        records: list[dict[str, Any]],
        *,
        modality: str,
        threshold_profile: str,
        top_k: int,
        tenant_id: str = "default",
    ) -> list[dict[str, Any]]:
        ...


class LocalVectorStore:
    backend_name = "local_numpy"

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "status": "ready",
            "index": "numpy_matrix_topk",
            "production_ready": False,
            "note": "Use PORTRAIT_VECTOR_BACKEND=pgvector or qdrant for production galleries.",
        }

    def upsert_feature(self, person: dict[str, Any], feature: dict[str, Any]) -> dict[str, Any]:
        # 写操作使本地归一化矩阵缓存失效，避免后续搜索返回过时结果
        tenant_id = str(person.get("tenant_id") or "default")
        modality = str(feature.get("modality") or "")
        invalidate_local_vector_cache(tenant_id, modality if modality else None)
        return {"backend": self.backend_name, "status": "local_index_derived_from_gallery"}

    def delete_person(self, tenant_id: str, person_id: str) -> dict[str, Any]:
        # 删除操作使该租户全部模态的缓存失效
        invalidate_local_vector_cache(tenant_id)
        return {"backend": self.backend_name, "status": "local_index_derived_from_gallery"}

    def _build_matrix(
        self,
        records: list[dict[str, Any]],
        query_dim: int,
        tenant_id: str,
        modality: str,
    ) -> tuple[FloatArray, list[dict[str, Any]]]:
        """构建或从缓存返回预归一化矩阵。

        仅当调用方传入非空 records 时才尝试缓存（records 来自外层图库快照，
        是完整的当前图库视图）。缓存命中时直接返回，避免重建 O(N·D) 操作。
        """
        cache_key = _local_cache_key(tenant_id, modality, query_dim)
        with _LOCAL_VECTOR_CACHE_LOCK:
            cached = _LOCAL_VECTOR_CACHE.get(cache_key)
        if cached is not None:
            return cached

        vectors: list[FloatArray] = []
        vector_records: list[dict[str, Any]] = []
        for record in records:
            vector = np.asarray(record.get("embedding") or [], dtype=np.float32).reshape(-1)
            if vector.shape[0] != query_dim:
                continue
            norm = float(np.linalg.norm(vector))
            if norm <= 0:
                continue
            vectors.append(vector / norm)
            vector_records.append(record)
        if not vectors:
            return np.empty((0, query_dim), dtype=np.float32), []
        matrix: FloatArray = np.stack(vectors, axis=0)
        result = (matrix, vector_records)
        with _LOCAL_VECTOR_CACHE_LOCK:
            _LOCAL_VECTOR_CACHE[cache_key] = result
        return result

    def search(
        self,
        query_embedding: list[float],
        records: list[dict[str, Any]],
        *,
        modality: str,
        threshold_profile: str,
        top_k: int,
        tenant_id: str = "default",
    ) -> list[dict[str, Any]]:
        if not records:
            return []
        profile_key = validate_threshold_profile(threshold_profile)
        threshold = get_threshold(modality, profile_key)
        query = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
        query_norm = float(np.linalg.norm(query))
        if query_norm <= 0:
            return []
        query = query / query_norm

        matrix, vector_records = self._build_matrix(records, query.shape[0], tenant_id, modality)
        if matrix.shape[0] == 0:
            return []

        similarities = matrix @ query
        limit = min(max(1, int(top_k)), len(vector_records))
        if limit < len(vector_records):
            top_indexes = np.argpartition(-similarities, limit - 1)[:limit]
            top_indexes = top_indexes[np.argsort(-similarities[top_indexes])]
        else:
            top_indexes = np.argsort(-similarities)
        candidates = []
        for index in top_indexes:
            record = vector_records[int(index)]
            similarity = float(similarities[int(index)])
            distance = float(np.linalg.norm(matrix[int(index)] - query))
            public_record = {key: value for key, value in record.items() if key != "embedding"}
            candidates.append(
                {
                    **public_record,
                    "modality": modality,
                    "similarity": round(similarity, 6),
                    "distance": round(distance, 6),
                    "threshold": threshold,
                    "threshold_profile": profile_key,
                    "passed": similarity >= threshold,
                }
            )
        return candidates


class PgvectorVectorStore(LocalVectorStore):
    backend_name = "pgvector"

    def health(self) -> dict[str, Any]:
        from app.portrait_postgres import postgres_health

        return {"backend": self.backend_name, **postgres_health()}

    def upsert_feature(self, person: dict[str, Any], feature: dict[str, Any]) -> dict[str, Any]:
        return {"backend": self.backend_name, "status": "stored_by_postgres_gallery"}

    def delete_person(self, tenant_id: str, person_id: str) -> dict[str, Any]:
        return {"backend": self.backend_name, "status": "deleted_by_postgres_gallery"}

    def search(
        self,
        query_embedding: list[float],
        records: list[dict[str, Any]],
        *,
        modality: str,
        threshold_profile: str,
        top_k: int,
        tenant_id: str = "default",
    ) -> list[dict[str, Any]]:
        from app.portrait_postgres import search_pgvector
        from app.portrait_thresholds import normalize_modality

        modality_key = normalize_modality(modality)
        profile_key = validate_threshold_profile(threshold_profile)
        try:
            with trace_span("portrait.vector.pgvector.search", modality=modality_key, top_k=top_k, tenant_id=tenant_id):
                return search_pgvector(
                    query_embedding,
                    modality=modality_key,
                    threshold=get_threshold(modality_key, profile_key),
                    threshold_profile=profile_key,
                    top_k=top_k,
                    tenant_id=tenant_id,
                )
        except Exception as exc:
            _require_or_fallback_allowed(exc, self.backend_name)
            logger.warning("pgvector 检索失败，回退到本地向量扫描: %s", exception_log_summary(exc))
            return super().search(
                query_embedding,
                records or _gallery_scan_records(modality, tenant_id),
                modality=modality,
                threshold_profile=profile_key,
                top_k=top_k,
                tenant_id=tenant_id,
            )


class QdrantVectorStore(LocalVectorStore):
    backend_name = "qdrant"

    def __init__(self) -> None:
        self._cached_client: Any | None = None

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "configured": bool(QDRANT_URL),
            "api_key_configured": bool(QDRANT_API_KEY),
            "driver_available": QdrantClient is not None,
            "prefer_grpc": QDRANT_PREFER_GRPC,
            "status": "ready" if QDRANT_URL and QdrantClient is not None else "not_ready",
        }

    def _client(self) -> Any:
        if QdrantClient is None:
            raise RuntimeError("未安装 qdrant-client；请安装 requirements-prod-optional.txt")
        if not QDRANT_URL:
            raise RuntimeError("未配置 QDRANT_URL")
        if self._cached_client is None:
            self._cached_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, prefer_grpc=QDRANT_PREFER_GRPC)
        return self._cached_client

    def _collection_for(self, modality: str) -> str:
        from app.portrait_thresholds import normalize_modality

        return QDRANT_COLLECTIONS.get(normalize_modality(modality), "portrait_body_vectors")

    def _point_id(self, tenant_id: str, feature_id: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"portrait-hub:{tenant_id}:{feature_id}"))

    def _ensure_collection(self, client: Any, collection_name: str, vector_size: int) -> None:
        if qdrant_models is None:
            raise RuntimeError("qdrant-client 模型不可用")
        try:
            client.get_collection(collection_name)
            return
        except Exception as exc:
            if not _qdrant_not_found_error(exc):
                raise
            client.create_collection(
                collection_name=collection_name,
                vectors_config=qdrant_models.VectorParams(size=vector_size, distance=qdrant_models.Distance.COSINE),
            )

    def upsert_feature(self, person: dict[str, Any], feature: dict[str, Any]) -> dict[str, Any]:
        embedding = feature.get("embedding") or []
        if not embedding:
            return {"backend": self.backend_name, "status": "skipped", "reason": "embedding_missing"}
        client = self._client()
        collection_name = self._collection_for(str(feature.get("modality", "body")))
        self._ensure_collection(client, collection_name, len(embedding))
        payload = {
            "tenant_id": person["tenant_id"],
            "person_id": person["person_id"],
            "display_name": person.get("display_name"),
            "feature_id": feature["feature_id"],
            "modality": feature.get("modality"),
            "model_id": feature.get("model_id"),
            "model_version": feature.get("model_version"),
            "quality_score": feature.get("quality_score"),
            "source_id": feature.get("source_id"),
            "created_at": feature.get("created_at"),
        }
        with trace_span("portrait.vector.qdrant.upsert", collection=collection_name, modality=feature.get("modality")):
            client.upsert(
                collection_name=collection_name,
                points=[
                    qdrant_models.PointStruct(
                        id=self._point_id(str(person["tenant_id"]), str(feature["feature_id"])),
                        vector=[float(value) for value in embedding],
                        payload=payload,
                    )
                ],
            )
        return {"backend": self.backend_name, "status": "upserted", "collection": collection_name}

    def delete_person(self, tenant_id: str, person_id: str) -> dict[str, Any]:
        if qdrant_models is None:
            return {"backend": self.backend_name, "status": "skipped", "reason": "driver_unavailable"}
        client = self._client()
        selector = qdrant_models.FilterSelector(
            filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(key="tenant_id", match=qdrant_models.MatchValue(value=tenant_id)),
                    qdrant_models.FieldCondition(key="person_id", match=qdrant_models.MatchValue(value=person_id)),
                ]
            )
        )
        deleted = []
        for collection_name in QDRANT_COLLECTIONS.values():
            try:
                client.delete(collection_name=collection_name, points_selector=selector)
                deleted.append(collection_name)
            except Exception:
                continue
        return {"backend": self.backend_name, "status": "delete_requested", "collections": deleted}

    def search(
        self,
        query_embedding: list[float],
        records: list[dict[str, Any]],
        *,
        modality: str,
        threshold_profile: str,
        top_k: int,
        tenant_id: str = "default",
    ) -> list[dict[str, Any]]:
        profile_key = validate_threshold_profile(threshold_profile)
        if qdrant_models is None:
            return super().search(
                query_embedding,
                records or _gallery_scan_records(modality, tenant_id),
                modality=modality,
                threshold_profile=profile_key,
                top_k=top_k,
                tenant_id=tenant_id,
            )
        from app.portrait_thresholds import normalize_modality

        modality_key = normalize_modality(modality)
        threshold = get_threshold(modality_key, profile_key)
        try:
            client = self._client()
            collection_name = self._collection_for(modality_key)
            with trace_span("portrait.vector.qdrant.search", collection=collection_name, modality=modality_key, top_k=top_k, tenant_id=tenant_id):
                result = client.search(
                    collection_name=collection_name,
                    query_vector=[float(value) for value in query_embedding],
                    query_filter=qdrant_models.Filter(
                        must=[qdrant_models.FieldCondition(key="tenant_id", match=qdrant_models.MatchValue(value=tenant_id))]
                    ),
                    limit=int(top_k),
                    with_payload=True,
                )
        except Exception as exc:
            _require_or_fallback_allowed(exc, self.backend_name)
            logger.warning("Qdrant 检索失败，回退到本地向量扫描: %s", exception_log_summary(exc))
            return super().search(
                query_embedding,
                records or _gallery_scan_records(modality, tenant_id),
                modality=modality,
                threshold_profile=profile_key,
                top_k=top_k,
                tenant_id=tenant_id,
            )

        candidates = []
        for item in result:
            payload = item.payload or {}
            similarity = float(item.score or 0.0)
            candidates.append(
                {
                    "tenant_id": payload.get("tenant_id"),
                    "person_id": payload.get("person_id"),
                    "display_name": payload.get("display_name"),
                    "feature": {
                        "feature_id": payload.get("feature_id"),
                        "modality": payload.get("modality"),
                        "model_id": payload.get("model_id"),
                        "model_version": payload.get("model_version"),
                        "quality_score": payload.get("quality_score"),
                        "source_id": payload.get("source_id"),
                        "created_at": payload.get("created_at"),
                    },
                    "modality": modality_key,
                    "similarity": round(similarity, 6),
                    "distance": round(1.0 - similarity, 6),
                    "threshold": threshold,
                    "threshold_profile": profile_key,
                    "passed": similarity >= threshold,
                }
            )
        return candidates


def configured_vector_store() -> VectorStore:
    if PORTRAIT_VECTOR_BACKEND == "pgvector":
        return PgvectorVectorStore()
    if PORTRAIT_VECTOR_BACKEND == "qdrant":
        return QdrantVectorStore()
    if PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND:
        raise RuntimeError("PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND 已禁用本地向量后端")
    return LocalVectorStore()


VECTOR_STORE = configured_vector_store()
