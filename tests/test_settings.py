from __future__ import annotations

import logging

import pytest

from app import settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("NO", False),
        ("off", False),
    ],
)
def test_parse_bool_env_accepts_explicit_boolean_values(monkeypatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("TEST_BOOLEAN_SETTING", raw)

    assert settings.parse_bool_env("TEST_BOOLEAN_SETTING", not expected) is expected


def test_invalid_typed_environment_values_warn_and_are_summarized(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "_INVALID_ENV_VALUES", {})
    monkeypatch.setenv("TEST_INTEGER_SETTING", "10MB")
    monkeypatch.setenv("TEST_FLOAT_SETTING", "nan")
    monkeypatch.setenv("TEST_BOOLEAN_SETTING", "ture")

    with caplog.at_level(logging.WARNING, logger="app.settings"):
        assert settings.parse_int_env("TEST_INTEGER_SETTING", 10) == 10
        assert settings.parse_float_env("TEST_FLOAT_SETTING", 1.5) == 1.5
        assert settings.parse_bool_env("TEST_BOOLEAN_SETTING", True) is True
        settings.parse_int_env("TEST_INTEGER_SETTING", 10)

    assert settings.invalid_environment_variables() == {
        "TEST_BOOLEAN_SETTING": "a boolean (1/0, true/false, yes/no, or on/off)",
        "TEST_FLOAT_SETTING": "a finite number",
        "TEST_INTEGER_SETTING": "an integer",
    }
    assert caplog.text.count("TEST_INTEGER_SETTING") == 1
    assert "10MB" not in caplog.text
    assert "ture" not in caplog.text
