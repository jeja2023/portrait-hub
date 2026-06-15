import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.image_io import load_images
from app.inference_tracks import infer_tracks_for_images
from app.metrics import observe
from app.model_refs import validate_model_reference_parts
from app.observability import log_json, logger, now, request_id_from_headers
from app.portrait_auth import permission_dependency
from app.portrait_response import exception_log_summary, raise_internal_error
from app.security import require_api_token
from app.settings import MAX_PIPELINE_FRAMES


router = APIRouter()


@router.post("/infer/person-tracks", dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))])
async def infer_person_tracks(
    request: Request,
    files: list[UploadFile] = File(...),
    detector_project_name: str = Form("portrait_hub"),
    detector_artifact_name: str = Form("yolov8n.onnx", alias="detector_model_name"),
    reid_project_name: str = Form("portrait_hub"),
    reid_artifact_name: str = Form("osnet_ibn_x1_0.onnx", alias="reid_model_name"),
    confidence: float = Form(0.25),
    iou: float = Form(0.45),
    max_detections: int = Form(100),
    include_embeddings: bool = Form(False),
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

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one image file is required")
    if len(files) > MAX_PIPELINE_FRAMES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"too many image files: {len(files)}, max {MAX_PIPELINE_FRAMES}",
        )
    if not 0 <= confidence <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confidence must be between 0 and 1")
    if not 0 <= iou <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="iou must be between 0 and 1")
    if max_detections < 1 or max_detections > 1000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_detections must be between 1 and 1000")

    try:
        images, filenames, decode_seconds = await load_images(files)
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

        total_seconds = now() - total_start
        detector_meta = result["detector_meta"]
        embedding_meta = result["embedding_meta"]
        observe("decode_seconds_sum", decode_seconds)

        log_json(
            logging.INFO,
            "person_tracks_infer_completed",
            request_id=request_id,
            detector_model=result["detector_key"],
            reid_model=result["reid_key"],
            frame_count=len(result["frames"]),
            person_count=result["person_count"],
            embedding_count=result["embedding_count"],
            detector_mode=detector_meta["inference_mode"],
            reid_mode=embedding_meta["inference_mode"],
            decode_seconds=round(decode_seconds, 6),
            detector_inference_seconds=round(detector_meta["timing"]["inference_seconds"], 6),
            reid_inference_seconds=round(embedding_meta["timing"]["inference_seconds"], 6),
            total_seconds=round(total_seconds, 6),
        )
    except HTTPException:
        observe("tracks_errors_total")
        raise
    except Exception as exc:
        observe("tracks_errors_total")
        logger.warning("person track inference failed: request_id=%s error=%s", request_id, exception_log_summary(exc))
        raise_internal_error(request_id, "person track inference runtime error")

    return {
        "status": "success",
        "request_id": request_id,
        "detector_model": result["detector_key"],
        "reid_model": result["reid_key"],
        "cold_loaded": {
            "detector": result["detector_cold_loaded"],
            "reid": result["reid_cold_loaded"],
        },
        "timing": {
            "decode_seconds": decode_seconds,
            "detector_load_seconds": result["detector_load_seconds"],
            "reid_load_seconds": result["reid_load_seconds"],
            "detector": detector_meta["timing"],
            "reid": embedding_meta["timing"],
            "total_seconds": total_seconds,
        },
        "detector": {
            "input_shape": detector_meta["input_shape"],
            "output_shapes": detector_meta["output_shapes"],
            "inference_mode": detector_meta["inference_mode"],
        },
        "reid": {
            "input_shape": embedding_meta["input_shape"],
            "output_shapes": embedding_meta["output_shapes"],
            "inference_mode": embedding_meta["inference_mode"],
            "embedding_dim": embedding_meta["embedding_dim"],
            "embedding_count": result["embedding_count"],
        },
        "frames": result["frames"],
        "tracks": result["tracks"],
        "track_count": result["track_count"],
        "tracker": result["tracker"],
        "frame_count": len(result["frames"]),
        "person_count": result["person_count"],
        "embedding_count": result["embedding_count"],
    }
