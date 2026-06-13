from __future__ import annotations

import json
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app.observability import logger, wall_time
from app.portrait_response import exception_log_summary
from app.portrait_security import REDACTED, is_sensitive_field
from app.portrait_state import append_jsonl, state_path_fingerprint
from app.settings import (
    MAX_AUDIT_DEPTH,
    MAX_AUDIT_KEYS,
    MAX_AUDIT_LIST_ITEMS,
    MAX_AUDIT_PAYLOAD_BYTES,
    MAX_AUDIT_STRING_LENGTH,
    AUDIT_WRITE_FAIL_CLOSED,
    PORTRAIT_AUDIT_PATH,
    PORTRAIT_STORAGE_BACKEND,
)


AUDIT_HASH_ALGORITHM = "sha256-canonical-json-v1"
AUDIT_CHAIN_FIELDS = {"audit_chain_version", "audit_hash_algorithm", "audit_prev_hash", "audit_hash"}
CORE_AUDIT_FIELDS = {"event", "request_id", "tenant_id", "outcome", "created_at"}
RESERVED_AUDIT_FIELDS = CORE_AUDIT_FIELDS | {
    "audit_truncated",
    "audit_omitted_fields",
    "audit_omitted_field_names",
    "audit_omitted_items",
} | AUDIT_CHAIN_FIELDS
MAX_AUDIT_KEY_LENGTH = 128
MAX_OMITTED_FIELD_NAMES = 8


@dataclass
class AuditSanitizeStats:
    truncated: bool = False
    omitted_fields: int = 0
    omitted_items: int = 0


def _positive_int(value: int, *, default: int, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, number)


def _truncate_string(value: str, stats: AuditSanitizeStats, *, max_length: int | None = None) -> str:
    limit = _positive_int(max_length if max_length is not None else MAX_AUDIT_STRING_LENGTH, default=2048)
    if len(value) <= limit:
        return value
    stats.truncated = True
    suffix = "<truncated>"
    if limit <= len(suffix):
        return value[:limit]
    return value[: limit - len(suffix)] + suffix


def _sanitize_key(raw_key: Any, stats: AuditSanitizeStats) -> str:
    key = str(raw_key).strip() or "<empty_key>"
    return _truncate_string(key, stats, max_length=MAX_AUDIT_KEY_LENGTH)


def _unique_key(key: str, output: dict[str, Any], stats: AuditSanitizeStats) -> str:
    if key not in output:
        return key
    stats.truncated = True
    index = 2
    while f"{key}_{index}" in output:
        index += 1
    return f"{key}_{index}"


def sanitize_audit_value(
    value: Any,
    key: str = "",
    *,
    depth: int = 0,
    stats: AuditSanitizeStats | None = None,
    remaining_keys: list[int] | None = None,
    seen: set[int] | None = None,
) -> Any:
    stats = stats or AuditSanitizeStats()
    remaining_keys = remaining_keys or [_positive_int(MAX_AUDIT_KEYS, default=128)]
    seen = seen or set()

    if key and is_sensitive_field(key):
        return REDACTED

    max_depth = _positive_int(MAX_AUDIT_DEPTH, default=6)
    if depth >= max_depth:
        stats.truncated = True
        return "<max-depth>"

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            text = str(value)
        except ValueError:
            stats.truncated = True
            return "<int>"
        if len(text) > _positive_int(MAX_AUDIT_STRING_LENGTH, default=2048):
            stats.truncated = True
            return f"<int:{len(text)} digits>"
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        stats.truncated = True
        return "<non-finite-number>"
    if isinstance(value, str):
        return _truncate_string(value, stats)
    if isinstance(value, (bytes, bytearray, memoryview)):
        stats.truncated = True
        return f"<{type(value).__name__}:{len(value)} bytes>"

    if isinstance(value, dict):
        object_id = id(value)
        if object_id in seen:
            stats.truncated = True
            return "<cycle>"
        seen.add(object_id)
        try:
            output: dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                if remaining_keys[0] <= 0:
                    stats.truncated = True
                    stats.omitted_fields += 1
                    continue
                remaining_keys[0] -= 1
                raw_key_text = str(raw_key)
                item_key = _unique_key(_sanitize_key(raw_key, stats), output, stats)
                value_key = raw_key_text if is_sensitive_field(raw_key_text) else item_key
                output[item_key] = sanitize_audit_value(
                    raw_value,
                    value_key,
                    depth=depth + 1,
                    stats=stats,
                    remaining_keys=remaining_keys,
                    seen=seen,
                )
            return output
        finally:
            seen.remove(object_id)

    if isinstance(value, (list, tuple, set)):
        object_id = id(value)
        if object_id in seen:
            stats.truncated = True
            return "<cycle>"
        seen.add(object_id)
        try:
            output: list[Any] = []
            max_items = _positive_int(MAX_AUDIT_LIST_ITEMS, default=64)
            for index, item in enumerate(value):
                if index >= max_items:
                    stats.truncated = True
                    stats.omitted_items += 1
                    break
                output.append(
                    sanitize_audit_value(
                        item,
                        depth=depth + 1,
                        stats=stats,
                        remaining_keys=remaining_keys,
                        seen=seen,
                    )
                )
            return output
        finally:
            seen.remove(object_id)

    if hasattr(value, "isoformat"):
        try:
            return _truncate_string(str(value.isoformat()), stats)
        except Exception:
            pass

    stats.truncated = True
    return f"<{type(value).__name__}>"


