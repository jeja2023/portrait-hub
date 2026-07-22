from dataclasses import dataclass

from fastapi import Request

from app.observability import TENANT_ID_CONTEXT, request_id_from_headers
from app.portrait_projects import project_id_from_request, project_scope_id
from app.portrait_security import tenant_id_from_request


@dataclass(frozen=True)
class PortraitRequestContext:
    request_id: str
    tenant_id: str
    project_id: str
    scope_id: str


def portrait_request_context(request: Request) -> PortraitRequestContext:
    tenant_id = tenant_id_from_request(request)
    project_id = project_id_from_request(request, tenant_id)
    state = getattr(request, "state", None)
    if state is not None:
        state.portrait_tenant_id = tenant_id
    TENANT_ID_CONTEXT.set(tenant_id)
    return PortraitRequestContext(
        request_id=request_id_from_headers(request),
        tenant_id=tenant_id,
        project_id=project_id,
        scope_id=project_scope_id(tenant_id, project_id),
    )
