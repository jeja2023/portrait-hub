from dataclasses import dataclass

from fastapi import Request

from app.observability import TENANT_ID_CONTEXT, request_id_from_headers
from app.portrait_security import tenant_id_from_request


@dataclass(frozen=True)
class PortraitRequestContext:
    request_id: str
    tenant_id: str


def portrait_request_context(request: Request) -> PortraitRequestContext:
    tenant_id = tenant_id_from_request(request)
    TENANT_ID_CONTEXT.set(tenant_id)
    return PortraitRequestContext(
        request_id=request_id_from_headers(request),
        tenant_id=tenant_id,
    )
