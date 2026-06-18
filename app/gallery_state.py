from __future__ import annotations

from copy import deepcopy
import json
import threading
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.observability import logger, wall_time
from app.portrait_gallery_records import FeatureRecord, GalleryKey, PersonRecord, feature_object_infos, gallery_key
from app.portrait_response import exception_log_summary
from app.portrait_security import validate_person_id
from app.portrait_state import append_jsonl, handle_state_read_error, read_json_state, state_path_fingerprint, write_json_state
from app.portrait_thresholds import normalize_modality
from app.settings import PORTRAIT_GALLERY_STATE_PATH, PORTRAIT_GALLERY_WAL_COMPACT_EVERY, PORTRAIT_GALLERY_WAL_ENABLED, PORTRAIT_STORAGE_BACKEND


def postgres_gallery_enabled() -> bool:
    return PORTRAIT_STORAGE_BACKEND == "postgres"


GALLERY: dict[GalleryKey, PersonRecord] = {}
GALLERY_LOCK = threading.RLock()
GALLERY_WAL_COUNTER = 0


def gallery_wal_path() -> Any:
    return PORTRAIT_GALLERY_STATE_PATH.with_suffix(PORTRAIT_GALLERY_STATE_PATH.suffix + ".wal.jsonl")


def gallery_state_payload() -> dict[str, Any]:
    with GALLERY_LOCK:
        return {
            "version": 1,
            "people": [person.state_dict() for person in sorted(GALLERY.values(), key=lambda item: (item.tenant_id, item.person_id))],
        }


def apply_gallery_wal(payload: dict[str, Any]) -> dict[str, Any]:
    path = gallery_wal_path()
    if not PORTRAIT_GALLERY_WAL_ENABLED or not path.exists():
        return payload
    people = {}
    for item in payload.get("people", []) if isinstance(payload.get("people"), list) else []:
        if isinstance(item, dict) and "person_id" in item:
            people[(str(item.get("tenant_id", "default")), str(item["person_id"]))] = item
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue
                tenant_id = str(entry.get("tenant_id", "default"))
                person_id = str(entry.get("person_id", ""))
                if not person_id:
                    continue
                key = (tenant_id, person_id)
                if entry.get("op") == "delete_person":
                    people.pop(key, None)
                elif isinstance(entry.get("person"), dict):
                    people[key] = entry["person"]
    except Exception as exc:
        logger.warning(
            "failed to replay gallery WAL: path_hash=%s error=%s",
            state_path_fingerprint(path),
            exception_log_summary(exc),
        )
        raise
    return {"version": 1, "people": list(people.values())}


def load_gallery_state() -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import load_gallery_snapshot

        payload = load_gallery_snapshot()
    else:
        payload = read_json_state(PORTRAIT_GALLERY_STATE_PATH, {"people": []})
        payload = apply_gallery_wal(payload)
    if not isinstance(payload, dict):
        handle_state_read_error(f"gallery state root must be a mapping: {PORTRAIT_GALLERY_STATE_PATH}")
        return
    people = payload.get("people", [])
    if not isinstance(people, list):
        handle_state_read_error(f"gallery state people must be a list: {PORTRAIT_GALLERY_STATE_PATH}")
        return
    with GALLERY_LOCK:
        GALLERY.clear()
        for item in people:
            if not isinstance(item, dict) or "person_id" not in item:
                continue
            try:
                person = PersonRecord.from_state(item)
                validate_person_id(person.person_id)
            except Exception as exc:
                logger.warning("skipping invalid gallery person state: %s", exception_log_summary(exc))
                continue
            GALLERY[gallery_key(person.tenant_id, person.person_id)] = person


def save_gallery_state() -> None:
    global GALLERY_WAL_COUNTER
    if postgres_gallery_enabled():
        from app.portrait_postgres import replace_gallery_snapshot

        replace_gallery_snapshot(gallery_state_payload())
        return
    write_json_state(PORTRAIT_GALLERY_STATE_PATH, gallery_state_payload())
    if PORTRAIT_GALLERY_WAL_ENABLED:
        try:
            gallery_wal_path().unlink(missing_ok=True)
            GALLERY_WAL_COUNTER = 0
        except Exception as exc:
            logger.warning("failed to compact gallery WAL: %s", exception_log_summary(exc))


def append_gallery_wal(op: str, *, tenant_id: str, person_id: str, person: PersonRecord | None = None) -> None:
    global GALLERY_WAL_COUNTER
    if not PORTRAIT_GALLERY_WAL_ENABLED:
        save_gallery_state()
        return
    payload: dict[str, Any] = {
        "op": op,
        "tenant_id": tenant_id,
        "person_id": person_id,
        "updated_at": wall_time(),
    }
    if person is not None:
        payload["person"] = person.state_dict()
    append_jsonl(gallery_wal_path(), payload, fail_closed=True)
    GALLERY_WAL_COUNTER += 1
    if PORTRAIT_GALLERY_WAL_COMPACT_EVERY > 0 and GALLERY_WAL_COUNTER >= PORTRAIT_GALLERY_WAL_COMPACT_EVERY:
        save_gallery_state()


