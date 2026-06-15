import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from app.portrait_auth import has_permission, jwt_tenant_matches, roles_from_claims, verify_hs256_jwt
from app.portrait_jobs import get_video_job
from app.portrait_security import validate_job_id, validate_stream_id
from app.portrait_streams import get_stream
from app.settings import API_LIST_DEFAULT_LIMIT, API_TOKEN, AUTH_REQUIRED, RBAC_ENABLED


router = APIRouter()


def websocket_tenant(websocket: WebSocket) -> str:
    return websocket.headers.get("x-tenant-id") or websocket.query_params.get("tenant_id") or "default"


async def require_websocket_permission(websocket: WebSocket, permission: str, tenant_id: str) -> bool:
    authorization = websocket.headers.get("authorization") or ""
    query_bearer = websocket.query_params.get("access_token") or ""
    token = websocket.query_params.get("token") or websocket.headers.get("x-api-key") or ""
    bearer = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else query_bearer
    if RBAC_ENABLED:
        if not bearer:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return False
        try:
            claims = verify_hs256_jwt(bearer)
        except HTTPException:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return False
        if not jwt_tenant_matches(claims, tenant_id) or not has_permission(roles_from_claims(claims), permission):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return False
        return True
    if AUTH_REQUIRED and not API_TOKEN:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    if API_TOKEN and token != API_TOKEN and bearer != API_TOKEN:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    return True


async def send_until_closed(websocket: WebSocket, payload_factory: Any, *, interval_seconds: float = 1.0) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(payload_factory())
            await asyncio.sleep(interval_seconds)
    except WebSocketDisconnect:
        return


@router.websocket("/ws/jobs/{job_id}")
async def ws_job_progress(websocket: WebSocket, job_id: str) -> None:
    tenant_id = websocket_tenant(websocket)
    if not await require_websocket_permission(websocket, "jobs:read", tenant_id):
        return
    try:
        job_id = validate_job_id(job_id)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    def payload() -> dict[str, Any]:
        job = get_video_job(job_id, tenant_id=tenant_id)
        if job is None:
            return {"status": "not_found", "job_id": job_id, "tenant_id": tenant_id}
        return {"status": "success", "job": job.public_dict(include_result=False)}

    await send_until_closed(websocket, payload)


@router.websocket("/ws/streams/{stream_id}")
async def ws_stream_events(websocket: WebSocket, stream_id: str) -> None:
    tenant_id = websocket_tenant(websocket)
    if not await require_websocket_permission(websocket, "streams:read", tenant_id):
        return
    try:
        stream_id = validate_stream_id(stream_id)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    limit = min(max(1, int(websocket.query_params.get("limit", API_LIST_DEFAULT_LIMIT))), 200)

    def payload() -> dict[str, Any]:
        stream = get_stream(stream_id, tenant_id=tenant_id)
        if stream is None:
            return {"status": "not_found", "stream_id": stream_id, "tenant_id": tenant_id}
        events = [event.public_dict() for event in stream.events[-limit:]]
        return {"status": "success", "stream": stream.public_dict(include_events=False), "events": events}

    await send_until_closed(websocket, payload)
