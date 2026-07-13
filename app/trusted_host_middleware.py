from __future__ import annotations

from collections.abc import Callable, Sequence

from starlette.datastructures import Headers, URL
from starlette.middleware.trustedhost import ENFORCE_DOMAIN_WILDCARD
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send


class HotReloadTrustedHostMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        allowed_hosts_getter: Callable[[], Sequence[str] | None],
        www_redirect: bool = True,
    ) -> None:
        self.app = app
        self.allowed_hosts_getter = allowed_hosts_getter
        self.www_redirect = www_redirect

    def _allowed_hosts(self) -> list[str]:
        allowed_hosts = self.allowed_hosts_getter()
        if allowed_hosts is None:
            allowed_hosts = ["*"]
        allowed = list(allowed_hosts)
        for pattern in allowed:
            assert "*" not in pattern[1:], ENFORCE_DOMAIN_WILDCARD
            if pattern.startswith("*") and pattern != "*":
                assert pattern.startswith("*."), ENFORCE_DOMAIN_WILDCARD
        return allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        allowed_hosts = self._allowed_hosts()
        if "*" in allowed_hosts or scope["type"] not in ("http", "websocket"):  # pragma: no cover
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        host = headers.get("host", "").split(":")[0]
        is_valid_host = False
        found_www_redirect = False
        for pattern in allowed_hosts:
            if host == pattern or (pattern.startswith("*") and host.endswith(pattern[1:])):
                is_valid_host = True
                break
            if "www." + host == pattern:
                found_www_redirect = True

        if is_valid_host:
            await self.app(scope, receive, send)
            return

        response: Response
        if found_www_redirect and self.www_redirect:
            url = URL(scope=scope)
            redirect_url = url.replace(netloc="www." + url.netloc)
            response = RedirectResponse(url=str(redirect_url))
        else:
            response = PlainTextResponse("Host 头无效", status_code=400)
        await response(scope, receive, send)
