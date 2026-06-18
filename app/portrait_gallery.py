from __future__ import annotations

from typing import Any

from app import gallery_search as _gallery_search
from app import gallery_state as _gallery_state
from app.gallery_search import (  # noqa: F401
    aggregate_gallery_candidates,
    apply_gallery_query_quality,
    apply_gallery_rank_context,
    gallery_candidate_key,
    gallery_candidate_quality,
    gallery_candidate_rank_context,
    gallery_candidate_score,
    gallery_decision_risk_severity,
    gallery_primary_risk,
    gallery_query_expansion_plan,
    gallery_query_quality_gate,
    gallery_records_by_feature_id,
    merge_gallery_candidate_pools,
)
from app.gallery_state import (  # noqa: F401
    GALLERY,
    GALLERY_LOCK,
    GALLERY_WAL_COUNTER,
    apply_gallery_wal,
    gallery_state_payload,
    gallery_wal_path,
    postgres_gallery_enabled,
)
from app.portrait_gallery_records import FeatureRecord, GalleryKey, PersonRecord, feature_object_infos, gallery_key
from app.settings import (
    PORTRAIT_GALLERY_STATE_PATH,
    PORTRAIT_GALLERY_WAL_COMPACT_EVERY,
    PORTRAIT_GALLERY_WAL_ENABLED,
    PORTRAIT_STORAGE_BACKEND,
)


_state_persist_person = _gallery_state.persist_person
_state_persist_feature = _gallery_state.persist_feature
_state_persist_person_delete = _gallery_state.persist_person_delete


def _sync_state_dependencies() -> None:
    _gallery_state.PORTRAIT_STORAGE_BACKEND = PORTRAIT_STORAGE_BACKEND
    _gallery_state.PORTRAIT_GALLERY_STATE_PATH = PORTRAIT_GALLERY_STATE_PATH
    _gallery_state.PORTRAIT_GALLERY_WAL_ENABLED = PORTRAIT_GALLERY_WAL_ENABLED
    _gallery_state.PORTRAIT_GALLERY_WAL_COMPACT_EVERY = PORTRAIT_GALLERY_WAL_COMPACT_EVERY
    _gallery_state.GALLERY = GALLERY
    _gallery_state.GALLERY_LOCK = GALLERY_LOCK
    _gallery_state.persist_person = persist_person
    _gallery_state.persist_feature = persist_feature
    _gallery_state.persist_person_delete = persist_person_delete


def _restore_state_dependencies() -> None:
    _gallery_state.persist_person = _state_persist_person
    _gallery_state.persist_feature = _state_persist_feature
    _gallery_state.persist_person_delete = _state_persist_person_delete


def _sync_search_dependencies() -> None:
    _gallery_search.GALLERY = GALLERY


def load_gallery_state() -> None:
    _sync_state_dependencies()
    try:
        _gallery_state.load_gallery_state()
    finally:
        _restore_state_dependencies()


def save_gallery_state() -> None:
    _sync_state_dependencies()
    try:
        _gallery_state.save_gallery_state()
    finally:
        _restore_state_dependencies()


def append_gallery_wal(op: str, *, tenant_id: str, person_id: str, person: PersonRecord | None = None) -> None:
    _sync_state_dependencies()
    try:
        _gallery_state.append_gallery_wal(op, tenant_id=tenant_id, person_id=person_id, person=person)
    finally:
        _restore_state_dependencies()


def persist_person(person: PersonRecord) -> None:
    _sync_state_dependencies()
    try:
        _state_persist_person(person)
    finally:
        _restore_state_dependencies()


def persist_feature(person: PersonRecord, feature: FeatureRecord) -> None:
    _sync_state_dependencies()
    try:
        _state_persist_feature(person, feature)
    finally:
        _restore_state_dependencies()


def persist_person_delete(tenant_id: str, person_id: str) -> None:
    _sync_state_dependencies()
    try:
        _state_persist_person_delete(tenant_id, person_id)
    finally:
        _restore_state_dependencies()


