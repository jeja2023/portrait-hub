from typing import Any

from fastapi import Depends, File, Form, UploadFile

from app.api_contracts import ContractAPIRouter as APIRouter
from app.api_contracts import (
    InferAppearanceResponse,
    InferFacesResponse,
    InferGaitResponse,
    InferPersonsResponse,
    InferPoseResponse,
)
from app.media.image_decode import decode_upload_images
from app.portrait_analysis_archive import create_analysis_archive
from app.portrait_async import run_blocking_io
from app.portrait_auth import permission_dependency
from app.portrait_embeddings import (
    FALLBACK_EMBEDDING_MODEL_ID,
    FALLBACK_EMBEDDING_VERSION,
)
from app.portrait_model_runtime import (
    face_model_summary,
    infer_appearance_record_for_image,
    infer_body_record_for_image,
    infer_face_records_for_image,
    infer_gait_embedding_for_images,
    infer_pose_record_for_image,
)
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.security import require_api_token
from app.settings import MAX_VIDEO_FRAME_UPLOADS, MAX_VISION_IMAGES

router = APIRouter(dependencies=[Depends(require_api_token)])


async def archive_image_response(
    ctx: PortraitRequestContext,
    *,
    mode: str,
    endpoint: str,
    payload: dict[str, Any],
    decoded: list[Any],
) -> dict[str, Any]:
    await run_blocking_io(
        create_analysis_archive,
        tenant_id=ctx.scope_id,
        request_id=ctx.request_id,
        source_type="image",
        source_ref=ctx.request_id,
        mode=mode,
        endpoint=endpoint,
        payload=payload,
        images=[item.image for item in decoded],
    )
    return portrait_success(ctx.request_id, payload)


def validate_file_count(files: list[UploadFile], limit: int) -> None:
    from fastapi import HTTPException, status

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要一个图片文件")
    if len(files) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"图片文件过多：{len(files)}，最大 {limit}",
        )


