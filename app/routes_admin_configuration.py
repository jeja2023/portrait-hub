from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.admin_configuration import (
    configuration_catalog_snapshot,
    configuration_state_snapshot,
    restore_configuration_state,
    update_configuration_state,
)
from app.network_access_policy import (
    network_access_policy_snapshot,
    restore_network_access_policy,
    save_network_access_policy,
)
from app.portrait_async import run_blocking_io
from app.portrait_audit import audit_event
from app.portrait_auth import permission_dependency
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_response import portrait_success
from app.security import require_api_token
from app.settings import (
    ALLOW_PRIVATE_STREAM_HOSTS,
    ALLOW_PRIVATE_WEBHOOK_HOSTS,
    STREAM_ALLOWED_CIDRS,
    STREAM_ALLOWED_HOSTS,
    WEBHOOK_ALLOWED_CIDRS,
    WEBHOOK_ALLOWED_HOSTS,
)

router = APIRouter(dependencies=[Depends(require_api_token)])


class ConfigurationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]*$")
    value: str | None = Field(default=None, max_length=65_536)


class ConfigurationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0)
    changes: list[ConfigurationChange] = Field(..., min_length=1, max_length=100)


class EndpointNetworkPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_private_hosts: bool
    allowed_hosts: list[str] = Field(default_factory=list, max_length=512)
    allowed_cidrs: list[str] = Field(default_factory=list, max_length=512)


class NetworkAccessPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0)
    stream: EndpointNetworkPolicyRequest
    webhook: EndpointNetworkPolicyRequest


def _network_policy_defaults() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "allow_private_hosts": ALLOW_PRIVATE_STREAM_HOSTS,
            "allowed_hosts": STREAM_ALLOWED_HOSTS,
            "allowed_cidrs": STREAM_ALLOWED_CIDRS,
        },
        {
            "allow_private_hosts": ALLOW_PRIVATE_WEBHOOK_HOSTS,
            "allowed_hosts": WEBHOOK_ALLOWED_HOSTS,
            "allowed_cidrs": WEBHOOK_ALLOWED_CIDRS,
        },
    )


def _network_policy_snapshot() -> dict[str, Any]:
    stream_default, webhook_default = _network_policy_defaults()
    return network_access_policy_snapshot(stream_default=stream_default, webhook_default=webhook_default)


@router.get(
    "/v1/admin/configuration",
    dependencies=[Depends(permission_dependency("admin:configuration"))],
)
async def admin_configuration_get(
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    payload = await run_blocking_io(configuration_catalog_snapshot)
    return portrait_success(ctx.request_id, payload)


@router.put(
    "/v1/admin/configuration",
    dependencies=[Depends(permission_dependency("admin:configuration"))],
)
async def admin_configuration_update(
    payload: ConfigurationUpdateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    before = await run_blocking_io(configuration_state_snapshot)
    state, changed_keys = await run_blocking_io(
        update_configuration_state,
        changes=[change.model_dump() for change in payload.changes],
        expected_revision=payload.expected_revision,
        updated_at=time.time(),
    )
    if changed_keys:
        try:
            await run_blocking_io(
                audit_event,
                "admin_configuration_update",
                request_id=ctx.request_id,
                tenant_id=ctx.tenant_id,
                changed_keys=changed_keys,
                changed_count=len(changed_keys),
                revision=state["revision"],
            )
        except Exception:
            await run_blocking_io(restore_configuration_state, before)
            raise
    result = await run_blocking_io(configuration_catalog_snapshot)
    result["changed_keys"] = changed_keys
    return portrait_success(ctx.request_id, result)


@router.get(
    "/v1/admin/network-access-policy",
    dependencies=[Depends(permission_dependency("admin:configuration"))],
)
async def admin_network_access_policy_get(
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    policy = await run_blocking_io(_network_policy_snapshot)
    return portrait_success(ctx.request_id, policy)


@router.put(
    "/v1/admin/network-access-policy",
    dependencies=[Depends(permission_dependency("admin:configuration"))],
)
async def admin_network_access_policy_update(
    payload: NetworkAccessPolicyUpdateRequest,
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    before = await run_blocking_io(_network_policy_snapshot)
    try:
        saved = await run_blocking_io(
            save_network_access_policy,
            current=before,
            stream=payload.stream.model_dump(),
            webhook=payload.webhook.model_dump(),
            updated_at=time.time(),
            expected_revision=payload.expected_revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    try:
        await run_blocking_io(
            audit_event,
            "admin_network_access_policy_update",
            request_id=ctx.request_id,
            tenant_id=ctx.tenant_id,
            revision=saved["revision"],
            stream_private=bool(saved["stream"]["allow_private_hosts"]),
            stream_host_rule_count=len(saved["stream"]["allowed_hosts"]),
            stream_cidr_rule_count=len(saved["stream"]["allowed_cidrs"]),
            webhook_private=bool(saved["webhook"]["allow_private_hosts"]),
            webhook_host_rule_count=len(saved["webhook"]["allowed_hosts"]),
            webhook_cidr_rule_count=len(saved["webhook"]["allowed_cidrs"]),
        )
    except Exception:
        await run_blocking_io(restore_network_access_policy, before)
        raise
    return portrait_success(ctx.request_id, saved)


__all__ = ["router"]
