from dataclasses import dataclass

from fastapi import Request

from app.observability import request_id_from_headers
from app.portrait_security import tenant_id_from_request


@dataclass(frozen=True)
class PortraitRequestContext:
    request_id: str
    tenant_id: str


def portrait_request_context(request: Request) -> PortraitRequestContext:
    return PortraitRequestContext(
        request_id=request_id_from_headers(request),
        tenant_id=tenant_id_from_request(request),
    )
