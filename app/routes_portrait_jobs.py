import math
from copy import deepcopy
from typing import Any

from fastapi import (
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)

from app.api_contracts import ContractAPIRouter as APIRouter
from app.model_refs import validate_model_reference_parts
from app.observability import logger
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_jobs import (
    JobStatus,
    VideoJob,
    create_video_job,
    get_video_job,
    load_video_jobs_state,
    persist_video_job,
    public_video_job_result,
    remove_video_job,
    request_cancel_video_job,
    restore_video_job,
)
from app.portrait_pagination import normalize_list_pagination, page_items_keyset
from app.portrait_request_context import (
    PortraitRequestContext,
    portrait_request_context,
)
from app.portrait_request_validation import validate_int_range
from app.portrait_response import (
    exception_log_summary,
    portrait_success,
    raise_rollback_failure,
)
from app.portrait_runtime_store import video_jobs_snapshots
from app.portrait_security import validate_job_id
from app.portrait_task_queue import TASK_QUEUE
from app.routes_inference_common import validate_detection_parameters
from app.security import require_api_token
from app.settings import (
    DEFAULT_CONFIDENCE,
    DEFAULT_DETECTOR_ARTIFACT,
    DEFAULT_DETECTOR_PROJECT,
    DEFAULT_IOU,
    DEFAULT_REID_ARTIFACT,
    INFERENCE_BATCH_SIZE_LIMIT,
    VIDEO_INFERENCE_BATCH_SIZE,
    VIDEO_JOB_WORKER_IN_PROCESS,
    VIDEO_SAMPLE_INTERVAL_SECONDS,
)
from app.video_io import delete_video_job_input, stage_video_upload

router = APIRouter(dependencies=[Depends(require_api_token)])


async def refresh_video_job_view() -> None:
    if not VIDEO_JOB_WORKER_IN_PROCESS:
        await run_blocking_io(load_video_jobs_state)
        # Ref: video_jobs_snapshots(tenant_id)


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


@router.get("/v1/jobs", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_list_jobs(
    kind: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    created_since: float | None = Query(None),
    created_until: float | None = Query(None),
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    allowed_kinds = {"video", "batch"}
    if kind is not None and kind not in allowed_kinds:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="kind 必须为 video 或 batch")
    allowed_statuses = {str(value) for value in JobStatus}
    if status_filter is not None and status_filter not in allowed_statuses:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="status 无效")
    if created_since is not None and created_until is not None and created_since > created_until:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="created_since 不能晚于 created_until"
        )

    await refresh_video_job_view()
    rows: list[dict[str, Any]] = []
    for job in video_jobs_snapshots(ctx.scope_id):
        job_kind = "batch" if job.job_id.startswith("batch_") else "video"
        normalized_status = str(job.status)
        if kind is not None and job_kind != kind:
            continue
        if status_filter is not None and normalized_status != status_filter:
            continue
        if created_since is not None and job.created_at < created_since:
            continue
        if created_until is not None and job.created_at > created_until:
            continue
        row = job.public_dict(include_result=False)
        row["kind"] = job_kind
        row["sort_created_at"] = -float(job.created_at)
        rows.append(row)

    rows.sort(key=lambda item: (item["sort_created_at"], item["job_id"]))
    pagination_request = normalize_list_pagination(limit, offset, cursor)
    page, pagination = page_items_keyset(
        rows,
        limit=pagination_request.limit,
        offset=pagination_request.offset,
        cursor=pagination_request.cursor,
        key_fields=["sort_created_at", "job_id"],
    )
    public_page = [{key: value for key, value in item.items() if key != "sort_created_at"} for item in page]
    return portrait_success(ctx.request_id, {"items": public_page, "jobs": public_page, **pagination})


@router.post("/v1/jobs/video", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_create_video_job(
    file: UploadFile = File(...),
    sample_interval_seconds: float = Form(VIDEO_SAMPLE_INTERVAL_SECONDS),
    batch_size: int = Form(VIDEO_INFERENCE_BATCH_SIZE),
    detector_project_name: str = Form(DEFAULT_DETECTOR_PROJECT),
    detector_artifact_name: str = Form(DEFAULT_DETECTOR_ARTIFACT, alias="detector_model_name"),
    reid_project_name: str = Form(DEFAULT_DETECTOR_PROJECT),
    reid_artifact_name: str = Form(DEFAULT_REID_ARTIFACT, alias="reid_model_name"),
    confidence: float = Form(DEFAULT_CONFIDENCE),
    iou: float = Form(DEFAULT_IOU),
    max_detections: int = Form(100),
    include_embeddings: bool = Form(False),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.scope_id
    if not math.isfinite(sample_interval_seconds) or sample_interval_seconds <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sample_interval_seconds 必须大于 0")
    batch_size = validate_int_range("batch_size", batch_size, minimum=1, maximum=INFERENCE_BATCH_SIZE_LIMIT)
    detector_project_name, detector_model_name, reid_project_name, reid_model_name = validate_model_reference_parts(
        detector_project_name,
        detector_artifact_name,
        reid_project_name,
        reid_artifact_name,
    )
    validate_detection_parameters(confidence=confidence, iou=iou, max_detections=max_detections)
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
                "sample_interval_seconds": sample_interval_seconds,
                "batch_size": batch_size,
                "detector_project_name": detector_project_name,
                "detector_model_name": detector_model_name,
                "reid_project_name": reid_project_name,
                "reid_model_name": reid_model_name,
                "confidence": confidence,
                "iou": iou,
                "max_detections": max_detections,
                "include_embeddings": include_embeddings,
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
        {
            "job": job.public_dict(include_result=False),
            "queue_message": queue_message.public_dict(),
        },
    )


@router.get("/v1/jobs/{job_id}", dependencies=[Depends(permission_dependency("jobs:read"))])
async def v1_get_video_job(
    job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.scope_id
    job_id = validate_job_id(job_id)
    await refresh_video_job_view()
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return portrait_success(request_id, {"job": job.public_dict(include_result=False)})


@router.get(
    "/v1/jobs/{job_id}/result",
    dependencies=[Depends(permission_dependency("jobs:read"))],
)
async def v1_get_video_job_result(
    job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.scope_id
    job_id = validate_job_id(job_id)
    await refresh_video_job_view()
    job = get_video_job(job_id, tenant_id=tenant_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if job.status != "completed":
        return portrait_success(request_id, {"job": job.public_dict(include_result=False), "result": None})
    return portrait_success(
        request_id,
        {
            "job": job.public_dict(include_result=False),
            "result": public_video_job_result(job.result),
        },
    )


@router.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(permission_dependency("jobs"))])
async def v1_cancel_video_job(
    job_id: str, ctx: PortraitRequestContext = Depends(portrait_request_context)
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.scope_id
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
        await run_blocking_io(
            audit_event,
            "video_job_cancelled",
            request_id=request_id,
            tenant_id=tenant_id,
            job_id=job_id,
        )
    except Exception as exc:
        if cancellation_marked:
            await run_blocking_io(TASK_QUEUE.clear_cancelled, "video_jobs", tenant_id, job_id)
        if job is not None and previous_job is not None:
            rollback_errors = rollback_video_job_snapshot(job, previous_job)
            if rollback_errors:
                raise_job_rollback_failure(exc, rollback_errors)
        raise
    return portrait_success(request_id, {"job": job.public_dict(include_result=False) if job else None})
