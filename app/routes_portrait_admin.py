from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_gallery import (
    GALLERY,
    PersonRecord,
    delete_person as delete_gallery_person,
    feature_object_infos,
    gallery_key,
    list_gallery_people,
    persist_feature,
    persist_person,
)
from app.portrait_jobs import VIDEO_JOBS, VideoJob, job_key, persist_video_job, remove_video_job
from app.portrait_model_capabilities import MODEL_CAPABILITIES
from app.portrait_object_storage import OBJECT_STORE
from app.portrait_pagination import normalize_list_pagination, normalize_stream_event_pagination, page_items_keyset
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import OBJECT_CLEANUP_FAILED, exception_log_summary, portrait_success, raise_rollback_failure
from app.portrait_security import redact_sensitive_fields
from app.portrait_storage import GALLERY_STORE
from app.portrait_stream_worker import stream_worker_status
from app.portrait_streams import STREAMS, StreamRecord, persist_stream, restore_stream, stream_key
from app.portrait_task_queue import TASK_QUEUE
from app.portrait_thresholds import threshold_snapshot
from app.portrait_vector_store import VECTOR_STORE
from app.security import require_api_token
from app.settings import (
    API_TOKEN,
    AUDIT_WRITE_FAIL_CLOSED,
    ENCRYPTION_KEY,
    ENCRYPTION_KEY_ID,
    ENCRYPTION_KEYRING,
    JWT_AUDIENCE,
    JWT_REQUIRE_AUD,
    JWT_REQUIRE_EXP,
    JWT_REQUIRE_ISS,
    JWT_REQUIRE_TENANT,
    JWT_SECRET,
    JWT_SECRET_ID,
    JWT_SECRET_KEYRING,
    PORTRAIT_OBJECT_STORAGE_BACKEND,
    PORTRAIT_STORAGE_BACKEND,
    PORTRAIT_VECTOR_BACKEND,
    RBAC_ENABLED,
    REQUIRE_ENCRYPTION,
    TASK_QUEUE_BACKEND,
    TENANT_HEADER_REQUIRED,
)


router = APIRouter(dependencies=[Depends(require_api_token)])


class RetentionCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retention_days: int = Field(..., ge=0, le=3650)
    confirm: str | None = Field(default=None)


def rollback_retention_cleanup(
    removed_jobs: list[VideoJob],
    trimmed_streams: list[tuple[StreamRecord, StreamRecord]],
    removed_gallery_people: list[PersonRecord],
) -> list[str]:
    errors: list[str] = []
    for person in reversed(removed_gallery_people):
        restored_person = deepcopy(person)
        GALLERY[gallery_key(restored_person.tenant_id, restored_person.person_id)] = restored_person
        try:
            persist_person(restored_person)
            for feature in restored_person.features:
                persist_feature(restored_person, feature)
        except Exception as exc:
            logger.warning("failed to persist restored gallery person during retention rollback: %s", exception_log_summary(exc))
            errors.append("restore retained gallery person failed")

    for stream, previous_stream in reversed(trimmed_streams):
        restore_stream(stream, previous_stream)
        STREAMS[stream_key(stream.tenant_id, stream.stream_id)] = stream
    for job in reversed(removed_jobs):
        VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job

    for stream, _ in reversed(trimmed_streams):
        try:
            persist_stream(stream)
        except Exception as exc:
            logger.warning("failed to persist restored stream during retention rollback: %s", exception_log_summary(exc))
            errors.append("restore retained stream failed")
    for job in reversed(removed_jobs):
        try:
            persist_video_job(job)
        except Exception as exc:
            logger.warning("failed to persist restored video job during retention rollback: %s", exception_log_summary(exc))
            errors.append("restore retained video job failed")
    return errors


def raise_retention_cleanup_rollback_failure(original_error: Exception, rollback_errors: list[str]) -> None:
    raise_rollback_failure("retention cleanup failed and rollback persistence failed", original_error, rollback_errors)


def cleanup_retained_gallery_feature_objects(person: PersonRecord) -> tuple[int, list[str]]:
    deleted_count = 0
    errors: list[str] = []
    for object_info in feature_object_infos(person):
        try:
            result = OBJECT_STORE.delete_object(object_info)
            if result.get("deleted"):
                deleted_count += 1
                continue
            logger.warning(
                "object cleanup during retention did not delete gallery object: backend=%s reason=%s",
                result.get("backend"),
                result.get("reason"),
            )
            errors.append(OBJECT_CLEANUP_FAILED)
        except Exception as exc:
            logger.warning("failed to cleanup gallery object during retention: %s", exception_log_summary(exc))
            errors.append(OBJECT_CLEANUP_FAILED)
    return deleted_count, errors


