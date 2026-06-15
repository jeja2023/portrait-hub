from copy import deepcopy
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status

from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_jobs import (
    VIDEO_JOBS,
    VideoJob,
    create_video_job,
    get_video_job,
    job_key,
    persist_video_job,
    public_video_job_result,
    remove_video_job,
    request_cancel_video_job,
    restore_video_job,
    run_video_job,
)
from app.portrait_response import exception_log_summary, portrait_success, raise_rollback_failure
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_request_validation import validate_int_range
from app.portrait_security import validate_job_id
from app.portrait_task_queue import TASK_QUEUE
from app.security import require_api_token
from app.settings import MAX_VIDEO_FRAMES, VIDEO_FRAME_INTERVAL
from app.video_io import read_video_file


router = APIRouter(dependencies=[Depends(require_api_token)])


def rollback_video_job_snapshot(job: VideoJob, previous_job: VideoJob) -> list[str]:
    restore_video_job(job, previous_job)
    VIDEO_JOBS[job_key(job.tenant_id, job.job_id)] = job
    try:
        persist_video_job(job)
    except Exception as exc:
        logger.warning("failed to persist restored video job snapshot: %s", exception_log_summary(exc))
        return ["restore video job failed"]
    return []


def raise_job_rollback_failure(original_error: Exception, rollback_errors: list[str]) -> None:
    raise_rollback_failure("video job mutation failed and rollback persistence failed", original_error, rollback_errors)


@router.post("/v1/jobs/video", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_create_video_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    frame_interval: int = Form(VIDEO_FRAME_INTERVAL),
    max_frames: int = Form(MAX_VIDEO_FRAMES),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    frame_interval = validate_int_range("frame_interval", frame_interval, minimum=1)
    max_frames = validate_int_range("max_frames", max_frames, minimum=1, maximum=MAX_VIDEO_FRAMES)
    data = await read_video_file(file)
    job = await run_blocking_io(create_video_job, None, tenant_id=tenant_id)
    try:
        queue_message = await run_blocking_io(TASK_QUEUE.enqueue, "video_jobs", {"job_id": job.job_id, "tenant_id": tenant_id})
    except Exception:
        await run_blocking_io(remove_video_job, job.job_id, tenant_id)
        raise
    background_tasks.add_task(
        run_video_job,
        job.job_id,
        tenant_id,
        data,
        file.filename,
        frame_interval,
        max_frames,
    )
    try:
        await run_blocking_io(audit_event, "video_job_created", request_id=request_id, tenant_id=tenant_id, job_id=job.job_id)
    except Exception:
        await run_blocking_io(remove_video_job, job.job_id, tenant_id)
        raise
    return portrait_success(request_id, {"job": job.public_dict(include_result=False), "queue_message": queue_message.public_dict()})


@router.get("/v1/jobs/{job_id}", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_get_video_job(job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    job_id = validate_job_id(job_id)
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return portrait_success(request_id, {"job": job.public_dict(include_result=False)})


@router.get("/v1/jobs/{job_id}/result", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_get_video_job_result(job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    job_id = validate_job_id(job_id)
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    if job.status != "completed":
        return portrait_success(request_id, {"job": job.public_dict(include_result=False), "result": None})
    return portrait_success(request_id, {"job": job.public_dict(include_result=False), "result": public_video_job_result(job.result)})


@router.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_cancel_video_job(job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    job_id = validate_job_id(job_id)
    previous_job = deepcopy(get_video_job(job_id, tenant_id=tenant_id))
    cancelled = await run_blocking_io(request_cancel_video_job, job_id, tenant_id=tenant_id)
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    job = get_video_job(job_id, tenant_id=tenant_id)
    try:
        await run_blocking_io(audit_event, "video_job_cancelled", request_id=request_id, tenant_id=tenant_id, job_id=job_id)
    except Exception as exc:
        if job is not None and previous_job is not None:
            rollback_errors = rollback_video_job_snapshot(job, previous_job)
            if rollback_errors:
                raise_job_rollback_failure(exc, rollback_errors)
        raise
    return portrait_success(request_id, {"job": job.public_dict(include_result=False) if job else None})
