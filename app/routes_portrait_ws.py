import asyncio
import inspect
from typing import Any, cast

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from app.portrait_access import (
    application_record_for_key,
    application_scopes_allow_permission,
    project_is_active,
)
from app.portrait_async import run_blocking_io
from app.portrait_auth import has_permission, jwt_tenant_matches, roles_from_claims, verify_hs256_jwt
from app.portrait_console_access import consume_console_ws_ticket
from app.portrait_jobs import get_video_job
from app.portrait_projects import (
    DEFAULT_PROJECT_ID,
    project_ids_from_claims,
    project_scope_id,
    publicize_project_scope,
    validate_project_id,
)
from app.portrait_security import validate_job_id, validate_stream_id
from app.portrait_streams import get_stream, refresh_streams_state
from app.settings import API_LIST_DEFAULT_LIMIT, API_TOKEN, AUTH_REQUIRED, RBAC_ENABLED

router = APIRouter()


def websocket_tenant(websocket: WebSocket) -> str:
    return websocket.headers.get("x-tenant-id") or websocket.query_params.get("tenant_id") or "default"


def websocket_project(websocket: WebSocket) -> str:
    raw_project = websocket.headers.get("x-project-id") or websocket.query_params.get("project_id")
    return validate_project_id(raw_project or DEFAULT_PROJECT_ID)


async def require_websocket_permission(
    websocket: WebSocket,
    permission: str,
    tenant_id: str,
    project_id: str,
    *,
    resource_type: str,
    resource_id: str,
) -> bool:
    if not await run_blocking_io(project_is_active, tenant_id, project_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    ticket = websocket.query_params.get("ticket") or ""
    if ticket:
        if consume_console_ws_ticket(
            ticket,
            tenant_id=tenant_id,
            project_id=project_id,
            resource_type=resource_type,
            resource_id=resource_id,
            permission=permission,
        ):
            return True
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    # 契约（方案 §8.4）：WebSocket URL 只允许携带一次性 ticket，主凭证（API Key/JWT）
    # 禁止经 query 参数传递，仅接受请求头，避免凭证落入访问日志与浏览器历史。
    authorization = websocket.headers.get("authorization") or ""
    token = websocket.headers.get("x-api-key") or ""
    bearer = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    if API_TOKEN and (token == API_TOKEN or bearer == API_TOKEN):
        return True
    if RBAC_ENABLED and bearer:
        try:
            claims = verify_hs256_jwt(bearer)
        except HTTPException:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return False
        claimed_projects = project_ids_from_claims(claims)
        if claimed_projects is not None and project_id not in claimed_projects:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return False
        if not jwt_tenant_matches(claims, tenant_id) or not has_permission(roles_from_claims(claims), permission):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return False
        return True
    if token:
        application = await run_blocking_io(application_record_for_key, tenant_id, token)
        if (
            application is not None
            and str(application.get("project_id") or DEFAULT_PROJECT_ID) == project_id
            and application_scopes_allow_permission(application.get("scopes"), permission)
        ):
            return True
    if AUTH_REQUIRED or RBAC_ENABLED:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    return True


async def send_until_closed(websocket: WebSocket, payload_factory: Any, *, interval_seconds: float = 1.0) -> None:
    await websocket.accept()
    try:
        while True:
            payload = payload_factory()
            if inspect.isawaitable(payload):
                payload = await payload
            if isinstance(payload, dict):
                payload = {"schema_version": "1.0", **payload}
            await websocket.send_json(payload)
            await asyncio.sleep(interval_seconds)
    except WebSocketDisconnect:
        return


@router.websocket("/ws/jobs/{job_id}")
async def ws_job_progress(websocket: WebSocket, job_id: str) -> None:
    tenant_id = websocket_tenant(websocket)
    project_id = websocket_project(websocket)
    scope_id = project_scope_id(tenant_id, project_id)
    try:
        job_id = validate_job_id(job_id)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not await require_websocket_permission(
        websocket,
        "jobs:read",
        tenant_id,
        project_id,
        resource_type="job",
        resource_id=job_id,
    ):
        return

    def payload() -> dict[str, Any]:
        job = get_video_job(job_id, tenant_id=scope_id)
        if job is None:
            return {"status": "not_found", "job_id": job_id, "tenant_id": tenant_id, "project_id": project_id}
        return cast(
            dict[str, Any],
            publicize_project_scope(
                {"status": "success", "job": job.public_dict(include_result=isinstance(job.result, dict))}
            ),
        )

    await send_until_closed(websocket, payload)


@router.websocket("/ws/streams/{stream_id}")
async def ws_stream_events(websocket: WebSocket, stream_id: str) -> None:
    tenant_id = websocket_tenant(websocket)
    project_id = websocket_project(websocket)
    scope_id = project_scope_id(tenant_id, project_id)
    try:
        stream_id = validate_stream_id(stream_id)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not await require_websocket_permission(
        websocket,
        "streams:read",
        tenant_id,
        project_id,
        resource_type="stream",
        resource_id=stream_id,
    ):
        return
    limit = min(max(1, int(websocket.query_params.get("limit", API_LIST_DEFAULT_LIMIT))), 200)

    async def payload() -> dict[str, Any]:
        await run_blocking_io(refresh_streams_state)
        stream = get_stream(stream_id, tenant_id=scope_id)
        if stream is None:
            return {"status": "not_found", "stream_id": stream_id, "tenant_id": tenant_id, "project_id": project_id}
        events = [event.public_dict() for event in stream.events[-limit:]]
        return cast(
            dict[str, Any],
            publicize_project_scope(
                {"status": "success", "stream": stream.public_dict(include_events=False), "events": events}
            ),
        )

    await send_until_closed(websocket, payload)
