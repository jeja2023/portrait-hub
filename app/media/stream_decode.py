import ipaddress
import socket
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, status

from app.network_access_policy import host_is_allowed, network_access_policy_snapshot
from app.settings import (
    ALLOW_PRIVATE_STREAM_HOSTS,
    ALLOW_PRIVATE_WEBHOOK_HOSTS,
    STREAM_ALLOWED_CIDRS,
    STREAM_ALLOWED_HOSTS,
    WEBHOOK_ALLOWED_CIDRS,
    WEBHOOK_ALLOWED_HOSTS,
)

SUPPORTED_STREAM_SCHEMES = {"rtsp", "rtmp", "http", "https"}


def current_stream_network_policy() -> dict[str, Any]:
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
    return cast(dict[str, Any], policy["stream"])


def host_matches_allowlist(hostname: str) -> bool:
    return host_is_allowed(
        hostname,
        allowed_hosts=STREAM_ALLOWED_HOSTS,
        allowed_cidrs=STREAM_ALLOWED_CIDRS,
    )


def is_blocked_stream_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private_hosts: bool,
) -> bool:
    if allow_private_hosts:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def reject_blocked_stream_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private_hosts: bool,
) -> None:
    if is_blocked_stream_address(address, allow_private_hosts=allow_private_hosts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stream_url 主机被 SSRF 防护策略拒绝",
        )


def reject_private_ip_literal(hostname: str, *, allow_private_hosts: bool) -> None:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    reject_blocked_stream_address(address, allow_private_hosts=allow_private_hosts)


def resolve_stream_host_addresses(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
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


def reject_private_resolved_addresses(
    hostname: str,
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    *,
    allow_private_hosts: bool,
) -> None:
    if allow_private_hosts:
        return
    try:
        ipaddress.ip_address(hostname)
        return
    except ValueError:
        pass
    for address in addresses:
        reject_blocked_stream_address(address, allow_private_hosts=allow_private_hosts)


def validate_media_stream_url(stream_url: str) -> str:
    parsed = urlsplit(stream_url)
    if parsed.scheme not in SUPPORTED_STREAM_SCHEMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stream_url 必须使用 rtsp、rtmp、http 或 https",
        )
    if not parsed.hostname:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="stream_url 必须包含主机")
    policy = current_stream_network_policy()
    allow_private_hosts = bool(policy["allow_private_hosts"])
    allowed_hosts = list(policy["allowed_hosts"])
    allowed_cidrs = list(policy["allowed_cidrs"])
    reject_private_ip_literal(parsed.hostname, allow_private_hosts=allow_private_hosts)
    try:
        ipaddress.ip_address(parsed.hostname)
        resolved_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    except ValueError:
        resolved_addresses = resolve_stream_host_addresses(parsed.hostname)
    if not host_is_allowed(
        parsed.hostname,
        allowed_hosts=allowed_hosts,
        allowed_cidrs=allowed_cidrs,
        resolved_addresses=resolved_addresses,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stream_url 主机不在 STREAM_ALLOWED_HOSTS/STREAM_ALLOWED_CIDRS 网络访问策略允许范围内",
        )
    reject_private_resolved_addresses(
        parsed.hostname,
        resolved_addresses,
        allow_private_hosts=allow_private_hosts,
    )
    return stream_url


def revalidate_stream_url(stream_url: str) -> None:
    # 在解码器连接前立即重跑 SSRF 校验。这里会再次解析主机，缩小原始校验（在注册/请求时）
    # 与实际拉流之间的 DNS-rebinding（TOCTOU）窗口——对常驻流 worker 而言该窗口可能任意长。
    # 本地文件路径（无流 scheme）保持不动，不影响本地视频解码。
    parsed = urlsplit(stream_url)
    if parsed.scheme not in SUPPORTED_STREAM_SCHEMES:
        return
    validate_media_stream_url(stream_url)


def mask_stream_url(stream_url: str) -> str:
    parsed = urlsplit(stream_url)
    if not parsed.username and not parsed.password and not parsed.query and not parsed.fragment:
        return stream_url

    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    masked_netloc = f"***:***@{host}" if parsed.username or parsed.password else host
    query = "<redacted>" if parsed.query else ""
    fragment = "<redacted>" if parsed.fragment else ""
    return urlunsplit((parsed.scheme, masked_netloc, parsed.path, query, fragment))


__all__ = [
    "SUPPORTED_STREAM_SCHEMES",
    "current_stream_network_policy",
    "host_matches_allowlist",
    "is_blocked_stream_address",
    "mask_stream_url",
    "reject_blocked_stream_address",
    "reject_private_ip_literal",
    "reject_private_resolved_addresses",
    "resolve_stream_host_addresses",
    "revalidate_stream_url",
    "validate_media_stream_url",
]
