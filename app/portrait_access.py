from __future__ import annotations

import copy
import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import PORTRAIT_ACCESS_KEY_ROTATION_GRACE_SECONDS, PORTRAIT_ACCESS_STATE_PATH


_ACCESS_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_ACCESS_STATUS_VALUES = {"active", "disabled"}
_APPLICATION_SCOPES = {
    "infer",
    "compare",
    "gallery:read",
    "gallery:write",
    "jobs",
    "jobs:read",
    "streams",
    "streams:read",
    "models:read",
    "models:write",
    "thresholds:write",
    "admin:status",
    "metrics:read",
}
_WEBHOOK_EVENTS = {
    "gallery.enrolled",
    "search.completed",
    "compare.completed",
    "job.completed",
    "stream.event",
    "model.rollout",
}
_ACCESS_STATE: dict[str, list[dict[str, Any]]] = {"applications": [], "webhooks": []}
_ACCESS_LOCK = threading.RLock()


def now_seconds() -> float:
    return time.time()


def new_secret(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secret_preview(secret: str) -> str:
    if len(secret) <= 12:
        return "<redacted>"
    return f"{secret[:8]}...{secret[-4:]}"


def validate_access_id(value: str, *, field_name: str = "id") -> str:
    cleaned = str(value or "").strip()
    if not _ACCESS_ID_PATTERN.fullmatch(cleaned):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} 无效")
    return cleaned


def normalize_status(value: str | None) -> str:
    status_value = str(value or "active").strip().lower()
    if status_value not in _ACCESS_STATUS_VALUES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的接入状态")
    return status_value


def normalize_scopes(scopes: list[str] | None) -> list[str]:
    values = sorted({str(item).strip() for item in (scopes or []) if str(item).strip()})
    if not values:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="至少需要一个权限范围")
    unsupported = [item for item in values if item not in _APPLICATION_SCOPES]
    if unsupported:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的权限范围")
    return values

def normalize_optional_limit(value: Any, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} 无效") from exc
    if limit < 0 or limit > 1_000_000_000:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} 无效")
    return limit


def configured_positive_limit(value: Any) -> int | None:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    return limit if limit > 0 else None


def quota_date(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(timestamp))


def seconds_until_next_quota_day(timestamp: float) -> int:
    next_midnight = (int(timestamp) // 86400 + 1) * 86400
    return max(1, next_midnight - int(timestamp))


def normalize_events(events: list[str] | None) -> list[str]:
    values = sorted({str(item).strip() for item in (events or []) if str(item).strip()})
    if not values:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="至少需要一个事件")
    unsupported = [item for item in values if item not in _WEBHOOK_EVENTS]
    if unsupported:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的事件回调类型")
    return values


def validate_webhook_url(value: str | None, *, required: bool) -> str:
    url = str(value or "").strip()
    if not url:
        if required:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="事件回调 URL 为必填项")
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="事件回调 URL 无效")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="事件回调 URL 不能包含凭证")
    if len(url) > 2048:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="事件回调 URL 过长")
    return url


def access_state_payload() -> dict[str, list[dict[str, Any]]]:
    with _ACCESS_LOCK:
        return copy.deepcopy(_ACCESS_STATE)


def restore_access_state(snapshot: dict[str, list[dict[str, Any]]]) -> None:
    with _ACCESS_LOCK:
        _ACCESS_STATE["applications"] = copy.deepcopy(snapshot.get("applications", []))
        _ACCESS_STATE["webhooks"] = copy.deepcopy(snapshot.get("webhooks", []))
        save_access_state()


def save_access_state() -> None:
    with _ACCESS_LOCK:
        write_json_state(PORTRAIT_ACCESS_STATE_PATH, access_state_payload())


