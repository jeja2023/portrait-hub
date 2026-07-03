from functools import lru_cache
from pathlib import Path
from secrets import token_urlsafe

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse

from app.portrait_auth import permission_dependency
from app.security import require_api_token


router = APIRouter()
CONSOLE_ROOT = Path(__file__).resolve().parents[1] / "frontend" / "console"
CONSOLE_HTML_PATH = CONSOLE_ROOT / "console.html"
CONSOLE_CSS_PATH = CONSOLE_ROOT / "console.css"
CONSOLE_CONFIG_JS_PATH = CONSOLE_ROOT / "console.config.js"
CONSOLE_JS_PATH = CONSOLE_ROOT / "console.js"


def console_csp(nonce: str) -> str:
    return (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data: blob:; connect-src 'self'; "
        f"style-src 'self' 'nonce-{nonce}'; script-src 'self' 'nonce-{nonce}'"
    )


@lru_cache(maxsize=1)
def render_console_html() -> str:
    # 控制台页面是静态资源，没有逐请求的模板渲染（CSP nonce 只存在于响应头），
    # 因此只从磁盘读取一次并缓存（已变更为“影鉴”）。
    return CONSOLE_HTML_PATH.read_text(encoding="utf-8")


@router.get("/assets/console.css")
async def portrait_console_css() -> FileResponse:
    return FileResponse(CONSOLE_CSS_PATH, media_type="text/css")


@router.get("/assets/console.config.js")
async def portrait_console_config_js() -> FileResponse:
    return FileResponse(CONSOLE_CONFIG_JS_PATH, media_type="text/javascript")


@router.get("/assets/console.js")
async def portrait_console_js() -> FileResponse:
    return FileResponse(CONSOLE_JS_PATH, media_type="text/javascript")


def console_asset_path(asset_path: str) -> Path:
    root = CONSOLE_ROOT.resolve()
    target = (CONSOLE_ROOT / asset_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="console asset not found") from exc
    if not target.is_file() or target.name == "console.html":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="console asset not found")
    return target


@router.get("/assets/console/{asset_path:path}")
async def portrait_console_asset(asset_path: str) -> FileResponse:
    target = console_asset_path(asset_path)
    media_type = "text/javascript" if target.suffix == ".js" else "text/css" if target.suffix == ".css" else "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@router.get(
    "/console",
    response_class=HTMLResponse,
    dependencies=[Depends(require_api_token), Depends(permission_dependency("admin:status"))],
)
async def portrait_console() -> HTMLResponse:
    nonce = token_urlsafe(16)
    return HTMLResponse(content=render_console_html(), headers={"Content-Security-Policy": console_csp(nonce)})

# 2026-06-23: 优化服务总览顶部 8 统计卡片为同一行，紧凑化平台状态数据展示，完成平台状态英文标签/值汉化且支持长文本换行对齐
# 2026-06-23 (v0.5.39): 重构控制台导航为可展开分组（智能解析/人员库/视频分析/运维治理），将人员库拆分为 注册/搜人/管理 三子页、治理拆分为 阈值/数据 两子页，并统一收窄表单控件宽度、降低结果区与 JSON 区高度
