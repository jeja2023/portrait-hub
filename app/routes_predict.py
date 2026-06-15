import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.metrics import observe
from app.model_package import get_model_path
from app.model_refs import cache_key
from app.observability import log_json, logger, now, request_id_from_headers
from app.portrait_auth import permission_dependency
from app.portrait_response import exception_log_summary, raise_internal_error
from app.runtime import build_input_array, get_or_load_model, input_dtype, run_model_bundle, touch_model
from app.schemas import InferenceRequest
from app.security import require_api_token


router = APIRouter()


@router.post("/predict", dependencies=[Depends(require_api_token), Depends(permission_dependency("infer"))])
async def predict(req: InferenceRequest, request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    observe("predict_requests_total")
    total_start = now()
    key = cache_key(req.project_name, req.model_name)
    model_path = get_model_path(req.project_name, req.model_name)

    try:
        bundle, cold_loaded, load_seconds = await get_or_load_model(key, model_path)
        session = bundle["session"]
        input_meta = session.get_inputs()[0]
        input_array = build_input_array(req.tensor_data, input_dtype(input_meta.type))

        raw_outputs, queue_seconds, inference_seconds = await run_model_bundle(bundle, input_array)
        total_seconds = now() - total_start
        output_shapes = [list(output.shape) for output in raw_outputs]
        await touch_model(key, bundle)
        log_json(
            logging.INFO,
            "predict_completed",
            request_id=request_id,
            model=key,
            input_shape=list(input_array.shape),
            input_dtype=str(input_array.dtype),
            output_shapes=output_shapes,
            cold_loaded=cold_loaded,
            queue_seconds=round(queue_seconds, 6),
            load_seconds=round(load_seconds, 6),
            inference_seconds=round(inference_seconds, 6),
            total_seconds=round(total_seconds, 6),
        )
    except HTTPException:
        observe("predict_errors_total")
        raise
    except Exception as exc:
        observe("predict_errors_total")
        logger.warning("inference failed: request_id=%s error=%s", request_id, exception_log_summary(exc))
        raise_internal_error(request_id, "inference runtime error")

    return {
        "status": "success",
        "request_id": request_id,
        "model": key,
        "cold_loaded": cold_loaded,
        "timing": {
            "queue_seconds": queue_seconds,
            "load_seconds": load_seconds,
            "inference_seconds": inference_seconds,
            "total_seconds": total_seconds,
        },
        "input_shape": list(input_array.shape),
        "output_shapes": output_shapes,
        "outputs": [output.tolist() for output in raw_outputs],
    }
