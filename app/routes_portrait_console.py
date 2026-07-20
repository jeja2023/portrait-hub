from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.observability import logger
from app.oidc_auth import oidc_identity_metadata
from app.portrait_access import (
    access_state_payload,
    create_member,
    delete_member,
    list_members,
    restore_access_state,
    update_member,
)
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import ROLE_PERMISSIONS, permission_dependency, require_permission
from app.portrait_console_access import console_principal, issue_console_ws_ticket
from app.portrait_jobs import get_video_job
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.portrait_security import validate_job_id, validate_stream_id
from app.portrait_streams import get_stream
from app.security import require_api_token

router = APIRouter()
FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend"
CONSOLE_NEXT_ROOT = FRONTEND_ROOT / "console-next" / "dist"
CONSOLE_NEXT_HTML_PATH = CONSOLE_NEXT_ROOT / "index.html"
CONSOLE_HTML_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}
CONSOLE_NEXT_ASSET_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def next_console_csp() -> str:
    return (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data: blob:; connect-src 'self'; "
        "font-src 'self' data:; style-src 'self'; script-src 'self'; "
        "manifest-src 'self'; worker-src 'self' blob:"
    )


def render_next_console_html() -> str:
    if not CONSOLE_NEXT_HTML_PATH.is_file():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="console-next static bundle has not been built",
        )
    return CONSOLE_NEXT_HTML_PATH.read_text(encoding="utf-8")


def role_catalog() -> list[dict[str, object]]:
    return [
        {
            "role": role,
            "permissions": sorted(permissions),
        }
        for role, permissions in ROLE_PERMISSIONS.items()
    ]


class ConsoleWebSocketTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: Literal["job", "stream"]
    resource_id: str = Field(min_length=1, max_length=128)


class IdentityMemberCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=64)
    phone: str = Field(min_length=7, max_length=32)
    display_name: str = Field(min_length=1, max_length=256)
    subject: str | None = Field(default=None, max_length=256)
    roles: list[str] = Field(min_length=1)
    status: str = Field(default="active", max_length=32)


class IdentityMemberPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str | None = Field(default=None, min_length=7, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    subject: str | None = Field(default=None, max_length=256)
    roles: list[str] | None = Field(default=None, min_length=1)
    status: str | None = Field(default=None, max_length=32)


async def audit_identity_or_restore(
    event: str,
    snapshot: dict[str, list[dict[str, Any]]],
    **payload: Any,
) -> None:
    try:
        await run_blocking_io(audit_event, event, **payload)
    except Exception:
        await run_blocking_io(restore_access_state, snapshot)
        raise


@router.get("/v1/console/me", dependencies=[Depends(require_api_token)])
async def portrait_console_me(
    request: Request,
    response: Response,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    principal = console_principal(
        tenant_id=ctx.tenant_id,
        authorization=authorization,
        x_api_key=x_api_key,
        request=request,
    )
    response.headers["Cache-Control"] = "no-store"
    return portrait_success(
        ctx.request_id,
        {
            "tenant_id": ctx.tenant_id,
            "identity": oidc_identity_metadata(),
            **principal,
        },
    )


@router.get(
    "/v1/admin/identity",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("admin:identity"))],
)
async def portrait_identity_admin(
    response: Response,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return portrait_success(
        ctx.request_id,
        {
            "identity": oidc_identity_metadata(include_admin_url=True),
            "roles": role_catalog(),
        },
    )


@router.get(
    "/v1/admin/members",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("admin:identity"))],
)
async def portrait_identity_members(
    response: Response,
    tenant_id: str | None = None,
    all_tenants: bool = False,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, object]:
    members = await run_blocking_io(list_members, None if all_tenants else tenant_id or ctx.tenant_id)
    response.headers["Cache-Control"] = "no-store"
    return portrait_success(ctx.request_id, {"members": members, "count": len(members)})


@router.post(
    "/v1/admin/members",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("admin:identity"))],
)
async def portrait_identity_create_member(
    payload: IdentityMemberCreateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, object]:
    snapshot = await run_blocking_io(access_state_payload)
    member = await run_blocking_io(
        create_member,
        payload.tenant_id,
        phone=payload.phone,
        display_name=payload.display_name,
        subject=payload.subject,
        roles=payload.roles,
        status_value=payload.status,
    )
    await audit_identity_or_restore(
        "identity_member_created",
        snapshot,
        request_id=ctx.request_id,
        tenant_id=payload.tenant_id,
        member_id=member["member_id"],
        roles=member["roles"],
    )
    return portrait_success(ctx.request_id, {"member": member})


