import sys

from tools import type_check


def test_type_check_requires_mypy_unless_fallback_is_explicit(monkeypatch) -> None:
    monkeypatch.setattr(type_check.importlib.util, "find_spec", lambda name: None if name == "mypy" else object())
    monkeypatch.setattr(type_check, "mypy_strict_enabled", lambda: True)
    monkeypatch.setattr(type_check, "annotation_errors", lambda targets: [])
    monkeypatch.setattr(type_check, "discover_default_targets", lambda: ["main.py"])
    monkeypatch.setattr(type_check.Path, "is_file", lambda self: True)

    monkeypatch.setattr(sys, "argv", ["type_check.py"])
    assert type_check.main() == 2

    monkeypatch.setattr(sys, "argv", ["type_check.py", "--fallback-ok"])
    assert type_check.main() == 0
