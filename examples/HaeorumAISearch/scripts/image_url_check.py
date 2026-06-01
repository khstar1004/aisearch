from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.image_probe import ProductImageProbe
from app.url_safety import safe_absolute_http_url, safe_absolute_http_url_uses_https
from scripts.sample_data_guard import builtin_sample_dataset_profile, file_fingerprint
from app.sync import CsvProductSource, make_product_source


BLOCKING_IMAGE_WARNING_TYPES = {"placeholder_or_sample_image"}


def run(args: argparse.Namespace) -> dict[str, object]:
    validate_args(args)
    settings = load_settings()
    source = CsvProductSource(Path(args.csv)) if args.csv else make_product_source(settings)
    active_products = [product for product in source.fetch_all() if product.active]
    missing_active_image_url_ids = sorted(product.product_id for product in active_products if not product.image_url)
    require_https = bool(getattr(args, "require_https", False))
    non_https_active_image_url_ids = sorted(
        product.product_id
        for product in active_products
        if product.image_url
        and safe_absolute_http_url(product.image_url) is not None
        and not safe_absolute_http_url_uses_https(product.image_url)
    )
    non_https_active_image_url_id_set = set(non_https_active_image_url_ids)
    products = active_products
    if require_https:
        products = [product for product in products if product.product_id not in non_https_active_image_url_id_set]
    if args.limit:
        products = products[: args.limit]
    source_profile = builtin_sample_dataset_profile(args.csv, [product.product_id for product in active_products])
    probe = ProductImageProbe(
        max_bytes=args.max_mb * 1024 * 1024,
        timeout_seconds=args.timeout_seconds,
        retry_count=args.retry_count,
        retry_delay_seconds=args.retry_delay_seconds,
        min_dimension=args.min_dimension,
    )
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(probe.validate, product) for product in products]
        for future in as_completed(futures):
            results.append(future.result())
    failures = [result for result in results if not result.ok]
    warnings = [result for result in results if result.ok and result.warnings]
    failure_category_counts = Counter(categorize_failure(result.message) for result in failures)
    warning_type_counts = Counter(warning for result in warnings for warning in result.warnings)
    blocking_warnings = [
        result
        for result in warnings
        if any(warning in BLOCKING_IMAGE_WARNING_TYPES for warning in result.warnings)
    ]
    blocking_warning_type_counts = Counter(
        warning
        for result in blocking_warnings
        for warning in result.warnings
        if warning in BLOCKING_IMAGE_WARNING_TYPES
    )
    attempts = [int(result.attempts or 1) for result in results]
    report = {
        "ok": len(failures) == 0 and not missing_active_image_url_ids and not blocking_warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "require_https": require_https,
        "csv": args.csv or "",
        "csv_fingerprint": file_fingerprint(args.csv) if args.csv else {},
        "source": source_profile,
        "active_products": len(active_products),
        "missing_active_image_url_count": len(missing_active_image_url_ids),
        "missing_active_image_url_product_ids": missing_active_image_url_ids[: args.max_failures],
        "non_https_active_image_url_count": len(non_https_active_image_url_ids),
        "non_https_active_image_url_product_ids": non_https_active_image_url_ids[: args.max_failures],
        "checked": len(results),
        "failed": len(failures),
        "warning_count": len(warnings),
        "failure_category_counts": dict(sorted(failure_category_counts.items())),
        "warning_type_counts": dict(sorted(warning_type_counts.items())),
        "blocking_warning_count": len(blocking_warnings),
        "blocking_warning_type_counts": dict(sorted(blocking_warning_type_counts.items())),
        "attempts": {
            "max": max(attempts) if attempts else 0,
            "retried_successes": sum(1 for result in results if result.ok and int(result.attempts or 1) > 1),
            "retried_failures": sum(1 for result in results if not result.ok and int(result.attempts or 1) > 1),
        },
        "limit": args.limit or None,
        "concurrency": args.concurrency,
        "timeout_seconds": args.timeout_seconds,
        "retry_count": args.retry_count,
        "retry_delay_seconds": args.retry_delay_seconds,
        "max_mb": args.max_mb,
        "min_dimension": args.min_dimension,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "failures": [
            {
                "product_id": failure.product_id,
                "image_url": failure.image_url,
                "message": failure.message,
                "category": categorize_failure(failure.message),
                "attempts": failure.attempts,
            }
            for failure in failures[: args.max_failures]
        ],
        "warnings": [
            {
                "product_id": warning.product_id,
                "image_url": warning.image_url,
                "warnings": list(warning.warnings),
                "attempts": warning.attempts,
            }
            for warning in warnings[: args.max_warnings]
        ],
        "blocking_warnings": [
            {
                "product_id": warning.product_id,
                "image_url": warning.image_url,
                "warnings": [
                    warning_type
                    for warning_type in warning.warnings
                    if warning_type in BLOCKING_IMAGE_WARNING_TYPES
                ],
                "attempts": warning.attempts,
            }
            for warning in blocking_warnings[: args.max_warnings]
        ],
    }
    report["ok"] = bool(report["ok"] and (not require_https or not non_https_active_image_url_ids))
    return report


def categorize_failure(message: str | None) -> str:
    lowered = str(message or "").lower()
    if not lowered:
        return "unknown"
    if "missing image url" in lowered:
        return "missing_image_url"
    if "safe http(s)" in lowered:
        return "unsafe_image_url"
    if "non-public address" in lowered or "non-public" in lowered:
        return "unsafe_image_url"
    if "http 4" in lowered:
        return "http_4xx"
    if "http 5" in lowered:
        return "http_5xx"
    if "image download failed" in lowered or "download failed" in lowered:
        return "download_failed"
    if "declared image type" in lowered and "does not match" in lowered:
        return "mime_mismatch"
    if "only jpg, png, and webp" in lowered:
        return "unsupported_image_type"
    if "damaged" in lowered or "cannot be decoded" in lowered:
        return "decode_failed"
    if "too small" in lowered or "minimum dimension" in lowered:
        return "too_small"
    if "exceeds" in lowered:
        return "too_large"
    if "empty" in lowered:
        return "empty_image"
    return "validation_failed"


def validate_args(args: argparse.Namespace) -> None:
    if args.limit < 0:
        raise ValueError("--limit must be zero or greater")
    if args.concurrency < 1:
        raise ValueError("--concurrency must be at least 1")
    if args.timeout_seconds < 1:
        raise ValueError("--timeout-seconds must be at least 1")
    if args.retry_count < 0:
        raise ValueError("--retry-count must be zero or greater")
    if args.retry_delay_seconds < 0:
        raise ValueError("--retry-delay-seconds must be zero or greater")
    if args.max_mb < 1:
        raise ValueError("--max-mb must be at least 1")
    if args.min_dimension < 1:
        raise ValueError("--min-dimension must be at least 1")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate product main_image_url values before indexing.")
    parser.add_argument("--csv", default="", help="Optional CSV path. Defaults to configured MSSQL/CSV source.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of active products to check.")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=10)
    parser.add_argument("--retry-count", type=int, default=1)
    parser.add_argument("--retry-delay-seconds", type=float, default=0.25)
    parser.add_argument("--require-https", action="store_true", help="Fail if active image URLs are HTTP instead of HTTPS.")
    parser.add_argument("--max-mb", type=int, default=5)
    parser.add_argument("--min-dimension", type=int, default=16)
    parser.add_argument("--max-failures", type=int, default=50)
    parser.add_argument("--max-warnings", type=int, default=50)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = run(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
