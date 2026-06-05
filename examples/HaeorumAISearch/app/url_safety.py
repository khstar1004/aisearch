from __future__ import annotations

import ipaddress
import socket
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from http.client import HTTPMessage
from ipaddress import IPv4Address, IPv6Address
from urllib.parse import parse_qsl, unquote, urlparse, urlunparse


class UnsafePublicHttpTargetError(ValueError):
    pass


class SafePublicHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, resolver=None):  # type: ignore[no-untyped-def]
        super().__init__()
        self.resolver = resolver

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp,  # type: ignore[no-untyped-def]
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirect = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirect is None:
            return None
        validate_http_url_resolves_to_public_network(redirect.full_url, "redirect URL", resolver=self.resolver)
        return redirect


PUBLIC_HTTP_OPENER = urllib.request.build_opener(SafePublicHTTPRedirectHandler())
SAFE_ABSOLUTE_HTTP_URL_CACHE_MAX = 65536
_SAFE_ABSOLUTE_HTTP_URL_CACHE: OrderedDict[str, str | None] = OrderedDict()
_SAFE_ABSOLUTE_HTTP_URL_CACHE_LOCK = threading.RLock()
PRODUCT_ID_QUERY_PARAM_NAMES = {
    "id",
    "p_idx",
    "pidx",
    "product_id",
    "productid",
    "product_no",
    "productno",
    "goods_id",
    "goodsid",
    "goods_no",
    "goodsno",
    "item_id",
    "itemid",
}


def safe_absolute_http_url(value: object) -> str | None:
    text = str(value or "").strip()
    with _SAFE_ABSOLUTE_HTTP_URL_CACHE_LOCK:
        if text in _SAFE_ABSOLUTE_HTTP_URL_CACHE:
            result = _SAFE_ABSOLUTE_HTTP_URL_CACHE.pop(text)
            _SAFE_ABSOLUTE_HTTP_URL_CACHE[text] = result
            return result
    result = _safe_absolute_http_url_text(text)
    with _SAFE_ABSOLUTE_HTTP_URL_CACHE_LOCK:
        _SAFE_ABSOLUTE_HTTP_URL_CACHE[text] = result
        if len(_SAFE_ABSOLUTE_HTTP_URL_CACHE) > SAFE_ABSOLUTE_HTTP_URL_CACHE_MAX:
            _SAFE_ABSOLUTE_HTTP_URL_CACHE.popitem(last=False)
    return result


def safe_absolute_http_url_uses_https(value: object) -> bool:
    text = safe_absolute_http_url(value)
    return bool(text and urlparse(text).scheme.lower() == "https")


