from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from app.portrait_access import find_project, list_applications
from app.portrait_call_logs import list_call_logs
from app.portrait_control_state import ControlStateBackend, ControlStateLock
from app.portrait_metering import aggregate_usage
from app.portrait_projects import validate_project_id
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import PORTRAIT_COMMERCIAL_STATE_PATH

COMMERCIAL_STATUSES = {"trial", "active", "grace", "suspended", "offboarding", "closed"}
COMMERCIAL_STATUS_TRANSITIONS = {
    "trial": {"active", "grace", "suspended", "offboarding"},
    "active": {"grace", "suspended", "offboarding"},
    "grace": {"active", "suspended", "offboarding"},
    "suspended": {"active", "grace", "offboarding"},
    "offboarding": {"active", "closed"},
    "closed": set(),
}
INCIDENT_STATUSES = {"investigating", "identified", "monitoring", "resolved", "closed"}
INCIDENT_SEVERITIES = {"sev1", "sev2", "sev3", "sev4"}
RIGHTS_REQUEST_TYPES = {"access", "correction", "deletion", "withdrawal", "restriction", "export"}
RIGHTS_REQUEST_STATUSES = {"received", "identity_pending", "verified", "in_progress", "completed", "rejected"}
RIGHTS_REQUEST_TRANSITIONS = {
    "received": {"identity_pending", "rejected"},
    "identity_pending": {"verified", "rejected"},
    "verified": {"in_progress", "rejected"},
    "in_progress": {"completed", "rejected"},
    "completed": set(),
    "rejected": set(),
}
RIGHTS_EXECUTION_BACKENDS = {"postgresql", "vector_store", "object_storage", "cache", "exports", "backups"}
SUPPORT_CASE_STATUSES = {"open", "acknowledged", "investigating", "waiting_customer", "resolved", "closed"}
ENTITLEMENT_CHANGE_TYPES = {
    "new",
    "renewal",
    "upgrade",
    "downgrade",
    "temporary_expansion",
    "emergency",
}
ENTITLEMENT_ACTIONS = {"cancel", "revoke", "rollback"}
COMPLIANCE_CONTROL_IDS = {f"COM-{index:03d}" for index in range(1, 13)}
COMPLIANCE_REQUIRED_CONTROL_DATA = {
    "COM-001": {"responsible_contact", "necessity_assessment", "recipient_categories"},
    "COM-002": {"notice_version", "consent_scope", "obtained_at", "source", "proof_ref", "withdrawal_status"},
    "COM-003": {"minor_policy", "guardian_consent_status", "guardian_verification_status"},
    "COM-004": {"alternative_available", "alternative_process"},
    "COM-005": {"assessment_ref", "assessment_version", "review_due_at"},
    "COM-006": {"allowed_regions", "transfer_policy", "export_requires_approval"},
    "COM-007": {"backend_retention", "deletion_workflow", "backup_expiry_policy"},
    "COM-008": {"identity_verification_policy", "due_days", "fulfillment_backends"},
    "COM-009": {"collection_area", "signage_ref", "controller", "prohibited_areas"},
    "COM-010": {"filing_threshold", "current_count", "warning_ratio", "filing_status"},
    "COM-011": {"human_review_enabled", "appeal_process", "decision_use"},
    "COM-012": {"incident_process", "notification_decision_owner", "response_plan_ref"},
}
_COMPLIANCE_BACKENDS = {"postgresql", "vector_store", "object_storage", "cache", "exports", "backups"}

_COLLECTIONS = (
    "commercial_profiles",
    "entitlements",
    "sla_definitions",
    "sla_reports",
    "incidents",
    "compliance_records",
    "rights_requests",
    "evidence_packages",
    "industry_templates",
    "template_applications",
    "support_cases",
)
_LOCK = ControlStateLock()
_T = TypeVar("_T")
_METERING_EXCLUDED_PREFIXES = ("/v1/access/", "/v1/admin/", "/v1/auth/", "/health", "/ready", "/metrics")
_TEMPLATE_ACCEPTANCE_ROOT = Path(__file__).resolve().parents[1]


def _empty_state() -> dict[str, Any]:
    return {"revision": 0, **{name: [] for name in _COLLECTIONS}}


_STATE = _empty_state()


def now_seconds() -> float:
    return time.time()


def utc_date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _scope(record: dict[str, Any], tenant_id: str, project_id: str | None = None) -> bool:
    if record.get("tenant_id") != tenant_id:
        return False
    return project_id is None or record.get("project_id") == project_id


def _copy(value: _T) -> _T:
    return copy.deepcopy(value)


def _validate_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        handle_state_read_error("commercial state root must be an object")
        return _empty_state()
    state = _empty_state()
    try:
        state["revision"] = max(0, int(payload.get("revision", 0)))
    except (TypeError, ValueError):
        handle_state_read_error("commercial state revision is invalid")
    for name in _COLLECTIONS:
        value = payload.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            handle_state_read_error(f"commercial state collection is invalid: {name}")
            continue
        state[name] = _copy(value)
    return state


_BACKEND = ControlStateBackend("commercial", _STATE, _LOCK.raw, _empty_state, _validate_state)
_LOCK.bind(_BACKEND)


def load_commercial_state() -> None:
    with _LOCK:
        if _BACKEND.postgres_enabled():
            template_count = len(_STATE["industry_templates"])
            _ensure_builtin_templates()
            if _BACKEND.revision == 0 or len(_STATE["industry_templates"]) != template_count:
                _save(increment=False)
            return
        _STATE.clear()
        _STATE.update(_validate_state(read_json_state(PORTRAIT_COMMERCIAL_STATE_PATH, _empty_state())))
        _ensure_builtin_templates()


def reset_commercial_state(*, persist: bool = False) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(_empty_state())
        _ensure_builtin_templates()
        if persist:
            _save()
        elif _BACKEND.postgres_enabled():
            _BACKEND.invalidate()


def commercial_state_payload() -> dict[str, Any]:
    with _LOCK:
        return _copy(_STATE)


def restore_commercial_state(payload: dict[str, Any]) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(_validate_state(payload))
        _save(increment=False)


def _save(*, increment: bool = True) -> None:
    if increment:
        _STATE["revision"] = int(_STATE.get("revision", 0)) + 1
    if _BACKEND.postgres_enabled():
        _BACKEND.save(actor="commercial-control-plane")
    else:
        write_json_state(PORTRAIT_COMMERCIAL_STATE_PATH, _STATE)


def _ensure_project(tenant_id: str, project_id: str) -> str:
    normalized = validate_project_id(project_id)
    if find_project(tenant_id, normalized) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project does not exist")
    return normalized


def _profile_record(tenant_id: str, project_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in _STATE["commercial_profiles"] if _scope(item, tenant_id, project_id)),
        None,
    )


def _entitlement_record(tenant_id: str, project_id: str, entitlement_id: str | None) -> dict[str, Any] | None:
    if not entitlement_id:
        return None
    return next(
        (
            item
            for item in _STATE["entitlements"]
            if _scope(item, tenant_id, project_id) and item.get("entitlement_id") == entitlement_id
        ),
        None,
    )


def _append_entitlement_history(
    entitlement: dict[str, Any],
    next_status: str,
    *,
    actor: str,
    reason: str,
    timestamp: float,
) -> None:
    current_status = str(entitlement.get("status") or "pending")
    if current_status == next_status:
        return
    entitlement.setdefault("status_history", []).append(
        {
            "from": current_status,
            "to": next_status,
            "at": timestamp,
            "actor": actor[:256],
            "reason": reason[:1000],
        }
    )
    entitlement["status"] = next_status
    entitlement["updated_at"] = timestamp
    entitlement["updated_by"] = actor[:256]


def _rollback_target_is_usable(target: dict[str, Any] | None, timestamp: float) -> bool:
    if target is None or target.get("status") in {"revoked", "expired", "pending"}:
        return False
    if timestamp < float(target.get("starts_at") or 0):
        return False
    expires_at = target.get("expires_at")
    if expires_at is None:
        return True
    grace_until = float(expires_at) + max(0, int(target.get("grace_period_seconds") or 0))
    return timestamp <= grace_until


def _restore_entitlement_rollback_target(
    tenant_id: str,
    project_id: str,
    entitlement: dict[str, Any],
    profile: dict[str, Any],
    *,
    actor: str,
    reason: str,
    timestamp: float,
) -> dict[str, Any] | None:
    target = _entitlement_record(tenant_id, project_id, str(entitlement.get("rollback_target_id") or ""))
    if not _rollback_target_is_usable(target, timestamp):
        profile["current_entitlement_id"] = None
        return None
    assert target is not None
    _append_entitlement_history(target, "active", actor=actor, reason=reason, timestamp=timestamp)
    profile["current_entitlement_id"] = target["entitlement_id"]
    return target


