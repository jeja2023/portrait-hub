from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPECTED_REQUIREMENTS = {
    "MOD-P0-01": "P0-01",
    "OPS-P0-02": "P0-02",
    "PERF-P0-03": "P0-03",
    "SCH-P1-01": "P1-01",
    "DEP-P1-02": "P1-02",
    "MOD-P1-03": "P1-03",
    "REG-P1-04": "P1-04",
    "DATA-P1-05": "P1-05",
    "CUS-P2-01": "P2-01",
    "SLA-P2-02": "P2-02",
    "SDK-P2-03": "P2-03",
    "VID-P2-04": "P2-04",
    "COM-P2-05": "P2-05",
    "TPL-P3-01": "P3-01",
}
STATUS_SEQUENCE = ["draft", "reviewed", "ready", "in_progress", "verification", "accepted", "released", "measured", "closed"]
ALLOWED_STATUSES = {*STATUS_SEQUENCE, "blocked", "cancelled"}
RELEASE_STATUSES = {"accepted", "released", "measured", "closed"}
REQUIRED_CARD_FIELDS = {
    "id",
    "module",
    "title",
    "priority",
    "target_version",
    "owner_role",
    "approver_roles",
    "status",
    "status_history",
    "product_context",
    "scope",
    "current_state_mapping",
    "behavior",
    "rules",
    "interfaces",
    "data",
    "security_compliance",
    "non_functional",
    "delivery",
    "acceptance",
    "implementation_state",
    "implementation_artifacts",
    "blockers",
    "approval_records",
}
REQUIRED_SCOPE_FIELDS = {"in_scope", "out_of_scope", "dependencies", "assumptions", "known_limitations"}
REQUIRED_ACCEPTANCE_FIELDS = {"criteria", "thresholds", "test_ids", "evidence_paths", "observation_window"}


def _load_matrix(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"traceability matrix cannot be read: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ValueError("traceability matrix root must be an object")
    return payload


def _nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC) <= datetime.now(UTC)


def _validate_repo_path(root: Path, value: str) -> str | None:
    relative = Path(value.split("::", maxsplit=1)[0])
    if relative.is_absolute():
        return f"repository evidence path must be relative: {value}"
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return f"repository evidence path escapes root: {value}"
    if not candidate.is_file():
        return f"repository evidence path does not exist: {value}"
    return None


