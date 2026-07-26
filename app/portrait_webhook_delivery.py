from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from copy import deepcopy
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from app.observability import logger, wall_time
from app.portrait_access import (
    list_webhooks,
    require_webhook,
    validate_webhook_url,
    webhook_signing_secret,
)
from app.portrait_response import exception_log_summary
from app.portrait_state import read_json_state, write_json_state
from app.settings import WEBHOOK_DELIVERY_STATE_PATH

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {"version": 1, "deliveries": []}


class WebhookDeliveryNotFoundError(LookupError):
    pass


class WebhookDeliveryConflictError(RuntimeError):
    pass


def load_webhook_delivery_state() -> None:
    payload = read_json_state(WEBHOOK_DELIVERY_STATE_PATH, {"version": 1, "deliveries": []})
    rows = payload.get("deliveries", []) if isinstance(payload, dict) else []
    with _LOCK:
        _STATE.clear()
        _STATE.update({"version": 1, "deliveries": [row for row in rows if isinstance(row, dict)]})


def save_webhook_delivery_state() -> None:
    write_json_state(WEBHOOK_DELIVERY_STATE_PATH, deepcopy(_STATE))


def reset_webhook_delivery_state() -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update({"version": 1, "deliveries": []})


def delivery_id_for(webhook_id: str, event: str, resource_id: str) -> str:
    digest = hashlib.sha256(f"{webhook_id}\0{event}\0{resource_id}".encode()).hexdigest()[:24]
    return f"evt_{digest}"