def load_access_state() -> None:
    with _ACCESS_LOCK:
        payload = read_json_state(PORTRAIT_ACCESS_STATE_PATH, {"applications": [], "webhooks": []})
        if not isinstance(payload, dict):
            handle_state_read_error("access state 根节点必须是映射")
            return
        applications = payload.get("applications", [])
        webhooks = payload.get("webhooks", [])
        if not isinstance(applications, list) or not isinstance(webhooks, list):
            handle_state_read_error("access state lists must be arrays")
            return
        _ACCESS_STATE["applications"] = [item for item in applications if isinstance(item, dict)]
        _ACCESS_STATE["webhooks"] = [item for item in webhooks if isinstance(item, dict)]


def public_application(record: dict[str, Any]) -> dict[str, Any]:
    output = {key: value for key, value in record.items() if key not in {"api_key_hash", "previous_api_key_hashes"}}
    output.setdefault("api_key_preview", None)
    return output


def public_webhook(record: dict[str, Any]) -> dict[str, Any]:
    output = {key: value for key, value in record.items() if key != "signing_secret_hash"}
    output.setdefault("signing_secret_preview", None)
    return output


def find_application(tenant_id: str, app_id: str) -> dict[str, Any] | None:
    with _ACCESS_LOCK:
        normalized_id = validate_access_id(app_id, field_name="app_id")
        return next(
            (item for item in _ACCESS_STATE["applications"] if item.get("tenant_id") == tenant_id and item.get("app_id") == normalized_id),
            None,
        )


def require_application(tenant_id: str, app_id: str) -> dict[str, Any]:
    app = find_application(tenant_id, app_id)
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="接入应用不存在")
    return app


def list_applications(tenant_id: str) -> list[dict[str, Any]]:
    with _ACCESS_LOCK:
        rows = [public_application(item) for item in _ACCESS_STATE["applications"] if item.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda item: str(item.get("app_id", "")))


def create_application(
    tenant_id: str,
    *,
    app_id: str,
    name: str,
    owner: str,
    status_value: str,
    scopes: list[str],
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    rate_limit_per_minute: int | None = None,
    rate_limit_burst: int | None = None,
    daily_quota: int | None = None,
) -> tuple[dict[str, Any], str]:
    with _ACCESS_LOCK:
        normalized_id = validate_access_id(app_id, field_name="app_id")
        if find_application(tenant_id, normalized_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="接入应用已存在")
        secret = new_secret("phk")
        timestamp = now_seconds()
        record = {
            "tenant_id": tenant_id,
            "app_id": normalized_id,
            "name": (name or normalized_id).strip()[:256] or normalized_id,
            "owner": (owner or "platform").strip()[:256] or "platform",
            "status": normalize_status(status_value),
            "scopes": normalize_scopes(scopes),
            "jwt_issuer": (jwt_issuer or "").strip()[:256],
            "jwt_audience": (jwt_audience or "").strip()[:256],
            "rate_limit_per_minute": normalize_optional_limit(rate_limit_per_minute, field_name="rate_limit_per_minute"),
            "rate_limit_burst": normalize_optional_limit(rate_limit_burst, field_name="rate_limit_burst"),
            "daily_quota": normalize_optional_limit(daily_quota, field_name="daily_quota"),
            "quota_date": quota_date(timestamp),
            "daily_quota_used": 0,
            "api_key_hash": secret_hash(secret),
            "previous_api_key_hashes": [],
            "api_key_preview": secret_preview(secret),
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_rotated_at": timestamp,
            "last_called_at": None,
            "last_error_at": None,
            "call_count": 0,
            "error_count": 0,
            "error_rate": 0.0,
        }
        _ACCESS_STATE["applications"].append(record)
        save_access_state()
        return public_application(record), secret


