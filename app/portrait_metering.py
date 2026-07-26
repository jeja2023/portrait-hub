from __future__ import annotations

import copy
import hashlib
import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, TypeVar, cast
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status

from app.portrait_control_state import ControlStateBackend, ControlStateLock
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import PORTRAIT_METERING_STATE_PATH

METERING_DEFINITION_VERSION = "1.0"
USAGE_QUANTITIES = (
    "request_count",
    "image_count",
    "video_seconds",
    "gpu_seconds",
    "storage_byte_seconds",
    "network_egress_bytes",
    "third_party_units",
)
COST_PARAMETERS = (
    "request_unit_cost",
    "image_unit_cost",
    "video_second_cost",
    "gpu_second_cost",
    "storage_gb_month_cost",
    "network_gb_cost",
    "third_party_unit_cost",
)
OUTCOME_CATEGORIES = {"success", "business_rejection", "system_failure"}
DELIVERY_KINDS = {"original", "retry", "duplicate", "reversal"}
_EXCLUDED_PREFIXES = ("/v1/access/", "/v1/admin/", "/v1/auth/", "/health", "/ready", "/metrics")
_COLLECTIONS = ("usage_events", "cost_models")
_LOCK = ControlStateLock()
_T = TypeVar("_T")


def _empty_state() -> dict[str, Any]:
    return {"revision": 0, **{name: [] for name in _COLLECTIONS}}


_STATE = _empty_state()


def _copy(value: _T) -> _T:
    return copy.deepcopy(value)


def _validate_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        handle_state_read_error("metering state root must be an object")
        return _empty_state()
    state = _empty_state()
    try:
        state["revision"] = max(0, int(payload.get("revision", 0)))
    except (TypeError, ValueError):
        handle_state_read_error("metering state revision is invalid")
    for name in _COLLECTIONS:
        value = payload.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            handle_state_read_error(f"metering state collection is invalid: {name}")
            continue
        state[name] = _copy(value)
    return state


_BACKEND = ControlStateBackend("metering", _STATE, _LOCK.raw, _empty_state, _validate_state)
_LOCK.bind(_BACKEND)


def _save(*, increment: bool = True) -> None:
    if increment:
        _STATE["revision"] = int(_STATE.get("revision", 0)) + 1
    if _BACKEND.postgres_enabled():
        _BACKEND.save(actor="metering-control-plane")
    else:
        write_json_state(PORTRAIT_METERING_STATE_PATH, _STATE)


def load_metering_state() -> None:
    with _LOCK:
        if _BACKEND.postgres_enabled():
            return
        _STATE.clear()
        _STATE.update(_validate_state(read_json_state(PORTRAIT_METERING_STATE_PATH, _empty_state())))


def reset_metering_state(*, persist: bool = False) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(_empty_state())
        if persist:
            _save()
        elif _BACKEND.postgres_enabled():
            _BACKEND.invalidate()


def metering_state_payload() -> dict[str, Any]:
    with _LOCK:
        return _copy(_STATE)


def restore_metering_state(payload: dict[str, Any]) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(_validate_state(payload))
        _save(increment=False)


def _scope(record: dict[str, Any], tenant_id: str, project_id: str) -> bool:
    return record.get("tenant_id") == tenant_id and record.get("project_id") == project_id


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_quantities(payload: dict[str, Any], *, allow_negative: bool = False) -> dict[str, float]:
    quantities: dict[str, float] = {}
    for key in USAGE_QUANTITIES:
        try:
            value = float(payload.get(key) or 0.0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{key} is invalid") from exc
        if not allow_negative and value < 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{key} cannot be negative")
        quantities[key] = value
    if not any(value != 0 for value in quantities.values()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="usage quantities are empty")
    return quantities


def record_usage_event(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    with _LOCK:
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        idempotency_payload_sha256 = _canonical_sha256(payload)
        if idempotency_key:
            existing = next(
                (
                    item
                    for item in _STATE["usage_events"]
                    if _scope(item, tenant_id, project_id) and item.get("idempotency_key") == idempotency_key
                ),
                None,
            )
            if existing is not None:
                if existing.get("idempotency_payload_sha256") not in {None, idempotency_payload_sha256}:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="idempotency key was used with different usage data",
                    )
                return _copy(cast(dict[str, Any], existing))
        outcome = str(payload.get("outcome_category") or "success").strip().lower()
        delivery_kind = str(payload.get("delivery_kind") or "original").strip().lower()
        if outcome not in OUTCOME_CATEGORIES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="outcome category is invalid")
        if delivery_kind not in DELIVERY_KINDS - {"reversal"}:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="delivery kind is invalid")
        timestamp = time.time()
        quantities = _normalize_quantities(payload)
        event_without_hash = {
            "usage_event_id": f"usage_{uuid4().hex}",
            "definition_version": METERING_DEFINITION_VERSION,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "event_type": "usage",
            "event_time": float(payload.get("event_time") or timestamp),
            "received_at": timestamp,
            "request_id": str(payload.get("request_id") or "")[:128] or None,
            "application_id": str(payload.get("application_id") or "--")[:128],
            "capability": str(payload.get("capability") or "unknown")[:128],
            "model_version": str(payload.get("model_version") or "unknown")[:256],
            "endpoint": str(payload.get("endpoint") or "unknown")[:512],
            "resource_type": str(payload.get("resource_type") or "request")[:64],
            "outcome_category": outcome,
            "delivery_kind": delivery_kind,
            "quantities": quantities,
            "idempotency_key": idempotency_key[:256] or None,
            "idempotency_payload_sha256": idempotency_payload_sha256,
            "reverses_event_id": None,
            "reason": str(payload.get("reason") or "runtime metering event")[:1000],
            "created_by": actor[:256],
        }
        record = {**event_without_hash, "event_sha256": _canonical_sha256(event_without_hash)}
        _STATE["usage_events"].append(record)
        _save()
        return _copy(record)