def validate_traceability(
    root: Path,
    matrix_path: Path,
    *,
    release: bool = False,
) -> dict[str, Any]:
    try:
        payload = _load_matrix(matrix_path)
    except ValueError as exc:
        return {"ok": False, "release_ready": False, "errors": [str(exc)], "release_blockers": []}
    errors: list[str] = []
    release_blockers: list[dict[str, Any]] = []
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    cards = payload.get("requirements")
    if not isinstance(cards, list):
        return {"ok": False, "release_ready": False, "errors": [*errors, "requirements must be a list"], "release_blockers": []}
    by_id = {str(card.get("id")): card for card in cards if isinstance(card, dict)}
    actual_ids = set(by_id)
    if actual_ids != set(EXPECTED_REQUIREMENTS):
        errors.append(
            "requirement IDs must exactly match the commercial plan; "
            f"missing={sorted(set(EXPECTED_REQUIREMENTS) - actual_ids)}, extra={sorted(actual_ids - set(EXPECTED_REQUIREMENTS))}"
        )
    for requirement_id in sorted(set(EXPECTED_REQUIREMENTS) & actual_ids):
        card = by_id[requirement_id]
        missing = sorted(field for field in REQUIRED_CARD_FIELDS if field not in card)
        if missing:
            errors.append(f"{requirement_id}: missing fields: {', '.join(missing)}")
            continue
        if card.get("module") != EXPECTED_REQUIREMENTS[requirement_id]:
            errors.append(f"{requirement_id}: module must be {EXPECTED_REQUIREMENTS[requirement_id]}")
        for field in REQUIRED_CARD_FIELDS - {"blockers", "approval_records"}:
            if not _nonempty(card.get(field)):
                errors.append(f"{requirement_id}: {field} must not be empty")
        scope = card.get("scope")
        if not isinstance(scope, dict) or set(scope) < REQUIRED_SCOPE_FIELDS:
            errors.append(f"{requirement_id}: scope must contain {sorted(REQUIRED_SCOPE_FIELDS)}")
        acceptance = card.get("acceptance")
        if not isinstance(acceptance, dict) or set(acceptance) < REQUIRED_ACCEPTANCE_FIELDS:
            errors.append(f"{requirement_id}: acceptance must contain {sorted(REQUIRED_ACCEPTANCE_FIELDS)}")
            acceptance = {}
        status = str(card.get("status") or "")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{requirement_id}: unsupported status {status}")
        history = card.get("status_history")
        if not isinstance(history, list) or not history:
            errors.append(f"{requirement_id}: status_history must not be empty")
        else:
            for event in history:
                if not isinstance(event, dict) or not all(
                    _nonempty(event.get(key)) for key in ("status", "actor", "at", "reason", "evidence")
                ):
                    errors.append(f"{requirement_id}: every status event requires status, actor, at, reason and evidence")
                    continue
                if event.get("status") not in ALLOWED_STATUSES or not _valid_timestamp(event.get("at")):
                    errors.append(f"{requirement_id}: status event is invalid")
                evidence = event.get("evidence")
                if not isinstance(evidence, list):
                    errors.append(f"{requirement_id}: status event evidence must be a list")
                else:
                    for value in evidence:
                        path_error = _validate_repo_path(root, str(value))
                        if path_error:
                            errors.append(f"{requirement_id}: {path_error}")
            if isinstance(history[-1], dict) and history[-1].get("status") != status:
                errors.append(f"{requirement_id}: final status event must match current status")
        blockers = card.get("blockers")
        if status == "blocked" and (not isinstance(blockers, list) or not blockers):
            errors.append(f"{requirement_id}: blocked status requires blockers")
        if status == "blocked" and card.get("resume_status") not in ALLOWED_STATUSES - {"blocked", "cancelled"}:
            errors.append(f"{requirement_id}: blocked status requires a valid resume_status")
        for field in ("implementation_artifacts",):
            values = card.get(field)
            if not isinstance(values, list):
                errors.append(f"{requirement_id}: {field} must be a list")
                continue
            for value in values:
                path_error = _validate_repo_path(root, str(value))
                if path_error:
                    errors.append(f"{requirement_id}: {path_error}")
        for field in ("test_ids", "evidence_paths"):
            values = acceptance.get(field)
            if not isinstance(values, list) or not values:
                errors.append(f"{requirement_id}: acceptance.{field} must not be empty")
                continue
            for value in values:
                path_error = _validate_repo_path(root, str(value))
                if path_error:
                    errors.append(f"{requirement_id}: {path_error}")
        approval_records = card.get("approval_records")
        if not isinstance(approval_records, list):
            errors.append(f"{requirement_id}: approval_records must be a list")
            approval_records = []
        if status in RELEASE_STATUSES:
            approved_roles = {
                str(record.get("role"))
                for record in approval_records
                if isinstance(record, dict)
                and record.get("decision") == "approved"
                and _valid_timestamp(record.get("approved_at"))
                and _nonempty(record.get("approver"))
            }
            missing_roles = sorted(set(card.get("approver_roles") or []) - approved_roles)
            if missing_roles:
                errors.append(f"{requirement_id}: accepted+ status lacks approvals from {missing_roles}")
        if status not in RELEASE_STATUSES:
            release_blockers.append(
                {
                    "requirement_id": requirement_id,
                    "status": status,
                    "blockers": blockers if isinstance(blockers, list) else [],
                }
            )
    structurally_valid = not errors
    release_ready = structurally_valid and not release_blockers
    return {
        "ok": structurally_valid and (release_ready if release else True),
        "structurally_valid": structurally_valid,
        "release_ready": release_ready,
        "decision": "release" if release_ready else "block",
        "requirement_count": len(cards),
        "errors": errors,
        "release_blockers": release_blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate commercial upgrade requirement cards and release status.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--matrix", type=Path, default=Path("docs/requirements/COMMERCIAL_REQUIREMENTS.json"))
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    matrix_path = args.matrix if args.matrix.is_absolute() else root / args.matrix
    result = validate_traceability(root, matrix_path, release=args.release)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