def update_application(tenant_id: str, app_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    with _ACCESS_LOCK:
        record = require_application(tenant_id, app_id)
        if "name" in updates and updates["name"] is not None:
            record["name"] = str(updates["name"]).strip()[:256] or record["app_id"]
        if "owner" in updates and updates["owner"] is not None:
            record["owner"] = str(updates["owner"]).strip()[:256] or "platform"
        if "status" in updates and updates["status"] is not None:
            record["status"] = normalize_status(updates["status"])
        if "scopes" in updates and updates["scopes"] is not None:
            record["scopes"] = normalize_scopes(updates["scopes"])
        if "jwt_issuer" in updates and updates["jwt_issuer"] is not None:
            record["jwt_issuer"] = str(updates["jwt_issuer"]).strip()[:256]
        if "jwt_audience" in updates and updates["jwt_audience"] is not None:
            record["jwt_audience"] = str(updates["jwt_audience"]).strip()[:256]
        if "rate_limit_per_minute" in updates:
            record["rate_limit_per_minute"] = normalize_optional_limit(updates["rate_limit_per_minute"], field_name="rate_limit_per_minute")
        if "rate_limit_burst" in updates:
            record["rate_limit_burst"] = normalize_optional_limit(updates["rate_limit_burst"], field_name="rate_limit_burst")
        if "daily_quota" in updates:
            record["daily_quota"] = normalize_optional_limit(updates["daily_quota"], field_name="daily_quota")
            record["quota_date"] = quota_date(now_seconds())
            record["daily_quota_used"] = 0
        record["updated_at"] = now_seconds()
        save_access_state()
        return public_application(record)


def active_previous_api_key_hashes(record: dict[str, Any], timestamp: float) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    raw_items = record.get("previous_api_key_hashes") or []
    if not isinstance(raw_items, list):
        return active
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        hash_value = raw_item.get("hash")
        expires_at = raw_item.get("expires_at")
        if isinstance(hash_value, str) and isinstance(expires_at, (int, float)) and float(expires_at) > timestamp:
            active.append({"hash": hash_value, "expires_at": float(expires_at)})
    return active


def rotate_application_secret(tenant_id: str, app_id: str) -> tuple[dict[str, Any], str]:
    with _ACCESS_LOCK:
        record = require_application(tenant_id, app_id)
        secret = new_secret("phk")
        timestamp = now_seconds()
        previous_hashes = active_previous_api_key_hashes(record, timestamp)
        old_hash = str(record.get("api_key_hash") or "")
        grace_seconds = max(0.0, float(PORTRAIT_ACCESS_KEY_ROTATION_GRACE_SECONDS))
        if old_hash and grace_seconds > 0:
            previous_hashes.append({"hash": old_hash, "expires_at": timestamp + grace_seconds})
        record["api_key_hash"] = secret_hash(secret)
        record["previous_api_key_hashes"] = previous_hashes[-5:]
        record["api_key_preview"] = secret_preview(secret)
        record["last_rotated_at"] = timestamp
        record["updated_at"] = timestamp
        save_access_state()
        return public_application(record), secret


def application_record_for_key(tenant_id: str, api_key: str, timestamp: float | None = None) -> dict[str, Any] | None:
    with _ACCESS_LOCK:
        if not api_key:
            return None
        digest = secret_hash(api_key)
        current_time = now_seconds() if timestamp is None else timestamp
        for item in _ACCESS_STATE["applications"]:
            if item.get("tenant_id") != tenant_id or item.get("status") != "active":
                continue
            if hmac.compare_digest(str(item.get("api_key_hash") or ""), digest):
                return item
            for previous_hash in active_previous_api_key_hashes(item, current_time):
                if hmac.compare_digest(str(previous_hash.get("hash") or ""), digest):
                    return item
        return None


def application_key_matches(tenant_id: str, api_key: str) -> dict[str, Any] | None:
    record = application_record_for_key(tenant_id, api_key)
    return public_application(record) if record is not None else None


def application_request_policy(tenant_id: str, api_key: str, timestamp: float | None = None) -> dict[str, Any] | None:
    with _ACCESS_LOCK:
        current_time = now_seconds() if timestamp is None else timestamp
        record = application_record_for_key(tenant_id, api_key, current_time)
        if record is None:
            return None
        daily_quota = configured_positive_limit(record.get("daily_quota"))
        if daily_quota is not None:
            current_date = quota_date(current_time)
            if record.get("quota_date") != current_date:
                record["quota_date"] = current_date
                record["daily_quota_used"] = 0
            used = configured_positive_limit(record.get("daily_quota_used")) or 0
            if used >= daily_quota:
                save_access_state()
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="每日配额已耗尽",
                    headers={"Retry-After": str(seconds_until_next_quota_day(current_time))},
                )
            record["daily_quota_used"] = used + 1
        if daily_quota is not None:
            save_access_state()
        return public_application(record)



