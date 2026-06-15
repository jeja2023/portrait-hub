import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

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
    split_cache_key,
    traceparent_from_headers,
)
from app.rate_limit import check_rate_limit
from app.routes import router
from app.security_headers import apply_security_headers
from app.settings import APP_VERSION, ENABLE_API_DOCS, MAX_REQUEST_BODY_BYTES, TRUSTED_HOSTS
from app.metrics import observe_request_status
from app.portrait_bootstrap import ensure_portrait_runtime_state_loaded


def request_body_too_large_detail() -> str:
    return f"request body too large: max {MAX_REQUEST_BODY_BYTES} bytes"


async def warmup_models() -> None:
    if not WARMUP_MODELS:
        return

    for item in WARMUP_MODELS:
        project_name, model_name = split_cache_key(item)
        model_path = get_model_path(project_name, model_name)
        key = cache_key(project_name, model_name)
        await get_or_load_model(key, model_path)
        logger.info("startup warmup completed: %s", key)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await ensure_portrait_runtime_state_loaded()
    await warmup_models()
    yield


def limit_request_body(request: Request) -> Request:
    if MAX_REQUEST_BODY_BYTES <= 0:
        return request

    raw_content_length = request.headers.get("content-length")
    if raw_content_length:
        try:
            content_length = int(raw_content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content-length header") from exc
        if content_length > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(status_code=413, detail=request_body_too_large_detail())

    received = 0
    receive = request.receive

    async def limited_receive() -> dict[str, Any]:
        nonlocal received
        message = await receive()
        if message.get("type") == "http.request":
            received += len(message.get("body", b""))
            if received > MAX_REQUEST_BODY_BYTES:
                raise HTTPException(status_code=413, detail=request_body_too_large_detail())
        return message

    return Request(request.scope, limited_receive)


def validation_error_payload(exc: RequestValidationError, request_id: str | None = None) -> dict[str, Any]:
    errors = []
    for error in exc.errors():
        errors.append(
            {
                "type": error.get("type", "validation_error"),
                "loc": validation_error_loc(error),
                "msg": error.get("msg", "validation error"),
            }
        )
    payload: dict[str, Any] = {"detail": errors}
    if request_id:
        payload["request_id"] = request_id
    return payload


def validation_error_loc(error: dict[str, Any]) -> list[Any]:
    loc = list(error.get("loc", []))
    if error.get("type") == "extra_forbidden" and loc:
        loc[-1] = "extra_field"
    return loc


def internal_error_payload(request_id: str) -> dict[str, Any]:
    return {
        "detail": "internal server error",
        "request_id": request_id,
    }


def create_app() -> FastAPI:
    app = FastAPI(
        title="PortraitHub Inference Service",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if ENABLE_API_DOCS else None,
        redoc_url="/redoc" if ENABLE_API_DOCS else None,
        openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS, www_redirect=False)

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=validation_error_payload(exc, request_id_from_headers(request)))

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next: Any) -> Response:
        request_id = request_id_from_headers(request)
        traceparent = traceparent_from_headers(request)
        start = now()
        observe("requests_total")
        try:
            request = limit_request_body(request)
            check_rate_limit(request)
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail, "request_id": request_id},
                headers=exc.headers,
            )
        except Exception:
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
            response = JSONResponse(status_code=500, content=internal_error_payload(request_id))
        duration = now() - start
        observe_request_status(response.status_code)
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
        return response

    app.include_router(router)
    return app


app = create_app()
