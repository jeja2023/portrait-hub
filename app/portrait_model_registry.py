from __future__ import annotations

import copy
import hashlib
import time
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from fastapi import HTTPException, status

from app.model_config import MODEL_ALIASES, MODEL_CONFIGS
from app.model_package import get_model_path
from app.model_refs import split_cache_key, validate_alias_name, validate_model_target
from app.portrait_control_state import ControlStateBackend, ControlStateLock
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import PORTRAIT_MODEL_REGISTRY_STATE_PATH

MODEL_VERSION_STATUSES = {"draft", "candidate", "shadow", "canary", "active", "deprecated", "blocked"}
MODEL_VERSION_TRANSITIONS = {
    "draft": {"candidate", "blocked"},
    "candidate": {"shadow", "canary", "blocked", "deprecated"},
    "shadow": {"canary", "candidate", "blocked", "deprecated"},
    "canary": {"active", "candidate", "blocked", "deprecated"},
    "active": {"deprecated", "blocked"},
    "deprecated": {"candidate", "active"},
    "blocked": {"draft", "candidate", "deprecated"},
}
RELEASE_ACTIONS = {"shadow", "canary", "activate", "pause", "rollback", "deprecate"}
RELEASE_RISK_LEVELS = {"low", "medium", "high", "critical"}
_COLLECTIONS = ("models", "versions", "evaluations", "approvals", "release_events")
_LOCK = ControlStateLock()
_T = TypeVar("_T")


def _empty_state() -> dict[str, Any]:
    return {"revision": 0, **{name: [] for name in _COLLECTIONS}}


_STATE = _empty_state()


def now_seconds() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _copy(value: _T) -> _T:
    return copy.deepcopy(value)


def _validate_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        handle_state_read_error("model registry state root must be an object")
        return _empty_state()
    state = _empty_state()
    try:
        state["revision"] = max(0, int(payload.get("revision", 0)))
    except (TypeError, ValueError):
        handle_state_read_error("model registry revision is invalid")
    for name in _COLLECTIONS:
        value = payload.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            handle_state_read_error(f"model registry collection is invalid: {name}")
            continue
        state[name] = _copy(value)
    return state


_BACKEND = ControlStateBackend("model_registry", _STATE, _LOCK.raw, _empty_state, _validate_state)
_LOCK.bind(_BACKEND)


def load_model_registry_state() -> None:
    with _LOCK:
        if _BACKEND.postgres_enabled():
            if _BACKEND.revision == 0:
                _save(increment=False)
            return
        _STATE.clear()
        _STATE.update(_validate_state(read_json_state(PORTRAIT_MODEL_REGISTRY_STATE_PATH, _empty_state())))


def reset_model_registry_state(*, persist: bool = False) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(_empty_state())
        if persist:
            _save()
        elif _BACKEND.postgres_enabled():
            _BACKEND.invalidate()


def model_registry_state_payload() -> dict[str, Any]:
    with _LOCK:
        return _copy(_STATE)


def restore_model_registry_state(payload: dict[str, Any]) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(_validate_state(payload))
        _save(increment=False)


def _save(*, increment: bool = True) -> None:
    if increment:
        _STATE["revision"] = int(_STATE.get("revision", 0)) + 1
    if _BACKEND.postgres_enabled():
        _BACKEND.save(actor="model-registry-control-plane")
    else:
        write_json_state(PORTRAIT_MODEL_REGISTRY_STATE_PATH, _STATE)


def _model_record(model_id: str) -> dict[str, Any] | None:
    return next((item for item in _STATE["models"] if item.get("model_id") == model_id), None)


def _version_record(version_id: str) -> dict[str, Any] | None:
    return next((item for item in _STATE["versions"] if item.get("model_version_id") == version_id), None)


