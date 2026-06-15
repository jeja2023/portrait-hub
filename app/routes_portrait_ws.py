import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.portrait_jobs import get_video_job
from app.portrait_security import validate_job_id
from app.portrait_streams import get_stream
from app.settings import API_LIST_DEFAULT_LIMIT


router = APIRouter()


def websocket_tenant(websocket: WebSocket) -> str:
    return websocket.headers.get("x-tenant-id") or websocket.query_params.get("tenant_id") or "default"


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
    job_id = validate_job_id(job_id)

    def payload() -> dict[str, Any]:
        job = get_video_job(job_id, tenant_id=tenant_id)
        if job is None:
            return {"status": "not_found", "job_id": job_id, "tenant_id": tenant_id}
        return {"status": "success", "job": job.public_dict(include_result=False)}

    await send_until_closed(websocket, payload)


@router.websocket("/ws/streams/{stream_id}")
async def ws_stream_events(websocket: WebSocket, stream_id: str) -> None:
    tenant_id = websocket_tenant(websocket)
    limit = min(max(1, int(websocket.query_params.get("limit", API_LIST_DEFAULT_LIMIT))), 200)

    def payload() -> dict[str, Any]:
        stream = get_stream(stream_id, tenant_id=tenant_id)
        if stream is None:
            return {"status": "not_found", "stream_id": stream_id, "tenant_id": tenant_id}
        events = [event.public_dict() for event in stream.events[-limit:]]
        return {"status": "success", "stream": stream.public_dict(include_events=False), "events": events}

    await send_until_closed(websocket, payload)
