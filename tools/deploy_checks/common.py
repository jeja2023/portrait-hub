"""Shared report and file-reading primitives for deployment checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.report_redaction import redact_for_report


@dataclass
class DeployReport:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: Any = None) -> None:
        self.checks.append(
            {"name": name, "ok": ok, "detail": redact_for_report(detail)}
        )

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.checks)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")
