from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import DEPLOYABLE_SEARCH_ENGINES, Settings, load_mall_configs, load_settings, validate_marqo_url_value
from app.engine import LocalSearchEngine
from app.engine_factory import create_search_engine
from app.image_validation import validate_image_bytes
from app.models import ProductDocument, SearchRequest, SearchResultItem
from app.search_service import AISearchService, SearchLogger
from app.sync import CsvProductSource
from app.url_safety import product_url_contains_product_id, safe_absolute_http_url
from scripts.mall_config_check import product_url_template_prefix
from scripts.sample_data_guard import builtin_sample_dataset_profile, file_fingerprint, is_local_sample_evidence


QUALITY_CASES = [
    {
        "name": "text_black_umbrella",
        "query": {"q": "검은 우산"},
        "expected_top_product_id": "P001",
        "expected_category": "우산",
        "expected_min_results": 3,
    },
    {
        "name": "text_typo_umbrella",
        "query": {"q": "우싼"},
        "expected_category": "우산",
        "expected_min_results": 3,
        "tags": ["typo_or_synonym"],
    },
    {
        "name": "text_stainless_tumbler",
        "query": {"q": "스텐 텀블러"},
        "expected_top_product_id": "P004",
        "expected_category": "텀블러",
        "expected_min_results": 3,
    },
    {
        "name": "text_sticky_memo",
        "query": {"q": "점착 메모지"},
        "expected_category": "점착메모지",
        "expected_min_results": 3,
    },
    {
        "name": "text_crystal_award",
        "query": {"q": "크리스탈 상패"},
        "expected_top_product_id": "P008",
        "expected_category": "상패",
        "expected_min_results": 3,
    },
    {
        "name": "text_premium_pen",
        "query": {"q": "고급 볼펜"},
        "expected_top_product_id": "P010",
        "expected_category": "볼펜",
        "expected_min_results": 3,
    },
    {
        "name": "image_only_smoke",
        "query": {"image": True},
        "expected_category": "우산",
        "expected_min_results": 3,
    },
    {
        "name": "mixed_black_image",
        "query": {"q": "검은색", "image": True},
        "expected_category": "텀블러",
        "expected_min_results": 3,
    },
    {
        "name": "low_confidence_image_notice",
        "query": {"image": True},
        "expected_low_confidence": True,
        "expected_min_results": 0,
    },
]
QUALITY_CASE_MIN_TYPE_COUNTS = {"text": 2, "image": 1, "text_image": 1}
QUALITY_CASE_MIN_RESULTS = 3
QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES = 1
QUALITY_CASE_MIN_TEXT_VARIANT_CASES = 1


def load_quality_cases(cases_path: str | Path | None = None) -> tuple[list[dict[str, Any]], str]:
    if not cases_path:
        return [dict(case) for case in QUALITY_CASES], "builtin"
    path = Path(cases_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(cases, list) or not cases:
        raise ValueError("quality cases file must contain a non-empty cases list")
    normalized = []
    for index, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"quality case {index} must be an object")
        case = dict(item)
        name = str(case.get("name") or "").strip()
        if not name:
            raise ValueError(f"quality case {index} is missing name")
        query = case.get("query")
        if not isinstance(query, dict):
            raise ValueError(f"quality case {name} is missing query object")
        query = dict(query)
        if case.get("image_path") or case.get("image_base64") or case.get("image_data_url"):
            query["image"] = True
        if not str(query.get("q") or "").strip() and not query.get("image"):
            raise ValueError(f"quality case {name} must include q or image input")
        image_path = str(case.get("image_path") or "").strip()
        if image_path and not Path(image_path).is_absolute():
            case["image_path"] = str((path.parent / image_path).resolve())
        case["query"] = query
        normalized.append(case)
    return normalized, str(path)


def make_png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(12, 120, 110)).save(buffer, format="PNG")
    return buffer.getvalue()


def image_payload_for_case(case: dict[str, Any], default_image_base64: str) -> tuple[str, str]:
    image_path = str(case.get("image_path") or "").strip()
    if image_path:
        image = validate_image_bytes(Path(image_path).read_bytes(), max_bytes=5 * 1024 * 1024)
        return image.data_url.split(",", 1)[1], "case_file"
    image_data_url = str(case.get("image_data_url") or "").strip()
    if image_data_url:
        image = validate_image_base64_for_report(image_data_url)
        return image.data_url.split(",", 1)[1], "case_data_url"
    image_base64 = str(case.get("image_base64") or "").strip()
    if image_base64:
        image = validate_image_base64_for_report(image_base64)
        return image.data_url.split(",", 1)[1], "case_base64"
    return default_image_base64, "generated"


def image_fingerprint_for_case(case: dict[str, Any], image_source: str | None) -> dict[str, Any] | None:
    if image_source != "case_file":
        return None
    image_path = str(case.get("image_path") or "").strip()
    if not image_path:
        return None
    return file_fingerprint(image_path)


