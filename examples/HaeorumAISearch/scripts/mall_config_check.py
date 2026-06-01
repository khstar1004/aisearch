from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import (
    PUBLIC_API_KEY_MIN_DISTINCT_CHARS,
    PUBLIC_API_KEY_MIN_LENGTH,
    is_placeholder_public_api_key,
    normalize_origin_value,
    origin_uses_safe_public_url,
    public_api_key_strength_problems,
    validate_product_url_template_value,
)
from app.identifiers import normalize_mall_id


REDACTED_API_KEY_VALUE = "[redacted]"


def api_key_hash(value: Any) -> str:
    text = str(value or "").strip()
    if not text or is_placeholder_public_api_key(text):
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redacted_api_key_duplicate(value: Any) -> dict[str, str]:
    detail = {"value": REDACTED_API_KEY_VALUE}
    key_hash = api_key_hash(value)
    if key_hash:
        detail["api_key_hash"] = key_hash
    return detail


def redacted_api_key_duplicates(values: list[str]) -> list[dict[str, str]]:
    return [redacted_api_key_duplicate(value) for value in values]


def load_raw_malls(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data if isinstance(data, list) else data.get("malls", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        raise ValueError("mall config must be a list or an object with a malls list")
    return [item for item in raw if isinstance(item, dict)]


def validate_mall_config(path: Path, min_count: int = 1, require_strong_api_keys: bool | None = None) -> dict[str, Any]:
    if require_strong_api_keys is None:
        require_strong_api_keys = min_count >= 100
    raw_malls = load_raw_malls(path)
    mall_ids: list[str] = []
    enabled_mall_ids: list[str] = []
    enabled_mall_origins: dict[str, list[str]] = {}
    enabled_mall_product_url_prefixes: dict[str, str] = {}
    enabled_mall_api_key_hashes: dict[str, str] = {}
    enabled_origin_mall_ids: dict[str, set[str]] = {}
    enabled_product_url_prefix_mall_ids: dict[str, set[str]] = {}
    api_keys: list[str] = []
    weak_api_key_mall_ids: list[str] = []
    enabled_count = 0
    problems: list[dict[str, Any]] = []

    for index, raw in enumerate(raw_malls):
        mall_id = str(raw.get("mall_id") or raw.get("mallId") or "").strip()
        valid_mall_id = True
        try:
            normalized_mall_id = normalize_mall_id(mall_id, required=True)
        except ValueError as exc:
            valid_mall_id = False
            normalized_mall_id = mall_id
            if mall_id:
                problems.append({"index": index, "field": "mall_id", "value": mall_id, "message": str(exc)})
        api_key = str(raw.get("api_key") or raw.get("apiKey") or "").strip()
        template = str(raw.get("product_url_template") or raw.get("productUrlTemplate") or "").strip()
        enabled_raw = raw.get("enabled")
        enabled = parse_bool(enabled_raw, default=True)
        allowed_origins = parse_policy_list(policy_value(raw, "allowed_origins", "allowedOrigins"))
        excluded_product_ids = parse_policy_list(policy_value(raw, "excluded_product_ids", "excludedProductIds"))
        excluded_categories = parse_policy_list(policy_value(raw, "excluded_categories", "excludedCategories"))
        hide_prices = policy_value(raw, "hide_prices", "hidePrices")
        price_multiplier = policy_value(raw, "price_multiplier", "priceMultiplier")
        price_adjustment = policy_value(raw, "price_adjustment", "priceAdjustment")
        price_round_to = policy_value(raw, "price_round_to", "priceRoundTo")

        if enabled:
            enabled_count += 1
            if normalized_mall_id and valid_mall_id:
                enabled_mall_ids.append(normalized_mall_id)
                normalized_origins = normalized_allowed_origin_values(allowed_origins)
                enabled_mall_origins[normalized_mall_id] = normalized_origins
                for origin in normalized_origins:
                    enabled_origin_mall_ids.setdefault(origin, set()).add(normalized_mall_id)
                product_url_prefix = product_url_template_prefix(template, normalized_mall_id)
                if product_url_prefix:
                    enabled_mall_product_url_prefixes[normalized_mall_id] = product_url_prefix
                    enabled_product_url_prefix_mall_ids.setdefault(product_url_prefix, set()).add(normalized_mall_id)
                key_hash = api_key_hash(api_key)
                if key_hash:
                    enabled_mall_api_key_hashes[normalized_mall_id] = key_hash
        if not mall_id:
            problems.append({"index": index, "field": "mall_id", "message": "mall_id is required"})
        elif valid_mall_id:
            mall_ids.append(normalized_mall_id)

        if enabled and not api_key:
            problems.append({"index": index, "mall_id": mall_id, "field": "api_key", "message": "enabled mall requires api_key"})
        elif enabled and is_placeholder_public_api_key(api_key):
            problems.append(
                {
                    "index": index,
                    "mall_id": mall_id,
                    "field": "api_key",
                    "message": "enabled mall api_key must be changed from sample or placeholder value",
                }
            )
        elif enabled and require_strong_api_keys:
            strength_problems = public_api_key_strength_problems(api_key)
            if strength_problems:
                weak_api_key_mall_ids.append(normalized_mall_id if valid_mall_id else mall_id)
                problems.append(
                    {
                        "index": index,
                        "mall_id": mall_id,
                        "field": "api_key",
                        "message": "enabled mall api_key must be a strong random value",
                        "strength_problems": strength_problems,
                    }
                )
        if api_key:
            api_keys.append(api_key)

        if enabled and not template:
            problems.append(
                {
                    "index": index,
                    "mall_id": mall_id,
                    "field": "product_url_template",
                    "message": "enabled mall requires product_url_template",
                }
            )
        elif template:
            validate_template(index, mall_id, template, problems)
        if enabled and not allowed_origins:
            problems.append(
                {
                    "index": index,
                    "mall_id": mall_id,
                    "field": "allowed_origins",
                    "message": "enabled mall requires allowed_origins",
                }
            )
        validate_bool_field(index, mall_id, "enabled", enabled_raw, problems)
        validate_policy_list(index, mall_id, "allowed_origins", allowed_origins, problems)
        validate_allowed_origins(index, mall_id, allowed_origins, problems)
        validate_policy_list(index, mall_id, "excluded_product_ids", excluded_product_ids, problems)
        validate_policy_list(index, mall_id, "excluded_categories", excluded_categories, problems)
        validate_bool_field(index, mall_id, "hide_prices", hide_prices, problems)
        validate_float_field(
            index,
            mall_id,
            "price_multiplier",
            price_multiplier,
            problems,
            minimum=0.0,
            allow_equal_minimum=False,
        )
        validate_float_field(index, mall_id, "price_adjustment", price_adjustment, problems)
        validate_int_field(index, mall_id, "price_round_to", price_round_to, problems, minimum=1)

    duplicate_mall_ids = duplicates(mall_ids)
    duplicate_api_key_values = duplicates(api_keys)
    duplicate_api_keys = redacted_api_key_duplicates(duplicate_api_key_values)
    duplicate_allowed_origins = duplicate_value_map(enabled_origin_mall_ids)
    duplicate_product_url_prefixes = duplicate_value_map(enabled_product_url_prefix_mall_ids)
    for mall_id in duplicate_mall_ids:
        problems.append({"field": "mall_id", "value": mall_id, "message": "duplicate mall_id"})
    for api_key in duplicate_api_key_values:
        problems.append({"field": "api_key", **redacted_api_key_duplicate(api_key), "message": "duplicate api_key"})
    for origin, mall_ids_with_origin in duplicate_allowed_origins.items():
        problems.append(
            {
                "field": "allowed_origins",
                "value": origin,
                "mall_ids": mall_ids_with_origin,
                "message": "allowed_origins value is reused by multiple enabled malls",
            }
        )
    for prefix, mall_ids_with_prefix in duplicate_product_url_prefixes.items():
        problems.append(
            {
                "field": "product_url_template",
                "value": prefix,
                "mall_ids": mall_ids_with_prefix,
                "message": "product_url_template prefix is reused by multiple enabled malls",
            }
        )
    if len(raw_malls) < min_count:
        problems.append({"field": "malls", "message": f"mall count is below required minimum {min_count}"})
    if enabled_count < min_count:
        problems.append(
            {
                "field": "enabled_count",
                "message": f"enabled mall count is below required minimum {min_count}",
            }
        )

    return {
        "ok": not problems,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "path": str(path),
        "mall_count": len(raw_malls),
        "enabled_count": enabled_count,
        "enabled_mall_ids": sorted(enabled_mall_ids),
        "enabled_mall_origins": {mall_id: enabled_mall_origins[mall_id] for mall_id in sorted(enabled_mall_origins)},
        "enabled_mall_product_url_prefixes": {
            mall_id: enabled_mall_product_url_prefixes[mall_id]
            for mall_id in sorted(enabled_mall_product_url_prefixes)
        },
        "enabled_mall_api_key_hashes": {
            mall_id: enabled_mall_api_key_hashes[mall_id]
            for mall_id in sorted(enabled_mall_api_key_hashes)
        },
        "min_count": min_count,
        "duplicate_mall_ids": duplicate_mall_ids,
        "duplicate_api_keys": duplicate_api_keys,
        "duplicate_allowed_origins": duplicate_allowed_origins,
        "duplicate_product_url_prefixes": duplicate_product_url_prefixes,
        "api_key_strength": {
            "required": bool(require_strong_api_keys),
            "minimum_length": PUBLIC_API_KEY_MIN_LENGTH,
            "minimum_distinct_chars": PUBLIC_API_KEY_MIN_DISTINCT_CHARS,
        },
        "weak_api_key_mall_ids": sorted(set(mall_id for mall_id in weak_api_key_mall_ids if mall_id)),
        "problems": problems,
    }


def normalized_allowed_origin_values(values: list[str]) -> list[str]:
    normalized_values: list[str] = []
    for value in values:
        try:
            normalized = normalize_origin_value(value, allow_wildcard=False)
        except ValueError:
            continue
        normalized_values.append(normalized.rstrip("/"))
    return sorted(set(normalized_values))


def product_url_template_prefix(template: str, mall_id: str) -> str | None:
    if not template:
        return None
    try:
        validate_product_url_template_value(template, mall_id=mall_id or "shop001")
    except ValueError:
        return None
    sentinel = "__HAEORUM_PRODUCT_ID__"
    try:
        formatted = template.format(product_id=sentinel, mall_id=mall_id or "shop001")
    except Exception:
        return None
    if sentinel not in formatted:
        return None
    prefix = formatted.split(sentinel, 1)[0].rstrip("/?&=")
    parsed = urlparse(prefix)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc}{path}"


def validate_template(index: int, mall_id: str, template: str, problems: list[dict[str, Any]]) -> None:
    try:
        validate_product_url_template_value(template, mall_id=mall_id or "shop001")
    except ValueError as exc:
        problems.append(
            {
                "index": index,
                "mall_id": mall_id,
                "field": "product_url_template",
                "message": str(exc),
            }
        )


def parse_policy_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.replace("|", ",").replace(";", ",").split(",") if item.strip()]
    return ["__invalid_policy_list__"]


