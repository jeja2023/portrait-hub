"""确保运维工具报告中不包含敏感泄密信息的辅助函数。"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

REDACTED = "<redacted>"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    exact = {
        "authorization",
        "credential",
        "credentials",
        "jwt",
        "password",
        "secret",
        "stream_url",
        "token",
        "vector",
        "vectors",
        "x-api-key",
    }
    if lowered in exact:
        return True
    if lowered == "embedding":
        return True
    if lowered.startswith("embedding_") and lowered != "embedding_dim":
        return True
    if lowered.endswith("_embedding") or lowered.endswith("_vector"):
        return True
    return any(
        marker in lowered
        for marker in (
            "access_key",
            "api_key",
            "authorization",
            "ciphertext",
            "credential",
            "password",
            "private_key",
            "secret",
            "stream_url",
            "token",
        )
    )


def redact_text(value: str) -> str:
    redacted = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,'\"}]+", rf"\1{REDACTED}", value)
    redacted = re.sub(
        r'(?i)(["\']?(?:api[_-]?key|x-api-key|access[_-]?key|token|password|secret|credential)["\']?\s*:\s*["\']?)[^,\s"\'}]+',
        rf"\1{REDACTED}",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|x-api-key|access[_-]?key|token|password|secret|credential)\s*[:=]\s*[^\s,'\"}]+",
        lambda match: f"{match.group(1)}={REDACTED}",
        redacted,
    )
    return redacted


def redact_for_report(value: Any, key: str = "") -> Any:
    if key and is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {item_key: redact_for_report(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_for_report(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return deepcopy(value)


def path_contains_sensitive_key(path: str) -> bool:
    parts = [part.strip("[]") for part in path.replace("[", ".[").replace("$", "").split(".")]
    return any(part and not part.isdigit() and is_sensitive_key(part) for part in parts)


def redact_for_path(value: Any, path: str) -> Any:
    if path_contains_sensitive_key(path):
        return REDACTED
    return redact_for_report(value)


def safe_report_repr(value: Any, path: str = "") -> str:
    return repr(redact_for_path(value, path))