def reverse_usage_event(
    tenant_id: str,
    project_id: str,
    usage_event_id: str,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    with _LOCK:
        original = next(
            (
                item
                for item in _STATE["usage_events"]
                if _scope(item, tenant_id, project_id) and item.get("usage_event_id") == usage_event_id
            ),
            None,
        )
        if original is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="usage event does not exist")
        if original.get("event_type") != "usage":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="reversal events cannot be reversed")
        existing = next(
            (
                item
                for item in _STATE["usage_events"]
                if _scope(item, tenant_id, project_id) and item.get("reverses_event_id") == usage_event_id
            ),
            None,
        )
        if existing is not None:
            return _copy(cast(dict[str, Any], existing))
        if not reason.strip():
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="reversal reason is required")
        timestamp = time.time()
        quantities = {key: -float(value) for key, value in dict(original.get("quantities") or {}).items()}
        event_without_hash = {
            **{key: original.get(key) for key in (
                "definition_version",
                "tenant_id",
                "project_id",
                "request_id",
                "application_id",
                "capability",
                "model_version",
                "endpoint",
                "resource_type",
                "outcome_category",
            )},
            "usage_event_id": f"usage_{uuid4().hex}",
            "event_type": "reversal",
            "event_time": timestamp,
            "received_at": timestamp,
            "delivery_kind": "reversal",
            "quantities": quantities,
            "idempotency_key": f"reversal:{usage_event_id}",
            "idempotency_payload_sha256": original.get("event_sha256"),
            "reverses_event_id": usage_event_id,
            "reason": reason.strip()[:1000],
            "created_by": actor[:256],
        }
        record = {**event_without_hash, "event_sha256": _canonical_sha256(event_without_hash)}
        _STATE["usage_events"].append(record)
        _save()
        return _copy(record)