def _require_version(version_id: str) -> dict[str, Any]:
    record = _version_record(version_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model version does not exist")
    return record


def public_model(record: dict[str, Any]) -> dict[str, Any]:
    model_id = str(record.get("model_id") or "")
    versions = [_copy(item) for item in _STATE["versions"] if item.get("model_id") == model_id]
    versions.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    active = next((item for item in versions if item.get("status") == "active"), None)
    return {
        **_copy(record),
        "version_count": len(versions),
        "active_version": active,
        "latest_version": versions[0] if versions else None,
    }


def list_registered_models(
    *,
    capability: str | None = None,
    status_filter: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = []
        for item in _STATE["models"]:
            if capability and item.get("capability") != capability:
                continue
            public = public_model(item)
            if status_filter and not any(
                version.get("status") == status_filter
                for version in _STATE["versions"]
                if version.get("model_id") == item.get("model_id")
            ):
                continue
            rows.append(public)
        ordered = sorted(rows, key=lambda item: str(item.get("name") or ""))
        return ordered if limit is None else ordered[:limit]


def list_model_versions(model_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        if _model_record(model_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model does not exist")
        rows = [_copy(item) for item in _STATE["versions"] if item.get("model_id") == model_id]
        for row in rows:
            version_id = row["model_version_id"]
            row["evaluations"] = [
                _copy(item) for item in _STATE["evaluations"] if item.get("model_version_id") == version_id
            ]
            row["approvals"] = [
                _copy(item) for item in _STATE["approvals"] if item.get("model_version_id") == version_id
            ]
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


def register_model_version(payload: dict[str, Any], *, actor: str, request_id: str) -> dict[str, Any]:
    with _LOCK:
        name = str(payload.get("name") or "").strip()
        capability = str(payload.get("capability") or "").strip()
        version = str(payload.get("version") or "").strip()
        if not name or not capability or not version:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name, capability and version are required"
            )
        model = next(
            (item for item in _STATE["models"] if item.get("name") == name and item.get("capability") == capability),
            None,
        )
        timestamp = now_seconds()
        if model is None:
            model = {
                "model_id": new_id("model"),
                "name": name[:256],
                "capability": capability[:128],
                "description": str(payload.get("description") or "")[:2000],
                "owner": str(payload.get("owner") or actor)[:256],
                "quality_gates": _copy(payload.get("quality_gates") or {}),
                "created_at": timestamp,
                "created_by": actor[:256],
                "updated_at": timestamp,
                "updated_by": actor[:256],
            }
            _STATE["models"].append(model)
        if any(
            item.get("model_id") == model["model_id"] and item.get("version") == version for item in _STATE["versions"]
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model version already exists")
        sha256 = str(payload.get("sha256") or "").lower().strip()
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="model SHA-256 is invalid")
        license_name = str(payload.get("license") or "").strip()
        source = str(payload.get("source") or "").strip()
        model_card_ref = str(payload.get("model_card_ref") or "").strip()
        governance_ref = str(payload.get("governance_ref") or "").strip()
        if not license_name or not source or not model_card_ref or not governance_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="license, source, model card and governance sidecar are required",
            )
        model_target = validate_model_target(str(payload.get("model_target") or ""))
        record = {
            "model_version_id": new_id("model_version"),
            "model_id": model["model_id"],
            "version": version[:128],
            "framework": str(payload.get("framework") or "onnxruntime")[:128],
            "runtime": str(payload.get("runtime") or "onnxruntime")[:128],
            "model_target": model_target,
            "sha256": sha256,
            "artifact_size": max(0, int(payload.get("artifact_size") or 0)),
            "artifact_uri": str(payload.get("artifact_uri") or "")[:2000],
            "license": license_name[:512],
            "source": source[:2000],
            "redistribution_allowed": bool(payload.get("redistribution_allowed", False)),
            "model_card_ref": model_card_ref[:2000],
            "governance_ref": governance_ref[:2000],
            "input_contract": _copy(payload.get("input_contract") or {}),
            "output_contract": _copy(payload.get("output_contract") or {}),
            "thresholds": _copy(payload.get("thresholds") or {}),
            "dataset_lineage": _copy(payload.get("dataset_lineage") or []),
            "supports_cpu": bool(payload.get("supports_cpu", False)),
            "supports_batching": bool(payload.get("supports_batching", False)),
            "max_batch_size": max(1, int(payload.get("max_batch_size") or 1)),
            "status": "draft",
            "rollback_target": False,
            "request_id": request_id,
            "created_at": timestamp,
            "created_by": actor[:256],
            "updated_at": timestamp,
            "updated_by": actor[:256],
            "version_counter": 1,
        }
        _STATE["versions"].append(record)
        model["updated_at"] = timestamp
        model["updated_by"] = actor[:256]
        _save()
        return _copy(record)


def evaluate_quality_gates(metrics: dict[str, Any], gates: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    results = []
    passed = True
    for metric_name, raw_gate in gates.items():
        gate = raw_gate if isinstance(raw_gate, dict) else {"min": raw_gate}
        value = metrics.get(metric_name)
        numeric_value = float(value) if isinstance(value, (int, float)) else None
        metric_passed = numeric_value is not None
        if metric_passed and gate.get("min") is not None:
            assert numeric_value is not None
            metric_passed = numeric_value >= float(gate["min"])
        if metric_passed and gate.get("max") is not None:
            assert numeric_value is not None
            metric_passed = numeric_value <= float(gate["max"])
        results.append({"metric": metric_name, "value": value, "gate": _copy(gate), "passed": metric_passed})
        passed = passed and metric_passed
    return passed, results


def create_model_evaluation(
    version_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        version = _require_version(version_id)
        model = _model_record(str(version["model_id"])) or {}
        metrics = _copy(payload.get("metrics") or {})
        gates = _copy(payload.get("quality_gates") or model.get("quality_gates") or {})
        passed, gate_results = evaluate_quality_gates(metrics, gates)
        timestamp = now_seconds()
        record = {
            "evaluation_id": new_id("evaluation"),
            "model_version_id": version_id,
            "dataset_id": str(payload.get("dataset_id") or "")[:256],
            "dataset_manifest_sha256": str(payload.get("dataset_manifest_sha256") or "")[:64],
            "definition_version": str(payload.get("definition_version") or "1.0")[:64],
            "environment": _copy(payload.get("environment") or {}),
            "thresholds": _copy(payload.get("thresholds") or version.get("thresholds") or {}),
            "metrics": metrics,
            "quality_gates": gates,
            "gate_results": gate_results,
            "passed": passed,
            "report_ref": str(payload.get("report_ref") or "")[:2000],
            "request_id": request_id,
            "created_at": timestamp,
            "created_by": actor[:256],
        }
        _STATE["evaluations"].append(record)
        if passed and version.get("status") == "draft":
            version["status"] = "candidate"
            version["updated_at"] = timestamp
            version["updated_by"] = actor[:256]
            version["version_counter"] = int(version.get("version_counter", 1)) + 1
        _save()
        return _copy(record)


def create_model_approval(
    version_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    with _LOCK:
        _require_version(version_id)
        decision = str(payload.get("decision") or "approve").lower()
        if decision not in {"approve", "reject"}:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="approval decision is invalid")
        policy = str(payload.get("policy") or "model_release")[:128]
        if any(
            item.get("model_version_id") == version_id
            and item.get("approver") == actor
            and item.get("policy") == policy
            and item.get("decision") == decision
            for item in _STATE["approvals"]
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="approver already submitted this decision")
        record = {
            "approval_id": new_id("approval"),
            "model_version_id": version_id,
            "approver": actor[:256],
            "decision": decision,
            "policy": policy,
            "comment": str(payload.get("comment") or "")[:2000],
            "request_id": request_id,
            "created_at": now_seconds(),
        }
        _STATE["approvals"].append(record)
        _save()
        return _copy(record)


def artifact_verification(version: dict[str, Any]) -> dict[str, Any]:
    model_target = str(version.get("model_target") or "")
    try:
        project_name, model_name = split_cache_key(model_target)
        path = get_model_path(project_name, model_name)
        actual_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        actual_size = Path(path).stat().st_size
        configured = model_target in MODEL_CONFIGS
        error = None
    except Exception as exc:
        actual_sha256 = None
        actual_size = None
        configured = False
        error = type(exc).__name__
    return {
        "configured": configured,
        "actual_sha256": actual_sha256,
        "expected_sha256": version.get("sha256"),
        "sha256_matches": actual_sha256 == version.get("sha256"),
        "actual_size": actual_size,
        "expected_size": version.get("artifact_size"),
        "error": error,
    }


def release_preflight(version_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        version = _copy(_require_version(version_id))
        action = str(payload.get("action") or "activate").lower()
        if action not in RELEASE_ACTIONS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="release action is invalid")
        alias_name = validate_alias_name(str(payload.get("alias") or ""))
        risk_level = str(payload.get("risk_level") or "high").lower()
        if risk_level not in RELEASE_RISK_LEVELS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="release risk level is invalid"
            )
        artifact = artifact_verification(version)
        evaluations = [item for item in _STATE["evaluations"] if item.get("model_version_id") == version_id]
        passing_evaluations = [item for item in evaluations if item.get("passed")]
        approvals = [
            item
            for item in _STATE["approvals"]
            if item.get("model_version_id") == version_id and item.get("decision") == "approve"
        ]
        release_actor = str(payload.get("release_actor") or "")
        distinct_approvers = sorted(
            {str(item.get("approver")) for item in approvals if str(item.get("approver")) != release_actor}
        )
        required_approvals = 2 if risk_level in {"high", "critical"} else 1
        blockers = []
        if version.get("status") in {"draft", "blocked", "deprecated"} and action in {"shadow", "canary", "activate"}:
            blockers.append("model version state is not releasable")
        if not artifact["configured"]:
            blockers.append("model target is not configured")
        if not artifact["sha256_matches"]:
            blockers.append("model artifact digest does not match")
        if not passing_evaluations:
            blockers.append("no passing model evaluation")
        if len(distinct_approvers) < required_approvals:
            blockers.append("release approval policy is not satisfied")
        if action in {"shadow", "canary"} and not MODEL_ALIASES.get(alias_name):
            blockers.append("shadow or canary release requires an existing stable alias target")
        if action == "rollback" and not version.get("rollback_target"):
            blockers.append("selected version is not an approved rollback target")
        if not version.get("license") or not version.get("source"):
            blockers.append("model provenance or license is missing")
        if not version.get("model_card_ref") or not version.get("governance_ref"):
            blockers.append("model card or governance sidecar is missing")
        alias_config = _copy(MODEL_ALIASES.get(alias_name))
        current_target = None
        if isinstance(alias_config, str):
            current_target = alias_config
        elif isinstance(alias_config, dict):
            current_target = alias_config.get("target")
        return {
            "ok": not blockers,
            "blockers": blockers,
            "warnings": [] if version.get("redistribution_allowed") else ["artifact redistribution is not approved"],
            "action": action,
            "risk_level": risk_level,
            "required_approvals": required_approvals,
            "approvers": distinct_approvers,
            "version": version,
            "artifact": artifact,
            "alias": alias_name,
            "current_target": current_target,
            "target": version["model_target"],
            "rollback_target": current_target,
        }


def record_release_event(
    version_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
    request_id: str,
    previous_target: str | None,
    outcome: str,
) -> dict[str, Any]:
    with _LOCK:
        version = _require_version(version_id)
        action = str(payload.get("action") or "activate").lower()
        target_status = {
            "shadow": "shadow",
            "canary": "canary",
            "activate": "active",
            "rollback": "active",
            "pause": "blocked",
            "deprecate": "deprecated",
        }.get(action)
        timestamp = now_seconds()
        if outcome == "success" and target_status:
            if target_status == "active":
                for other in _STATE["versions"]:
                    if (
                        other.get("model_id") == version.get("model_id")
                        and other.get("status") == "active"
                        and other is not version
                    ):
                        other["status"] = "deprecated"
                        other["rollback_target"] = True
            current_status = str(version.get("status") or "draft")
            if target_status != current_status and target_status not in MODEL_VERSION_TRANSITIONS.get(
                current_status, set()
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="model lifecycle transition is not allowed"
                )
            version["status"] = target_status
            version["updated_at"] = timestamp
            version["updated_by"] = actor[:256]
            version["version_counter"] = int(version.get("version_counter", 1)) + 1
        record = {
            "release_event_id": new_id("release"),
            "model_version_id": version_id,
            "model_id": version["model_id"],
            "action": action,
            "alias": str(payload.get("alias") or ""),
            "previous_target": previous_target,
            "target": version["model_target"],
            "risk_level": str(payload.get("risk_level") or "high"),
            "reason": str(payload.get("reason") or "")[:2000],
            "outcome": outcome,
            "request_id": request_id,
            "created_at": timestamp,
            "created_by": actor[:256],
        }
        _STATE["release_events"].append(record)
        _save()
        return _copy(record)


def list_release_events(*, limit: int | None = 100) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [_copy(item) for item in _STATE["release_events"]]
        ordered = sorted(rows, key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return ordered if limit is None else ordered[:limit]


load_model_registry_state()


__all__ = [
    "artifact_verification",
    "create_model_approval",
    "create_model_evaluation",
    "evaluate_quality_gates",
    "list_model_versions",
    "list_registered_models",
    "list_release_events",
    "load_model_registry_state",
    "model_registry_state_payload",
    "record_release_event",
    "register_model_version",
    "release_preflight",
    "reset_model_registry_state",
    "restore_model_registry_state",
]