def _reconcile_entitlements(tenant_id: str, project_id: str, *, timestamp: float | None = None) -> bool:
    current_time = now_seconds() if timestamp is None else timestamp
    profile = _profile_record(tenant_id, project_id)
    if profile is None:
        return False
    changed = False
    current = _entitlement_record(tenant_id, project_id, profile.get("current_entitlement_id"))
    if current is not None and current.get("status") == "active" and current.get("expires_at") is not None:
        expires_at = float(current["expires_at"])
        grace_until = expires_at + max(0, int(current.get("grace_period_seconds") or 0))
        temporary_expired = current.get("change_type") == "temporary_expansion" and current_time > expires_at
        fully_expired = current_time > grace_until
        if temporary_expired or fully_expired:
            _append_entitlement_history(
                current,
                "expired",
                actor="entitlement-scheduler",
                reason="temporary entitlement ended" if temporary_expired else "entitlement validity ended",
                timestamp=current_time,
            )
            _restore_entitlement_rollback_target(
                tenant_id,
                project_id,
                current,
                profile,
                actor="entitlement-scheduler",
                reason="automatic rollback after entitlement expiry",
                timestamp=current_time,
            )
            changed = True

    due: list[dict[str, Any]] = []
    for item in _STATE["entitlements"]:
        if not _scope(item, tenant_id, project_id) or item.get("status") != "pending":
            continue
        if float(item.get("starts_at") or 0) > current_time:
            continue
        expires_at = item.get("expires_at")
        pending_grace_until = (
            float(expires_at) + max(0, int(item.get("grace_period_seconds") or 0))
            if expires_at is not None
            else None
        )
        if pending_grace_until is not None and current_time > pending_grace_until:
            _append_entitlement_history(
                item,
                "expired",
                actor="entitlement-scheduler",
                reason="scheduled entitlement expired before reconciliation",
                timestamp=current_time,
            )
            changed = True
            continue
        due.append(item)

    if due:
        winner = max(due, key=lambda item: (float(item.get("starts_at") or 0), int(item.get("version") or 0)))
        current = _entitlement_record(tenant_id, project_id, profile.get("current_entitlement_id"))
        if current is not None and current is not winner and current.get("status") == "active":
            _append_entitlement_history(
                current,
                "superseded",
                actor="entitlement-scheduler",
                reason=f"scheduled entitlement {winner['entitlement_id']} activated",
                timestamp=current_time,
            )
        for item in due:
            _append_entitlement_history(
                item,
                "active" if item is winner else "superseded",
                actor="entitlement-scheduler",
                reason="scheduled entitlement reached its effective time",
                timestamp=current_time,
            )
        profile["current_entitlement_id"] = winner["entitlement_id"]
        profile["updated_at"] = current_time
        profile["updated_by"] = "entitlement-scheduler"
        profile["version"] = int(profile.get("version", 1)) + 1
        changed = True
    return changed


def _completed_execution_backends(evidence: Any) -> set[str]:
    if not isinstance(evidence, list):
        return set()
    return {
        str(item.get("backend"))
        for item in evidence
        if isinstance(item, dict)
        and item.get("status") in {"deleted", "expired", "restricted"}
        and len(str(item.get("evidence_sha256") or "")) == 64
    }


def _require_offboarding_closure(tenant_id: str, project_id: str) -> None:
    active_application_ids = [
        str(item.get("app_id") or "")
        for item in list_applications(tenant_id, project_id)
        if item.get("status") == "active"
    ]
    deletion_evidence_ready = any(
        item.get("request_type") == "deletion"
        and item.get("status") == "completed"
        and RIGHTS_EXECUTION_BACKENDS.issubset(_completed_execution_backends(item.get("execution_evidence")))
        for item in _STATE["rights_requests"]
        if _scope(item, tenant_id, project_id)
    )
    blockers: list[dict[str, Any]] = []
    if active_application_ids:
        blockers.append({"code": "active_applications", "application_ids": sorted(active_application_ids)})
    if not deletion_evidence_ready:
        blockers.append(
            {
                "code": "deletion_evidence_required",
                "required_backends": sorted(RIGHTS_EXECUTION_BACKENDS),
            }
        )
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "offboarding_closure_blocked",
                "message": "offboarding closure prerequisites are incomplete",
                "blockers": blockers,
            },
        )


def _reconcile_commercial_profile_transition(
    tenant_id: str,
    project_id: str,
    *,
    timestamp: float | None = None,
) -> bool:
    profile = _profile_record(tenant_id, project_id)
    if profile is None:
        return False
    transition = profile.get("scheduled_transition")
    if not isinstance(transition, dict) or transition.get("status") not in {"pending", "blocked"}:
        return False
    current_time = now_seconds() if timestamp is None else timestamp
    if current_time < float(transition.get("effective_at") or 0):
        return False
    current_status = str(profile.get("commercial_status") or "trial")
    expected_status = str(transition.get("from_status") or "")
    next_status = str(transition.get("to_status") or current_status)
    if current_status != expected_status:
        transition["status"] = "conflicted"
        transition["blocked_reason"] = "commercial status changed before scheduled transition"
        transition["evaluated_at"] = current_time
        return True
    if next_status not in COMMERCIAL_STATUS_TRANSITIONS.get(current_status, set()) and next_status != current_status:
        transition["status"] = "conflicted"
        transition["blocked_reason"] = "scheduled commercial status transition is no longer allowed"
        transition["evaluated_at"] = current_time
        return True
    if current_status == "offboarding" and next_status == "closed":
        try:
            _require_offboarding_closure(tenant_id, project_id)
        except HTTPException as exc:
            transition["status"] = "blocked"
            transition["blocked_reason"] = _copy(exc.detail)
            transition["evaluated_at"] = current_time
            return True
    allowed = {
        "commercial_status",
        "delivery_tier",
        "environment",
        "timezone",
        "budget_limit",
        "budget_currency",
        "retention_policy_id",
        "notification_channels",
        "expires_at",
    }
    for key, value in dict(transition.get("updates") or {}).items():
        if key in allowed:
            profile[key] = _copy(value)
    profile["commercial_status"] = next_status
    profile["effective_at"] = float(transition["effective_at"])
    profile["status_reason"] = str(transition.get("reason") or "")[:1000]
    profile["approved_by"] = transition.get("approved_by")
    profile["updated_at"] = current_time
    profile["updated_by"] = str(transition.get("actor") or "commercial-scheduler")[:256]
    profile["version"] = int(profile.get("version") or 1) + 1
    profile.setdefault("status_history", []).append(
        {
            "from": current_status,
            "to": next_status,
            "effective_at": float(transition["effective_at"]),
            "executed_at": current_time,
            "actor": profile["updated_by"],
            "approved_by": transition.get("approved_by"),
            "reason": profile["status_reason"],
        }
    )
    completed = _copy(transition)
    completed["status"] = "completed"
    completed["executed_at"] = current_time
    profile["last_scheduled_transition"] = completed
    profile["scheduled_transition"] = None
    return True


def _default_profile(tenant_id: str, project_id: str) -> dict[str, Any]:
    timestamp = now_seconds()
    return {
        "profile_id": new_id("commercial"),
        "tenant_id": tenant_id,
        "project_id": project_id,
        "commercial_status": "trial",
        "delivery_tier": "platform_api",
        "environment": "development",
        "timezone": "Asia/Shanghai",
        "budget_limit": None,
        "budget_currency": "CNY",
        "retention_policy_id": None,
        "notification_channels": [],
        "current_entitlement_id": None,
        "scheduled_transition": None,
        "status_reason": "initial provisioning",
        "approved_by": None,
        "effective_at": timestamp,
        "expires_at": None,
        "version": 1,
        "created_at": timestamp,
        "created_by": "system",
        "updated_at": timestamp,
        "updated_by": "system",
        "status_history": [],
    }


def get_commercial_profile(tenant_id: str, project_id: str, *, create: bool = True) -> dict[str, Any]:
    with _LOCK:
        normalized = _ensure_project(tenant_id, project_id)
        record = _profile_record(tenant_id, normalized)
        if record is None and create:
            record = _default_profile(tenant_id, normalized)
            _STATE["commercial_profiles"].append(record)
            _save()
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="commercial profile does not exist")
        if _reconcile_commercial_profile_transition(tenant_id, normalized):
            _save()
        if _reconcile_entitlements(tenant_id, normalized):
            _save()
        result = _copy(record)
        entitlement_id = result.get("current_entitlement_id")
        result["entitlement"] = next(
            (_copy(item) for item in _STATE["entitlements"] if item.get("entitlement_id") == entitlement_id),
            None,
        )
        return result


def update_commercial_profile(
    tenant_id: str,
    project_id: str,
    updates: dict[str, Any],
    *,
    actor: str,
    approved_by: str | None,
    reason: str,
    expected_version: int | None = None,
    cancel_scheduled_transition: bool = False,
) -> dict[str, Any]:
    with _LOCK:
        normalized = _ensure_project(tenant_id, project_id)
        record = _profile_record(tenant_id, normalized)
        if record is None:
            record = _default_profile(tenant_id, normalized)
            _STATE["commercial_profiles"].append(record)
        if expected_version is not None and int(record.get("version", 1)) != expected_version:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="commercial profile version conflict")
        timestamp = now_seconds()
        if cancel_scheduled_transition:
            scheduled = record.get("scheduled_transition")
            if not isinstance(scheduled, dict) or scheduled.get("status") not in {"pending", "blocked"}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="commercial profile has no cancellable scheduled transition",
                )
            if not reason.strip() or not approved_by:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="scheduled transition cancellation requires reason and approver",
                )
            cancelled = _copy(scheduled)
            cancelled["status"] = "cancelled"
            cancelled["cancelled_at"] = timestamp
            cancelled["cancelled_by"] = actor[:256]
            cancelled["cancellation_reason"] = reason.strip()[:1000]
            record["last_scheduled_transition"] = cancelled
            record["scheduled_transition"] = None
            record["updated_at"] = timestamp
            record["updated_by"] = actor[:256]
            record["version"] = int(record.get("version", 1)) + 1
            _save()
            return get_commercial_profile(tenant_id, normalized)
        current_status = str(record.get("commercial_status") or "trial")
        next_status = str(updates.get("commercial_status") or current_status).strip().lower()
        if next_status not in COMMERCIAL_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported commercial status"
            )
        if next_status != current_status and next_status not in COMMERCIAL_STATUS_TRANSITIONS[current_status]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="commercial status transition is not allowed"
            )
        if next_status != current_status and (not reason.strip() or not approved_by):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="status transition requires reason and approver",
            )
        if current_status == "offboarding" and next_status == "closed":
            effective_at = float(updates.get("effective_at") or timestamp)
            if effective_at <= timestamp:
                _require_offboarding_closure(tenant_id, normalized)
        allowed = {
            "commercial_status",
            "delivery_tier",
            "environment",
            "timezone",
            "budget_limit",
            "budget_currency",
            "retention_policy_id",
            "notification_channels",
            "effective_at",
            "expires_at",
        }
        effective_at = float(updates.get("effective_at") or timestamp)
        if effective_at > timestamp:
            if not reason.strip() or not approved_by:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="scheduled transition requires reason and approver",
                )
            transition_updates = {key: _copy(value) for key, value in updates.items() if key in allowed}
            record["scheduled_transition"] = {
                "transition_id": new_id("commercial_transition"),
                "status": "pending",
                "from_status": current_status,
                "to_status": next_status,
                "updates": transition_updates,
                "effective_at": effective_at,
                "created_at": timestamp,
                "actor": actor[:256],
                "approved_by": approved_by[:256],
                "reason": reason.strip()[:1000],
                "expected_profile_version": int(record.get("version", 1)),
            }
            record["updated_at"] = timestamp
            record["updated_by"] = actor[:256]
            record["version"] = int(record.get("version", 1)) + 1
            _save()
            return get_commercial_profile(tenant_id, normalized)
        for key, value in updates.items():
            if key in allowed:
                record[key] = _copy(value)
        if next_status != current_status:
            record.setdefault("status_history", []).append(
                {
                    "from": current_status,
                    "to": next_status,
                    "effective_at": effective_at,
                    "executed_at": timestamp,
                    "actor": actor[:256],
                    "approved_by": approved_by,
                    "reason": reason.strip()[:1000],
                }
            )
        record["status_reason"] = reason.strip()[:1000]
        record["approved_by"] = approved_by
        record["updated_at"] = timestamp
        record["updated_by"] = actor[:256]
        record["version"] = int(record.get("version", 1)) + 1
        _save()
        return get_commercial_profile(tenant_id, normalized)


