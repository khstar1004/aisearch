from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import is_placeholder_public_api_key
from app.image_validation import validate_image_base64, validate_image_bytes
from app.url_safety import is_non_public_host, open_public_http_request, product_url_contains_product_id
from scripts.mall_config_check import api_key_hash
from scripts.widget_integration_probe import (
    analyze_html_source,
    csp_reports_inline_script_risk,
    csp_source_allows_target_url,
    element_is_disabled_or_readonly,
    element_is_hidden,
    has_local_explicit_probe_sources,
    inject_snippet_preview_html,
    is_remote_probe_source,
    missing_pc_mobile_variants,
    parse_csp_directives,
    probe_source_entries,
    site_widget_src,
    url_origin,
    validate_preview_html_body,
)

DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HaeorumAISearchSiteCheck/1.0"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) HaeorumAISearchSiteCheck/1.0"
PAGE_CHECK_NAMES = {
    "desktop": "desktop_page",
    "mobile": "mobile_page",
}
WIDGET_INIT_CHECK_NAME = "widget_init"
WIDGET_SCRIPT_ASSET_CHECK_NAME = "widget_script_asset"
SAVED_WIDGET_PROBE_CHECK_NAME = "saved_widget_probe_sources"
RESULT_IMAGE_CSP_CHECK_NAME = "result_image_csp"
SEARCH_CHECK_NAMES = {
    "text": "text_search",
    "image": "image_search",
    "mixed": "mixed_search",
}
MODE_RESULT_CHECK_NAMES = {
    "text_category_refetch",
    "text_product_url_rule",
    "text_detail_url",
    "text_click_log",
    "image_product_url_rule",
    "image_detail_url",
    "image_click_log",
    "mixed_product_url_rule",
    "mixed_detail_url",
    "mixed_click_log",
}
PLACEHOLDER_PATTERNS = (
    re.compile(r"^<[^>\r\n]+>$"),
    re.compile(r"^(replace-with|change-me|changeme)", re.IGNORECASE),
    re.compile(r"(^|[=:/,;])\.\.\.($|[=:/,;])"),
)
REQUIRED_RESULT_FIELDS = {
    "product_id",
    "name",
    "category",
    "price",
    "image_url",
    "product_url",
    "score",
    "score_percent",
    "mall_id",
    "source_scores",
}
REQUIRED_META_FIELDS = {
    "query_type",
    "elapsed_ms",
    "engine",
    "limit",
    "offset",
    "has_more",
    "next_offset",
    "mall_id",
    "text_weight",
    "image_weight",
    "low_confidence",
    "notice",
}
DEFAULT_QUERIES = {
    "text": "검은 우산",
    "mixed": "검은색",
}
WIDGET_SELECTOR_KEYS = ("target", "attachToSearchInput", "attachAfterSelector")
WIDGET_STRING_OPTION_KEYS = (*WIDGET_SELECTOR_KEYS, "apiBaseUrl", "mallId", "siteId")
WIDGET_SCRIPT_DATA_AUTO_INIT_ATTRS = ("data-hai-auto-init", "data-haeorum-auto-init", "data-auto-init")
WIDGET_SCRIPT_DATA_OPTION_ATTRS = {
    "target": ("data-hai-target", "data-target"),
    "mallId": ("data-hai-mall-id", "data-mall-id"),
    "siteId": ("data-hai-site-id", "data-site-id"),
    "apiBaseUrl": ("data-hai-api-base-url", "data-api-base-url"),
    "apiKey": ("data-hai-api-key", "data-api-key"),
    "attachToSearchInput": ("data-hai-attach-to-search-input", "data-attach-to-search-input"),
    "attachAfterSelector": ("data-hai-attach-after-selector", "data-attach-after-selector"),
    "triggerTitle": ("data-hai-trigger-title", "data-trigger-title"),
    "triggerAriaLabel": ("data-hai-trigger-aria-label", "data-trigger-aria-label"),
    "accentColor": ("data-hai-accent-color", "data-accent-color"),
    "accentTextColor": ("data-hai-accent-text-color", "data-accent-text-color"),
    "accentSoftColor": ("data-hai-accent-soft-color", "data-accent-soft-color"),
    "limit": ("data-hai-limit", "data-limit"),
    "maxImageMb": ("data-hai-max-image-mb", "data-max-image-mb"),
    "minImageDimension": ("data-hai-min-image-dimension", "data-min-image-dimension"),
    "mountWaitMs": ("data-hai-mount-wait-ms", "data-mount-wait-ms"),
    "zIndex": ("data-hai-z-index", "data-z-index"),
}
WIDGET_SCRIPT_DATA_BOOLEAN_OPTION_ATTRS = {
    "autoAttach": ("data-hai-auto-attach", "data-auto-attach"),
    "fallbackFloating": ("data-hai-fallback-floating", "data-fallback-floating"),
    "prefillFromSearchInput": ("data-hai-prefill-from-search-input", "data-prefill-from-search-input"),
}
AUTO_SEARCH_BAD_INPUT_TYPES = {"hidden", "submit", "button", "checkbox", "radio", "file", "image", "password", "reset"}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class HtmlSelectorIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, Any]] = []
        self.stack: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        parent = self.stack[-1] if self.stack else None
        index = len(self.elements)
        self.elements.append(
            {
                "index": index,
                "tag": normalized,
                "attrs": {str(name).lower(): value or "" for name, value in attrs},
                "parent": parent,
                "children": [],
            }
        )
        if parent is not None:
            self.elements[parent]["children"].append(index)
        if normalized not in VOID_TAGS:
            self.stack.append(index)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        normalized = tag.lower()
        if self.stack and self.elements[self.stack[-1]]["tag"] == normalized:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        while self.stack:
            index = self.stack.pop()
            if self.elements[index]["tag"] == normalized:
                break


def is_contract_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_meta_shape(meta: dict[str, Any], expected_query_type: str, expected_mall_id: str = "") -> list[str]:
    problems = []
    if not is_contract_number(meta.get("elapsed_ms")) or meta.get("elapsed_ms") < 0:
        problems.append("elapsed_ms_non_negative")
    if not isinstance(meta.get("limit"), int) or meta.get("limit") < 1:
        problems.append("limit_positive_integer")
    if not isinstance(meta.get("offset"), int) or meta.get("offset") < 0:
        problems.append("offset_non_negative_integer")
    if not isinstance(meta.get("has_more"), bool):
        problems.append("has_more_boolean")
    next_offset = meta.get("next_offset")
    if next_offset is not None and (not isinstance(next_offset, int) or next_offset < 0):
        problems.append("next_offset_non_negative_or_null")
    if meta.get("has_more") is True:
        if next_offset is None:
            problems.append("next_offset_required_when_has_more")
        elif isinstance(meta.get("offset"), int) and next_offset <= meta.get("offset"):
            problems.append("next_offset_greater_than_offset")
    elif meta.get("has_more") is False and next_offset is not None:
        problems.append("next_offset_null_when_no_more")
    if not isinstance(meta.get("low_confidence"), bool):
        problems.append("low_confidence_boolean")
    if meta.get("notice") is not None and not isinstance(meta.get("notice"), str):
        problems.append("notice_string_or_null")
    mall_id = meta.get("mall_id")
    if expected_mall_id:
        if not isinstance(mall_id, str) or not mall_id.strip():
            problems.append("mall_id_non_empty")
        elif mall_id.strip() != expected_mall_id:
            problems.append("mall_id_matches_request")
    elif mall_id is not None and not isinstance(mall_id, str):
        problems.append("mall_id_string_or_null")
    if meta.get("text_weight") is not None:
        if not is_contract_number(meta.get("text_weight")) or meta.get("text_weight") < 0:
            problems.append("text_weight_non_negative_or_null")
    if meta.get("image_weight") is not None:
        if not is_contract_number(meta.get("image_weight")) or meta.get("image_weight") < 0:
            problems.append("image_weight_non_negative_or_null")
    if expected_query_type == "text":
        if not is_contract_number(meta.get("text_weight")):
            problems.append("text_query_requires_text_weight")
        if meta.get("image_weight") is not None:
            problems.append("text_query_image_weight_null")
    elif expected_query_type == "image":
        if meta.get("text_weight") is not None:
            problems.append("image_query_text_weight_null")
        if not is_contract_number(meta.get("image_weight")):
            problems.append("image_query_requires_image_weight")
    elif expected_query_type == "text_image":
        if not is_contract_number(meta.get("text_weight")):
            problems.append("mixed_query_requires_text_weight")
        if not is_contract_number(meta.get("image_weight")):
            problems.append("mixed_query_requires_image_weight")
    return problems


