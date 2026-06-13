from pathlib import Path
from secrets import token_urlsafe

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, HTMLResponse

from app.portrait_auth import permission_dependency
from app.security import require_api_token


router = APIRouter()
CONSOLE_ROOT = Path(__file__).resolve().parent
CONSOLE_HTML_PATH = CONSOLE_ROOT / "console.html"
CONSOLE_CSS_PATH = CONSOLE_ROOT / "console.css"
CONSOLE_JS_PATH = CONSOLE_ROOT / "console.js"


def console_csp(nonce: str) -> str:
    return (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; connect-src 'self'; "
        f"style-src 'self' 'nonce-{nonce}'; script-src 'self' 'nonce-{nonce}'"
    )


def render_console_html() -> str:
    return CONSOLE_HTML_PATH.read_text(encoding="utf-8")


@router.get("/assets/console.css")
async def portrait_console_css() -> FileResponse:
    return FileResponse(CONSOLE_CSS_PATH, media_type="text/css")


@router.get("/assets/console.js")
async def portrait_console_js() -> FileResponse:
    return FileResponse(CONSOLE_JS_PATH, media_type="text/javascript")


@router.get(
    "/console",
    response_class=HTMLResponse,
    dependencies=[Depends(require_api_token), Depends(permission_dependency("admin:status"))],
)
async def portrait_console() -> HTMLResponse:
    nonce = token_urlsafe(16)
    return HTMLResponse(content=render_console_html(), headers={"Content-Security-Policy": console_csp(nonce)})