def create_entitlement(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    approved_by: str,
) -> dict[str, Any]:
    with _LOCK:
        normalized = _ensure_project(tenant_id, project_id)
        profile = get_commercial_profile(tenant_id, normalized)
        timestamp = now_seconds()
        profile_record = _profile_record(tenant_id, normalized)
        assert profile_record is not None
        current_entitlement_id = str(profile_record.get("current_entitlement_id") or "") or None
        expected_current = payload.get("expected_current_entitlement_id")
        if expected_current is not None and str(expected_current or "") != str(current_entitlement_id or ""):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "entitlement_current_version_conflict",
                    "message": "current entitlement changed after the proposal was prepared",
                    "current_entitlement_id": current_entitlement_id,
                },
            )
        starts_at = float(payload.get("starts_at") or timestamp)
        expires_at = float(payload["expires_at"]) if payload.get("expires_at") is not None else None
        if expires_at is not None and expires_at <= starts_at:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="entitlement expiry must be after start"
            )
        allowed_capabilities = sorted(
            {str(item).strip() for item in payload.get("allowed_capabilities", []) if str(item).strip()}
        )
        if not allowed_capabilities:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="entitlement requires capabilities"
            )
        change_type = str(payload.get("change_type") or ("new" if current_entitlement_id is None else "renewal"))
        if change_type not in ENTITLEMENT_CHANGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="unsupported entitlement change type",
            )
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="entitlement change requires a reason",
            )
        requested_rollback_target = payload.get("rollback_target_id")
        rollback_target_id = str(requested_rollback_target or current_entitlement_id or "") or None
        if rollback_target_id is not None:
            rollback_target = _entitlement_record(tenant_id, normalized, rollback_target_id)
            if rollback_target is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="entitlement rollback target does not exist in this project",
                )
        if change_type == "temporary_expansion" and (expires_at is None or rollback_target_id is None):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="temporary expansion requires expires_at and a rollback target",
            )
        previous = [item for item in _STATE["entitlements"] if _scope(item, tenant_id, normalized)]
        initial_status = "pending" if starts_at > timestamp else "active"
        record = {
            "entitlement_id": new_id("entitlement"),
            "tenant_id": tenant_id,
            "project_id": normalized,
            "definition_version": str(payload.get("definition_version") or "1.0"),
            "product_version": str(payload.get("product_version") or "1.0"),
            "delivery_tier": str(payload.get("delivery_tier") or profile.get("delivery_tier") or "platform_api"),
            "allowed_capabilities": allowed_capabilities,
            "allowed_models": sorted(
                {str(item).strip() for item in payload.get("allowed_models", []) if str(item).strip()}
            ),
            "project_limit": max(1, int(payload.get("project_limit") or 1)),
            "concurrency_limit": max(1, int(payload.get("concurrency_limit") or 1)),
            "stream_limit": max(0, int(payload.get("stream_limit") or 0)),
            "support_level": str(payload.get("support_level") or "standard"),
            "starts_at": starts_at,
            "expires_at": expires_at,
            "grace_period_seconds": max(0, int(payload.get("grace_period_seconds") or 0)),
            "change_type": change_type,
            "change_reason": reason[:1000],
            "rollback_target_id": rollback_target_id,
            "supersedes": current_entitlement_id,
            "status": initial_status,
            "status_history": [
                {
                    "from": None,
                    "to": initial_status,
                    "at": timestamp,
                    "actor": actor[:256],
                    "reason": reason[:1000],
                }
            ],
            "created_at": timestamp,
            "created_by": actor[:256],
            "approved_by": approved_by[:256],
            "version": len(previous) + 1,
            "record_version": 1,
        }
        if initial_status == "active":
            current = _entitlement_record(tenant_id, normalized, current_entitlement_id)
            if current is not None and current.get("status") == "active":
                _append_entitlement_history(
                    current,
                    "superseded",
                    actor=actor,
                    reason=f"entitlement {record['entitlement_id']} activated",
                    timestamp=timestamp,
                )
        _STATE["entitlements"].append(record)
        if initial_status == "active":
            profile_record["current_entitlement_id"] = record["entitlement_id"]
            profile_record["updated_at"] = timestamp
            profile_record["updated_by"] = actor[:256]
            profile_record["version"] = int(profile_record.get("version", 1)) + 1
        _save()
        return _copy(record)


def list_entitlements(
    tenant_id: str,
    project_id: str,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    with _LOCK:
        if _reconcile_commercial_profile_transition(tenant_id, project_id):
            _save()
        if _reconcile_entitlements(tenant_id, project_id):
            _save()
        rows = [_copy(item) for item in _STATE["entitlements"] if _scope(item, tenant_id, project_id)]
        ordered = sorted(
            rows,
            key=lambda item: (int(item.get("version", 0)), float(item.get("created_at", 0))),
            reverse=True,
        )
        return ordered if limit is None else ordered[:limit]


def change_entitlement_status(
    tenant_id: str,
    project_id: str,
    entitlement_id: str,
    action: str,
    *,
    actor: str,
    approved_by: str,
    reason: str,
    expected_version: int | None = None,
    expected_current_entitlement_id: str | None = None,
) -> dict[str, Any]:
    with _LOCK:
        normalized = _ensure_project(tenant_id, project_id)
        if _reconcile_commercial_profile_transition(tenant_id, normalized):
            _save()
        if _reconcile_entitlements(tenant_id, normalized):
            _save()
        normalized_action = action.strip().lower()
        if normalized_action not in ENTITLEMENT_ACTIONS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported entitlement action")
        normalized_reason = reason.strip()
        if not normalized_reason or not approved_by.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="entitlement action requires reason and approver",
            )
        record = _entitlement_record(tenant_id, normalized, entitlement_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entitlement does not exist")
        if expected_version is not None and int(record.get("record_version") or 1) != expected_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "entitlement_version_conflict", "message": "entitlement version conflict"},
            )
        profile = _profile_record(tenant_id, normalized)
        assert profile is not None
        current_id = str(profile.get("current_entitlement_id") or "") or None
        if (
            expected_current_entitlement_id is not None
            and str(expected_current_entitlement_id or "") != str(current_id or "")
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "entitlement_current_version_conflict",
                    "message": "current entitlement changed after the action was prepared",
                    "current_entitlement_id": current_id,
                },
            )
        current_time = now_seconds()
        if normalized_action == "cancel":
            if record.get("status") != "pending":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="only pending entitlements can be cancelled")
            _append_entitlement_history(
                record,
                "revoked",
                actor=actor,
                reason=normalized_reason,
                timestamp=current_time,
            )
        elif normalized_action == "revoke":
            if record.get("status") not in {"pending", "active"}:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="entitlement is not revocable")
            was_current = current_id == entitlement_id
            _append_entitlement_history(
                record,
                "revoked",
                actor=actor,
                reason=normalized_reason,
                timestamp=current_time,
            )
            if was_current:
                _restore_entitlement_rollback_target(
                    tenant_id,
                    normalized,
                    record,
                    profile,
                    actor=actor,
                    reason=f"rollback after revocation: {normalized_reason}",
                    timestamp=current_time,
                )
        else:
            if current_id != entitlement_id or record.get("status") != "active":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="only the current active entitlement can be rolled back",
                )
            target = _entitlement_record(tenant_id, normalized, str(record.get("rollback_target_id") or ""))
            if not _rollback_target_is_usable(target, current_time):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="entitlement rollback target is unavailable",
                )
            _append_entitlement_history(
                record,
                "superseded",
                actor=actor,
                reason=normalized_reason,
                timestamp=current_time,
            )
            _restore_entitlement_rollback_target(
                tenant_id,
                normalized,
                record,
                profile,
                actor=actor,
                reason=normalized_reason,
                timestamp=current_time,
            )
        record["action_approved_by"] = approved_by.strip()[:256]
        record["record_version"] = int(record.get("record_version") or 1) + 1
        profile["updated_at"] = current_time
        profile["updated_by"] = actor[:256]
        profile["version"] = int(profile.get("version") or 0) + 1
        _save()
        return {
            "entitlement": _copy(record),
            "commercial_profile": get_commercial_profile(tenant_id, normalized),
        }