def admin_health_snapshot() -> dict[str, Any]:
    return {
        "storage": GALLERY_STORE.health(),
        "vector_store": VECTOR_STORE.health(),
        "object_storage": OBJECT_STORE.health(),
        "task_queue": TASK_QUEUE.health(),
        "stream_worker": stream_worker_status(),
    }


def retention_cleanup_transaction(
    *,
    request_id: str,
    tenant_id: str,
    retention_days: int,
) -> dict[str, Any]:
    import time

    cutoff = time.time() - retention_days * 86400
    gallery_candidates = [
        deepcopy(person)
        for person in sorted(GALLERY.values(), key=lambda item: item.person_id)
        if person.tenant_id == tenant_id and person.updated_at < cutoff
    ]
    removed_jobs = 0
    trimmed_events = 0
    removed_gallery_people = 0
    deleted_gallery_objects = 0
    removed_job_snapshots: list[VideoJob] = []
    trimmed_stream_snapshots: list[tuple[StreamRecord, StreamRecord]] = []
    removed_gallery_snapshots: list[PersonRecord] = []

    try:
        audit_event(
            "retention_cleanup",
            request_id=request_id,
            tenant_id=tenant_id,
            outcome="started",
            retention_days=retention_days,
            candidate_gallery_people=len(gallery_candidates),
            candidate_gallery_feature_count=sum(len(person.features) for person in gallery_candidates),
            candidate_gallery_object_reference_count=sum(len(feature_object_infos(person)) for person in gallery_candidates),
        )

        for job in list(VIDEO_JOBS.values()):
            if job.tenant_id == tenant_id and job.updated_at < cutoff:
                previous_job = deepcopy(job)
                if remove_video_job(job.job_id, tenant_id):
                    removed_job_snapshots.append(previous_job)
                    removed_jobs += 1

        for stream in list(STREAMS.values()):
            if stream.tenant_id != tenant_id:
                continue
            before = len(stream.events)
            retained_events = [event for event in stream.events if event.created_at >= cutoff]
            if before == len(retained_events):
                continue
            previous_stream = deepcopy(stream)
            stream.events = retained_events
            trimmed_stream_snapshots.append((stream, previous_stream))
            persist_stream(stream)
            trimmed_events += before - len(retained_events)

        for previous_person in gallery_candidates:
            if delete_gallery_person(previous_person.person_id, tenant_id=tenant_id):
                removed_gallery_snapshots.append(previous_person)
                deleted_object_count, object_cleanup_errors = cleanup_retained_gallery_feature_objects(previous_person)
                if object_cleanup_errors:
                    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=OBJECT_CLEANUP_FAILED)
                deleted_gallery_objects += deleted_object_count
                removed_gallery_people += 1
    except Exception as exc:
        rollback_errors = rollback_retention_cleanup(removed_job_snapshots, trimmed_stream_snapshots, removed_gallery_snapshots)
        if rollback_errors:
            raise_retention_cleanup_rollback_failure(exc, rollback_errors)
        raise

    return {
        "tenant_id": tenant_id,
        "retention_days": retention_days,
        "removed_jobs": removed_jobs,
        "trimmed_stream_events": trimmed_events,
        "removed_gallery_people": removed_gallery_people,
        "deleted_gallery_objects": deleted_gallery_objects,
    }


