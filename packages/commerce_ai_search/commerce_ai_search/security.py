from __future__ import annotations

import threading
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .config import MallConfig, Settings, normalize_origin_value
from .identifiers import normalize_mall_id
from .url_safety import product_url_contains_product_id, safe_absolute_http_url

UNSAFE_PUBLIC_API_KEY_FIELDS = {
    "api_key",
    "apiKey",
    "apikey",
    "api-key",
    "x-api-key",
    "x_api_key",
    "X-API-Key",
    "admin_key",
    "adminKey",
    "admin-key",
    "x-admin-key",
    "x_admin_key",
    "X-Admin-Key",
}
UNSAFE_PUBLIC_API_KEY_FIELD_NORMALIZED = {
    "apikey",
    "xapikey",
    "publicapikey",
    "adminapikey",
    "adminkey",
    "xadminkey",
}
MULTIPART_SEARCH_FIELDS = frozenset(
    {
        "mall_id",
        "site_id",
        "q",
        "image",
        "limit",
        "offset",
        "category",
        "print_method",
        "material",
        "color",
        "min_price",
        "max_price",
        "quantity",
        "order_qty",
        "max_delivery_days",
        "text_weight",
        "image_weight",
    }
)


@dataclass(frozen=True)
class PublicAccessError(Exception):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class PublicHeaderAccessIndex:
    enabled_count: int
    origins_by_api_key: dict[str, frozenset[str]]
    any_origin_api_keys: frozenset[str]
    anonymous_origins: frozenset[str]
    anonymous_allows_any_origin: bool = False


@dataclass(frozen=True)
class MallAccessIndex:
    enabled_malls: dict[str, MallConfig]
    origins_by_mall_id: dict[str, frozenset[str]]
    any_origin_mall_ids: frozenset[str]

    @property
    def enabled_count(self) -> int:
        return len(self.enabled_malls)


_PUBLIC_HEADER_INDEX_CACHE: dict[int, tuple[Settings, dict[str, MallConfig], PublicHeaderAccessIndex]] = {}
_PUBLIC_HEADER_INDEX_LOCK = threading.RLock()
_PUBLIC_HEADER_INDEX_MAX_ENTRIES = 128
_MALL_ACCESS_INDEX_CACHE: dict[int, tuple[Settings, dict[str, MallConfig], MallAccessIndex]] = {}
_MALL_ACCESS_INDEX_LOCK = threading.RLock()
_MALL_ACCESS_INDEX_MAX_ENTRIES = 128


def validate_mall_access(
    settings: Settings,
    mall_id: str | None,
    api_key: str | None,
    origin: str | None = None,
) -> None:
    try:
        normalized_mall_id = normalize_mall_id(mall_id, required=False)
    except ValueError as exc:
        raise PublicAccessError(status_code=400, detail=str(exc)) from exc
    if not settings.malls:
        return
    if not normalized_mall_id:
        raise PublicAccessError(status_code=403, detail="mall_id is required")
    index = mall_access_index(settings)
    mall = index.enabled_malls.get(normalized_mall_id)
    if mall is None or not mall.enabled:
        raise PublicAccessError(status_code=403, detail="mall_id is not allowed")
    if mall.api_key and not secure_public_api_key_matches(api_key, mall.api_key):
        raise PublicAccessError(status_code=401, detail="invalid API key")
    validate_indexed_origin_access(index, normalized_mall_id, origin)


def validate_public_header_access(
    settings: Settings,
    api_key: str | None,
    origin: str | None = None,
) -> None:
    if not settings.malls:
        return
    index = public_header_access_index(settings)
    if index.enabled_count <= 0:
        raise PublicAccessError(status_code=403, detail="mall_id is not allowed")
    api_key_text = str(api_key or "")
    exact_origins = index.origins_by_api_key.get(api_key_text, frozenset()) if api_key_text else frozenset()
    exact_allows_any_origin = api_key_text in index.any_origin_api_keys if api_key_text else False
    anonymous_candidate_exists = index.anonymous_allows_any_origin or bool(index.anonymous_origins)
    if not exact_allows_any_origin and not exact_origins and not anonymous_candidate_exists:
        raise PublicAccessError(status_code=401, detail="invalid API key")
    if exact_allows_any_origin or index.anonymous_allows_any_origin:
        return
    raw_origin = str(origin or "").strip()
    if not raw_origin:
        raise PublicAccessError(status_code=403, detail="origin is required")
    try:
        normalized_origin = normalize_origin_value(raw_origin, allow_wildcard=False)
    except ValueError:
        raise PublicAccessError(status_code=403, detail="origin is not allowed") from None
    if not normalized_origin:
        raise PublicAccessError(status_code=403, detail="origin is required")
    allowed_origins = set(exact_origins)
    allowed_origins.update(index.anonymous_origins)
    if normalized_origin in allowed_origins:
        return
    raise PublicAccessError(status_code=403, detail="origin is not allowed")