def record_application_call(tenant_id: str | None, app_id: str | None, status_code: int, timestamp: float) -> None:
    if not tenant_id or not app_id or app_id == "--":
        return
    with _ACCESS_LOCK:
        record = next(
            (
                item
                for item in _ACCESS_STATE["applications"]
                if item.get("tenant_id") == tenant_id and item.get("app_id") == app_id
            ),
            None,
        )
        if record is None:
            return
        try:
            call_count = int(record.get("call_count") or 0) + 1
        except (TypeError, ValueError):
            call_count = 1
        try:
            error_count = int(record.get("error_count") or 0)
        except (TypeError, ValueError):
            error_count = 0
        if status_code >= 400:
            error_count += 1
            record["last_error_at"] = timestamp
        record["last_called_at"] = timestamp
        record["call_count"] = call_count
        record["error_count"] = error_count
        record["error_rate"] = round(error_count / call_count, 6) if call_count > 0 else 0.0
        save_access_state()


def application_scopes_allow_permission(scopes: Any, permission: str) -> bool:
    if not isinstance(scopes, list):
        return False
    normalized = {str(item).strip() for item in scopes if str(item).strip()}
    root_permission = permission.split(":", 1)[0]
    return "*" in normalized or permission in normalized or root_permission in normalized


def find_webhook(tenant_id: str, webhook_id: str) -> dict[str, Any] | None:
    with _ACCESS_LOCK:
        normalized_id = validate_access_id(webhook_id, field_name="webhook_id")
        return next(
            (item for item in _ACCESS_STATE["webhooks"] if item.get("tenant_id") == tenant_id and item.get("webhook_id") == normalized_id),
            None,
        )


def require_webhook(tenant_id: str, webhook_id: str) -> dict[str, Any]:
    webhook = find_webhook(tenant_id, webhook_id)
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="事件回调不存在")
    return webhook


def list_webhooks(tenant_id: str) -> list[dict[str, Any]]:
    with _ACCESS_LOCK:
        rows = [public_webhook(item) for item in _ACCESS_STATE["webhooks"] if item.get("tenant_id") == tenant_id]
        return sorted(rows, key=lambda item: str(item.get("webhook_id", "")))


def create_webhook(
    tenant_id: str,
    *,
    webhook_id: str,
    name: str,
    application_id: str,
    url: str | None,
    status_value: str,
    events: list[str],
    retry_limit: int,
    timeout_seconds: int,
) -> tuple[dict[str, Any], str]:
    with _ACCESS_LOCK:
        normalized_id = validate_access_id(webhook_id, field_name="webhook_id")
        if find_webhook(tenant_id, normalized_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="事件回调已存在")
        if find_application(tenant_id, application_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="接入应用不存在")
        status_text = normalize_status(status_value)
        secret = new_secret("whsec")
        timestamp = now_seconds()
        record = {
            "tenant_id": tenant_id,
            "webhook_id": normalized_id,
            "name": (name or normalized_id).strip()[:256] or normalized_id,
            "application_id": validate_access_id(application_id, field_name="app_id"),
            "url": validate_webhook_url(url, required=status_text == "active"),
            "status": status_text,
            "events": normalize_events(events),
            "retry_limit": max(0, min(int(retry_limit), 10)),
            "timeout_seconds": max(1, min(int(timeout_seconds), 60)),
            "signing_secret_hash": secret_hash(secret),
            "signing_secret_preview": secret_preview(secret),
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_rotated_at": timestamp,
            "last_delivery_at": None,
            "failure_count": 0,
        }
        _ACCESS_STATE["webhooks"].append(record)
        save_access_state()
        return public_webhook(record), secret


