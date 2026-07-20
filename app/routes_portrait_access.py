from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.observability import request_id_from_headers
from app.portrait_access import (
    access_state_payload,
    create_application,
    create_tenant,
    create_webhook,
    find_tenant,
    list_applications,
    list_tenants,
    list_webhooks,
    restore_access_state,
    rotate_application_secret,
    rotate_webhook_secret,
    update_application,
    update_tenant,
    update_webhook,
    webhook_sample_delivery,
)
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_call_logs import list_call_logs, summarize_call_logs
from app.portrait_errors import error_code_catalog
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.security import require_api_token

router = APIRouter(dependencies=[Depends(require_api_token)])



class AccessTenantCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(default=None, max_length=64)
    name: str = Field(..., min_length=1, max_length=256)
    status: str = Field(default="active", max_length=32)
    create_default_application: bool = True
    application_name: str | None = Field(default=None, max_length=256)
    owner: str = Field(default="platform", max_length=256)
    scopes: list[str] = Field(
        default_factory=lambda: [
            "infer",
            "compare",
            "gallery:read",
            "gallery:write",
            "jobs",
            "jobs:read",
            "streams",
            "streams:read",
            "models:read",
        ]
    )
    rate_limit_per_minute: int | None = Field(default=None, ge=0, le=1_000_000_000)
    rate_limit_burst: int | None = Field(default=None, ge=0, le=1_000_000_000)
    daily_quota: int | None = Field(default=None, ge=0, le=1_000_000_000)


class AccessTenantPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=256)
    status: str | None = Field(default=None, max_length=32)


class AccessApplicationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str | None = Field(default=None, max_length=96)
    name: str = Field(..., min_length=1, max_length=256)
    owner: str = Field(default="platform", max_length=256)
    status: str = Field(default="active", max_length=32)
    scopes: list[str] = Field(default_factory=lambda: ["infer", "compare", "gallery:read"])
    jwt_issuer: str | None = Field(default=None, max_length=256)
    jwt_audience: str | None = Field(default=None, max_length=256)
    rate_limit_per_minute: int | None = Field(default=None, ge=0, le=1_000_000_000)
    rate_limit_burst: int | None = Field(default=None, ge=0, le=1_000_000_000)
    daily_quota: int | None = Field(default=None, ge=0, le=1_000_000_000)


class AccessApplicationPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=256)
    owner: str | None = Field(default=None, max_length=256)
    status: str | None = Field(default=None, max_length=32)
    scopes: list[str] | None = None
    jwt_issuer: str | None = Field(default=None, max_length=256)
    jwt_audience: str | None = Field(default=None, max_length=256)
    rate_limit_per_minute: int | None = Field(default=None, ge=0, le=1_000_000_000)
    rate_limit_burst: int | None = Field(default=None, ge=0, le=1_000_000_000)
    daily_quota: int | None = Field(default=None, ge=0, le=1_000_000_000)


class WebhookCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    webhook_id: str | None = Field(default=None, max_length=96)
    name: str = Field(..., min_length=1, max_length=256)
    application_id: str = Field(..., min_length=1, max_length=96)
    url: str | None = Field(default=None, max_length=2048)
    status: str = Field(default="disabled", max_length=32)
    events: list[str] = Field(default_factory=lambda: ["job.completed"])
    retry_limit: int = Field(default=3, ge=0, le=10)
    timeout_seconds: int = Field(default=5, ge=1, le=60)


class WebhookPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=256)
    application_id: str | None = Field(default=None, max_length=96)
    url: str | None = Field(default=None, max_length=2048)
    status: str | None = Field(default=None, max_length=32)
    events: list[str] | None = None
    retry_limit: int | None = Field(default=None, ge=0, le=10)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)


def generated_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


async def audit_or_restore(event: str, snapshot: dict[str, list[dict[str, Any]]], **payload: Any) -> None:
    try:
        await run_blocking_io(audit_event, event, **payload)
    except Exception:
        await run_blocking_io(restore_access_state, snapshot)
        raise