def policy_value(raw: dict[str, Any], primary: str, alias: str) -> Any:
    if primary in raw:
        return raw[primary]
    return raw.get(alias)


def validate_policy_list(
    index: int,
    mall_id: str,
    field: str,
    values: list[str],
    problems: list[dict[str, Any]],
) -> None:
    if "__invalid_policy_list__" in values:
        problems.append(
            {
                "index": index,
                "mall_id": mall_id,
                "field": field,
                "message": f"{field} must be a list or delimited string",
            }
        )
        return
    duplicates_in_field = duplicates(values)
    for value in duplicates_in_field:
        problems.append(
            {
                "index": index,
                "mall_id": mall_id,
                "field": field,
                "value": value,
                "message": f"duplicate {field} value",
            }
        )


def validate_allowed_origins(
    index: int,
    mall_id: str,
    values: list[str],
    problems: list[dict[str, Any]],
) -> None:
    if "__invalid_policy_list__" in values:
        return
    for value in values:
        try:
            normalized = normalize_origin_value(value, allow_wildcard=True)
        except ValueError as exc:
            problems.append(
                {
                    "index": index,
                    "mall_id": mall_id,
                    "field": "allowed_origins",
                    "value": value,
                    "message": str(exc),
                }
            )
            continue
        if normalized == "*":
            problems.append(
                {
                    "index": index,
                    "mall_id": mall_id,
                    "field": "allowed_origins",
                    "value": value,
                    "message": "allowed_origins must not contain wildcard *",
                }
            )
            continue
        if not normalized.startswith("https://"):
            problems.append(
                {
                    "index": index,
                    "mall_id": mall_id,
                    "field": "allowed_origins",
                    "value": value,
                    "message": "allowed_origins must use https",
                }
            )
        if not origin_uses_safe_public_url(normalized):
            problems.append(
                {
                    "index": index,
                    "mall_id": mall_id,
                    "field": "allowed_origins",
                    "value": value,
                    "message": "allowed_origins must use safe public origins",
                }
            )


