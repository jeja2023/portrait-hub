from __future__ import annotations

import time

import pytest

from app import portrait_idempotency, settings


@pytest.fixture(autouse=True)
def reset_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REDIS_URL", "")
    portrait_idempotency.reset_idempotency_store()


def context(*, owner: str = "owner", request_hash: str = "request") -> portrait_idempotency.IdempotencyContext:
    return portrait_idempotency.IdempotencyContext(
        storage_key="portrait:idempotency:test",
        public_key_fingerprint="fingerprint",
        request_hash=request_hash,
        owner_token=owner,
        expires_at=time.time() + 60,
    )


def test_memory_backend_exposes_in_progress_and_completed_records() -> None:
    first = context()
    assert portrait_idempotency._reserve(first) is None

    pending = portrait_idempotency._reserve(context(owner="second"))
    assert pending is not None
    assert pending.state == "in_progress"
    completed = portrait_idempotency.IdempotencyRecord(
        request_hash=first.request_hash,
        owner_token=first.owner_token,
        state="completed",
        expires_at=first.expires_at,
        status_code=201,
        headers={"content-type": "application/json"},
        body_base64="e30=",
    )
    assert portrait_idempotency._complete(first, completed) is True

    replay = portrait_idempotency._reserve(context(owner="third"))
    assert replay is not None
    assert replay.state == "completed"
    assert replay.status_code == 201


def test_memory_backend_only_owner_can_complete_or_release() -> None:
    first = context()
    assert portrait_idempotency._reserve(first) is None
    other = context(owner="other")
    completed = portrait_idempotency.IdempotencyRecord(
        request_hash=first.request_hash,
        owner_token=other.owner_token,
        state="completed",
        expires_at=first.expires_at,
        status_code=200,
        body_base64="e30=",
    )

    assert portrait_idempotency._complete(other, completed) is False
    portrait_idempotency._release(other)
    assert portrait_idempotency._reserve(context(owner="third")) is not None