def public_header_access_index(settings: Settings) -> PublicHeaderAccessIndex:
    cache_key = id(settings)
    with _PUBLIC_HEADER_INDEX_LOCK:
        cached = _PUBLIC_HEADER_INDEX_CACHE.get(cache_key)
        if cached is not None and cached[0] is settings and cached[1] is settings.malls:
            return cached[2]
    index = build_public_header_access_index(settings)
    with _PUBLIC_HEADER_INDEX_LOCK:
        if len(_PUBLIC_HEADER_INDEX_CACHE) >= _PUBLIC_HEADER_INDEX_MAX_ENTRIES:
            _PUBLIC_HEADER_INDEX_CACHE.clear()
        _PUBLIC_HEADER_INDEX_CACHE[cache_key] = (settings, settings.malls, index)
    return index


def mall_access_index(settings: Settings) -> MallAccessIndex:
    cache_key = id(settings)
    with _MALL_ACCESS_INDEX_LOCK:
        cached = _MALL_ACCESS_INDEX_CACHE.get(cache_key)
        if cached is not None and cached[0] is settings and cached[1] is settings.malls:
            return cached[2]
    index = build_mall_access_index(settings)
    with _MALL_ACCESS_INDEX_LOCK:
        if len(_MALL_ACCESS_INDEX_CACHE) >= _MALL_ACCESS_INDEX_MAX_ENTRIES:
            _MALL_ACCESS_INDEX_CACHE.clear()
        _MALL_ACCESS_INDEX_CACHE[cache_key] = (settings, settings.malls, index)
    return index


def build_mall_access_index(settings: Settings) -> MallAccessIndex:
    enabled_malls: dict[str, MallConfig] = {}
    origins_by_mall_id: dict[str, frozenset[str]] = {}
    any_origin_mall_ids: set[str] = set()
    for mall_id, mall in settings.malls.items():
        if not mall.enabled:
            continue
        enabled_malls[mall_id] = mall
        normalized_origins, allows_any_origin = normalized_allowed_origin_set(mall.allowed_origins)
        if allows_any_origin:
            any_origin_mall_ids.add(mall_id)
        else:
            origins_by_mall_id[mall_id] = frozenset(normalized_origins)
    return MallAccessIndex(
        enabled_malls=enabled_malls,
        origins_by_mall_id=origins_by_mall_id,
        any_origin_mall_ids=frozenset(any_origin_mall_ids),
    )


def build_public_header_access_index(settings: Settings) -> PublicHeaderAccessIndex:
    origins_by_api_key: dict[str, set[str]] = {}
    any_origin_api_keys: set[str] = set()
    anonymous_origins: set[str] = set()
    anonymous_allows_any_origin = False
    enabled_count = 0
    for mall in settings.malls.values():
        if not mall.enabled:
            continue
        enabled_count += 1
        normalized_origins, allows_any_origin = normalized_allowed_origin_set(mall.allowed_origins)
        api_key = str(mall.api_key or "")
        if not api_key:
            anonymous_allows_any_origin = anonymous_allows_any_origin or allows_any_origin
            anonymous_origins.update(normalized_origins)
            continue
        if allows_any_origin:
            any_origin_api_keys.add(api_key)
        else:
            origins_by_api_key.setdefault(api_key, set()).update(normalized_origins)
    return PublicHeaderAccessIndex(
        enabled_count=enabled_count,
        origins_by_api_key={api_key: frozenset(origins) for api_key, origins in origins_by_api_key.items()},
        any_origin_api_keys=frozenset(any_origin_api_keys),
        anonymous_origins=frozenset(anonymous_origins),
        anonymous_allows_any_origin=anonymous_allows_any_origin,
    )


def secure_public_api_key_matches(candidate: str | None, expected: str) -> bool:
    if not candidate:
        return False
    return secrets.compare_digest(str(candidate), str(expected))


def normalized_allowed_origin_set(origins: tuple[str, ...]) -> tuple[set[str], bool]:
    if not origins:
        return set(), True
    normalized: set[str] = set()
    for origin in origins:
        text = str(origin or "").strip()
        if not text:
            continue
        if text == "*":
            return set(), True
        try:
            value = normalize_origin_value(text, allow_wildcard=False)
        except ValueError:
            value = text.rstrip("/")
        if value:
            normalized.add(value)
    return normalized, False


def validate_indexed_origin_access(index: MallAccessIndex, mall_id: str, origin: str | None) -> None:
    if mall_id in index.any_origin_mall_ids:
        return
    allowed = index.origins_by_mall_id.get(mall_id, frozenset())
    if not allowed:
        return
    raw_origin = str(origin or "").strip()
    if not raw_origin:
        raise PublicAccessError(status_code=403, detail="origin is required")
    try:
        normalized_origin = normalize_origin_value(raw_origin, allow_wildcard=False)
    except ValueError:
        raise PublicAccessError(status_code=403, detail="origin is not allowed") from None
    if not normalized_origin:
        raise PublicAccessError(status_code=403, detail="origin is required")
    if normalized_origin in allowed:
        return
    raise PublicAccessError(status_code=403, detail="origin is not allowed")


