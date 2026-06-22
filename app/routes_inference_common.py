from __future__ import annotations

from collections.abc import Iterator, Sized
from contextlib import contextmanager

from fastapi import HTTPException, status

from app.metrics import observe
from app.observability import logger
from app.portrait_request_validation import validate_int_range
from app.portrait_response import exception_log_summary, raise_internal_error
from app.settings import MAX_DETECTIONS


def validate_image_files(files: Sized, *, max_images: int) -> None:
    """校验上传的图像批次非空且不超过单请求上限。"""
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one image file is required")
    if len(files) > max_images:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"too many image files: {len(files)}, max {max_images}",
        )


def validate_detection_parameters(
    *,
    confidence: float | None = None,
    iou: float | None = None,
    max_detections: int | None = None,
    max_detections_cap: int = MAX_DETECTIONS,
) -> None:
    """校验共享的 YOLO 风格检测参数。

    每个值仅在提供时才校验，因此既适用于 person 路由（字段始终有表单默认值），也适用于
    vision 路由（这些字段可选）。max_detections 复用 validate_int_range 以保持单一事实来源。
    """
    if confidence is not None and not 0 <= confidence <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confidence must be between 0 and 1")
    if iou is not None and not 0 <= iou <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="iou must be between 0 and 1")
    if max_detections is not None:
        validate_int_range("max_detections", max_detections, minimum=1, maximum=max_detections_cap)


@contextmanager
def inference_error_boundary(
    request_id: str,
    *,
    errors_metric: str,
    log_label: str,
    internal_message: str,
) -> Iterator[None]:
    """推理端点的标准错误处理尾部。

    对客户端错误（HTTPException）和服务端错误都递增该端点的错误计数；对后者记录脱敏摘要，
    并通过 raise_internal_error 把意外异常转换为只含 request-id 的 500 响应。
    """
    try:
        yield
    except HTTPException:
        observe(errors_metric)
        raise
    except Exception as exc:
        observe(errors_metric)
        logger.warning("%s: request_id=%s error=%s", log_label, request_id, exception_log_summary(exc))
        raise_internal_error(request_id, internal_message)