def persist_person(person: PersonRecord) -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import upsert_gallery_person

        upsert_gallery_person(person.state_dict())
        return
    append_gallery_wal("upsert_person", tenant_id=person.tenant_id, person_id=person.person_id, person=person)


def persist_feature(person: PersonRecord, feature: FeatureRecord) -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import upsert_gallery_feature

        upsert_gallery_feature(person.tenant_id, person.person_id, feature.state_dict())
    else:
        append_gallery_wal("upsert_person", tenant_id=person.tenant_id, person_id=person.person_id, person=person)

    try:
        from app.portrait_vector_store import VECTOR_STORE

        VECTOR_STORE.upsert_feature(person.public_dict(include_embeddings=False), feature.state_dict())
    except Exception as exc:
        logger.warning("vector upsert failed: %s", exception_log_summary(exc))


def persist_person_delete(tenant_id: str, person_id: str) -> None:
    if postgres_gallery_enabled():
        from app.portrait_postgres import delete_gallery_person

        delete_gallery_person(tenant_id, person_id)
    else:
        append_gallery_wal("delete_person", tenant_id=tenant_id, person_id=person_id)

    try:
        from app.portrait_vector_store import VECTOR_STORE

        VECTOR_STORE.delete_person(tenant_id, person_id)
    except Exception as exc:
        logger.warning("vector delete failed: %s", exception_log_summary(exc))


def list_gallery_people(tenant_id: str = "default") -> list[dict[str, Any]]:
    with GALLERY_LOCK:
        return [
            person.public_dict()
            for person in sorted(GALLERY.values(), key=lambda item: item.person_id)
            if person.tenant_id == tenant_id
        ]


def upsert_person(
    person_id: str | None,
    display_name: str | None,
    metadata: dict[str, Any] | None = None,
    tenant_id: str = "default",
) -> PersonRecord:
    with GALLERY_LOCK:
        resolved_id = validate_person_id(person_id or f"p_{uuid4().hex[:12]}")
        key = gallery_key(tenant_id, resolved_id)
        person = GALLERY.get(key)
        previous_person = deepcopy(person) if person is not None else None
        if person is None:
            person = PersonRecord(tenant_id=tenant_id, person_id=resolved_id, display_name=display_name, metadata=metadata or {})
            GALLERY[key] = person
        else:
            if display_name is not None:
                person.display_name = display_name
            if metadata:
                person.metadata.update(metadata)
            person.updated_at = wall_time()
        try:
            persist_person(person)
        except Exception:
            if previous_person is None:
                GALLERY.pop(key, None)
            else:
                GALLERY[key] = previous_person
            raise
        return person


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
    with GALLERY_LOCK:
        modality_key = normalize_modality(modality)
        feature = FeatureRecord(
            feature_id=f"f_{uuid4().hex[:16]}",
            modality=modality_key,
            embedding=embedding,
            embedding_dim=len(embedding),
            model_id=model_id,
            model_version=model_version,
            quality_score=round(float(quality_score), 6),
            source_id=source_id,
            created_at=wall_time(),
            object_info=deepcopy(object_info) if object_info else None,
        )
        previous_person = deepcopy(person)
        person.features.append(feature)
        person.updated_at = wall_time()
        try:
            persist_feature(person, feature)
        except Exception:
            key = gallery_key(person.tenant_id, person.person_id)
            GALLERY[key] = previous_person
            person.features = previous_person.features
            person.updated_at = previous_person.updated_at
            person.display_name = previous_person.display_name
            person.metadata = previous_person.metadata
            raise
        return feature


def get_person_or_404(person_id: str, tenant_id: str = "default") -> PersonRecord:
    resolved_id = validate_person_id(person_id)
    with GALLERY_LOCK:
        person = GALLERY.get(gallery_key(tenant_id, resolved_id))
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person not found")
    return person


def patch_person(person_id: str, payload: dict[str, Any], tenant_id: str = "default") -> PersonRecord:
    with GALLERY_LOCK:
        resolved_id = validate_person_id(person_id)
        person = get_person_or_404(resolved_id, tenant_id=tenant_id)
        previous_person = deepcopy(person)
        if "display_name" in payload:
            person.display_name = payload["display_name"]
        if isinstance(payload.get("metadata"), dict):
            person.metadata.update(payload["metadata"])
        person.updated_at = wall_time()
        try:
            persist_person(person)
        except Exception:
            GALLERY[gallery_key(tenant_id, resolved_id)] = previous_person
            person.display_name = previous_person.display_name
            person.metadata = previous_person.metadata
            person.updated_at = previous_person.updated_at
            person.features = previous_person.features
            raise
        return person


def delete_person(person_id: str, tenant_id: str = "default") -> bool:
    with GALLERY_LOCK:
        resolved_id = validate_person_id(person_id)
        key = gallery_key(tenant_id, resolved_id)
        removed = GALLERY.pop(key, None)
        if removed is not None:
            try:
                persist_person_delete(tenant_id, resolved_id)
            except Exception:
                GALLERY[key] = removed
                raise
            return True
        return False

