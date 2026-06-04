from __future__ import annotations

import argparse
import base64
import io
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_mall_configs
from app.url_safety import (
    normalize_http_base_url,
    normalize_http_origin,
    normalize_public_http_base_url,
    normalize_public_http_origin,
    open_public_http_request,
    product_url_contains_product_id,
    redact_url_for_report,
    safe_absolute_http_url,
)
from scripts.mall_config_check import product_url_template_prefix


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
ALLOW_LOCAL_TARGET = False


def make_png_bytes(width: int = 32, height: int = 32) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(12, 120, 110)).save(buffer, format="PNG")
    return buffer.getvalue()


def make_damaged_png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_png_data_url(image_bytes: bytes | None = None) -> str:
    raw = image_bytes if image_bytes is not None else make_png_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def make_oversized_upload_bytes(size_mb: int) -> bytes:
    size = max(1, int(size_mb)) * 1024 * 1024
    return b"x" * size


def make_oversized_json_image_body(mall_id: str, size_mb: int) -> bytes:
    size = max(1, int(size_mb)) * 2 * 1024 * 1024
    return json.dumps(
        {"mall_id": mall_id, "image_base64": "A" * size, "limit": 20},
        ensure_ascii=False,
    ).encode("utf-8")


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any], float]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            **(headers or {}),
        },
    )
    started = time.perf_counter()
    try:
        with open_target_request(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return response.status, data, round((time.perf_counter() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"message": raw}
        return exc.code, data, round((time.perf_counter() - started) * 1000, 1)


def request_raw_json(method: str, url: str, body: bytes, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any], float]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            **(headers or {}),
        },
    )
    started = time.perf_counter()
    try:
        with open_target_request(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return response.status, data, round((time.perf_counter() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"message": raw}
        return exc.code, data, round((time.perf_counter() - started) * 1000, 1)


def request_text(method: str, url: str, headers: dict[str, str] | None = None) -> tuple[int, str, float]:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    started = time.perf_counter()
    try:
        with open_target_request(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, raw, round((time.perf_counter() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, raw, round((time.perf_counter() - started) * 1000, 1)


def request_preflight(
    url: str,
    origin: str,
    request_method: str = "POST",
    request_headers: str = "content-type,x-api-key",
) -> tuple[int, dict[str, str], float]:
    request = urllib.request.Request(
        url,
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": request_method,
            "Access-Control-Request-Headers": request_headers,
        },
    )
    started = time.perf_counter()
    try:
        with open_target_request(request, timeout=60) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            response.read()
            return response.status, headers, round((time.perf_counter() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        headers = {key.lower(): value for key, value in exc.headers.items()}
        exc.read()
        return exc.code, headers, round((time.perf_counter() - started) * 1000, 1)


def build_multipart_body(
    fields: dict[str, Any],
    files: dict[str, tuple[str, str, bytes]],
    boundary: str,
) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, (filename, content_type, content) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("ascii"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks)


def open_target_request(request: urllib.request.Request, timeout: int | float):  # type: ignore[no-untyped-def]
    if ALLOW_LOCAL_TARGET:
        return urllib.request.urlopen(request, timeout=timeout)
    return open_public_http_request(request, timeout=timeout)


def request_multipart(
    url: str,
    fields: dict[str, Any],
    files: dict[str, tuple[str, str, bytes]],
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], float]:
    boundary = "----haeorum-ai-search-smoke-boundary"
    body = build_multipart_body(fields, files, boundary)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            **(headers or {}),
        },
    )
    started = time.perf_counter()
    try:
        with open_target_request(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return response.status, data, round((time.perf_counter() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"message": raw}
        return exc.code, data, round((time.perf_counter() - started) * 1000, 1)


def request_raw_multipart(
    url: str,
    body: bytes,
    boundary: str = "----haeorum-ai-search-smoke-boundary",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], float]:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            **(headers or {}),
        },
    )
    started = time.perf_counter()
    try:
        with open_target_request(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return response.status, data, round((time.perf_counter() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"message": raw}
        return exc.code, data, round((time.perf_counter() - started) * 1000, 1)


def check(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def sensitive_log_markers(args: argparse.Namespace, image_payloads: list[str]) -> list[str]:
    values = [
        getattr(args, "api_key", ""),
        getattr(args, "admin_key", ""),
        getattr(args, "invalid_api_key", ""),
        getattr(args, "invalid_admin_key", ""),
    ]
    for payload in image_payloads:
        values.append(payload)
        if "," in payload:
            values.append(payload.split(",", 1)[1])

    markers: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if len(text) >= 8 and text not in markers:
            markers.append(text)
    return markers


def assert_no_sensitive_log_markers(value: Any, markers: list[str], label: str) -> dict[str, Any]:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    leaked_marker_indexes = [
        index + 1
        for index, marker in enumerate(markers)
        if marker and marker in serialized
    ]
    leaked_fixed_patterns = [
        pattern
        for pattern in ["data:image/"]
        if pattern in serialized
    ]
    check(
        not leaked_marker_indexes and not leaked_fixed_patterns,
        (
            f"{label} contains unredacted sensitive marker(s): "
            f"marker_indexes={leaked_marker_indexes}, fixed_patterns={leaked_fixed_patterns}"
        ),
    )
    return {
        "sensitive_log_redaction_ok": True,
        "sensitive_marker_count": len(markers),
        "payload_bytes_checked": len(serialized.encode("utf-8")),
    }


def validate_args(args: argparse.Namespace) -> dict[str, Any]:
    base_normalizer = normalize_http_base_url if getattr(args, "allow_local_target", False) else normalize_public_http_base_url
    origin_normalizer = normalize_http_origin if getattr(args, "allow_local_target", False) else normalize_public_http_origin
    base_url = base_normalizer(getattr(args, "base_url", ""), "--base-url")
    origin_text = str(getattr(args, "origin", "") or "").strip()
    invalid_origin_text = str(getattr(args, "invalid_origin", "") or "").strip()
    origin = origin_normalizer(origin_text, "--origin") if origin_text else ""
    invalid_origin = origin_normalizer(invalid_origin_text, "--invalid-origin")
    args.expected_product_url_prefix = expected_product_url_prefix_for_args(args)
    args.expected_click_product_url_prefix = expected_click_product_url_prefix_for_args(
        args,
        args.expected_product_url_prefix,
    )
    return {
        "ok": True,
        "base_url": base_url,
        "origin": origin or None,
        "invalid_origin": invalid_origin,
        "expected_product_url_prefix": args.expected_product_url_prefix or None,
        "expected_click_product_url_prefix": args.expected_click_product_url_prefix or None,
    }


def failed_validation_report(args: argparse.Namespace, error: Exception | str) -> dict[str, Any]:
    message = str(error)
    return {
        "ok": False,
        "base_url": redact_url_for_report(getattr(args, "base_url", "")),
        "mall_id": getattr(args, "mall_id", ""),
        "origin": redact_url_for_report(getattr(args, "origin", "")) or None,
        "expected_product_url_prefix": getattr(args, "expected_product_url_prefix", "") or None,
        "expected_click_product_url_prefix": getattr(args, "expected_click_product_url_prefix", "") or None,
        "target_validation": {
            "ok": False,
            "error": message,
        },
        "checks": [
            {
                "name": "target_validation",
                "ok": False,
                "error": message,
            }
        ],
    }


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def normalized_url_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    if scheme.lower() == "https":
        return 443
    if scheme.lower() == "http":
        return 80
    return None


def url_matches_prefix(url: str, prefix: str) -> bool:
    url_text = str(url or "").strip()
    prefix_text = str(prefix or "").strip().rstrip("/")
    if not url_text or not prefix_text:
        return False
    safe_url = safe_absolute_http_url(url_text)
    safe_prefix = safe_absolute_http_url(prefix_text)
    if safe_url is None or safe_prefix is None:
        return False
    from urllib.parse import urlparse

    parsed_url = urlparse(safe_url)
    parsed_prefix = urlparse(safe_prefix)
    if parsed_url.scheme.lower() != parsed_prefix.scheme.lower():
        return False
    if (parsed_url.hostname or "").lower() != (parsed_prefix.hostname or "").lower():
        return False
    if normalized_url_port(parsed_url.scheme, parsed_url.port) != normalized_url_port(parsed_prefix.scheme, parsed_prefix.port):
        return False
    prefix_path = parsed_prefix.path.rstrip("/")
    if not prefix_path:
        return True
    url_path = parsed_url.path.rstrip("/")
    return url_path == prefix_path or url_path.startswith(prefix_path + "/")


def normalized_url_authority_and_tail(value: str) -> tuple[str, str, int | None]:
    parsed = urlparse(value)
    separator_index = value.find("://")
    tail_start = separator_index + 3 + len(parsed.netloc) if separator_index >= 0 else 0
    authority = f"{parsed.scheme.lower()}://{(parsed.hostname or '').lower()}"
    return authority, value[tail_start:], normalized_url_port(parsed.scheme, parsed.port)


def url_matches_template_prefix(url: str, prefix: str) -> bool:
    safe_url = safe_absolute_http_url(str(url or "").strip())
    safe_prefix = safe_absolute_http_url(str(prefix or "").strip())
    if safe_url is None or safe_prefix is None:
        return False
    url_authority, url_tail, url_port = normalized_url_authority_and_tail(safe_url)
    prefix_authority, prefix_tail, prefix_port = normalized_url_authority_and_tail(safe_prefix)
    return url_authority == prefix_authority and url_port == prefix_port and url_tail.startswith(prefix_tail)


def normalize_expected_product_url_prefix(value: Any, option_name: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if safe_absolute_http_url(text) is None:
        raise ValueError(f"{option_name} must be a safe public http(s) URL prefix")
    return text


def normalize_expected_click_product_url_prefix(value: Any, option_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if safe_absolute_http_url(text) is None:
        raise ValueError(f"{option_name} must be a safe public http(s) URL prefix")
    return text


def expected_product_url_prefix_for_args(args: argparse.Namespace) -> str:
    explicit_prefix = normalize_expected_product_url_prefix(
        getattr(args, "expected_product_url_prefix", ""),
        "--expected-product-url-prefix",
    )
    if explicit_prefix:
        return explicit_prefix
    mall_config_path = str(getattr(args, "mall_config", "") or "").strip()
    if not mall_config_path:
        return ""
    mall_id = str(getattr(args, "mall_id", "") or "").strip()
    if not mall_id:
        raise ValueError("--mall-id is required when --mall-config is set")
    path = Path(mall_config_path)
    if not path.exists():
        raise ValueError("--mall-config does not exist")
    malls = load_mall_configs(path)
    mall = malls.get(mall_id)
    if mall is None or not mall.enabled:
        raise ValueError("--mall-config must contain an enabled entry for --mall-id")
    prefix = product_url_template_prefix(mall.product_url_template or "", mall.mall_id) or ""
    if not prefix:
        raise ValueError("--mall-config mall product_url_template must produce a safe product URL prefix")
    return normalize_expected_product_url_prefix(prefix, "--mall-config product_url_template")


def product_url_template_click_prefix(template: str, mall_id: str) -> str:
    if not template:
        return ""
    sentinel = "__HAEORUM_CLICK_PRODUCT_ID__"
    try:
        formatted = template.format(product_id=sentinel, mall_id=mall_id or "shop001")
    except Exception:
        return ""
    if sentinel not in formatted or safe_absolute_http_url(formatted) is None:
        return ""
    return formatted.split(sentinel, 1)[0]


def expected_click_product_url_prefix_for_args(args: argparse.Namespace, result_prefix: str) -> str:
    explicit_prefix = normalize_expected_click_product_url_prefix(
        getattr(args, "expected_click_product_url_prefix", ""),
        "--expected-click-product-url-prefix",
    )
    if explicit_prefix:
        return explicit_prefix
    mall_config_path = str(getattr(args, "mall_config", "") or "").strip()
    if not mall_config_path:
        return result_prefix
    mall_id = str(getattr(args, "mall_id", "") or "").strip()
    if not mall_id:
        raise ValueError("--mall-id is required when --mall-config is set")
    path = Path(mall_config_path)
    if not path.exists():
        raise ValueError("--mall-config does not exist")
    malls = load_mall_configs(path)
    mall = malls.get(mall_id)
    if mall is None or not mall.enabled:
        raise ValueError("--mall-config must contain an enabled entry for --mall-id")
    prefix = product_url_template_click_prefix(mall.product_url_template or "", mall.mall_id)
    if not prefix:
        raise ValueError("--mall-config mall product_url_template must produce a safe click product URL prefix")
    return normalize_expected_click_product_url_prefix(prefix, "--mall-config product_url_template")


def check_optional_number(value: Any, field_name: str, label: str) -> None:
    check(value is None or is_number(value), f"{label} meta {field_name} must be a number or null: {value}")


def requested_mall_id(payload: dict[str, Any]) -> str:
    return str(payload.get("mall_id") or payload.get("site_id") or "").strip()


def validate_result_item_shape(
    item: dict[str, Any],
    section: str,
    index: int,
    label: str,
    expected_mall_id: str = "",
    expected_product_url_prefix: str = "",
) -> None:
    prefix = f"{label} {section}[{index}]"
    product_id = str(item.get("product_id") or "").strip()
    for field_name in ["product_id", "name", "category", "mall_id"]:
        value = item.get(field_name)
        check(isinstance(value, str) and value.strip(), f"{prefix} {field_name} must be a non-empty string: {item}")
        if field_name == "mall_id" and expected_mall_id:
            check(
                value.strip() == expected_mall_id,
                f"{prefix} mall_id must match requested mall_id {expected_mall_id}: {item}",
            )
    price = item.get("price")
    check(price is None or is_number(price), f"{prefix} price must be a number or null: {item}")
    if is_number(price):
        check(math.isfinite(float(price)) and price >= 0, f"{prefix} price must be finite and non-negative: {item}")
    for field_name in ["image_url", "product_url"]:
        value = item.get(field_name)
        check(isinstance(value, str) and value.strip(), f"{prefix} {field_name} must be a non-empty URL string: {item}")
        check(safe_absolute_http_url(value) is not None, f"{prefix} {field_name} must be a safe public http(s) URL: {item}")
        if field_name == "product_url":
            check(
                product_url_contains_product_id(value, product_id),
                f"{prefix} product_url must contain product_id {product_id}: {item}",
            )
            if expected_product_url_prefix:
                check(
                    url_matches_prefix(value, expected_product_url_prefix),
                    f"{prefix} product_url must match expected product URL prefix {expected_product_url_prefix}: {item}",
                )
    score = item.get("score")
    score_percent = item.get("score_percent")
    check(is_number(score) and math.isfinite(float(score)) and 0 <= score <= 1, f"{prefix} score must be 0..1: {item}")
    check(
        is_number(score_percent) and math.isfinite(float(score_percent)) and 0 <= score_percent <= 100,
        f"{prefix} score_percent must be 0..100: {item}",
    )
    for source, value in item.get("source_scores", {}).items():
        check(is_number(value) and math.isfinite(float(value)), f"{prefix} source_scores.{source} must be finite number: {item}")


def validate_meta_shape(
    meta: dict[str, Any],
    expected_query_type: str,
    label: str,
    expected_mall_id: str = "",
) -> None:
    check(is_number(meta.get("elapsed_ms")) and meta.get("elapsed_ms") >= 0, f"{label} meta elapsed_ms must be non-negative: {meta}")
    check(isinstance(meta.get("limit"), int) and meta.get("limit") >= 1, f"{label} meta limit must be a positive integer: {meta}")
    check(isinstance(meta.get("offset"), int) and meta.get("offset") >= 0, f"{label} meta offset must be a non-negative integer: {meta}")
    check(isinstance(meta.get("has_more"), bool), f"{label} meta has_more must be boolean: {meta}")
    check(
        meta.get("next_offset") is None or isinstance(meta.get("next_offset"), int),
        f"{label} meta next_offset must be integer or null: {meta}",
    )
    check(
        meta.get("next_offset") is None or meta.get("next_offset") >= 0,
        f"{label} meta next_offset must be non-negative or null: {meta}",
    )
    if meta.get("has_more") is True:
        check(meta.get("next_offset") is not None, f"{label} meta next_offset is required when has_more is true: {meta}")
        check(meta.get("next_offset") > meta.get("offset"), f"{label} meta next_offset must be greater than offset: {meta}")
    elif meta.get("has_more") is False:
        check(meta.get("next_offset") is None, f"{label} meta next_offset must be null when has_more is false: {meta}")
    check(isinstance(meta.get("low_confidence"), bool), f"{label} meta low_confidence must be boolean: {meta}")
    check(meta.get("notice") is None or isinstance(meta.get("notice"), str), f"{label} meta notice must be string or null: {meta}")
    mall_id = meta.get("mall_id")
    check(isinstance(mall_id, str) and mall_id.strip(), f"{label} meta mall_id must be a non-empty string: {meta}")
    if expected_mall_id:
        check(
            mall_id.strip() == expected_mall_id,
            f"{label} meta mall_id must match requested mall_id {expected_mall_id}: {meta}",
        )
    check_optional_number(meta.get("text_weight"), "text_weight", label)
    check_optional_number(meta.get("image_weight"), "image_weight", label)
    if is_number(meta.get("text_weight")):
        check(meta.get("text_weight") >= 0, f"{label} meta text_weight must be non-negative: {meta}")
    if is_number(meta.get("image_weight")):
        check(meta.get("image_weight") >= 0, f"{label} meta image_weight must be non-negative: {meta}")
    if expected_query_type == "text":
        check(is_number(meta.get("text_weight")), f"{label} text search must include text_weight: {meta}")
        check(meta.get("image_weight") is None, f"{label} text search image_weight must be null: {meta}")
    elif expected_query_type == "image":
        check(meta.get("text_weight") is None, f"{label} image search text_weight must be null: {meta}")
        check(is_number(meta.get("image_weight")), f"{label} image search must include image_weight: {meta}")
    elif expected_query_type == "text_image":
        check(is_number(meta.get("text_weight")), f"{label} mixed search must include text_weight: {meta}")
        check(is_number(meta.get("image_weight")), f"{label} mixed search must include image_weight: {meta}")


def validate_search_response(
    data: dict[str, Any],
    expected_query_type: str,
    label: str,
    expected_mall_id: str = "",
    expected_product_url_prefix: str = "",
) -> dict[str, Any]:
    check(isinstance(data, dict), f"{label} response must be an object: {data}")
    meta = data.get("meta")
    check(isinstance(meta, dict), f"{label} response missing meta object: {data}")
    missing_meta = sorted(REQUIRED_META_FIELDS - set(meta or {}))
    check(not missing_meta, f"{label} meta missing fields {missing_meta}: {meta}")
    check(meta.get("query_type") == expected_query_type, f"unexpected {label} meta: {meta}")
    validate_meta_shape(meta, expected_query_type, label, expected_mall_id)

    top = data.get("top")
    items = data.get("items")
    categories = data.get("suggested_categories")
    check(isinstance(top, list), f"{label} response missing top list: {data}")
    check(1 <= len(top) <= 3, f"{label} top should contain 1 to 3 products, got {len(top)}")
    check(isinstance(items, list), f"{label} response missing related item list: {data}")
    check(len(items) > 0, f"{label} response should include related items")
    check(len(items) <= meta.get("limit"), f"{label} related items must not exceed meta.limit: {data}")
    if meta.get("has_more") is True:
        check(
            meta.get("next_offset") == meta.get("offset") + len(items),
            f"{label} meta next_offset must equal offset plus related item count: {meta}",
        )
    check(isinstance(categories, list), f"{label} response missing suggested_categories list: {data}")
    check(len(categories) > 0, f"{label} response should include suggested_categories")
    top_product_ids = [str(item.get("product_id")) for item in top if isinstance(item, dict)]
    item_product_ids = [str(item.get("product_id")) for item in items if isinstance(item, dict)]
    check(len(set(top_product_ids)) == len(top_product_ids), f"{label} top product_ids must be unique")
    check(len(set(item_product_ids)) == len(item_product_ids), f"{label} related item product_ids must be unique")
    repeated_ids = sorted(set(top_product_ids).intersection(item_product_ids))
    check(not repeated_ids, f"{label} related items must exclude top product_ids: {repeated_ids}")
    category_values = [str(category).strip() for category in categories]
    check(len(set(category_values)) == len(category_values), f"{label} suggested_categories must be unique")

    for section, results in [("top", top), ("items", items)]:
        for index, item in enumerate(results):
            check(isinstance(item, dict), f"{label} {section}[{index}] must be an object: {item}")
            missing = sorted(REQUIRED_RESULT_FIELDS - set(item))
            check(not missing, f"{label} {section}[{index}] missing fields {missing}: {item}")
            check(isinstance(item.get("source_scores"), dict), f"{label} {section}[{index}] source_scores must be an object: {item}")
            validate_result_item_shape(item, section, index, label, expected_mall_id, expected_product_url_prefix)
    result_mall_ids = [
        str(item.get("mall_id") or "").strip()
        for item in [*top, *items]
        if isinstance(item, dict)
    ]
    result_product_urls = [
        str(item.get("product_url") or "").strip()
        for item in [*top, *items]
        if isinstance(item, dict)
    ]
    product_url_prefix_mismatch_count = (
        sum(1 for product_url in result_product_urls if not url_matches_prefix(product_url, expected_product_url_prefix))
        if expected_product_url_prefix
        else 0
    )
    return {
        "response_contract_ok": True,
        "expected_mall_id": expected_mall_id,
        "expected_product_url_prefix": expected_product_url_prefix or None,
        "meta_mall_id": meta.get("mall_id"),
        "result_mall_ids": result_mall_ids,
        "result_product_urls": result_product_urls,
        "product_url_prefix_mismatch_count": product_url_prefix_mismatch_count,
        "query_type": meta.get("query_type"),
        "engine": meta.get("engine"),
        "top_count": len(top),
        "item_count": len(items),
        "category_count": len(categories),
        "required_result_fields": sorted(REQUIRED_RESULT_FIELDS),
        "required_meta_fields": sorted(REQUIRED_META_FIELDS),
    }


def first_result_product_url(data: dict[str, Any]) -> str:
    top = data.get("top") if isinstance(data, dict) else []
    if not isinstance(top, list) or not top or not isinstance(top[0], dict):
        return ""
    return str(top[0].get("product_url") or "").strip()


def first_result_product_id(data: dict[str, Any]) -> str:
    top = data.get("top") if isinstance(data, dict) else []
    if not isinstance(top, list) or not top or not isinstance(top[0], dict):
        return ""
    return str(top[0].get("product_id") or "").strip()


def fallback_product_url_from_prefix(expected_product_url_prefix: str, product_id: str) -> str:
    prefix = str(expected_product_url_prefix or "").strip().rstrip("/")
    if not prefix:
        return ""
    return f"{prefix}/{quote(str(product_id or '').strip(), safe='')}"


def fallback_click_product_url_from_prefix(expected_click_product_url_prefix: str, product_id: str) -> str:
    prefix = str(expected_click_product_url_prefix or "").strip()
    if not prefix:
        return ""
    encoded_product_id = quote(str(product_id or "").strip(), safe="")
    if "?" in prefix or "#" in prefix or prefix.endswith(("/", "=", "-", "_")):
        return f"{prefix}{encoded_product_id}"
    return f"{prefix.rstrip('/')}/{encoded_product_id}"


def wrong_click_product_url_for_prefix(expected_click_product_url_prefix: str, product_id: str) -> str:
    prefix = str(expected_click_product_url_prefix or "").strip()
    parsed = urlparse(prefix)
    encoded_product_id = quote(str(product_id or "").strip(), safe="")
    if parsed.query:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?__wrong_product_param__={encoded_product_id}"
    return f"{parsed.scheme}://{parsed.netloc}/__wrong_product_path__/{encoded_product_id}"


def run(args: argparse.Namespace) -> dict[str, Any]:
    global ALLOW_LOCAL_TARGET
    ALLOW_LOCAL_TARGET = bool(getattr(args, "allow_local_target", False))
    try:
        target_validation = validate_args(args)
    except ValueError as exc:
        return failed_validation_report(args, exc)
    base_url = str(target_validation["base_url"])
    origin = str(target_validation.get("origin") or "")
    invalid_origin = str(target_validation["invalid_origin"])
    expected_product_url_prefix = str(target_validation.get("expected_product_url_prefix") or "")
    expected_click_product_url_prefix = str(target_validation.get("expected_click_product_url_prefix") or "")
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    if origin:
        headers["Origin"] = origin
    admin_headers = {"X-Admin-Key": args.admin_key} if args.admin_key else {}
    image_bytes = make_png_bytes()
    image_base64 = make_png_data_url(image_bytes)
    small_image_bytes = make_png_bytes(8, 8)
    small_image_base64 = make_png_data_url(small_image_bytes)
    sensitive_markers = sensitive_log_markers(args, [image_base64, small_image_base64])
    checks = []
    latest_click_product_url = ""
    latest_click_product_id = ""

    def add_check(name: str, fn) -> None:
        started = time.perf_counter()
        try:
            details = fn()
            item = {"name": name, "ok": True, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
            if isinstance(details, dict):
                item.update(details)
            checks.append(item)
        except Exception as exc:
            checks.append(
                {
                    "name": name,
                    "ok": False,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "error": str(exc),
                }
            )

    def health() -> None:
        status, data, _ = request_json("GET", f"{base_url}/health")
        check(status == 200, f"health returned {status}: {data}")
        check(data.get("ready") is True, f"health is not ready: {data}")

    def cors_preflight() -> None:
        check(bool(origin), "--origin is required to verify CORS preflight")
        status, response_headers, _ = request_preflight(f"{base_url}/api/ai-search", origin)
        check(status == 200, f"CORS preflight returned {status}: {response_headers}")
        allow_origin = response_headers.get("access-control-allow-origin")
        check(allow_origin == origin, f"CORS allow-origin should be {origin}, got {allow_origin}")

    def invalid_cors_preflight_rejected() -> None:
        check(bool(origin), "--origin is required to verify invalid CORS preflight rejection")
        status, response_headers, _ = request_preflight(f"{base_url}/api/ai-search", invalid_origin)
        check(status in {400, 403}, f"invalid CORS preflight should return 400/403, got {status}: {response_headers}")

    def click_log_cors_preflight() -> None:
        check(bool(origin), "--origin is required to verify click-log CORS preflight")
        status, response_headers, _ = request_preflight(f"{base_url}/api/click-log", origin)
        check(status == 200, f"click-log CORS preflight returned {status}: {response_headers}")
        allow_origin = response_headers.get("access-control-allow-origin")
        check(allow_origin == origin, f"click-log CORS allow-origin should be {origin}, got {allow_origin}")

    def invalid_click_log_cors_preflight_rejected() -> None:
        check(bool(origin), "--origin is required to verify invalid click-log CORS preflight rejection")
        status, response_headers, _ = request_preflight(f"{base_url}/api/click-log", invalid_origin)
        check(
            status in {400, 403},
            f"invalid click-log CORS preflight should return 400/403, got {status}: {response_headers}",
        )

    def search_text() -> None:
        nonlocal latest_click_product_id, latest_click_product_url
        payload = {"mall_id": args.mall_id, "q": "검은 우산", "limit": 20}
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            payload,
            headers,
        )
        check(status == 200, f"text search returned {status}: {data}")
        details = validate_search_response(data, "text", "text search", requested_mall_id(payload), expected_product_url_prefix)
        latest_click_product_id = first_result_product_id(data) or latest_click_product_id
        latest_click_product_url = first_result_product_url(data) or latest_click_product_url
        return details

    def site_id_search() -> None:
        payload = {"site_id": args.mall_id, "q": "검은 우산", "limit": 20}
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            payload,
            headers,
        )
        check(status == 200, f"site_id text search returned {status}: {data}")
        return validate_search_response(data, "text", "site_id text search", requested_mall_id(payload), expected_product_url_prefix)

    def conflicting_site_id_rejected() -> None:
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "site_id": f"{args.mall_id}-other", "q": "검은 우산", "limit": 1},
            headers,
        )
        check(status == 400, f"conflicting site_id search should return 400, got {status}: {data}")

    def search_image() -> None:
        payload = {"mall_id": args.mall_id, "image_base64": image_base64, "limit": 20}
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            payload,
            headers,
        )
        check(status == 200, f"image search returned {status}: {data}")
        return validate_search_response(data, "image", "image search", requested_mall_id(payload), expected_product_url_prefix)

    def oversized_json_image_rejected() -> None:
        status, data, _ = request_raw_json(
            "POST",
            f"{base_url}/api/ai-search",
            make_oversized_json_image_body(args.mall_id, args.oversized_upload_mb),
            headers,
        )
        check(status == 413, f"oversized JSON image body should return 413, got {status}: {data}")

    def small_json_image_rejected() -> None:
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "q": "small image smoke", "image_base64": small_image_base64, "limit": 20},
            headers,
        )
        detail = str(data.get("detail") or data.get("message") or "").lower()
        check(status == 400, f"too-small JSON image should return 400, got {status}: {data}")
        check(
            "minimum dimension" in detail or "too small" in detail,
            f"too-small JSON image response should mention minimum dimension, got: {data}",
        )

    def search_multipart_image() -> None:
        fields = {"mall_id": args.mall_id, "limit": 20}
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            fields,
            {"image": ("smoke.png", "image/png", image_bytes)},
            headers,
        )
        check(status == 200, f"multipart image search returned {status}: {data}")
        return validate_search_response(data, "image", "multipart image search", requested_mall_id(fields), expected_product_url_prefix)

    def site_id_multipart_image_search() -> None:
        fields = {"site_id": args.mall_id, "limit": 20}
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            fields,
            {"image": ("smoke.png", "image/png", image_bytes)},
            headers,
        )
        check(status == 200, f"site_id multipart image search returned {status}: {data}")
        return validate_search_response(data, "image", "site_id multipart image search", requested_mall_id(fields), expected_product_url_prefix)

    def conflicting_multipart_site_id_rejected() -> None:
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "site_id": f"{args.mall_id}-other", "limit": 20},
            {"image": ("smoke.png", "image/png", image_bytes)},
            headers,
        )
        check(status == 400, f"conflicting multipart site_id should return 400, got {status}: {data}")

    def unsupported_multipart_field_rejected() -> None:
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 20, "unexpected": "value"},
            {"image": ("smoke.png", "image/png", image_bytes)},
            headers,
        )
        detail = str(data.get("detail") or data.get("message") or "").lower()
        check(status == 400, f"unsupported multipart field should return 400, got {status}: {data}")
        check("unsupported multipart" in detail, f"unsupported multipart field response should mention unsupported field, got: {data}")

    def invalid_multipart_image_rejected() -> None:
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 20},
            {"image": ("not-image.txt", "text/plain", b"this is not an image")},
            headers,
        )
        check(status == 400, f"invalid multipart image should return 400, got {status}: {data}")

    def damaged_multipart_image_rejected() -> None:
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 20},
            {"image": ("damaged.png", "image/png", make_damaged_png_bytes())},
            headers,
        )
        check(status == 400, f"damaged multipart image should return 400, got {status}: {data}")

    def small_multipart_image_rejected() -> None:
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 20},
            {"image": ("small.png", "image/png", small_image_bytes)},
            headers,
        )
        detail = str(data.get("detail") or data.get("message") or "").lower()
        check(status == 400, f"too-small multipart image should return 400, got {status}: {data}")
        check(
            "minimum dimension" in detail or "too small" in detail,
            f"too-small multipart image response should mention minimum dimension, got: {data}",
        )

    def oversized_multipart_image_rejected() -> None:
        oversized_bytes = make_oversized_upload_bytes(args.oversized_upload_mb)
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 20},
            {"image": ("oversized.png", "image/png", oversized_bytes)},
            headers,
        )
        check(status == 413, f"oversized multipart image should return 413, got {status}: {data}")

    def malformed_multipart_rejected() -> None:
        status, data, _ = request_raw_multipart(
            f"{base_url}/api/ai-search",
            b"not-a-valid-multipart-body",
            headers=headers,
        )
        check(status == 400, f"malformed multipart body should return 400, got {status}: {data}")

    def search_mixed() -> None:
        payload = {"mall_id": args.mall_id, "q": "검은색", "image_base64": image_base64, "limit": 20}
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            payload,
            headers,
        )
        check(status == 200, f"mixed search returned {status}: {data}")
        return validate_search_response(data, "text_image", "mixed search", requested_mall_id(payload), expected_product_url_prefix)

    def openapi_click_rate_limit_documented() -> None:
        status, data, _ = request_json("GET", f"{base_url}/openapi.json")
        check(status == 200, f"OpenAPI schema returned {status}: {data}")
        search_properties = data.get("components", {}).get("schemas", {}).get("SearchRequest", {}).get("properties", {})
        multipart_properties = (
            data.get("paths", {})
            .get("/api/ai-search", {})
            .get("post", {})
            .get("requestBody", {})
            .get("content", {})
            .get("multipart/form-data", {})
            .get("schema", {})
            .get("properties", {})
        )
        for field in [
            "print_method",
            "material",
            "color",
            "min_price",
            "max_price",
            "quantity",
            "order_qty",
            "max_delivery_days",
        ]:
            check(field in search_properties, f"SearchRequest OpenAPI schema missing {field}")
            check(field in multipart_properties, f"multipart OpenAPI schema missing {field}")
        click_responses = data.get("paths", {}).get("/api/click-log", {}).get("post", {}).get("responses", {})
        rate_limit_ref = click_responses.get("429", {}).get("$ref")
        check(
            rate_limit_ref == "#/components/responses/RateLimited",
            f"click-log OpenAPI 429 response should reference RateLimited, got {rate_limit_ref}",
        )

    def invalid_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify invalid API key rejection")
        bad_headers = dict(headers)
        bad_headers["X-API-Key"] = args.invalid_api_key
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1},
            bad_headers,
        )
        check(status == 401, f"invalid API key should return 401, got {status}: {data}")

    def query_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify query API key rejection")
        query_headers = dict(headers)
        query_headers.pop("X-API-Key", None)
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search?api_key={args.api_key}",
            {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1},
            query_headers,
        )
        check(status == 400, f"query API key should return 400, got {status}: {data}")

    def query_api_key_alias_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify query API key alias rejection")
        query_headers = dict(headers)
        query_headers.pop("X-API-Key", None)
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search?api-key={args.api_key}",
            {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1},
            query_headers,
        )
        check(status == 400, f"query API key alias should return 400, got {status}: {data}")

    def query_admin_key_alias_rejected() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify query admin key alias rejection")
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search?admin_key={args.admin_key}",
            {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1},
            headers,
        )
        check(status == 400, f"query admin key alias should return 400, got {status}: {data}")

    def empty_query_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify empty query API key rejection")
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search?api_key=",
            {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1},
            headers,
        )
        check(status == 400, f"empty query API key parameter should return 400, got {status}: {data}")

    def body_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify body API key rejection")
        payload = {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1, "api_key": args.api_key}
        status, data, _ = request_json("POST", f"{base_url}/api/ai-search", payload, headers)
        check(status == 400, f"body API key should return 400, got {status}: {data}")

    def body_api_key_alias_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify body API key alias rejection")
        payload = {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1, "x-api-key": args.api_key}
        status, data, _ = request_json("POST", f"{base_url}/api/ai-search", payload, headers)
        check(status == 400, f"body API key alias should return 400, got {status}: {data}")

    def body_admin_key_alias_rejected() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify body admin key alias rejection")
        payload = {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1, "x-admin-key": args.admin_key}
        status, data, _ = request_json("POST", f"{base_url}/api/ai-search", payload, headers)
        check(status == 400, f"body admin key alias should return 400, got {status}: {data}")

    def multipart_body_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify multipart body API key rejection")
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 20, "api_key": args.api_key},
            {"image": ("smoke.png", "image/png", image_bytes)},
            headers,
        )
        check(status == 400, f"multipart body API key should return 400, got {status}: {data}")

    def multipart_body_api_key_alias_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify multipart body API key alias rejection")
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 20, "x-api-key": args.api_key},
            {"image": ("smoke.png", "image/png", image_bytes)},
            headers,
        )
        check(status == 400, f"multipart body API key alias should return 400, got {status}: {data}")

    def multipart_body_admin_key_alias_rejected() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify multipart body admin key alias rejection")
        status, data, _ = request_multipart(
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 20, "x-admin-key": args.admin_key},
            {"image": ("smoke.png", "image/png", image_bytes)},
            headers,
        )
        check(status == 400, f"multipart body admin key alias should return 400, got {status}: {data}")

    def invalid_origin_rejected() -> None:
        check(bool(origin), "--origin is required to verify invalid origin rejection")
        bad_headers = dict(headers)
        bad_headers["Origin"] = invalid_origin
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1},
            bad_headers,
        )
        check(status == 403, f"invalid origin should return 403, got {status}: {data}")

    def invalid_search_payload_rejected() -> None:
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "limit": 1},
            headers,
        )
        check(status == 400, f"invalid search payload should return 400, got {status}: {data}")

    def unsupported_json_field_rejected() -> None:
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1, "unexpected": "value"},
            headers,
        )
        detail = str(data.get("detail") or data.get("message") or "").lower()
        check(status == 400, f"unsupported JSON search field should return 400, got {status}: {data}")
        check(
            "unexpected" in detail or "extra" in detail or "not permitted" in detail,
            f"unsupported JSON search field response should mention rejected field, got: {data}",
        )

    def invalid_domain_filter_rejected() -> None:
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            {"mall_id": args.mall_id, "q": "우산", "min_price": 2000, "max_price": 1000},
            headers,
        )
        check(status == 400, f"invalid domain filter payload should return 400, got {status}: {data}")

    def malformed_search_json_rejected() -> None:
        status, data, _ = request_raw_json(
            "POST",
            f"{base_url}/api/ai-search",
            b'{"mall_id":',
            headers,
        )
        check(status == 400, f"malformed search JSON should return 400, got {status}: {data}")

    def click_payload() -> dict[str, Any]:
        product_id = latest_click_product_id or "smoke-product"
        return {
            "mall_id": args.mall_id,
            "product_id": product_id,
            "position": 1,
            "query": "smoke",
            "query_type": "text",
            "score_percent": 91.2,
            "product_url": latest_click_product_url or fallback_click_product_url(product_id),
        }

    def fallback_click_product_url(product_id: str = "smoke-product") -> str:
        product_url = fallback_click_product_url_from_prefix(expected_click_product_url_prefix, product_id)
        if product_url:
            return product_url
        if origin:
            return f"{origin.rstrip('/')}/product/{product_id}"
        return f"https://{args.mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}"

    def click_product_url_details(payload: dict[str, Any]) -> dict[str, Any]:
        product_id = str(payload.get("product_id") or "").strip()
        product_url = str(payload.get("product_url") or "").strip()
        prefix_matches = (
            url_matches_prefix(product_url, expected_product_url_prefix)
            if expected_product_url_prefix
            else None
        )
        template_prefix_matches = (
            url_matches_template_prefix(product_url, expected_click_product_url_prefix)
            if expected_click_product_url_prefix
            else None
        )
        contains_product_id = product_url_contains_product_id(product_url, product_id)
        if expected_product_url_prefix:
            check(
                prefix_matches is True,
                f"click product_url must match expected product URL prefix {expected_product_url_prefix}: {product_url}",
            )
        if expected_click_product_url_prefix:
            check(
                template_prefix_matches is True,
                (
                    "click product_url must match expected click product URL template prefix "
                    f"{expected_click_product_url_prefix}: {product_url}"
                ),
            )
        check(
            contains_product_id,
            f"click product_url must contain product_id {product_id}: {product_url}",
        )
        return {
            "expected_product_url_prefix": expected_product_url_prefix or None,
            "expected_click_product_url_prefix": expected_click_product_url_prefix or None,
            "click_product_id": product_id,
            "click_product_url": product_url,
            "click_product_url_prefix_matches": prefix_matches,
            "click_product_url_template_prefix_matches": template_prefix_matches,
            "click_product_url_contains_product_id": contains_product_id,
        }

    def click_log() -> dict[str, Any]:
        payload = click_payload()
        details = click_product_url_details(payload)
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            payload,
            headers,
        )
        check(status == 200, f"click log returned {status}: {data}")
        check(data.get("ok") is True, f"unexpected click log response: {data}")
        return details

    def site_id_click_log() -> dict[str, Any]:
        payload = click_payload()
        payload["site_id"] = payload.pop("mall_id")
        details = click_product_url_details(payload)
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            payload,
            headers,
        )
        check(status == 200, f"site_id click log returned {status}: {data}")
        check(data.get("ok") is True, f"unexpected site_id click log response: {data}")
        return details

    def conflicting_click_site_id_rejected() -> None:
        payload = click_payload()
        payload["site_id"] = f"{args.mall_id}-other"
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            payload,
            headers,
        )
        check(status == 400, f"conflicting click site_id should return 400, got {status}: {data}")

    def invalid_click_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify invalid click API key rejection")
        bad_headers = dict(headers)
        bad_headers["X-API-Key"] = args.invalid_api_key
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            click_payload(),
            bad_headers,
        )
        check(status == 401, f"invalid click API key should return 401, got {status}: {data}")

    def query_click_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify query click API key rejection")
        query_headers = dict(headers)
        query_headers.pop("X-API-Key", None)
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log?api_key={args.api_key}",
            click_payload(),
            query_headers,
        )
        check(status == 400, f"query click API key should return 400, got {status}: {data}")

    def query_click_api_key_alias_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify query click API key alias rejection")
        query_headers = dict(headers)
        query_headers.pop("X-API-Key", None)
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log?api-key={args.api_key}",
            click_payload(),
            query_headers,
        )
        check(status == 400, f"query click API key alias should return 400, got {status}: {data}")

    def query_click_admin_key_alias_rejected() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify query click admin key alias rejection")
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log?admin_key={args.admin_key}",
            click_payload(),
            headers,
        )
        check(status == 400, f"query click admin key alias should return 400, got {status}: {data}")

    def empty_query_click_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify empty query click API key rejection")
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log?api_key=",
            click_payload(),
            headers,
        )
        check(status == 400, f"empty query click API key parameter should return 400, got {status}: {data}")

    def body_click_api_key_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify body click API key rejection")
        payload = click_payload()
        payload["api_key"] = args.api_key
        status, data, _ = request_json("POST", f"{base_url}/api/click-log", payload, headers)
        check(status == 400, f"body click API key should return 400, got {status}: {data}")

    def body_click_api_key_alias_rejected() -> None:
        check(bool(args.api_key), "--api-key is required to verify body click API key alias rejection")
        payload = click_payload()
        payload["x-api-key"] = args.api_key
        status, data, _ = request_json("POST", f"{base_url}/api/click-log", payload, headers)
        check(status == 400, f"body click API key alias should return 400, got {status}: {data}")

    def body_click_admin_key_alias_rejected() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify body click admin key alias rejection")
        payload = click_payload()
        payload["x-admin-key"] = args.admin_key
        status, data, _ = request_json("POST", f"{base_url}/api/click-log", payload, headers)
        check(status == 400, f"body click admin key alias should return 400, got {status}: {data}")

    def invalid_click_origin_rejected() -> None:
        check(bool(origin), "--origin is required to verify invalid click origin rejection")
        bad_headers = dict(headers)
        bad_headers["Origin"] = invalid_origin
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            click_payload(),
            bad_headers,
        )
        check(status == 403, f"invalid click origin should return 403, got {status}: {data}")

    def invalid_click_payload_rejected() -> None:
        payload = click_payload()
        payload.pop("product_id", None)
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            payload,
            headers,
        )
        check(status == 400, f"invalid click payload should return 400, got {status}: {data}")

    def unsupported_click_field_rejected() -> None:
        payload = {**click_payload(), "unexpected": "value"}
        status, data, _ = request_json("POST", f"{base_url}/api/click-log", payload, headers)
        detail = str(data.get("detail") or data.get("message") or "").lower()
        check(status == 400, f"unsupported click JSON field should return 400, got {status}: {data}")
        check(
            "unexpected" in detail or "extra" in detail or "not permitted" in detail,
            f"unsupported click JSON field response should mention rejected field, got: {data}",
        )

    def unsafe_click_product_url_rejected() -> None:
        unsafe_urls = [
            "javascript:alert(1)",
            "https://user:pass@example.test/product/smoke-product",
            "https://token@example.test/product/smoke-product",
        ]
        for unsafe_url in unsafe_urls:
            payload = click_payload()
            payload["product_url"] = unsafe_url
            status, data, _ = request_json(
                "POST",
                f"{base_url}/api/click-log",
                payload,
                headers,
            )
            check(status == 400, f"unsafe click product_url should return 400 for {unsafe_url}, got {status}: {data}")

    def foreign_click_product_url_rejected() -> None:
        payload = click_payload()
        payload["product_url"] = f"https://foreign.example.test/product/{payload['product_id']}"
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            payload,
            headers,
        )
        check(status == 400, f"foreign click product_url should return 400, got {status}: {data}")

    def click_product_url_template_prefix_mismatch_rejected() -> None:
        check(
            bool(expected_click_product_url_prefix),
            (
                "--mall-config, --expected-click-product-url-prefix, or --expected-product-url-prefix is required "
                "to verify click product URL template prefix rejection"
            ),
        )
        payload = click_payload()
        product_id = str(payload["product_id"])
        payload["product_url"] = wrong_click_product_url_for_prefix(expected_click_product_url_prefix, product_id)
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            payload,
            headers,
        )
        detail = str(data.get("detail") or data.get("message") or "").lower()
        check(status == 400, f"same-origin wrong-template click product_url should return 400, got {status}: {data}")
        check(
            "product_url" in detail or "product url" in detail,
            f"same-origin wrong-template click product_url response should mention product_url, got: {data}",
        )

    def click_product_url_product_id_mismatch_rejected() -> None:
        payload = click_payload()
        payload["product_url"] = fallback_click_product_url("wrong-smoke-product")
        status, data, _ = request_json(
            "POST",
            f"{base_url}/api/click-log",
            payload,
            headers,
        )
        detail = str(data.get("detail") or data.get("message") or "").lower()
        check(status == 400, f"mismatched click product_url should return 400, got {status}: {data}")
        check("product_id" in detail, f"mismatched click product_url response should mention product_id, got: {data}")

    def malformed_click_json_rejected() -> None:
        status, data, _ = request_raw_json(
            "POST",
            f"{base_url}/api/click-log",
            b'{"mall_id":',
            headers,
        )
        check(status == 400, f"malformed click JSON should return 400, got {status}: {data}")

    def click_log_rate_limited() -> None:
        probe_count = int(getattr(args, "click_rate_limit_probe_count", 0) or 0)
        check(
            probe_count >= 2,
            "--click-rate-limit-probe-count must be at least 2 when --expect-click-rate-limit is used",
        )
        statuses: list[int] = []
        for attempt in range(probe_count):
            payload = click_payload()
            payload["product_id"] = f"smoke-rate-limit-{attempt + 1}"
            payload["product_url"] = fallback_click_product_url(str(payload["product_id"]))
            click_product_url_details(payload)
            status, data, _ = request_json(
                "POST",
                f"{base_url}/api/click-log",
                payload,
                headers,
            )
            statuses.append(status)
            if status == 429:
                detail = str(data.get("detail", "")).lower()
                check("rate limit" in detail, f"click-log 429 response should mention rate limit, got: {data}")
                return
            check(status == 200 and data.get("ok") is True, f"click-log rate limit probe returned {status}: {data}")
        raise AssertionError(f"click-log rate limit did not return 429 within {probe_count} requests: {statuses}")

    def sync_status() -> dict[str, Any]:
        check(bool(args.admin_key), "--admin-key is required to verify sync status")
        status, data, _ = request_json("GET", f"{base_url}/admin/sync-status", headers=admin_headers)
        check(status == 200, f"sync status returned {status}: {data}")
        check("engine" in data and "index" in data, f"sync status missing engine/index: {data}")
        engine = str(data.get("engine") or "").strip().lower()
        index = str(data.get("index") or "").strip()
        check(engine == "marqo", f"sync status engine must be marqo, got {data}")
        check(bool(index), f"sync status index must be non-empty, got {data}")
        return {
            "sync_status_engine": data.get("engine"),
            "sync_status_index": index,
            "sync_status_engine_ok": True,
            "sync_status_index_ok": True,
        }

    def search_log() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify search log")
        status, data, _ = request_json("GET", f"{base_url}/admin/search-log?limit=100", headers=admin_headers)
        check(status == 200, f"search log returned {status}: {data}")
        check(isinstance(data, list), f"search log should return a list: {data}")
        search_entries = [entry for entry in data if isinstance(entry, dict) and entry.get("type") == "search"]
        check(search_entries, f"search log should include at least one search entry: {data}")
        check(
            any("normalized_query" in entry for entry in search_entries),
            f"search log should include normalized_query in search entries: {data}",
        )
        check(
            any("inferred_categories" in entry for entry in search_entries),
            f"search log should include inferred_categories in search entries: {data}",
        )
        check(
            any("image_perceptual_hash" in entry for entry in search_entries),
            f"search log should include image_perceptual_hash in search entries: {data}",
        )
        image_entries = [
            entry
            for entry in search_entries
            if entry.get("query_type") in {"image", "text_image"}
        ]
        check(image_entries, f"search log should include image/text-image entries: {data}")
        for field in [
            "image_size_bytes",
            "image_width",
            "image_height",
            "image_normalized",
            "image_quality_warnings",
        ]:
            check(
                any(field in entry for entry in image_entries),
                f"search log should include {field} in image search entries: {data}",
            )

    def sync_log() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify sync log")
        status, data, _ = request_json("GET", f"{base_url}/admin/sync-log?limit=5", headers=admin_headers)
        check(status == 200, f"sync log returned {status}: {data}")
        check(isinstance(data, list), f"sync log should return a list: {data}")

    def error_log() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify error log")
        status, data, _ = request_json("GET", f"{base_url}/admin/error-log?limit=5", headers=admin_headers)
        check(status == 200, f"error log returned {status}: {data}")
        check(isinstance(data, list), f"error log should return a list: {data}")

    def sensitive_log_redaction() -> dict[str, Any]:
        check(bool(args.admin_key), "--admin-key is required to verify sensitive log redaction")
        payload_bytes_by_endpoint: dict[str, int] = {}
        for name, url in [
            ("search_log", f"{base_url}/admin/search-log?limit=100"),
            ("sync_log", f"{base_url}/admin/sync-log?limit=20"),
            ("error_log", f"{base_url}/admin/error-log?limit=20"),
        ]:
            status, data, _ = request_json("GET", url, headers=admin_headers)
            check(status == 200, f"{name} returned {status}: {data}")
            check(isinstance(data, list), f"{name} should return a list: {data}")
            details = assert_no_sensitive_log_markers(data, sensitive_markers, name)
            payload_bytes_by_endpoint[name] = int(details["payload_bytes_checked"])
        return {
            "sensitive_log_redaction_ok": True,
            "sensitive_marker_count": len(sensitive_markers),
            "checked_log_endpoints": sorted(payload_bytes_by_endpoint),
            "payload_bytes_by_endpoint": payload_bytes_by_endpoint,
        }

    def search_insights() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify search insights")
        status, data, _ = request_json(
            "GET",
            f"{base_url}/admin/search-insights?limit=5&min_searches=1",
            headers=admin_headers,
        )
        check(status == 200, f"search insights returned {status}: {data}")
        for field in [
            "search_events",
            "click_events",
            "top_queries",
            "mixed_weight_performance",
            "image_quality_warning_counts",
            "recommendations",
        ]:
            check(field in data, f"search insights missing {field}: {data}")

    def metrics() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify metrics")
        status, data, _ = request_json("GET", f"{base_url}/admin/metrics?limit=100", headers=admin_headers)
        check(status == 200, f"metrics returned {status}: {data}")
        for field in [
            "engine",
            "sync",
            "search",
            "errors",
            "rate_limit",
            "cache",
            "search_queue",
            "image_queue",
            "logs",
            "disk",
            "process",
            "system",
            "alerts",
        ]:
            check(field in data, f"metrics missing {field}: {data}")
        engine = data.get("engine")
        check(isinstance(engine, dict), f"metrics engine should be an object: {data}")
        for field in ["ok", "backend"]:
            check(field in engine, f"metrics engine missing {field}: {engine}")
        rate_limit = data.get("rate_limit")
        check(isinstance(rate_limit, dict), f"metrics rate_limit should be an object: {data}")
        for field in ["backend", "redis_enabled", "fallback_events", "fallback_active", "fallback_bucket_count", "fallback_max_buckets"]:
            check(field in rate_limit, f"metrics rate_limit missing {field}: {rate_limit}")
        cache = data.get("cache")
        check(isinstance(cache, dict), f"metrics cache should be an object: {data}")
        for field in ["backend", "redis_enabled", "ttl_seconds", "error_count"]:
            check(field in cache, f"metrics cache missing {field}: {cache}")
        search_queue = data.get("search_queue")
        check(isinstance(search_queue, dict), f"metrics search_queue should be an object: {data}")
        for field in ["enabled", "max_concurrency", "queue_timeout_seconds", "in_flight", "queue_full_events"]:
            check(field in search_queue, f"metrics search_queue missing {field}: {search_queue}")
        image_queue = data.get("image_queue")
        check(isinstance(image_queue, dict), f"metrics image_queue should be an object: {data}")
        for field in ["enabled", "max_concurrency", "queue_timeout_seconds", "in_flight", "queue_full_events"]:
            check(field in image_queue, f"metrics image_queue missing {field}: {image_queue}")
        check(isinstance(data.get("alerts"), list), f"metrics alerts should be a list: {data}")
        return {
            "engine_ok": engine.get("ok"),
            "engine_backend": engine.get("backend"),
            "search_queue_enabled": search_queue.get("enabled"),
            "search_queue_max_concurrency": search_queue.get("max_concurrency"),
            "image_queue_enabled": image_queue.get("enabled"),
            "image_queue_max_concurrency": image_queue.get("max_concurrency"),
        }

    def prometheus_metrics() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify Prometheus metrics")
        status, text, _ = request_text("GET", f"{base_url}/admin/metrics.prom?limit=100", headers=admin_headers)
        check(status == 200, f"Prometheus metrics returned {status}: {text}")
        for metric_name in [
            "haeorum_engine_up",
            "haeorum_recent_search_events",
            "haeorum_rate_limit_redis_enabled",
            "haeorum_search_cache_redis_enabled",
            "haeorum_search_cache_error_events",
            "haeorum_search_queue_enabled",
            "haeorum_search_queue_full_events",
            "haeorum_image_search_queue_enabled",
            "haeorum_image_search_queue_full_events",
            "haeorum_operational_alerts",
        ]:
            check(metric_name in text, f"Prometheus metrics missing {metric_name}: {text}")
        check("# TYPE haeorum_engine_up gauge" in text, "Prometheus metrics missing gauge type metadata")

    def invalid_admin_key_rejected() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify invalid admin key rejection")
        status, data, _ = request_json(
            "GET",
            f"{base_url}/admin/sync-status",
            headers={"X-Admin-Key": args.invalid_admin_key},
        )
        check(status == 401, f"invalid admin key should return 401, got {status}: {data}")

    def admin_query_key_alias_rejected() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify admin query key alias rejection")
        status, data, _ = request_json(
            "GET",
            f"{base_url}/admin/sync-status?admin_key={args.admin_key}",
            headers=admin_headers,
        )
        check(status == 400, f"admin query key alias should return 400, got {status}: {data}")

    def admin_mutation_endpoints_protected() -> None:
        check(bool(args.admin_key), "--admin-key is required to verify admin mutation endpoint protection")
        probes = [
            ("POST", f"{base_url}/admin/sync", None),
            ("POST", f"{base_url}/admin/reindex", None),
            ("POST", f"{base_url}/admin/reindex/__smoke_product__", None),
            ("POST", f"{base_url}/admin/reindex/__smoke/product__", None),
            ("POST", f"{base_url}/admin/reindex-product", {"product_id": "__smoke/product?query#fragment__"}),
            ("DELETE", f"{base_url}/admin/product/__smoke_product__", None),
            ("DELETE", f"{base_url}/admin/product/__smoke/product__", None),
            ("POST", f"{base_url}/admin/delete-product", {"product_id": "__smoke/product?query#fragment__"}),
        ]
        for method, url, payload in probes:
            status, data, _ = request_json(method, url, payload=payload, headers={"X-Admin-Key": args.invalid_admin_key})
            check(status == 401, f"{method} {url} should reject invalid admin key with 401, got {status}: {data}")

    add_check("health", health)
    add_check("cors_preflight", cors_preflight)
    add_check("invalid_cors_preflight_rejected", invalid_cors_preflight_rejected)
    add_check("click_log_cors_preflight", click_log_cors_preflight)
    add_check("invalid_click_log_cors_preflight_rejected", invalid_click_log_cors_preflight_rejected)
    add_check("text_search", search_text)
    add_check("site_id_search", site_id_search)
    add_check("conflicting_site_id_rejected", conflicting_site_id_rejected)
    add_check("image_search", search_image)
    add_check("oversized_json_image_rejected", oversized_json_image_rejected)
    add_check("small_json_image_rejected", small_json_image_rejected)
    add_check("multipart_image_search", search_multipart_image)
    add_check("site_id_multipart_image_search", site_id_multipart_image_search)
    add_check("conflicting_multipart_site_id_rejected", conflicting_multipart_site_id_rejected)
    add_check("unsupported_multipart_field_rejected", unsupported_multipart_field_rejected)
    add_check("invalid_multipart_image_rejected", invalid_multipart_image_rejected)
    add_check("damaged_multipart_image_rejected", damaged_multipart_image_rejected)
    add_check("small_multipart_image_rejected", small_multipart_image_rejected)
    add_check("oversized_multipart_image_rejected", oversized_multipart_image_rejected)
    add_check("malformed_multipart_rejected", malformed_multipart_rejected)
    add_check("mixed_search", search_mixed)
    add_check("openapi_click_rate_limit_documented", openapi_click_rate_limit_documented)
    add_check("invalid_api_key_rejected", invalid_api_key_rejected)
    add_check("query_api_key_rejected", query_api_key_rejected)
    add_check("query_api_key_alias_rejected", query_api_key_alias_rejected)
    add_check("query_admin_key_alias_rejected", query_admin_key_alias_rejected)
    add_check("empty_query_api_key_rejected", empty_query_api_key_rejected)
    add_check("body_api_key_rejected", body_api_key_rejected)
    add_check("body_api_key_alias_rejected", body_api_key_alias_rejected)
    add_check("body_admin_key_alias_rejected", body_admin_key_alias_rejected)
    add_check("multipart_body_api_key_rejected", multipart_body_api_key_rejected)
    add_check("multipart_body_api_key_alias_rejected", multipart_body_api_key_alias_rejected)
    add_check("multipart_body_admin_key_alias_rejected", multipart_body_admin_key_alias_rejected)
    add_check("invalid_origin_rejected", invalid_origin_rejected)
    add_check("invalid_search_payload_rejected", invalid_search_payload_rejected)
    add_check("unsupported_json_field_rejected", unsupported_json_field_rejected)
    add_check("invalid_domain_filter_rejected", invalid_domain_filter_rejected)
    add_check("malformed_search_json_rejected", malformed_search_json_rejected)
    add_check("click_log", click_log)
    add_check("site_id_click_log", site_id_click_log)
    add_check("conflicting_click_site_id_rejected", conflicting_click_site_id_rejected)
    add_check("invalid_click_api_key_rejected", invalid_click_api_key_rejected)
    add_check("query_click_api_key_rejected", query_click_api_key_rejected)
    add_check("query_click_api_key_alias_rejected", query_click_api_key_alias_rejected)
    add_check("query_click_admin_key_alias_rejected", query_click_admin_key_alias_rejected)
    add_check("empty_query_click_api_key_rejected", empty_query_click_api_key_rejected)
    add_check("body_click_api_key_rejected", body_click_api_key_rejected)
    add_check("body_click_api_key_alias_rejected", body_click_api_key_alias_rejected)
    add_check("body_click_admin_key_alias_rejected", body_click_admin_key_alias_rejected)
    add_check("invalid_click_origin_rejected", invalid_click_origin_rejected)
    add_check("invalid_click_payload_rejected", invalid_click_payload_rejected)
    add_check("unsupported_click_field_rejected", unsupported_click_field_rejected)
    add_check("unsafe_click_product_url_rejected", unsafe_click_product_url_rejected)
    add_check("foreign_click_product_url_rejected", foreign_click_product_url_rejected)
    add_check("click_product_url_template_prefix_mismatch_rejected", click_product_url_template_prefix_mismatch_rejected)
    add_check("click_product_url_product_id_mismatch_rejected", click_product_url_product_id_mismatch_rejected)
    add_check("malformed_click_json_rejected", malformed_click_json_rejected)
    if getattr(args, "expect_click_rate_limit", False):
        add_check("click_log_rate_limited", click_log_rate_limited)
    add_check("sync_status", sync_status)
    add_check("search_log", search_log)
    add_check("sync_log", sync_log)
    add_check("error_log", error_log)
    add_check("sensitive_log_redaction", sensitive_log_redaction)
    add_check("search_insights", search_insights)
    add_check("metrics", metrics)
    add_check("prometheus_metrics", prometheus_metrics)
    add_check("invalid_admin_key_rejected", invalid_admin_key_rejected)
    add_check("admin_query_key_alias_rejected", admin_query_key_alias_rejected)
    add_check("admin_mutation_endpoints_protected", admin_mutation_endpoints_protected)

    return {
        "ok": all(item["ok"] for item in checks),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "mall_id": args.mall_id,
        "origin": origin or None,
        "expected_product_url_prefix": expected_product_url_prefix or None,
        "expected_click_product_url_prefix": expected_click_product_url_prefix or None,
        "target_validation": target_validation,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a running Haeorum AI Search API.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--mall-id", default="shop001")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--origin", default="", help="Optional Origin header for mall allowed_origins checks.")
    parser.add_argument("--mall-config", default="", help="Optional mall config used to validate result product URL prefixes.")
    parser.add_argument(
        "--expected-product-url-prefix",
        default="",
        help="Optional explicit product URL prefix that all returned product_url values must match.",
    )
    parser.add_argument(
        "--expected-click-product-url-prefix",
        default="",
        help=(
            "Optional explicit click product URL template prefix. "
            "When omitted with --mall-config, this is derived from product_url_template including query keys."
        ),
    )
    parser.add_argument("--invalid-api-key", default="invalid-smoke-api-key")
    parser.add_argument("--invalid-origin", default="https://invalid-origin.example.test")
    parser.add_argument("--invalid-admin-key", default="invalid-smoke-admin-key")
    parser.add_argument("--admin-key", default="")
    parser.add_argument(
        "--oversized-upload-mb",
        type=int,
        default=12,
        help="Multipart upload size used to verify 413 rejection. Keep above the production image limit.",
    )
    parser.add_argument(
        "--expect-click-rate-limit",
        action="store_true",
        help="Burst /api/click-log until a 429 is observed. Use only on an isolated mall/key or staging.",
    )
    parser.add_argument(
        "--click-rate-limit-probe-count",
        type=int,
        default=0,
        help="Number of click-log requests to send when --expect-click-rate-limit is set.",
    )
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--allow-local-target",
        action="store_true",
        help="Allow localhost/private targets for local development only. Operational evidence must omit this flag.",
    )
    args = parser.parse_args()

    report = run(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