def safe_product_source_url(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    absolute = safe_absolute_http_url(text)
    if absolute is not None:
        return absolute
    return safe_root_relative_url(text)


def product_url_contains_product_id(value: object, product_id: object) -> bool:
    product_id_text = unquote(str(product_id or "").strip())
    if not product_id_text:
        return True
    text = str(value or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    candidates = [unquote(part) for part in parsed.path.split("/") if part]
    candidates.extend(unquote(item) for item in parsed.params.split(";") if item)
    product_param_matches: list[bool] = []
    for param_name, param_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_name = unquote(param_name).strip().lower().replace("-", "_")
        decoded_value = unquote(param_value)
        if normalized_name in PRODUCT_ID_QUERY_PARAM_NAMES:
            product_param_matches.append(decoded_value == product_id_text)
        else:
            candidates.append(decoded_value)
    if product_param_matches:
        return any(product_param_matches)
    if parsed.fragment:
        candidates.append(unquote(parsed.fragment))
    return any(text_contains_token(candidate, product_id_text) for candidate in candidates)


def text_contains_token(value: object, token: str) -> bool:
    text = str(value or "")
    if not token:
        return True
    start = 0
    while True:
        index = text.find(token, start)
        if index < 0:
            return False
        before_ok = index == 0 or not text[index - 1].isalnum()
        end = index + len(token)
        after_ok = end == len(text) or not text[end].isalnum()
        if before_ok and after_ok:
            return True
        start = index + 1


def safe_root_relative_url(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        return None
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc or not text.startswith("/") or text.startswith("//"):
        return None
    return text


def _safe_absolute_http_url_text(text: str) -> str | None:
    if not text:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        return None
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    if is_non_public_host(parsed.hostname):
        return None
    return text


def normalize_http_base_url(value: object, field_name: str = "URL") -> str:
    parsed, text = parse_http_target_url(value, field_name)
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not include params, query, or fragment")
    if parsed.hostname and is_link_or_unspecified_host(parsed.hostname):
        raise ValueError(f"{field_name} must not use link-local or unspecified hosts")
    return text.rstrip("/")


def normalize_public_http_base_url(value: object, field_name: str = "URL") -> str:
    parsed, text = parse_http_target_url(value, field_name)
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not include params, query, or fragment")
    if parsed.hostname and is_non_public_host(parsed.hostname):
        raise ValueError(f"{field_name} must not use non-public hosts")
    return text.rstrip("/")


def normalize_http_origin(value: object, field_name: str = "origin") -> str:
    parsed, _ = parse_http_target_url(value, field_name)
    if parsed.params or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError(f"{field_name} must include only scheme, host, and optional port")
    if parsed.hostname and is_link_or_unspecified_host(parsed.hostname):
        raise ValueError(f"{field_name} must not use link-local or unspecified hosts")
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def normalize_public_http_origin(value: object, field_name: str = "origin") -> str:
    parsed, _ = parse_http_target_url(value, field_name)
    if parsed.params or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError(f"{field_name} must include only scheme, host, and optional port")
    if parsed.hostname and is_non_public_host(parsed.hostname):
        raise ValueError(f"{field_name} must not use non-public hosts")
    return normalize_origin_parts(parsed)


def validate_http_url_resolves_to_public_network(
    value: object,
    field_name: str = "URL",
    resolver=None,  # type: ignore[no-untyped-def]
) -> list[str]:
    if resolver is None:
        resolver = socket.getaddrinfo
    parsed, _ = parse_http_target_url(value, field_name)
    if parsed.hostname and is_non_public_host(parsed.hostname):
        raise UnsafePublicHttpTargetError(f"{field_name} must not use non-public hosts")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise urllib.error.URLError(f"DNS resolution failed for {field_name}: {exc}") from exc
    resolved_ips = sorted(
        {
            str(sockaddr[0])
            for *_, sockaddr in addresses
            if isinstance(sockaddr, tuple) and sockaddr
        }
    )
    if not resolved_ips:
        raise urllib.error.URLError(f"DNS resolution returned no addresses for {field_name}")
    non_public_ips = [address for address in resolved_ips if is_non_public_ip_address(address)]
    if non_public_ips:
        raise UnsafePublicHttpTargetError(
            f"{field_name} resolves to a non-public address: " + ", ".join(non_public_ips[:5])
        )
    return resolved_ips


def open_public_http_request(
    request: urllib.request.Request,
    timeout: int | float,
    resolver=None,  # type: ignore[no-untyped-def]
):  # type: ignore[no-untyped-def]
    validate_http_url_resolves_to_public_network(request.full_url, "request URL", resolver=resolver)
    opener = PUBLIC_HTTP_OPENER if resolver is None else urllib.request.build_opener(SafePublicHTTPRedirectHandler(resolver))
    return opener.open(request, timeout=timeout)


def normalize_origin_parts(parsed) -> str:  # type: ignore[no-untyped-def]
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def parse_http_target_url(value: object, field_name: str):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        raise ValueError(f"{field_name} must not contain whitespace, control characters, or backslashes")
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} must include a valid port") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must not include credentials")
    return parsed, text


def redact_url_for_report(value: object) -> str:
    text = str(value or "").strip()
    safe_text = "".join(" " if char.isspace() or ord(char) < 32 or ord(char) == 127 else char for char in text).strip()
    if not safe_text:
        return ""
    parsed = urlparse(safe_text)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if parsed.username is None and parsed.password is None:
        if parsed.query or parsed.fragment or parsed.params:
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return safe_text
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port:
        host = f"{host}:{port}"
    return urlunparse((parsed.scheme, f"[redacted]@{host}", parsed.path, "", "", ""))


def is_local_or_link_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in {"localhost", "0.0.0.0", "::", "::1"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified or address.is_link_local


def is_non_public_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return is_non_public_ip_address(address)


def is_non_public_ip_address(address: str | IPv4Address | IPv6Address) -> bool:
    parsed = ipaddress.ip_address(address) if isinstance(address, str) else address
    return bool(
        parsed.is_loopback
        or parsed.is_unspecified
        or parsed.is_link_local
        or parsed.is_private
        or parsed.is_reserved
        or parsed.is_multicast
        or not parsed.is_global
    )


def is_link_or_unspecified_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if host in {"0.0.0.0", "::"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_unspecified or address.is_link_local
