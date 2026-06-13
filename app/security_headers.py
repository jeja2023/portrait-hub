from fastapi import Response

from app.settings import (
    CONTENT_SECURITY_POLICY,
    HSTS_ENABLED,
    HSTS_INCLUDE_SUBDOMAINS,
    HSTS_MAX_AGE_SECONDS,
    HSTS_PRELOAD,
    SECURITY_HEADERS_ENABLED,
)


def hsts_header_value() -> str:
    directives = [f"max-age={max(0, HSTS_MAX_AGE_SECONDS)}"]
    if HSTS_INCLUDE_SUBDOMAINS:
        directives.append("includeSubDomains")
    if HSTS_PRELOAD:
        directives.append("preload")
    return "; ".join(directives)


def apply_security_headers(response: Response) -> None:
    if not SECURITY_HEADERS_ENABLED:
        return
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    response.headers.setdefault("X-Download-Options", "noopen")
    if CONTENT_SECURITY_POLICY:
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if HSTS_ENABLED:
        response.headers.setdefault("Strict-Transport-Security", hsts_header_value())
