import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, URL

from app import rate_limit


class DummyRequest:
    def __init__(self, path: str, tenant_id: str = "tenant-a"):
        self.url = URL(f"http://testserver{path}")
        self.headers = Headers({"x-tenant-id": tenant_id})


def test_rate_limit_cleanup_removes_idle_buckets(monkeypatch) -> None:
    rate_limit.BUCKETS.clear()
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BUCKET_TTL_SECONDS", 10)
    monkeypatch.setattr(rate_limit, "LAST_CLEANUP_AT", 0.0)
    rate_limit.BUCKETS["active:/v1/models"] = rate_limit.TokenBucket(tokens=1.0, updated_at=95.0)
    rate_limit.BUCKETS["idle:/v1/models"] = rate_limit.TokenBucket(tokens=1.0, updated_at=80.0)

    rate_limit.cleanup_idle_buckets(now=100.0)

    assert "active:/v1/models" in rate_limit.BUCKETS
    assert "idle:/v1/models" not in rate_limit.BUCKETS


def test_rate_limit_rejects_new_bucket_when_capacity_is_full(monkeypatch) -> None:
    rate_limit.BUCKETS.clear()
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_MAX_BUCKETS", 1)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BUCKET_TTL_SECONDS", 0)
    rate_limit.BUCKETS["tenant-a:/v1/models"] = rate_limit.TokenBucket(tokens=1.0, updated_at=100.0)

    with pytest.raises(HTTPException) as exc_info:
        rate_limit.ensure_bucket_capacity(now=101.0)

    assert exc_info.value.status_code == 429
    assert "bucket capacity" in str(exc_info.value.detail)
    assert exc_info.value.headers == {"Retry-After": "1"}


def test_rate_limit_rejects_same_tenant_path_after_burst(monkeypatch) -> None:
    rate_limit.BUCKETS.clear()
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 60)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_MAX_BUCKETS", 100)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BUCKET_TTL_SECONDS", 3600)
    monkeypatch.setattr(rate_limit, "wall_time", lambda: 100.0)
    request = DummyRequest("/v1/models", "tenant-a")

    rate_limit.check_rate_limit(request)
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.check_rate_limit(request)

    assert exc_info.value.status_code == 429
    assert "rate limit exceeded" in str(exc_info.value.detail)
    assert exc_info.value.headers == {"Retry-After": "1"}


def test_retry_after_uses_refill_window() -> None:
    assert rate_limit.retry_after_seconds(tokens=0.0, refill_per_second=0.5) == 2
    assert rate_limit.retry_after_seconds(tokens=0.99, refill_per_second=100.0) == 1
    assert rate_limit.retry_after_seconds(tokens=0.0, refill_per_second=0.0) == 60
