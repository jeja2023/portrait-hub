import asyncio
import hashlib
import logging
import signal
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config_hot_reload import ENV_PATH, reload_runtime_config
from app.core import (
    WARMUP_MODELS,
    cache_key,
    get_model_path,
    get_or_load_model,
    log_json,
    logger,
    now,
    observe,
    request_id_from_headers,
    reset_log_context,
    set_log_context,
    split_cache_key,
    traceparent_from_headers,
)
from app.metrics import observe_request_status
from app.portrait_access import flush_access_call_stats
from app.portrait_async import run_blocking_io
from app.portrait_bootstrap import ensure_portrait_runtime_state_loaded
from app.portrait_call_logs import application_id_from_api_key, record_call_log
from app.portrait_errors import PortraitError
from app.portrait_response import exception_log_summary
from app.portrait_security import inferred_tenant_id_from_request
from app.portrait_video_job_worker import start_in_process_worker
from app.production_gates import validate_production_externalization
from app.rate_limit import check_rate_limit
from app.routes import router
from app.security_headers import apply_security_headers
from app.settings import (
    ACCESS_STATS_FLUSH_INTERVAL_SECONDS,
    APP_VERSION,
    CONFIG_HOT_RELOAD_ENABLED,
    ENABLE_API_DOCS,
    MAX_REQUEST_BODY_BYTES,
    MODEL_CAPABILITIES_PATH,
    MODEL_CONFIG_PATH,
    OPENTELEMETRY_ENABLED,
    OTEL_SERVICE_NAME,
    TRUSTED_HOSTS,
    WARMUP_FAIL_FAST,
)
from app.trusted_host_middleware import HotReloadTrustedHostMiddleware


def request_body_too_large_detail() -> str:
    return f"请求体过大：最大 {MAX_REQUEST_BODY_BYTES} 字节"


async def warmup_models() -> None:
    if not WARMUP_MODELS:
        return

    succeeded = 0
    failed: list[str] = []
    for item in WARMUP_MODELS:
        try:
            project_name, model_name = split_cache_key(item)
            model_path = get_model_path(project_name, model_name)
            key = cache_key(project_name, model_name)
            await get_or_load_model(key, model_path)
        except Exception as exc:
            # 预热默认尽力而为：单个模型失败只记录并继续，避免一个坏模型拖垮整个启动。
            # WARMUP_FAIL_FAST=true 时改为严格模式，任一失败即让启动失败。
            failed.append(item)
            logger.warning(
                "startup warmup failed for a model: error=%s",
                exception_log_summary(exc),
            )
            if WARMUP_FAIL_FAST:
                raise
            continue
        succeeded += 1
        logger.info("startup warmup completed: %s", key)
    logger.info(
        "startup warmup summary: succeeded=%d failed=%d total=%d",
        succeeded,
        len(failed),
        len(WARMUP_MODELS),
    )


async def config_hot_reload_loop() -> None:
    mtimes: dict[str, float] = {}
    paths = {
        "env": ENV_PATH,
        "models": MODEL_CONFIG_PATH,
        "capabilities": MODEL_CAPABILITIES_PATH,
    }
    while True:
        try:
            for name, path in paths.items():
                if not path.exists():
                    continue
                key = str(path)
                mtime = path.stat().st_mtime
                previous = mtimes.get(key)
                mtimes[key] = mtime
                if previous is not None and mtime > previous:
                    reload_runtime_config(source=f"watch:{name}", include_env=True)
                    logger.info(
                        "configuration hot reload completed: path_hash=%s",
                        hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
                    )
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("configuration hot reload failed: %s", exc)
            await asyncio.sleep(2.0)


def install_config_reload_signal_handler() -> bool:
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is None:
        return False

    def reload_config_from_signal(_signum: int, _frame: Any) -> None:
        try:
            reload_runtime_config(source="sighup", include_env=True)
            logger.info("configuration hot reload completed from SIGHUP")
        except Exception as exc:
            logger.warning("configuration hot reload from SIGHUP failed: %s", exc)

    try:
        signal.signal(sighup, reload_config_from_signal)
    except (OSError, ValueError):
        return False
    return True


async def access_stats_flush_loop() -> None:
    interval = max(0.1, float(ACCESS_STATS_FLUSH_INTERVAL_SECONDS))
    while True:
        await asyncio.sleep(interval)
        try:
            await run_blocking_io(flush_access_call_stats)
        except Exception as exc:
            logger.warning("接入调用统计刷盘失败: %s", exception_log_summary(exc))


