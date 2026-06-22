import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, status

from app.settings import ALLOW_PRIVATE_STREAM_HOSTS, STREAM_ALLOWED_HOSTS


SUPPORTED_STREAM_SCHEMES = {"rtsp", "rtmp", "http", "https"}


def host_matches_allowlist(hostname: str) -> bool:
    if not STREAM_ALLOWED_HOSTS:
        return True
    normalized = hostname.lower().rstrip(".")
    for allowed in STREAM_ALLOWED_HOSTS:
        allowed_host = allowed.lower().rstrip(".")
        if normalized == allowed_host or normalized.endswith(f".{allowed_host}"):
            return True
    return False


def is_blocked_stream_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ALLOW_PRIVATE_STREAM_HOSTS:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def reject_blocked_stream_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if is_blocked_stream_address(address):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stream_url host is not allowed by SSRF protection",
        )


def reject_private_ip_literal(hostname: str) -> None:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    reject_blocked_stream_address(address)


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


def reject_private_resolved_addresses(hostname: str) -> None:
    if ALLOW_PRIVATE_STREAM_HOSTS:
        return
    try:
        ipaddress.ip_address(hostname)
        return
    except ValueError:
        pass
    for address in resolve_stream_host_addresses(hostname):
        reject_blocked_stream_address(address)


def validate_media_stream_url(stream_url: str) -> str:
    parsed = urlsplit(stream_url)
    if parsed.scheme not in SUPPORTED_STREAM_SCHEMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stream_url must use rtsp, rtmp, http, or https",
        )
    if not parsed.hostname:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="stream_url must include host")
    reject_private_ip_literal(parsed.hostname)
    if not host_matches_allowlist(parsed.hostname):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stream_url host is not in STREAM_ALLOWED_HOSTS",
        )
    reject_private_resolved_addresses(parsed.hostname)
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
    "host_matches_allowlist",
    "is_blocked_stream_address",
    "reject_blocked_stream_address",
    "reject_private_ip_literal",
    "resolve_stream_host_addresses",
    "reject_private_resolved_addresses",
    "validate_media_stream_url",
    "revalidate_stream_url",
    "mask_stream_url",
]
