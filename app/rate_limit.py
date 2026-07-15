import threading
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from app.observability import wall_time
from app.security import authenticated_request_identity
from app.settings import (
    RATE_LIMIT_BUCKET_TTL_SECONDS,
    RATE_LIMIT_BURST,
    RATE_LIMIT_MAX_BUCKETS,
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_TRUST_FORWARDED_FOR,
)


@dataclass
class TokenBucket:
    tokens: float
    updated_at: float


@dataclass(frozen=True)
class RequestRateLimits:
    per_minute: int
    burst: int


BUCKETS: dict[str, TokenBucket] = {}
BUCKETS_LOCK = threading.RLock()
LAST_CLEANUP_AT = 0.0


def retry_after_seconds(tokens: float, refill_per_second: float) -> int:
    if refill_per_second <= 0:
        return 60
    needed = max(0.0, 1.0 - tokens)
    return max(1, int((needed / refill_per_second) + 0.999))


def positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def application_policy_from_request(request: Request, now: float) -> dict[str, Any] | None:
    tenant_id = request.headers.get("x-tenant-id")
    api_key = request.headers.get("x-api-key")
    if not api_key:
        return None
    from app.portrait_access import application_key_matches_any_tenant, application_request_policy

    if not tenant_id:
        application = application_key_matches_any_tenant(api_key)
        tenant_id = str(application.get("tenant_id") or "") if application else ""
    if not tenant_id:
        return None
    policy = application_request_policy(tenant_id, api_key, now)
    state = getattr(request, "state", None)
    if state is not None:
        state.portrait_tenant_id = tenant_id
        state.portrait_application_id = str(policy.get("app_id") or "") if policy else None
    return policy


def request_rate_limits(request: Request, now: float) -> RequestRateLimits:
    per_minute = RATE_LIMIT_PER_MINUTE
    burst = RATE_LIMIT_BURST if RATE_LIMIT_BURST > 0 else RATE_LIMIT_PER_MINUTE
    application_policy = application_policy_from_request(request, now)
    if application_policy is not None:
        application_per_minute = positive_int(application_policy.get("rate_limit_per_minute"))
        application_burst = positive_int(application_policy.get("rate_limit_burst"))
        if application_per_minute is not None:
            per_minute = application_per_minute
        if application_burst is not None:
            burst = application_burst
        elif application_per_minute is not None:
            burst = application_per_minute
    return RequestRateLimits(per_minute=per_minute, burst=burst)


def rate_limit_enabled() -> bool:
    return RATE_LIMIT_PER_MINUTE > 0


def client_identity(request: Request) -> str:
    """用于限流的稳定每调用者身份。

    已认证的调用者以其认证身份为主键；匿名
    调用者回退到客户端 IP（在部署信任反向代理时，可选使用最左侧的 X-Forwarded-For
    跃点）。
    """
    identity = authenticated_request_identity(
        request.headers.get("authorization"),
        request.headers.get("x-api-key"),
        request.headers.get("x-tenant-id"),
    )
    if identity:
        return identity
    if RATE_LIMIT_TRUST_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first_hop = forwarded.split(",", 1)[0].strip()
            if first_hop:
                return first_hop
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def bucket_key(request: Request) -> str:
    # 基于稳定的客户端身份，而不是由客户端控制的 x-tenant-id
    # 请求头来建键；否则，调用者可以通过轮换请求头来为每次请求生成全新的令牌桶，从而完全绕过限流器。
    return f"{client_identity(request)}:{request.url.path}"


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
                detail="限流桶容量已耗尽",
                headers={"Retry-After": str(max(1, RATE_LIMIT_BUCKET_TTL_SECONDS))},
            )


def check_rate_limit(request: Request) -> None:
    now = wall_time()
    limits = request_rate_limits(request, now)
    if limits.per_minute <= 0:
        return
    with BUCKETS_LOCK:
        cleanup_idle_buckets(now)
        burst = limits.burst if limits.burst > 0 else limits.per_minute
        refill_per_second = limits.per_minute / 60.0
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
                detail="已超过限流阈值",
                headers={"Retry-After": str(retry_after_seconds(bucket.tokens, refill_per_second))},
            )
        bucket.tokens -= 1.0