@router.post(
    "/v1/infer/faces",
    response_model=InferFacesResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(permission_dependency("infer"))],
)
async def v1_infer_faces(
    files: list[UploadFile] = File(...),
    include_embeddings: bool = Form(False),
    fallback_to_image: bool = Form(False),
    confidence: float | None = Form(None),
    iou: float | None = Form(None),
    max_detections: int | None = Form(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    validate_file_count(files, MAX_VISION_IMAGES)
    decoded = await decode_upload_images(files)
    confidence = max(0.0, min(1.0, confidence)) if confidence is not None else None
    iou = max(0.0, min(1.0, iou)) if iou is not None else None
    max_detections = max(1, min(256, max_detections)) if max_detections is not None else None
    frames = []
    face_count = 0
    model_summary: dict[str, Any] | None = None
    for index, item in enumerate(decoded):
        faces = await infer_face_records_for_image(
            item.image,
            include_embeddings=include_embeddings,
            fallback=fallback_to_image,
            confidence=confidence,
            iou=iou,
            max_detections=max_detections,
        )
        if model_summary is None:
            model_summary = face_model_summary(faces, include_embeddings=include_embeddings)
        face_count += len(faces)
        frame = item.frame.to_dict()
        frame["image_index"] = index
        frame["faces"] = faces
        frame["face_count"] = len(faces)
        frames.append(frame)
    response_data = {
        "frames": frames,
        "frame_count": len(frames),
        "face_count": face_count,
        "model": model_summary
        or {
            "id": "opencv/haarcascade_frontalface_default",
            "fallback_embedding_model_id": FALLBACK_EMBEDDING_MODEL_ID if include_embeddings else None,
            "version": FALLBACK_EMBEDDING_VERSION,
        },
    }
    return await archive_image_response(
        ctx,
        mode="faces",
        endpoint="/v1/infer/faces",
        payload=response_data,
        decoded=decoded,
    )


@router.post(
    "/v1/infer/persons",
    response_model=InferPersonsResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(permission_dependency("infer"))],
)
async def v1_infer_persons(
    files: list[UploadFile] = File(...),
    include_embeddings: bool = Form(False),
    confidence: float | None = Form(None),
    iou: float | None = Form(None),
    max_detections: int | None = Form(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    validate_file_count(files, MAX_VISION_IMAGES)
    decoded = await decode_upload_images(files)
    confidence = max(0.0, min(1.0, confidence)) if confidence is not None else None
    iou = max(0.0, min(1.0, iou)) if iou is not None else None
    max_detections = max(1, min(256, max_detections)) if max_detections is not None else None
    frames = []
    model_summary: dict[str, Any] | None = None
    for index, item in enumerate(decoded):
        person = await infer_body_record_for_image(
            item.image,
            include_embedding=include_embeddings,
            confidence=confidence,
            iou=iou,
            max_detections=max_detections,
        )
        if model_summary is None:
            model_summary = {
                "id": person.get("embedding_model_id", FALLBACK_EMBEDDING_MODEL_ID),
                "version": person.get("embedding_model_version", FALLBACK_EMBEDDING_VERSION),
                "status": person.get("model_status", person.get("embedding_model_status", "whole_image_fallback")),
                "adapter": person.get("embedding_adapter"),
                "embedding_dim": person.get("embedding_dim", 0) if include_embeddings else 0,
            }
        frame = item.frame.to_dict()
        frame["image_index"] = index
        frame["persons"] = [person]
        frame["person_count"] = 1
        frames.append(frame)
    response_data = {
        "frames": frames,
        "frame_count": len(frames),
        "person_count": len(frames),
        "model": model_summary
        or {
            "id": FALLBACK_EMBEDDING_MODEL_ID,
            "version": FALLBACK_EMBEDDING_VERSION,
            "status": "whole_image_fallback",
            "embedding_dim": 0,
        },
    }
    return await archive_image_response(
        ctx,
        mode="persons",
        endpoint="/v1/infer/persons",
        payload=response_data,
        decoded=decoded,
    )


@router.post(
    "/v1/infer/pose",
    response_model=InferPoseResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(permission_dependency("infer"))],
)
async def v1_infer_pose(
    files: list[UploadFile] = File(...),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    validate_file_count(files, MAX_VISION_IMAGES)
    decoded = await decode_upload_images(files)
    results = []
    for index, item in enumerate(decoded):
        frame = item.frame.to_dict()
        frame["image_index"] = index
        frame["pose"] = await infer_pose_record_for_image(item.image)
        results.append(frame)
    response_data = {
        "frames": results,
        "frame_count": len(results),
        "model": {
            "id": "portrait_hub/geometric_pose_placeholder",
            "status": "placeholder",
        },
    }
    return await archive_image_response(
        ctx,
        mode="pose",
        endpoint="/v1/infer/pose",
        payload=response_data,
        decoded=decoded,
    )


@router.post(
    "/v1/infer/appearance",
    response_model=InferAppearanceResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(permission_dependency("infer"))],
)
async def v1_infer_appearance(
    files: list[UploadFile] = File(...),
    include_embeddings: bool = Form(False),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    validate_file_count(files, MAX_VISION_IMAGES)
    decoded = await decode_upload_images(files)
    results = []
    model_summary: dict[str, Any] | None = None
    for index, item in enumerate(decoded):
        appearance = await infer_appearance_record_for_image(item.image, include_embedding=include_embeddings)
        if model_summary is None:
            model_summary = {
                "id": appearance.get("model_id", FALLBACK_EMBEDDING_MODEL_ID),
                "version": appearance.get("model_version", FALLBACK_EMBEDDING_VERSION),
                "status": appearance.get("model_status", "color_histogram_fallback"),
                "adapter": appearance.get("adapter"),
                "embedding_dim": appearance.get("embedding_dim", 0) if include_embeddings else 0,
            }
        frame = item.frame.to_dict()
        frame["image_index"] = index
        frame["appearance"] = appearance
        results.append(frame)
    response_data = {
        "frames": results,
        "frame_count": len(results),
        "model": model_summary
        or {
            "id": FALLBACK_EMBEDDING_MODEL_ID,
            "version": FALLBACK_EMBEDDING_VERSION,
            "status": "color_histogram_fallback",
            "embedding_dim": 0,
        },
    }
    return await archive_image_response(
        ctx,
        mode="appearance",
        endpoint="/v1/infer/appearance",
        payload=response_data,
        decoded=decoded,
    )


@router.post(
    "/v1/infer/gait",
    response_model=InferGaitResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(permission_dependency("infer"))],
)
async def v1_infer_gait(
    files: list[UploadFile] = File(...),
    include_embedding: bool = Form(False),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    validate_file_count(files, MAX_VIDEO_FRAME_UPLOADS)
    decoded = await decode_upload_images(files)
    embedding, meta = await infer_gait_embedding_for_images([item.image for item in decoded])
    result: dict[str, Any] = {
        "tracklet": {
            "frame_count": len(decoded),
            "quality": meta.get("quality"),
            "reason": meta.get("reason"),
            "embedding_dim": meta.get("embedding_dim", 0),
        },
        "model": {
            "id": meta.get("model_id", FALLBACK_EMBEDDING_MODEL_ID),
            "version": meta.get("model_version", FALLBACK_EMBEDDING_VERSION),
            "status": meta.get("model_status", "not_available"),
        },
    }
    if include_embedding and embedding is not None:
        result["tracklet"]["embedding"] = embedding
    return await archive_image_response(
        ctx,
        mode="gait",
        endpoint="/v1/infer/gait",
        payload=result,
        decoded=decoded,
    )
