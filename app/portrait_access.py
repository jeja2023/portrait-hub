from __future__ import annotations

import copy
import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
import threading
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.network_access_policy import host_is_allowed, network_access_policy_snapshot
from app.portrait_auth import ROLE_PERMISSIONS
from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import (
    ALLOW_PRIVATE_STREAM_HOSTS,
    ALLOW_PRIVATE_WEBHOOK_HOSTS,
    PORTRAIT_ACCESS_KEY_ROTATION_GRACE_SECONDS,
    PORTRAIT_ACCESS_STATE_PATH,
    STREAM_ALLOWED_CIDRS,
    STREAM_ALLOWED_HOSTS,
    WEBHOOK_ALLOWED_CIDRS,
    WEBHOOK_ALLOWED_HOSTS,
)

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
    "access:read",
    "access:write",
    "tenants:read",
    "tenants:write",
}
_WEBHOOK_EVENTS = {
    "gallery.enrolled",
    "search.completed",
    "compare.completed",
    "job.completed",
    "stream.event",
    "model.rollout",
}
_ACCESS_STATE: dict[str, list[dict[str, Any]]] = {
    "tenants": [],
    "members": [],
    "applications": [],
    "webhooks": [],
}
_ACCESS_LOCK = threading.RLock()
_ACCESS_STATS_DIRTY = False


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


def validate_tenant_id(value: str, *, field_name: str = "tenant_id") -> str:
    cleaned = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", cleaned):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} 无效")
    return cleaned


def normalize_tenant_name(value: str | None, *, fallback: str | None = None) -> str:
    cleaned = str(value or "").strip()
    if not cleaned and fallback is not None:
        cleaned = str(fallback).strip()
    if not cleaned:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="租户名称不能为空")
    return cleaned[:256]


def normalize_member_phone(value: str | None) -> str:
    cleaned = re.sub(r"[\s()-]", "", str(value or "").strip())
    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"
    if re.fullmatch(r"1[3-9][0-9]{9}", cleaned):
        cleaned = f"+86{cleaned}"
    if not re.fullmatch(r"\+[1-9][0-9]{6,19}", cleaned):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="成员手机号无效")
    return cleaned


def normalize_member_roles(roles: list[str] | None) -> list[str]:
    values = sorted({str(item).strip() for item in (roles or []) if str(item).strip()})
    if not values:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="至少需要分配一个角色")
    if any(role not in ROLE_PERMISSIONS for role in values):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="包含不支持的角色")
    return values


def normalize_member_subject(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > 256:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="身份主体过长")
    return cleaned


def tenant_id_from_name(name: str) -> str:
    source = normalize_tenant_name(name)
    ascii_source = source.encode("ascii", "ignore").decode("ascii").lower()
    slug_core = re.sub(r"[^a-z0-9._:-]+", "-", ascii_source).strip("-_.:")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
    base = f"tenant-{slug_core}" if slug_core else f"tenant-{digest}"
    base = base[:64].rstrip("-_.:")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", base):
        base = f"tenant-{digest}"
    return base


def _tenant_record(tenant_id: str) -> dict[str, Any] | None:
    return next((item for item in _ACCESS_STATE["tenants"] if item.get("tenant_id") == tenant_id), None)


def _tenant_name_exists(name: str) -> bool:
    normalized = name.strip().casefold()
    return any(str(item.get("name") or "").strip().casefold() == normalized for item in _ACCESS_STATE["tenants"])


def _unique_tenant_id(base: str) -> str:
    normalized_base = validate_tenant_id(base)
    existing = {str(item.get("tenant_id") or "") for item in _ACCESS_STATE["tenants"]}
    existing.update(str(item.get("tenant_id") or "") for item in _ACCESS_STATE["applications"])
    existing.update(str(item.get("tenant_id") or "") for item in _ACCESS_STATE["webhooks"])
    existing.update(str(item.get("tenant_id") or "") for item in _ACCESS_STATE["members"])
    candidate = normalized_base
    serial = 2
    while candidate in existing:
        suffix = f"-{serial}"
        root = normalized_base[: 64 - len(suffix)].rstrip("-_.:") or "tenant"
        candidate = f"{root}{suffix}"
        serial += 1
    return candidate


