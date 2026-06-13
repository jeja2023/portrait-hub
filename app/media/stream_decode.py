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
