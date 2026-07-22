import logging
from typing import Any

from fastapi import Depends, File, Form, UploadFile

from app.api_contracts import ContractAPIRouter as APIRouter
from app.api_contracts import InferTracksResponse
from app.image_io import load_images
from app.inference_tracks import infer_tracks_for_images
from app.metrics import observe
from app.model_refs import validate_model_reference_parts
from app.observability import log_json, now
from app.portrait_analysis_archive import create_analysis_archive
from app.portrait_async import run_blocking_io
from app.portrait_auth import permission_dependency
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.routes_inference_common import (
    inference_error_boundary,
    validate_detection_parameters,
    validate_image_files,
)
from app.security import require_api_token
from app.settings import (
    DEFAULT_CONFIDENCE,
    DEFAULT_DETECTOR_ARTIFACT,
    DEFAULT_DETECTOR_PROJECT,
    DEFAULT_IOU,
    DEFAULT_REID_ARTIFACT,
    MAX_PIPELINE_FRAMES,
)

router = APIRouter()


@router.post(
    "/v1/infer/tracks",
    response_model=InferTracksResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))],
)
async def infer_person_tracks(
    files: list[UploadFile] = File(...),
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
    observe("tracks_requests_total")
    total_start = now()

    detector_project_name, detector_model_name, reid_project_name, reid_model_name = validate_model_reference_parts(
        detector_project_name,
        detector_artifact_name,
        reid_project_name,
        reid_artifact_name,
    )

    validate_image_files(files, max_images=MAX_PIPELINE_FRAMES)
    validate_detection_parameters(confidence=confidence, iou=iou, max_detections=max_detections)

    with inference_error_boundary(
        request_id,
        errors_metric="tracks_errors_total",
        log_label="person track inference failed",
        internal_message="人员轨迹推理运行时错误",
    ):
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

    response_data = {
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
    await run_blocking_io(
        create_analysis_archive,
        tenant_id=ctx.scope_id,
        request_id=request_id,
        source_type="image",
        source_ref=request_id,
        mode="tracks",
        endpoint="/v1/infer/tracks",
        payload=response_data,
        images=images,
    )
    return portrait_success(request_id, response_data)
