from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_pagination import normalize_list_pagination, normalize_stream_event_pagination, page_items_keyset
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import exception_log_summary, portrait_success, raise_rollback_failure
from app.portrait_security import normalize_public_metadata, validate_stream_id
from app.portrait_stream_worker import (
    emit_stream_event,
    normalize_stream_analysis_settings,
    restore_stream_worker_sessions,
    start_stream_worker_session,
    stop_stream_worker_session,
    stream_session_key,
    stream_worker_sessions_snapshot,
)
from app.portrait_streams import (
    StreamRecord,
    create_stream,
    get_stream,
    persist_stream,
    refresh_streams_state,
    remove_stream,
    restore_stream,
    restore_stream_snapshot_in_store,
    start_stream,
    stop_stream,
    stream_records_snapshot,
)
from app.security import require_api_token

router = APIRouter(dependencies=[Depends(require_api_token)])


class StreamCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream_url: str = Field(..., min_length=3, max_length=2048)
    name: str | None = Field(default=None, max_length=256)
    settings: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def stream_or_404(stream_id: str, tenant_id: str) -> StreamRecord:
    stream_id = validate_stream_id(stream_id)
    stream = get_stream(stream_id, tenant_id=tenant_id)
    if stream is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="视频流不存在")
    return stream


def rollback_stream_snapshot(stream: StreamRecord, previous_stream: StreamRecord) -> list[str]:
    restore_stream(stream, previous_stream)
    restore_stream_snapshot_in_store(stream)
    try:
        persist_stream(stream)
    except Exception as exc:
        logger.warning("持久化恢复后的视频流快照失败: %s", exception_log_summary(exc))
        return ["恢复视频流失败"]
    return []


def raise_stream_rollback_failure(original_error: Exception, rollback_errors: list[str]) -> None:
    raise_rollback_failure("视频流变更失败，且回滚持久化失败", original_error, rollback_errors)


@router.post("/v1/streams", dependencies=[Depends(permission_dependency("streams"))])
async def v1_create_stream(
    payload: StreamCreateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    stream = await run_blocking_io(
        create_stream,
        payload.stream_url,
        tenant_id=tenant_id,
        name=payload.name,
        settings=normalize_stream_analysis_settings(
            normalize_public_metadata(payload.settings, field_name="settings")
        ),
        metadata=normalize_public_metadata(payload.metadata, field_name="metadata"),
    )
    try:
        await run_blocking_io(audit_event, "stream_created", request_id=request_id, tenant_id=tenant_id, stream_id=stream.stream_id)
    except Exception:
        await run_blocking_io(remove_stream, stream.stream_id, tenant_id)
        raise
    return portrait_success(request_id, {"stream": stream.public_dict(include_events=True)})


@router.get("/v1/streams", dependencies=[Depends(permission_dependency("streams:read"))])
async def v1_list_streams(
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    await run_blocking_io(refresh_streams_state)
    pagination_request = normalize_list_pagination(limit, offset, cursor)
    tenant_streams = [stream for stream in sorted(stream_records_snapshot(), key=lambda item: item.stream_id) if stream.tenant_id == tenant_id]
    streams, pagination = page_items_keyset(
        tenant_streams,
        limit=pagination_request.limit,
        offset=pagination_request.offset,
        cursor=pagination_request.cursor,
        key_fields=["stream_id"],
    )
    return portrait_success(
        request_id,
        {
            "streams": [stream.public_dict(include_events=False) for stream in streams],
            **pagination,
        },
    )


@router.get("/v1/streams/{stream_id}", dependencies=[Depends(permission_dependency("streams:read"))])
async def v1_get_stream(
    stream_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    await run_blocking_io(refresh_streams_state)
    stream = stream_or_404(stream_id, tenant_id)
    return portrait_success(request_id, {"stream": stream.public_dict(include_events=True)})


@router.post("/v1/streams/{stream_id}/start", dependencies=[Depends(permission_dependency("streams"))])
async def v1_start_stream(
    stream_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    await run_blocking_io(refresh_streams_state)
    stream = stream_or_404(stream_id, tenant_id)
    previous_stream = deepcopy(stream)
    previous_worker_sessions = stream_worker_sessions_snapshot()
    try:
        stream = await run_blocking_io(start_stream, stream)
        emit_stream_event(stream, "stream_worker_start_requested", "stream worker start requested", {"status": stream.status})
        if stream.status == "running":
            start_stream_worker_session(stream)
        await run_blocking_io(
            audit_event,
            "stream_started",
            request_id=request_id,
            tenant_id=tenant_id,
            stream_id=stream.stream_id,
            status=stream.status,
        )
    except Exception as exc:
        restore_stream_worker_sessions(previous_worker_sessions)
        rollback_errors = await run_blocking_io(rollback_stream_snapshot, stream, previous_stream)
        if rollback_errors:
            raise_stream_rollback_failure(exc, rollback_errors)
        raise
    return portrait_success(request_id, {"stream": stream.public_dict(include_events=True)})


@router.post("/v1/streams/{stream_id}/stop", dependencies=[Depends(permission_dependency("streams"))])
async def v1_stop_stream(
    stream_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    await run_blocking_io(refresh_streams_state)
    stream = stream_or_404(stream_id, tenant_id)
    previous_stream = deepcopy(stream)
    previous_worker_sessions = stream_worker_sessions_snapshot()
    try:
        stream = await run_blocking_io(stop_stream, stream)
        emit_stream_event(stream, "stream_worker_stop_requested", "stream worker stop requested")
        if stream_session_key(stream) in previous_worker_sessions:
            stop_stream_worker_session(stream)
        await run_blocking_io(audit_event, "stream_stopped", request_id=request_id, tenant_id=tenant_id, stream_id=stream.stream_id)
    except Exception as exc:
        restore_stream_worker_sessions(previous_worker_sessions)
        rollback_errors = await run_blocking_io(rollback_stream_snapshot, stream, previous_stream)
        if rollback_errors:
            raise_stream_rollback_failure(exc, rollback_errors)
        raise
    return portrait_success(request_id, {"stream": stream.public_dict(include_events=True)})


@router.get("/v1/streams/{stream_id}/status", dependencies=[Depends(permission_dependency("streams:read"))])
async def v1_stream_status(
    stream_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    await run_blocking_io(refresh_streams_state)
    stream = stream_or_404(stream_id, tenant_id)
    return portrait_success(
        request_id,
        {
            "stream_id": stream.stream_id,
            "status": stream.status,
            "updated_at": stream.updated_at,
            "event_count": len(stream.events),
        },
    )


@router.get("/v1/streams/{stream_id}/events", dependencies=[Depends(permission_dependency("streams:read"))])
async def v1_stream_events(
    stream_id: str,
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    pagination_request = normalize_stream_event_pagination(limit, offset, cursor)
    tenant_id = ctx.tenant_id
    await run_blocking_io(refresh_streams_state)
    stream = stream_or_404(stream_id, tenant_id)
    events, pagination = page_items_keyset(
        stream.events,
        limit=pagination_request.limit,
        offset=pagination_request.offset,
        cursor=pagination_request.cursor,
        key_fields=["created_at", "event_id"],
    )
    return portrait_success(
        request_id,
        {
            "stream_id": stream.stream_id,
            "events": [event.public_dict() for event in events],
            **pagination,
        },
    )
