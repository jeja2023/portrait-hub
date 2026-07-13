import json
from collections import deque
from typing import Any

from app.observability import wall_time
from app.settings import ROLLOUT_AUDIT_PATH

MAX_ROLLOUT_AUDIT_LIMIT = 500
ROLLOUT_AUDIT_FIELDS = {
    "time",
    "event",
    "alias",
    "old_target",
    "new_target",
    "rollout",
    "total_weight",
    "dry_run",
    "config_loaded",
    "would_write",
    "written",
}
ROLLOUT_TARGET_FIELDS = {"target", "weight", "status"}


def write_rollout_audit(event: str, payload: dict[str, Any]) -> None:
    record = {
        "time": wall_time(),
        "event": event,
        **payload,
    }
    ROLLOUT_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ROLLOUT_AUDIT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def normalize_rollout_target(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {key: raw[key] for key in ROLLOUT_TARGET_FIELDS if key in raw}


def public_rollout_audit_record(raw: dict[str, Any]) -> dict[str, Any]:
    record = {key: raw[key] for key in ROLLOUT_AUDIT_FIELDS if key in raw}
    rollout = record.get("rollout")
    if isinstance(rollout, list):
        record["rollout"] = [item for item in (normalize_rollout_target(value) for value in rollout) if item]
    return record


def read_rollout_audit(limit: int = 100) -> tuple[list[dict[str, Any]], int]:
    bounded_limit = max(1, min(int(limit), MAX_ROLLOUT_AUDIT_LIMIT))
    records: deque[dict[str, Any]] = deque(maxlen=bounded_limit)
    malformed_count = 0
    if not ROLLOUT_AUDIT_PATH.is_file():
        return [], 0

    with ROLLOUT_AUDIT_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            raw_line = line.strip()
            if not raw_line:
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                malformed_count += 1
                continue
            if not isinstance(payload, dict):
                malformed_count += 1
                continue
            records.append(public_rollout_audit_record(payload))
    return list(reversed(records)), malformed_count


__all__ = [
    "MAX_ROLLOUT_AUDIT_LIMIT",
    "public_rollout_audit_record",
    "read_rollout_audit",
    "write_rollout_audit",
]