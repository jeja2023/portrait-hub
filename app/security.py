from fastapi import Header, HTTPException, status

from app.portrait_auth import jwt_tenant_matches, optional_header_value, unauthorized, verify_hs256_jwt
from app.settings import API_TOKEN, AUTH_REQUIRED, RBAC_ENABLED


def authenticated_request_identity(
    authorization: str | None,
    x_api_key: str | None = None,
    x_tenant_id: str | None = None,
) -> str | None:
    try:
        if RBAC_ENABLED and authorization and authorization.startswith("Bearer "):
            claims = verify_hs256_jwt(authorization.removeprefix("Bearer ").strip())
            if not jwt_tenant_matches(claims, optional_header_value(x_tenant_id)):
                return None
            subject = claims.get("sub")
            if isinstance(subject, str) and subject.strip():
                return f"jwt:{subject.strip()}"
            tenant_claim = claims.get("tenant_id", claims.get("tenant"))
            if isinstance(tenant_claim, str) and tenant_claim.strip():
                return f"jwt:{tenant_claim.strip()}"
            tenants_claim = claims.get("tenants")
            if isinstance(tenants_claim, list):
                tenants = sorted(
                    {
                        str(item).strip()
                        for item in tenants_claim
                        if isinstance(item, str) and str(item).strip()
                    }
                )
                if tenants:
                    return f"jwt:{','.join(tenants)}"
            return "jwt"
        if API_TOKEN:
            bearer = f"Bearer {API_TOKEN}"
            if authorization == bearer or x_api_key == API_TOKEN:
                return "api-token"
    except HTTPException:
        return None
    return None


def request_is_authenticated(
    authorization: str | None,
    x_api_key: str | None = None,
    x_tenant_id: str | None = None,
) -> bool:
    """Best-effort, non-raising check of whether a request carries valid credentials.

    Used by probes (e.g. /ready) that stay publicly reachable but should only
    disclose internal dependency detail to authenticated callers.
    """
    return authenticated_request_identity(authorization, x_api_key, x_tenant_id) is not None


async def require_api_token(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> None:
    if RBAC_ENABLED and authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        claims = verify_hs256_jwt(token)
        if not jwt_tenant_matches(claims, optional_header_value(x_tenant_id)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="JWT is not valid for tenant")
        return
    if RBAC_ENABLED and not API_TOKEN:
        raise unauthorized("missing bearer JWT")

    if AUTH_REQUIRED and not API_TOKEN:
        raise unauthorized("authentication is required but no credential backend is configured")

    if not API_TOKEN:
        return

    bearer = f"Bearer {API_TOKEN}"
    if authorization == bearer or x_api_key == API_TOKEN:
        return

    raise unauthorized("invalid or missing API token")
