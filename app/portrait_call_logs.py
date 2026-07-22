from __future__ import annotations

import threading
from collections import deque
from typing import Any

from fastapi import HTTPException, status

from app.observability import logger
from app.portrait_response import exception_log_summary
from app.settings import PORTRAIT_STORAGE_BACKEND

_CALL_LOG_LIMIT = 2_000
_CALL_LOGS: deque[dict[str, Any]] = deque(maxlen=_CALL_LOG_LIMIT)
_CALL_LOGS_LOCK = threading.RLock()


def postgres_call_logs_enabled() -> bool:
    return PORTRAIT_STORAGE_BACKEND == "postgres"


def clear_call_logs() -> None:
    with _CALL_LOGS_LOCK:
        _CALL_LOGS.clear()


def application_id_from_api_key(tenant_id: str | None, api_key: str | None) -> str | None:
    if not api_key:
        return None
    from app.portrait_access import application_key_matches, application_key_matches_any_tenant

    application = (
        application_key_matches(tenant_id, api_key) if tenant_id else application_key_matches_any_tenant(api_key)
    )
    if application is None:
        return None
    return str(application.get("app_id") or application.get("id") or "") or None


def record_call_log(
    *,
    request_id: str,
    tenant_id: str | None,
    project_id: str | None = None,
    method: str,
    path: str,
    status_code: int,
    latency_ms: int,
    created_at: float,
    application_id: str | None = None,
    model_version: str | None = None,
    worker: str | None = None,
    error_code: str | None = None,
) -> None:
    status_text = "success" if status_code < 400 else "error"
    normalized_error_code = (error_code or "").strip() or f"http_{status_code}"
    row = {
        "project_id": project_id or "default",
        "request_id": request_id,
        "tenant_id": tenant_id or "default",
        "application_id": application_id or "--",
        "method": method,
        "path": path,
        "endpoint": path,
        "status": status_text,
        "http_status": status_code,
        "error_code": None if status_text == "success" else normalized_error_code,
        "latency_ms": latency_ms,
        "model_version": model_version or "--",
        "worker": worker or "--",
        "created_at": created_at,
    }
    with _CALL_LOGS_LOCK:
        _CALL_LOGS.append(row)
    if postgres_call_logs_enabled():
        try:
            from app.portrait_postgres import insert_call_log

            insert_call_log(row)
        except Exception as exc:
            logger.warning("PostgreSQL call log insert failed: %s", exception_log_summary(exc))
    try:
        from app.portrait_access import record_application_call

        record_application_call(tenant_id, application_id, status_code, created_at)
    except Exception:
        # 调用日志持久化是尽力而为的遥测，不应改变请求结果。
        return


def list_call_logs(
    tenant_id: str,
    *,
    request_id: str | None = None,
    project_id: str | None = None,
    endpoint: str | None = None,
    status_text: str | None = None,
    application_id: str | None = None,
    error_code: str | None = None,
    created_since: float | None = None,
    created_until: float | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if postgres_call_logs_enabled():
        from app.portrait_postgres import query_call_logs

        try:
            return query_call_logs(
                tenant_id,
                request_id=request_id,
                project_id=project_id,
                endpoint=endpoint,
                status_text=status_text,
                application_id=application_id,
                error_code=error_code,
                created_since=created_since,
                created_until=created_until,
                limit=limit,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="call log storage is unavailable",
            ) from exc
    filtered = _filter_call_logs(
        tenant_id,
        request_id=request_id,
        endpoint=endpoint,
        project_id=project_id,
        status_text=status_text,
        application_id=application_id,
        error_code=error_code,
        created_since=created_since,
        created_until=created_until,
    )
    return filtered[: max(1, min(int(limit), 500))]


def summarize_call_logs(
    tenant_id: str,
    *,
    request_id: str | None = None,
    project_id: str | None = None,
    endpoint: str | None = None,
    status_text: str | None = None,
    application_id: str | None = None,
    error_code: str | None = None,
    created_since: float | None = None,
    created_until: float | None = None,
) -> dict[str, Any]:
    if postgres_call_logs_enabled():
        from app.portrait_postgres import summarize_call_logs as summarize_postgres_call_logs

        try:
            return summarize_postgres_call_logs(
                tenant_id,
                request_id=request_id,
                project_id=project_id,
                endpoint=endpoint,
                status_text=status_text,
                application_id=application_id,
                error_code=error_code,
                created_since=created_since,
                created_until=created_until,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="call log storage is unavailable",
            ) from exc
    rows = _filter_call_logs(
        tenant_id,
        request_id=request_id,
        endpoint=endpoint,
        status_text=status_text,
        project_id=project_id,
        application_id=application_id,
        error_code=error_code,
        created_since=created_since,
        created_until=created_until,
    )
    success_count = sum(1 for row in rows if row.get("status") == "success")
    error_count = len(rows) - success_count
    with _CALL_LOGS_LOCK:
        retained_count = len(_CALL_LOGS)
    oldest = min((float(row.get("created_at") or 0) for row in rows), default=None)
    newest = max((float(row.get("created_at") or 0) for row in rows), default=None)
    return {
        "request_count": len(rows),
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": success_count / len(rows) if rows else 1.0,
        "oldest_created_at": oldest,
        "newest_created_at": newest,
        "complete": retained_count < _CALL_LOG_LIMIT,
        "retained_count": retained_count,
        "retained_limit": _CALL_LOG_LIMIT,
    }


def _filter_call_logs(
    tenant_id: str,
    *,
    request_id: str | None = None,
    project_id: str | None = None,
    endpoint: str | None = None,
    status_text: str | None = None,
    application_id: str | None = None,
    error_code: str | None = None,
    created_since: float | None = None,
    created_until: float | None = None,
) -> list[dict[str, Any]]:
    normalized_request = (request_id or "").strip().lower()
    normalized_endpoint = (endpoint or "").strip().lower()
    normalized_status = (status_text or "").strip().lower()
    normalized_application = (application_id or "").strip().lower()
    normalized_error_code = (error_code or "").strip().lower()
    with _CALL_LOGS_LOCK:
        rows = list(_CALL_LOGS)
    filtered: list[dict[str, Any]] = []
    for row in reversed(rows):
        if row.get("tenant_id") != tenant_id:
            continue
        if project_id is not None and row.get("project_id", "default") != project_id:
            continue
        if normalized_request and normalized_request not in str(row.get("request_id") or "").lower():
            continue
        endpoint_text = f"{row.get('method', '')} {row.get('path', '')} {row.get('endpoint', '')}".lower()
        if normalized_endpoint and normalized_endpoint not in endpoint_text:
            continue
        if normalized_status and row.get("status") != normalized_status:
            continue
        if normalized_application and normalized_application not in str(row.get("application_id") or "").lower():
            continue
        if normalized_error_code and normalized_error_code not in str(row.get("error_code") or "").lower():
            continue
        created_at = float(row.get("created_at") or 0.0)
        if created_since is not None and created_at < float(created_since):
            continue
        if created_until is not None and created_at > float(created_until):
            continue
        filtered.append(dict(row))
    return filtered


__all__ = [
    "application_id_from_api_key",
    "clear_call_logs",
    "list_call_logs",
    "postgres_call_logs_enabled",
    "record_call_log",
    "summarize_call_logs",
]
