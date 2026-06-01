from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.identifiers import product_identity_key, product_identity_label
from app.models import ProductDocument
from app.sync import CsvProductSource
from app.url_safety import (
    product_url_contains_product_id,
    safe_absolute_http_url,
    safe_absolute_http_url_uses_https,
    safe_product_source_url,
)
from scripts.sample_data_guard import builtin_sample_dataset_profile, file_fingerprint, is_local_sample_evidence


RECOMMENDED_CATEGORIES = [
    "우산",
    "텀블러",
    "볼펜",
    "타올",
    "점착메모지",
    "상패",
    "달력",
    "가방",
    "마우스패드",
    "생활용품",
]
EXPORT_COLUMNS = [
    "product_id",
    "product_name",
    "category_name",
    "price",
    "price_min",
    "price_max",
    "print_methods",
    "materials",
    "colors",
    "min_order_qty",
    "delivery_days",
    "product_group_id",
    "main_image_url",
    "image_hash",
    "image_tags",
    "product_url",
    "status",
    "updated_at",
    "is_deleted",
    "display_yn",
    "mall_id",
    "description",
    "keywords",
]


def build_poc_dataset(
    products: list[ProductDocument],
    target_size: int = 300,
    min_products: int = 300,
    min_per_recommended_category: int = 10,
    recommended_categories: list[str] | None = None,
    source_csv: str | Path | None = None,
) -> tuple[list[ProductDocument], dict[str, Any]]:
    recommended = recommended_categories or RECOMMENDED_CATEGORIES
    active = [product for product in products if product.active]
    active.sort(key=lambda product: (product.category or "", product.product_id))
    selected = select_balanced(active, target_size, recommended)
    report = summarize(products, active, selected, target_size, min_products, min_per_recommended_category, recommended, source_csv)
    return selected, report


def select_balanced(
    products: list[ProductDocument],
    target_size: int,
    preferred_categories: list[str],
) -> list[ProductDocument]:
    grouped: dict[str, list[ProductDocument]] = defaultdict(list)
    for product in products:
        if not product.active:
            continue
        grouped[product.category or "(none)"].append(product)
    for group in grouped.values():
        group.sort(key=lambda product: (not bool(product.image_url), product.product_id))

    ordered_categories = [category for category in preferred_categories if category in grouped]
    ordered_categories.extend(category for category in sorted(grouped) if category not in set(ordered_categories))

    selected: list[ProductDocument] = []
    seen_ids: set[tuple[str, str]] = set()
    cursor = {category: 0 for category in ordered_categories}
    while len(selected) < target_size:
        added = False
        for category in ordered_categories:
            index = cursor[category]
            group = grouped[category]
            if index >= len(group):
                continue
            product = group[index]
            cursor[category] += 1
            identity = product_identity_key(product.mall_id, product.product_id)
            if identity in seen_ids:
                continue
            selected.append(product)
            seen_ids.add(identity)
            added = True
            if len(selected) >= target_size:
                break
        if not added:
            break
    return selected


def summarize(
    products: list[ProductDocument],
    active: list[ProductDocument],
    selected: list[ProductDocument],
    target_size: int,
    min_products: int,
    min_per_recommended_category: int,
    recommended_categories: list[str],
    source_csv: str | Path | None = None,
) -> dict[str, Any]:
    active_categories = Counter(product.category or "(none)" for product in active)
    selected_categories = Counter(product.category or "(none)" for product in selected)
    duplicate_ids = duplicates(product_identity_label(*product_identity_key(product.mall_id, product.product_id)) for product in products)
    missing_recommended = [category for category in recommended_categories if active_categories.get(category, 0) == 0]
    thin_recommended = [
        {
            "category": category,
            "active_products": active_categories.get(category, 0),
            "minimum": min_per_recommended_category,
        }
        for category in recommended_categories
        if 0 < active_categories.get(category, 0) < min_per_recommended_category
    ]
    selected_missing_recommended, selected_thin_recommended = selected_category_gaps(
        active_categories,
        selected_categories,
        recommended_categories,
        min_per_recommended_category,
    )
    selected_missing_images = [product.product_id for product in selected if not product.image_url]
    selected_unsafe_image_urls = [
        product.product_id
        for product in selected
        if product.image_url and safe_absolute_http_url(product.image_url) is None
    ]
    selected_non_https_image_urls = [
        product.product_id
        for product in selected
        if product.image_url
        and safe_absolute_http_url(product.image_url) is not None
        and not safe_absolute_http_url_uses_https(product.image_url)
    ]
    selected_missing_product_urls = [product.product_id for product in selected if not str(product.product_url or "").strip()]
    selected_missing_mall_ids = [product.product_id for product in selected if not str(product.mall_id or "").strip()]
    selected_unsafe_product_urls = [
        product.product_id
        for product in selected
        if product.product_url and safe_product_source_url(product.product_url) is None
    ]
    selected_product_url_product_id_mismatches = [
        product.product_id
        for product in selected
        if product.product_url
        and safe_product_source_url(product.product_url) is not None
        and not product_url_contains_product_id(product.product_url, product.product_id)
    ]
    ready = (
        len(selected) >= min_products
        and not duplicate_ids
        and not selected_missing_images
        and not selected_unsafe_image_urls
        and not selected_non_https_image_urls
        and not selected_missing_product_urls
        and not selected_missing_mall_ids
        and not selected_unsafe_product_urls
        and not selected_product_url_product_id_mismatches
        and not missing_recommended
        and not thin_recommended
        and not selected_missing_recommended
        and not selected_thin_recommended
    )
    source_profile = builtin_sample_dataset_profile(source_csv, (product.product_id for product in selected))
    local_sample = is_local_sample_evidence(source_profile)
    return {
        "ok": ready,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "local_only": local_sample,
        "not_operational_readiness": local_sample,
        "source": source_profile,
        "source_csv": str(source_csv or ""),
        "source_csv_fingerprint": file_fingerprint(source_csv),
        "target_size": target_size,
        "minimum_products": min_products,
        "minimum_per_recommended_category": min_per_recommended_category,
        "recommended_categories": recommended_categories,
        "total_products": len(products),
        "active_products": len(active),
        "inactive_products": len(products) - len(active),
        "selected_products": len(selected),
        "category_count": len(active_categories),
        "active_categories": dict(sorted(active_categories.items())),
        "selected_categories": dict(sorted(selected_categories.items())),
        "missing_recommended_categories": missing_recommended,
        "thin_recommended_categories": thin_recommended,
        "selected_missing_recommended_categories": selected_missing_recommended,
        "selected_thin_recommended_categories": selected_thin_recommended,
        "duplicate_product_ids": duplicate_ids[:20],
        "selected_missing_image_url_count": len(selected_missing_images),
        "selected_missing_image_url_product_ids": selected_missing_images[:20],
        "selected_unsafe_image_url_count": len(selected_unsafe_image_urls),
        "selected_unsafe_image_url_product_ids": selected_unsafe_image_urls[:20],
        "selected_non_https_image_url_count": len(selected_non_https_image_urls),
        "selected_non_https_image_url_product_ids": selected_non_https_image_urls[:20],
        "selected_missing_product_url_count": len(selected_missing_product_urls),
        "selected_missing_product_url_product_ids": selected_missing_product_urls[:20],
        "selected_missing_mall_id_count": len(selected_missing_mall_ids),
        "selected_missing_mall_id_product_ids": selected_missing_mall_ids[:20],
        "selected_unsafe_product_url_count": len(selected_unsafe_product_urls),
        "selected_unsafe_product_url_product_ids": selected_unsafe_product_urls[:20],
        "selected_product_url_product_id_mismatch_count": len(selected_product_url_product_id_mismatches),
        "selected_product_url_product_id_mismatch_product_ids": selected_product_url_product_id_mismatches[:20],
    }


