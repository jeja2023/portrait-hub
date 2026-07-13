import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.inference_tracks import infer_tracks_for_images
from app.metrics import observe, observe_video_sampling_metrics
from app.model_refs import validate_model_reference_parts
from app.observability import log_json, now, request_id_from_headers
from app.portrait_auth import permission_dependency
from app.portrait_request_validation import validate_int_range
from app.routes_inference_common import inference_error_boundary, validate_detection_parameters
from app.security import require_api_token
from app.settings import (
    DEFAULT_CONFIDENCE,
    DEFAULT_DETECTOR_ARTIFACT,
    DEFAULT_DETECTOR_PROJECT,
    DEFAULT_IOU,
    DEFAULT_REID_ARTIFACT,
    MAX_VIDEO_FRAMES,
    VIDEO_FRAME_INTERVAL,
)
from app.video_io import extract_video_frames_from_upload
from app.video_io import public_video_metadata


router = APIRouter()


@router.post("/infer/video/person-tracks", dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))])
async def infer_video_person_tracks(
    request: Request,
    file: UploadFile = File(...),
    detector_project_name: str = Form(DEFAULT_DETECTOR_PROJECT),
    detector_artifact_name: str = Form(DEFAULT_DETECTOR_ARTIFACT, alias="detector_model_name"),
    reid_project_name: str = Form(DEFAULT_DETECTOR_PROJECT),
    reid_artifact_name: str = Form(DEFAULT_REID_ARTIFACT, alias="reid_model_name"),
    confidence: float = Form(DEFAULT_CONFIDENCE),
    iou: float = Form(DEFAULT_IOU),
    max_detections: int = Form(100),
    include_embeddings: bool = Form(False),
    frame_interval: int = Form(VIDEO_FRAME_INTERVAL),
    max_frames: int = Form(MAX_VIDEO_FRAMES),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    observe("tracks_requests_total")
    total_start = now()

    detector_project_name, detector_model_name, reid_project_name, reid_model_name = validate_model_reference_parts(
        detector_project_name,
        detector_artifact_name,
        reid_project_name,
        reid_artifact_name,
    )

    validate_detection_parameters(confidence=confidence, iou=iou, max_detections=max_detections)
    validate_int_range("frame_interval", frame_interval, minimum=1)
    validate_int_range("max_frames", max_frames, minimum=1, maximum=MAX_VIDEO_FRAMES)

    with inference_error_boundary(
        request_id,
        errors_metric="tracks_errors_total",
        log_label="video person track inference failed",
        internal_message="视频人员轨迹推理运行时错误",
    ):
        images, video_meta = await extract_video_frames_from_upload(file, frame_interval, max_frames)
        if not images:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no frames could be extracted from video")
        filenames: list[str | None] = [f"video#frame-{frame_index}" for frame_index in video_meta["source_frame_indexes"]]
        result = await infer_tracks_for_images(
            images,
            filenames,
            detector_project_name,
            detector_model_name,
            reid_project_name,
            reid_model_name,
            confidence=confidence,
            iou=iou,
            max_detections=max_detections,
            include_embeddings=include_embeddings,
        )
        for frame, source_frame_index in zip(result["frames"], video_meta["source_frame_indexes"]):
            frame["source_frame_index"] = source_frame_index
            if video_meta.get("fps"):
                frame["source_seconds"] = round(source_frame_index / video_meta["fps"], 6)

        total_seconds = now() - total_start
        observe("decode_seconds_sum", video_meta["decode_seconds"])
        observe_video_sampling_metrics(video_meta)
        log_json(
            logging.INFO,
            "video_person_tracks_completed",
            request_id=request_id,
            detector_model=result["detector_key"],
            reid_model=result["reid_key"],
            filename_present=bool(file.filename),
            extracted_frames=len(images),
            person_count=result["person_count"],
            total_seconds=round(total_seconds, 6),
        )

    detector_meta = result["detector_meta"]
    embedding_meta = result["embedding_meta"]
    return {
        "status": "success",
        "request_id": request_id,
        "source_type": "video_upload",
        "video": public_video_metadata(video_meta),
        "detector_model": result["detector_key"],
        "reid_model": result["reid_key"],
        "timing": {
            "video_decode_seconds": video_meta["decode_seconds"],
            "detector_load_seconds": result["detector_load_seconds"],
            "reid_load_seconds": result["reid_load_seconds"],
            "detector": detector_meta["timing"],
            "reid": embedding_meta["timing"],
            "total_seconds": total_seconds,
        },
        "frames": result["frames"],
        "tracks": result["tracks"],
        "track_count": result["track_count"],
        "tracker": result["tracker"],
        "frame_count": len(result["frames"]),
        "person_count": result["person_count"],
        "embedding_count": result["embedding_count"],
    }
