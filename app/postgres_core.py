from __future__ import annotations

import json
import math
from contextlib import contextmanager
from typing import Any, Iterator

from app.observability import logger, trace_span
from app.portrait_response import HEALTH_CHECK_FAILED, exception_log_summary
from app.settings import POSTGRES_CONNECT_TIMEOUT_SECONDS, POSTGRES_DSN, POSTGRES_POOL_MAX_SIZE, POSTGRES_POOL_MIN_SIZE

try:  # pragma: no cover - 可选的生产环境依赖
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - 当依赖不存在时执行
    psycopg = None
    dict_row = None

try:  # pragma: no cover - 可选的生产环境依赖
    from psycopg_pool import ConnectionPool
except Exception:  # pragma: no cover - 当依赖不存在时执行
    ConnectionPool = None


class PostgresUnavailable(RuntimeError):
    pass


POSTGRES_POOL: Any | None = None


def postgres_configured() -> bool:
    return bool(POSTGRES_DSN.strip())


def postgres_driver_available() -> bool:
    return psycopg is not None


def postgres_pool_available() -> bool:
    return ConnectionPool is not None


def require_postgres() -> None:
    if not postgres_configured():
        raise PostgresUnavailable("POSTGRES_DSN is not configured")
    if psycopg is None:
        raise PostgresUnavailable("psycopg is not installed; install requirements-prod-optional.txt")


def get_postgres_pool() -> Any | None:
    global POSTGRES_POOL
    if not postgres_configured() or psycopg is None or ConnectionPool is None:
        return None
    if POSTGRES_POOL is None:
        POSTGRES_POOL = ConnectionPool(
            conninfo=POSTGRES_DSN,
            min_size=max(0, int(POSTGRES_POOL_MIN_SIZE)),
            max_size=max(1, int(POSTGRES_POOL_MAX_SIZE)),
            timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
            kwargs={"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS},
            open=False,
        )
    return POSTGRES_POOL


@contextmanager
def postgres_connection(row_factory: Any = None) -> Iterator[Any]:
    require_postgres()
    pool = get_postgres_pool()
    if pool is not None:
        with trace_span("portrait.postgres.connection", pooled=True):
            with pool.connection() as connection:
                previous_row_factory = getattr(connection, "row_factory", None)
                if row_factory is not None:
                    connection.row_factory = row_factory
                try:
                    yield connection
                finally:
                    if row_factory is not None:
                        connection.row_factory = previous_row_factory
        return

    kwargs: dict[str, Any] = {"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS}
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    with trace_span("portrait.postgres.connection", pooled=False):
        with psycopg.connect(POSTGRES_DSN, **kwargs) as connection:
            yield connection


def postgres_health() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "configured": postgres_configured(),
        "driver_available": postgres_driver_available(),
        "pool_driver_available": postgres_pool_available(),
        "pool_enabled": postgres_pool_available() and postgres_configured(),
        "pool_min_size": POSTGRES_POOL_MIN_SIZE,
        "pool_max_size": POSTGRES_POOL_MAX_SIZE,
        "connect_timeout_seconds": POSTGRES_CONNECT_TIMEOUT_SECONDS,
    }
    if not postgres_configured() or psycopg is None:
        return {**payload, "status": "not_ready"}
    try:
        with trace_span("portrait.postgres.health"):
            with postgres_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
        return {**payload, "status": "ready"}
    except Exception as exc:  # pragma: no cover - 需要外部数据库支持
        logger.warning("postgres 健康检查失败: %s", exception_log_summary(exc))
        return {**payload, "status": "error", "error": HEALTH_CHECK_FAILED}


def jsonb(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def normalized_embedding(values: list[float]) -> list[float]:
    normalized: list[float] = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            number = 0.0
        normalized.append(number)
    return normalized


def embedding_bytes(values: list[float]) -> bytes:
    return json.dumps(normalized_embedding(values), separators=(",", ":")).encode("utf-8")


def vector_literal(values: list[float]) -> str:
    numbers = normalized_embedding(values)
    return "[" + ",".join(format(value, ".8g") for value in numbers) + "]"



__all__ = [
    "ConnectionPool",
    "PostgresUnavailable",
    "POSTGRES_CONNECT_TIMEOUT_SECONDS",
    "POSTGRES_DSN",
    "POSTGRES_POOL",
    "POSTGRES_POOL_MAX_SIZE",
    "POSTGRES_POOL_MIN_SIZE",
    "dict_row",
    "postgres_configured",
    "postgres_driver_available",
    "postgres_pool_available",
    "psycopg",
    "require_postgres",
    "get_postgres_pool",
    "postgres_connection",
    "postgres_health",
    "jsonb",
    "normalized_embedding",
    "embedding_bytes",
    "vector_literal",
]