@router.get("/v1/admin/status", dependencies=[Depends(permission_dependency("admin:status"))])
async def v1_admin_status(ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    health = await run_blocking_io(admin_health_snapshot)
    return portrait_success(
        request_id,
        {
            "tenant_id": tenant_id,
            **health,
            "security": {
                "api_token_enabled": bool(API_TOKEN),
                "jwt_configured": bool(JWT_SECRET),
                "jwt_secret_id_configured": bool(JWT_SECRET_ID),
                "jwt_secret_keyring_configured": bool(JWT_SECRET_KEYRING),
                "rbac_enabled": RBAC_ENABLED,
                "jwt_audience": JWT_AUDIENCE,
                "jwt_require_exp": JWT_REQUIRE_EXP,
                "jwt_require_iss": JWT_REQUIRE_ISS,
                "jwt_require_aud": JWT_REQUIRE_AUD,
                "jwt_require_tenant": JWT_REQUIRE_TENANT,
                "tenant_header_required": TENANT_HEADER_REQUIRED,
                "encryption_enabled": bool(ENCRYPTION_KEY),
                "encryption_key_id_configured": bool(ENCRYPTION_KEY_ID),
                "encryption_keyring_configured": bool(ENCRYPTION_KEYRING),
                "require_encryption": REQUIRE_ENCRYPTION,
                "audit_write_fail_closed": AUDIT_WRITE_FAIL_CLOSED,
            },
            "configured_backends": {
                "gallery": PORTRAIT_STORAGE_BACKEND,
                "vector": PORTRAIT_VECTOR_BACKEND,
                "object_storage": PORTRAIT_OBJECT_STORAGE_BACKEND,
                "task_queue": TASK_QUEUE_BACKEND,
            },
            "model_capabilities": MODEL_CAPABILITIES,
        },
    )


@router.get("/v1/admin/export", dependencies=[Depends(permission_dependency("admin:export"))])
async def v1_admin_export(
    people_limit: int | None = Query(None),
    people_offset: int | None = Query(None),
    people_cursor: str | None = Query(None),
    jobs_limit: int | None = Query(None),
    jobs_offset: int | None = Query(None),
    jobs_cursor: str | None = Query(None),
    streams_limit: int | None = Query(None),
    streams_offset: int | None = Query(None),
    streams_cursor: str | None = Query(None),
    stream_events_limit: int | None = Query(None),
    stream_events_offset: int | None = Query(None),
    stream_events_cursor: str | None = Query(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    people_request = normalize_list_pagination(people_limit, people_offset, people_cursor)
    jobs_request = normalize_list_pagination(jobs_limit, jobs_offset, jobs_cursor)
    streams_request = normalize_list_pagination(streams_limit, streams_offset, streams_cursor)
    events_request = normalize_stream_event_pagination(stream_events_limit, stream_events_offset, stream_events_cursor)

    people, people_page = page_items_keyset(
        sorted(list_gallery_people(tenant_id=tenant_id), key=lambda item: item["person_id"]),
        limit=people_request.limit,
        offset=people_request.offset,
        cursor=people_request.cursor,
        key_fields=["person_id"],
    )
    jobs, jobs_page = page_items_keyset(
        sorted((job for job in VIDEO_JOBS.values() if job.tenant_id == tenant_id), key=lambda item: item.job_id),
        limit=jobs_request.limit,
        offset=jobs_request.offset,
        cursor=jobs_request.cursor,
        key_fields=["job_id"],
    )
    streams, streams_page = page_items_keyset(
        sorted((stream for stream in STREAMS.values() if stream.tenant_id == tenant_id), key=lambda item: item.stream_id),
        limit=streams_request.limit,
        offset=streams_request.offset,
        cursor=streams_request.cursor,
        key_fields=["stream_id"],
    )
    stream_payloads = []
    event_pages = {}
    for stream in streams:
        event_page, pagination = page_items_keyset(
            stream.events,
            limit=events_request.limit,
            offset=events_request.offset,
            cursor=events_request.cursor,
            key_fields=["created_at", "event_id"],
        )
        payload = stream.public_dict(include_events=False)
        payload["events"] = [event.public_dict() for event in event_page]
        payload["events_pagination"] = pagination
        stream_payloads.append(payload)
        event_pages[stream.stream_id] = pagination

    export_payload = {
        "tenant_id": tenant_id,
        "people": people,
        "thresholds": threshold_snapshot(),
        "model_capabilities": MODEL_CAPABILITIES,
        "jobs": [job.public_dict(include_result=False) for job in jobs],
        "streams": stream_payloads,
        "pagination": {
            "people": people_page,
            "jobs": jobs_page,
            "streams": streams_page,
            "stream_events": event_pages,
        },
    }
    await run_blocking_io(
        audit_event,
        "admin_export",
        request_id=request_id,
        tenant_id=tenant_id,
        people_count=len(people),
        people_total=people_page["total"],
        jobs_count=len(jobs),
        jobs_total=jobs_page["total"],
        streams_count=len(stream_payloads),
        streams_total=streams_page["total"],
        stream_events_count=sum(page["count"] for page in event_pages.values()),
        stream_count=len(event_pages),
        people_limit=people_page["limit"],
        people_offset=people_page["offset"],
        jobs_limit=jobs_page["limit"],
        jobs_offset=jobs_page["offset"],
        streams_limit=streams_page["limit"],
        streams_offset=streams_page["offset"],
        stream_events_limit=events_request.limit,
        stream_events_offset=events_request.offset,
        stream_events_cursor=events_request.cursor,
    )
    return portrait_success(
        request_id,
        redact_sensitive_fields(export_payload),
    )


@router.post("/v1/admin/retention/cleanup", dependencies=[Depends(permission_dependency("admin:retention"))])
async def v1_admin_retention_cleanup(
    payload: RetentionCleanupRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    if payload.confirm is not None and payload.confirm != "cleanup":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='enter "cleanup" in confirm to run retention cleanup',
        )

    result = await run_blocking_io(
        retention_cleanup_transaction,
        request_id=request_id,
        tenant_id=tenant_id,
        retention_days=payload.retention_days,
    )
    return portrait_success(request_id, result)