def validate_result_item_shape(
    item: dict[str, Any],
    section: str,
    index: int,
    expected_mall_id: str = "",
) -> list[str]:
    problems = []
    prefix = f"{section}_{index}"
    for field_name in ["product_id", "name", "category", "mall_id"]:
        value = item.get(field_name)
        if field_name == "mall_id" and value is None and not expected_mall_id:
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{prefix}_{field_name}_non_empty")
        elif field_name == "mall_id" and expected_mall_id and value.strip() != expected_mall_id:
            problems.append(f"{prefix}_mall_id_matches_request")
    price = item.get("price")
    if price is not None and not is_contract_number(price):
        problems.append(f"{prefix}_price_number_or_null")
    elif is_contract_number(price) and price < 0:
        problems.append(f"{prefix}_price_non_negative")
    for field_name in ["image_url", "product_url"]:
        value = item.get(field_name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{prefix}_{field_name}_non_empty")
        else:
            url_error = validate_url(value)
            if url_error:
                problems.append(f"{prefix}_{field_name}_safe_https")
    score = item.get("score")
    if not is_contract_number(score) or not 0 <= score <= 1:
        problems.append(f"{prefix}_score_0_to_1")
    score_percent = item.get("score_percent")
    if not is_contract_number(score_percent) or not 0 <= score_percent <= 100:
        problems.append(f"{prefix}_score_percent_0_to_100")
    for source, value in item.get("source_scores", {}).items():
        if not is_contract_number(value):
            problems.append(f"{prefix}_source_scores_{source}_number")
    return problems


def make_png_data_url() -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(12, 120, 110)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def image_data_url_for_args(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    max_bytes = max(1, int(getattr(args, "image_max_mb", 5) or 5)) * 1024 * 1024
    image_file = str(getattr(args, "image_file", "") or "").strip()
    if image_file:
        try:
            image = validate_image_bytes(Path(image_file).read_bytes(), max_bytes=max_bytes, min_dimension=16)
        except Exception as exc:
            return (
                "",
                {
                    "ok": False,
                    "source": "file",
                    "file": image_file,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
        return image.data_url, image_input_metadata(image, source="file", file=image_file)
    image = validate_image_base64(make_png_data_url(), max_bytes=max_bytes, min_dimension=16)
    return image.data_url, image_input_metadata(image, source="generated")


def image_input_metadata(image: Any, *, source: str, file: str = "") -> dict[str, Any]:
    return {
        "ok": True,
        "source": source,
        **({"file": file} if file else {}),
        "mime_type": image.mime_type,
        "size_bytes": image.size_bytes,
        "width": image.width,
        "height": image.height,
        "sha256": image.sha256,
        "normalized": image.normalized,
        "quality_warnings": list(image.quality_warnings),
    }


def load_site_configs(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    sites = raw.get("sites", raw) if isinstance(raw, dict) else raw
    if not isinstance(sites, list):
        raise ValueError("representative site config must be a list or an object with a sites list")
    normalized = []
    for index, site in enumerate(sites, start=1):
        if not isinstance(site, dict):
            raise ValueError("representative site entries must be objects")
        mall_id = str(site.get("mall_id") or site.get("site_id") or "").strip()
        if not mall_id:
            raise ValueError(f"site #{index} requires mall_id or site_id")
        normalized.append({**site, "mall_id": mall_id})
    return normalized


def validate_site_collection(sites: list[dict[str, Any]]) -> dict[str, Any]:
    duplicate_mall_ids = duplicate_site_values(sites, "mall_id", normalize_duplicate_text)
    duplicate_urls = duplicate_site_values(sites, "url", normalize_duplicate_url)
    duplicate_origins = duplicate_site_values(sites, "origin", normalize_duplicate_origin)
    duplicate_api_keys = duplicate_site_values(
        sites,
        "api_key",
        normalize_duplicate_text,
        redact_value=True,
    )
    problems = []
    for field, groups in [
        ("duplicate_mall_ids", duplicate_mall_ids),
        ("duplicate_urls", duplicate_urls),
        ("duplicate_origins", duplicate_origins),
        ("duplicate_api_keys", duplicate_api_keys),
    ]:
        if groups:
            problems.append(field)
    return {
        "name": "site_collection",
        "ok": not problems,
        "site_count": len(sites),
        "problems": problems,
        "duplicate_mall_ids": duplicate_mall_ids,
        "duplicate_urls": duplicate_urls,
        "duplicate_origins": duplicate_origins,
        "duplicate_api_keys": duplicate_api_keys,
    }


def duplicate_site_values(
    sites: list[dict[str, Any]],
    field: str,
    normalizer: Any,
    *,
    redact_value: bool = False,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for index, site in enumerate(sites, start=1):
        raw_value = site.get(field)
        if is_missing_config_value(raw_value):
            continue
        value = normalizer(raw_value)
        if not value:
            continue
        if value not in groups:
            groups[value] = {
                "value": "[redacted]" if redact_value else value,
                "sites": [],
            }
        groups[value]["sites"].append(
            {
                "index": index,
                "name": str(site.get("name") or "").strip() or None,
                "mall_id": str(site.get("mall_id") or "").strip() or None,
            }
        )
    return [group for _value, group in sorted(groups.items()) if len(group["sites"]) > 1]


def normalize_duplicate_text(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_duplicate_origin(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlparse(text)
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    if parsed.scheme and parsed.hostname:
        default_port = normalized_url_port(parsed.scheme, None)
        port = normalized_url_port(parsed.scheme, parsed_port)
        host = parsed.hostname.lower()
        netloc = f"{host}:{port}" if port and port != default_port else host
        return f"{parsed.scheme.lower()}://{netloc}"
    return text.lower()


def normalize_duplicate_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlparse(text)
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    if parsed.scheme and parsed.hostname:
        default_port = normalized_url_port(parsed.scheme, None)
        port = normalized_url_port(parsed.scheme, parsed_port)
        host = parsed.hostname.lower()
        netloc = f"{host}:{port}" if port and port != default_port else host
        path = parsed.path.rstrip("/")
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme.lower()}://{netloc}{path}{query}"
    return text.lower()


def is_missing_config_value(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return any(pattern.search(text) for pattern in PLACEHOLDER_PATTERNS)


def validate_url(value: Any, *, origin_only: bool = False) -> str | None:
    text = str(value or "").strip()
    if is_missing_config_value(text):
        return "value is missing or still a placeholder"
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        return "value must not include whitespace, control characters, or backslashes"
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError:
        return "value has an invalid port"
    if parsed.scheme != "https" or not parsed.netloc:
        return "value must be a production https URL"
    if parsed.username or parsed.password:
        return "value must not include credentials"
    if parsed.hostname and is_non_public_host(parsed.hostname):
        return "value must not use non-public hosts"
    if origin_only and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password):
        return "origin must include only scheme, host, and optional port"
    return None


def validate_api_base_url(value: Any) -> str | None:
    url_error = validate_url(value)
    if url_error:
        return url_error
    parsed = urlparse(str(value or "").strip())
    if parsed.query or parsed.fragment:
        return "API base URL must not include query string or fragment"
    return None


def validate_product_url_prefix(value: Any) -> str | None:
    text = str(value or "").strip()
    url_error = validate_url(text)
    if url_error:
        return url_error
    parsed = urlparse(text)
    if parsed.query or parsed.fragment:
        return "product URL prefix must not include query string or fragment"
    return None


def is_local_host(hostname: str) -> bool:
    return is_non_public_host(hostname)


def validate_site_config(site: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    problems: list[dict[str, str]] = []

    def add(field: str, message: str) -> None:
        problems.append({"field": field, "message": message})

    if is_missing_config_value(site.get("mall_id")):
        add("mall_id", "mall_id is required")
    if not args.skip_page:
        page_error = validate_url(site.get("url"))
        if page_error:
            add("url", page_error)
    if not args.skip_api:
        api_base_error = validate_api_base_url(site.get("api_base_url") or args.api_base_url)
        if api_base_error:
            add("api_base_url", api_base_error)
        api_key = str(site.get("api_key") or args.api_key or "").strip()
        if is_missing_config_value(api_key):
            add("api_key", "site api_key or --api-key is required")
        elif is_placeholder_public_api_key(api_key):
            add("api_key", "site api_key or --api-key must be changed from sample or placeholder value")
        origin_error = validate_url(site.get("origin"), origin_only=True)
        if origin_error:
            add("origin", origin_error)
        has_url_rule = any(
            not is_missing_config_value(site.get(field))
            for field in ("expected_product_url_prefix", "expected_product_url_contains", "expected_product_url_pattern")
        )
        if not has_url_rule and origin_error:
            add(
                "product_url_rule",
                "origin or expected_product_url_prefix/contains/pattern is required",
            )
        for prefix in expected_list(site.get("expected_product_url_prefix")):
            prefix_error = validate_product_url_prefix(prefix)
            if prefix_error:
                add("expected_product_url_prefix", prefix_error)

    return {
        "name": "site_config",
        "ok": not problems,
        "problems": problems,
    }


def fetch_text(url: str, user_agent: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": user_agent})
    started = time.perf_counter()
    try:
        with open_public_http_request(request, timeout=timeout) as response:
            body = response.read(2_000_000).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= response.status < 400,
                "status": response.status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "body": body,
                "content_type": response.headers.get("Content-Type"),
                "csp_headers": response.headers.get_all("Content-Security-Policy", []) or [],
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "body": body,
            "content_type": exc.headers.get("Content-Type") if exc.headers else None,
            "csp_headers": exc.headers.get_all("Content-Security-Policy", []) if exc.headers else [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8", **headers},
    )
    started = time.perf_counter()
    try:
        with open_public_http_request(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "data": data,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"message": raw}
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "data": data,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
            "data": {},
        }


def site_markers(site: dict[str, Any]) -> list[str]:
    markers = [str(marker) for marker in site.get("required_markers", []) if str(marker)]
    if site.get("expect_widget", True):
        markers.extend(
            [
                str(site.get("widget_global") or "HaeorumAISearch"),
                str(site.get("widget_script") or "widget.js"),
            ]
        )
    if site.get("expect_mall_id_marker", True):
        markers.append(str(site["mall_id"]))
    return sorted(set(markers))


def check_site_page(site: dict[str, Any], variant: str, timeout: int) -> dict[str, Any]:
    check_name = PAGE_CHECK_NAMES[variant]
    url = str(site.get("url") or "").strip()
    if not url:
        return {"name": check_name, "ok": False, "message": "site url is missing"}
    user_agent = MOBILE_UA if variant == "mobile" else DESKTOP_UA
    fetched = fetch_text(url, user_agent, timeout)
    body = fetched.pop("body", "")
    missing = [marker for marker in site_markers(site) if marker not in body]
    widget_global = str(site.get("widget_global") or "HaeorumAISearch")
    widget_script = str(site.get("widget_script") or "widget.js")
    if widget_global in missing and extract_widget_script_auto_init_found(body, widget_script):
        missing.remove(widget_global)
    ok = fetched.get("ok") is True and not missing
    return {
        "name": check_name,
        "ok": ok,
        "url": url,
        "status": fetched.get("status"),
        "elapsed_ms": fetched.get("elapsed_ms"),
        "content_type": fetched.get("content_type"),
        "missing_markers": missing,
        **({"error": fetched["error"]} if fetched.get("error") else {}),
    }


def html_selector_index(body: str) -> HtmlSelectorIndex:
    parser = HtmlSelectorIndex()
    parser.feed(body or "")
    return parser


def extract_widget_option_strings(body: str, keys: tuple[str, ...] = WIDGET_STRING_OPTION_KEYS) -> dict[str, str]:
    options = {}
    text = str(body or "")
    for key in keys:
        match = re.search(rf"['\"]?{re.escape(key)}['\"]?\s*:\s*(['\"])(.*?)\1", text, re.DOTALL)
        if match:
            options[key] = match.group(2).strip()
    return options


def extract_widget_option_booleans(body: str) -> dict[str, bool]:
    options = {}
    text = str(body or "")
    for key in ("autoAttach", "fallbackFloating"):
        match = re.search(rf"['\"]?{re.escape(key)}['\"]?\s*:\s*(true|false)\b", text, re.IGNORECASE)
        if match:
            options[key] = match.group(1).lower() == "true"
    return options


def widget_script_attrs(body: str, widget_script: str = "widget.js") -> list[dict[str, str]]:
    script_name = str(widget_script or "widget.js")
    return [
        element["attrs"]
        for element in html_selector_index(body).elements
        if element.get("tag") == "script"
        and (not script_name or script_name in str(element.get("attrs", {}).get("src") or ""))
    ]


def first_html_attr(attrs: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        normalized = name.lower()
        if normalized in attrs:
            return str(attrs.get(normalized) or "").strip()
    return None


def html_truthy_attr(value: str | None) -> bool:
    if value is None:
        return False
    text = str(value or "").strip().lower()
    return text == "" or text in {"1", "true", "yes", "y", "on"}


def html_boolean_attr(value: str | None) -> bool | None:
    if value is None:
        return None
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def extract_widget_script_auto_init_found(body: str, widget_script: str = "widget.js") -> bool:
    return any(
        html_truthy_attr(first_html_attr(attrs, WIDGET_SCRIPT_DATA_AUTO_INIT_ATTRS))
        for attrs in widget_script_attrs(body, widget_script)
    )


def extract_widget_script_data_options(body: str, widget_script: str = "widget.js") -> dict[str, Any]:
    for attrs in widget_script_attrs(body, widget_script):
        if not html_truthy_attr(first_html_attr(attrs, WIDGET_SCRIPT_DATA_AUTO_INIT_ATTRS)):
            continue
        options: dict[str, Any] = {}
        for key, names in WIDGET_SCRIPT_DATA_OPTION_ATTRS.items():
            value = first_html_attr(attrs, names)
            if value is not None:
                options[key] = value
        for key, names in WIDGET_SCRIPT_DATA_BOOLEAN_OPTION_ATTRS.items():
            value = html_boolean_attr(first_html_attr(attrs, names))
            if value is not None:
                options[key] = value
        return options
    return {}


def extract_widget_script_api_base_url(body: str, widget_script: str = "widget.js") -> str:
    text = str(body or "")
    script_name = str(widget_script or "widget.js")
    for match in re.finditer(r"<script\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1", text, re.IGNORECASE | re.DOTALL):
        source = match.group(2).strip()
        if script_name and script_name not in source:
            continue
        parsed = urlparse(source)
        if parsed.scheme and parsed.netloc:
            base_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            if validate_api_base_url(base_url) is None:
                return base_url
    return ""


def extract_widget_script_src(body: str, widget_script: str = "widget.js") -> str:
    for attrs in widget_script_attrs(body, widget_script):
        source = str(attrs.get("src") or "").strip()
        if source:
            return source
    return ""


def absolute_widget_script_src(body: str, widget_script: str, page_url: str) -> str:
    source = extract_widget_script_src(body, widget_script)
    if not source:
        return ""
    return urljoin(page_url, source)


def validate_widget_script_url(value: str) -> str:
    url_error = validate_url(value)
    if url_error:
        return url_error
    parsed = urlparse(str(value or "").strip())
    if parsed.params or parsed.query or parsed.fragment:
        return "widget script URL must not include params, query string, or fragment"
    return ""


def javascript_content_type_ok(content_type: Any) -> bool:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    return bool(normalized) and (
        normalized in {"application/javascript", "text/javascript", "application/x-javascript"}
        or normalized.endswith("+javascript")
        or "ecmascript" in normalized
    )


def response_body_looks_html(body: str) -> bool:
    prefix = str(body or "").lstrip()[:512].lower()
    return prefix.startswith("<!doctype html") or prefix.startswith("<html") or bool(re.search(r"<html[\s>]", prefix))


def widget_csp_evidence(
    body: str,
    *,
    page_url: str,
    widget_script: str,
    api_base_url: str,
    response_policies: list[str],
    manual_init_call_found: bool,
    auto_init_found: bool,
) -> dict[str, Any]:
    widget_src = absolute_widget_script_src(body, widget_script, page_url)
    csp = csp_reports_inline_script_risk(
        html_selector_index(body).elements,
        response_policies=response_policies,
        widget_src=widget_src,
        api_base_url=api_base_url,
        page_url=page_url,
    )
    problems = []
    if manual_init_call_found and not auto_init_found and csp["inline_init_risky"]:
        problems.append("inline_init_blocked_by_csp")
    if csp["external_widget_src_risky"]:
        problems.append("widget_script_blocked_by_csp")
    if csp["api_connect_risky"]:
        problems.append("api_connect_blocked_by_csp")
    return {
        "ok": not problems,
        "problems": problems,
        "policy_count": len(csp["policies"]),
        "inline_init_allowed": not csp["inline_init_risky"],
        "inline_init_blocking_directives": csp["inline_init_blocking_directives"],
        "widget_src_allowed": csp["widget_src_allowed"],
        "widget_src_blocking_directives": csp["widget_src_blocking_directives"],
        "widget_src": widget_src,
        "widget_src_origin": csp["widget_src_origin"],
        "api_connect_allowed": csp["api_connect_allowed"],
        "api_connect_blocking_directives": csp["api_connect_blocking_directives"],
        "api_base_url_origin": csp["api_base_url_origin"],
        "page_origin": csp["page_origin"],
        "policies": csp["policies"],
    }


def csp_image_sources(policy: str) -> tuple[str, list[str]]:
    directives = parse_csp_directives(policy)
    for name in ("img-src", "default-src"):
        if name in directives:
            return name, directives[name]
    return "", []


def csp_image_url_blocking_directives(policies: list[str], image_url: str, page_url: str = "") -> list[str]:
    blocking = []
    target = str(image_url or "").strip()
    if not target:
        return blocking
    for policy in policies:
        directive_name, sources = csp_image_sources(policy)
        if sources and not any(csp_source_allows_target_url(source, target, page_url) for source in sources):
            blocking.append(directive_name or "img-src")
    return sorted(set(blocking))


def configured_widget_selector_config(site: dict[str, Any], body: str) -> dict[str, Any]:
    widget_script = str(site.get("widget_script") or "widget.js")
    data_options = extract_widget_script_data_options(body, widget_script)
    inline_options = extract_widget_option_strings(body)
    options = {**data_options, **inline_options}
    bool_options = {
        **{key: value for key, value in data_options.items() if isinstance(value, bool)},
        **extract_widget_option_booleans(body),
    }
    if "widget_target" in site:
        target = site.get("widget_target")
        target_source = "site"
    elif "target" in options:
        target = options["target"]
        target_source = "option"
    else:
        target = "#haeorum-ai-search"
        target_source = "default"

    selectors = {
        "target": str(target or "").strip(),
        "attachToSearchInput": str(
            site.get("attach_to_search_input") if "attach_to_search_input" in site else options.get("attachToSearchInput") or ""
        ).strip(),
        "attachAfterSelector": str(
            site.get("attach_after_selector") if "attach_after_selector" in site else options.get("attachAfterSelector") or ""
        ).strip(),
    }
    selector_sources = {
        "target": target_source,
        "attachToSearchInput": "site"
        if "attach_to_search_input" in site
        else "option"
        if "attachToSearchInput" in options
        else "",
        "attachAfterSelector": "site"
        if "attach_after_selector" in site
        else "option"
        if "attachAfterSelector" in options
        else "",
    }
    selectors = {key: value for key, value in selectors.items() if value}
    selector_sources = {key: selector_sources[key] for key in selectors}
    if selector_sources.get("target") == "default" and (
        selectors.get("attachToSearchInput") or selectors.get("attachAfterSelector")
    ):
        selectors.pop("target", None)
        selector_sources.pop("target", None)
    explicit_selector_keys = sorted(
        key for key, value in selectors.items() if value and selector_sources.get(key) in {"site", "option"}
    )
    auto_attach_enabled = bool_options.get("autoAttach", True) is not False
    fallback_floating_enabled = bool_options.get("fallbackFloating", False) is True
    return {
        "selectors": selectors,
        "selector_sources": selector_sources,
        "explicit_selector_keys": explicit_selector_keys,
        "auto_attach_enabled": auto_attach_enabled,
        "auto_attach_allowed": auto_attach_enabled and not explicit_selector_keys,
        "fallback_floating_enabled": fallback_floating_enabled,
        "fallback_floating_allowed": fallback_floating_enabled and auto_attach_enabled,
    }


def configured_widget_selectors(site: dict[str, Any], body: str) -> dict[str, str]:
    return configured_widget_selector_config(site, body)["selectors"]


def verify_widget_selectors(body: str, selector_config: dict[str, Any]) -> dict[str, Any]:
    selectors = selector_config["selectors"]
    index = html_selector_index(body)
    matches_by_key = {key: selector_matches(index, selector) for key, selector in selectors.items()}
    match_counts = {key: len(matches) for key, matches in matches_by_key.items()}
    found = {key: count > 0 for key, count in match_counts.items()}
    hidden_selectors = sorted(
        key for key, matches in matches_by_key.items() if any(element_is_hidden(index.elements, element) for element in matches)
    )
    disabled_or_readonly_selectors = sorted(
        key
        for key, matches in matches_by_key.items()
        if key == "attachToSearchInput"
        and any(element_is_disabled_or_readonly(index.elements, element) for element in matches)
    )
    duplicate_id_refs = {
        key: refs
        for key, selector in selectors.items()
        for refs in [selector_duplicate_id_refs(index, selector)]
        if refs
    }
    multiple_match_selectors = sorted(key for key, count in match_counts.items() if count > 1)
    ambiguous_selectors = sorted(set(duplicate_id_refs) | set(multiple_match_selectors))
    selector_problems = [
        f"{key}_selector_duplicate_id:{element_id}"
        for key in ambiguous_selectors
        for element_id in sorted(duplicate_id_refs.get(key, {}))
    ]
    selector_problems.extend(
        f"{key}_selector_multiple_matches:{match_counts[key]}"
        for key in multiple_match_selectors
    )
    selector_problems.extend(f"{key}_selector_hidden" for key in hidden_selectors)
    selector_problems.extend(f"{key}_selector_disabled_or_readonly" for key in disabled_or_readonly_selectors)
    auto_attach = detect_auto_attach_mount(index) if selector_config["auto_attach_allowed"] else {
        "ok": False,
        "search_input_found": False,
        "attach_anchor_found": False,
    }
    explicit_keys = set(selector_config["explicit_selector_keys"])
    configured_mount_found = bool(found.get("target") or found.get("attachAfterSelector") or found.get("attachToSearchInput"))
    fallback_floating_mount = (
        selector_config.get("fallback_floating_allowed") is True
        and not configured_mount_found
        and not auto_attach["ok"]
    )
    missing = sorted(
        key
        for key, exists in found.items()
        if exists is False and not fallback_floating_mount and (key in explicit_keys or not auto_attach["ok"])
    )
    mount_selector_ok = bool(configured_mount_found or auto_attach["ok"] or fallback_floating_mount)
    if not mount_selector_ok:
        missing.append("mount_selector")
    mount_mode = (
        "target"
        if found.get("target")
        else "configured"
        if found.get("attachAfterSelector") or found.get("attachToSearchInput")
        else "auto"
        if auto_attach["ok"]
        else "floating"
        if fallback_floating_mount
        else "missing"
    )
    return {
        "selectors": selectors,
        "selector_sources": selector_config["selector_sources"],
        "explicit_selector_keys": selector_config["explicit_selector_keys"],
        "selector_match_counts": match_counts,
        "selector_found": found,
        "missing_selectors": sorted(set(missing)),
        "selector_duplicate_ids": duplicate_id_refs,
        "multiple_match_selectors": multiple_match_selectors,
        "ambiguous_selectors": ambiguous_selectors,
        "hidden_selectors": hidden_selectors,
        "disabled_or_readonly_selectors": disabled_or_readonly_selectors,
        "selector_problems": selector_problems,
        "selectors_unique": not ambiguous_selectors and not hidden_selectors and not disabled_or_readonly_selectors,
        "mount_selector_ok": mount_selector_ok,
        "mount_mode": mount_mode,
        "auto_attach_enabled": selector_config["auto_attach_enabled"],
        "auto_attach_allowed": selector_config["auto_attach_allowed"],
        "auto_attach_selector_found": auto_attach,
        "fallback_floating_enabled": selector_config["fallback_floating_enabled"],
        "fallback_floating_allowed": selector_config["fallback_floating_allowed"],
        "fallback_floating_mount": fallback_floating_mount,
    }


def detect_auto_attach_mount(index: HtmlSelectorIndex) -> dict[str, Any]:
    best_input: dict[str, Any] | None = None
    best_score = 0
    for element in index.elements:
        if element["tag"] != "input":
            continue
        score = score_auto_search_input(element, index)
        if score > best_score:
            best_score = score
            best_input = element
    ok = best_input is not None and best_score > 0
    return {
        "ok": ok,
        "search_input_found": ok,
        "attach_anchor_found": ok,
        "search_input_score": best_score,
        "search_input_selector": describe_element_selector(best_input) if best_input else "",
    }


def score_auto_search_input(element: dict[str, Any], index: HtmlSelectorIndex | None = None) -> int:
    attrs = element["attrs"]
    input_type = str(attrs.get("type") or "text").strip().lower()
    if input_type in AUTO_SEARCH_BAD_INPUT_TYPES:
        return 0
    if index is not None and (
        element_is_hidden(index.elements, element) or element_is_disabled_or_readonly(index.elements, element)
    ):
        return 0
    id_value = str(attrs.get("id") or "").lower()
    name = str(attrs.get("name") or "").lower()
    classes = str(attrs.get("class") or "").lower()
    placeholder = str(attrs.get("placeholder") or "").lower()
    label = f"{attrs.get('aria-label') or ''} {attrs.get('title') or ''}".lower()
    haystack = " ".join([id_value, name, classes, placeholder, label])
    score = 0
    if input_type == "search":
        score += 60
    if re.search(r"(^|[-_\s])(search|srch|keyword|query|q|s)([-_\s]|$)", " ".join([id_value, name, classes])):
        score += 40
    if re.search(r"(검색|상품명|품명|키워드|찾기)", haystack):
        score += 40
    if re.search(r"(search|srch|keyword|query|product)", haystack):
        score += 30
    return score


def describe_element_selector(element: dict[str, Any] | None) -> str:
    if not element:
        return ""
    attrs = element["attrs"]
    if attrs.get("id"):
        return f"#{attrs['id']}"
    if attrs.get("name"):
        return f"{element['tag']}[name=\"{attrs['name']}\"]"
    if attrs.get("type"):
        return f"{element['tag']}[type=\"{attrs['type']}\"]"
    return str(element["tag"])


def selector_exists(index: HtmlSelectorIndex, selector: str) -> bool:
    return selector_match_count(index, selector) > 0


def selector_match_count(index: HtmlSelectorIndex, selector: str) -> int:
    return len(selector_matches(index, selector))


def selector_matches(index: HtmlSelectorIndex, selector: str) -> list[dict[str, Any]]:
    selector_text = str(selector or "").strip()
    if not selector_text:
        return []
    matches = [
        element
        for part in selector_text.split(",")
        for element in selector_chain_matches(index, part.strip())
        if part.strip()
    ]
    return unique_elements(matches)


def html_id_counts(index: HtmlSelectorIndex) -> dict[str, int]:
    counts: dict[str, int] = {}
    for element in index.elements:
        element_id = str(element.get("attrs", {}).get("id") or "")
        if not element_id:
            continue
        counts[element_id] = counts.get(element_id, 0) + 1
    return counts


def selector_referenced_ids(selector: str) -> list[str]:
    ids = re.findall(r"#([^\s>+~,\[.#]+)", str(selector or ""))
    return sorted(set(ids))


def selector_duplicate_id_refs(index: HtmlSelectorIndex, selector: str) -> dict[str, int]:
    counts = html_id_counts(index)
    return {
        element_id: counts[element_id]
        for element_id in selector_referenced_ids(selector)
        if counts.get(element_id, 0) > 1
    }


def selector_chain_exists(index: HtmlSelectorIndex, selector: str) -> bool:
    return bool(selector_chain_matches(index, selector))


def selector_chain_matches(index: HtmlSelectorIndex, selector: str) -> list[dict[str, Any]]:
    parts = selector_sequence_parts(selector)
    if not parts:
        return []
    _first_combinator, first_selector = parts[0]
    candidates = [
        element
        for element in index.elements
        if simple_selector_matches(element, first_selector)
    ]
    for combinator, simple_selector in parts[1:]:
        next_candidates = []
        for element in index.elements:
            if not simple_selector_matches(element, simple_selector):
                continue
            if combinator == "child":
                if any(element.get("parent") == candidate.get("index") for candidate in candidates):
                    next_candidates.append(element)
            elif any(is_descendant_of(index, int(element["index"]), int(candidate["index"])) for candidate in candidates):
                next_candidates.append(element)
        candidates = next_candidates
        if not candidates:
            return []
    return unique_elements(candidates)


def unique_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    unique = []
    for element in elements:
        index = int(element.get("index") or 0)
        if index in seen:
            continue
        seen.add(index)
        unique.append(element)
    return unique


def simple_selector_exists(index: HtmlSelectorIndex, selector: str) -> bool:
    return any(simple_selector_matches(element, selector) for element in index.elements)


def selector_sequence_parts(selector: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    buffer: list[str] = []
    pending_combinator = "descendant"
    bracket_depth = 0
    quote = ""
    for char in str(selector or "").strip():
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            buffer.append(char)
            quote = char
            continue
        if char == "[":
            bracket_depth += 1
            buffer.append(char)
            continue
        if char == "]" and bracket_depth:
            bracket_depth -= 1
            buffer.append(char)
            continue
        if bracket_depth == 0 and char in {"+", "~"}:
            return []
        if bracket_depth == 0 and char == ">":
            if buffer:
                parts.append((pending_combinator, "".join(buffer).strip()))
                buffer = []
            pending_combinator = "child"
            continue
        if bracket_depth == 0 and char.isspace():
            if buffer:
                parts.append((pending_combinator, "".join(buffer).strip()))
                buffer = []
                pending_combinator = "descendant"
            continue
        buffer.append(char)
    if buffer:
        parts.append((pending_combinator, "".join(buffer).strip()))
    return [(combinator, simple) for combinator, simple in parts if simple]


def is_descendant_of(index: HtmlSelectorIndex, child_index: int, ancestor_index: int) -> bool:
    parent = index.elements[child_index].get("parent")
    while parent is not None:
        if parent == ancestor_index:
            return True
        parent = index.elements[parent].get("parent")
    return False


def simple_selector_matches(element: dict[str, Any], selector: str) -> bool:
    selector = selector.strip()
    fallback_id = simple_hash_id_selector_value(selector)
    if fallback_id and element["attrs"].get("id") == fallback_id:
        return True
    if not selector or ":" in selector:
        return False
    attr_matches = re.findall(r"\[([\w:-]+)(?:\s*=\s*(['\"]?)(.*?)\2)?\]", selector)
    selector_without_attrs = re.sub(r"\[[^\]]+\]", "", selector)
    id_match = re.search(r"#([\w:-]+)", selector_without_attrs)
    class_matches = re.findall(r"\.([\w:-]+)", selector_without_attrs)
    tag = re.split(r"[#.]", selector_without_attrs, maxsplit=1)[0].strip().lower()
    if tag == "*":
        tag = ""
    attrs = element["attrs"]
    if tag and element["tag"] != tag:
        return False
    if id_match and attrs.get("id") != id_match.group(1):
        return False
    classes = set(str(attrs.get("class") or "").split())
    if any(class_name not in classes for class_name in class_matches):
        return False
    for attr_name, _quote, expected in attr_matches:
        attr_value = attrs.get(attr_name.lower())
        if attr_value is None:
            return False
        if expected and attr_value != expected:
            return False
    return True


def simple_hash_id_selector_value(selector: str) -> str:
    selector_text = str(selector or "").strip()
    if not selector_text.startswith("#") or re.search(r"[\s>+~,]", selector_text):
        return ""
    element_id = selector_text[1:]
    if not element_id or not re.search(r"[^\w-]", element_id):
        return ""
    return element_id


def check_widget_init(site: dict[str, Any], timeout: int, expected_api_base_url: str = "") -> dict[str, Any]:
    url = str(site.get("url") or "").strip()
    if not url:
        return {"name": WIDGET_INIT_CHECK_NAME, "ok": False, "message": "site url is missing"}
    if site.get("expect_widget", True) is False:
        return {
            "name": WIDGET_INIT_CHECK_NAME,
            "ok": True,
            "url": url,
            "skipped": True,
            "message": "expect_widget is false",
        }

    fetched = fetch_text(url, DESKTOP_UA, timeout)
    response_policies = [str(policy) for policy in (fetched.get("csp_headers") or []) if str(policy).strip()]
    body = fetched.pop("body", "")
    selector_evidence = verify_widget_selectors(body, configured_widget_selector_config(site, body))
    widget_global = str(site.get("widget_global") or "HaeorumAISearch")
    widget_script = str(site.get("widget_script") or "widget.js")
    data_options = extract_widget_script_data_options(body, widget_script)
    inline_options = extract_widget_option_strings(body)
    widget_options = {**data_options, **inline_options}
    mall_id = str(site["mall_id"])
    manual_init_call_found = re.search(rf"\b{re.escape(widget_global)}\b\s*(?:\.|\?\.)\s*init\s*\(", body) is not None
    auto_init_found = extract_widget_script_auto_init_found(body, widget_script)
    init_call_found = manual_init_call_found or auto_init_found
    id_patterns = {
        "mallId": re.compile(rf"['\"]?mallId['\"]?\s*:\s*['\"]{re.escape(mall_id)}['\"]"),
        "mall_id": re.compile(rf"['\"]?mall_id['\"]?\s*:\s*['\"]{re.escape(mall_id)}['\"]"),
        "siteId": re.compile(rf"['\"]?siteId['\"]?\s*:\s*['\"]{re.escape(mall_id)}['\"]"),
        "site_id": re.compile(rf"['\"]?site_id['\"]?\s*:\s*['\"]{re.escape(mall_id)}['\"]"),
    }
    id_keys_found = [key for key, pattern in id_patterns.items() if pattern.search(body)]
    if data_options.get("mallId") == mall_id and "data-mall-id" not in id_keys_found:
        id_keys_found.append("data-mall-id")
    if data_options.get("siteId") == mall_id and "data-site-id" not in id_keys_found:
        id_keys_found.append("data-site-id")
    if inline_options.get("mallId") == mall_id and "mallId" not in id_keys_found:
        id_keys_found.append("mallId")
    if inline_options.get("siteId") == mall_id and "siteId" not in id_keys_found:
        id_keys_found.append("siteId")
    widget_script_found = widget_script in body
    require_mall_id = site.get("expect_mall_id_marker", True) is not False
    api_base_url = str(site.get("api_base_url") or expected_api_base_url or "").rstrip("/")
    api_base_url_init_option = str(widget_options.get("apiBaseUrl") or "").rstrip("/")
    api_base_url_script_src = extract_widget_script_api_base_url(body, widget_script)
    api_base_url_resolved = api_base_url_init_option or api_base_url_script_src
    api_base_url_source = (
        "init_option"
        if inline_options.get("apiBaseUrl")
        else "script_data"
        if data_options.get("apiBaseUrl")
        else "script_src"
        if api_base_url_script_src
        else ""
    )
    api_base_url_page_marker_found = bool(api_base_url and api_base_url in body)
    api_base_url_marker_found = bool(api_base_url and api_base_url_resolved == api_base_url)
    require_api_base_url = site.get("expect_api_base_url_marker", True) is not False
    missing = []
    if not init_call_found:
        missing.append(f"{widget_global}.init call or data-hai-auto-init")
    if not widget_script_found:
        missing.append(widget_script)
    if require_mall_id and not id_keys_found:
        missing.append(f"mallId/siteId={mall_id}")
    if require_api_base_url and not api_base_url_marker_found:
        missing.append(f"apiBaseUrl/scriptSrc={api_base_url}" if api_base_url else "apiBaseUrl/scriptSrc")
    missing.extend(selector_evidence["missing_selectors"])
    missing.extend(selector_evidence["selector_problems"])
    csp_evidence = widget_csp_evidence(
        body,
        page_url=url,
        widget_script=widget_script,
        api_base_url=api_base_url_resolved or api_base_url,
        response_policies=response_policies,
        manual_init_call_found=manual_init_call_found,
        auto_init_found=auto_init_found,
    )

    return {
        "name": WIDGET_INIT_CHECK_NAME,
        "ok": fetched.get("ok") is True and not missing and csp_evidence["ok"] is True,
        "url": url,
        "status": fetched.get("status"),
        "elapsed_ms": fetched.get("elapsed_ms"),
        "content_type": fetched.get("content_type"),
        "init_call_found": init_call_found,
        "manual_init_call_found": manual_init_call_found,
        "auto_init_found": auto_init_found,
        "widget_script_found": widget_script_found,
        "mall_or_site_id_found": bool(id_keys_found),
        "id_keys_found": id_keys_found,
        "api_base_url_marker_found": api_base_url_marker_found,
        "api_base_url_page_marker_found": api_base_url_page_marker_found,
        "api_base_url_init_option": api_base_url_init_option,
        "api_base_url_script_src": api_base_url_script_src,
        "api_base_url_resolved": api_base_url_resolved,
        "api_base_url_source": api_base_url_source,
        "api_base_url_required": require_api_base_url,
        "csp": csp_evidence,
        "csp_problems": csp_evidence["problems"],
        **selector_evidence,
        "missing_markers": missing,
        **({"error": fetched["error"]} if fetched.get("error") else {}),
    }


def check_widget_script_asset(
    site: dict[str, Any],
    timeout: int,
    widget_init_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_url = str(site.get("url") or "").strip()
    if not page_url:
        return {"name": WIDGET_SCRIPT_ASSET_CHECK_NAME, "ok": False, "message": "site url is missing"}
    if site.get("expect_widget", True) is False:
        return {
            "name": WIDGET_SCRIPT_ASSET_CHECK_NAME,
            "ok": True,
            "url": "",
            "page_url": page_url,
            "skipped": True,
            "message": "expect_widget is false",
        }

    widget_script = str(site.get("widget_script") or "widget.js")
    widget_global = str(site.get("widget_global") or "HaeorumAISearch")
    script_url = ""
    source = ""
    if widget_init_check:
        csp = widget_init_check.get("csp") if isinstance(widget_init_check.get("csp"), dict) else {}
        script_url = str(csp.get("widget_src") or "").strip()
        if script_url:
            source = "widget_init.csp.widget_src"

    if not script_url:
        fetched_page = fetch_text(page_url, DESKTOP_UA, timeout)
        body = fetched_page.pop("body", "")
        script_url = absolute_widget_script_src(body, widget_script, page_url)
        source = "page_html"

    if not script_url:
        return {
            "name": WIDGET_SCRIPT_ASSET_CHECK_NAME,
            "ok": False,
            "url": "",
            "page_url": page_url,
            "script_url_source": source,
            "missing_markers": [widget_script],
            "message": "widget script src is missing",
        }

    url_error = validate_widget_script_url(script_url)
    if url_error:
        return {
            "name": WIDGET_SCRIPT_ASSET_CHECK_NAME,
            "ok": False,
            "url": script_url,
            "page_url": page_url,
            "script_url_source": source,
            "url_error": url_error,
            "missing_markers": [],
        }

    fetched = fetch_text(script_url, DESKTOP_UA, timeout)
    body = fetched.pop("body", "")
    content_type = fetched.get("content_type")
    content_type_ok = javascript_content_type_ok(content_type)
    body_looks_html = response_body_looks_html(body)
    widget_global_found = widget_global in body
    init_marker_found = re.search(r"\binit\b", body) is not None
    missing = []
    if not widget_global_found:
        missing.append(widget_global)
    if not init_marker_found:
        missing.append("init")
    ok = (
        fetched.get("ok") is True
        and content_type_ok
        and not body_looks_html
        and not missing
    )
    return {
        "name": WIDGET_SCRIPT_ASSET_CHECK_NAME,
        "ok": ok,
        "url": script_url,
        "page_url": page_url,
        "script_url_source": source,
        "status": fetched.get("status"),
        "elapsed_ms": fetched.get("elapsed_ms"),
        "content_type": content_type,
        "content_type_ok": content_type_ok,
        "body_looks_html": body_looks_html,
        "widget_global_found": widget_global_found,
        "init_marker_found": init_marker_found,
        "missing_markers": missing,
        **({"error": fetched["error"]} if fetched.get("error") else {}),
    }


def check_saved_widget_probe_sources(site: dict[str, Any], api_base_url: str) -> dict[str, Any]:
    entries = probe_source_entries(site)
    if not has_local_explicit_probe_sources(entries):
        return {
            "name": SAVED_WIDGET_PROBE_CHECK_NAME,
            "ok": True,
            "skipped": True,
            "message": "no local saved widget probe sources configured",
            "source_count": len(entries),
            "local_source_count": 0,
            "sources": [],
            "problems": [],
        }
    missing_variants = missing_pc_mobile_variants(entries)
    problems = [
        f"missing_{variant}_saved_html"
        for variant in missing_variants
    ]
    mall_id = str(site.get("mall_id") or site.get("site_id") or "").strip()
    api_key = str(site.get("api_key") or "").strip()
    page_url = str(site.get("url") or "").strip()
    widget_src = site_widget_src(site, api_base_url, "")
    sources: list[dict[str, Any]] = []
    for entry in entries:
        source = str(entry.get("source") or "").strip()
        if is_remote_probe_source(source):
            continue
        source_path = Path(source)
        source_result: dict[str, Any] = {
            "source": source,
            "raw_source": entry.get("raw_source"),
            "variant": entry.get("variant"),
            "ok": False,
        }
        try:
            body = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            source_result.update({"error": str(exc), "error_type": exc.__class__.__name__})
            problems.append(f"{source}: read_failed:{exc.__class__.__name__}")
            sources.append(source_result)
            continue
        report = analyze_html_source(
            str(source_path),
            body,
            api_base_url=api_base_url,
            widget_src=widget_src,
            mall_id=mall_id,
            api_key=api_key,
            page_url=page_url,
        )
        recommendation = report.get("recommendation") if isinstance(report.get("recommendation"), dict) else {}
        source_problems: list[str] = []
        if report.get("data_auto_init_ready") is not True:
            risks = report.get("blocking_risks") or report.get("risks") or ["not_ready"]
            source_problems.append("data_auto_init_not_ready:" + ",".join(str(risk) for risk in risks))
        elif report.get("ok") is not True:
            risks = report.get("risks") or ["unsafe_recommended_selectors"]
            source_problems.append("unsafe_recommended_selectors:" + ",".join(str(risk) for risk in risks))
        elif recommendation.get("ready") is True and not bool((report.get("existing_widget") or {}).get("found")):
            snippet = str(recommendation.get("snippet") or "").strip()
            if not snippet:
                source_problems.append("preview_snippet_missing")
            else:
                preview_body = inject_snippet_preview_html(body, snippet)
                validation = validate_preview_html_body(
                    preview_body,
                    snippet,
                    name=str(source_path),
                    mall_id=mall_id,
                    source_file=str(source_path),
                )
                if validation.get("ok") is not True:
                    preview_problems = ",".join(str(problem) for problem in validation.get("problems") or ["failed"])
                    source_problems.append(f"preview_validation_failed:{preview_problems}")
        if source_problems:
            problems.extend(f"{source}: {problem}" for problem in source_problems)
        source_result.update(
            {
                "ok": not source_problems,
                "candidate_count": report.get("candidate_count"),
                "data_auto_init_ready": report.get("data_auto_init_ready"),
                "recommendation_ready": recommendation.get("ready") if recommendation else None,
                "blocking_risks": report.get("blocking_risks") or [],
                "review_risks": report.get("review_risks") or [],
                "risks": report.get("risks") or [],
                "problems": source_problems,
            }
        )
        sources.append(source_result)
    return {
        "name": SAVED_WIDGET_PROBE_CHECK_NAME,
        "ok": not problems and bool(sources),
        "source_count": len(entries),
        "local_source_count": len(sources),
        "missing_variants": missing_variants,
        "sources": sources,
        "problems": problems,
    }


def check_result_image_csp(
    site: dict[str, Any],
    search_products: dict[str, list[dict[str, Any]]],
    widget_init_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_url = str(site.get("url") or "").strip()
    csp = widget_init_check.get("csp") if isinstance((widget_init_check or {}).get("csp"), dict) else {}
    policies = [str(policy) for policy in (csp.get("policies") or []) if str(policy).strip()]
    image_urls_by_mode: dict[str, list[str]] = {}
    unique_image_urls: dict[str, dict[str, Any]] = {}
    unsafe_image_urls = []
    for mode, products in search_products.items():
        for item in products:
            product = item.get("product") if isinstance(item, dict) else None
            if not isinstance(product, dict):
                continue
            image_url = str(product.get("image_url") or "").strip()
            if not image_url:
                continue
            image_urls_by_mode.setdefault(mode, [])
            if image_url not in image_urls_by_mode[mode]:
                image_urls_by_mode[mode].append(image_url)
            url_error = validate_url(image_url)
            if url_error:
                unsafe_image_urls.append(
                    {
                        "mode": mode,
                        "product_id": product.get("product_id"),
                        "image_url": image_url,
                        "message": url_error,
                    }
                )
                continue
            unique_image_urls.setdefault(
                image_url,
                {
                    "image_url": image_url,
                    "origin": url_origin(image_url),
                    "modes": [],
                    "product_ids": [],
                },
            )
            if mode not in unique_image_urls[image_url]["modes"]:
                unique_image_urls[image_url]["modes"].append(mode)
            product_id = str(product.get("product_id") or "").strip()
            if product_id and product_id not in unique_image_urls[image_url]["product_ids"]:
                unique_image_urls[image_url]["product_ids"].append(product_id)

    blocked = []
    for image_url, item in sorted(unique_image_urls.items()):
        blocking_directives = csp_image_url_blocking_directives(policies, image_url, page_url)
        if blocking_directives:
            blocked.append(
                {
                    "image_url": image_url,
                    "origin": item["origin"],
                    "modes": sorted(item["modes"]),
                    "product_ids": item["product_ids"][:10],
                    "blocking_directives": blocking_directives,
                }
            )
    ok = bool(unique_image_urls) and not unsafe_image_urls and not blocked
    return {
        "name": RESULT_IMAGE_CSP_CHECK_NAME,
        "ok": ok,
        "page_url": page_url,
        "policy_count": len(policies),
        "checked": len(unique_image_urls),
        "image_origins": sorted({item["origin"] for item in unique_image_urls.values() if item["origin"]}),
        "image_urls_by_mode": {mode: len(urls) for mode, urls in sorted(image_urls_by_mode.items())},
        "blocked_image_urls": blocked,
        "unsafe_image_urls": unsafe_image_urls,
        "message": "" if unique_image_urls else "search results contain no image_url evidence",
    }


def api_headers(site: dict[str, Any], default_api_key: str = "") -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = str(site.get("api_key") or default_api_key or "").strip()
    origin = str(site.get("origin") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    if origin:
        headers["Origin"] = origin
    return headers


def search_payload(site: dict[str, Any], mode: str, limit: int, image_base64: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"mall_id": site["mall_id"], "limit": limit}
    queries = site.get("queries") if isinstance(site.get("queries"), dict) else {}
    if mode in {"text", "mixed"}:
        payload["q"] = str(queries.get(mode) or DEFAULT_QUERIES[mode])
    if mode in {"image", "mixed"}:
        payload["image_base64"] = image_base64
    return payload


def check_search(
    site: dict[str, Any],
    api_base_url: str,
    mode: str,
    headers: dict[str, str],
    image_base64: str,
    limit: int,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str], list[dict[str, Any]]]:
    check_name = SEARCH_CHECK_NAMES[mode]
    response = request_json(
        "POST",
        f"{api_base_url.rstrip('/')}/api/ai-search",
        search_payload(site, mode, limit, image_base64),
        headers,
        timeout,
    )
    data = response.get("data") or {}
    expected_query_type = "text_image" if mode == "mixed" else mode
    expected_mall_id = str(site["mall_id"]).strip()
    top = data.get("top") if isinstance(data.get("top"), list) else []
    items = data.get("items") if isinstance(data.get("items"), list) else []
    categories = data.get("suggested_categories") if isinstance(data.get("suggested_categories"), list) else []
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    missing_meta_fields = sorted(REQUIRED_META_FIELDS - set(meta or {}))
    meta_contract_problems = validate_meta_shape(meta, expected_query_type, expected_mall_id) if not missing_meta_fields else []
    ok = (
        response.get("ok") is True
        and meta.get("query_type") == expected_query_type
        and str(meta.get("engine") or "").strip().lower() == "marqo"
        and not missing_meta_fields
        and not meta_contract_problems
        and isinstance(items, list)
        and isinstance(categories, list)
        and len(top) > 0
        and len(top) <= 3
        and len(items) > 0
        and len(categories) > 0
    )
    first = top[0] if top and isinstance(top[0], dict) else None
    missing_top_fields = sorted(REQUIRED_RESULT_FIELDS - set(first or {}))
    first_item = items[0] if items and isinstance(items[0], dict) else None
    missing_item_fields = sorted(REQUIRED_RESULT_FIELDS - set(first_item or {})) if first_item else []
    source_scores_ok = isinstance((first or {}).get("source_scores"), dict) and (
        not first_item or isinstance(first_item.get("source_scores"), dict)
    )
    result_contract_problems = []
    result_products: list[dict[str, Any]] = []
    result_mall_ids = set()
    result_mall_id_mismatches = []
    for section, results in [("top", top), ("items", items)]:
        for index, item in enumerate(results):
            if isinstance(item, dict):
                result_contract_problems.extend(validate_result_item_shape(item, section, index, expected_mall_id))
                result_mall_id = str(item.get("mall_id") or "").strip()
                if result_mall_id:
                    result_mall_ids.add(result_mall_id)
                if result_mall_id != expected_mall_id:
                    result_mall_id_mismatches.append(
                        {
                            "section": section,
                            "index": index,
                            "product_id": item.get("product_id"),
                            "mall_id": result_mall_id or None,
                            "expected_mall_id": expected_mall_id,
                        }
                    )
                result_products.append({"section": section, "index": index, "product": item})
            else:
                result_contract_problems.append(f"{section}_{index}_object")
    top_product_ids = [str(item.get("product_id")) for item in top if isinstance(item, dict)]
    item_product_ids = [str(item.get("product_id")) for item in items if isinstance(item, dict)]
    repeated_product_ids = sorted(set(top_product_ids).intersection(item_product_ids))
    duplicate_top_product_ids = len(set(top_product_ids)) != len(top_product_ids)
    duplicate_item_product_ids = len(set(item_product_ids)) != len(item_product_ids)
    duplicate_categories = len({str(category).strip() for category in categories}) != len(categories)
    related_items_exclude_top = not repeated_product_ids and not duplicate_top_product_ids and not duplicate_item_product_ids
    related_items_limit_ok = isinstance(meta.get("limit"), int) and len(items) <= meta.get("limit")
    next_offset_consistent = meta.get("has_more") is not True or meta.get("next_offset") == meta.get("offset") + len(items)
    categories_unique = not duplicate_categories
    ok = (
        ok
        and not missing_top_fields
        and not missing_item_fields
        and source_scores_ok
        and not result_contract_problems
        and related_items_exclude_top
        and related_items_limit_ok
        and next_offset_consistent
        and categories_unique
    )
    return (
        {
            "name": check_name,
            "ok": ok,
            "status": response.get("status"),
            "elapsed_ms": response.get("elapsed_ms"),
            "query_type": meta.get("query_type"),
            "engine": meta.get("engine"),
            "engine_ok": str(meta.get("engine") or "").strip().lower() == "marqo",
            "expected_mall_id": expected_mall_id,
            "meta_mall_id": meta.get("mall_id"),
            "top_count": len(top),
            "item_count": len(items),
            "category_count": len(categories),
            "suggested_categories": [str(category) for category in categories[:15]],
            "missing_meta_fields": missing_meta_fields,
            "meta_contract_problems": meta_contract_problems,
            "missing_top_fields": missing_top_fields,
            "missing_item_fields": missing_item_fields,
            "source_scores_ok": source_scores_ok,
            "result_contract_problems": result_contract_problems,
            "result_mall_ids": sorted(result_mall_ids),
            "result_mall_id_mismatches": result_mall_id_mismatches[:20],
            "related_items_exclude_top": related_items_exclude_top,
            "related_items_limit_ok": related_items_limit_ok,
            "next_offset_consistent": next_offset_consistent,
            "repeated_product_ids": repeated_product_ids,
            "categories_unique": categories_unique,
            **({"error": response["error"]} if response.get("error") else {}),
        },
        first,
        [str(category).strip() for category in categories if str(category).strip()],
        result_products,
    )


def mode_check_name(mode: str, check_name: str) -> str:
    return f"{mode}_{check_name}"


def check_category_refetch(
    site: dict[str, Any],
    api_base_url: str,
    headers: dict[str, str],
    categories: list[str],
    image_base64: str,
    limit: int,
    timeout: int,
) -> dict[str, Any]:
    category = next((str(item).strip() for item in categories if str(item).strip()), "")
    if not category:
        return {"name": "text_category_refetch", "ok": False, "message": "text search returned no suggested category"}

    payload = search_payload(site, "text", limit, image_base64)
    payload["category"] = category
    payload["offset"] = 0
    response = request_json(
        "POST",
        f"{api_base_url.rstrip('/')}/api/ai-search",
        payload,
        headers,
        timeout,
    )
    data = response.get("data") or {}
    top = data.get("top") if isinstance(data.get("top"), list) else []
    items = data.get("items") if isinstance(data.get("items"), list) else []
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    products = [product for product in [*top, *items] if isinstance(product, dict)]
    mismatched = [
        {
            "product_id": product.get("product_id"),
            "category": product.get("category"),
        }
        for product in products
        if str(product.get("category") or "").strip() != category
    ]
    ok = (
        response.get("ok") is True
        and meta.get("query_type") == "text"
        and bool(products)
        and not mismatched
    )
    return {
        "name": "text_category_refetch",
        "ok": ok,
        "status": response.get("status"),
        "elapsed_ms": response.get("elapsed_ms"),
        "category": category,
        "result_count": len(products),
        "mismatched_categories": mismatched,
        **({"error": response["error"]} if response.get("error") else {}),
    }


def check_detail_url(
    product: dict[str, Any] | None,
    timeout: int,
    name: str = "detail_url",
    label: str = "first result",
    site: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_url = str((product or {}).get("product_url") or "").strip()
    product_id = str((product or {}).get("product_id") or "").strip()
    if not product_url:
        return {"name": name, "ok": False, "message": f"{label} has no product_url"}
    if site is not None:
        product_rule = build_product_url_rule_result(site, product_url, f"{name}_product_url_rule", label, product_id)
        if product_rule.get("ok") is not True:
            rule_message = str(product_rule.get("message") or "product_url does not match expected representative mall rule")
            return {
                "name": name,
                "ok": False,
                "url": product_url,
                "message": f"detail_url fetch skipped because {rule_message}",
                "product_url_rule": {key: value for key, value in product_rule.items() if key != "name"},
            }
    else:
        product_url_error = validate_url(product_url)
        if product_url_error:
            return {
                "name": name,
                "ok": False,
                "url": product_url,
                "message": f"product_url is invalid: {product_url_error}",
            }
    fetched = fetch_text(product_url, DESKTOP_UA, timeout)
    return {
        "name": name,
        "ok": fetched.get("ok") is True,
        "url": product_url,
        "status": fetched.get("status"),
        "elapsed_ms": fetched.get("elapsed_ms"),
        **({"error": fetched["error"]} if fetched.get("error") else {}),
    }


def expected_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def check_product_url_rule(
    site: dict[str, Any],
    product: dict[str, Any] | None,
    name: str = "product_url_rule",
    label: str = "first result",
) -> dict[str, Any]:
    product_url = str((product or {}).get("product_url") or "").strip()
    product_id = str((product or {}).get("product_id") or "").strip()
    return build_product_url_rule_result(site, product_url, name, label, product_id)


def check_all_product_url_rules(
    site: dict[str, Any],
    products: list[dict[str, Any]],
    name: str = "all_product_url_rules",
) -> dict[str, Any]:
    failures = []
    checked = 0
    for item in products:
        product = item.get("product") if isinstance(item, dict) else None
        if not isinstance(product, dict):
            continue
        checked += 1
        section = str(item.get("section") or "result")
        index = int(item.get("index") or 0)
        product_id = str(product.get("product_id") or "").strip()
        label = f"{section}[{index}] result" + (f" product {product_id}" if product_id else "")
        rule = build_product_url_rule_result(
            site,
            str(product.get("product_url") or "").strip(),
            name="product_url_rule",
            label=label,
            product_id=product_id,
        )
        if rule.get("ok") is not True:
            failures.append(
                {
                    "section": section,
                    "index": index,
                    "product_id": product_id or None,
                    "url": rule.get("url"),
                    "message": rule.get("message"),
                    "matched_prefixes": rule.get("matched_prefixes") or [],
                    "expected_prefixes": rule.get("expected_prefixes") or [],
                    "product_id_match": rule.get("product_id_match"),
                }
            )
    return {
        "name": name,
        "ok": checked > 0 and not failures,
        "checked": checked,
        "failed": len(failures),
        "failures": failures[:20],
        "failure_overflow": max(0, len(failures) - 20),
    }


def build_product_url_rule_result(
    site: dict[str, Any],
    product_url: str,
    name: str = "product_url_rule",
    label: str = "first result",
    product_id: str = "",
) -> dict[str, Any]:
    if not product_url:
        return {"name": name, "ok": False, "message": f"{label} has no product_url"}
    product_url_error = validate_url(product_url)
    if product_url_error:
        return {
            "name": name,
            "ok": False,
            "url": product_url,
            "message": f"product_url is invalid: {product_url_error}",
        }

    prefixes = expected_list(site.get("expected_product_url_prefix"))
    if not prefixes and site.get("origin"):
        prefixes = [str(site["origin"]).rstrip("/")]
    contains = expected_list(site.get("expected_product_url_contains"))
    pattern = str(site.get("expected_product_url_pattern") or "").strip()

    if not prefixes and not contains and not pattern:
        return {
            "name": name,
            "ok": False,
            "url": product_url,
            "message": "expected_product_url_prefix, expected_product_url_contains, expected_product_url_pattern, or origin is required",
        }
    invalid_prefixes = [
        {"prefix": prefix, "message": prefix_error}
        for prefix in prefixes
        for prefix_error in [validate_product_url_prefix(prefix)]
        if prefix_error
    ]
    if invalid_prefixes:
        return {
            "name": name,
            "ok": False,
            "url": product_url,
            "expected_prefixes": prefixes,
            "invalid_prefixes": invalid_prefixes,
            "message": "expected_product_url_prefix is invalid",
        }

    prefix_matches = [prefix for prefix in prefixes if url_matches_prefix(product_url, prefix)]
    prefix_ok = not prefixes or bool(prefix_matches)
    contains_ok = all(token in product_url for token in contains)
    try:
        pattern_ok = not pattern or re.search(pattern, product_url) is not None
        pattern_error = None
    except re.error as exc:
        pattern_ok = False
        pattern_error = str(exc)

    product_id_text = str(product_id or "").strip()
    product_id_match = not product_id_text or product_url_contains_product_id(product_url, product_id_text)
    ok = prefix_ok and contains_ok and pattern_ok and product_id_match
    message = None
    if pattern_error:
        message = None
    elif not prefix_ok or not contains_ok or not pattern_ok:
        message = "product_url does not match expected representative mall rule"
    elif not product_id_match:
        message = "product_url does not contain result product_id"
    return {
        "name": name,
        "ok": ok,
        "url": product_url,
        "expected_product_id": product_id_text or None,
        "product_id_match": product_id_match,
        "expected_prefixes": prefixes,
        "matched_prefixes": prefix_matches,
        "expected_contains": contains,
        "expected_pattern": pattern or None,
        **({"pattern_error": pattern_error} if pattern_error else {}),
        **({"message": message} if message else {}),
    }


def url_matches_prefix(url: str, prefix: str) -> bool:
    prefix_text = str(prefix or "").strip().rstrip("/")
    if not prefix_text:
        return False
    if validate_product_url_prefix(prefix_text) is not None:
        return False
    parsed_url = urlparse(url)
    parsed_prefix = urlparse(prefix_text)
    try:
        url_port = parsed_url.port
        prefix_port = parsed_prefix.port
    except ValueError:
        return False
    if parsed_url.scheme != parsed_prefix.scheme:
        return False
    if (parsed_url.hostname or "").lower() != (parsed_prefix.hostname or "").lower():
        return False
    if normalized_url_port(parsed_url.scheme, url_port) != normalized_url_port(parsed_prefix.scheme, prefix_port):
        return False
    prefix_path = parsed_prefix.path.rstrip("/")
    if not prefix_path:
        return True
    url_path = parsed_url.path.rstrip("/")
    return url_path == prefix_path or url_path.startswith(prefix_path + "/")


def normalized_url_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def check_click_log(
    site: dict[str, Any],
    api_base_url: str,
    headers: dict[str, str],
    product: dict[str, Any] | None,
    timeout: int,
    mode: str = "text",
    name: str = "click_log",
) -> dict[str, Any]:
    product_id = str((product or {}).get("product_id") or "").strip()
    if not product_id:
        return {"name": name, "ok": False, "message": f"first {mode} result has no product_id"}
    query_type = "text_image" if mode == "mixed" else mode
    payload: dict[str, Any] = {
        "mall_id": site["mall_id"],
        "product_id": product_id,
        "position": 1,
        "query_type": query_type,
        "score_percent": (product or {}).get("score_percent"),
        "product_url": (product or {}).get("product_url"),
    }
    if mode in {"text", "mixed"}:
        payload["query"] = DEFAULT_QUERIES["mixed" if mode == "mixed" else "text"]
    response = request_json(
        "POST",
        f"{api_base_url.rstrip('/')}/api/click-log",
        payload,
        headers,
        timeout,
    )
    data = response.get("data") or {}
    return {
        "name": name,
        "ok": response.get("ok") is True and data.get("ok") is True,
        "status": response.get("status"),
        "elapsed_ms": response.get("elapsed_ms"),
        "product_id": product_id,
        "query_type": query_type,
        **({"error": response["error"]} if response.get("error") else {}),
    }


def evaluate_site(site: dict[str, Any], args: argparse.Namespace, image_base64: str) -> dict[str, Any]:
    api_base_url = str(site.get("api_base_url") or args.api_base_url).rstrip("/")
    site_api_key_hash = api_key_hash(site.get("api_key") or args.api_key)
    headers = api_headers(site, args.api_key)
    checks: list[dict[str, Any]] = [validate_site_config(site, args)]
    widget_init_check: dict[str, Any] | None = None
    if checks[0].get("ok") is not True:
        return {
            "name": site.get("name") or site["mall_id"],
            "mall_id": site["mall_id"],
            "url": site.get("url"),
            "origin": site.get("origin"),
            "api_base_url": api_base_url,
            "api_key_hash": site_api_key_hash,
            "ok": False,
            "checks": checks,
        }
    if not args.skip_page:
        checks.append(check_site_page(site, "desktop", args.timeout))
        checks.append(check_site_page(site, "mobile", args.timeout))
        checks.append(check_saved_widget_probe_sources(site, api_base_url))
        widget_init_check = check_widget_init(site, args.timeout, api_base_url)
        checks.append(widget_init_check)
        checks.append(check_widget_script_asset(site, args.timeout, widget_init_check))
    first_products: dict[str, dict[str, Any] | None] = {}
    search_products: dict[str, list[dict[str, Any]]] = {}
    search_categories: dict[str, list[str]] = {}
    if not args.skip_api:
        for mode in ["text", "image", "mixed"]:
            check, first, categories, products = check_search(
                site,
                api_base_url,
                mode,
                headers,
                image_base64,
                args.limit,
                args.timeout,
            )
            checks.append(check)
            first_products[mode] = first
            search_products[mode] = products
            search_categories[mode] = categories
        if not args.skip_page:
            checks.append(check_result_image_csp(site, search_products, widget_init_check))
        checks.append(
            check_category_refetch(
                site,
                api_base_url,
                headers,
                search_categories.get("text", []),
                image_base64,
                args.limit,
                args.timeout,
            )
        )
        for mode in ["text", "image", "mixed"]:
            product = first_products.get(mode)
            label = f"first {mode} result"
            checks.append(check_product_url_rule(site, product, name=mode_check_name(mode, "product_url_rule"), label=label))
            checks.append(
                check_all_product_url_rules(
                    site,
                    search_products.get(mode, []),
                    name=mode_check_name(mode, "all_product_url_rules"),
                )
            )
            checks.append(check_detail_url(product, args.timeout, name=mode_check_name(mode, "detail_url"), label=label, site=site))
            checks.append(
                check_click_log(
                    site,
                    api_base_url,
                    headers,
                    product,
                    args.timeout,
                    mode=mode,
                    name=mode_check_name(mode, "click_log"),
                )
            )
    return {
        "name": site.get("name") or site["mall_id"],
        "mall_id": site["mall_id"],
        "url": site.get("url"),
        "origin": site.get("origin"),
        "api_base_url": api_base_url,
        "api_key_hash": site_api_key_hash,
        "ok": bool(checks) and all(check.get("ok") is True for check in checks),
        "checks": checks,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    sites = load_site_configs(Path(args.sites))
    site_collection = validate_site_collection(sites)
    image_base64, image_input = image_data_url_for_args(args)
    execution_mode = representative_execution_mode(args, image_input)
    if image_input.get("ok") is not True:
        return {
            "ok": False,
            "not_operational_readiness": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "site_count": len(sites),
            "api_base_url": args.api_base_url.rstrip("/"),
            "site_collection": site_collection,
            "image_input": image_input,
            "execution_mode": execution_mode,
            "sites": [],
        }
    results = [evaluate_site(site, args, image_base64) for site in sites]
    full_operational_check = execution_mode["ok"] is True
    return {
        "ok": full_operational_check and site_collection["ok"] is True and all(site.get("ok") is True for site in results),
        **({"not_operational_readiness": True} if not full_operational_check else {}),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "site_count": len(results),
        "api_base_url": args.api_base_url.rstrip("/"),
        "site_collection": site_collection,
        "image_input": image_input,
        "execution_mode": execution_mode,
        "sites": results,
    }


def representative_execution_mode(args: argparse.Namespace, image_input: dict[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    if getattr(args, "skip_page", False):
        problems.append("skip_page")
    if getattr(args, "skip_api", False):
        problems.append("skip_api")
    if image_input.get("source") != "file":
        problems.append("image_input.source")
    if image_input.get("source") == "file" and not str(image_input.get("file") or "").strip():
        problems.append("image_input.file")
    return {
        "ok": not problems,
        "partial_run": bool(getattr(args, "skip_page", False) or getattr(args, "skip_api", False)),
        "skip_page": bool(getattr(args, "skip_page", False)),
        "skip_api": bool(getattr(args, "skip_api", False)),
        "image_source": image_input.get("source"),
        "problems": problems,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Haeorum AI Search Representative Site Check",
        "",
        f"- OK: `{report['ok']}`",
        f"- Site count: `{report['site_count']}`",
        f"- Not operational readiness: `{report.get('not_operational_readiness') is True}`",
        "",
        "| Site | Mall | OK | Failed Checks |",
        "| --- | --- | --- | --- |",
    ]
    if report.get("execution_mode", {}).get("ok") is not True:
        lines.extend(
            [
                "",
                f"- Execution mode problems: `{', '.join(report.get('execution_mode', {}).get('problems') or [])}`",
            ]
        )
    if report.get("site_collection", {}).get("ok") is not True:
        lines.extend(
            [
                "",
                f"- Site collection problems: `{', '.join(report.get('site_collection', {}).get('problems') or [])}`",
            ]
        )
    for site in report["sites"]:
        failed = [check["name"] for check in site.get("checks", []) if check.get("ok") is not True]
        lines.append(f"| {site.get('name')} | {site.get('mall_id')} | `{site.get('ok')}` | {', '.join(failed)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check representative Haeorum mall sites and build readiness evidence.")
    parser.add_argument("--sites", required=True, help="JSON file with representative site configs.")
    parser.add_argument("--api-base-url", default="https://ai-search.haeorumgift.com")
    parser.add_argument("--api-key", default="", help="Default public API key used when a site entry omits api_key.")
    parser.add_argument("--image-file", default="", help="Reference JPG/PNG/WEBP file used for image and mixed representative searches.")
    parser.add_argument("--image-max-mb", type=int, default=5, help="Maximum reference image size in MiB.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--skip-page", action="store_true")
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    args = parser.parse_args()

    report = run(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
