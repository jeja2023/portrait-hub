from __future__ import annotations

import copy

import pytest
from fastapi import HTTPException

from app import portrait_control_state, settings
from app.portrait_control_state import ControlStateBackend, ControlStateLock
from app.postgres_control_state import ControlStateConflict


def test_control_state_backend_refreshes_and_saves_with_revision_cas(monkeypatch) -> None:
    shared = {"payload": None, "revision": 0}

    def load(_state_key: str):
        return copy.deepcopy(shared["payload"]), shared["revision"]

    def save(_state_key: str, payload: dict, expected_revision: int, *, actor: str):
        assert actor == "test-owner"
        if expected_revision != shared["revision"]:
            raise ControlStateConflict("stale")
        shared["revision"] += 1
        shared["payload"] = copy.deepcopy(payload)
        return shared["revision"]

    monkeypatch.setattr(settings, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(portrait_control_state, "load_control_snapshot", load)
    monkeypatch.setattr(portrait_control_state, "save_control_snapshot", save)
    state = {"revision": 0, "items": []}
    lock = ControlStateLock()
    backend = ControlStateBackend(
        "test",
        state,
        lock.raw,
        lambda: {"revision": 0, "items": []},
        lambda value: copy.deepcopy(value),
    )
    lock.bind(backend)

    with lock:
        state["items"].append({"id": "one"})
        backend.save(actor="test-owner")

    assert shared == {"payload": {"revision": 0, "items": [{"id": "one"}]}, "revision": 1}
    shared["revision"] = 2
    shared["payload"] = {"revision": 1, "items": [{"id": "remote"}]}
    with lock:
        assert state["items"] == [{"id": "remote"}]


def test_control_state_backend_conflict_restores_latest_snapshot(monkeypatch) -> None:
    shared = {"payload": {"revision": 0, "items": []}, "revision": 1}

    def load(_state_key: str):
        return copy.deepcopy(shared["payload"]), shared["revision"]

    def save(_state_key: str, payload: dict, expected_revision: int, *, actor: str):
        del actor
        if expected_revision != shared["revision"]:
            raise ControlStateConflict("stale")
        shared["revision"] += 1
        shared["payload"] = copy.deepcopy(payload)
        return shared["revision"]

    monkeypatch.setattr(settings, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(portrait_control_state, "load_control_snapshot", load)
    monkeypatch.setattr(portrait_control_state, "save_control_snapshot", save)

    def instance():
        state = {"revision": 0, "items": []}
        lock = ControlStateLock()
        backend = ControlStateBackend(
            "shared",
            state,
            lock.raw,
            lambda: {"revision": 0, "items": []},
            lambda value: copy.deepcopy(value),
        )
        lock.bind(backend)
        return state, lock, backend

    state_a, lock_a, backend_a = instance()
    state_b, lock_b, backend_b = instance()
    with lock_b:
        state_b["items"].append({"id": "stale"})
        with lock_a:
            state_a["items"].append({"id": "committed"})
            backend_a.save(actor="writer-a")
        with pytest.raises(HTTPException) as exc_info:
            backend_b.save(actor="writer-b")

    assert exc_info.value.status_code == 409
    assert state_b["items"] == [{"id": "committed"}]


def test_control_state_backend_invalidate_forces_next_refresh(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PORTRAIT_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(
        portrait_control_state,
        "load_control_snapshot",
        lambda _state_key: ({"revision": 0, "items": ["remote"]}, 8),
    )
    state = {"revision": 0, "items": ["cached"]}
    backend = ControlStateBackend("test", state, ControlStateLock().raw, lambda: {"revision": 0, "items": []}, copy.deepcopy)
    backend.revision = 7

    backend.invalidate()

    assert backend.revision == -1
    assert backend.refresh() is True
    assert state["items"] == ["remote"]
