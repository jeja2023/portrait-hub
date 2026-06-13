import asyncio
import logging
from typing import Any

import numpy as np
import onnxruntime as ort
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status

from app.core import *
from app.media.stream_decode import mask_stream_url
from app.portrait_auth import permission_dependency
from app.portrait_response import exception_log_summary, raise_internal_error
from app.settings import APP_VERSION
from app.video_io import public_video_metadata


router = APIRouter()


@router.post("/infer/stream/person-tracks", dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))])
async def infer_stream_person_tracks(
    request: Request,
    stream_url: str = Form(...),
    detector_project_name: str = Form("portrait_hub"),
    detector_model_name: str = Form("yolov8n.onnx"),
    reid_project_name: str = Form("portrait_hub"),
    reid_model_name: str = Form("osnet_ibn_x1_0.onnx"),
    confidence: float = Form(0.25),
    iou: float = Form(0.45),
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
        detector_model_name,
        reid_project_name,
        reid_model_name,
    )

    if not 0 <= confidence <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confidence must be between 0 and 1")
    if not 0 <= iou <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="iou must be between 0 and 1")
    if max_detections < 1 or max_detections > 1000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_detections must be between 1 and 1000")
    if frame_interval < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="frame_interval must be >= 1")
    if max_frames < 1 or max_frames > MAX_STREAM_FRAMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"max_frames must be between 1 and {MAX_STREAM_FRAMES}")
    if read_timeout_seconds < 1 or read_timeout_seconds > STREAM_READ_TIMEOUT_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"read_timeout_seconds must be between 1 and {STREAM_READ_TIMEOUT_SECONDS}",
        )

    try:
        images, stream_meta = await asyncio.to_thread(
            extract_video_frames_from_path,
            stream_url,
            frame_interval,
            max_frames,
            read_timeout_seconds,
        )
        if not images:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no frames could be read from stream")
        filenames = [f"stream#frame-{frame_index}" for frame_index in stream_meta["source_frame_indexes"]]
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
    except HTTPException:
        observe("tracks_errors_total")
        raise
    except Exception as exc:
        observe("tracks_errors_total")
        logger.warning("stream person track inference failed: request_id=%s error=%s", request_id, exception_log_summary(exc))
        raise_internal_error(request_id, "stream person track inference runtime error")

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