def selected_category_gaps(
    active_categories: Counter,
    selected_categories: Counter,
    recommended_categories: list[str],
    minimum_per_category: int,
) -> tuple[list[str], list[dict[str, int | str]]]:
    missing = []
    thin = []
    for category in recommended_categories:
        active_count = int(active_categories.get(category, 0) or 0)
        if active_count <= 0:
            continue
        selected_count = int(selected_categories.get(category, 0) or 0)
        required = min(active_count, minimum_per_category)
        if selected_count <= 0:
            missing.append(category)
        elif selected_count < required:
            thin.append(
                {
                    "category": category,
                    "selected_products": selected_count,
                    "active_products": active_count,
                    "minimum": required,
                }
            )
    return missing, thin


def duplicates(values) -> list[str]:  # type: ignore[no-untyped-def]
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def write_products_csv(path: Path, products: list[ProductDocument]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for product in products:
            writer.writerow(product_to_row(product))


def product_to_row(product: ProductDocument) -> dict[str, Any]:
    return {
        "product_id": product.product_id,
        "product_name": product.name,
        "category_name": product.category,
        "price": product.price,
        "price_min": product.price_min,
        "price_max": product.price_max,
        "print_methods": ",".join(product.print_methods),
        "materials": ",".join(product.materials),
        "colors": ",".join(product.colors),
        "min_order_qty": product.min_order_qty,
        "delivery_days": product.delivery_days,
        "product_group_id": product.product_group_id,
        "main_image_url": product.image_url,
        "image_hash": product.extra.get("image_hash", ""),
        "image_tags": ",".join(product.extra.get("image_tags", [])),
        "product_url": product.product_url,
        "status": product.status,
        "updated_at": product.updated_at,
        "is_deleted": product.is_deleted,
        "display_yn": product.display_yn,
        "mall_id": product.mall_id,
        "description": product.description,
        "keywords": ",".join(product.keywords),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and validate a balanced PoC product CSV.")
    parser.add_argument("--csv", default=str(ROOT / "sample_products.csv"), help="Source product CSV.")
    parser.add_argument("--output-csv", default="", help="Where to write the selected PoC CSV.")
    parser.add_argument("--report-output", default="", help="Where to write the JSON report.")
    parser.add_argument("--target-size", type=int, default=300)
    parser.add_argument("--min-products", type=int, default=300)
    parser.add_argument("--min-per-category", type=int, default=10)
    parser.add_argument(
        "--recommended-categories",
        default=",".join(RECOMMENDED_CATEGORIES),
        help="Comma-separated recommended category names.",
    )
    args = parser.parse_args()

    recommended = [category.strip() for category in args.recommended_categories.split(",") if category.strip()]
    products = CsvProductSource(Path(args.csv)).fetch_all()
    selected, report = build_poc_dataset(
        products,
        target_size=args.target_size,
        min_products=args.min_products,
        min_per_recommended_category=args.min_per_category,
        recommended_categories=recommended,
        source_csv=args.csv,
    )
    if args.output_csv:
        write_products_csv(Path(args.output_csv), selected)
        report["output_csv"] = args.output_csv
        report["output_csv_fingerprint"] = file_fingerprint(args.output_csv)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report_output:
        Path(args.report_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_output).write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
