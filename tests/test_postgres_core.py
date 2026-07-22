from __future__ import annotations

from typing import Any, ClassVar

from app import postgres_core


class FakePool:
    instances: ClassVar[list[FakePool]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.open_count = 0
        self.instances.append(self)

    def open(self) -> None:
        self.open_count += 1


def test_get_postgres_pool_opens_and_reuses_pool(monkeypatch: Any) -> None:
    FakePool.instances.clear()
    monkeypatch.setattr(postgres_core, "POSTGRES_DSN", "postgresql://example/test")
    monkeypatch.setattr(postgres_core, "POSTGRES_POOL", None)
    monkeypatch.setattr(postgres_core, "psycopg", object())
    monkeypatch.setattr(postgres_core, "ConnectionPool", FakePool)

    first = postgres_core.get_postgres_pool()
    second = postgres_core.get_postgres_pool()

    assert first is second
    assert len(FakePool.instances) == 1
    assert FakePool.instances[0].open_count == 1
    assert FakePool.instances[0].kwargs["open"] is False
