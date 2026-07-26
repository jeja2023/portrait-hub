from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from fastapi import HTTPException, status

from app import settings
from app.postgres_control_state import ControlStateConflict, load_control_snapshot, save_control_snapshot

StateFactory = Callable[[], dict[str, Any]]
StateValidator = Callable[[Any], dict[str, Any]]


class ControlStateBackend:
    def __init__(
        self,
        state_key: str,
        state: dict[str, Any],
        lock: threading.RLock,
        empty_state: StateFactory,
        validator: StateValidator,
    ) -> None:
        self.state_key = state_key
        self.state = state
        self.lock = lock
        self.empty_state = empty_state
        self.validator = validator
        self.revision = -1
        self._operation_state = threading.local()

    def postgres_enabled(self) -> bool:
        return settings.PORTRAIT_STORAGE_BACKEND == "postgres"

    def invalidate(self) -> None:
        """Force the next PostgreSQL operation to reload the authoritative snapshot."""
        self.revision = -1

    def _apply(self, payload: Any, revision: int) -> None:
        validated = self.validator(payload if payload is not None else self.empty_state())
        self.state.clear()
        self.state.update(validated)
        self.revision = max(0, int(revision))

    def refresh(self, *, force: bool = False) -> bool:
        if not self.postgres_enabled():
            return False
        try:
            payload, revision = load_control_snapshot(self.state_key)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "control_state_unavailable", "message": "control state is unavailable"},
            ) from exc
        if not force and revision <= self.revision:
            return False
        self._apply(payload, revision)
        return True

    @contextmanager
    def operation(self, *, refresh: bool = True) -> Iterator[None]:
        with self.lock:
            depth = int(getattr(self._operation_state, "depth", 0))
            if refresh and depth == 0:
                self.refresh()
            self._operation_state.depth = depth + 1
            try:
                yield
            finally:
                self._operation_state.depth = depth

    def load(self, local_payload: Any) -> None:
        with self.operation(refresh=False):
            if self.postgres_enabled():
                self.refresh(force=True)
            else:
                self._apply(local_payload, 0)

    def save(self, *, actor: str = "portrait-api") -> None:
        if not self.postgres_enabled():
            raise RuntimeError("PostgreSQL control state backend is not enabled")
        payload = copy.deepcopy(self.state)
        try:
            self.revision = save_control_snapshot(
                self.state_key,
                payload,
                max(0, self.revision),
                actor=actor,
            )
        except ControlStateConflict as exc:
            self.refresh(force=True)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "conflict", "message": "control state changed concurrently; retry the request"},
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "control_state_unavailable", "message": "control state persistence is unavailable"},
            ) from exc


class ControlStateLock:
    def __init__(self) -> None:
        self.raw = threading.RLock()
        self.backend: ControlStateBackend | None = None
        self._contexts = threading.local()

    def bind(self, backend: ControlStateBackend) -> None:
        self.backend = backend

    def __enter__(self) -> ControlStateLock:
        context = self.backend.operation() if self.backend is not None else self._raw_context()
        stack = list(getattr(self._contexts, "stack", []))
        context.__enter__()
        stack.append(context)
        self._contexts.stack = stack
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        stack = list(getattr(self._contexts, "stack", []))
        if not stack:
            raise RuntimeError("control state lock exit without enter")
        context = stack.pop()
        self._contexts.stack = stack
        return cast(bool | None, context.__exit__(exc_type, exc, traceback))

    @contextmanager
    def _raw_context(self) -> Iterator[None]:
        with self.raw:
            yield


__all__ = ["ControlStateBackend", "ControlStateLock"]