def update_webhook(tenant_id: str, webhook_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    with _ACCESS_LOCK:
        record = require_webhook(tenant_id, webhook_id)
        next_status = normalize_status(updates["status"]) if "status" in updates and updates["status"] is not None else str(record.get("status") or "disabled")
        if "application_id" in updates and updates["application_id"] is not None:
            if find_application(tenant_id, updates["application_id"]) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="接入应用不存在")
            record["application_id"] = validate_access_id(updates["application_id"], field_name="app_id")
        if "name" in updates and updates["name"] is not None:
            record["name"] = str(updates["name"]).strip()[:256] or record["webhook_id"]
        if "url" in updates and updates["url"] is not None:
            record["url"] = validate_webhook_url(updates["url"], required=next_status == "active")
        if "events" in updates and updates["events"] is not None:
            record["events"] = normalize_events(updates["events"])
        if "retry_limit" in updates and updates["retry_limit"] is not None:
            record["retry_limit"] = max(0, min(int(updates["retry_limit"]), 10))
        if "timeout_seconds" in updates and updates["timeout_seconds"] is not None:
            record["timeout_seconds"] = max(1, min(int(updates["timeout_seconds"]), 60))
        record["status"] = next_status
        if record["status"] == "active":
            record["url"] = validate_webhook_url(record.get("url"), required=True)
        record["updated_at"] = now_seconds()
        save_access_state()
        return public_webhook(record)


def rotate_webhook_secret(tenant_id: str, webhook_id: str) -> tuple[dict[str, Any], str]:
    with _ACCESS_LOCK:
        record = require_webhook(tenant_id, webhook_id)
        secret = new_secret("whsec")
        timestamp = now_seconds()
        record["signing_secret_hash"] = secret_hash(secret)
        record["signing_secret_preview"] = secret_preview(secret)
        record["last_rotated_at"] = timestamp
        record["updated_at"] = timestamp
        save_access_state()
        return public_webhook(record), secret


def webhook_sample_delivery(tenant_id: str, webhook_id: str) -> dict[str, Any]:
    with _ACCESS_LOCK:
        record = require_webhook(tenant_id, webhook_id)
        event = (record.get("events") or ["job.completed"])[0]
        event_id = f"evt_{secrets.token_hex(12)}"
        body = {
            "id": event_id,
            "event": event,
            "tenant_id": tenant_id,
            "request_id": f"req_{secrets.token_hex(8)}",
            "created_at": now_seconds(),
            "data": {
                "status": "success",
                "resource_type": str(event).split(".", 1)[0],
                "resource_id": f"demo_{secrets.token_hex(6)}",
            },
        }
        return {
            "delivery_status": "dry_run",
            "endpoint": record.get("url") or "",
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-PortraitHub-Event": event,
                "X-PortraitHub-Delivery": event_id,
                "X-PortraitHub-Signature": f"sha256={secrets.token_hex(32)}",
            },
            "body": body,
            "retry_limit": record.get("retry_limit", 3),
            "timeout_seconds": record.get("timeout_seconds", 5),
        }


def clear_access_state() -> None:
    with _ACCESS_LOCK:
        _ACCESS_STATE["applications"] = []
        _ACCESS_STATE["webhooks"] = []


__all__ = [
    "access_state_payload",
    "application_key_matches",
    "application_request_policy",
    "application_scopes_allow_permission",
    "clear_access_state",
    "create_application",
    "create_webhook",
    "list_applications",
    "list_webhooks",
    "load_access_state",
    "restore_access_state",
    "record_application_call",
    "rotate_application_secret",
    "rotate_webhook_secret",
    "update_application",
    "update_webhook",
    "webhook_sample_delivery",
]
