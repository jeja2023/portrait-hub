from fastapi import Header, HTTPException, status

from app.portrait_auth import jwt_tenant_matches, optional_header_value, verify_hs256_jwt
from app.settings import API_TOKEN, AUTH_REQUIRED, RBAC_ENABLED


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


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
