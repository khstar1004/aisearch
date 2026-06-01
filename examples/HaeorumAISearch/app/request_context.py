from __future__ import annotations

import ipaddress
from collections.abc import Mapping

from .config import Settings


def resolve_client_ip(peer_host: str | None, headers: Mapping[str, str], settings: Settings) -> str:
    client = str(peer_host or "unknown").strip() or "unknown"
    if not is_trusted_proxy(client, settings.trusted_proxy_ips):
        return client

    forwarded_for = header_value(headers, "x-forwarded-for")
    if forwarded_for:
        candidate = normalized_ip_token(forwarded_for.split(",", 1)[0])
        if candidate:
            return candidate

    forwarded = extract_forwarded_for(headers)
    if forwarded:
        return forwarded

    real_ip = header_value(headers, "x-real-ip")
    if real_ip:
        candidate = normalized_ip_token(real_ip)
        if candidate:
            return candidate
    return client


def is_trusted_proxy(peer_host: str, trusted_proxy_ips: tuple[str, ...]) -> bool:
    try:
        peer = ipaddress.ip_address(peer_host)
    except ValueError:
        return peer_host in trusted_proxy_ips
    for trusted in trusted_proxy_ips:
        try:
            if peer in ipaddress.ip_network(trusted, strict=False):
                return True
        except ValueError:
            if peer_host == trusted:
                return True
    return False


def valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def normalized_ip_token(value: str | None) -> str | None:
    token = str(value or "").strip().strip('"')
    if not token:
        return None
    if token.startswith("[") and "]" in token:
        token = token[1 : token.index("]")]
    elif token.count(":") == 1:
        host, port = token.rsplit(":", 1)
        if port.isdigit():
            token = host
    return token if valid_ip(token) else None


def extract_forwarded_for(headers: Mapping[str, str]) -> str | None:
    forwarded = header_value(headers, "forwarded")
    if not forwarded:
        return None
    first_hop = forwarded.split(",", 1)[0]
    for part in first_hop.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.strip().lower() == "for":
            return normalized_ip_token(value)
    return None


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key, candidate in headers.items():
        if key.lower() == lowered:
            return candidate
    return None