@router.patch(
    "/v1/admin/members/{member_id}",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("admin:identity"))],
)
async def portrait_identity_update_member(
    member_id: str,
    payload: IdentityMemberPatchRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, object]:
    snapshot = await run_blocking_io(access_state_payload)
    member = await run_blocking_io(update_member, member_id, payload.model_dump(exclude_unset=True))
    await audit_identity_or_restore(
        "identity_member_updated",
        snapshot,
        request_id=ctx.request_id,
        tenant_id=member["tenant_id"],
        member_id=member_id,
        changed_fields=sorted(payload.model_fields_set),
    )
    return portrait_success(ctx.request_id, {"member": member})


@router.delete(
    "/v1/admin/members/{member_id}",
    dependencies=[Depends(require_api_token), Depends(permission_dependency("admin:identity"))],
)
async def portrait_identity_delete_member(
    member_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, object]:
    snapshot = await run_blocking_io(access_state_payload)
    member = await run_blocking_io(delete_member, member_id)
    await audit_identity_or_restore(
        "identity_member_deleted",
        snapshot,
        request_id=ctx.request_id,
        tenant_id=member["tenant_id"],
        member_id=member_id,
    )
    return portrait_success(ctx.request_id, {"member": member})


@router.post("/v1/console/ws-ticket", dependencies=[Depends(require_api_token)])
async def portrait_console_ws_ticket(
    payload: ConsoleWebSocketTicketRequest,
    request: Request,
    response: Response,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    permission = "jobs:read" if payload.resource_type == "job" else "streams:read"
    await require_permission(permission, authorization, ctx.tenant_id, x_api_key, request)
    if payload.resource_type == "job":
        resource_id = validate_job_id(payload.resource_id)
        if get_video_job(resource_id, tenant_id=ctx.tenant_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实时资源不存在")
        websocket_path = f"/ws/jobs/{resource_id}"
    else:
        resource_id = validate_stream_id(payload.resource_id)
        if get_stream(resource_id, tenant_id=ctx.tenant_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实时资源不存在")
        websocket_path = f"/ws/streams/{resource_id}"

    raw_ticket, ticket = issue_console_ws_ticket(
        tenant_id=ctx.tenant_id,
        resource_type=payload.resource_type,
        resource_id=resource_id,
        permission=permission,
    )
    logger.info(
        "console websocket ticket issued fingerprint=%s tenant=%s resource_type=%s",
        ticket.fingerprint,
        ctx.tenant_id,
        payload.resource_type,
    )
    response.headers["Cache-Control"] = "no-store"
    return portrait_success(
        ctx.request_id,
        {
            "ticket": raw_ticket,
            "expires_at": ticket.expires_at,
            "websocket_path": websocket_path,
        },
    )


def safe_console_asset(root: Path, asset_path: str, *, hide_source_maps: bool = False) -> Path:
    resolved_root = root.resolve()
    target = (root / asset_path).resolve()
    try:
        relative = target.relative_to(resolved_root)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="控制台资源不存在") from exc
    if (
        not target.is_file()
        or target.name == "index.html"
        or any(part.startswith(".") for part in relative.parts)
        or (hide_source_maps and target.suffix == ".map")
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="控制台资源不存在")
    return target


@router.get("/assets/console-next/{asset_path:path}")
async def portrait_console_next_asset(asset_path: str) -> FileResponse:
    target = safe_console_asset(CONSOLE_NEXT_ROOT, asset_path, hide_source_maps=True)
    return FileResponse(target, headers=CONSOLE_NEXT_ASSET_HEADERS)


def next_console_response() -> HTMLResponse:
    return HTMLResponse(
        content=render_next_console_html(),
        headers={
            "Content-Security-Policy": next_console_csp(),
            **CONSOLE_HTML_HEADERS,
        },
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def portrait_home() -> HTMLResponse:
    return next_console_response()


@router.get("/console", response_class=HTMLResponse)
async def portrait_console() -> HTMLResponse:
    return next_console_response()


@router.get("/console/next", response_class=HTMLResponse)
async def portrait_console_next() -> HTMLResponse:
    return next_console_response()
