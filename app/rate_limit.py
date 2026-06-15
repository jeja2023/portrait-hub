from dataclasses import dataclass
import threading

from fastapi import HTTPException, Request, status

from app.observability import wall_time
from app.portrait_security import tenant_id_from_request
from app.settings import (
    RATE_LIMIT_BUCKET_TTL_SECONDS,
    RATE_LIMIT_BURST,
    RATE_LIMIT_MAX_BUCKETS,
    RATE_LIMIT_PER_MINUTE,
)


@dataclass
class TokenBucket:
    tokens: float
    updated_at: float


BUCKETS: dict[str, TokenBucket] = {}
BUCKETS_LOCK = threading.RLock()
LAST_CLEANUP_AT = 0.0


def retry_after_seconds(tokens: float, refill_per_second: float) -> int:
    if refill_per_second <= 0:
        return 60
    needed = max(0.0, 1.0 - tokens)
    return max(1, int((needed / refill_per_second) + 0.999))


def rate_limit_enabled() -> bool:
    return RATE_LIMIT_PER_MINUTE > 0


def bucket_key(request: Request) -> str:
    tenant_id = tenant_id_from_request(request)
    return f"{tenant_id}:{request.url.path}"


def cleanup_idle_buckets(now: float) -> None:
    global LAST_CLEANUP_AT
    with BUCKETS_LOCK:
        if RATE_LIMIT_BUCKET_TTL_SECONDS <= 0:
            return
        if now - LAST_CLEANUP_AT < min(float(RATE_LIMIT_BUCKET_TTL_SECONDS), 60.0):
            return
        cutoff = now - RATE_LIMIT_BUCKET_TTL_SECONDS
        idle_keys = [key for key, bucket in BUCKETS.items() if bucket.updated_at < cutoff]
        for key in idle_keys:
            BUCKETS.pop(key, None)
        LAST_CLEANUP_AT = now


def ensure_bucket_capacity(now: float) -> None:
    with BUCKETS_LOCK:
        if RATE_LIMIT_MAX_BUCKETS <= 0 or len(BUCKETS) < RATE_LIMIT_MAX_BUCKETS:
            return
        cleanup_idle_buckets(now)
        if len(BUCKETS) >= RATE_LIMIT_MAX_BUCKETS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit bucket capacity exceeded",
                headers={"Retry-After": str(max(1, RATE_LIMIT_BUCKET_TTL_SECONDS))},
            )


def check_rate_limit(request: Request) -> None:
    if not rate_limit_enabled():
        return
    now = wall_time()
    with BUCKETS_LOCK:
        cleanup_idle_buckets(now)
        burst = RATE_LIMIT_BURST if RATE_LIMIT_BURST > 0 else RATE_LIMIT_PER_MINUTE
        refill_per_second = RATE_LIMIT_PER_MINUTE / 60.0
        key = bucket_key(request)
        bucket = BUCKETS.get(key)
        if bucket is None:
            ensure_bucket_capacity(now)
            bucket = TokenBucket(tokens=float(burst), updated_at=now)
            BUCKETS[key] = bucket
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(burst), bucket.tokens + elapsed * refill_per_second)
        bucket.updated_at = now
        if bucket.tokens < 1.0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
                headers={"Retry-After": str(retry_after_seconds(bucket.tokens, refill_per_second))},
            )
        bucket.tokens -= 1.0