def validate_image_base64_for_report(value: str):  # type: ignore[no-untyped-def]
    from app.image_validation import validate_image_base64

    return validate_image_base64(value, max_bytes=5 * 1024 * 1024)


def make_service(csv_path: Path, log_path: Path, args: argparse.Namespace | None = None) -> tuple[AISearchService, list[ProductDocument]]:
    products = CsvProductSource(csv_path).fetch_all()
    engine_backend = str(getattr(args, "engine", "local") or "local")
    runtime_settings = load_settings()
    malls = load_quality_mall_configs(args)
    settings = Settings(
        engine_backend=engine_backend,
        product_csv_path=csv_path,
        search_log_path=log_path,
        product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
        cache_ttl_seconds=0,
        marqo_url=runtime_settings.marqo_url,
        marqo_model=runtime_settings.marqo_model,
        embedding_backend=runtime_settings.embedding_backend,
        qwen_embedding_url=runtime_settings.qwen_embedding_url,
        qwen_embedding_dimensions=runtime_settings.qwen_embedding_dimensions,
        qwen_model=runtime_settings.qwen_model,
        qwen_query_embedding_cache_path=runtime_settings.qwen_query_embedding_cache_path,
        index_name=runtime_settings.index_name,
        text_auxiliary_weight=runtime_settings.text_auxiliary_weight,
        text_auxiliary_candidate_multiplier=runtime_settings.text_auxiliary_candidate_multiplier,
        text_auxiliary_search_parallelism=runtime_settings.text_auxiliary_search_parallelism,
        malls=malls,
    )
    if args is not None:
        updates: dict[str, Any] = {}
        if getattr(args, "marqo_url", ""):
            updates["marqo_url"] = validate_marqo_url_value(args.marqo_url, "--marqo-url")
        if getattr(args, "index_name", ""):
            updates["index_name"] = str(args.index_name)
        if getattr(args, "marqo_model", ""):
            updates["marqo_model"] = str(args.marqo_model)
        if updates:
            settings = replace(settings, **updates)
    engine = LocalSearchEngine(products) if settings.engine_backend == "local" else create_search_engine(settings, preload_local_products=False)
    return AISearchService(engine, settings, SearchLogger(log_path)), products


def load_quality_mall_configs(args: argparse.Namespace | None) -> dict[str, Any]:
    path_text = str(getattr(args, "mall_config", "") if args is not None else "").strip()
    if not path_text:
        return {}
    try:
        return load_mall_configs(Path(path_text))
    except Exception:
        return {}


def summarize_dataset(products: list[ProductDocument], min_products: int, recommended_products: int) -> dict[str, Any]:
    active = [product for product in products if product.active]
    categories = Counter(product.category or "(none)" for product in active)
    missing_image_urls = [product.product_id for product in active if not product.image_url]
    return {
        "total_products": len(products),
        "active_products": len(active),
        "inactive_products": len(products) - len(active),
        "category_count": len(categories),
        "categories": dict(sorted(categories.items())),
        "missing_image_url_count": len(missing_image_urls),
        "missing_image_url_product_ids": missing_image_urls[:20],
        "minimum_poc_products": min_products,
        "recommended_poc_products": recommended_products,
        "meets_minimum_poc_size": len(active) >= min_products,
        "meets_recommended_poc_size": len(active) >= recommended_products,
    }


def failed_quality_report(args: argparse.Namespace, csv_path: Path, message: str) -> dict[str, Any]:
    dataset = summarize_dataset([], args.min_products, args.recommended_products)
    return {
        "ok": False,
        "local_only": True,
        "not_operational_readiness": True,
        "quality_ok": False,
        "response_time_ok": False,
        "dataset_ready": False,
        "strict": args.strict,
        "csv": str(csv_path),
        "csv_fingerprint": file_fingerprint(csv_path),
        "mall_id": args.mall_id,
        "case_source": "not_loaded",
        "case_source_fingerprint": file_fingerprint(getattr(args, "cases", "")),
        "mall_config": mall_config_report(getattr(args, "mall_config", ""), getattr(args, "mall_id", "")),
        "custom_cases": False,
        "case_count": 0,
        "skipped_case_checks": 0,
        "image_source_counts": {},
        "image_cases_with_supplied_source": 0,
        "mixed_cases_with_supplied_source": 0,
        "image_cases_with_file_source": 0,
        "mixed_cases_with_file_source": 0,
        "case_image_fingerprints": [],
        "case_contract": {"ok": False, "problems": [message]},
        "result_contract": {"ok": False, "problems": [message]},
        "source": {
            "engine": getattr(args, "engine", ""),
            "marqo_url_configured": bool(getattr(args, "marqo_url", "")),
        },
        "dataset": dataset,
        "response_time": {"ok": False, "by_query_type": {}},
        "cases": [],
        "error": message,
    }


