import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from PIL import Image

from app.image_io import load_images
from app.inference import (
    infer_classification_images,
    infer_detection_images,
    infer_reid_images,
)
from app.metrics import observe
from app.model_config import model_config, model_task, resolve_model_reference
from app.model_package import get_model_path, model_package_info
from app.observability import log_json, now
from app.portrait_async import run_blocking_io
from app.portrait_auth import permission_dependency
from app.portrait_image_results import (
    create_image_analysis_result,
    image_analysis_results_snapshot,
)
from app.portrait_pagination import normalize_list_pagination, page_items_keyset
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_request_validation import validate_int_range
from app.portrait_response import portrait_success
from app.routes_inference_common import (
    inference_error_boundary,
    validate_detection_parameters,
    validate_image_files,
)
from app.runtime import get_or_load_model, touch_model
from app.schemas import ModelBundle
from app.security import require_api_token
from app.settings import MAX_TOP_K, MAX_VISION_IMAGES

router = APIRouter()

DETECTION_TASKS = {"detection", "detect", "yolo"}
CLASSIFICATION_TASKS = {"classification", "classify", "classifier"}
REID_TASKS = {"reid", "embedding", "embeddings"}


async def _run_detection_task(
    bundle: ModelBundle,
    key: str,
    images: list[Image.Image],
    filenames: list[str | None],
    *,
    confidence: float | None,
    iou: float | None,
    max_detections: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    results, infer_meta = await infer_detection_images(
        bundle,
        key,
        images,
        filenames,
        confidence=confidence,
        iou=iou,
        max_detections=max_detections,
    )
    return results, infer_meta, sum(item["detection_count"] for item in results)


async def _run_classification_task(
    bundle: ModelBundle,
    key: str,
    images: list[Image.Image],
    filenames: list[str | None],
    *,
    top_k: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    results, infer_meta = await infer_classification_images(
        bundle, key, images, filenames, top_k=top_k
    )
    return results, infer_meta, sum(item["prediction_count"] for item in results)


async def _run_reid_task(
    bundle: ModelBundle,
    key: str,
    images: list[Image.Image],
    filenames: list[str | None],
    *,
    include_vectors: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    embeddings, infer_meta = await infer_reid_images(bundle, key, images)
    results: list[dict[str, Any]] = []
    for index, _filename in enumerate(filenames):
        item: dict[str, Any] = {
            "image_index": index,
            "width": images[index].width,
            "height": images[index].height,
            "embedding_dim": infer_meta["embedding_dim"],
        }
        if include_vectors:
            item["embedding"] = [
                round(float(value), 8) for value in embeddings[index].tolist()
            ]
        results.append(item)
    return results, infer_meta, len(results)


@router.get(
    "/v1/vision/results",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))],
)
async def v1_list_image_analysis_results(
    limit: int | None = Query(None),
    offset: int | None = Query(None),
    cursor: str | None = Query(None),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    pagination_request = normalize_list_pagination(limit, offset, cursor)
    items: list[dict[str, Any]] = [
        {
            "sort_key": -float(record.created_at),
            "result_id": record.result_id,
            "record": record,
        }
        for record in image_analysis_results_snapshot(ctx.tenant_id)
    ]
    items.sort(key=lambda item: (item["sort_key"], item["result_id"]))
    page, pagination = page_items_keyset(
        items,
        limit=pagination_request.limit,
        offset=pagination_request.offset,
        cursor=pagination_request.cursor,
        key_fields=["sort_key", "result_id"],
    )
    return portrait_success(
        ctx.request_id,
        {
            "results": [item["record"].public_dict() for item in page],
            **pagination,
        },
    )


@router.post(
    "/v1/vision/infer",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))],
)
async def vision_infer(
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
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    observe("vision_requests_total")
    total_start = now()

    validate_image_files(files, max_images=MAX_VISION_IMAGES)
    validate_detection_parameters(
        confidence=confidence, iou=iou, max_detections=max_detections
    )
    if top_k is not None:
        validate_int_range("top_k", top_k, minimum=1, maximum=MAX_TOP_K)

    with inference_error_boundary(
        request_id,
        errors_metric="vision_errors_total",
        log_label="vision inference failed",
        internal_message="图片推理运行时错误",
    ):
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

        if task_name in DETECTION_TASKS:
            task_name = "detection"
            results, infer_meta, result_count = await _run_detection_task(
                bundle,
                key,
                images,
                filenames,
                confidence=confidence,
                iou=iou,
                max_detections=max_detections,
            )
        elif task_name in CLASSIFICATION_TASKS:
            task_name = "classification"
            results, infer_meta, result_count = await _run_classification_task(
                bundle, key, images, filenames, top_k=top_k
            )
        elif task_name in REID_TASKS:
            task_name = "reid"
            results, infer_meta, result_count = await _run_reid_task(
                bundle, key, images, filenames, include_vectors=include_vectors
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不支持的视觉任务",
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

    response_data = {
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
    await run_blocking_io(
        create_image_analysis_result,
        tenant_id=ctx.tenant_id,
        request_id=request_id,
        mode=task_name,
        endpoint="/v1/vision/infer",
        payload=response_data,
        images=images,
        filenames=filenames,
    )
    return portrait_success(request_id, response_data)