def entitlement_runtime_status(entitlement: dict[str, Any], timestamp: float | None = None) -> str:
    now = now_seconds() if timestamp is None else timestamp
    starts_at = float(entitlement.get("starts_at") or 0)
    expires_at = entitlement.get("expires_at")
    if now < starts_at:
        return "pending"
    stored_status = str(entitlement.get("status") or "active")
    if stored_status in {"revoked", "superseded", "expired"}:
        return stored_status
    if expires_at is None or now <= float(expires_at):
        return "active"
    grace_until = float(expires_at) + max(0, int(entitlement.get("grace_period_seconds") or 0))
    return "grace" if now <= grace_until else "expired"


def require_entitlement_capability(
    tenant_id: str,
    project_id: str,
    capability: str,
    *,
    model_id: str | None = None,
) -> dict[str, Any] | None:
    from app import settings
    from app.portrait_commercial_license import require_license_capability

    if not settings.COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED:
        return None
    with _LOCK:
        normalized = _ensure_project(tenant_id, project_id)
        if _reconcile_entitlements(tenant_id, normalized):
            _save()
        profile = _profile_record(tenant_id, normalized)
        if profile is None or profile.get("commercial_status") not in {"active", "grace"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "commercial_profile_inactive", "message": "commercial profile is not active"},
            )
        entitlement_id = profile.get("current_entitlement_id")
        entitlement = next(
            (
                item
                for item in _STATE["entitlements"]
                if _scope(item, tenant_id, normalized) and item.get("entitlement_id") == entitlement_id
            ),
            None,
        )
        if entitlement is None or entitlement_runtime_status(entitlement) not in {"active", "grace"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "entitlement_inactive", "message": "project entitlement is not active"},
            )
        if capability not in set(entitlement.get("allowed_capabilities") or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "capability_not_entitled", "message": "capability is not entitled for this project"},
            )
        allowed_models = set(entitlement.get("allowed_models") or [])
        if model_id and allowed_models and model_id not in allowed_models:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "model_not_entitled", "message": "model is not entitled for this project"},
            )
        compliance = compliance_status(tenant_id, normalized)
        if not compliance["ready"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "compliance_gate_blocked",
                    "message": "required compliance controls are not approved",
                    "blocking_controls": compliance["blocking_controls"],
                },
            )
        license_entitlement = require_license_capability(tenant_id, normalized, capability, model_id=model_id)
        result = _copy(cast(dict[str, Any], entitlement))
        concurrency_limits = [int(result.get("concurrency_limit") or 1)]
        if license_entitlement is not None:
            concurrency_limits.append(int(license_entitlement.get("concurrency_limit") or 1))
        result["effective_concurrency_limit"] = min(concurrency_limits)
        return result


def require_entitlement_allocation(
    tenant_id: str,
    project_id: str,
    operation: str,
    *,
    current_count: int | None = None,
) -> dict[str, Any] | None:
    from app import settings
    from app.portrait_commercial_license import require_license_allocation

    normalized = validate_project_id(project_id)
    entitlement: dict[str, Any] | None = None
    if settings.COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED:
        with _LOCK:
            _ensure_project(tenant_id, normalized)
            if _reconcile_commercial_profile_transition(tenant_id, normalized):
                _save()
            if _reconcile_entitlements(tenant_id, normalized):
                _save()
            profile = _profile_record(tenant_id, normalized)
            if profile is None or profile.get("commercial_status") != "active":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "commercial_profile_allocation_restricted",
                        "message": "commercial profile must be active for new allocations",
                    },
                )
            entitlement_id = profile.get("current_entitlement_id")
            entitlement = next(
                (
                    item
                    for item in _STATE["entitlements"]
                    if _scope(item, tenant_id, normalized) and item.get("entitlement_id") == entitlement_id
                ),
                None,
            )
            if entitlement is None or entitlement_runtime_status(entitlement) != "active":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "entitlement_allocation_restricted", "message": "entitlement is not active"},
                )
            if operation in {"stream_create", "stream_start"} and current_count is not None:
                if current_count >= int(entitlement.get("stream_limit") or 0):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={"code": "stream_limit_exceeded", "message": "entitlement stream limit is exhausted"},
                    )
    license_entitlement = require_license_allocation(
        tenant_id,
        normalized,
        operation,
        current_count=current_count,
    )
    return _copy(entitlement) if entitlement is not None else license_entitlement


def require_project_allocation(
    tenant_id: str,
    entitlement_project_id: str,
    requested_project_id: str,
    *,
    current_count: int,
) -> dict[str, Any] | None:
    """Authorize a tenant project allocation against its current plan and signed license."""
    from app import settings
    from app.portrait_commercial_license import require_license_allocation

    requested = validate_project_id(requested_project_id)
    entitlement: dict[str, Any] | None = None
    if settings.COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED:
        with _LOCK:
            source_project = _ensure_project(tenant_id, entitlement_project_id)
            if _reconcile_commercial_profile_transition(tenant_id, source_project):
                _save()
            if _reconcile_entitlements(tenant_id, source_project):
                _save()
            profile = _profile_record(tenant_id, source_project)
            if profile is None or profile.get("commercial_status") != "active":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "commercial_profile_allocation_restricted",
                        "message": "commercial profile must be active for new project allocations",
                    },
                )
            entitlement_id = profile.get("current_entitlement_id")
            entitlement = next(
                (
                    item
                    for item in _STATE["entitlements"]
                    if _scope(item, tenant_id, source_project) and item.get("entitlement_id") == entitlement_id
                ),
                None,
            )
            if entitlement is None or entitlement_runtime_status(entitlement) != "active":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "entitlement_allocation_restricted", "message": "entitlement is not active"},
                )
            if max(0, int(current_count)) >= int(entitlement.get("project_limit") or 0):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "project_limit_exceeded", "message": "entitlement project limit is exhausted"},
                )
    license_entitlement = require_license_allocation(
        tenant_id,
        requested,
        "project_create",
        current_count=max(0, int(current_count)),
    )
    return _copy(entitlement) if entitlement is not None else license_entitlement


def usage_summary(
    tenant_id: str,
    project_id: str,
    *,
    created_since: float | None = None,
    created_until: float | None = None,
) -> dict[str, Any]:
    profile = get_commercial_profile(tenant_id, project_id)
    aggregate = aggregate_usage(
        tenant_id,
        project_id,
        created_since=created_since,
        created_until=created_until,
        timezone=str(profile.get("timezone") or "Asia/Shanghai"),
        granularity="day",
        budget_limit=profile.get("budget_limit"),
        budget_currency=str(profile.get("budget_currency") or "CNY"),
    )
    rows = list_call_logs(
        tenant_id,
        project_id=project_id,
        created_since=created_since,
        created_until=created_until,
        limit=500,
    )
    rows = [row for row in rows if not str(row.get("path") or "").startswith(_METERING_EXCLUDED_PREFIXES)]
    if aggregate["event_count"]:
        latency_values = sorted(float(row.get("latency_ms") or 0) for row in rows)
        dimensions = aggregate["dimensions"]
        return {
            **aggregate,
            "latency_ms": {
                "p50": percentile(latency_values, 0.50),
                "p95": percentile(latency_values, 0.95),
                "p99": percentile(latency_values, 0.99),
            },
            "by_endpoint": dimensions["endpoint"],
            "by_model": dimensions["model_version"],
            "by_capability": dimensions["capability"],
            "by_resource_type": dimensions["resource_type"],
        }
    request_count = len(rows)
    success_count = sum(1 for row in rows if row.get("status") == "success")
    latency_values = sorted(float(row.get("latency_ms") or 0) for row in rows)
    endpoint_counts: dict[str, int] = defaultdict(int)
    model_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        endpoint_counts[str(row.get("endpoint") or row.get("path") or "unknown")] += 1
        model_counts[str(row.get("model_version") or "unknown")] += 1
    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "request_count": request_count,
        "success_count": success_count,
        "error_count": request_count - success_count,
        "success_rate": success_count / request_count if request_count else 1.0,
        "latency_ms": {
            "p50": percentile(latency_values, 0.50),
            "p95": percentile(latency_values, 0.95),
            "p99": percentile(latency_values, 0.99),
        },
        "by_endpoint": [{"endpoint": key, "request_count": value} for key, value in sorted(endpoint_counts.items())],
        "by_model": [{"model_version": key, "request_count": value} for key, value in sorted(model_counts.items())],
        "window": {"created_since": created_since, "created_until": created_until},
        "source": "call_logs",
        "complete": request_count < 500,
    }


def usage_timeseries(
    tenant_id: str,
    project_id: str,
    *,
    created_since: float | None = None,
    created_until: float | None = None,
    timezone: str = "UTC",
    granularity: str = "day",
) -> list[dict[str, Any]]:
    profile = get_commercial_profile(tenant_id, project_id)
    aggregate = aggregate_usage(
        tenant_id,
        project_id,
        created_since=created_since,
        created_until=created_until,
        timezone=timezone or str(profile.get("timezone") or "Asia/Shanghai"),
        granularity=granularity,
        budget_limit=profile.get("budget_limit"),
        budget_currency=str(profile.get("budget_currency") or "CNY"),
    )
    if aggregate["event_count"]:
        return cast(list[dict[str, Any]], aggregate["series"])
    rows = list_call_logs(
        tenant_id,
        project_id=project_id,
        created_since=created_since,
        created_until=created_until,
        limit=500,
    )
    rows = [row for row in rows if not str(row.get("path") or "").startswith(_METERING_EXCLUDED_PREFIXES)]
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        timestamp = float(row.get("created_at") or 0)
        local = datetime.fromtimestamp(timestamp, tz=UTC).astimezone(ZoneInfo(timezone))
        key = local.strftime("%Y-%m") if granularity == "month" else local.date().isoformat()
        bucket = buckets.setdefault(
            key, {"date": key, "request_count": 0, "success_count": 0, "error_count": 0, "latency_ms_sum": 0.0}
        )
        bucket["request_count"] += 1
        bucket["success_count" if row.get("status") == "success" else "error_count"] += 1
        bucket["latency_ms_sum"] += float(row.get("latency_ms") or 0)
    result = []
    for key in sorted(buckets):
        bucket = buckets[key]
        count = int(bucket["request_count"])
        latency_ms_sum = float(bucket.pop("latency_ms_sum"))
        result.append(
            {
                **bucket,
                "success_rate": int(bucket["success_count"]) / count if count else 1.0,
                "average_latency_ms": latency_ms_sum / count if count else 0.0,
            }
        )
    return result


