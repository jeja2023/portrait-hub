from __future__ import annotations

import importlib.util

try:  # pragma: no cover - 可选生产依赖
    import boto3
except Exception:  # pragma: no cover - 本地环境可能故意缺少该依赖
    boto3 = None

try:  # pragma: no cover - 可选生产依赖
    import redis
except Exception:  # pragma: no cover - 本地环境可能故意缺少该依赖
    redis = None

from app import settings
from app.postgres_core import postgres_driver_available, postgres_pool_available

PRODUCTION_PROFILES = {"prod", "production"}
PRODUCTION_VECTOR_BACKENDS = {"pgvector", "qdrant"}


class ProductionExternalizationError(RuntimeError):
    pass


def production_profile_enabled(profile: str | None = None) -> bool:
    value = settings.PORTRAIT_RUNTIME_PROFILE if profile is None else profile
    return str(value).strip().lower() in PRODUCTION_PROFILES


def optional_module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def optional_dependency_available(module_name: str, loaded_module: object | None) -> bool:
    return loaded_module is not None or optional_module_available(module_name)


def production_externalization_failures() -> list[str]:
    if not production_profile_enabled() or not settings.PRODUCTION_EXTERNAL_SERVICES_REQUIRED:
        return []

    failures: list[str] = []
    if not settings.AUTH_REQUIRED:
        failures.append("生产环境中 AUTH_REQUIRED 必须为 true")
    if not (settings.API_TOKEN or settings.RBAC_ENABLED):
        failures.append("生产环境必须配置 API_TOKEN 或启用 RBAC_ENABLED")
    if settings.API_TOKEN and not settings.API_TOKEN_TENANT_ID and not settings.API_TOKEN_ALLOW_TENANT_OVERRIDE:
        failures.append("生产环境中的 API_TOKEN 必须配置 API_TOKEN_TENANT_ID，或显式允许平台跨租户访问")
    if settings.DEBUG_ENDPOINTS_ENABLED:
        failures.append("生产环境中 DEBUG_ENDPOINTS_ENABLED 必须为 false")
    if settings.ENABLE_API_DOCS:
        failures.append("生产环境中 ENABLE_API_DOCS 必须为 false")
    if settings.RATE_LIMIT_PER_MINUTE <= 0:
        failures.append("生产环境中 RATE_LIMIT_PER_MINUTE 必须大于 0")
    if settings.MAX_REQUEST_BODY_BYTES < settings.MAX_VIDEO_BYTES + 1024 * 1024:
        failures.append("生产环境中 MAX_REQUEST_BODY_BYTES 必须至少比 MAX_VIDEO_BYTES 大 1 MiB")
    if settings.MAX_REQUEST_BODY_BYTES > settings.MAX_VIDEO_BYTES + 16 * 1024 * 1024:
        failures.append("生产环境中 MAX_REQUEST_BODY_BYTES 不应显著超过 MAX_VIDEO_BYTES")
    if settings.VIDEO_JOB_WORKER_IN_PROCESS:
        failures.append("生产环境中 VIDEO_JOB_WORKER_IN_PROCESS 必须为 false")
    if settings.PORTRAIT_STORAGE_BACKEND != "postgres":
        failures.append("生产环境中 PORTRAIT_STORAGE_BACKEND 必须为 postgres")
    if not settings.POSTGRES_DSN.strip():
        failures.append("POSTGRES_DSN must be configured in production")
    if not postgres_driver_available():
        failures.append("psycopg must be installed in production")
    if not postgres_pool_available():
        failures.append("psycopg_pool must be installed in production")

    if settings.PORTRAIT_VECTOR_BACKEND not in PRODUCTION_VECTOR_BACKENDS:
        failures.append("生产环境中 PORTRAIT_VECTOR_BACKEND 必须为 pgvector 或 qdrant")
    if not settings.PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND:
        failures.append("PORTRAIT_REQUIRE_PRODUCTION_VECTOR_BACKEND must be true in production")
    if settings.PORTRAIT_VECTOR_BACKEND == "qdrant":
        if not settings.QDRANT_URL.strip():
            failures.append("QDRANT_URL must be configured when PORTRAIT_VECTOR_BACKEND=qdrant")
        if not optional_module_available("qdrant_client"):
            failures.append("qdrant-client must be installed when PORTRAIT_VECTOR_BACKEND=qdrant")
    if settings.PORTRAIT_VECTOR_BACKEND == "pgvector" and not optional_module_available("pgvector"):
        failures.append("pgvector must be installed when PORTRAIT_VECTOR_BACKEND=pgvector")

    if settings.PORTRAIT_OBJECT_STORAGE_BACKEND != "s3":
        failures.append("生产环境中 PORTRAIT_OBJECT_STORAGE_BACKEND 必须为 s3")
    if not settings.S3_BUCKET.strip():
        failures.append("S3_BUCKET must be configured in production")
    if not settings.S3_REGION.strip():
        failures.append("S3_REGION must be configured in production")
    if not optional_dependency_available("boto3", boto3):
        failures.append("boto3 must be installed in production")

    if settings.TASK_QUEUE_BACKEND != "redis":
        failures.append("生产环境中 TASK_QUEUE_BACKEND 必须为 redis")
    if not settings.REDIS_URL.strip():
        failures.append("REDIS_URL must be configured in production")
    if not optional_dependency_available("redis", redis):
        failures.append("redis must be installed in production")

    if not settings.OPENTELEMETRY_ENABLED:
        failures.append("生产环境中 OPENTELEMETRY_ENABLED 必须为 true")
    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT.strip():
        failures.append("OTEL_EXPORTER_OTLP_ENDPOINT must be configured in production")
    for module_name in [
        "opentelemetry.sdk",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.instrumentation.fastapi",
    ]:
        if not optional_module_available(module_name):
            failures.append(f"{module_name} must be installed in production")

    if not settings.PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES:
        failures.append("PORTRAIT_REQUIRE_PRODUCTION_MODEL_CAPABILITIES must be true in production")
    return failures


def validate_production_externalization() -> None:
    failures = production_externalization_failures()
    if failures:
        raise ProductionExternalizationError("production external services are not fully configured: " + "; ".join(failures))


__all__ = [
    "PRODUCTION_PROFILES",
    "PRODUCTION_VECTOR_BACKENDS",
    "ProductionExternalizationError",
    "production_externalization_failures",
    "production_profile_enabled",
    "validate_production_externalization",
]

