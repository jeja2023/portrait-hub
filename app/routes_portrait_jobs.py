from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_pagination import normalize_list_pagination, page_items_keyset
from app.portrait_jobs import (
    JobStatus,
    VideoJob,
    create_video_job,
    get_video_job,
    normalize_job_status,
    load_video_jobs_state,
    public_video_job_result,
    persist_video_job,
    remove_video_job,
    request_cancel_video_job,
    restore_video_job,
)
from app.portrait_response import exception_log_summary, portrait_success, raise_rollback_failure
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_runtime_store import video_jobs_snapshots
from app.portrait_request_validation import validate_int_range
from app.portrait_security import validate_job_id
from app.portrait_task_queue import TASK_QUEUE
from app.security import require_api_token
from app.settings import MAX_VIDEO_FRAMES, VIDEO_FRAME_INTERVAL, VIDEO_JOB_WORKER_IN_PROCESS
from app.video_io import delete_video_job_input, stage_video_upload


router = APIRouter(dependencies=[Depends(require_api_token)])


async def refresh_video_job_view() -> None:
    if not VIDEO_JOB_WORKER_IN_PROCESS:
        await run_blocking_io(load_video_jobs_state)


def rollback_video_job_snapshot(job: VideoJob, previous_job: VideoJob) -> list[str]:
    restore_video_job(job, previous_job)
    try:
        persist_video_job(job)
    except Exception as exc:
        logger.warning("持久化恢复后的视频任务快照失败: %s", exception_log_summary(exc))
        return ["restore 视频任务失败"]
    return []


def raise_job_rollback_failure(original_error: Exception, rollback_errors: list[str]) -> None:
    raise_rollback_failure("视频任务变更失败，且回滚持久化失败", original_error, rollback_errors)


@router.post("/v1/jobs/video", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_create_video_job(
    file: UploadFile = File(...),
    frame_interval: int = Form(VIDEO_FRAME_INTERVAL),
    max_frames: int = Form(MAX_VIDEO_FRAMES),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    frame_interval = validate_int_range("frame_interval", frame_interval, minimum=1)
    max_frames = validate_int_range("max_frames", max_frames, minimum=1, maximum=MAX_VIDEO_FRAMES)
    job = await run_blocking_io(create_video_job, None, tenant_id=tenant_id)
    input_ref: str | None = None
    queue_message: Any | None = None
    try:
        input_ref = await stage_video_upload(file, tenant_id, job.job_id)
        await run_blocking_io(
            audit_event,
            "video_job_created",
            request_id=request_id,
            tenant_id=tenant_id,
            job_id=job.job_id,
        )
        queue_message = await run_blocking_io(
            TASK_QUEUE.enqueue,
            "video_jobs",
            {
                "job_id": job.job_id,
                "tenant_id": tenant_id,
                "input_ref": input_ref,
                "frame_interval": frame_interval,
                "max_frames": max_frames,
            },
        )
    except Exception:
        if queue_message is not None:
            try:
                await run_blocking_io(TASK_QUEUE.remove, queue_message)
            except Exception as exc:
                logger.warning("移除回滚视频队列消息失败: %s", exception_log_summary(exc))
        if input_ref is not None:
            await run_blocking_io(delete_video_job_input, input_ref)
        await run_blocking_io(remove_video_job, job.job_id, tenant_id)
        raise
    assert queue_message is not None
    return portrait_success(
        request_id,
        {"job": job.public_dict(include_result=False), "queue_message": queue_message.public_dict()},
    )

@router.get("/v1/jobs/video/results", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_list_video_job_results(
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    await refresh_video_job_view()
    pagination_request = normalize_list_pagination(limit, offset, cursor)
    items: list[dict[str, Any]] = []
    for job in video_jobs_snapshots(tenant_id):
        if normalize_job_status(job.status) != JobStatus.COMPLETED:
            continue
        result = job.result if isinstance(job.result, dict) else None
        frames = result.get("frames") if isinstance(result, dict) else None
        if not isinstance(frames, list) or not frames:
            continue
        items.append({"sort_key": -float(job.updated_at or job.created_at or 0.0), "job_id": job.job_id, "job": job})
    items.sort(key=lambda item: (item["sort_key"], item["job_id"]))
    page, pagination = page_items_keyset(
        items,
        limit=pagination_request.limit,
        offset=pagination_request.offset,
        cursor=pagination_request.cursor,
        key_fields=["sort_key", "job_id"],
    )
    results = [
        {"job": item["job"].public_dict(include_result=False), "result": public_video_job_result(item["job"].result)}
        for item in page
    ]
    return portrait_success(request_id, {"results": results, **pagination})


@router.get("/v1/jobs/{job_id}", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_get_video_job(job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    job_id = validate_job_id(job_id)
    await refresh_video_job_view()
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return portrait_success(request_id, {"job": job.public_dict(include_result=False)})


@router.get("/v1/jobs/{job_id}/result", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_get_video_job_result(job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    job_id = validate_job_id(job_id)
    await refresh_video_job_view()
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if job.status != "completed":
        return portrait_success(request_id, {"job": job.public_dict(include_result=False), "result": None})
    return portrait_success(request_id, {"job": job.public_dict(include_result=False), "result": public_video_job_result(job.result)})


@router.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_cancel_video_job(job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    job_id = validate_job_id(job_id)
    await refresh_video_job_view()
    previous_job = deepcopy(get_video_job(job_id, tenant_id=tenant_id))
    cancelled = await run_blocking_io(request_cancel_video_job, job_id, tenant_id=tenant_id)
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    job = get_video_job(job_id, tenant_id=tenant_id)
    cancellation_marked = False
    try:
        if job is not None and job.cancel_requested:
            await run_blocking_io(TASK_QUEUE.mark_cancelled, "video_jobs", tenant_id, job_id)
            cancellation_marked = True
        await run_blocking_io(audit_event, "video_job_cancelled", request_id=request_id, tenant_id=tenant_id, job_id=job_id)
    except Exception as exc:
        if cancellation_marked:
            await run_blocking_io(TASK_QUEUE.clear_cancelled, "video_jobs", tenant_id, job_id)
        if job is not None and previous_job is not None:
            rollback_errors = rollback_video_job_snapshot(job, previous_job)
            if rollback_errors:
                raise_job_rollback_failure(exc, rollback_errors)
        raise
    return portrait_success(request_id, {"job": job.public_dict(include_result=False) if job else None})
