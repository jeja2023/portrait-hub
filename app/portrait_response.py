from typing import Any

from fastapi import HTTPException, status

from app.observability import logger

HEALTH_CHECK_FAILED = "健康检查失败"
MODEL_READINESS_CHECK_FAILED = "模型就绪检查失败"
OBJECT_CLEANUP_FAILED = "对象清理失败"
OBJECT_DELETE_FAILED = "对象删除失败"


def portrait_success(
    request_id: str,
    data: dict[str, Any] | None = None,
    *,
    warnings: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "success",
        "request_id": request_id,
        "data": data or {},
    }
    if warnings:
        payload["warnings"] = warnings
    if meta:
        payload["meta"] = meta
    return payload


def capability_payload(name: str, status: str, reason: str | None = None) -> dict[str, Any]:
    payload = {"name": name, "status": status}
    if reason:
        payload["reason"] = reason
    return payload


def raise_internal_error(request_id: str, detail: str = "内部服务器错误") -> None:
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"message": detail, "request_id": request_id},
    )


def exception_log_summary(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": type(exc).__name__}
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        payload["status_code"] = status_code
    detail = getattr(exc, "detail", None)
    if detail is not None:
        payload["detail_type"] = type(detail).__name__
    return payload


def raise_rollback_failure(message: str, original_error: Exception, rollback_errors: list[str]) -> None:
    logger.error(
        "%s: original_error=%s rollback_error_count=%s",
        message,
        exception_log_summary(original_error),
        len(rollback_errors),
    )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "message": message,
            "rollback_failed": True,
            "rollback_error_count": len(rollback_errors),
        },
    ) from original_error