def _public_delivery(record: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(record)
    public.pop("_event_data", None)
    public.setdefault("signature_status", "unknown")
    public.setdefault("next_retry_at", None)
    public.setdefault("dead_lettered_at", None)
    public.setdefault("dead_letter_reason", None)
    public.setdefault("manual_retry_count", 0)
    public["dead_letter"] = bool(
        public.get("dead_lettered_at") is not None or public.get("status") == "dead_letter"
    )
    attempts = public.get("attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if isinstance(attempt, dict):
                attempt.setdefault("signature_status", "unknown")
                attempt.setdefault("signed_at", None)
                attempt.setdefault("trigger", "legacy")
    return public


def list_webhook_deliveries(
    tenant_id: str,
    project_id: str,
    *,
    webhook_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [
            _public_delivery(row)
            for row in _STATE["deliveries"]
            if row.get("tenant_id") == tenant_id
            and row.get("project_id") == project_id
            and (webhook_id is None or row.get("webhook_id") == webhook_id)
            and (status is None or row.get("status") == status)
        ]
    rows.sort(key=lambda item: (-float(item.get("created_at") or 0), str(item.get("delivery_id") or "")))
    return rows[: max(1, min(int(limit), 500))]


def _find_delivery(delivery_id: str, tenant_id: str, project_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in _STATE["deliveries"]
            if row.get("delivery_id") == delivery_id
            and row.get("tenant_id") == tenant_id
            and row.get("project_id") == project_id
        ),
        None,
    )


def get_webhook_delivery(tenant_id: str, project_id: str, delivery_id: str) -> dict[str, Any]:
    with _LOCK:
        record = _find_delivery(delivery_id, tenant_id, project_id)
        if record is None:
            raise WebhookDeliveryNotFoundError("webhook delivery does not exist")
        return _public_delivery(record)


def _record_attempt(record: dict[str, Any], attempt: dict[str, Any]) -> None:
    with _LOCK:
        record["attempts"].append(attempt)
        record["attempt_count"] = len(record["attempts"])
        record["updated_at"] = wall_time()
        save_webhook_delivery_state()


def _attempt_trigger(*, manual_retry: bool, attempt_index: int, has_previous_attempts: bool) -> str:
    if manual_retry and attempt_index == 1:
        return "manual_retry"
    if attempt_index > 1 or has_previous_attempts:
        return "automatic_retry"
    return "initial"


def deliver_webhook_event(
    *,
    tenant_id: str,
    project_id: str,
    webhook_id: str,
    event: str,
    resource_id: str,
    request_id: str,
    data: dict[str, Any],
    sleep: Any = time.sleep,
    manual_retry: bool = False,
    retry_limit_override: int | None = None,
) -> dict[str, Any]:
    webhook = require_webhook(tenant_id, webhook_id, project_id)
    if webhook.get("status") != "active" or event not in webhook.get("events", []):
        raise ValueError("webhook is not active for this event")
    endpoint = validate_webhook_url(str(webhook.get("url") or ""), required=True)
    delivery_id = delivery_id_for(webhook_id, event, resource_id)
    with _LOCK:
        existing = _find_delivery(delivery_id, tenant_id, project_id)
        if existing is not None and existing.get("status") == "delivered":
            return {**_public_delivery(existing), "idempotent_replay": True}
        if existing is not None and existing.get("status") in {"delivering", "retrying"}:
            return {**_public_delivery(existing), "idempotent_replay": True}
        if existing is None:
            timestamp = wall_time()
            record = {
                "delivery_id": delivery_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "webhook_id": webhook_id,
                "event": event,
                "resource_id": resource_id,
                "request_id": request_id,
                "endpoint": endpoint,
                "status": "pending",
                "attempt_count": 0,
                "attempts": [],
                "signature_status": "pending",
                "signature_algorithm": "hmac-sha256",
                "next_retry_at": None,
                "dead_lettered_at": None,
                "dead_letter_reason": None,
                "manual_retry_count": 0,
                "last_manual_retry_at": None,
                "_event_data": deepcopy(data),
                "created_at": timestamp,
                "updated_at": timestamp,
                "delivered_at": None,
            }
            _STATE["deliveries"].append(record)
            save_webhook_delivery_state()
        else:
            record = existing

        record["status"] = "delivering"
        record["next_retry_at"] = None
        record["dead_lettered_at"] = None
        record["dead_letter_reason"] = None
        record.setdefault("_event_data", deepcopy(data))
        record["updated_at"] = wall_time()
        save_webhook_delivery_state()

    raw_created_at = record.get("created_at")
    created_at = float(raw_created_at) if isinstance(raw_created_at, (int, float, str)) else 0.0
    body = {
        "id": delivery_id,
        "event": event,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "request_id": request_id,
        "created_at": int(created_at),
        "data": deepcopy(data),
    }
    serialized = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    configured_retry_limit = webhook.get("retry_limit") if retry_limit_override is None else retry_limit_override
    retry_limit = max(0, min(int(configured_retry_limit or 0), 10))
    timeout_seconds = max(1, min(int(webhook.get("timeout_seconds") or 5), 60))
    secret = webhook_signing_secret(webhook)
    raw_attempts = record.get("attempts")
    base_attempt_count = len(raw_attempts) if isinstance(raw_attempts, list) else 0

    for attempt_index in range(1, retry_limit + 2):
        attempt_number = base_attempt_count + attempt_index
        signed_at = int(time.time())
        signature = hmac.new(
            secret.encode("utf-8"),
            str(signed_at).encode("ascii") + b"." + serialized,
            hashlib.sha256,
        ).hexdigest()
        signature_verified = hmac.compare_digest(
            signature,
            hmac.new(
                secret.encode("utf-8"),
                str(signed_at).encode("ascii") + b"." + serialized,
                hashlib.sha256,
            ).hexdigest(),
        )
        signature_status = "self_verified" if signature_verified else "verification_failed"
        with _LOCK:
            record["status"] = "delivering"
            record["signature_status"] = signature_status
            record["next_retry_at"] = None
            record["updated_at"] = wall_time()
            save_webhook_delivery_state()
        req = urllib_request.Request(
            endpoint,
            data=serialized,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": delivery_id,
                "User-Agent": "PortraitHub-Webhook/1.0",
                "X-PortraitHub-Delivery": delivery_id,
                "X-PortraitHub-Event": event,
                "X-PortraitHub-Signature": f"sha256={signature}",
                "X-PortraitHub-Timestamp": str(signed_at),
            },
        )
        attempt_started = wall_time()
        attempt: dict[str, Any]
        try:
            with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
                response_body = response.read(4096)
                status_code = int(getattr(response, "status", 200))
            success = 200 <= status_code < 300
            attempt = {
                "attempt": attempt_number,
                "started_at": attempt_started,
                "finished_at": wall_time(),
                "status_code": status_code,
                "success": success,
                "error_type": None,
                "response_bytes": len(response_body),
                "signature_status": signature_status,
                "signed_at": signed_at,
                "trigger": _attempt_trigger(
                    manual_retry=manual_retry,
                    attempt_index=attempt_index,
                    has_previous_attempts=base_attempt_count > 0,
                ),
            }
        except HTTPError as exc:
            response_body = exc.read(4096)
            success = False
            attempt = {
                "attempt": attempt_number,
                "started_at": attempt_started,
                "finished_at": wall_time(),
                "status_code": int(exc.code),
                "success": False,
                "error_type": "HTTPError",
                "response_bytes": len(response_body),
                "signature_status": signature_status,
                "signed_at": signed_at,
                "trigger": _attempt_trigger(
                    manual_retry=manual_retry,
                    attempt_index=attempt_index,
                    has_previous_attempts=base_attempt_count > 0,
                ),
            }
        except (URLError, TimeoutError, OSError) as exc:
            success = False
            attempt = {
                "attempt": attempt_number,
                "started_at": attempt_started,
                "finished_at": wall_time(),
                "status_code": None,
                "success": False,
                "error_type": type(exc).__name__,
                "response_bytes": 0,
                "signature_status": signature_status,
                "signed_at": signed_at,
                "trigger": _attempt_trigger(
                    manual_retry=manual_retry,
                    attempt_index=attempt_index,
                    has_previous_attempts=base_attempt_count > 0,
                ),
            }
        _record_attempt(record, attempt)
        if success:
            with _LOCK:
                record["status"] = "delivered"
                record["delivered_at"] = wall_time()
                record["updated_at"] = record["delivered_at"]
                record["next_retry_at"] = None
                record["dead_lettered_at"] = None
                record["dead_letter_reason"] = None
                save_webhook_delivery_state()
            return {**_public_delivery(record), "idempotent_replay": False}
        if attempt_index <= retry_limit:
            retry_delay = min(30.0, 0.25 * (2 ** (attempt_index - 1)))
            with _LOCK:
                record["status"] = "retrying"
                record["next_retry_at"] = wall_time() + retry_delay
                record["updated_at"] = wall_time()
                save_webhook_delivery_state()
            sleep(retry_delay)

    with _LOCK:
        record["status"] = "dead_letter"
        record["next_retry_at"] = None
        record["dead_lettered_at"] = wall_time()
        record["dead_letter_reason"] = "retry_exhausted"
        record["updated_at"] = record["dead_lettered_at"]
        save_webhook_delivery_state()
    return {**_public_delivery(record), "idempotent_replay": False}


def retry_webhook_delivery(
    tenant_id: str,
    project_id: str,
    delivery_id: str,
    *,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    with _LOCK:
        record = _find_delivery(delivery_id, tenant_id, project_id)
        if record is None:
            raise WebhookDeliveryNotFoundError("webhook delivery does not exist")
        if record.get("status") not in {"failed", "dead_letter"}:
            raise WebhookDeliveryConflictError("only failed or dead-letter webhook deliveries can be retried")
        event_data = record.get("_event_data")
        if not isinstance(event_data, dict):
            raise WebhookDeliveryConflictError("webhook delivery payload is unavailable for retry")
        webhook_id = str(record.get("webhook_id") or "")
        event = str(record.get("event") or "")
        resource_id = str(record.get("resource_id") or "")
        original_request_id = str(record.get("request_id") or "")

    webhook = require_webhook(tenant_id, webhook_id, project_id)
    if webhook.get("status") != "active" or event not in webhook.get("events", []):
        raise WebhookDeliveryConflictError("webhook is not active for this delivery event")

    with _LOCK:
        record = _find_delivery(delivery_id, tenant_id, project_id)
        if record is None:
            raise WebhookDeliveryNotFoundError("webhook delivery does not exist")
        if record.get("status") not in {"failed", "dead_letter"}:
            raise WebhookDeliveryConflictError("webhook delivery state changed before retry")
        record["manual_retry_count"] = int(record.get("manual_retry_count") or 0) + 1
        record["last_manual_retry_at"] = wall_time()
        record["status"] = "pending"
        record["next_retry_at"] = None
        record["dead_lettered_at"] = None
        record["dead_letter_reason"] = None
        record["updated_at"] = record["last_manual_retry_at"]
        save_webhook_delivery_state()

    try:
        return deliver_webhook_event(
            tenant_id=tenant_id,
            project_id=project_id,
            webhook_id=webhook_id,
            event=event,
            resource_id=resource_id,
            request_id=original_request_id,
            data=deepcopy(event_data),
            sleep=sleep,
            manual_retry=True,
            retry_limit_override=0,
        )
    except Exception:
        with _LOCK:
            record = _find_delivery(delivery_id, tenant_id, project_id)
            if record is not None and record.get("status") in {"pending", "delivering", "retrying"}:
                record["status"] = "dead_letter"
                record["next_retry_at"] = None
                record["dead_lettered_at"] = wall_time()
                record["dead_letter_reason"] = "manual_retry_setup_failed"
                record["updated_at"] = record["dead_lettered_at"]
                save_webhook_delivery_state()
        raise


def deliver_job_terminal_event(job: Any, request_id: str | None = None) -> list[dict[str, Any]]:
    event = "job.completed"
    deliveries: list[dict[str, Any]] = []
    for webhook in list_webhooks(job.tenant_id, None):
        if webhook.get("status") != "active" or event not in webhook.get("events", []):
            continue
        try:
            result = deliver_webhook_event(
                tenant_id=job.tenant_id,
                project_id=str(webhook.get("project_id") or "default"),
                webhook_id=str(webhook["webhook_id"]),
                event=event,
                resource_id=job.job_id,
                request_id=request_id or f"job_{job.job_id}",
                data={
                    "resource_type": "job",
                    "resource_id": job.job_id,
                    "status": str(job.status),
                    "terminal_reason": job.terminal_reason,
                    "progress": job.progress,
                },
            )
            deliveries.append(result)
        except Exception as exc:
            logger.warning(
                "webhook delivery failed: webhook_hash=%s job_hash=%s error=%s",
                hashlib.sha256(str(webhook.get("webhook_id") or "").encode()).hexdigest()[:12],
                hashlib.sha256(str(job.job_id).encode()).hexdigest()[:12],
                exception_log_summary(exc),
            )
    return deliveries


load_webhook_delivery_state()


__all__ = [
    "WebhookDeliveryConflictError",
    "WebhookDeliveryNotFoundError",
    "deliver_job_terminal_event",
    "deliver_webhook_event",
    "delivery_id_for",
    "get_webhook_delivery",
    "list_webhook_deliveries",
    "load_webhook_delivery_state",
    "reset_webhook_delivery_state",
    "retry_webhook_delivery",
]