def validate_bool_field(
    index: int,
    mall_id: str,
    field: str,
    value: Any,
    problems: list[dict[str, Any]],
) -> None:
    if value in (None, ""):
        return
    if isinstance(value, bool):
        return
    if isinstance(value, str) and value.strip().lower() in {
        "1",
        "0",
        "true",
        "false",
        "yes",
        "no",
        "y",
        "n",
        "on",
        "off",
        "사용",
        "미사용",
        "활성",
        "비활성",
        "예",
        "아니오",
        "네",
        "아니요",
    }:
        return
    problems.append({"index": index, "mall_id": mall_id, "field": field, "message": f"{field} must be a boolean"})


def parse_bool(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "사용", "활성", "예", "네"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "미사용", "비활성", "아니오", "아니요"}:
            return False
    return bool(value)


def validate_float_field(
    index: int,
    mall_id: str,
    field: str,
    value: Any,
    problems: list[dict[str, Any]],
    minimum: float | None = None,
    allow_equal_minimum: bool = True,
) -> None:
    if value in (None, ""):
        return
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        problems.append({"index": index, "mall_id": mall_id, "field": field, "message": f"{field} must be a number"})
        return
    if not math.isfinite(parsed):
        problems.append({"index": index, "mall_id": mall_id, "field": field, "message": f"{field} must be finite"})
        return
    if minimum is None:
        return
    if allow_equal_minimum and parsed < minimum:
        problems.append(
            {"index": index, "mall_id": mall_id, "field": field, "message": f"{field} must be at least {minimum:g}"}
        )
    if not allow_equal_minimum and parsed <= minimum:
        problems.append(
            {"index": index, "mall_id": mall_id, "field": field, "message": f"{field} must be greater than {minimum:g}"}
        )


