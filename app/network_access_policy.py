from __future__ import annotations

import ipaddress
import re
import threading
from copy import deepcopy
from typing import Any, Literal

from fastapi import HTTPException, status

from app.portrait_state import handle_state_read_error, read_json_state, write_json_state
from app.settings import RUNTIME_STATE_DIR

NETWORK_ACCESS_POLICY_PATH = RUNTIME_STATE_DIR / "network-access-policy.json"
NETWORK_ACCESS_POLICY_VERSION = 1
MAX_NETWORK_RULES = 512
MAX_HOST_RULE_LENGTH = 253
_HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_POLICY_LOCK = threading.RLock()

PolicyKind = Literal["stream", "webhook"]


def normalize_host_rule(value: Any) -> str:
    rule = str(value or "").strip().lower().rstrip(".")
    if not rule or len(rule) > MAX_HOST_RULE_LENGTH:
        raise ValueError("主机规则长度无效")
    if any(marker in rule for marker in ("://", "/", "?", "#", "@")):
        raise ValueError("主机规则只能填写域名或单个 IP")
    try:
        return ipaddress.ip_address(rule).compressed
    except ValueError:
        pass
    labels = rule.split(".")
    if any(not _HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise ValueError("主机规则格式无效")
    return rule


def normalize_cidr_rule(value: Any) -> str:
    rule = str(value or "").strip()
    if not rule:
        raise ValueError("CIDR 规则不能为空")
    try:
        return ipaddress.ip_network(rule, strict=False).with_prefixlen
    except ValueError as exc:
        raise ValueError("CIDR 规则格式无效") from exc


def normalize_rule_list(values: Any, *, cidr: bool) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("网络规则必须是列表")
    if len(values) > MAX_NETWORK_RULES:
        raise ValueError(f"网络规则不能超过 {MAX_NETWORK_RULES} 条")
    normalizer = normalize_cidr_rule if cidr else normalize_host_rule
    return sorted({normalizer(value) for value in values if str(value or "").strip()})


def normalize_endpoint_policy(payload: Any, *, require_private_allowlist: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("网络策略格式无效")
    allow_private_hosts = payload.get("allow_private_hosts", False)
    if not isinstance(allow_private_hosts, bool):
        raise ValueError("allow_private_hosts 必须是布尔值")
    policy = {
        "allow_private_hosts": allow_private_hosts,
        "allowed_hosts": normalize_rule_list(payload.get("allowed_hosts"), cidr=False),
        "allowed_cidrs": normalize_rule_list(payload.get("allowed_cidrs"), cidr=True),
    }
    if require_private_allowlist and allow_private_hosts and not policy["allowed_hosts"] and not policy["allowed_cidrs"]:
        raise ValueError("允许访问私网时必须配置至少一个主机或 CIDR 网段")
    return policy


def default_endpoint_policy(
    *,
    allow_private_hosts: bool,
    allowed_hosts: list[str],
    allowed_cidrs: list[str],
) -> dict[str, Any]:
    return normalize_endpoint_policy(
        {
            "allow_private_hosts": bool(allow_private_hosts),
            "allowed_hosts": list(allowed_hosts),
            "allowed_cidrs": list(allowed_cidrs),
        }
    )


def default_network_access_policy(
    *,
    stream: dict[str, Any],
    webhook: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": NETWORK_ACCESS_POLICY_VERSION,
        "revision": 0,
        "updated_at": None,
        "stream": normalize_endpoint_policy(stream),
        "webhook": normalize_endpoint_policy(webhook),
    }


def network_access_policy_snapshot(
    *,
    stream_default: dict[str, Any],
    webhook_default: dict[str, Any],
) -> dict[str, Any]:
    default = default_network_access_policy(stream=stream_default, webhook=webhook_default)
    with _POLICY_LOCK:
        raw = read_json_state(NETWORK_ACCESS_POLICY_PATH, None)
    if raw is None:
        return default
    if not isinstance(raw, dict) or raw.get("version") != NETWORK_ACCESS_POLICY_VERSION:
        handle_state_read_error("网络访问策略版本或格式无效")
        return default
    try:
        return {
            "version": NETWORK_ACCESS_POLICY_VERSION,
            "revision": max(0, int(raw.get("revision", 0))),
            "updated_at": raw.get("updated_at"),
            "stream": normalize_endpoint_policy(raw.get("stream")),
            "webhook": normalize_endpoint_policy(raw.get("webhook")),
        }
    except (TypeError, ValueError) as exc:
        handle_state_read_error("网络访问策略内容无效")
        raise AssertionError("unreachable") from exc


def save_network_access_policy(
    *,
    current: dict[str, Any],
    stream: dict[str, Any],
    webhook: dict[str, Any],
    updated_at: float,
    expected_revision: int,
) -> dict[str, Any]:
    with _POLICY_LOCK:
        latest = network_access_policy_snapshot(
            stream_default=current["stream"],
            webhook_default=current["webhook"],
        )
        current_revision = max(0, int(latest.get("revision", 0)))
        if expected_revision != current_revision:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="网络访问策略已被其他管理员修改，请刷新后重试")
        payload = {
            "version": NETWORK_ACCESS_POLICY_VERSION,
            "revision": current_revision + 1,
            "updated_at": float(updated_at),
            "stream": normalize_endpoint_policy(stream, require_private_allowlist=True),
            "webhook": normalize_endpoint_policy(webhook, require_private_allowlist=True),
        }
        write_json_state(NETWORK_ACCESS_POLICY_PATH, payload)
        try:
            NETWORK_ACCESS_POLICY_PATH.chmod(0o600)
        except OSError:
            pass
        return deepcopy(payload)


def restore_network_access_policy(payload: dict[str, Any] | None) -> None:
    with _POLICY_LOCK:
        if payload is None or int(payload.get("revision", 0)) == 0:
            try:
                NETWORK_ACCESS_POLICY_PATH.unlink(missing_ok=True)
            except OSError:
                pass
            return
        write_json_state(NETWORK_ACCESS_POLICY_PATH, payload)


def host_matches_rules(hostname: str, allowed_hosts: list[str]) -> bool:
    normalized = hostname.lower().rstrip(".")
    for allowed in allowed_hosts:
        allowed_host = allowed.lower().rstrip(".")
        try:
            ipaddress.ip_address(allowed_host)
            is_ip_rule = True
        except ValueError:
            is_ip_rule = False
        if normalized == allowed_host or (not is_ip_rule and normalized.endswith(f".{allowed_host}")):
            return True
    return False


def address_matches_cidrs(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    allowed_cidrs: list[str],
) -> bool:
    return any(address in ipaddress.ip_network(rule, strict=False) for rule in allowed_cidrs)


def host_is_allowed(
    hostname: str,
    *,
    allowed_hosts: list[str],
    allowed_cidrs: list[str],
    resolved_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] | None = None,
) -> bool:
    if not allowed_hosts and not allowed_cidrs:
        return True
    if host_matches_rules(hostname, allowed_hosts):
        return True
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return address_matches_cidrs(literal, allowed_cidrs)
    addresses = resolved_addresses or []
    return bool(addresses) and all(address_matches_cidrs(address, allowed_cidrs) for address in addresses)


__all__ = [
    "NETWORK_ACCESS_POLICY_PATH",
    "address_matches_cidrs",
    "default_endpoint_policy",
    "host_is_allowed",
    "network_access_policy_snapshot",
    "normalize_cidr_rule",
    "normalize_endpoint_policy",
    "normalize_host_rule",
    "restore_network_access_policy",
    "save_network_access_policy",
]