async def cancel_background_task(task: asyncio.Task[Any] | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if CONFIG_HOT_RELOAD_ENABLED:
        reload_runtime_config(source="startup", include_env=True)
    validate_production_externalization()
    await ensure_portrait_runtime_state_loaded()
    await warmup_models()
    if CONFIG_HOT_RELOAD_ENABLED:
        install_config_reload_signal_handler()
    reload_task = (
        asyncio.create_task(config_hot_reload_loop())
        if CONFIG_HOT_RELOAD_ENABLED
        else None
    )
    access_stats_task = (
        asyncio.create_task(access_stats_flush_loop())
        if ACCESS_STATS_FLUSH_INTERVAL_SECONDS > 0
        else None
    )
    video_job_worker_task = start_in_process_worker()
    try:
        yield
    finally:
        await cancel_background_task(video_job_worker_task)
        await cancel_background_task(access_stats_task)
        await cancel_background_task(reload_task)
        try:
            await run_blocking_io(flush_access_call_stats)
        except Exception as exc:
            logger.warning(
                "关闭服务时刷盘接入调用统计失败: %s", exception_log_summary(exc)
            )


def limit_request_body(request: Request) -> Request:
    if MAX_REQUEST_BODY_BYTES <= 0:
        return request

    raw_content_length = request.headers.get("content-length")
    if raw_content_length:
        try:
            content_length = int(raw_content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="content-length 请求头无效"
            ) from exc
        if content_length > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(status_code=413, detail=request_body_too_large_detail())

    received = 0
    receive = request.receive

    async def limited_receive() -> dict[str, Any]:
        nonlocal received
        message = dict(await receive())
        if message.get("type") == "http.request":
            received += len(message.get("body", b""))
            if received > MAX_REQUEST_BODY_BYTES:
                raise HTTPException(
                    status_code=413, detail=request_body_too_large_detail()
                )
        return message

    return Request(request.scope, limited_receive)


def validation_error_payload(
    exc: RequestValidationError, request_id: str | None = None
) -> dict[str, Any]:
    errors = []
    for error in exc.errors():
        errors.append(
            {
                "type": error.get("type", "validation_error"),
                "loc": validation_error_loc(error),
                "msg": error.get("msg", "validation error"),
            }
        )
    if request_id:
        return api_error_payload(
            request_id,
            "validation_error",
            "请求参数验证失败",
            details={"issues": errors},
        )
    return {"detail": errors}


def validation_error_loc(error: dict[str, Any]) -> list[Any]:
    raw_loc = error.get("loc", [])
    loc = list(raw_loc) if isinstance(raw_loc, (list, tuple)) else []
    if error.get("type") == "extra_forbidden" and loc:
        loc[-1] = "extra_field"
    return loc


def api_error_payload(
    request_id: str,
    code: str,
    message: str,
    *,
    details: Any | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details not in (None, {}, []):
        error["details"] = details
    return {"status": "error", "request_id": request_id, "error": error}


def error_payload_from_http_detail(
    detail: Any, status_code: int, request_id: str
) -> dict[str, Any]:
    code = error_code_from_http_detail(detail, status_code) or f"http_{status_code}"
    details: Any | None = None
    if isinstance(detail, dict):
        raw_message = detail.get("message")
        message = str(raw_message) if raw_message else "请求失败"
        details = {
            key: value
            for key, value in detail.items()
            if key not in {"code", "message", "request_id"}
        }
    elif isinstance(detail, str):
        message = detail
    else:
        message = "请求失败"
        details = detail
    return api_error_payload(request_id, code, message, details=details)


def internal_error_payload(
    request_id: str, *, v1_contract: bool = False
) -> dict[str, Any]:
    if v1_contract:
        return api_error_payload(request_id, "internal_error", "内部服务器错误")
    return {"detail": "内部服务器错误", "request_id": request_id}


def uses_v1_contract(request: Request) -> bool:
    return request.url.path.startswith("/v1/")


def error_code_from_http_detail(detail: Any, status_code: int) -> str | None:
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
        nested = detail.get("detail")
        if isinstance(nested, dict):
            nested_code = nested.get("code")
            if isinstance(nested_code, str) and nested_code.strip():
                return nested_code.strip()
    return f"http_{status_code}" if status_code >= 400 else None


def create_app() -> FastAPI:
    app = FastAPI(
        title="PortraitHub Inference Service",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if ENABLE_API_DOCS else None,
        redoc_url="/redoc" if ENABLE_API_DOCS else None,
        openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
    )
    app.add_middleware(
        HotReloadTrustedHostMiddleware,
        allowed_hosts_getter=lambda: TRUSTED_HOSTS,
        www_redirect=False,
    )
    configure_opentelemetry(app)

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request.state.portrait_error_code = "validation_error"
        request_id = request_id_from_headers(request)
        if uses_v1_contract(request):
            content = validation_error_payload(exc, request_id)
        else:
            content = validation_error_payload(exc)
            content["request_id"] = request_id
        return JSONResponse(status_code=422, content=content)

    @app.exception_handler(PortraitError)
    async def portrait_error_exception_handler(
        request: Request, exc: PortraitError
    ) -> JSONResponse:
        request.state.portrait_error_code = exc.code
        request_id = request_id_from_headers(request)
        content = (
            api_error_payload(request_id, exc.code, exc.message, details=exc.details)
            if uses_v1_contract(request)
            else {"detail": exc.public_detail(), "request_id": request_id}
        )
        return JSONResponse(
            status_code=exc.status_code, content=content, headers=exc.headers
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        request_id = request_id_from_headers(request)
        code = error_code_from_http_detail(exc.detail, exc.status_code)
        request.state.portrait_error_code = code
        content = (
            error_payload_from_http_detail(exc.detail, exc.status_code, request_id)
            if uses_v1_contract(request)
            else {"detail": exc.detail}
        )
        return JSONResponse(
            status_code=exc.status_code, content=content, headers=exc.headers
        )

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next: Any) -> Response:
        request_id = request_id_from_headers(request)
        traceparent = traceparent_from_headers(request)
        tenant_id = (
            request.headers.get("x-tenant-id")
            or inferred_tenant_id_from_request(request)
            or None
        )
        context_tokens = set_log_context(
            request_id=request_id, tenant_id=tenant_id, traceparent=traceparent
        )
        start = now()
        logged_error_code: str | None = None
        try:
            observe("requests_total")
            try:
                request = limit_request_body(request)
                check_rate_limit(request)
                response = await call_next(request)
            except HTTPException as exc:
                logged_error_code = error_code_from_http_detail(
                    exc.detail, exc.status_code
                )
                response = JSONResponse(
                    status_code=exc.status_code,
                    content=error_payload_from_http_detail(
                        exc.detail, exc.status_code, request_id
                    )
                    if uses_v1_contract(request)
                    else {"detail": exc.detail},
                    headers=exc.headers,
                )
            except Exception:
                logged_error_code = "internal_error"
                duration = now() - start
                log_json(
                    logging.ERROR,
                    "http_request_failed",
                    request_id=request_id,
                    traceparent=traceparent,
                    method=request.method,
                    path=request.url.path,
                    duration_seconds=round(duration, 6),
                )
                response = JSONResponse(
                    status_code=500,
                    content=internal_error_payload(
                        request_id, v1_contract=uses_v1_contract(request)
                    ),
                )
            duration = now() - start
            observe_request_status(response.status_code)
            request_state = getattr(request, "state", None)
            tenant_id = getattr(request_state, "portrait_tenant_id", None) or tenant_id
            logged_error_code = logged_error_code or getattr(
                request_state, "portrait_error_code", None
            )
            application_id = getattr(
                request_state, "portrait_application_id", None
            ) or application_id_from_api_key(
                tenant_id, request.headers.get("x-api-key")
            )
            record_call_log(
                request_id=request_id,
                tenant_id=tenant_id,
                application_id=application_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=max(0, int(duration * 1000)),
                created_at=start,
                error_code=logged_error_code,
            )
            response.headers["X-Request-ID"] = request_id
            if traceparent:
                response.headers["traceparent"] = traceparent
            apply_security_headers(response)
            log_json(
                logging.INFO,
                "http_request",
                request_id=request_id,
                traceparent=traceparent,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_seconds=round(duration, 6),
            )
            return cast(Response, response)
        finally:
            reset_log_context(context_tokens)

    app.include_router(router)
    return app


def configure_opentelemetry(app: FastAPI) -> None:
    if not OPENTELEMETRY_ENABLED:
        return
    try:  # pragma: no cover - 可选的生产环境依赖
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # pragma: no cover - 可选依赖缺失
        logger.warning(
            "opentelemetry is enabled but dependencies are unavailable: %s", exc
        )
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": OTEL_SERVICE_NAME})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


app = create_app()