def quota_forecast(tenant_id: str, project_id: str) -> dict[str, Any]:
    applications = list_applications(tenant_id, project_id)
    current_date = utc_date(now_seconds())
    forecasts = []
    for application in applications:
        quota = application.get("daily_quota")
        used = int(application.get("daily_quota_used") or 0) if application.get("quota_date") == current_date else 0
        quota_value = int(quota) if isinstance(quota, int) and quota > 0 else None
        remaining = max(0, quota_value - used) if quota_value is not None else None
        timestamp = now_seconds()
        midnight = datetime.fromtimestamp(timestamp, tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        elapsed_seconds = max(1.0, timestamp - midnight)
        consumption_rate_per_second = used / elapsed_seconds
        forecast_exhaustion_at = (
            timestamp + remaining / consumption_rate_per_second
            if remaining is not None and remaining > 0 and consumption_rate_per_second > 0
            else timestamp if remaining == 0 and quota_value is not None
            else None
        )
        forecasts.append(
            {
                "application_id": application.get("app_id"),
                "quota_date": current_date,
                "daily_quota": quota_value,
                "used": used,
                "remaining": remaining,
                "utilization": used / quota_value if quota_value else None,
                "consumption_rate_per_hour": consumption_rate_per_second * 3600,
                "forecast_exhaustion_at": forecast_exhaustion_at,
                "alert_status": (
                    "exhausted"
                    if remaining == 0 and quota_value is not None
                    else "warning"
                    if quota_value and used / quota_value >= 0.8
                    else "ok"
                ),
            }
        )
    return {
        "definition_version": "1.0",
        "tenant_id": tenant_id,
        "project_id": project_id,
        "applications": forecasts,
        "generated_at": now_seconds(),
    }


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    position = max(0.0, min(1.0, quantile)) * (len(values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(values[lower], 3)
    weight = position - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 3)


def upsert_sla_definition(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    with _LOCK:
        _ensure_project(tenant_id, project_id)
        definition_version = str(payload.get("definition_version") or "1.0").strip()
        if any(
            _scope(item, tenant_id, project_id) and item.get("definition_version") == definition_version
            for item in _STATE["sla_definitions"]
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SLA definition version already exists")
        timestamp = now_seconds()
        availability_target = float(payload.get("availability_target") or 0.995)
        record = {
            "sla_definition_id": new_id("sla"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "definition_version": definition_version,
            "availability_target": availability_target,
            "p95_latency_ms": int(payload.get("p95_latency_ms") or 2000),
            "p99_latency_ms": int(payload.get("p99_latency_ms") or 5000),
            "window_seconds": max(60, int(payload.get("window_seconds") or 2_592_000)),
            "timezone": str(payload.get("timezone") or "Asia/Shanghai"),
            "exclusion_rules": _copy(payload.get("exclusion_rules") or []),
            "effective_at": float(payload.get("effective_at") or timestamp),
            "expires_at": payload.get("expires_at"),
            "created_at": timestamp,
            "created_by": actor[:256],
        }
        if not 0 < availability_target <= 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="availability target is invalid"
            )
        _STATE["sla_definitions"].append(record)
        _save()
        return _copy(record)


def list_sla_definitions(
    tenant_id: str,
    project_id: str,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [_copy(item) for item in _STATE["sla_definitions"] if _scope(item, tenant_id, project_id)]
        return rows if limit is None else rows[:limit]


def compute_sla_report(
    tenant_id: str,
    project_id: str,
    *,
    created_since: float,
    created_until: float,
    actor: str,
) -> dict[str, Any]:
    with _LOCK:
        definitions = list_sla_definitions(tenant_id, project_id)
        if not definitions:
            definition = upsert_sla_definition(tenant_id, project_id, {}, actor="system")
        else:
            definition = max(definitions, key=lambda item: float(item.get("effective_at") or 0))
    summary = usage_summary(
        tenant_id,
        project_id,
        created_since=created_since,
        created_until=created_until,
    )
    availability = float(summary["success_rate"])
    target = float(definition["availability_target"])
    request_count = int(summary["request_count"])
    allowed_errors = max(0.0, request_count * (1.0 - target))
    consumed_errors = int(summary["error_count"])
    timestamp = now_seconds()
    report = {
        "sla_report_id": new_id("sla_report"),
        "tenant_id": tenant_id,
        "project_id": project_id,
        "definition_version": definition["definition_version"],
        "created_since": created_since,
        "created_until": created_until,
        "availability": availability,
        "availability_target": target,
        "p95_latency_ms": summary["latency_ms"]["p95"],
        "p99_latency_ms": summary["latency_ms"]["p99"],
        "request_count": request_count,
        "error_count": consumed_errors,
        "error_budget_allowed": allowed_errors,
        "error_budget_remaining": allowed_errors - consumed_errors,
        "met": availability >= target
        and (
            summary["latency_ms"]["p95"] is None
            or float(summary["latency_ms"]["p95"]) <= int(definition["p95_latency_ms"])
        ),
        "source_complete": summary["complete"],
        "created_at": timestamp,
        "created_by": actor[:256],
    }
    with _LOCK:
        _STATE["sla_reports"].append(report)
        _save()
    return _copy(report)


def list_sla_reports(tenant_id: str, project_id: str, *, limit: int | None = 100) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [_copy(item) for item in _STATE["sla_reports"] if _scope(item, tenant_id, project_id)]
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def create_incident(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        _ensure_project(tenant_id, project_id)
        severity = str(payload.get("severity") or "sev3").lower()
        if severity not in INCIDENT_SEVERITIES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="incident severity is invalid")
        timestamp = now_seconds()
        incident_id = new_id("incident")
        record = {
            "incident_id": incident_id,
            "incident_number": f"INC-{datetime.fromtimestamp(timestamp, tz=UTC):%Y%m%d}-{len(_STATE['incidents']) + 1:04d}",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "title": str(payload.get("title") or "Untitled incident")[:256],
            "severity": severity,
            "status": "investigating",
            "impact_scope": str(payload.get("impact_scope") or "unknown")[:1000],
            "customer_visible_summary": str(payload.get("customer_visible_summary") or "")[:4000],
            "internal_summary": str(payload.get("internal_summary") or "")[:8000],
            "started_at": float(payload.get("started_at") or timestamp),
            "recovered_at": None,
            "closed_at": None,
            "owner": str(payload.get("owner") or actor)[:256],
            "root_cause": None,
            "action_items": [],
            "timeline": [
                {
                    "event_id": new_id("timeline"),
                    "at": timestamp,
                    "type": "created",
                    "message": "incident created",
                    "actor": actor[:256],
                }
            ],
            "related_request_ids": sorted(
                {request_id, *[str(item) for item in payload.get("related_request_ids", []) if str(item)]}
            ),
            "related_model_versions": _copy(payload.get("related_model_versions") or []),
            "version": 1,
            "created_at": timestamp,
            "created_by": actor[:256],
            "updated_at": timestamp,
            "updated_by": actor[:256],
        }
        _STATE["incidents"].append(record)
        _save()
        return _copy(record)


def list_incidents(
    tenant_id: str,
    project_id: str,
    *,
    status_filter: str | None = None,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [
            _copy(item)
            for item in _STATE["incidents"]
            if _scope(item, tenant_id, project_id) and (status_filter is None or item.get("status") == status_filter)
        ]
        ordered = sorted(rows, key=lambda item: float(item.get("started_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def update_incident(
    tenant_id: str,
    project_id: str,
    incident_id: str,
    updates: dict[str, Any],
    *,
    actor: str,
    expected_version: int | None,
) -> dict[str, Any]:
    with _LOCK:
        record = next(
            (
                item
                for item in _STATE["incidents"]
                if _scope(item, tenant_id, project_id) and item.get("incident_id") == incident_id
            ),
            None,
        )
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident does not exist")
        if expected_version is not None and int(record.get("version", 1)) != expected_version:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="incident version conflict")
        next_status = str(updates.get("status") or record.get("status") or "investigating").lower()
        if next_status not in INCIDENT_STATUSES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="incident status is invalid")
        timestamp = now_seconds()
        allowed = {
            "status",
            "severity",
            "impact_scope",
            "customer_visible_summary",
            "internal_summary",
            "owner",
            "root_cause",
            "action_items",
        }
        for key, value in updates.items():
            if key in allowed and value is not None:
                record[key] = _copy(value)
        message = str(updates.get("timeline_message") or f"status changed to {next_status}")[:2000]
        record["timeline"].append(
            {
                "event_id": new_id("timeline"),
                "at": timestamp,
                "type": "update",
                "message": message,
                "actor": actor[:256],
            }
        )
        if next_status == "resolved" and record.get("recovered_at") is None:
            record["recovered_at"] = timestamp
        if next_status == "closed":
            record["closed_at"] = timestamp
        record["updated_at"] = timestamp
        record["updated_by"] = actor[:256]
        record["version"] = int(record.get("version", 1)) + 1
        _save()
        return _copy(record)


def health_timeline(tenant_id: str, project_id: str, *, limit: int | None = 100) -> list[dict[str, Any]]:
    events = []
    for incident in list_incidents(tenant_id, project_id, limit=None):
        for entry in incident.get("timeline", []):
            events.append(
                {
                    "at": entry.get("at"),
                    "type": "incident",
                    "source_id": incident.get("incident_id"),
                    "severity": incident.get("severity"),
                    "status": incident.get("status"),
                    "message": entry.get("message"),
                }
            )
    ordered = sorted(events, key=lambda item: float(item.get("at") or 0), reverse=True)
    return ordered if limit is None else ordered[:limit]


def _compliance_semantic_violations(
    control_id: str,
    data: dict[str, Any],
    *,
    timestamp: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    derived: dict[str, Any] = {}

    def require(condition: bool, field: str, code: str, message: str) -> None:
        if not condition:
            violations.append({"field": field, "code": code, "message": message})

    def number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    if control_id == "COM-001":
        require(bool(str(data.get("responsible_contact") or "").strip()), "responsible_contact", "contact_required", "responsible contact is required")
        require(data.get("necessity_assessment") in {True, "approved"}, "necessity_assessment", "necessity_not_approved", "necessity assessment must be approved")
        require(bool(data.get("recipient_categories")), "recipient_categories", "recipients_required", "recipient categories must be recorded")
    elif control_id == "COM-002":
        require(number(data.get("obtained_at")) > 0, "obtained_at", "consent_time_invalid", "consent time must be recorded")
        require(str(data.get("withdrawal_status") or "").lower() in {"not_withdrawn", "active", "not_applicable"}, "withdrawal_status", "consent_withdrawn", "withdrawn consent cannot approve processing")
        require(bool(str(data.get("proof_ref") or "").strip()), "proof_ref", "consent_proof_required", "consent proof reference is required")
    elif control_id == "COM-003":
        policy = str(data.get("minor_policy") or "").lower()
        require(policy in {"not_applicable", "prohibit", "guardian_consent_required"}, "minor_policy", "minor_policy_invalid", "minor policy must fail closed or require verified guardian consent")
        if policy == "guardian_consent_required":
            require(str(data.get("guardian_consent_status") or "").lower() == "obtained", "guardian_consent_status", "guardian_consent_missing", "guardian consent is required")
            require(str(data.get("guardian_verification_status") or "").lower() == "verified", "guardian_verification_status", "guardian_not_verified", "guardian identity must be verified")
    elif control_id == "COM-004":
        alternative = data.get("alternative_available")
        require(alternative is True or str(alternative).lower() in {"not_applicable", "no_equivalent"}, "alternative_available", "alternative_not_available", "a non-face alternative or approved no-equivalent decision is required")
        require(bool(str(data.get("alternative_process") or "").strip()), "alternative_process", "alternative_process_required", "the alternative process must be documented")
    elif control_id == "COM-005":
        require(bool(str(data.get("assessment_ref") or "").strip()), "assessment_ref", "assessment_required", "impact assessment evidence is required")
        require(number(data.get("review_due_at")) > timestamp, "review_due_at", "assessment_review_expired", "impact assessment review date must be in the future")
    elif control_id == "COM-006":
        allowed_regions = data.get("allowed_regions")
        require(isinstance(allowed_regions, list) and bool(allowed_regions), "allowed_regions", "regions_required", "at least one allowed storage region is required")
        require(str(data.get("transfer_policy") or "").lower() in {"local_only", "blocked", "approved_only"}, "transfer_policy", "transfer_policy_unsafe", "cross-region transfer must be blocked or approval-only")
        require(data.get("export_requires_approval") is True, "export_requires_approval", "export_approval_required", "external export must require approval")
    elif control_id == "COM-007":
        retention = data.get("backend_retention")
        require(isinstance(retention, dict) and _COMPLIANCE_BACKENDS.issubset(retention), "backend_retention", "retention_incomplete", "retention must cover all execution backends")
        require(bool(str(data.get("deletion_workflow") or "").strip()), "deletion_workflow", "deletion_workflow_required", "verified deletion workflow is required")
    elif control_id == "COM-008":
        due_days = int(number(data.get("due_days")))
        fulfillment = {str(item) for item in data.get("fulfillment_backends") or []}
        require(1 <= due_days <= 365, "due_days", "rights_due_invalid", "rights request due days must be within 1..365")
        require(_COMPLIANCE_BACKENDS.issubset(fulfillment), "fulfillment_backends", "rights_backends_incomplete", "rights fulfillment must cover all execution backends")
    elif control_id == "COM-009":
        prohibited = data.get("prohibited_areas")
        require(isinstance(prohibited, list) and bool(prohibited), "prohibited_areas", "privacy_spaces_required", "privacy spaces must be explicitly prohibited")
        require(bool(str(data.get("signage_ref") or "").strip()), "signage_ref", "signage_required", "public-place signage evidence is required")
        require(str(data.get("collection_area") or "").strip() not in {str(item).strip() for item in prohibited or []}, "collection_area", "prohibited_area_selected", "collection area cannot be a prohibited privacy space")
    elif control_id == "COM-010":
        threshold = int(number(data.get("filing_threshold")))
        current_count = max(0, int(number(data.get("current_count"))))
        warning_ratio = number(data.get("warning_ratio"))
        filing_status = str(data.get("filing_status") or "").lower()
        require(threshold > 0, "filing_threshold", "filing_threshold_invalid", "filing threshold must be positive")
        require(0 < warning_ratio <= 0.8, "warning_ratio", "warning_ratio_invalid", "filing warning ratio must be no greater than 80%")
        utilization = current_count / threshold if threshold > 0 else 1.0
        derived.update({"utilization": utilization, "warning": utilization >= warning_ratio, "filing_required": utilization >= 1.0})
        if utilization >= warning_ratio:
            require(filing_status in {"warning_acknowledged", "in_progress", "filed", "approved"}, "filing_status", "filing_workflow_required", "threshold warning must start a filing workflow")
    elif control_id == "COM-011":
        require(data.get("human_review_enabled") is True, "human_review_enabled", "human_review_required", "human review must be enabled")
        require(bool(str(data.get("appeal_process") or "").strip()), "appeal_process", "appeal_required", "appeal process is required")
        require(str(data.get("decision_use") or "").lower() in {"assistive_only", "human_decision", "approved_exception"}, "decision_use", "sole_automated_decision_forbidden", "results cannot be the sole unapproved high-impact decision")
    elif control_id == "COM-012":
        for field in ("incident_process", "notification_decision_owner", "response_plan_ref"):
            require(bool(str(data.get(field) or "").strip()), field, "privacy_incident_control_required", "privacy incident response evidence is required")
    return violations, derived


def upsert_compliance_record(
    tenant_id: str,
    project_id: str,
    control_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    approved_by: str | None,
) -> dict[str, Any]:
    with _LOCK:
        _ensure_project(tenant_id, project_id)
        normalized_control = control_id.upper()
        if normalized_control not in COMPLIANCE_CONTROL_IDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="compliance control id is invalid"
            )
        record_status = str(payload.get("status") or "draft").lower()
        control_data = _copy(payload.get("control_data") or {})
        required_control_data = COMPLIANCE_REQUIRED_CONTROL_DATA[normalized_control]
        missing_control_data = sorted(
            key
            for key in required_control_data
            if key not in control_data
            or control_data[key] is None
            or control_data[key] == ""
            or control_data[key] == []
        )
        semantic_violations, semantic_derived = _compliance_semantic_violations(
            normalized_control,
            control_data,
            timestamp=now_seconds(),
        )
        if record_status == "approved":
            missing_baseline = []
            for field in ("legal_basis", "processing_purpose", "evidence_refs"):
                if not payload.get(field):
                    missing_baseline.append(field)
            if str(payload.get("applicability") or "pending") == "pending":
                missing_baseline.append("applicability")
            if not approved_by:
                missing_baseline.append("approved_by")
            if missing_baseline or missing_control_data or semantic_violations:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "compliance_record_incomplete",
                        "message": "approved compliance record is incomplete",
                        "missing_fields": sorted(set(missing_baseline)),
                        "missing_control_data": missing_control_data,
                        "semantic_violations": semantic_violations,
                    },
                )
        record = next(
            (
                item
                for item in _STATE["compliance_records"]
                if _scope(item, tenant_id, project_id) and item.get("control_id") == normalized_control
            ),
            None,
        )
        timestamp = now_seconds()
        if record is None:
            record = {
                "compliance_record_id": new_id("compliance"),
                "tenant_id": tenant_id,
                "project_id": project_id,
                "control_id": normalized_control,
                "version": 0,
                "created_at": timestamp,
                "created_by": actor[:256],
            }
            _STATE["compliance_records"].append(record)
        record.update(
            {
                "status": record_status,
                "definition_version": str(payload.get("definition_version") or "1.0"),
                "applicability": str(payload.get("applicability") or "pending"),
                "legal_basis": str(payload.get("legal_basis") or "")[:2000],
                "processing_purpose": str(payload.get("processing_purpose") or "")[:2000],
                "data_categories": _copy(payload.get("data_categories") or []),
                "data_subjects": _copy(payload.get("data_subjects") or []),
                "storage_regions": _copy(payload.get("storage_regions") or []),
                "retention": _copy(payload.get("retention") or {}),
                "evidence_refs": _copy(payload.get("evidence_refs") or []),
                "risk_summary": str(payload.get("risk_summary") or "")[:4000],
                "mitigations": _copy(payload.get("mitigations") or []),
                "control_data": control_data,
                "control_data_valid": not missing_control_data,
                "control_semantics_valid": not semantic_violations,
                "semantic_violations": semantic_violations,
                "derived_status": semantic_derived,
                "approved_by": approved_by,
                "approved_at": timestamp if approved_by else None,
                "expires_at": payload.get("expires_at"),
                "updated_at": timestamp,
                "updated_by": actor[:256],
                "version": int(record.get("version", 0)) + 1,
            }
        )
        _save()
        return _copy(record)


def compliance_status(tenant_id: str, project_id: str) -> dict[str, Any]:
    with _LOCK:
        rows = [_copy(item) for item in _STATE["compliance_records"] if _scope(item, tenant_id, project_id)]
    by_id = {str(item.get("control_id")): item for item in rows}
    timestamp = now_seconds()
    controls = []
    blocking = []
    for control_id in sorted(COMPLIANCE_CONTROL_IDS):
        record = by_id.get(control_id)
        expired = bool(record and record.get("expires_at") is not None and float(record["expires_at"]) <= timestamp)
        approved = bool(
            record
            and record.get("status") == "approved"
            and record.get("approved_by")
            and record.get("control_data_valid") is True
            and record.get("control_semantics_valid") is True
            and not expired
        )
        controls.append({"control_id": control_id, "approved": approved, "expired": expired, "record": record})
        if not approved:
            blocking.append(control_id)
    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "controls": controls,
        "blocking_controls": blocking,
        "ready": not blocking,
    }


def require_compliance_control(tenant_id: str, project_id: str, control_id: str) -> dict[str, Any] | None:
    from app import settings

    if not settings.COMMERCIAL_ENTITLEMENT_ENFORCEMENT_ENABLED:
        return None
    normalized = control_id.upper()
    result = compliance_status(tenant_id, project_id)
    control = next((item for item in result["controls"] if item["control_id"] == normalized), None)
    if control is None or not control["approved"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "compliance_operation_blocked",
                "message": "required compliance control is not approved",
                "control_id": normalized,
            },
        )
    return cast(dict[str, Any], control["record"])


def create_rights_request(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    with _LOCK:
        _ensure_project(tenant_id, project_id)
        request_type = str(payload.get("request_type") or "").lower()
        if request_type not in RIGHTS_REQUEST_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="rights request type is invalid"
            )
        if request_type == "export":
            require_compliance_control(tenant_id, project_id, "COM-006")
        timestamp = now_seconds()
        record = {
            "rights_request_id": new_id("rights"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "request_type": request_type,
            "status": "received",
            "subject_reference": hashlib.sha256(
                str(payload.get("subject_reference") or "").encode("utf-8")
            ).hexdigest(),
            "identity_verification": "pending",
            "due_at": float(payload.get("due_at") or (timestamp + 30 * 86400)),
            "exception_basis": None,
            "execution_evidence": [],
            "timeline": [{"at": timestamp, "status": "received", "actor": actor[:256]}],
            "created_at": timestamp,
            "created_by": actor[:256],
            "updated_at": timestamp,
            "updated_by": actor[:256],
            "version": 1,
        }
        _STATE["rights_requests"].append(record)
        _save()
        return _copy(record)


def list_rights_requests(tenant_id: str, project_id: str, *, limit: int | None = 100) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [_copy(item) for item in _STATE["rights_requests"] if _scope(item, tenant_id, project_id)]
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def update_rights_request(
    tenant_id: str,
    project_id: str,
    rights_request_id: str,
    updates: dict[str, Any],
    *,
    actor: str,
    expected_version: int | None,
) -> dict[str, Any]:
    with _LOCK:
        record = next(
            (
                item
                for item in _STATE["rights_requests"]
                if _scope(item, tenant_id, project_id) and item.get("rights_request_id") == rights_request_id
            ),
            None,
        )
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="rights request does not exist")
        if expected_version is not None and int(record.get("version", 1)) != expected_version:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="rights request version conflict")
        current_status = str(record.get("status") or "received")
        next_status = str(updates.get("status") or current_status).lower()
        if next_status != current_status and next_status not in RIGHTS_REQUEST_TRANSITIONS.get(current_status, set()):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="rights request transition is not allowed")
        identity_verification = str(
            updates.get("identity_verification") or record.get("identity_verification") or "pending"
        )
        evidence = _copy(updates.get("execution_evidence", record.get("execution_evidence") or []))
        if next_status in {"verified", "in_progress", "completed"} and identity_verification != "verified":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="identity verification is required"
            )
        if next_status == "completed":
            if not isinstance(evidence, list) or not evidence:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="completion evidence is required"
                )
            if record.get("request_type") in {"deletion", "withdrawal", "restriction"}:
                missing_backends = sorted(RIGHTS_EXECUTION_BACKENDS - _completed_execution_backends(evidence))
                if missing_backends:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={
                            "code": "rights_request_evidence_incomplete",
                            "message": "cross-backend execution evidence is incomplete",
                            "missing_backends": missing_backends,
                        },
                    )
        timestamp = now_seconds()
        record["status"] = next_status
        record["identity_verification"] = identity_verification
        record["exception_basis"] = updates.get("exception_basis", record.get("exception_basis"))
        record["execution_evidence"] = evidence
        record["timeline"].append(
            {
                "at": timestamp,
                "status": next_status,
                "actor": actor[:256],
                "message": str(updates.get("timeline_message") or f"status changed to {next_status}")[:2000],
            }
        )
        record["updated_at"] = timestamp
        record["updated_by"] = actor[:256]
        record["version"] = int(record.get("version", 1)) + 1
        _save()
        return _copy(record)


