import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.image_io import load_images
from app.inference import infer_classification_images, infer_detection_images, infer_reid_images
from app.metrics import observe
from app.model_config import model_config, model_task, resolve_model_reference
from app.model_package import get_model_path, model_package_info
from app.observability import log_json, logger, now, request_id_from_headers
from app.portrait_auth import permission_dependency
from app.portrait_response import exception_log_summary, raise_internal_error
from app.runtime import get_or_load_model, touch_model
from app.security import require_api_token
from app.settings import MAX_VISION_IMAGES


router = APIRouter()


@router.post("/vision/infer", dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))])
@router.post("/vision/batch-infer", dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))])
async def vision_infer(
    request: Request,
    files: list[UploadFile] = File(...),
    requested_model_id: str | None = Form(None, alias="model_id"),
    project_name: str | None = Form(None),
    requested_model_name: str | None = Form(None, alias="model_name"),
    task: str | None = Form(None),
    confidence: float | None = Form(None),
    iou: float | None = Form(None),
    max_detections: int | None = Form(None),
    top_k: int | None = Form(None),
    include_vectors: bool = Form(False),
    traffic_key: str | None = Form(None),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    observe("vision_requests_total")
    total_start = now()

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one image file is required")
    if len(files) > MAX_VISION_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"too many image files: {len(files)}, max {MAX_VISION_IMAGES}",
        )
    if confidence is not None and not 0 <= confidence <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confidence must be between 0 and 1")
    if iou is not None and not 0 <= iou <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="iou must be between 0 and 1")
    if max_detections is not None and (max_detections < 1 or max_detections > 1000):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_detections must be between 1 and 1000")
    if top_k is not None and (top_k < 1 or top_k > 100):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="top_k must be between 1 and 100")

    try:
        rollout_key = traffic_key or request_id
        project, model, key, alias_name = resolve_model_reference(
            requested_model_id,
            project_name,
            requested_model_name,
            traffic_key=rollout_key,
        )
        config = model_config(key)
        task_name = model_task({"task": task} if task else config)
        model_path = get_model_path(project, model)
        bundle, cold_loaded, load_seconds = await get_or_load_model(key, model_path)
        images, filenames, decode_seconds = await load_images(files)

        if task_name in {"detection", "detect", "yolo"}:
            results, infer_meta = await infer_detection_images(
                bundle,
                key,
                images,
                filenames,
                confidence=confidence,
                iou=iou,
                max_detections=max_detections,
            )
            task_name = "detection"
            result_count = sum(item["detection_count"] for item in results)
        elif task_name in {"classification", "classify", "classifier"}:
            results, infer_meta = await infer_classification_images(
                bundle,
                key,
                images,
                filenames,
                top_k=top_k,
            )
            task_name = "classification"
            result_count = sum(item["prediction_count"] for item in results)
        elif task_name in {"reid", "embedding", "embeddings"}:
            embeddings, infer_meta = await infer_reid_images(bundle, key, images)
            results = []
            for index, _filename in enumerate(filenames):
                item: dict[str, Any] = {
                    "image_index": index,
                    "width": images[index].width,
                    "height": images[index].height,
                    "embedding_dim": infer_meta["embedding_dim"],
                }
                if include_vectors:
                    item["embedding"] = [round(float(value), 8) for value in embeddings[index].tolist()]
                results.append(item)
            task_name = "reid"
            result_count = len(results)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="unsupported vision task",
            )

        total_seconds = now() - total_start
        await touch_model(key, bundle)
        observe("vision_images_total", len(images))
        observe("decode_seconds_sum", decode_seconds)
        observe("preprocess_seconds_sum", infer_meta["timing"]["preprocess_seconds"])
        observe("postprocess_seconds_sum", infer_meta["timing"]["postprocess_seconds"])

        package = model_package_info(key, model_path, bundle["model_hash"])
        log_json(
            logging.INFO,
            "vision_infer_completed",
            request_id=request_id,
            model=key,
            alias=alias_name,
            traffic_key=rollout_key if alias_name else None,
            task=task_name,
            image_count=len(images),
            result_count=result_count,
            inference_mode=infer_meta["inference_mode"],
            input_shape=infer_meta["input_shape"],
            output_shapes=infer_meta["output_shapes"],
            cold_loaded=cold_loaded,
            decode_seconds=round(decode_seconds, 6),
            preprocess_seconds=round(infer_meta["timing"]["preprocess_seconds"], 6),
            queue_seconds=round(infer_meta["timing"]["queue_seconds"], 6),
            load_seconds=round(load_seconds, 6),
            inference_seconds=round(infer_meta["timing"]["inference_seconds"], 6),
            postprocess_seconds=round(infer_meta["timing"]["postprocess_seconds"], 6),
            total_seconds=round(total_seconds, 6),
        )
    except HTTPException:
        observe("vision_errors_total")
        raise
    except Exception as exc:
        observe("vision_errors_total")
        logger.warning("vision inference failed: request_id=%s error=%s", request_id, exception_log_summary(exc))
        raise_internal_error(request_id, "vision inference runtime error")

    return {
        "status": "success",
        "request_id": request_id,
        "model": {
            "id": alias_name or requested_model_id or key,
            "alias": alias_name,
            "traffic_key": rollout_key if alias_name else None,
            "project_name": project,
            "model_name": model,
            "key": key,
            "task": task_name,
            "type": package.get("type"),
            "runtime": package.get("runtime"),
            "version": package.get("version"),
            "precision": package.get("precision"),
            "hash": bundle["model_hash"],
        },
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
        "parameters": infer_meta.get("parameters", {}),
        "results": results,
        "image_count": len(results),
        "result_count": result_count,
    }