def summarize_response_times(cases: list[dict[str, Any]], thresholds: dict[str, int]) -> dict[str, Any]:
    by_query_type: dict[str, dict[str, Any]] = {}
    for query_type in ["text", "image", "text_image"]:
        values = [
            float(case["elapsed_ms"])
            for case in cases
            if case.get("query_type") == query_type and isinstance(case.get("elapsed_ms"), int | float)
        ]
        threshold = int(thresholds[query_type])
        max_ms = round(max(values), 1) if values else None
        by_query_type[query_type] = {
            "count": len(values),
            "avg_ms": round(sum(values) / len(values), 1) if values else None,
            "max_ms": max_ms,
            "threshold_ms": threshold,
            "ok": bool(values) and max_ms is not None and max_ms <= threshold,
        }
    return {
        "ok": all(summary["ok"] for summary in by_query_type.values()),
        "by_query_type": by_query_type,
    }


def summarize_case_contract(cases: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts = {query_type: 0 for query_type in QUALITY_CASE_MIN_TYPE_COUNTS}
    missing_expectation_cases = []
    low_min_result_cases = []
    seen_names: set[str] = set()
    duplicate_case_names: set[str] = set()
    low_confidence_case_count = 0
    text_variant_case_count = 0
    for case in cases:
        name = str(case.get("name") or "")
        if name:
            if name in seen_names:
                duplicate_case_names.add(name)
            seen_names.add(name)
        query_type = str(case.get("query_type") or "")
        if query_type in type_counts:
            type_counts[query_type] += 1
        if query_type == "text" and case_has_text_variant_marker(case):
            text_variant_case_count += 1
        checks = case.get("checks") if isinstance(case.get("checks"), list) else []
        check_names = {str(check.get("name") or "") for check in checks if isinstance(check, dict)}
        low_confidence_case = case_expects_low_confidence(checks)
        if low_confidence_case:
            low_confidence_case_count += 1
        if not ({"expected_category", "expected_top_product_id"} & check_names) and not low_confidence_case:
            missing_expectation_cases.append(name)
        if low_confidence_case:
            continue
        min_result_expectations = [
            parse_int_value(check.get("expected"), 0)
            for check in checks
            if isinstance(check, dict) and check.get("name") == "expected_min_results"
        ]
        if max(min_result_expectations or [0]) < QUALITY_CASE_MIN_RESULTS:
            low_min_result_cases.append(name)
    missing_type_counts = {
        query_type: {"expected": expected, "actual": type_counts.get(query_type, 0)}
        for query_type, expected in QUALITY_CASE_MIN_TYPE_COUNTS.items()
        if type_counts.get(query_type, 0) < expected
    }
    missing_low_confidence_case = low_confidence_case_count < QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES
    missing_text_variant_case = text_variant_case_count < QUALITY_CASE_MIN_TEXT_VARIANT_CASES
    return {
        "ok": not missing_type_counts
        and not missing_expectation_cases
        and not low_min_result_cases
        and not duplicate_case_names
        and not missing_low_confidence_case
        and not missing_text_variant_case,
        "min_type_counts": dict(QUALITY_CASE_MIN_TYPE_COUNTS),
        "min_expected_results": QUALITY_CASE_MIN_RESULTS,
        "min_low_confidence_cases": QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES,
        "min_text_variant_cases": QUALITY_CASE_MIN_TEXT_VARIANT_CASES,
        "query_type_counts": type_counts,
        "low_confidence_case_count": low_confidence_case_count,
        "missing_low_confidence_case": missing_low_confidence_case,
        "text_variant_case_count": text_variant_case_count,
        "missing_text_variant_case": missing_text_variant_case,
        "missing_type_counts": missing_type_counts,
        "missing_expectation_cases": missing_expectation_cases,
        "low_min_result_cases": low_min_result_cases,
        "duplicate_case_names": sorted(duplicate_case_names),
    }


def case_has_text_variant_marker(case: dict[str, Any]) -> bool:
    name = str(case.get("name") or "").lower()
    if any(token in name for token in ["typo", "synonym", "variant"]):
        return True
    return "typo_or_synonym" in case_tags(case)


def case_tags(case: dict[str, Any]) -> list[str]:
    raw_tags = case.get("tags")
    if isinstance(raw_tags, str):
        values = raw_tags.replace(";", ",").replace("|", ",").split(",")
    elif isinstance(raw_tags, list):
        values = raw_tags
    else:
        values = []
    return [str(value).strip().lower() for value in values if str(value).strip()]


def case_expects_low_confidence(checks: list[Any]) -> bool:
    for check in checks:
        if not isinstance(check, dict) or check.get("name") != "expected_low_confidence":
            continue
        expected = check.get("expected")
        return expected is True or str(expected).strip().lower() == "true"
    return False


def summarize_case_result_contract(
    *,
    expected_mall_id: str,
    expected_product_url_prefix: str = "",
    top: list[SearchResultItem],
    items: list[SearchResultItem],
) -> dict[str, Any]:
    expected_mall_id = str(expected_mall_id or "").strip()
    expected_product_url_prefix = str(expected_product_url_prefix or "").strip().rstrip("/")
    observed_items: list[dict[str, Any]] = []
    product_url_missing_count = 0
    product_url_unsafe_count = 0
    product_url_product_id_mismatch_count = 0
    product_url_prefix_mismatch_count = 0
    mall_id_missing_count = 0
    mall_id_mismatch_count = 0

    for section, result_items in [("top", top), ("items", items)]:
        for index, item in enumerate(result_items):
            product_id = str(item.product_id or "").strip()
            product_url = str(item.product_url or "").strip()
            mall_id = str(item.mall_id or "").strip()
            product_url_safe = bool(product_url and safe_absolute_http_url(product_url) is not None)
            product_url_contains_id = bool(
                product_id
                and product_url
                and product_url_contains_product_id(product_url, product_id)
            )
            product_url_matches_expected_prefix = (
                url_matches_prefix(product_url, expected_product_url_prefix)
                if expected_product_url_prefix
                else None
            )
            mall_id_matches_request = bool(mall_id and (not expected_mall_id or mall_id == expected_mall_id))

            if not product_url:
                product_url_missing_count += 1
            elif not product_url_safe:
                product_url_unsafe_count += 1
            if product_url and not product_url_contains_id:
                product_url_product_id_mismatch_count += 1
            if expected_product_url_prefix and not product_url_matches_expected_prefix:
                product_url_prefix_mismatch_count += 1
            if not mall_id:
                mall_id_missing_count += 1
            elif expected_mall_id and mall_id != expected_mall_id:
                mall_id_mismatch_count += 1

            observed_items.append(
                {
                    "section": section,
                    "index": index,
                    "product_id": product_id,
                    "mall_id": mall_id,
                    "product_url": product_url,
                    "product_url_safe": product_url_safe,
                    "product_url_contains_product_id": product_url_contains_id,
                    "product_url_matches_expected_prefix": product_url_matches_expected_prefix,
                    "mall_id_matches_request": mall_id_matches_request,
                }
            )

    problems = []
    if product_url_missing_count:
        problems.append("product_url_missing_count")
    if product_url_unsafe_count:
        problems.append("product_url_unsafe_count")
    if product_url_product_id_mismatch_count:
        problems.append("product_url_product_id_mismatch_count")
    if product_url_prefix_mismatch_count:
        problems.append("product_url_prefix_mismatch_count")
    if mall_id_missing_count:
        problems.append("mall_id_missing_count")
    if mall_id_mismatch_count:
        problems.append("mall_id_mismatch_count")

    checked = len(observed_items)
    return {
        "ok": not problems,
        "expected_mall_id": expected_mall_id,
        "expected_product_url_prefix": expected_product_url_prefix,
        "checked": checked,
        "top_count": len(top),
        "item_count": len(items),
        "product_url_missing_count": product_url_missing_count,
        "product_url_unsafe_count": product_url_unsafe_count,
        "product_url_product_id_mismatch_count": product_url_product_id_mismatch_count,
        "product_url_prefix_mismatch_count": product_url_prefix_mismatch_count,
        "mall_id_missing_count": mall_id_missing_count,
        "mall_id_mismatch_count": mall_id_mismatch_count,
        "mall_ids": sorted({item["mall_id"] for item in observed_items if item["mall_id"]}),
        "product_ids": [item["product_id"] for item in observed_items if item["product_id"]],
        "observed_items": observed_items,
        "problems": problems,
    }


def summarize_result_contract(cases: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "checked": 0,
        "product_url_missing_count": 0,
        "product_url_unsafe_count": 0,
        "product_url_product_id_mismatch_count": 0,
        "product_url_prefix_mismatch_count": 0,
        "mall_id_missing_count": 0,
        "mall_id_mismatch_count": 0,
    }
    problems: list[str] = []
    failed_cases: list[str] = []
    cases_with_expected_product_url_prefix = 0
    missing_expected_product_url_prefix_cases: list[str] = []
    for index, case in enumerate(cases, start=1):
        case_name = str(case.get("name") or f"case_{index}")
        contract = case.get("result_contract")
        if not isinstance(contract, dict):
            problems.append(f"{case_name}.result_contract")
            failed_cases.append(case_name)
            continue
        if contract.get("ok") is not True:
            problems.append(f"{case_name}.result_contract.ok")
            failed_cases.append(case_name)
        if str(contract.get("expected_product_url_prefix") or "").strip():
            cases_with_expected_product_url_prefix += 1
        else:
            missing_expected_product_url_prefix_cases.append(case_name)
        for key in totals:
            totals[key] += parse_int_value(contract.get(key), 0)
        for key in [
            "product_url_missing_count",
            "product_url_unsafe_count",
            "product_url_product_id_mismatch_count",
            "product_url_prefix_mismatch_count",
            "mall_id_missing_count",
            "mall_id_mismatch_count",
        ]:
            if parse_int_value(contract.get(key), 0) != 0:
                problems.append(f"{case_name}.result_contract.{key}")

    if totals["checked"] <= 0:
        problems.append("checked")
    return {
        "ok": not problems,
        "case_count": len(cases),
        "failed_cases": sorted(set(failed_cases)),
        "cases_with_expected_product_url_prefix": cases_with_expected_product_url_prefix,
        "missing_expected_product_url_prefix_cases": missing_expected_product_url_prefix_cases,
        **totals,
        "problems": sorted(set(problems)),
    }


def summarize_operational_quality_mode(
    *,
    engine_name: str,
    source_is_local_only: bool,
    custom_cases: bool,
    case_source_fingerprint: dict[str, Any],
    skipped_case_checks: int,
    image_cases_with_file_source: int,
    mixed_cases_with_file_source: int,
    case_image_fingerprints: list[dict[str, Any]],
    mall_config: dict[str, Any],
    result_contract: dict[str, Any],
) -> dict[str, Any]:
    problems: list[str] = []
    expected_case_image_fingerprints = image_cases_with_file_source + mixed_cases_with_file_source
    case_image_fingerprint_problems = case_image_fingerprint_report_problems(
        case_image_fingerprints,
        expected_case_image_fingerprints,
    )
    if engine_name != "marqo":
        problems.append("engine")
    if source_is_local_only:
        problems.append("source")
    if not custom_cases:
        problems.append("custom_cases")
    if custom_cases and not fingerprint_report_ready(case_source_fingerprint):
        problems.append("case_source_fingerprint")
    if skipped_case_checks != 0:
        problems.append("skipped_case_checks")
    if image_cases_with_file_source < 1:
        problems.append("image_cases_with_file_source")
    if mixed_cases_with_file_source < 1:
        problems.append("mixed_cases_with_file_source")
    if case_image_fingerprint_problems:
        problems.append("case_image_fingerprints")
    if not isinstance(mall_config, dict) or mall_config.get("ok") is not True:
        problems.append("mall_config")
    if not str((mall_config or {}).get("expected_product_url_prefix") or "").strip():
        problems.append("product_url_prefix")
    if parse_int_value((result_contract or {}).get("cases_with_expected_product_url_prefix"), 0) < parse_int_value(
        (result_contract or {}).get("case_count"),
        0,
    ):
        problems.append("result_contract.product_url_prefix")
    if parse_int_value((result_contract or {}).get("product_url_prefix_mismatch_count"), 0) != 0:
        problems.append("result_contract.product_url_prefix_mismatch_count")
    return {
        "ok": not problems,
        "problems": problems,
        "requires_engine": "marqo",
        "requires_custom_cases": True,
        "requires_case_file_images": True,
        "requires_mall_config": True,
        "requires_product_url_prefix": True,
        "case_image_fingerprint_count": len(case_image_fingerprints),
        "case_image_fingerprint_problems": case_image_fingerprint_problems,
    }


def case_image_fingerprint_report_problems(value: Any, expected_count: int) -> list[str]:
    if expected_count <= 0:
        return []
    if not isinstance(value, list):
        return ["case_image_fingerprints"]
    problems: list[str] = []
    if len(value) < expected_count:
        problems.append("case_image_fingerprints.count")
    seen_paths: dict[str, str] = {}
    seen_digests: dict[str, str] = {}
    for index, entry in enumerate(value):
        prefix = f"case_image_fingerprints[{index}]"
        if not isinstance(entry, dict):
            problems.append(prefix)
            continue
        name = str(entry.get("name") or "").strip()
        if not str(entry.get("name") or "").strip():
            problems.append(prefix + ".name")
        if str(entry.get("query_type") or "") not in {"image", "text_image"}:
            problems.append(prefix + ".query_type")
        if entry.get("image_source") != "case_file":
            problems.append(prefix + ".image_source")
        fingerprint = entry.get("fingerprint")
        if not fingerprint_report_ready(fingerprint):
            problems.append(prefix + ".fingerprint")
            continue
        path = str(fingerprint.get("path") or "").strip()
        digest = str(fingerprint.get("digest") or "").strip().lower()
        if path:
            if path in seen_paths:
                problems.append(prefix + ".fingerprint.path_duplicate")
            else:
                seen_paths[path] = name
        if digest:
            if digest in seen_digests:
                problems.append(prefix + ".fingerprint.digest_duplicate")
            else:
                seen_digests[digest] = name
    return problems


def fingerprint_report_ready(value: dict[str, Any]) -> bool:
    digest = str((value or {}).get("digest") or "").strip().lower() if isinstance(value, dict) else ""
    return (
        isinstance(value, dict)
        and value.get("algorithm") == "sha256"
        and value.get("exists") is True
        and bool(str(value.get("path") or "").strip())
        and int(value.get("size_bytes") or 0) > 0
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    )


def parse_int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mall_config_report(mall_config_path: str | Path | None, mall_id: str) -> dict[str, Any]:
    path_text = str(mall_config_path or "").strip()
    fingerprint = file_fingerprint(path_text)
    if not path_text:
        return {
            "ok": False,
            "configured": False,
            "path": "",
            "fingerprint": fingerprint,
            "expected_mall_id": str(mall_id or "").strip(),
            "expected_product_url_prefix": "",
            "problems": ["path"],
        }
    try:
        malls = load_mall_configs(Path(path_text))
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "path": path_text,
            "fingerprint": fingerprint,
            "expected_mall_id": str(mall_id or "").strip(),
            "expected_product_url_prefix": "",
            "problems": ["load"],
            "error": str(exc),
        }
    expected_mall_id = str(mall_id or "").strip()
    mall = malls.get(expected_mall_id)
    problems = []
    prefix = ""
    if mall is None:
        problems.append("mall_id")
    elif not mall.enabled:
        problems.append("enabled")
    else:
        prefix = product_url_template_prefix(mall.product_url_template or "", mall.mall_id) or ""
        if not prefix:
            problems.append("product_url_template")
    return {
        "ok": not problems,
        "configured": True,
        "path": path_text,
        "fingerprint": fingerprint,
        "expected_mall_id": expected_mall_id,
        "enabled_mall_count": sum(1 for item in malls.values() if item.enabled),
        "expected_product_url_prefix": prefix,
        "problems": problems,
    }


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