@router.get("/v1/access/tenants", dependencies=[Depends(permission_dependency("tenants:read"))])
async def v1_access_tenants(request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenants = await run_blocking_io(list_tenants)
    return portrait_success(request_id, {"tenants": tenants, "count": len(tenants)})


@router.post("/v1/access/tenants", dependencies=[Depends(permission_dependency("tenants:write"))])
async def v1_access_create_tenant(payload: AccessTenantCreateRequest, request: Request) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    snapshot = await run_blocking_io(access_state_payload)
    application = None
    secret = None
    try:
        tenant = await run_blocking_io(
            create_tenant,
            payload.name,
            tenant_id=payload.tenant_id,
            status_value=payload.status,
        )
        if payload.create_default_application:
            application, secret = await run_blocking_io(
                create_application,
                tenant["tenant_id"],
                app_id=generated_id("app"),
                name=payload.application_name or f"{payload.name} 接入应用",
                owner=payload.owner,
                status_value="active",
                scopes=payload.scopes,
                rate_limit_per_minute=payload.rate_limit_per_minute,
                rate_limit_burst=payload.rate_limit_burst,
                daily_quota=payload.daily_quota,
            )
        current_tenant = await run_blocking_io(find_tenant, tenant["tenant_id"])
        if current_tenant is not None:
            tenant = current_tenant
    except Exception:
        await run_blocking_io(restore_access_state, snapshot)
        raise
    await audit_or_restore(
        "access_tenant_created",
        snapshot,
        request_id=request_id,
        tenant_id=tenant["tenant_id"],
        tenant_name=tenant.get("name"),
        created_default_application=application is not None,
        app_id=application.get("app_id") if application else None,
    )
    data: dict[str, Any] = {"tenant": tenant}
    if application is not None:
        data["application"] = application
        data["one_time_secret"] = secret
    return portrait_success(request_id, data)


@router.patch("/v1/access/tenants/{tenant_id}", dependencies=[Depends(permission_dependency("tenants:write"))])
async def v1_access_patch_tenant(
    tenant_id: str,
    payload: AccessTenantPatchRequest,
    request: Request,
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    snapshot = await run_blocking_io(access_state_payload)
    tenant = await run_blocking_io(update_tenant, tenant_id, payload.model_dump(exclude_unset=True))
    await audit_or_restore(
        "access_tenant_updated",
        snapshot,
        request_id=request_id,
        tenant_id=tenant_id,
        changed_fields=sorted(payload.model_fields_set),
    )
    return portrait_success(request_id, {"tenant": tenant})


@router.get("/v1/access/applications", dependencies=[Depends(permission_dependency("access:read"))])
async def v1_access_applications(ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    applications = await run_blocking_io(list_applications, ctx.tenant_id)
    return portrait_success(request_id, {"tenant_id": ctx.tenant_id, "applications": applications, "count": len(applications)})


@router.post("/v1/access/applications", dependencies=[Depends(permission_dependency("access:write"))])
async def v1_access_create_application(
    payload: AccessApplicationCreateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    snapshot = await run_blocking_io(access_state_payload)
    app, secret = await run_blocking_io(
        create_application,
        tenant_id,
        app_id=payload.app_id or generated_id("app"),
        name=payload.name,
        owner=payload.owner,
        status_value=payload.status,
        scopes=payload.scopes,
        jwt_issuer=payload.jwt_issuer,
        jwt_audience=payload.jwt_audience,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        rate_limit_burst=payload.rate_limit_burst,
        daily_quota=payload.daily_quota,
    )
    await audit_or_restore(
        "access_application_created",
        snapshot,
        request_id=request_id,
        tenant_id=tenant_id,
        app_id=app["app_id"],
        scope_count=len(app.get("scopes", [])),
        status=app.get("status"),
        rate_limit_per_minute=app.get("rate_limit_per_minute"),
        daily_quota=app.get("daily_quota"),
    )
    return portrait_success(request_id, {"application": app, "one_time_secret": secret})


@router.patch("/v1/access/applications/{app_id}", dependencies=[Depends(permission_dependency("access:write"))])
async def v1_access_patch_application(
    app_id: str,
    payload: AccessApplicationPatchRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    updates = payload.model_dump(exclude_unset=True)
    snapshot = await run_blocking_io(access_state_payload)
    app = await run_blocking_io(update_application, tenant_id, app_id, updates)
    await audit_or_restore(
        "access_application_updated",
        snapshot,
        request_id=request_id,
        tenant_id=tenant_id,
        app_id=app["app_id"],
        updated_fields=sorted(updates),
        status=app.get("status"),
    )
    return portrait_success(request_id, {"application": app})


@router.post("/v1/access/applications/{app_id}/rotate", dependencies=[Depends(permission_dependency("access:write"))])
async def v1_access_rotate_application(
    app_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    snapshot = await run_blocking_io(access_state_payload)
    app, secret = await run_blocking_io(rotate_application_secret, tenant_id, app_id)
    await audit_or_restore(
        "access_application_secret_rotated",
        snapshot,
        request_id=request_id,
        tenant_id=tenant_id,
        app_id=app["app_id"],
        status=app.get("status"),
    )
    return portrait_success(request_id, {"application": app, "one_time_secret": secret})


@router.get("/v1/access/error-codes", dependencies=[Depends(permission_dependency("access:read"))])
async def v1_access_error_codes(ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    error_codes = await run_blocking_io(error_code_catalog)
    return portrait_success(
        ctx.request_id,
        {"tenant_id": ctx.tenant_id, "error_codes": error_codes, "count": len(error_codes)},
    )

@router.get("/v1/access/call-logs/summary", dependencies=[Depends(permission_dependency("access:read"))])
async def v1_access_call_logs_summary(
    request_id: str | None = Query(default=None, max_length=128),
    endpoint: str | None = Query(default=None, max_length=256),
    status_text: str | None = Query(default=None, alias="status", max_length=32),
    application_id: str | None = Query(default=None, max_length=96),
    error_code: str | None = Query(default=None, max_length=96),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    summary = await run_blocking_io(
        summarize_call_logs,
        ctx.tenant_id,
        request_id=request_id,
        endpoint=endpoint,
        status_text=status_text,
        application_id=application_id,
        error_code=error_code,
        created_since=created_since,
        created_until=created_until,
    )
    return portrait_success(ctx.request_id, {"tenant_id": ctx.tenant_id, **summary})

@router.get("/v1/access/call-logs", dependencies=[Depends(permission_dependency("access:read"))])
async def v1_access_call_logs(
    request_id: str | None = Query(default=None, max_length=128),
    endpoint: str | None = Query(default=None, max_length=256),
    status_text: str | None = Query(default=None, alias="status", max_length=32),
    application_id: str | None = Query(default=None, max_length=96),
    error_code: str | None = Query(default=None, max_length=96),
    created_since: float | None = Query(default=None, ge=0),
    created_until: float | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    logs = await run_blocking_io(
        list_call_logs,
        ctx.tenant_id,
        request_id=request_id,
        endpoint=endpoint,
        status_text=status_text,
        application_id=application_id,
        error_code=error_code,
        created_since=created_since,
        created_until=created_until,
        limit=limit,
    )
    return portrait_success(ctx.request_id, {"tenant_id": ctx.tenant_id, "logs": logs, "count": len(logs)})


@router.get("/v1/access/webhooks", dependencies=[Depends(permission_dependency("access:read"))])
async def v1_access_webhooks(ctx: PortraitRequestContext = Depends(portrait_request_context)) -> dict[str, Any]:
    request_id = ctx.request_id
    webhooks = await run_blocking_io(list_webhooks, ctx.tenant_id)
    return portrait_success(request_id, {"tenant_id": ctx.tenant_id, "webhooks": webhooks, "count": len(webhooks)})


@router.post("/v1/access/webhooks", dependencies=[Depends(permission_dependency("access:write"))])
async def v1_access_create_webhook(
    payload: WebhookCreateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    snapshot = await run_blocking_io(access_state_payload)
    webhook, secret = await run_blocking_io(
        create_webhook,
        tenant_id,
        webhook_id=payload.webhook_id or generated_id("wh"),
        name=payload.name,
        application_id=payload.application_id,
        url=payload.url,
        status_value=payload.status,
        events=payload.events,
        retry_limit=payload.retry_limit,
        timeout_seconds=payload.timeout_seconds,
    )
    await audit_or_restore(
        "access_webhook_created",
        snapshot,
        request_id=request_id,
        tenant_id=tenant_id,
        webhook_id=webhook["webhook_id"],
        application_id=webhook.get("application_id"),
        event_count=len(webhook.get("events", [])),
        status=webhook.get("status"),
    )
    return portrait_success(request_id, {"webhook": webhook, "one_time_secret": secret})


@router.patch("/v1/access/webhooks/{webhook_id}", dependencies=[Depends(permission_dependency("access:write"))])
async def v1_access_patch_webhook(
    webhook_id: str,
    payload: WebhookPatchRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    updates = payload.model_dump(exclude_unset=True)
    snapshot = await run_blocking_io(access_state_payload)
    webhook = await run_blocking_io(update_webhook, tenant_id, webhook_id, updates)
    await audit_or_restore(
        "access_webhook_updated",
        snapshot,
        request_id=request_id,
        tenant_id=tenant_id,
        webhook_id=webhook["webhook_id"],
        updated_fields=sorted(updates),
        status=webhook.get("status"),
    )
    return portrait_success(request_id, {"webhook": webhook})


@router.post("/v1/access/webhooks/{webhook_id}/rotate", dependencies=[Depends(permission_dependency("access:write"))])
async def v1_access_rotate_webhook(
    webhook_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    tenant_id = ctx.tenant_id
    snapshot = await run_blocking_io(access_state_payload)
    webhook, secret = await run_blocking_io(rotate_webhook_secret, tenant_id, webhook_id)
    await audit_or_restore(
        "access_webhook_secret_rotated",
        snapshot,
        request_id=request_id,
        tenant_id=tenant_id,
        webhook_id=webhook["webhook_id"],
        status=webhook.get("status"),
    )
    return portrait_success(request_id, {"webhook": webhook, "one_time_secret": secret})


@router.post("/v1/access/webhooks/{webhook_id}/sample", dependencies=[Depends(permission_dependency("access:read"))])
async def v1_access_webhook_sample(
    webhook_id: str,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = ctx.request_id
    sample = await run_blocking_io(webhook_sample_delivery, ctx.tenant_id, webhook_id)
    return portrait_success(request_id, {"sample_delivery": sample})
