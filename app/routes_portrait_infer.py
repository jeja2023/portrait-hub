from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.media.image_decode import decode_upload_images
from app.observability import request_id_from_headers
from app.portrait_auth import permission_dependency
from app.portrait_embeddings import (
    FALLBACK_EMBEDDING_MODEL_ID,
    FALLBACK_EMBEDDING_VERSION,
    appearance_record,
    body_record,
)
from app.portrait_model_runtime import (
    face_model_summary,
    infer_face_records_for_image,
    infer_gait_embedding_for_images,
    infer_pose_record_for_image,
)
from app.portrait_response import portrait_success
from app.security import require_api_token
from app.settings import MAX_VIDEO_FRAMES, MAX_VISION_IMAGES


router = APIRouter(dependencies=[Depends(require_api_token)])


def validate_file_count(files: list[UploadFile], limit: int) -> None:
    from fastapi import HTTPException, status

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one image file is required")
    if len(files) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"too many image files: {len(files)}, max {limit}",
        )


@router.post("/v1/infer/faces", dependencies=[Depends(permission_dependency("infer"))])
async def v1_infer_faces(
    request: Request,
    files: list[UploadFile] = File(...),
    include_embeddings: bool = Form(False),
    fallback_to_image: bool = Form(False),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    validate_file_count(files, MAX_VISION_IMAGES)
    decoded = await decode_upload_images(files)
    frames = []
    face_count = 0
    model_summary: dict[str, Any] | None = None
    for index, item in enumerate(decoded):
        faces = await infer_face_records_for_image(item.image, include_embeddings=include_embeddings, fallback=fallback_to_image)
        if model_summary is None:
            model_summary = face_model_summary(faces, include_embeddings=include_embeddings)
        face_count += len(faces)
        frame = item.frame.to_dict()
        frame["image_index"] = index
        frame["faces"] = faces
        frame["face_count"] = len(faces)
        frames.append(frame)
    return portrait_success(
        request_id,
        {
            "frames": frames,
            "frame_count": len(frames),
            "face_count": face_count,
            "model": model_summary
            or {
                "id": "opencv/haarcascade_frontalface_default",
                "fallback_embedding_model_id": FALLBACK_EMBEDDING_MODEL_ID if include_embeddings else None,
                "version": FALLBACK_EMBEDDING_VERSION,
            },
        },
    )


@router.post("/v1/infer/persons", dependencies=[Depends(permission_dependency("infer"))])
async def v1_infer_persons(
    request: Request,
    files: list[UploadFile] = File(...),
    include_embeddings: bool = Form(False),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    validate_file_count(files, MAX_VISION_IMAGES)
    decoded = await decode_upload_images(files)
    frames = []
    for index, item in enumerate(decoded):
        person = body_record(item.image, include_embedding=include_embeddings)
        frame = item.frame.to_dict()
        frame["image_index"] = index
        frame["persons"] = [person]
        frame["person_count"] = 1
        frames.append(frame)
    return portrait_success(
        request_id,
        {
            "frames": frames,
            "frame_count": len(frames),
            "person_count": len(frames),
            "model": {
                "id": FALLBACK_EMBEDDING_MODEL_ID,
                "version": FALLBACK_EMBEDDING_VERSION,
                "status": "whole_image_fallback",
            },
        },
    )


@router.post("/v1/infer/pose", dependencies=[Depends(permission_dependency("infer"))])
async def v1_infer_pose(
    request: Request,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    validate_file_count(files, MAX_VISION_IMAGES)
    decoded = await decode_upload_images(files)
    results = []
    for index, item in enumerate(decoded):
        frame = item.frame.to_dict()
        frame["image_index"] = index
        frame["pose"] = await infer_pose_record_for_image(item.image)
        results.append(frame)
    return portrait_success(
        request_id,
        {
            "frames": results,
            "frame_count": len(results),
            "model": {"id": "portrait_hub/geometric_pose_placeholder", "status": "placeholder"},
        },
    )


@router.post("/v1/infer/appearance", dependencies=[Depends(permission_dependency("infer"))])
async def v1_infer_appearance(
    request: Request,
    files: list[UploadFile] = File(...),
    include_embeddings: bool = Form(False),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    validate_file_count(files, MAX_VISION_IMAGES)
    decoded = await decode_upload_images(files)
    results = []
    for index, item in enumerate(decoded):
        frame = item.frame.to_dict()
        frame["image_index"] = index
        frame["appearance"] = appearance_record(item.image, include_embedding=include_embeddings)
        results.append(frame)
    return portrait_success(
        request_id,
        {
            "frames": results,
            "frame_count": len(results),
            "model": {"id": FALLBACK_EMBEDDING_MODEL_ID, "status": "color_histogram_fallback"},
        },
    )


@router.post("/v1/infer/gait", dependencies=[Depends(permission_dependency("infer"))])
async def v1_infer_gait(
    request: Request,
    files: list[UploadFile] = File(...),
    include_embedding: bool = Form(False),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    validate_file_count(files, MAX_VIDEO_FRAMES)
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
    return portrait_success(request_id, result)