def list_usage_events(
    tenant_id: str,
    project_id: str,
    *,
    created_since: float | None = None,
    created_until: float | None = None,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [
            _copy(item)
            for item in _STATE["usage_events"]
            if _scope(item, tenant_id, project_id)
            and (created_since is None or float(item.get("event_time") or 0) >= created_since)
            and (created_until is None or float(item.get("event_time") or 0) <= created_until)
        ]
    return sorted(rows, key=lambda item: (float(item.get("event_time") or 0), str(item.get("usage_event_id"))))


def create_cost_model(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    with _LOCK:
        version = str(payload.get("version") or "").strip()
        if not version:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cost model version is required")
        if any(_scope(item, tenant_id, project_id) and item.get("version") == version for item in _STATE["cost_models"]):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cost model version already exists")
        parameters: dict[str, float] = {}
        for key in COST_PARAMETERS:
            try:
                value = float(payload.get(key) or 0.0)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{key} is invalid") from exc
            if value < 0:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{key} cannot be negative")
            parameters[key] = value
        timestamp = time.time()
        record_without_hash = {
            "cost_model_id": f"cost_{uuid4().hex}",
            "definition_version": METERING_DEFINITION_VERSION,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "version": version[:64],
            "currency": str(payload.get("currency") or "CNY").strip().upper()[:3],
            "effective_at": float(payload.get("effective_at") or timestamp),
            "parameters": parameters,
            "created_at": timestamp,
            "created_by": actor[:256],
            "approved_by": str(payload.get("approved_by") or "")[:256],
            "reason": str(payload.get("reason") or "cost model version")[:1000],
        }
        record = {**record_without_hash, "model_sha256": _canonical_sha256(record_without_hash)}
        _STATE["cost_models"].append(record)
        _save()
        return _copy(record)


def list_cost_models(tenant_id: str, project_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [_copy(item) for item in _STATE["cost_models"] if _scope(item, tenant_id, project_id)]
    return sorted(rows, key=lambda item: (float(item.get("effective_at") or 0), str(item.get("version"))))


def _cost_model_for_event(models: list[dict[str, Any]], event_time: float) -> dict[str, Any] | None:
    eligible = [item for item in models if float(item.get("effective_at") or 0) <= event_time]
    return max(eligible, key=lambda item: float(item.get("effective_at") or 0)) if eligible else None


def _event_cost(event: dict[str, Any], model: dict[str, Any]) -> float:
    quantities = dict(event.get("quantities") or {})
    parameters = dict(model.get("parameters") or {})
    gib = 1024.0**3
    month_seconds = 30.0 * 86400.0
    return (
        float(quantities.get("request_count") or 0) * float(parameters.get("request_unit_cost") or 0)
        + float(quantities.get("image_count") or 0) * float(parameters.get("image_unit_cost") or 0)
        + float(quantities.get("video_seconds") or 0) * float(parameters.get("video_second_cost") or 0)
        + float(quantities.get("gpu_seconds") or 0) * float(parameters.get("gpu_second_cost") or 0)
        + float(quantities.get("storage_byte_seconds") or 0)
        / gib
        / month_seconds
        * float(parameters.get("storage_gb_month_cost") or 0)
        + float(quantities.get("network_egress_bytes") or 0)
        / gib
        * float(parameters.get("network_gb_cost") or 0)
        + float(quantities.get("third_party_units") or 0)
        * float(parameters.get("third_party_unit_cost") or 0)
    )


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="timezone is invalid") from exc


def _bucket_key(timestamp: float, *, timezone: str, granularity: str) -> str:
    local = datetime.fromtimestamp(timestamp, tz=UTC).astimezone(_timezone(timezone))
    return local.strftime("%Y-%m") if granularity == "month" else local.date().isoformat()


def aggregate_usage(
    tenant_id: str,
    project_id: str,
    *,
    created_since: float | None = None,
    created_until: float | None = None,
    timezone: str = "Asia/Shanghai",
    granularity: str = "day",
    budget_limit: float | None = None,
    budget_currency: str = "CNY",
) -> dict[str, Any]:
    if granularity not in {"day", "month"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="granularity is invalid")
    _timezone(timezone)
    events = list_usage_events(
        tenant_id,
        project_id,
        created_since=created_since,
        created_until=created_until,
    )
    models = list_cost_models(tenant_id, project_id)
    totals = {key: 0.0 for key in USAGE_QUANTITIES}
    outcome_counts: dict[str, float] = defaultdict(float)
    delivery_counts: dict[str, float] = defaultdict(float)
    dimensions: dict[str, dict[str, float]] = {
        "endpoint": defaultdict(float),
        "model_version": defaultdict(float),
        "capability": defaultdict(float),
        "resource_type": defaultdict(float),
        "application_id": defaultdict(float),
    }
    buckets: dict[str, dict[str, Any]] = {}
    total_cost = 0.0
    priced_events = 0
    unpriced_events = 0
    currencies: set[str] = set()
    for event in events:
        quantities = dict(event.get("quantities") or {})
        request_quantity = float(quantities.get("request_count") or 0)
        for key in USAGE_QUANTITIES:
            totals[key] += float(quantities.get(key) or 0)
        outcome_counts[str(event.get("outcome_category") or "unknown")] += request_quantity
        delivery_counts[str(event.get("delivery_kind") or "unknown")] += request_quantity
        for field in dimensions:
            dimensions[field][str(event.get(field) or "unknown")] += request_quantity
        key = _bucket_key(float(event.get("event_time") or 0), timezone=timezone, granularity=granularity)
        bucket = buckets.setdefault(
            key,
            {
                "period": key,
                "date": key,
                "request_count": 0.0,
                "success_count": 0.0,
                "business_rejection_count": 0.0,
                "system_failure_count": 0.0,
                "retry_count": 0.0,
                "duplicate_count": 0.0,
                "image_count": 0.0,
                "video_seconds": 0.0,
                "gpu_seconds": 0.0,
                "cost": 0.0,
            },
        )
        bucket["request_count"] += request_quantity
        outcome = str(event.get("outcome_category") or "unknown")
        if outcome == "success":
            bucket["success_count"] += request_quantity
        elif outcome == "business_rejection":
            bucket["business_rejection_count"] += request_quantity
        elif outcome == "system_failure":
            bucket["system_failure_count"] += request_quantity
        if event.get("delivery_kind") == "retry":
            bucket["retry_count"] += request_quantity
        elif event.get("delivery_kind") == "duplicate":
            bucket["duplicate_count"] += request_quantity
        for quantity_key in ("image_count", "video_seconds", "gpu_seconds"):
            bucket[quantity_key] += float(quantities.get(quantity_key) or 0)
        model = _cost_model_for_event(models, float(event.get("event_time") or 0))
        if model is None:
            unpriced_events += 1
            continue
        cost = _event_cost(event, model)
        priced_events += 1
        total_cost += cost
        bucket["cost"] += cost
        currencies.add(str(model.get("currency") or budget_currency))
    request_count = totals["request_count"]
    success_count = outcome_counts.get("success", 0.0)
    alert_status = "unconfigured"
    budget_utilization = None
    if budget_limit is not None and budget_limit >= 0 and len(currencies) <= 1:
        budget_utilization = total_cost / budget_limit if budget_limit else (1.0 if total_cost > 0 else 0.0)
        alert_status = "exceeded" if total_cost > budget_limit else "warning" if budget_utilization >= 0.8 else "ok"
    series = []
    for key in sorted(buckets):
        bucket = buckets[key]
        count = float(bucket["request_count"])
        bucket["success_rate"] = float(bucket["success_count"]) / count if count else 1.0
        bucket["cost"] = round(float(bucket["cost"]), 6)
        series.append(bucket)
    return {
        "definition_version": METERING_DEFINITION_VERSION,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "timezone": timezone,
        "granularity": granularity,
        "window": {"created_since": created_since, "created_until": created_until},
        "event_count": len(events),
        "request_count": request_count,
        "success_count": success_count,
        "error_count": request_count - success_count,
        "success_rate": success_count / request_count if request_count else 1.0,
        "quantities": totals,
        "outcomes": dict(sorted(outcome_counts.items())),
        "delivery_kinds": dict(sorted(delivery_counts.items())),
        "dimensions": {
            field: [{field: key, "request_count": value} for key, value in sorted(values.items())]
            for field, values in dimensions.items()
        },
        "cost": {
            "amount": round(total_cost, 6),
            "currency": next(iter(currencies), budget_currency),
            "priced_event_count": priced_events,
            "unpriced_event_count": unpriced_events,
            "status": "priced" if events and not unpriced_events else "partial" if priced_events else "unconfigured",
        },
        "budget": {
            "limit": budget_limit,
            "currency": budget_currency,
            "utilization": budget_utilization,
            "alert_status": alert_status,
            "threshold": 0.8,
        },
        "series": series,
        "source": "immutable_usage_events",
        "complete": True,
    }


def infer_resource_type(path: str) -> str:
    lowered = path.lower()
    if "video" in lowered or "stream" in lowered:
        return "video"
    if any(token in lowered for token in ("image", "analyze", "extract", "compare", "detect")):
        return "image"
    return "request"


def record_http_usage_event(
    *,
    tenant_id: str | None,
    project_id: str,
    request_id: str,
    application_id: str | None,
    method: str,
    path: str,
    status_code: int,
    event_time: float,
    delivery_kind: str = "original",
    dimensions: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not tenant_id or path.startswith(_EXCLUDED_PREFIXES):
        return None
    values = dict(dimensions or {})
    resource_type = str(values.get("resource_type") or infer_resource_type(path))
    quantities = {
        "request_count": 1.0,
        "image_count": float(values.get("image_count") or (1 if resource_type == "image" else 0)),
        "video_seconds": float(values.get("video_seconds") or 0),
        "gpu_seconds": float(values.get("gpu_seconds") or 0),
        "storage_byte_seconds": float(values.get("storage_byte_seconds") or 0),
        "network_egress_bytes": float(values.get("network_egress_bytes") or 0),
        "third_party_units": float(values.get("third_party_units") or 0),
    }
    outcome = "success" if status_code < 400 else "business_rejection" if status_code < 500 else "system_failure"
    return record_usage_event(
        tenant_id,
        project_id,
        {
            **quantities,
            "idempotency_key": f"http:{request_id}:{delivery_kind}",
            "request_id": request_id,
            "application_id": application_id or "--",
            "capability": values.get("capability") or "unknown",
            "model_version": values.get("model_version") or "unknown",
            "endpoint": f"{method.upper()} {path}",
            "resource_type": resource_type,
            "outcome_category": outcome,
            "delivery_kind": delivery_kind,
            "event_time": event_time,
            "reason": "http request metering",
        },
        actor="runtime-meter",
    )


load_metering_state()


__all__ = [
    "COST_PARAMETERS",
    "METERING_DEFINITION_VERSION",
    "USAGE_QUANTITIES",
    "aggregate_usage",
    "create_cost_model",
    "list_cost_models",
    "list_usage_events",
    "metering_state_payload",
    "record_http_usage_event",
    "record_usage_event",
    "reset_metering_state",
    "restore_metering_state",
    "reverse_usage_event",
]
