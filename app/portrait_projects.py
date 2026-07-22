from __future__ import annotations

import hmac
import re
from typing import Any

from fastapi import HTTPException, Request, status

DEFAULT_PROJECT_ID = "default"
PROJECT_SCOPE_MARKER = "::project::"
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")


def validate_project_id(value: str | None, *, field_name: str = "project_id") -> str:
    project_id = str(value or "").strip()
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid {field_name}",
        )
    return project_id


def project_scope_id(tenant_id: str, project_id: str) -> str:
    """Return a legacy-compatible internal namespace for project-owned data."""
    normalized_project = validate_project_id(project_id)
    if normalized_project == DEFAULT_PROJECT_ID:
        return tenant_id
    return f"{tenant_id}{PROJECT_SCOPE_MARKER}{normalized_project}"


def split_project_scope_id(scope_id: str) -> tuple[str, str]:
    tenant_id, marker, project_id = str(scope_id).partition(PROJECT_SCOPE_MARKER)
    if not marker or not tenant_id or not project_id:
        return str(scope_id), DEFAULT_PROJECT_ID
    return tenant_id, project_id


def project_id_from_claims(claims: dict[str, Any]) -> str | None:
    project_claim = claims.get("project_id", claims.get("project"))
    if isinstance(project_claim, str) and project_claim.strip():
        return validate_project_id(project_claim.strip())
    projects_claim = claims.get("projects")
    if isinstance(projects_claim, list):
        projects = sorted(
            {validate_project_id(item.strip()) for item in projects_claim if isinstance(item, str) and item.strip()}
        )
        if len(projects) == 1:
            return projects[0]
    return None


def project_ids_from_claims(claims: dict[str, Any]) -> set[str] | None:
    project_ids: set[str] = set()
    project_claim = claims.get("project_id", claims.get("project"))
    if isinstance(project_claim, str) and project_claim.strip():
        project_ids.add(validate_project_id(project_claim.strip()))
    projects_claim = claims.get("projects")
    if isinstance(projects_claim, list):
        project_ids.update(
            validate_project_id(item.strip()) for item in projects_claim if isinstance(item, str) and item.strip()
        )
    return project_ids or None


def identity_claims_from_request(request: Request) -> dict[str, Any] | None:
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        from app.portrait_auth import verify_hs256_jwt

        try:
            return verify_hs256_jwt(authorization.removeprefix("Bearer ").strip())
        except HTTPException:
            return None
    if not authorization and not request.headers.get("x-api-key"):
        from app.oidc_auth import browser_session_claims

        return browser_session_claims(request)
    return None


def inferred_project_id_from_request(request: Request) -> str | None:
    api_key = request.headers.get("x-api-key")
    if api_key:
        from app.portrait_access import application_key_matches_any_tenant

        application = application_key_matches_any_tenant(api_key.strip())
        if application is not None:
            return validate_project_id(str(application.get("project_id") or DEFAULT_PROJECT_ID))

    claims = identity_claims_from_request(request)
    if claims is not None:
        return project_id_from_claims(claims)
    return None


def request_grants_project(request: Request, tenant_id: str, project_id: str) -> bool:
    target_project = validate_project_id(project_id)
    from app.security import global_api_token_matches

    if global_api_token_matches(request.headers.get("authorization"), request.headers.get("x-api-key")):
        return True

    api_key = request.headers.get("x-api-key")
    if api_key:
        from app.portrait_access import application_key_matches, application_key_matches_any_tenant

        application = application_key_matches(tenant_id, api_key.strip()) or application_key_matches_any_tenant(
            api_key.strip()
        )
        if application is None:
            return False
        bound_project = validate_project_id(str(application.get("project_id") or DEFAULT_PROJECT_ID))
        return hmac.compare_digest(bound_project, target_project)

    claims = identity_claims_from_request(request)
    claimed_projects = project_ids_from_claims(claims) if claims is not None else None
    return claimed_projects is None or target_project in claimed_projects


def project_id_from_request(request: Request, tenant_id: str) -> str:
    explicit_project = request.headers.get("x-project-id")
    inferred_project = inferred_project_id_from_request(request)
    project_id = validate_project_id(explicit_project or inferred_project or DEFAULT_PROJECT_ID)

    api_key = request.headers.get("x-api-key")
    if api_key:
        from app.portrait_access import application_key_matches, application_key_matches_any_tenant

        application = application_key_matches(tenant_id, api_key.strip()) or application_key_matches_any_tenant(
            api_key.strip()
        )
        if application is not None:
            bound_project = validate_project_id(str(application.get("project_id") or DEFAULT_PROJECT_ID))
            if explicit_project and not hmac.compare_digest(project_id, bound_project):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="API key does not grant access to the requested project",
                )
            project_id = bound_project
    else:
        claims = identity_claims_from_request(request)
        claimed_projects = project_ids_from_claims(claims) if claims is not None else None
        if claimed_projects is not None and project_id not in claimed_projects:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="credentials do not grant access to the requested project",
            )

    from app.portrait_access import project_is_active

    if not project_is_active(tenant_id, project_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="project is disabled or does not exist")
    state = getattr(request, "state", None)
    if state is not None:
        state.portrait_project_id = project_id
        state.portrait_scope_id = project_scope_id(tenant_id, project_id)
    return project_id


def publicize_project_scope(value: Any) -> Any:
    """Remove internal scope identifiers from recursively serialized payloads."""
    if isinstance(value, list):
        return [publicize_project_scope(item) for item in value]
    if not isinstance(value, dict):
        return value
    output = {key: publicize_project_scope(item) for key, item in value.items()}
    tenant_value = output.get("tenant_id")
    if isinstance(tenant_value, str):
        tenant_id, project_id = split_project_scope_id(tenant_value)
        output["tenant_id"] = tenant_id
        output.setdefault("project_id", project_id)
    return output


__all__ = [
    "DEFAULT_PROJECT_ID",
    "PROJECT_ID_PATTERN",
    "identity_claims_from_request",
    "inferred_project_id_from_request",
    "project_id_from_claims",
    "project_id_from_request",
    "project_ids_from_claims",
    "project_scope_id",
    "publicize_project_scope",
    "request_grants_project",
    "split_project_scope_id",
    "validate_project_id",
]
