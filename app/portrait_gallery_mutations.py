from __future__ import annotations

from collections.abc import Callable, MutableMapping
from copy import deepcopy
from typing import Any, Protocol

from app.observability import logger
from app.portrait_gallery import (
    GALLERY,
    FeatureRecord,
    GalleryKey,
    PersonRecord,
    feature_object_infos,
    gallery_key,
    persist_feature,
    persist_person,
    persist_person_delete,
)
from app.portrait_object_storage import OBJECT_STORE
from app.portrait_response import OBJECT_CLEANUP_FAILED, exception_log_summary, raise_rollback_failure


class ObjectStoreLike(Protocol):
    def delete_object(self, object_info: dict[str, Any]) -> dict[str, Any]:
        ...


PersistPersonHook = Callable[[PersonRecord], None]
PersistFeatureHook = Callable[[PersonRecord, FeatureRecord], None]
PersistDeleteHook = Callable[[str, str], None]
GalleryMapping = MutableMapping[GalleryKey, PersonRecord]


def cleanup_object_after_failed_feature(
    object_info: dict[str, Any],
    *,
    object_store: ObjectStoreLike = OBJECT_STORE,
) -> str | None:
    try:
        result = object_store.delete_object(object_info)
        if not result.get("deleted"):
            logger.warning(
                "object cleanup after feature persistence failure did not delete object: backend=%s reason=%s",
                result.get("backend"),
                result.get("reason"),
            )
            return OBJECT_CLEANUP_FAILED
    except Exception as exc:
        logger.warning("failed to cleanup object after feature persistence failure: %s", exception_log_summary(exc))
        return OBJECT_CLEANUP_FAILED
    return None


def cleanup_gallery_feature_objects(
    person: PersonRecord,
    *,
    object_store: ObjectStoreLike = OBJECT_STORE,
) -> tuple[int, list[str]]:
    deleted_count = 0
    errors: list[str] = []
    for object_info in feature_object_infos(person):
        try:
            result = object_store.delete_object(object_info)
            if result.get("deleted"):
                deleted_count += 1
                continue
            logger.warning(
                "object cleanup during gallery person deletion did not delete object: backend=%s reason=%s",
                result.get("backend"),
                result.get("reason"),
            )
            errors.append(OBJECT_CLEANUP_FAILED)
        except Exception as exc:
            logger.warning("failed to cleanup object during gallery person deletion: %s", exception_log_summary(exc))
            errors.append(OBJECT_CLEANUP_FAILED)
    return deleted_count, errors


def restore_gallery_person_snapshot(
    tenant_id: str,
    person_id: str,
    previous_person: PersonRecord | None,
    *,
    gallery: GalleryMapping = GALLERY,
    persist_delete_hook: PersistDeleteHook = persist_person_delete,
    persist_person_hook: PersistPersonHook = persist_person,
    persist_feature_hook: PersistFeatureHook = persist_feature,
) -> list[str]:
    errors: list[str] = []
    key = gallery_key(tenant_id, person_id)
    if previous_person is None:
        gallery.pop(key, None)
        try:
            persist_delete_hook(tenant_id, person_id)
        except Exception as exc:
            logger.warning("failed to persist restored empty gallery person deletion: %s", exception_log_summary(exc))
            errors.append("delete restored empty gallery person failed")
        return errors

    restored_person = deepcopy(previous_person)
    gallery.pop(key, None)
    try:
        persist_delete_hook(tenant_id, person_id)
    except Exception as exc:
        logger.warning("failed to persist mutated gallery person deletion before restore: %s", exception_log_summary(exc))
        errors.append("delete mutated gallery person before restore failed")
    gallery[key] = restored_person
    try:
        persist_person_hook(restored_person)
        for feature in restored_person.features:
            persist_feature_hook(restored_person, feature)
    except Exception as exc:
        logger.warning("failed to persist restored gallery person snapshot: %s", exception_log_summary(exc))
        errors.append("restore gallery person failed")
    return errors


def raise_gallery_rollback_failure(original_error: Exception, rollback_errors: list[str]) -> None:
    raise_rollback_failure("gallery mutation failed and rollback persistence failed", original_error, rollback_errors)


def rollback_gallery_mutation(
    *,
    tenant_id: str,
    person_id: str,
    previous_person: PersonRecord | None,
    created_object_infos: list[dict[str, Any]],
    original_error: Exception,
    object_store: ObjectStoreLike = OBJECT_STORE,
    gallery: GalleryMapping = GALLERY,
    persist_delete_hook: PersistDeleteHook = persist_person_delete,
    persist_person_hook: PersistPersonHook = persist_person,
    persist_feature_hook: PersistFeatureHook = persist_feature,
) -> None:
    rollback_errors: list[str] = []
    for object_info in reversed(created_object_infos):
        object_error = cleanup_object_after_failed_feature(object_info, object_store=object_store)
        if object_error:
            rollback_errors.append(object_error)
    rollback_errors.extend(
        restore_gallery_person_snapshot(
            tenant_id,
            person_id,
            previous_person,
            gallery=gallery,
            persist_delete_hook=persist_delete_hook,
            persist_person_hook=persist_person_hook,
            persist_feature_hook=persist_feature_hook,
        )
    )
    if rollback_errors:
        raise_gallery_rollback_failure(original_error, rollback_errors)