def register_evidence_package(record: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        _STATE["evidence_packages"].append(_copy(record))
        _save()
        return _copy(record)


def list_evidence_packages(tenant_id: str, project_id: str, *, limit: int | None = 100) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [_copy(item) for item in _STATE["evidence_packages"] if _scope(item, tenant_id, project_id)]
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def create_support_case(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        normalized = _ensure_project(tenant_id, project_id)
        severity = str(payload.get("severity") or "sev3").strip().lower()
        if severity not in INCIDENT_SEVERITIES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported support severity")
        timestamp = now_seconds()
        record = {
            "support_case_id": new_id("support"),
            "tenant_id": tenant_id,
            "project_id": normalized,
            "severity": severity,
            "status": "open",
            "title": str(payload.get("title") or "").strip()[:256],
            "description": str(payload.get("description") or "").strip()[:8000],
            "environment": str(payload.get("environment") or "").strip()[:128],
            "product_version": str(payload.get("product_version") or "").strip()[:64],
            "request_ids": sorted(
                {str(item).strip() for item in payload.get("request_ids") or [] if str(item).strip()}
            ),
            "task_ids": sorted({str(item).strip() for item in payload.get("task_ids") or [] if str(item).strip()}),
            "owner": str(payload.get("owner") or "").strip()[:256] or None,
            "response_due_at": payload.get("response_due_at"),
            "redacted_attachments": _copy(payload.get("redacted_attachments") or []),
            "request_id": request_id,
            "version": 1,
            "created_at": timestamp,
            "created_by": actor[:256],
            "updated_at": timestamp,
            "updated_by": actor[:256],
        }
        if not record["title"] or not record["environment"] or not record["product_version"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="support case requires title, environment and product version",
            )
        _STATE["support_cases"].append(record)
        _save()
        return _copy(record)


def list_support_cases(
    tenant_id: str,
    project_id: str,
    *,
    status_filter: str | None = None,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [
            _copy(item)
            for item in _STATE["support_cases"]
            if _scope(item, tenant_id, project_id) and (status_filter is None or item.get("status") == status_filter)
        ]
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def update_support_case(
    tenant_id: str,
    project_id: str,
    support_case_id: str,
    updates: dict[str, Any],
    *,
    actor: str,
    expected_version: int | None,
) -> dict[str, Any]:
    with _LOCK:
        record = next(
            (
                item
                for item in _STATE["support_cases"]
                if _scope(item, tenant_id, project_id) and item.get("support_case_id") == support_case_id
            ),
            None,
        )
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="support case does not exist")
        if expected_version is not None and int(record.get("version", 1)) != expected_version:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="support case version conflict")
        next_status = str(updates.get("status") or record.get("status") or "open").strip().lower()
        if next_status not in SUPPORT_CASE_STATUSES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported support status")
        allowed = {
            "status",
            "severity",
            "description",
            "owner",
            "response_due_at",
            "request_ids",
            "task_ids",
            "redacted_attachments",
        }
        for key, value in updates.items():
            if key in allowed:
                record[key] = _copy(value)
        if record.get("severity") not in INCIDENT_SEVERITIES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported support severity")
        record["status"] = next_status
        record["updated_at"] = now_seconds()
        record["updated_by"] = actor[:256]
        record["version"] = int(record.get("version", 1)) + 1
        _save()
        return _copy(record)


def _builtin_templates() -> list[dict[str, Any]]:
    definitions = [
        ("campus-safety", "园区安全与重点区域巡检", ["person_detection", "appearance", "tracking"]),
        ("retail-flow", "零售客流与服务质量分析", ["person_detection", "tracking", "appearance"]),
        ("worksite-safety", "工地人员安全与合规着装", ["person_detection", "pose", "appearance"]),
        ("venue-flow", "会展与大型场馆人员流动分析", ["person_detection", "tracking", "gait"]),
        ("visitor-control", "校园、访客与受控区域管理", ["face_detection", "face_embedding", "tracking"]),
    ]
    return [
        {
            "template_id": template_id,
            "name": name,
            "version": "1.0.0",
            "status": "published",
            "allowed_capabilities": capabilities,
            "configuration": {
                "recommended_model_aliases": {capability: "champion" for capability in capabilities},
                "threshold_profile": "normal",
                "rules": [],
                "roi": [],
                "sample_interval_seconds": 1.0,
                "performance": {"max_concurrency": 4, "max_queue_seconds": 2},
                "privacy_mask": True,
                "human_review": True,
                "retention_days": {"media": 7, "results": 30, "audit": 180},
                "console": {"menus": capabilities, "dashboard": "operations-default"},
                "webhooks": {"events": ["job.completed", "stream.event"], "mapping_version": "1.0"},
            },
            "acceptance_samples": [f"acceptance/{template_id}/manifest.json"],
            "capacity_assumptions": {"status": "unqualified", "report_required": True},
            "delivery_checklist": [
                "confirm processing purpose and lawful basis",
                "approve COM-001 through COM-012 applicability",
                "run fixed-sample quality acceptance",
                "run target-hardware capacity acceptance",
                "verify rollback target",
            ],
            "risk_controls": ["辅助分析，不作为高影响决定的唯一依据", "启用前完成 COM-001~012 适用性评估"],
            "rollback_supported": True,
            "created_at": 0.0,
            "created_by": "product-baseline",
        }
        for template_id, name, capabilities in definitions
    ]


def _ensure_builtin_templates() -> None:
    existing = {(item.get("template_id"), item.get("version")) for item in _STATE["industry_templates"]}
    for template in _builtin_templates():
        if (template["template_id"], template["version"]) not in existing:
            _STATE["industry_templates"].append(template)


def _template_acceptance_evidence(template: dict[str, Any], *, required: bool) -> dict[str, Any]:
    manifests = []
    errors = []
    root = _TEMPLATE_ACCEPTANCE_ROOT.resolve()
    for relative_path in template.get("acceptance_samples") or []:
        path = (root / str(relative_path)).resolve()
        try:
            path.relative_to(root / "acceptance")
        except ValueError:
            errors.append(f"acceptance manifest path escapes the acceptance root: {relative_path}")
            continue
        if not path.is_file():
            errors.append(f"acceptance manifest does not exist: {relative_path}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"acceptance manifest is unreadable: {relative_path}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"acceptance manifest must be an object: {relative_path}")
            continue
        if payload.get("template_id") != template.get("template_id"):
            errors.append(f"acceptance manifest template_id mismatch: {relative_path}")
        if payload.get("template_version") != template.get("version"):
            errors.append(f"acceptance manifest version mismatch: {relative_path}")
        if payload.get("schema_version") != "1.0":
            errors.append(f"acceptance manifest schema_version mismatch: {relative_path}")
        if payload.get("evidence_status") not in {"specification_only", "validated"}:
            errors.append(f"acceptance manifest evidence_status is invalid: {relative_path}")
        cases = payload.get("cases")
        if not isinstance(cases, list) or not cases:
            errors.append(f"acceptance manifest cases are empty: {relative_path}")
        elif any(
            not isinstance(case, dict)
            or not case.get("case_id")
            or not isinstance(case.get("expected_outcomes"), list)
            or not case.get("expected_outcomes")
            or not set(case.get("required_capabilities") or []).issubset(
                set(template.get("allowed_capabilities") or [])
            )
            for case in cases
        ):
            errors.append(f"acceptance manifest case contract is invalid: {relative_path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifests.append(
            {
                "path": str(relative_path),
                "sha256": digest,
                "case_count": len(cases) if isinstance(cases, list) else 0,
                "evidence_status": payload.get("evidence_status"),
                "customer_validation_required": bool(payload.get("customer_validation_required", True)),
            }
        )
    result = {
        "valid": not errors and bool(manifests),
        "manifests": manifests,
        "errors": errors,
        "combined_sha256": hashlib.sha256(
            json.dumps(manifests, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if manifests
        else None,
    }
    if required and not result["valid"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "template_acceptance_evidence_invalid",
                "message": "industry template acceptance evidence is missing or invalid",
                "errors": errors,
            },
        )
    return result


def list_industry_templates() -> list[dict[str, Any]]:
    with _LOCK:
        rows = []
        for item in _STATE["industry_templates"]:
            if item.get("status") != "published":
                continue
            record = _copy(item)
            record["acceptance_evidence"] = _template_acceptance_evidence(record, required=False)
            rows.append(record)
        return rows


def preview_template(tenant_id: str, project_id: str, template_id: str) -> dict[str, Any]:
    with _LOCK:
        template = next(
            (
                item
                for item in _STATE["industry_templates"]
                if item.get("template_id") == template_id and item.get("status") == "published"
            ),
            None,
        )
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="industry template does not exist")
        template = _copy(template)
        template["acceptance_evidence"] = _template_acceptance_evidence(template, required=True)
        profile = get_commercial_profile(tenant_id, project_id)
        changes = {
            "allowed_capabilities": {
                "before": (profile.get("entitlement") or {}).get("allowed_capabilities", []),
                "after": template["allowed_capabilities"],
            },
            "configuration": {"before": profile.get("template_configuration", {}), "after": template["configuration"]},
            "acceptance_evidence_sha256": {
                "before": profile.get("template_acceptance_evidence_sha256"),
                "after": template["acceptance_evidence"]["combined_sha256"],
            },
        }
        fingerprint = hashlib.sha256(
            json.dumps(changes, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {"template": _copy(template), "changes": changes, "fingerprint": fingerprint}


def apply_industry_template(
    tenant_id: str,
    project_id: str,
    template_id: str,
    *,
    actor: str,
    expected_fingerprint: str,
    dry_run: bool,
) -> dict[str, Any]:
    with _LOCK:
        preview = preview_template(tenant_id, project_id, template_id)
        if preview["fingerprint"] != expected_fingerprint:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="industry template preview is stale")
        if dry_run:
            return {**preview, "dry_run": True, "applied": False}
        profile = _profile_record(tenant_id, project_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="commercial profile does not exist")
        previous = {
            "template_id": profile.get("template_id"),
            "template_version": profile.get("template_version"),
            "template_capabilities": _copy(profile.get("template_capabilities", [])),
            "template_configuration": _copy(profile.get("template_configuration", {})),
            "template_acceptance_evidence_sha256": profile.get("template_acceptance_evidence_sha256"),
        }
        profile["template_id"] = template_id
        profile["template_version"] = preview["template"]["version"]
        profile["template_capabilities"] = _copy(preview["template"]["allowed_capabilities"])
        profile["template_configuration"] = _copy(preview["template"]["configuration"])
        profile["template_acceptance_evidence_sha256"] = preview["template"]["acceptance_evidence"][
            "combined_sha256"
        ]
        profile["updated_at"] = now_seconds()
        profile["updated_by"] = actor[:256]
        profile["version"] = int(profile.get("version", 1)) + 1
        application = {
            "template_application_id": new_id("template_apply"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "template_id": template_id,
            "template_version": preview["template"]["version"],
            "fingerprint": expected_fingerprint,
            "acceptance_evidence_sha256": preview["template"]["acceptance_evidence"]["combined_sha256"],
            "previous": previous,
            "applied": {
                "template_id": template_id,
                "template_version": preview["template"]["version"],
                "template_capabilities": _copy(preview["template"]["allowed_capabilities"]),
                "template_configuration": _copy(preview["template"]["configuration"]),
                "template_acceptance_evidence_sha256": preview["template"]["acceptance_evidence"][
                    "combined_sha256"
                ],
            },
            "status": "applied",
            "created_at": now_seconds(),
            "created_by": actor[:256],
        }
        _STATE["template_applications"].append(application)
        _save()
        return {**preview, "dry_run": False, "applied": True, "application": _copy(application)}


def list_template_applications(
    tenant_id: str,
    project_id: str,
    *,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [_copy(item) for item in _STATE["template_applications"] if _scope(item, tenant_id, project_id)]
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def rollback_industry_template(
    tenant_id: str,
    project_id: str,
    template_application_id: str,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    with _LOCK:
        application = next(
            (
                item
                for item in _STATE["template_applications"]
                if _scope(item, tenant_id, project_id)
                and item.get("template_application_id") == template_application_id
            ),
            None,
        )
        if application is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template application does not exist")
        if application.get("status") != "applied":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="template application is not rollbackable")
        profile = _profile_record(tenant_id, project_id)
        applied = application.get("applied") or {}
        if (
            profile is None
            or profile.get("template_id") != applied.get("template_id")
            or profile.get("template_version") != applied.get("template_version")
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="template application is no longer current"
            )
        previous = application.get("previous") or {}
        for key in (
            "template_id",
            "template_version",
            "template_capabilities",
            "template_configuration",
            "template_acceptance_evidence_sha256",
        ):
            profile[key] = _copy(previous.get(key))
        profile["updated_at"] = now_seconds()
        profile["updated_by"] = actor[:256]
        profile["version"] = int(profile.get("version", 1)) + 1
        application["status"] = "rolled_back"
        application["rolled_back_at"] = now_seconds()
        application["rolled_back_by"] = actor[:256]
        application["rollback_reason"] = reason.strip()[:1000]
        application["version"] = int(application.get("version", 1)) + 1
        _save()
        return {"application": _copy(application), "commercial_profile": get_commercial_profile(tenant_id, project_id)}


load_commercial_state()


__all__ = [
    "COMMERCIAL_STATUSES",
    "COMPLIANCE_CONTROL_IDS",
    "COMPLIANCE_REQUIRED_CONTROL_DATA",
    "apply_industry_template",
    "change_entitlement_status",
    "commercial_state_payload",
    "compliance_status",
    "compute_sla_report",
    "create_entitlement",
    "create_incident",
    "create_rights_request",
    "create_support_case",
    "entitlement_runtime_status",
    "get_commercial_profile",
    "health_timeline",
    "list_entitlements",
    "list_evidence_packages",
    "list_incidents",
    "list_industry_templates",
    "list_rights_requests",
    "list_sla_definitions",
    "list_sla_reports",
    "list_support_cases",
    "list_template_applications",
    "load_commercial_state",
    "preview_template",
    "quota_forecast",
    "register_evidence_package",
    "require_compliance_control",
    "require_entitlement_capability",
    "require_project_allocation",
    "reset_commercial_state",
    "restore_commercial_state",
    "rollback_industry_template",
    "update_commercial_profile",
    "update_incident",
    "update_rights_request",
    "update_support_case",
    "upsert_compliance_record",
    "upsert_sla_definition",
    "usage_summary",
    "usage_timeseries",
]