def _json_size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def canonical_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "audit_hash"}


def audit_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        canonical_audit_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seal_audit_payload(payload: dict[str, Any], previous_hash: str | None) -> dict[str, Any]:
    sealed = dict(payload)
    sealed["audit_chain_version"] = 1
    sealed["audit_hash_algorithm"] = AUDIT_HASH_ALGORITHM
    sealed["audit_prev_hash"] = previous_hash
    sealed["audit_hash"] = audit_payload_hash(sealed)
    return sealed


def last_audit_hash(path: Any) -> str | None:
    if not path.exists():
        return None
    last_line = ""
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                last_line = line
    if not last_line:
        return None
    payload = json.loads(last_line)
    if not isinstance(payload, dict):
        raise ValueError("last audit record is not a JSON object")
    current_hash = payload.get("audit_hash")
    if isinstance(current_hash, str) and current_hash:
        return current_hash
    return audit_payload_hash(payload)


def audit_chain_previous_hash() -> str | None:
    try:
        return last_audit_hash(PORTRAIT_AUDIT_PATH)
    except Exception as exc:
        logger.warning(
            "failed to read audit chain head: path_hash=%s error=%s",
            state_path_fingerprint(PORTRAIT_AUDIT_PATH),
            exception_log_summary(exc),
        )
        if AUDIT_WRITE_FAIL_CLOSED:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="audit chain unavailable",
            ) from exc
        return None


def _attach_audit_stats(payload: dict[str, Any], stats: AuditSanitizeStats, omitted_names: list[str] | None = None) -> None:
    if not stats.truncated and not stats.omitted_fields and not stats.omitted_items:
        return
    payload["audit_truncated"] = True
    if stats.omitted_fields:
        payload["audit_omitted_fields"] = stats.omitted_fields
    if stats.omitted_items:
        payload["audit_omitted_items"] = stats.omitted_items
    if omitted_names:
        payload["audit_omitted_field_names"] = omitted_names[:MAX_OMITTED_FIELD_NAMES]


def _fit_payload_to_budget(payload: dict[str, Any], stats: AuditSanitizeStats) -> dict[str, Any]:
    max_bytes = _positive_int(MAX_AUDIT_PAYLOAD_BYTES, default=32768, minimum=512)
    if _json_size(payload) <= max_bytes:
        return payload

    bounded = dict(payload)
    omitted_names: list[str] = []
    custom_keys = [key for key in bounded if key not in RESERVED_AUDIT_FIELDS]
    custom_keys = [key for key in custom_keys if key not in CORE_AUDIT_FIELDS]
    for key in sorted(custom_keys, key=lambda item: _json_size(bounded[item]), reverse=True):
        bounded.pop(key, None)
        stats.truncated = True
        stats.omitted_fields += 1
        if len(omitted_names) < MAX_OMITTED_FIELD_NAMES:
            omitted_names.append(_truncate_string(key, stats, max_length=64))
        _attach_audit_stats(bounded, stats, omitted_names)
        if _json_size(bounded) <= max_bytes:
            return bounded

    for limit in (128, 64, 32):
        for key in ("event", "request_id", "tenant_id", "outcome"):
            if isinstance(bounded.get(key), str):
                bounded[key] = _truncate_string(str(bounded[key]), stats, max_length=limit)
        _attach_audit_stats(bounded, stats, omitted_names)
        if _json_size(bounded) <= max_bytes:
            return bounded

    bounded.pop("audit_omitted_field_names", None)
    _attach_audit_stats(bounded, stats)
    if _json_size(bounded) <= max_bytes:
        return bounded

    minimal: dict[str, Any] = {
        "event": _truncate_string(str(bounded.get("event", "")), stats, max_length=32),
        "request_id": _truncate_string(str(bounded.get("request_id", "")), stats, max_length=32),
        "tenant_id": _truncate_string(str(bounded.get("tenant_id", "")), stats, max_length=32),
        "outcome": _truncate_string(str(bounded.get("outcome", "")), stats, max_length=32),
        "created_at": bounded.get("created_at"),
        "audit_truncated": True,
    }
    if stats.omitted_fields:
        minimal["audit_omitted_fields"] = stats.omitted_fields
    if stats.omitted_items:
        minimal["audit_omitted_items"] = stats.omitted_items
    return minimal


