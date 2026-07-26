from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import commercial_concurrency
from app.commercial_concurrency import acquire_commercial_slot, release_commercial_slot, reset_commercial_slots


def test_commercial_concurrency_is_scoped_and_released() -> None:
    reset_commercial_slots()
    token = acquire_commercial_slot("tenant-a", "project-a", 1)

    with pytest.raises(HTTPException) as exhausted:
        acquire_commercial_slot("tenant-a", "project-a", 1)
    assert exhausted.value.status_code == 429
    assert exhausted.value.detail["code"] == "commercial_concurrency_exhausted"

    acquire_commercial_slot("tenant-a", "project-b", 1)
    release_commercial_slot("tenant-a", "project-a", token)
    acquire_commercial_slot("tenant-a", "project-a", 1)
    reset_commercial_slots()


class FakeRedis:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.members: dict[str, dict[str, int]] = {}

    def eval(self, script: str, _key_count: int, key: str, *arguments: object) -> int:
        if self.unavailable:
            raise ConnectionError("redis unavailable")
        if "ZREMRANGEBYSCORE" in script:
            now_ms, expires_ms, limit, token, _lease_ms = arguments
            current = self.members.setdefault(key, {})
            current = {member: expiry for member, expiry in current.items() if expiry > int(now_ms)}
            self.members[key] = current
            if len(current) >= int(limit):
                return 0
            current[str(token)] = int(expires_ms)
            return 1
        token = str(arguments[0])
        return 1 if self.members.setdefault(key, {}).pop(token, None) is not None else 0


def test_redis_commercial_concurrency_is_atomic_and_released(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(commercial_concurrency.settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(commercial_concurrency.settings, "COMMERCIAL_CONCURRENCY_LEASE_SECONDS", 60.0)
    monkeypatch.setattr(commercial_concurrency, "_REDIS_CLIENT", fake)
    monkeypatch.setattr(commercial_concurrency, "redis", None)

    token = acquire_commercial_slot("tenant-a", "project-a", 1)
    with pytest.raises(HTTPException) as exhausted:
        acquire_commercial_slot("tenant-a", "project-a", 1)
    assert exhausted.value.status_code == 429

    release_commercial_slot("tenant-a", "project-a", token)
    assert acquire_commercial_slot("tenant-a", "project-a", 1)


def test_redis_commercial_concurrency_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commercial_concurrency.settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(commercial_concurrency, "_REDIS_CLIENT", FakeRedis(unavailable=True))

    with pytest.raises(HTTPException) as unavailable:
        acquire_commercial_slot("tenant-a", "project-a", 1)
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["code"] == "commercial_concurrency_backend_unavailable"
