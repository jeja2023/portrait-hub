import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.image_io import load_images
from app.inference import infer_person_frames
from app.metrics import observe
from app.model_package import get_model_path
from app.model_refs import cache_key, validate_model_reference_parts
from app.observability import log_json, now, request_id_from_headers
from app.portrait_auth import permission_dependency
from app.routes_inference_common import inference_error_boundary, validate_detection_parameters, validate_image_files
from app.runtime import get_or_load_model, touch_model
from app.security import require_api_token
from app.settings import DEFAULT_CONFIDENCE, DEFAULT_DETECTOR_ARTIFACT, DEFAULT_DETECTOR_PROJECT, DEFAULT_IOU, MAX_PERSON_FRAMES


router = APIRouter()


@router.post("/infer/persons", dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))])
async def infer_persons(
    request: Request,
    files: list[UploadFile] = File(...),
    project_name: str = Form(DEFAULT_DETECTOR_PROJECT),
    artifact_name: str = Form(DEFAULT_DETECTOR_ARTIFACT, alias="model_name"),
    confidence: float = Form(DEFAULT_CONFIDENCE),
    iou: float = Form(DEFAULT_IOU),
    max_detections: int = Form(100),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    observe("persons_requests_total")
    total_start = now()

    project_name, model_name = validate_model_reference_parts(project_name, artifact_name)
    validate_image_files(files, max_images=MAX_PERSON_FRAMES)
    validate_detection_parameters(confidence=confidence, iou=iou, max_detections=max_detections)

    key = cache_key(project_name, model_name)
    model_path = get_model_path(project_name, model_name)

    with inference_error_boundary(
        request_id,
        errors_metric="persons_errors_total",
        log_label="person inference failed",
        internal_message="person inference runtime error",
    ):
        bundle, cold_loaded, load_seconds = await get_or_load_model(key, model_path)
        images, filenames, decode_seconds = await load_images(files)
        frames, infer_meta = await infer_person_frames(
            bundle,
            key,
            images,
            filenames,
            confidence=confidence,
            iou=iou,
            max_detections=max_detections,
        )

        total_seconds = now() - total_start
        await touch_model(key, bundle)
        person_count = sum(frame["person_count"] for frame in frames)
        observe("persons_detected_total", person_count)
        observe("persons_frames_total", len(frames))
        observe("decode_seconds_sum", decode_seconds)
        observe("preprocess_seconds_sum", infer_meta["timing"]["preprocess_seconds"])
        observe("postprocess_seconds_sum", infer_meta["timing"]["postprocess_seconds"])
        log_json(
            logging.INFO,
            "persons_infer_completed",
            request_id=request_id,
            model=key,
            frame_count=len(files),
            inference_mode=infer_meta["inference_mode"],
            input_shape=infer_meta["input_shape"],
            output_shapes=infer_meta["output_shapes"],
            person_count=person_count,
            cold_loaded=cold_loaded,
            decode_seconds=round(decode_seconds, 6),
            preprocess_seconds=round(infer_meta["timing"]["preprocess_seconds"], 6),
            queue_seconds=round(infer_meta["timing"]["queue_seconds"], 6),
            load_seconds=round(load_seconds, 6),
            inference_seconds=round(infer_meta["timing"]["inference_seconds"], 6),
            postprocess_seconds=round(infer_meta["timing"]["postprocess_seconds"], 6),
            total_seconds=round(total_seconds, 6),
        )

    return {
        "status": "success",
        "request_id": request_id,
        "model": key,
        "cold_loaded": cold_loaded,
        "timing": {
            "decode_seconds": decode_seconds,
            "preprocess_seconds": infer_meta["timing"]["preprocess_seconds"],
            "queue_seconds": infer_meta["timing"]["queue_seconds"],
            "load_seconds": load_seconds,
            "inference_seconds": infer_meta["timing"]["inference_seconds"],
            "postprocess_seconds": infer_meta["timing"]["postprocess_seconds"],
            "total_seconds": total_seconds,
        },
        "input_shape": infer_meta["input_shape"],
        "output_shapes": infer_meta["output_shapes"],
        "inference_mode": infer_meta["inference_mode"],
        "frames": frames,
        "frame_count": len(frames),
        "person_count": person_count,
    }