def build_audit_payload(event: str, *, request_id: str, tenant_id: str, outcome: str, fields: dict[str, Any]) -> dict[str, Any]:
    stats = AuditSanitizeStats()
    payload: dict[str, Any] = {
        "event": _truncate_string(str(event), stats),
        "request_id": _truncate_string(str(request_id), stats),
        "tenant_id": _truncate_string(str(tenant_id), stats),
        "outcome": _truncate_string(str(outcome), stats),
        "created_at": wall_time(),
    }
    remaining_keys = [_positive_int(MAX_AUDIT_KEYS, default=128)]
    for raw_key, raw_value in fields.items():
        if remaining_keys[0] <= 0:
            stats.truncated = True
            stats.omitted_fields += 1
            continue
        remaining_keys[0] -= 1
        raw_key_text = str(raw_key)
        key = _sanitize_key(raw_key, stats)
        if key in RESERVED_AUDIT_FIELDS:
            key = f"field_{key}"
            stats.truncated = True
        key = _unique_key(key, payload, stats)
        value_key = raw_key_text if is_sensitive_field(raw_key_text) else key
        payload[key] = sanitize_audit_value(raw_value, value_key, stats=stats, remaining_keys=remaining_keys)

    _attach_audit_stats(payload, stats)
    return _fit_payload_to_budget(payload, stats)


def audit_event(event: str, *, request_id: str, tenant_id: str, outcome: str = "success", **fields: Any) -> None:
    payload = build_audit_payload(event, request_id=request_id, tenant_id=tenant_id, outcome=outcome, fields=fields)
    payload = seal_audit_payload(payload, audit_chain_previous_hash())
    append_jsonl(PORTRAIT_AUDIT_PATH, payload, fail_closed=AUDIT_WRITE_FAIL_CLOSED)
    if PORTRAIT_STORAGE_BACKEND == "postgres":
        from app.portrait_postgres import insert_audit_event

        try:
            insert_audit_event(payload)
        except Exception as exc:
            logger.warning("postgres audit write failed: %s", exception_log_summary(exc))
            if AUDIT_WRITE_FAIL_CLOSED:
                raise


def verify_audit_chain(path: Path | None = None) -> dict[str, Any]:
    audit_path = path or PORTRAIT_AUDIT_PATH
    if not audit_path.exists():
        return {
            "ok": True,
            "path": str(audit_path),
            "record_count": 0,
            "head_hash": None,
            "errors": [],
        }

    errors: list[dict[str, Any]] = []
    previous_hash: str | None = None
    head_hash: str | None = None
    record_count = 0
    with audit_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                errors.append({"line": line_number, "reason": "invalid_json"})
                continue
            if not isinstance(payload, dict):
                errors.append({"line": line_number, "reason": "record_not_object"})
                continue
            record_count += 1
            expected_prev = payload.get("audit_prev_hash")
            if expected_prev != previous_hash:
                errors.append({"line": line_number, "reason": "prev_hash_mismatch"})
            expected_hash = audit_payload_hash(payload)
            actual_hash = payload.get("audit_hash")
            if actual_hash != expected_hash:
                errors.append({"line": line_number, "reason": "audit_hash_mismatch"})
            previous_hash = actual_hash if isinstance(actual_hash, str) and actual_hash else expected_hash
            head_hash = previous_hash
    return {
        "ok": not errors,
        "path": str(audit_path),
        "record_count": record_count,
        "head_hash": head_hash,
        "error_count": len(errors),
        "errors": errors[:20],
    }