def public_api_key_field_names(data: Any) -> list[str]:
    if data is None:
        return []
    try:
        keys = list(data.keys())
    except AttributeError:
        return []
    return [str(key) for key in keys if is_unsafe_public_api_key_field(str(key))]


def is_unsafe_public_api_key_field(field_name: str) -> bool:
    text = str(field_name or "").strip()
    normalized = text.lower().replace("_", "").replace("-", "")
    return text in UNSAFE_PUBLIC_API_KEY_FIELDS or normalized in UNSAFE_PUBLIC_API_KEY_FIELD_NORMALIZED


def unsupported_multipart_field_names(data: Any) -> list[str]:
    if data is None:
        return []
    try:
        keys = list(data.keys())
    except AttributeError:
        return []
    return sorted(str(key) for key in keys if str(key) not in MULTIPART_SEARCH_FIELDS)


def validate_origin_access(mall: MallConfig, origin: str | None) -> None:
    if not mall.allowed_origins:
        return
    raw_origin = str(origin or "").strip()
    if not raw_origin:
        raise PublicAccessError(status_code=403, detail="origin is required")
    try:
        normalized_origin = normalize_origin_value(raw_origin, allow_wildcard=False)
    except ValueError:
        raise PublicAccessError(status_code=403, detail="origin is not allowed") from None
    if not normalized_origin:
        raise PublicAccessError(status_code=403, detail="origin is required")
    allowed = {item.strip().rstrip("/") for item in mall.allowed_origins if item.strip()}
    if "*" in allowed or normalized_origin in allowed:
        return
    raise PublicAccessError(status_code=403, detail="origin is not allowed")


def validate_click_product_url(
    settings: Settings,
    mall_id: str,
    product_url: str | None,
    product_id: str | None = None,
) -> None:
    if not product_url:
        return
    product_url_text = safe_absolute_http_url(product_url)
    if product_url_text is None:
        raise PublicAccessError(status_code=400, detail="product_url must be an absolute http(s) URL")
    if product_id is not None and not product_url_contains_product_id(product_url_text, product_id):
        raise PublicAccessError(status_code=400, detail="product_url must contain product_id")
    try:
        expected_origin = product_url_origin_for_mall(settings, mall_id)
    except ValueError as exc:
        raise PublicAccessError(status_code=400, detail=str(exc)) from exc
    if expected_origin is None:
        return
    actual = urlparse(product_url_text)
    expected = urlparse(expected_origin)
    if normalized_url_authority(actual) != normalized_url_authority(expected):
        raise PublicAccessError(status_code=400, detail="product_url is not allowed for mall")
    expected_prefix = product_url_prefix_for_mall(settings, mall_id)
    if expected_prefix and not url_matches_product_url_prefix(product_url_text, expected_prefix):
        raise PublicAccessError(status_code=400, detail="product_url does not match mall product URL template")


def product_url_origin_for_mall(settings: Settings, mall_id: str) -> str | None:
    prefix = product_url_prefix_for_mall(settings, mall_id)
    if not prefix:
        return None
    parsed = urlparse(prefix)
    return normalized_url_authority(parsed)


def product_url_prefix_for_mall(settings: Settings, mall_id: str) -> str | None:
    mall_id_text = normalize_mall_id(mall_id, required=False) or ""
    mall = settings.malls.get(mall_id_text) if mall_id_text else None
    template = mall.product_url_template if mall and mall.product_url_template else settings.product_url_template
    if not template:
        return None
    sentinel = "__HAEORUM_CLICK_PRODUCT_ID__"
    try:
        formatted = template.format(product_id=sentinel, mall_id=mall_id_text or "www")
    except (KeyError, ValueError):
        return None
    if sentinel not in formatted:
        return None
    safe_url = safe_absolute_http_url(formatted)
    if safe_url is None:
        return None
    return safe_url.split(sentinel, 1)[0]


def url_matches_product_url_prefix(url: str, prefix: str) -> bool:
    safe_url = safe_absolute_http_url(url)
    safe_prefix = safe_absolute_http_url(prefix)
    if safe_url is None or safe_prefix is None:
        return False
    url_authority, url_tail = normalized_url_authority_and_tail(safe_url)
    prefix_authority, prefix_tail = normalized_url_authority_and_tail(safe_prefix)
    if url_authority != prefix_authority:
        return False
    if not prefix_tail:
        return True
    return url_tail.startswith(prefix_tail)


def normalized_url_authority_and_tail(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    separator_index = value.find("://")
    tail_start = separator_index + 3 + len(parsed.netloc) if separator_index >= 0 else 0
    return normalized_url_authority(parsed), value[tail_start:]


def normalized_url_authority(parsed: Any) -> str:
    scheme = str(getattr(parsed, "scheme", "") or "").lower()
    host = str(getattr(parsed, "hostname", "") or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"