def validate_int_field(
    index: int,
    mall_id: str,
    field: str,
    value: Any,
    problems: list[dict[str, Any]],
    minimum: int | None = None,
) -> None:
    if value in (None, ""):
        return
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        problems.append({"index": index, "mall_id": mall_id, "field": field, "message": f"{field} must be an integer"})
        return
    if minimum is not None and parsed < minimum:
        problems.append({"index": index, "mall_id": mall_id, "field": field, "message": f"{field} must be at least {minimum}"})


def duplicates(values: list[str]) -> list[str]:
    seen = set()
    duplicate_values = set()
    for value in values:
        if value in seen:
            duplicate_values.add(value)
        seen.add(value)
    return sorted(duplicate_values)


def duplicate_value_map(value_to_mall_ids: dict[str, set[str]]) -> dict[str, list[str]]:
    return {
        value: sorted(mall_ids)
        for value, mall_ids in sorted(value_to_mall_ids.items())
        if value and len(mall_ids) > 1
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Haeorum mall configuration for multi-site rollout.")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "sample_malls.json"))
    parser.add_argument("--min-count", type=int, default=1)
    strength = parser.add_mutually_exclusive_group()
    parser.set_defaults(require_strong_api_keys=None)
    strength.add_argument(
        "--require-strong-api-keys",
        dest="require_strong_api_keys",
        action="store_true",
        help="Require enabled mall API keys to meet production strength rules.",
    )
    strength.add_argument(
        "--allow-weak-api-keys",
        dest="require_strong_api_keys",
        action="store_false",
        help="Disable production API key strength checks for local compatibility probes.",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = validate_mall_config(
        Path(args.config),
        min_count=args.min_count,
        require_strong_api_keys=args.require_strong_api_keys,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
