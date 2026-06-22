import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status

from app.inference_tracks import infer_tracks_for_images
from app.media.stream_decode import mask_stream_url
from app.metrics import observe
from app.model_refs import validate_model_reference_parts
from app.observability import log_json, now, request_id_from_headers
from app.portrait_auth import permission_dependency
from app.portrait_request_validation import validate_int_range
from app.routes_inference_common import inference_error_boundary, validate_detection_parameters
from app.security import require_api_token
from app.settings import (
    ALLOW_STREAM_URLS,
    DEFAULT_CONFIDENCE,
    DEFAULT_DETECTOR_ARTIFACT,
    DEFAULT_DETECTOR_PROJECT,
    DEFAULT_IOU,
    DEFAULT_REID_ARTIFACT,
    MAX_STREAM_FRAMES,
    STREAM_FRAME_INTERVAL,
    STREAM_READ_TIMEOUT_SECONDS,
)
from app.video_io import extract_video_frames_from_path, public_video_metadata, validate_stream_url


router = APIRouter()


@router.post("/infer/stream/person-tracks", dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))])
async def infer_stream_person_tracks(
    request: Request,
    stream_url: str = Form(...),
    detector_project_name: str = Form(DEFAULT_DETECTOR_PROJECT),
    detector_artifact_name: str = Form(DEFAULT_DETECTOR_ARTIFACT, alias="detector_model_name"),
    reid_project_name: str = Form(DEFAULT_DETECTOR_PROJECT),
    reid_artifact_name: str = Form(DEFAULT_REID_ARTIFACT, alias="reid_model_name"),
    confidence: float = Form(DEFAULT_CONFIDENCE),
    iou: float = Form(DEFAULT_IOU),
    max_detections: int = Form(100),
    include_embeddings: bool = Form(False),
    frame_interval: int = Form(STREAM_FRAME_INTERVAL),
    max_frames: int = Form(MAX_STREAM_FRAMES),
    read_timeout_seconds: int = Form(STREAM_READ_TIMEOUT_SECONDS),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    observe("tracks_requests_total")
    total_start = now()

    if not ALLOW_STREAM_URLS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="stream URL pulling is disabled. Set ALLOW_STREAM_URLS=true to enable it for trusted networks.",
        )

    stream_url = validate_stream_url(stream_url)
    detector_project_name, detector_model_name, reid_project_name, reid_model_name = validate_model_reference_parts(
        detector_project_name,
        detector_artifact_name,
        reid_project_name,
        reid_artifact_name,
    )

    validate_detection_parameters(confidence=confidence, iou=iou, max_detections=max_detections)
    validate_int_range("frame_interval", frame_interval, minimum=1)
    validate_int_range("max_frames", max_frames, minimum=1, maximum=MAX_STREAM_FRAMES)
    validate_int_range("read_timeout_seconds", read_timeout_seconds, minimum=1, maximum=STREAM_READ_TIMEOUT_SECONDS)

    with inference_error_boundary(
        request_id,
        errors_metric="tracks_errors_total",
        log_label="stream person track inference failed",
        internal_message="stream person track inference runtime error",
    ):
        images, stream_meta = await asyncio.to_thread(
            extract_video_frames_from_path,
            stream_url,
            frame_interval,
            max_frames,
            read_timeout_seconds,
        )
        if not images:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no frames could be read from stream")
        filenames: list[str | None] = [f"stream#frame-{frame_index}" for frame_index in stream_meta["source_frame_indexes"]]
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
        for frame, source_frame_index in zip(result["frames"], stream_meta["source_frame_indexes"]):
            frame["source_frame_index"] = source_frame_index
            if stream_meta.get("fps"):
                frame["source_seconds"] = round(source_frame_index / stream_meta["fps"], 6)

        total_seconds = now() - total_start
        observe("decode_seconds_sum", stream_meta["decode_seconds"])
        log_json(
            logging.INFO,
            "stream_person_tracks_completed",
            request_id=request_id,
            detector_model=result["detector_key"],
            reid_model=result["reid_key"],
            extracted_frames=len(images),
            person_count=result["person_count"],
            total_seconds=round(total_seconds, 6),
        )

    detector_meta = result["detector_meta"]
    embedding_meta = result["embedding_meta"]
    return {
        "status": "success",
        "request_id": request_id,
        "source_type": "stream",
        "stream": {
            **public_video_metadata(stream_meta),
            "url": mask_stream_url(stream_url),
            "read_timeout_seconds": read_timeout_seconds,
        },
        "detector_model": result["detector_key"],
        "reid_model": result["reid_key"],
        "timing": {
            "stream_read_seconds": stream_meta["decode_seconds"],
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