def normalized_url_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    if scheme.lower() == "https":
        return 443
    if scheme.lower() == "http":
        return 80
    return None


def run_case(
    service: AISearchService,
    case: dict[str, Any],
    mall_id: str,
    default_image_base64: str,
    limit: int,
    dataset_product_ids: set[str] | None = None,
    expected_product_url_prefix: str = "",
) -> dict[str, Any]:
    payload = {"mall_id": mall_id, "limit": limit}
    query = dict(case["query"])
    if query.get("q"):
        payload["q"] = query["q"]
    image_source = None
    image_fingerprint = None
    if query.get("image"):
        image_payload, image_source = image_payload_for_case(case, default_image_base64)
        image_fingerprint = image_fingerprint_for_case(case, image_source)
        payload["image_base64"] = image_payload

    started = time.perf_counter()
    result = service.search(SearchRequest.model_validate(payload))
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    products = result.top + result.items
    categories = [item.category for item in products]
    checks = []
    result_contract = summarize_case_result_contract(
        expected_mall_id=mall_id,
        expected_product_url_prefix=expected_product_url_prefix,
        top=result.top,
        items=result.items,
    )

    expected_top = case.get("expected_top_product_id")
    if expected_top:
        if dataset_product_ids is not None and expected_top not in dataset_product_ids:
            checks.append(
                {
                    "name": "expected_top_product_id",
                    "ok": True,
                    "skipped": True,
                    "reason": "expected sample product is not present in this dataset",
                    "expected": expected_top,
                    "actual": None,
                }
            )
        else:
            checks.append(
                {
                    "name": "expected_top_product_id",
                    "ok": bool(result.top and result.top[0].product_id == expected_top),
                    "expected": expected_top,
                    "actual": result.top[0].product_id if result.top else None,
                }
            )

    expected_category = case.get("expected_category")
    if expected_category:
        top_categories = [item.category for item in result.top]
        suggested_categories = list(result.suggested_categories)
        top_category_ok = expected_category in top_categories
        suggested_category_ok = expected_category in suggested_categories
        checks.append(
            {
                "name": "expected_category",
                "ok": top_category_ok and suggested_category_ok,
                "expected": expected_category,
                "actual": {
                    "top": top_categories,
                    "suggested": suggested_categories[:5],
                    "all": categories[:5],
                },
                "top_category_ok": top_category_ok,
                "suggested_category_ok": suggested_category_ok,
            }
        )

    expected_min = int(case.get("expected_min_results", 1 if not expected_top and not expected_category else 0))
    if expected_min:
        checks.append(
            {
                "name": "expected_min_results",
                "ok": len(products) >= expected_min,
                "expected": expected_min,
                "actual": len(products),
            }
        )

    if "expected_low_confidence" in case:
        checks.append(
            {
                "name": "expected_low_confidence",
                "ok": result.meta.low_confidence is bool(case["expected_low_confidence"]),
                "expected": bool(case["expected_low_confidence"]),
                "actual": result.meta.low_confidence,
            }
        )

    return {
        "name": case["name"],
        "tags": case_tags(case),
        "ok": all(check["ok"] for check in checks) and result_contract["ok"],
        "query_type": result.meta.query_type.value,
        "elapsed_ms": elapsed_ms,
        "top_product_ids": [item.product_id for item in result.top],
        "suggested_categories": result.suggested_categories,
        "result_count": len(products),
        "top_score_percent": result.top[0].score_percent if result.top else None,
        "low_confidence": result.meta.low_confidence,
        "notice": result.meta.notice,
        "image_source": image_source,
        "image_fingerprint": image_fingerprint,
        "result_contract": result_contract,
        "checks": checks,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    csv_path = Path(args.csv)
    if getattr(args, "engine", "") == "marqo":
        try:
            args.marqo_url = validate_marqo_url_value(args.marqo_url, "--marqo-url")
        except ValueError as exc:
            return failed_quality_report(args, csv_path, str(exc))
    quality_cases, case_source = load_quality_cases(getattr(args, "cases", ""))
    case_source_fingerprint = file_fingerprint(case_source if case_source != "builtin" else "")
    mall_config = mall_config_report(getattr(args, "mall_config", ""), args.mall_id)
    expected_product_url_prefix = str(mall_config.get("expected_product_url_prefix") or "").strip()
    with tempfile.TemporaryDirectory() as temp_dir:
        service, products = make_service(csv_path, Path(temp_dir) / "quality-search.jsonl", args)
        image = validate_image_bytes(make_png_bytes(), max_bytes=5 * 1024 * 1024)
        image_base64 = base64.b64encode(base64.b64decode(image.data_url.split(",", 1)[1])).decode("ascii")
        product_ids = {product.product_id for product in products if product.product_id}
        cases = [
            run_case(
                service,
                case,
                args.mall_id,
                image_base64,
                args.limit,
                product_ids,
                expected_product_url_prefix,
            )
            for case in quality_cases
        ]
    dataset = summarize_dataset(products, args.min_products, args.recommended_products)
    thresholds = {
        "text": args.max_text_ms,
        "image": args.max_image_ms,
        "text_image": args.max_mixed_ms,
    }
    response_time = summarize_response_times(cases, thresholds)
    case_contract = summarize_case_contract(cases)
    result_contract = summarize_result_contract(cases)
    quality_ok = all(case["ok"] for case in cases)
    response_time_ok = response_time["ok"]
    dataset_ready = bool(dataset["meets_minimum_poc_size"])
    skipped_case_checks = sum(1 for case in cases for check in case["checks"] if check.get("skipped") is True)
    image_source_counts = dict(
        sorted(Counter(str(case.get("image_source") or "none") for case in cases if case.get("image_source")).items())
    )
    image_cases_with_supplied_source = sum(
        1 for case in cases if case.get("query_type") == "image" and str(case.get("image_source") or "").startswith("case_")
    )
    mixed_cases_with_supplied_source = sum(
        1
        for case in cases
        if case.get("query_type") == "text_image" and str(case.get("image_source") or "").startswith("case_")
    )
    image_cases_with_file_source = sum(
        1 for case in cases if case.get("query_type") == "image" and case.get("image_source") == "case_file"
    )
    mixed_cases_with_file_source = sum(
        1 for case in cases if case.get("query_type") == "text_image" and case.get("image_source") == "case_file"
    )
    case_image_fingerprints = [
        {
            "name": case.get("name"),
            "query_type": case.get("query_type"),
            "image_source": case.get("image_source"),
            "fingerprint": case.get("image_fingerprint"),
        }
        for case in cases
        if case.get("image_source") == "case_file"
    ]
    source_profile = builtin_sample_dataset_profile(csv_path, (product.product_id for product in products))
    engine_name = getattr(service.engine, "name", getattr(args, "engine", "local"))
    source_is_local_only = is_local_sample_evidence(source_profile) or engine_name != "marqo"
    custom_cases = case_source != "builtin"
    operational_quality = summarize_operational_quality_mode(
        engine_name=engine_name,
        source_is_local_only=source_is_local_only,
        custom_cases=custom_cases,
        case_source_fingerprint=case_source_fingerprint,
        skipped_case_checks=skipped_case_checks,
        image_cases_with_file_source=image_cases_with_file_source,
        mixed_cases_with_file_source=mixed_cases_with_file_source,
        case_image_fingerprints=case_image_fingerprints,
        mall_config=mall_config,
        result_contract=result_contract,
    )
    operational_quality_required = engine_name == "marqo"
    report_ok = (
        quality_ok
        and response_time_ok
        and case_contract["ok"]
        and result_contract["ok"]
        and (dataset_ready or not args.strict)
        and (operational_quality["ok"] if operational_quality_required else True)
    )
    not_operational_readiness = source_is_local_only or (
        operational_quality_required and operational_quality["ok"] is not True
    )
    source = {
        "engine": engine_name,
        **source_profile,
    }
    if engine_name == "marqo":
        source.update(
            {
                "marqo_url": getattr(args, "marqo_url", ""),
                "index_name": getattr(args, "index_name", ""),
                "marqo_model": getattr(args, "marqo_model", ""),
            }
        )
    return {
        "ok": report_ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "local_only": source_is_local_only,
        "not_operational_readiness": not_operational_readiness,
        "quality_ok": quality_ok,
        "response_time_ok": response_time_ok,
        "dataset_ready": dataset_ready,
        "strict": args.strict,
        "csv": str(csv_path),
        "csv_fingerprint": file_fingerprint(csv_path),
        "mall_id": args.mall_id,
        "case_source": case_source,
        "case_source_fingerprint": case_source_fingerprint,
        "mall_config": mall_config,
        "custom_cases": custom_cases,
        "case_count": len(cases),
        "skipped_case_checks": skipped_case_checks,
        "image_source_counts": image_source_counts,
        "image_cases_with_supplied_source": image_cases_with_supplied_source,
        "mixed_cases_with_supplied_source": mixed_cases_with_supplied_source,
        "image_cases_with_file_source": image_cases_with_file_source,
        "mixed_cases_with_file_source": mixed_cases_with_file_source,
        "case_image_fingerprints": case_image_fingerprints,
        "case_contract": case_contract,
        "result_contract": result_contract,
        "operational_quality": operational_quality,
        "source": source,
        "dataset": dataset,
        "response_time": response_time,
        "cases": cases,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Haeorum AI Search PoC Quality Report",
        "",
        f"- Overall OK: `{report['ok']}`",
        f"- Quality OK: `{report['quality_ok']}`",
        f"- Response Time OK: `{report['response_time_ok']}`",
        f"- Dataset Ready: `{report['dataset_ready']}`",
        f"- Local only: `{report.get('local_only')}`",
        f"- Not operational readiness: `{report.get('not_operational_readiness')}`",
        f"- CSV: `{report['csv']}`",
        f"- Case source: `{report.get('case_source')}`",
        f"- Mall config: `{(report.get('mall_config') or {}).get('path')}`",
        f"- Custom cases: `{report.get('custom_cases')}`",
        f"- Skipped case checks: `{report.get('skipped_case_checks')}`",
        f"- Image source counts: `{report.get('image_source_counts')}`",
        f"- Image cases with supplied source: `{report.get('image_cases_with_supplied_source')}`",
        f"- Mixed cases with supplied source: `{report.get('mixed_cases_with_supplied_source')}`",
        f"- Image cases with file source: `{report.get('image_cases_with_file_source')}`",
        f"- Mixed cases with file source: `{report.get('mixed_cases_with_file_source')}`",
        f"- Case image fingerprints: `{len(report.get('case_image_fingerprints') or [])}`",
        f"- Case contract OK: `{(report.get('case_contract') or {}).get('ok')}`",
        f"- Result contract OK: `{(report.get('result_contract') or {}).get('ok')}`",
        f"- Operational quality OK: `{(report.get('operational_quality') or {}).get('ok')}`",
        "",
        "## Dataset",
        "",
    ]
    dataset = report["dataset"]
    for key in [
        "total_products",
        "active_products",
        "inactive_products",
        "category_count",
        "minimum_poc_products",
        "recommended_poc_products",
        "meets_minimum_poc_size",
        "meets_recommended_poc_size",
        "missing_image_url_count",
    ]:
        lines.append(f"- {key}: `{dataset[key]}`")
    lines.extend(
        [
            "",
            "## Response Time",
            "",
            "| Type | Count | Avg ms | Max ms | Threshold ms | OK |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for query_type, summary in report["response_time"]["by_query_type"].items():
        lines.append(
            "| {query_type} | {count} | {avg_ms} | {max_ms} | {threshold_ms} | {ok} |".format(
                query_type=query_type,
                count=summary["count"],
                avg_ms=summary["avg_ms"],
                max_ms=summary["max_ms"],
                threshold_ms=summary["threshold_ms"],
                ok=summary["ok"],
            )
        )
    lines.extend(["", "## Cases", "", "| Case | OK | Type | ms | Top IDs | Categories |", "| --- | --- | --- | ---: | --- | --- |"])
    for case in report["cases"]:
        lines.append(
            "| {name} | {ok} | {query_type} | {elapsed_ms} | {top_ids} | {categories} |".format(
                name=case["name"],
                ok=case["ok"],
                query_type=case["query_type"],
                elapsed_ms=case["elapsed_ms"],
                top_ids=", ".join(case["top_product_ids"]),
                categories=", ".join(case["suggested_categories"][:5]),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a local PoC quality report for Haeorum AI Search.")
    parser.add_argument("--csv", default=str(ROOT / "sample_products.csv"))
    parser.add_argument("--mall-id", default="shop001")
    parser.add_argument("--engine", choices=sorted(DEPLOYABLE_SEARCH_ENGINES), default="local")
    parser.add_argument("--index-name", default="haeorum-products")
    parser.add_argument("--marqo-url", default="http://localhost:8882")
    parser.add_argument("--marqo-model", default=Settings.marqo_model)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--cases", default="", help="JSON quality cases file for production PoC evidence.")
    parser.add_argument("--mall-config", default="", help="Mall config JSON used to verify result product URL prefixes.")
    parser.add_argument("--min-products", type=int, default=300)
    parser.add_argument("--recommended-products", type=int, default=500)
    parser.add_argument("--max-text-ms", type=int, default=3000)
    parser.add_argument("--max-image-ms", type=int, default=5000)
    parser.add_argument("--max-mixed-ms", type=int, default=5000)
    parser.add_argument("--strict", action="store_true", help="Fail when dataset size is below --min-products.")
    parser.add_argument("--json-output", default="")
    parser.add_argument("--markdown-output", default="")
    args = parser.parse_args()

    report = build_report(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_output:
        Path(args.json_output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
