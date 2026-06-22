import pytest
from fastapi import HTTPException
from starlette.datastructures import Address, Headers, URL

from app import rate_limit


class DummyRequest:
    def __init__(self, path: str, tenant_id: str = "tenant-a", host: str = "203.0.113.1", headers: dict | None = None):
        self.url = URL(f"http://testserver{path}")
        merged = {"x-tenant-id": tenant_id}
        if headers:
            merged.update(headers)
        self.headers = Headers(merged)
        self.client = Address(host=host, port=12345)


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


def test_rate_limit_cannot_be_bypassed_by_rotating_tenant_header(monkeypatch) -> None:
    rate_limit.BUCKETS.clear()
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 60)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_MAX_BUCKETS", 100)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BUCKET_TTL_SECONDS", 3600)
    monkeypatch.setattr(rate_limit, "wall_time", lambda: 100.0)

    # Same client IP, different forged tenant headers -> still throttled.
    rate_limit.check_rate_limit(DummyRequest("/v1/models", tenant_id="tenant-a"))
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.check_rate_limit(DummyRequest("/v1/models", tenant_id="forged-tenant"))

    assert exc_info.value.status_code == 429


def test_rate_limit_prefers_authenticated_identity_over_forged_headers(monkeypatch) -> None:
    rate_limit.BUCKETS.clear()
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 60)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_MAX_BUCKETS", 100)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BUCKET_TTL_SECONDS", 3600)
    monkeypatch.setattr(rate_limit, "wall_time", lambda: 100.0)
    monkeypatch.setattr(rate_limit, "authenticated_request_identity", lambda *args, **kwargs: "jwt:tenant-a")

    rate_limit.check_rate_limit(
        DummyRequest(
            "/v1/models",
            tenant_id="forged-tenant",
            host="198.51.100.7",
            headers={"authorization": "Bearer real-token"},
        )
    )
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.check_rate_limit(
            DummyRequest(
                "/v1/models",
                tenant_id="different-forged-tenant",
                host="198.51.100.8",
                headers={"authorization": "Bearer real-token"},
            )
        )

    assert exc_info.value.status_code == 429


def test_rate_limit_distinguishes_clients_by_ip(monkeypatch) -> None:
    rate_limit.BUCKETS.clear()
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MINUTE", 60)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_MAX_BUCKETS", 100)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_BUCKET_TTL_SECONDS", 3600)
    monkeypatch.setattr(rate_limit, "wall_time", lambda: 100.0)

    rate_limit.check_rate_limit(DummyRequest("/v1/models", host="198.51.100.7"))
    # A different IP gets its own bucket.
    rate_limit.check_rate_limit(DummyRequest("/v1/models", host="198.51.100.8"))


def test_client_identity_prefers_forwarded_for_when_trusted(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_TRUST_FORWARDED_FOR", True)
    request = DummyRequest("/v1/models", host="10.0.0.1", headers={"x-forwarded-for": "192.0.2.55, 10.0.0.1"})
    assert rate_limit.client_identity(request) == "192.0.2.55"


def test_client_identity_ignores_forwarded_for_when_untrusted(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_TRUST_FORWARDED_FOR", False)
    request = DummyRequest("/v1/models", host="10.0.0.1", headers={"x-forwarded-for": "192.0.2.55"})
    assert rate_limit.client_identity(request) == "10.0.0.1"


def test_retry_after_uses_refill_window() -> None:
    assert rate_limit.retry_after_seconds(tokens=0.0, refill_per_second=0.5) == 2
    assert rate_limit.retry_after_seconds(tokens=0.99, refill_per_second=100.0) == 1
    assert rate_limit.retry_after_seconds(tokens=0.0, refill_per_second=0.0) == 60