def _ensure_tenant_record(tenant_id: str, *, name: str | None = None, status_value: str = "active") -> dict[str, Any]:
    normalized_id = validate_tenant_id(tenant_id)
    record = _tenant_record(normalized_id)
    if record is not None:
        return record
    timestamp = now_seconds()
    record = {
        "tenant_id": normalized_id,
        "name": normalize_tenant_name(name, fallback=normalized_id),
        "status": normalize_status(status_value),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    _ACCESS_STATE["tenants"].append(record)
    return record


def _backfill_tenants_from_resources() -> None:
    known = {str(item.get("tenant_id") or "") for item in _ACCESS_STATE["tenants"]}
    tenant_ids: set[str] = set()
    for collection_name in ("applications", "webhooks"):
        for item in _ACCESS_STATE[collection_name]:
            tenant_id = str(item.get("tenant_id") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", tenant_id) and tenant_id not in known:
                tenant_ids.add(tenant_id)
    for tenant_id in sorted(tenant_ids):
        timestamp = now_seconds()
        _ACCESS_STATE["tenants"].append(
            {
                "tenant_id": tenant_id,
                "name": tenant_id,
                "status": "active",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )


def public_tenant(record: dict[str, Any]) -> dict[str, Any]:
    tenant_id = str(record.get("tenant_id") or "")
    return {
        "tenant_id": tenant_id,
        "name": str(record.get("name") or tenant_id),
        "status": str(record.get("status") or "active"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "member_count": sum(1 for item in _ACCESS_STATE["members"] if item.get("tenant_id") == tenant_id),
        "application_count": sum(1 for item in _ACCESS_STATE["applications"] if item.get("tenant_id") == tenant_id),
        "webhook_count": sum(1 for item in _ACCESS_STATE["webhooks"] if item.get("tenant_id") == tenant_id),
    }


def list_tenants() -> list[dict[str, Any]]:
    with _ACCESS_LOCK:
        _backfill_tenants_from_resources()
        rows = [public_tenant(item) for item in _ACCESS_STATE["tenants"]]
        return sorted(rows, key=lambda item: str(item.get("tenant_id", "")))


def find_tenant(tenant_id: str) -> dict[str, Any] | None:
    with _ACCESS_LOCK:
        normalized_id = validate_tenant_id(tenant_id)
        record = _tenant_record(normalized_id)
        return public_tenant(record) if record is not None else None


def tenant_is_active(tenant_id: str) -> bool:
    with _ACCESS_LOCK:
        record = _tenant_record(validate_tenant_id(tenant_id))
        return record is None or record.get("status", "active") == "active"


def create_tenant(name: str, *, tenant_id: str | None = None, status_value: str = "active") -> dict[str, Any]:
    with _ACCESS_LOCK:
        tenant_name = normalize_tenant_name(name)
        if _tenant_name_exists(tenant_name):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="租户名称已存在")
        normalized_id = (
            validate_tenant_id(tenant_id) if tenant_id else _unique_tenant_id(tenant_id_from_name(tenant_name))
        )
        if _tenant_record(normalized_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="租户已存在")
        record = _ensure_tenant_record(normalized_id, name=tenant_name, status_value=status_value)
        save_access_state()
        return public_tenant(record)


def ensure_tenant(tenant_id: str, *, name: str | None = None, status_value: str = "active") -> dict[str, Any]:
    with _ACCESS_LOCK:
        record = _ensure_tenant_record(tenant_id, name=name, status_value=status_value)
        save_access_state()
        return public_tenant(record)


def update_tenant(tenant_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    with _ACCESS_LOCK:
        normalized_id = validate_tenant_id(tenant_id)
        record = _tenant_record(normalized_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租户不存在")
        if "name" in updates and updates["name"] is not None:
            name = normalize_tenant_name(updates["name"])
            if any(
                item is not record and str(item.get("name") or "").strip().casefold() == name.casefold()
                for item in _ACCESS_STATE["tenants"]
            ):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="租户名称已存在")
            record["name"] = name
        if "status" in updates and updates["status"] is not None:
            record["status"] = normalize_status(updates["status"])
        record["updated_at"] = now_seconds()
        save_access_state()
        return public_tenant(record)


def public_member(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_id": str(record.get("member_id") or ""),
        "tenant_id": str(record.get("tenant_id") or ""),
        "phone": str(record.get("phone") or ""),
        "display_name": str(record.get("display_name") or ""),
        "subject": record.get("subject"),
        "roles": list(record.get("roles") or []),
        "status": str(record.get("status") or "active"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def list_members(tenant_id: str | None = None) -> list[dict[str, Any]]:
    with _ACCESS_LOCK:
        normalized_tenant = validate_tenant_id(tenant_id) if tenant_id else None
        rows = [
            public_member(item)
            for item in _ACCESS_STATE["members"]
            if normalized_tenant is None or item.get("tenant_id") == normalized_tenant
        ]
        return sorted(rows, key=lambda item: (str(item.get("tenant_id")), str(item.get("phone"))))


def find_member(member_id: str) -> dict[str, Any] | None:
    with _ACCESS_LOCK:
        normalized_id = validate_access_id(member_id, field_name="member_id")
        record = next((item for item in _ACCESS_STATE["members"] if item.get("member_id") == normalized_id), None)
        return public_member(record) if record is not None else None


def resolve_member(
    tenant_id: str,
    *,
    subject: str | None = None,
    phone: str | None = None,
    bind_subject: bool = False,
) -> dict[str, Any] | None:
    with _ACCESS_LOCK:
        normalized_tenant = validate_tenant_id(tenant_id)
        normalized_subject = normalize_member_subject(subject)
        try:
            normalized_phone = normalize_member_phone(phone) if str(phone or "").strip() else ""
        except HTTPException:
            normalized_phone = ""
        tenant_members = [item for item in _ACCESS_STATE["members"] if item.get("tenant_id") == normalized_tenant]
        if normalized_subject:
            for item in tenant_members:
                item_subject = str(item.get("subject") or "")
                if item_subject and hmac.compare_digest(normalized_subject, item_subject):
                    return public_member(item)
        if not normalized_phone:
            return None
        for item in tenant_members:
            item_phone = str(item.get("phone") or "")
            if not item_phone or not hmac.compare_digest(normalized_phone, item_phone):
                continue
            item_subject = str(item.get("subject") or "")
            if normalized_subject and item_subject:
                continue
            if bind_subject and normalized_subject:
                item["subject"] = normalized_subject
                item["updated_at"] = now_seconds()
                save_access_state()
            return public_member(item)
        return None


def create_member(
    tenant_id: str,
    *,
    phone: str,
    display_name: str,
    roles: list[str],
    subject: str | None = None,
    status_value: str = "active",
) -> dict[str, Any]:
    with _ACCESS_LOCK:
        normalized_tenant = validate_tenant_id(tenant_id)
        if _tenant_record(normalized_tenant) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租户不存在")
        normalized_phone = normalize_member_phone(phone)
        if any(
            item.get("tenant_id") == normalized_tenant and str(item.get("phone") or "") == normalized_phone
            for item in _ACCESS_STATE["members"]
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该手机号已加入租户")
        normalized_subject = normalize_member_subject(subject)
        if normalized_subject and any(
            item.get("tenant_id") == normalized_tenant and item.get("subject") == normalized_subject
            for item in _ACCESS_STATE["members"]
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该身份主体已加入租户")
        timestamp = now_seconds()
        record = {
            "member_id": f"member_{secrets.token_hex(8)}",
            "tenant_id": normalized_tenant,
            "phone": normalized_phone,
            "display_name": str(display_name or normalized_phone).strip()[:256] or normalized_phone,
            "subject": normalized_subject,
            "roles": normalize_member_roles(roles),
            "status": normalize_status(status_value),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        _ACCESS_STATE["members"].append(record)
        save_access_state()
        return public_member(record)


def update_member(member_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    with _ACCESS_LOCK:
        normalized_id = validate_access_id(member_id, field_name="member_id")
        record = next((item for item in _ACCESS_STATE["members"] if item.get("member_id") == normalized_id), None)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成员不存在")
        if "phone" in updates and updates["phone"] is not None:
            normalized_phone = normalize_member_phone(updates["phone"])
            if any(
                item is not record
                and item.get("tenant_id") == record.get("tenant_id")
                and item.get("phone") == normalized_phone
                for item in _ACCESS_STATE["members"]
            ):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该手机号已加入租户")
            record["phone"] = normalized_phone
        if "display_name" in updates and updates["display_name"] is not None:
            display_name = str(updates["display_name"]).strip()
            if not display_name:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="成员姓名不能为空")
            record["display_name"] = display_name[:256]
        if "subject" in updates:
            normalized_subject = normalize_member_subject(updates["subject"])
            if normalized_subject and any(
                item is not record
                and item.get("tenant_id") == record.get("tenant_id")
                and item.get("subject") == normalized_subject
                for item in _ACCESS_STATE["members"]
            ):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该身份主体已加入租户")
            record["subject"] = normalized_subject
        if "roles" in updates and updates["roles"] is not None:
            record["roles"] = normalize_member_roles(updates["roles"])
        if "status" in updates and updates["status"] is not None:
            record["status"] = normalize_status(updates["status"])
        record["updated_at"] = now_seconds()
        save_access_state()
        return public_member(record)


def delete_member(member_id: str) -> dict[str, Any]:
    with _ACCESS_LOCK:
        normalized_id = validate_access_id(member_id, field_name="member_id")
        for index, item in enumerate(_ACCESS_STATE["members"]):
            if item.get("member_id") == normalized_id:
                removed = _ACCESS_STATE["members"].pop(index)
                save_access_state()
                return public_member(removed)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成员不存在")


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
    # 东八区 (UTC+8) 偏移量 28800 秒 (8小时)
    return time.strftime("%Y-%m-%d", time.gmtime(timestamp + 28800))


def seconds_until_next_quota_day(timestamp: float) -> int:
    # 东八区 (UTC+8) 每日重置点计算
    next_midnight = (int(timestamp + 28800) // 86400 + 1) * 86400 - 28800
    return max(1, next_midnight - int(timestamp))


def normalize_events(events: list[str] | None) -> list[str]:
    values = sorted({str(item).strip() for item in (events or []) if str(item).strip()})
    if not values:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="至少需要一个事件")
    unsupported = [item for item in values if item not in _WEBHOOK_EVENTS]
    if unsupported:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持的事件回调类型")
    return values


def _current_webhook_network_policy() -> dict[str, Any]:
    policy = network_access_policy_snapshot(
        stream_default={
            "allow_private_hosts": ALLOW_PRIVATE_STREAM_HOSTS,
            "allowed_hosts": STREAM_ALLOWED_HOSTS,
            "allowed_cidrs": STREAM_ALLOWED_CIDRS,
        },
        webhook_default={
            "allow_private_hosts": ALLOW_PRIVATE_WEBHOOK_HOSTS,
            "allowed_hosts": WEBHOOK_ALLOWED_HOSTS,
            "allowed_cidrs": WEBHOOK_ALLOWED_CIDRS,
        },
    )
    return policy["webhook"]


def _webhook_address_is_blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_webhook_host_addresses(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        raw_address = str(sockaddr[0])
        if raw_address in seen:
            continue
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            continue
        seen.add(raw_address)
        addresses.append(address)
    return addresses


def _reject_blocked_webhook_host(
    hostname: str,
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    *,
    allow_private_hosts: bool,
) -> None:
    # 防 SSRF：拒绝解析到内网/回环/链路本地等地址的回调主机。DNS 名称按其解析结果全部校验，
    # 以缩小 DNS-rebinding 窗口；ALLOW_PRIVATE_WEBHOOK_HOSTS 可在受控环境放行内网目标。
    if allow_private_hosts:
        return
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if _webhook_address_is_blocked(literal):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="事件回调 URL 主机被 SSRF 防护策略拒绝",
            )
        return
    for address in addresses:
        if _webhook_address_is_blocked(address):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="事件回调 URL 主机被 SSRF 防护策略拒绝",
            )


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
    policy = _current_webhook_network_policy()
    allow_private_hosts = bool(policy["allow_private_hosts"])
    allowed_hosts = list(policy["allowed_hosts"])
    allowed_cidrs = list(policy["allowed_cidrs"])
    try:
        ipaddress.ip_address(parsed.hostname)
        resolved_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    except ValueError:
        resolved_addresses = _resolve_webhook_host_addresses(parsed.hostname)
    if not host_is_allowed(
        parsed.hostname,
        allowed_hosts=allowed_hosts,
        allowed_cidrs=allowed_cidrs,
        resolved_addresses=resolved_addresses,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="事件回调 URL 主机不在 WEBHOOK_ALLOWED_HOSTS/WEBHOOK_ALLOWED_CIDRS 网络访问策略允许范围内",
        )
    _reject_blocked_webhook_host(
        parsed.hostname,
        resolved_addresses,
        allow_private_hosts=allow_private_hosts,
    )
    return url


def access_state_payload() -> dict[str, list[dict[str, Any]]]:
    with _ACCESS_LOCK:
        return copy.deepcopy(_ACCESS_STATE)


def restore_access_state(snapshot: dict[str, list[dict[str, Any]]]) -> None:
    with _ACCESS_LOCK:
        _ACCESS_STATE["tenants"] = copy.deepcopy(snapshot.get("tenants", []))
        _ACCESS_STATE["members"] = copy.deepcopy(snapshot.get("members", []))
        _ACCESS_STATE["applications"] = copy.deepcopy(snapshot.get("applications", []))
        _ACCESS_STATE["webhooks"] = copy.deepcopy(snapshot.get("webhooks", []))
        _backfill_tenants_from_resources()
        save_access_state()


def save_access_state() -> None:
    global _ACCESS_STATS_DIRTY
    with _ACCESS_LOCK:
        write_json_state(PORTRAIT_ACCESS_STATE_PATH, access_state_payload())
        _ACCESS_STATS_DIRTY = False


def flush_access_call_stats() -> bool:
    global _ACCESS_STATS_DIRTY
    with _ACCESS_LOCK:
        if not _ACCESS_STATS_DIRTY:
            return False
        payload = access_state_payload()
        _ACCESS_STATS_DIRTY = False
    try:
        write_json_state(PORTRAIT_ACCESS_STATE_PATH, payload)
    except Exception:
        with _ACCESS_LOCK:
            _ACCESS_STATS_DIRTY = True
        raise
    return True


def load_access_state() -> None:
    with _ACCESS_LOCK:
        payload = read_json_state(
            PORTRAIT_ACCESS_STATE_PATH,
            {"tenants": [], "members": [], "applications": [], "webhooks": []},
        )
        if not isinstance(payload, dict):
            handle_state_read_error("access state 根节点必须是映射")
            return
        tenants = payload.get("tenants", [])
        members = payload.get("members", [])
        applications = payload.get("applications", [])
        webhooks = payload.get("webhooks", [])
        if (
            not isinstance(tenants, list)
            or not isinstance(members, list)
            or not isinstance(applications, list)
            or not isinstance(webhooks, list)
        ):
            handle_state_read_error("access state lists must be arrays")
            return
        _ACCESS_STATE["tenants"] = [item for item in tenants if isinstance(item, dict)]
        _ACCESS_STATE["members"] = [item for item in members if isinstance(item, dict)]
        _ACCESS_STATE["applications"] = [item for item in applications if isinstance(item, dict)]
        _ACCESS_STATE["webhooks"] = [item for item in webhooks if isinstance(item, dict)]
        _backfill_tenants_from_resources()


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
            (
                item
                for item in _ACCESS_STATE["applications"]
                if item.get("tenant_id") == tenant_id and item.get("app_id") == normalized_id
            ),
            None,
        )


def require_application(tenant_id: str, app_id: str) -> dict[str, Any]:
    app = find_application(tenant_id, app_id)
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="接入应用不存在")
    return app


def list_applications(tenant_id: str) -> list[dict[str, Any]]:
    with _ACCESS_LOCK:
        rows = [
            public_application(item) for item in _ACCESS_STATE["applications"] if item.get("tenant_id") == tenant_id
        ]
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
        _ensure_tenant_record(tenant_id)
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
            "rate_limit_per_minute": normalize_optional_limit(
                rate_limit_per_minute, field_name="rate_limit_per_minute"
            ),
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
            record["rate_limit_per_minute"] = normalize_optional_limit(
                updates["rate_limit_per_minute"], field_name="rate_limit_per_minute"
            )
        if "rate_limit_burst" in updates:
            record["rate_limit_burst"] = normalize_optional_limit(
                updates["rate_limit_burst"], field_name="rate_limit_burst"
            )
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
        _ensure_tenant_record(tenant_id)
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
        if not tenant_is_active(tenant_id):
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


def application_record_for_key_any_tenant(api_key: str, timestamp: float | None = None) -> dict[str, Any] | None:
    with _ACCESS_LOCK:
        if not api_key:
            return None
        digest = secret_hash(api_key)
        current_time = now_seconds() if timestamp is None else timestamp
        matches: list[dict[str, Any]] = []
        for item in _ACCESS_STATE["applications"]:
            if item.get("status") != "active":
                continue
            if not tenant_is_active(str(item.get("tenant_id") or "")):
                continue
            if hmac.compare_digest(str(item.get("api_key_hash") or ""), digest):
                matches.append(item)
                continue
            for previous_hash in active_previous_api_key_hashes(item, current_time):
                if hmac.compare_digest(str(previous_hash.get("hash") or ""), digest):
                    matches.append(item)
                    break
        if len(matches) == 1:
            return matches[0]
        return None


def application_key_matches_any_tenant(api_key: str) -> dict[str, Any] | None:
    record = application_record_for_key_any_tenant(api_key)
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
    global _ACCESS_STATS_DIRTY
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
        _ACCESS_STATS_DIRTY = True


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
            (
                item
                for item in _ACCESS_STATE["webhooks"]
                if item.get("tenant_id") == tenant_id and item.get("webhook_id") == normalized_id
            ),
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
        next_status = (
            normalize_status(updates["status"])
            if "status" in updates and updates["status"] is not None
            else str(record.get("status") or "disabled")
        )
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
    global _ACCESS_STATS_DIRTY
    with _ACCESS_LOCK:
        _ACCESS_STATE["tenants"] = []
        _ACCESS_STATE["members"] = []
        _ACCESS_STATE["applications"] = []
        _ACCESS_STATE["webhooks"] = []
        _ACCESS_STATS_DIRTY = False


__all__ = [
    "access_state_payload",
    "application_key_matches",
    "application_key_matches_any_tenant",
    "application_request_policy",
    "application_scopes_allow_permission",
    "clear_access_state",
    "create_application",
    "create_member",
    "create_tenant",
    "create_webhook",
    "delete_member",
    "ensure_tenant",
    "find_member",
    "find_tenant",
    "flush_access_call_stats",
    "list_applications",
    "list_members",
    "list_tenants",
    "list_webhooks",
    "load_access_state",
    "record_application_call",
    "resolve_member",
    "restore_access_state",
    "rotate_application_secret",
    "rotate_webhook_secret",
    "tenant_id_from_name",
    "tenant_is_active",
    "update_application",
    "update_member",
    "update_tenant",
    "update_webhook",
    "webhook_sample_delivery",
]
