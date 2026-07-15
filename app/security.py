import hmac

from fastapi import Header, HTTPException, Request, status

from app.portrait_auth import jwt_tenant_matches, optional_header_value, unauthorized, verify_hs256_jwt
from app.settings import (
    API_TOKEN,
    API_TOKEN_ALLOW_TENANT_OVERRIDE,
    API_TOKEN_TENANT_ID,
    AUTH_REQUIRED,
    RBAC_ENABLED,
)


def access_application_identity(x_api_key: str | None, x_tenant_id: str | None) -> str | None:
    tenant_id = optional_header_value(x_tenant_id)
    api_key = optional_header_value(x_api_key)
    if not api_key:
        return None
    from app.portrait_access import application_key_matches, application_key_matches_any_tenant

    application = application_key_matches(tenant_id, api_key) if tenant_id else application_key_matches_any_tenant(api_key)
    if application is None:
        return None
    resolved_tenant_id = str(application.get("tenant_id") or tenant_id or "default")
    app_id = str(application.get("app_id") or application.get("id") or "application")
    return f"access-app:{resolved_tenant_id}:{app_id}"


def global_api_token_matches(authorization: str | None, x_api_key: str | None) -> bool:
    if not API_TOKEN:
        return False
    bearer = optional_header_value(authorization)
    api_key = optional_header_value(x_api_key)
    return (
        (bearer is not None
        and hmac.compare_digest(bearer, f"Bearer {API_TOKEN}"))
        or (api_key is not None
        and hmac.compare_digest(api_key, API_TOKEN))
    )


def global_api_token_tenant_allowed(x_tenant_id: str | None, *, require_binding: bool) -> bool:
    tenant_id = optional_header_value(x_tenant_id)
    if API_TOKEN_ALLOW_TENANT_OVERRIDE:
        return True
    if not API_TOKEN_TENANT_ID:
        return not require_binding and tenant_id is None
    return tenant_id is None or hmac.compare_digest(tenant_id, API_TOKEN_TENANT_ID)


def authenticated_request_identity(
    authorization: str | None,
    x_api_key: str | None = None,
    x_tenant_id: str | None = None,
) -> str | None:
    try:
        if global_api_token_matches(authorization, x_api_key):
            if not global_api_token_tenant_allowed(x_tenant_id, require_binding=False):
                return None
            tenant_id = optional_header_value(x_tenant_id) or API_TOKEN_TENANT_ID
            return f"api-token:{tenant_id}" if tenant_id else "api-token"
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
                        item.strip()
                        for item in tenants_claim
                        if isinstance(item, str) and item.strip()
                    }
                )
                if tenants:
                    return f"jwt:{','.join(tenants)}"
            return "jwt"
        application_identity = access_application_identity(x_api_key, x_tenant_id)
        if application_identity:
            return application_identity
    except HTTPException:
        return None
    return None


def request_is_authenticated(
    authorization: str | None,
    x_api_key: str | None = None,
    x_tenant_id: str | None = None,
) -> bool:
    """对探针等公开端点执行不会抛出异常的凭证有效性检查。"""
    return authenticated_request_identity(authorization, x_api_key, x_tenant_id) is not None


async def require_api_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> None:
    if global_api_token_matches(authorization, x_api_key):
        if request.url.path.startswith("/v1/") and not global_api_token_tenant_allowed(
            x_tenant_id,
            require_binding=True,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="全局 API 令牌未绑定该租户",
            )
        return

    if RBAC_ENABLED and authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        claims = verify_hs256_jwt(token)
        if not jwt_tenant_matches(claims, optional_header_value(x_tenant_id)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="JWT 与租户不匹配")
        return

    if access_application_identity(x_api_key, x_tenant_id):
        return

    if RBAC_ENABLED and not API_TOKEN:
        raise unauthorized("missing bearer JWT or API key")

    if AUTH_REQUIRED and not API_TOKEN:
        raise unauthorized("authentication 为必填项 but no credential backend is configured")

    if not API_TOKEN:
        return

    raise unauthorized("API 令牌无效或缺失")