def list_gallery_people(tenant_id: str = "default") -> list[dict[str, Any]]:
    _sync_state_dependencies()
    try:
        return _gallery_state.list_gallery_people(tenant_id)
    finally:
        _restore_state_dependencies()


def upsert_person(
    person_id: str | None,
    display_name: str | None,
    metadata: dict[str, Any] | None = None,
    tenant_id: str = "default",
) -> PersonRecord:
    _sync_state_dependencies()
    try:
        return _gallery_state.upsert_person(person_id, display_name, metadata, tenant_id=tenant_id)
    finally:
        _restore_state_dependencies()


def add_feature(
    person: PersonRecord,
    *,
    modality: str,
    embedding: list[float],
    model_id: str,
    model_version: str,
    quality_score: float,
    source_id: str,
    object_info: dict[str, Any] | None = None,
) -> FeatureRecord:
    _sync_state_dependencies()
    try:
        return _gallery_state.add_feature(
            person,
            modality=modality,
            embedding=embedding,
            model_id=model_id,
            model_version=model_version,
            quality_score=quality_score,
            source_id=source_id,
            object_info=object_info,
        )
    finally:
        _restore_state_dependencies()


def get_person_or_404(person_id: str, tenant_id: str = "default") -> PersonRecord:
    _sync_state_dependencies()
    try:
        return _gallery_state.get_person_or_404(person_id, tenant_id=tenant_id)
    finally:
        _restore_state_dependencies()


def patch_person(person_id: str, payload: dict[str, Any], tenant_id: str = "default") -> PersonRecord:
    _sync_state_dependencies()
    try:
        return _gallery_state.patch_person(person_id, payload, tenant_id=tenant_id)
    finally:
        _restore_state_dependencies()


def delete_person(person_id: str, tenant_id: str = "default") -> bool:
    _sync_state_dependencies()
    try:
        return _gallery_state.delete_person(person_id, tenant_id=tenant_id)
    finally:
        _restore_state_dependencies()


def reindex_gallery_vectors(
    *,
    tenant_id: str = "default",
    modality: str | None = None,
    model_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    _sync_search_dependencies()
    return _gallery_search.reindex_gallery_vectors(tenant_id=tenant_id, modality=modality, model_id=model_id, dry_run=dry_run)


def search_gallery(
    embedding: list[float],
    *,
    modality: str,
    threshold_profile: str,
    top_k: int,
    tenant_id: str = "default",
    query_quality: float | None = None,
) -> list[dict[str, Any]]:
    _sync_search_dependencies()
    return _gallery_search.search_gallery(
        embedding,
        modality=modality,
        threshold_profile=threshold_profile,
        top_k=top_k,
        tenant_id=tenant_id,
        query_quality=query_quality,
    )


__all__ = [
    "FeatureRecord",
    "GalleryKey",
    "PersonRecord",
    "GALLERY",
    "GALLERY_LOCK",
    "aggregate_gallery_candidates",
    "apply_gallery_query_quality",
    "apply_gallery_rank_context",
    "apply_gallery_wal",
    "add_feature",
    "append_gallery_wal",
    "delete_person",
    "feature_object_infos",
    "gallery_candidate_key",
    "gallery_candidate_quality",
    "gallery_candidate_rank_context",
    "gallery_candidate_score",
    "gallery_decision_risk_severity",
    "gallery_key",
    "gallery_primary_risk",
    "gallery_query_expansion_plan",
    "gallery_query_quality_gate",
    "gallery_records_by_feature_id",
    "gallery_state_payload",
    "gallery_wal_path",
    "get_person_or_404",
    "list_gallery_people",
    "load_gallery_state",
    "merge_gallery_candidate_pools",
    "patch_person",
    "persist_feature",
    "persist_person",
    "persist_person_delete",
    "postgres_gallery_enabled",
    "reindex_gallery_vectors",
    "save_gallery_state",
    "search_gallery",
    "upsert_person",
]
