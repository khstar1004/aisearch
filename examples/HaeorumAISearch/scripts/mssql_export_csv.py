from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_settings, validate_mssql_connection_string_value, validate_sql_identifier_value
from app.identifiers import product_identity_key, product_identity_label
from app.models import ProductDocument
from app.sync import build_wrapped_mssql_query, mssql_sync_datetime_param, parse_sync_datetime, row_to_product
from app.url_safety import (
    product_url_contains_product_id,
    safe_absolute_http_url,
    safe_absolute_http_url_uses_https,
    safe_product_source_url,
)
from scripts.mall_config_check import validate_mall_config
from scripts.mssql_view_check import query_fingerprint_report, validate_columns
from scripts.poc_dataset_builder import EXPORT_COLUMNS, product_to_row
from scripts.representative_site_check import url_matches_prefix
from scripts.sample_data_guard import file_fingerprint

SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b((?:password|passwd|pwd|api[_-]?key|admin[_-]?key|token|secret)\s*[:=]\s*)([^;\s,&]+)",
    re.IGNORECASE,
)
URL_CREDENTIAL_PATTERN = re.compile(r"\b(https?://)([^/@\s]+)@", re.IGNORECASE)
DEFAULT_FETCH_SIZE = 1000


def build_export_query(
    query: str,
    limit: int = 0,
    since: str | None = None,
    product_id_column: str = "product_id",
    updated_at_column: str = "updated_at",
) -> tuple[str, list[Any]]:
    params: list[Any] = []
    filters = []
    product_id_column = validate_sql_identifier_value(product_id_column, "HAEORUM_MSSQL_PRODUCT_ID_COLUMN")
    updated_at_column = validate_sql_identifier_value(updated_at_column, "HAEORUM_MSSQL_UPDATED_AT_COLUMN")
    if since:
        filters.append(f"{updated_at_column} >= ?")
        params.append(mssql_sync_datetime_param(since, "since"))
    sql = build_wrapped_mssql_query(query, filters=filters, top=limit, order_by=product_id_column)
    return sql, params


def fetch_rows(
    connection_string: str,
    query: str,
    limit: int = 0,
    since: str | None = None,
    product_id_column: str = "product_id",
    updated_at_column: str = "updated_at",
    fetch_size: int = DEFAULT_FETCH_SIZE,
    row_callback: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError("pyodbc is required for MSSQL CSV export") from exc
    sql, params = build_export_query(
        query,
        limit=limit,
        since=since,
        product_id_column=product_id_column,
        updated_at_column=updated_at_column,
    )
    normalized_fetch_size = normalize_fetch_size(fetch_size)
    fetch_stats = {
        "fetch_size": normalized_fetch_size,
        "fetch_batches": 0,
        "max_fetch_batch_rows": 0,
        "rows_read": 0,
        "batched_fetch": True,
    }
    rows: list[dict[str, Any]] = []
    with pyodbc.connect(connection_string, readonly=True, autocommit=True) as connection:
        cursor = connection.cursor()
        try:
            cursor.arraysize = normalized_fetch_size
        except Exception:
            pass
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        while True:
            batch = cursor.fetchmany(normalized_fetch_size)
            if not batch:
                break
            batch_rows = [dict(zip(columns, row)) for row in batch]
            if row_callback is not None:
                row_callback(batch_rows)
            else:
                rows.extend(batch_rows)
            fetch_stats["fetch_batches"] += 1
            fetch_stats["max_fetch_batch_rows"] = max(fetch_stats["max_fetch_batch_rows"], len(batch_rows))
            fetch_stats["rows_read"] += len(batch_rows)
    fetch_rows.last_stats = fetch_stats
    return rows


def normalize_fetch_size(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FETCH_SIZE
    return parsed if parsed > 0 else DEFAULT_FETCH_SIZE


def current_fetch_stats(fetch_size: int, rows_read: int) -> dict[str, Any]:
    normalized_fetch_size = normalize_fetch_size(fetch_size)
    stats = getattr(fetch_rows, "last_stats", None)
    if (
        isinstance(stats, dict)
        and stats.get("fetch_size") == normalized_fetch_size
        and stats.get("rows_read") == rows_read
    ):
        return dict(stats)
    estimated_batches = (rows_read + normalized_fetch_size - 1) // normalized_fetch_size if rows_read else 0
    return {
        "fetch_size": normalized_fetch_size,
        "fetch_batches": estimated_batches,
        "max_fetch_batch_rows": min(normalized_fetch_size, rows_read) if rows_read else 0,
        "rows_read": rows_read,
        "batched_fetch": False,
    }


fetch_rows.last_stats = {
    "fetch_size": DEFAULT_FETCH_SIZE,
    "fetch_batches": 0,
    "max_fetch_batch_rows": 0,
    "rows_read": 0,
    "batched_fetch": True,
}


def parse_products(rows: list[dict[str, Any]]) -> tuple[list[ProductDocument], list[dict[str, Any]]]:
    return parse_product_rows(rows, start_index=1)


def parse_product_rows(
    rows: list[dict[str, Any]],
    *,
    start_index: int = 1,
) -> tuple[list[ProductDocument], list[dict[str, Any]]]:
    products = []
    errors = []
    for index, row in enumerate(rows, start=start_index):
        try:
            product = row_to_product(row)
            if not product.product_id.strip():
                raise ValueError("product_id is required")
            if not product.name.strip():
                raise ValueError("product_name is required")
            products.append(product)
        except Exception as exc:
            errors.append({"row": index, "message": str(exc), "product_id": row.get("product_id") or row.get("p_idx")})
    return products, errors


MAX_FUTURE_UPDATED_AT_SKEW_SECONDS = 300


def updated_at_quality(
    products: list[ProductDocument],
    now: datetime | None = None,
    max_future_skew_seconds: int = MAX_FUTURE_UPDATED_AT_SKEW_SECONDS,
) -> dict[str, Any]:
    missing = []
    invalid = []
    future = []
    parsed_values: list[datetime] = []
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    else:
        reference_now = reference_now.astimezone(timezone.utc)
    future_limit = reference_now + timedelta(seconds=max_future_skew_seconds)
    for product in products:
        if product.updated_at is None or (isinstance(product.updated_at, str) and not product.updated_at.strip()):
            missing.append(product.product_id)
            continue
        try:
            parsed = parse_sync_datetime(product.updated_at, f"updated_at for product {product.product_id}")
        except ValueError:
            invalid.append(product.product_id)
            continue
        parsed_values.append(parsed)
        if parsed > future_limit:
            future.append(product.product_id)
    return {
        "missing": sorted(missing),
        "invalid": sorted(invalid),
        "future": sorted(future),
        "min": min(parsed_values).isoformat() if parsed_values else None,
        "max": max(parsed_values).isoformat() if parsed_values else None,
        "reference_now": reference_now.isoformat(),
        "max_future_skew_seconds": max_future_skew_seconds,
    }


def mall_config_alignment_report(
    products: list[ProductDocument],
    mall_config_path: str | Path | None = None,
) -> dict[str, Any]:
    path_text = str(mall_config_path or "").strip()
    if not path_text:
        return {
            "ok": True,
            "checked": False,
            "mall_config": "",
            "problems": [],
            "active_products_checked": 0,
            "active_unknown_mall_id_count": 0,
            "active_unknown_mall_ids": [],
            "active_product_url_mismatch_count": 0,
            "active_product_url_mismatches": [],
        }

    path = Path(path_text)
    if not path.exists():
        return {
            "ok": False,
            "checked": True,
            "mall_config": path_text,
            "mall_config_ok": False,
            "problems": ["mall_config_missing"],
            "active_products_checked": 0,
            "active_unknown_mall_id_count": 0,
            "active_unknown_mall_ids": [],
            "active_product_url_mismatch_count": 0,
            "active_product_url_mismatches": [],
        }

    try:
        mall_report = validate_mall_config(path, min_count=1)
    except Exception as exc:
        return {
            "ok": False,
            "checked": True,
            "mall_config": path_text,
            "mall_config_ok": False,
            "error": sanitize_error_message(exc),
            "problems": ["mall_config_parse"],
            "active_products_checked": 0,
            "active_unknown_mall_id_count": 0,
            "active_unknown_mall_ids": [],
            "active_product_url_mismatch_count": 0,
            "active_product_url_mismatches": [],
        }

    enabled_mall_ids = {
        str(mall_id).strip()
        for mall_id in mall_report.get("enabled_mall_ids") or []
        if str(mall_id).strip()
    }
    product_url_prefixes = (
        mall_report.get("enabled_mall_product_url_prefixes")
        if isinstance(mall_report.get("enabled_mall_product_url_prefixes"), dict)
        else {}
    )
    active_products = [product for product in products if product.active]
    unknown_mall_counts: Counter[str] = Counter()
    product_url_mismatches: list[dict[str, str]] = []
    for product in active_products:
        mall_id = str(product.mall_id or "").strip()
        if not mall_id:
            continue
        if mall_id not in enabled_mall_ids:
            unknown_mall_counts[mall_id] += 1
            continue
        prefix = str(product_url_prefixes.get(mall_id) or "").strip().rstrip("/")
        product_url = str(product.product_url or "").strip()
        if prefix and product_url and not url_matches_prefix(product_url, prefix):
            product_url_mismatches.append(
                {
                    "product_id": product.product_id,
                    "mall_id": mall_id,
                    "product_url": product_url,
                    "expected_prefix": prefix,
                }
            )

    problems = []
    if mall_report.get("ok") is not True:
        problems.append("mall_config")
    if unknown_mall_counts:
        problems.append("active_unknown_mall_id_count")
    if product_url_mismatches:
        problems.append("active_product_url_mismatch_count")
    return {
        "ok": not problems,
        "checked": True,
        "mall_config": str(path),
        "mall_config_ok": mall_report.get("ok") is True,
        "enabled_mall_count": len(enabled_mall_ids),
        "active_products_checked": len(active_products),
        "active_unknown_mall_id_count": sum(unknown_mall_counts.values()),
        "active_unknown_mall_ids": [
            {"mall_id": mall_id, "count": count}
            for mall_id, count in sorted(unknown_mall_counts.items())[:20]
        ],
        "active_product_url_mismatch_count": len(product_url_mismatches),
        "active_product_url_mismatches": product_url_mismatches[:20],
        "problems": problems,
    }


SAMPLE_PRODUCT_ID_LIMIT = 20
DUPLICATE_PRODUCT_ID_LIMIT = 50


def append_sample(target: list[Any], value: Any, limit: int = SAMPLE_PRODUCT_ID_LIMIT) -> None:
    if len(target) < limit:
        target.append(value)


class StreamingProductCsvWriter:
    def __init__(self, path: Path):
        self.path = path
        self._file: Any | None = None
        self._writer: csv.DictWriter | None = None
        self.rows_written = 0

    def __enter__(self) -> "StreamingProductCsvWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=EXPORT_COLUMNS)
        self._writer.writeheader()
        return self

    def write(self, product: ProductDocument) -> None:
        if self._writer is None:
            raise RuntimeError("CSV writer is not open")
        self._writer.writerow(product_to_row(product))
        self.rows_written += 1

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
            self._writer = None


class MallConfigAlignmentAccumulator:
    def __init__(self, mall_config_path: str | Path | None = None):
        self.path_text = str(mall_config_path or "").strip()
        self.checked = bool(self.path_text)
        self.mall_config_ok = True
        self.enabled_mall_ids: set[str] = set()
        self.product_url_prefixes: dict[str, Any] = {}
        self.problems: list[str] = []
        self.error = ""
        self.active_products_checked = 0
        self.unknown_mall_counts: Counter[str] = Counter()
        self.product_url_mismatch_count = 0
        self.product_url_mismatches: list[dict[str, str]] = []
        if not self.path_text:
            return
        path = Path(self.path_text)
        if not path.exists():
            self.mall_config_ok = False
            self.problems.append("mall_config_missing")
            return
        try:
            mall_report = validate_mall_config(path, min_count=1)
        except Exception as exc:
            self.mall_config_ok = False
            self.error = sanitize_error_message(exc)
            self.problems.append("mall_config_parse")
            return
        self.mall_config_ok = mall_report.get("ok") is True
        self.enabled_mall_ids = {
            str(mall_id).strip()
            for mall_id in mall_report.get("enabled_mall_ids") or []
            if str(mall_id).strip()
        }
        self.product_url_prefixes = (
            mall_report.get("enabled_mall_product_url_prefixes")
            if isinstance(mall_report.get("enabled_mall_product_url_prefixes"), dict)
            else {}
        )
        if not self.mall_config_ok:
            self.problems.append("mall_config")

    def add_product(self, product: ProductDocument) -> None:
        if not self.checked or not product.active:
            return
        self.active_products_checked += 1
        mall_id = str(product.mall_id or "").strip()
        if not mall_id:
            return
        if mall_id not in self.enabled_mall_ids:
            self.unknown_mall_counts[mall_id] += 1
            return
        prefix = str(self.product_url_prefixes.get(mall_id) or "").strip().rstrip("/")
        product_url = str(product.product_url or "").strip()
        if prefix and product_url and not url_matches_prefix(product_url, prefix):
            self.product_url_mismatch_count += 1
            append_sample(
                self.product_url_mismatches,
                {
                    "product_id": product.product_id,
                    "mall_id": mall_id,
                    "product_url": product_url,
                    "expected_prefix": prefix,
                },
            )

    def report(self) -> dict[str, Any]:
        if not self.checked:
            return {
                "ok": True,
                "checked": False,
                "mall_config": "",
                "problems": [],
                "active_products_checked": 0,
                "active_unknown_mall_id_count": 0,
                "active_unknown_mall_ids": [],
                "active_product_url_mismatch_count": 0,
                "active_product_url_mismatches": [],
            }
        problems = list(self.problems)
        if self.unknown_mall_counts:
            problems.append("active_unknown_mall_id_count")
        if self.product_url_mismatch_count:
            problems.append("active_product_url_mismatch_count")
        report = {
            "ok": not problems,
            "checked": True,
            "mall_config": self.path_text,
            "mall_config_ok": self.mall_config_ok,
            "enabled_mall_count": len(self.enabled_mall_ids),
            "active_products_checked": self.active_products_checked,
            "active_unknown_mall_id_count": sum(self.unknown_mall_counts.values()),
            "active_unknown_mall_ids": [
                {"mall_id": mall_id, "count": count}
                for mall_id, count in sorted(self.unknown_mall_counts.items())[:SAMPLE_PRODUCT_ID_LIMIT]
            ],
            "active_product_url_mismatch_count": self.product_url_mismatch_count,
            "active_product_url_mismatches": self.product_url_mismatches,
            "problems": problems,
        }
        if self.error:
            report["error"] = self.error
        return report


class DomainFilterCoverageAccumulator:
    def __init__(self, minimum_price_filterable_products: int = 300):
        self.minimum_price_filterable_products = minimum_price_filterable_products
        self.active_products = 0
        self.price_filterable_count = 0
        self.price_range_count = 0
        self.min_order_qty_count = 0
        self.delivery_days_count = 0
        self.print_methods_count = 0
        self.materials_count = 0
        self.colors_count = 0
        self.price_filterable_product_ids: list[str] = []
        self.price_range_product_ids: list[str] = []
        self.min_order_qty_product_ids: list[str] = []
        self.delivery_days_product_ids: list[str] = []
        self.print_methods_product_ids: list[str] = []
        self.materials_product_ids: list[str] = []
        self.colors_product_ids: list[str] = []

    def add_product(self, product: ProductDocument) -> None:
        if not product.active:
            return
        self.active_products += 1
        if product.price is not None or product.price_min is not None or product.price_max is not None:
            self.price_filterable_count += 1
            append_sample(self.price_filterable_product_ids, product.product_id)
        if product.price_min is not None or product.price_max is not None:
            self.price_range_count += 1
            append_sample(self.price_range_product_ids, product.product_id)
        if product.min_order_qty is not None:
            self.min_order_qty_count += 1
            append_sample(self.min_order_qty_product_ids, product.product_id)
        if product.delivery_days is not None:
            self.delivery_days_count += 1
            append_sample(self.delivery_days_product_ids, product.product_id)
        if product.print_methods:
            self.print_methods_count += 1
            append_sample(self.print_methods_product_ids, product.product_id)
        if product.materials:
            self.materials_count += 1
            append_sample(self.materials_product_ids, product.product_id)
        if product.colors:
            self.colors_count += 1
            append_sample(self.colors_product_ids, product.product_id)

    def report(self) -> dict[str, Any]:
        required_price_count = (
            min(max(1, int(self.minimum_price_filterable_products or 0)), self.active_products)
            if self.active_products
            else 0
        )
        problems: list[str] = []
        if self.active_products and self.price_filterable_count < required_price_count:
            problems.append("price_filterable_count")
        if self.active_products and not self.price_range_count:
            problems.append("price_range_count")
        if self.active_products and not self.min_order_qty_count:
            problems.append("min_order_qty_count")
        if self.active_products and not self.delivery_days_count:
            problems.append("delivery_days_count")
        if self.active_products and not self.print_methods_count:
            problems.append("print_methods_count")
        if self.active_products and not self.materials_count:
            problems.append("materials_count")
        if self.active_products and not self.colors_count:
            problems.append("colors_count")
        return {
            "ok": not problems,
            "active_products": self.active_products,
            "minimum_price_filterable_products": required_price_count,
            "price_filterable_count": self.price_filterable_count,
            "price_range_count": self.price_range_count,
            "min_order_qty_count": self.min_order_qty_count,
            "delivery_days_count": self.delivery_days_count,
            "print_methods_count": self.print_methods_count,
            "materials_count": self.materials_count,
            "colors_count": self.colors_count,
            "price_filterable_product_ids": self.price_filterable_product_ids,
            "price_range_product_ids": self.price_range_product_ids,
            "min_order_qty_product_ids": self.min_order_qty_product_ids,
            "delivery_days_product_ids": self.delivery_days_product_ids,
            "print_methods_product_ids": self.print_methods_product_ids,
            "materials_product_ids": self.materials_product_ids,
            "colors_product_ids": self.colors_product_ids,
            "problems": problems,
        }


class ExportQualityAccumulator:
    def __init__(self, mall_config_path: str | Path = ""):
        self.mall_alignment = MallConfigAlignmentAccumulator(mall_config_path)
        self.domain_filter_coverage = DomainFilterCoverageAccumulator()
        self.product_key_counts: Counter[tuple[str, str]] = Counter()
        self.exported_products = 0
        self.active_products = 0
        self.inactive_products = 0
        self.source_deletion_signal_product_ids: list[str] = []
        self.parse_error_count = 0
        self.parse_errors: list[dict[str, Any]] = []
        self.missing_updated_at_count = 0
        self.invalid_updated_at_count = 0
        self.future_updated_at_count = 0
        self.missing_updated_at_product_ids: list[str] = []
        self.invalid_updated_at_product_ids: list[str] = []
        self.future_updated_at_product_ids: list[str] = []
        self.updated_at_min: datetime | None = None
        self.updated_at_max: datetime | None = None
        self.updated_at_reference_now = datetime.now(timezone.utc)
        self.active_missing_category_product_ids: list[str] = []
        self.active_missing_image_url_product_ids: list[str] = []
        self.active_missing_product_url_product_ids: list[str] = []
        self.active_missing_mall_id_product_ids: list[str] = []
        self.active_negative_price_product_ids: list[str] = []
        self.active_unsafe_image_url_product_ids: list[str] = []
        self.active_non_https_image_url_product_ids: list[str] = []
        self.active_unsafe_product_url_product_ids: list[str] = []
        self.active_product_url_product_id_mismatch_product_ids: list[str] = []
        self.active_missing_category_count = 0
        self.active_missing_image_url_count = 0
        self.active_missing_product_url_count = 0
        self.active_missing_mall_id_count = 0
        self.active_negative_price_count = 0
        self.active_unsafe_image_url_count = 0
        self.active_non_https_image_url_count = 0
        self.active_unsafe_product_url_count = 0
        self.active_product_url_product_id_mismatch_count = 0

    def add_parse_errors(self, errors: list[dict[str, Any]]) -> None:
        self.parse_error_count += len(errors)
        for error in errors:
            append_sample(self.parse_errors, error, limit=50)

    def add_product(self, product: ProductDocument) -> None:
        self.exported_products += 1
        self.product_key_counts[product_identity_key(product.mall_id, product.product_id)] += 1
        self._add_updated_at_quality(product)
        self.domain_filter_coverage.add_product(product)
        self.mall_alignment.add_product(product)
        if product.active:
            self.active_products += 1
            self._add_active_quality(product)
        else:
            self.inactive_products += 1
            append_sample(self.source_deletion_signal_product_ids, product.product_id)

    def _add_updated_at_quality(self, product: ProductDocument) -> None:
        if product.updated_at is None or (isinstance(product.updated_at, str) and not product.updated_at.strip()):
            self.missing_updated_at_count += 1
            append_sample(self.missing_updated_at_product_ids, product.product_id)
            return
        try:
            parsed = parse_sync_datetime(product.updated_at, f"updated_at for product {product.product_id}")
        except ValueError:
            self.invalid_updated_at_count += 1
            append_sample(self.invalid_updated_at_product_ids, product.product_id)
            return
        self.updated_at_min = parsed if self.updated_at_min is None else min(self.updated_at_min, parsed)
        self.updated_at_max = parsed if self.updated_at_max is None else max(self.updated_at_max, parsed)
        if parsed > self.updated_at_reference_now + timedelta(seconds=MAX_FUTURE_UPDATED_AT_SKEW_SECONDS):
            self.future_updated_at_count += 1
            append_sample(self.future_updated_at_product_ids, product.product_id)

    def _add_active_quality(self, product: ProductDocument) -> None:
        if not product.category:
            self.active_missing_category_count += 1
            append_sample(self.active_missing_category_product_ids, product.product_id)
        if not product.image_url:
            self.active_missing_image_url_count += 1
            append_sample(self.active_missing_image_url_product_ids, product.product_id)
        if not product.product_url:
            self.active_missing_product_url_count += 1
            append_sample(self.active_missing_product_url_product_ids, product.product_id)
        if not product.mall_id:
            self.active_missing_mall_id_count += 1
            append_sample(self.active_missing_mall_id_product_ids, product.product_id)
        if product.price is not None and float(product.price) < 0:
            self.active_negative_price_count += 1
            append_sample(self.active_negative_price_product_ids, product.product_id)
        safe_image_url = safe_absolute_http_url(product.image_url) if product.image_url else None
        if product.image_url and safe_image_url is None:
            self.active_unsafe_image_url_count += 1
            append_sample(self.active_unsafe_image_url_product_ids, product.product_id)
        if product.image_url and safe_image_url is not None and not safe_absolute_http_url_uses_https(product.image_url):
            self.active_non_https_image_url_count += 1
            append_sample(self.active_non_https_image_url_product_ids, product.product_id)
        safe_product_url = safe_product_source_url(product.product_url) if product.product_url else None
        if product.product_url and safe_product_url is None:
            self.active_unsafe_product_url_count += 1
            append_sample(self.active_unsafe_product_url_product_ids, product.product_id)
        if (
            product.product_url
            and safe_product_url is not None
            and not product_url_contains_product_id(product.product_url, product.product_id)
        ):
            self.active_product_url_product_id_mismatch_count += 1
            append_sample(self.active_product_url_product_id_mismatch_product_ids, product.product_id)

    def duplicate_product_ids(self) -> list[str]:
        return sorted(
            product_identity_label(*key)
            for key, count in self.product_key_counts.items()
            if key[1] and count > 1
        )

    def report_fields(self) -> dict[str, Any]:
        duplicate_product_ids = self.duplicate_product_ids()
        mall_alignment = self.mall_alignment.report()
        domain_filter_coverage = self.domain_filter_coverage.report()
        return {
            "mall_config_alignment": mall_alignment,
            "domain_filter_coverage": domain_filter_coverage,
            "active_unknown_mall_id_count": mall_alignment["active_unknown_mall_id_count"],
            "active_unknown_mall_ids": mall_alignment["active_unknown_mall_ids"],
            "active_product_url_mismatch_count": mall_alignment["active_product_url_mismatch_count"],
            "active_product_url_mismatches": mall_alignment["active_product_url_mismatches"],
            "exported_products": self.exported_products,
            "active_products": self.active_products,
            "inactive_products": self.inactive_products,
            "source_deletion_signal_ok": self.inactive_products > 0,
            "source_deletion_signal_count": self.inactive_products,
            "source_deletion_signal_product_ids": self.source_deletion_signal_product_ids,
            "duplicate_product_ids": duplicate_product_ids[:DUPLICATE_PRODUCT_ID_LIMIT],
            "missing_updated_at_count": self.missing_updated_at_count,
            "missing_updated_at_product_ids": self.missing_updated_at_product_ids,
            "invalid_updated_at_count": self.invalid_updated_at_count,
            "invalid_updated_at_product_ids": self.invalid_updated_at_product_ids,
            "future_updated_at_count": self.future_updated_at_count,
            "future_updated_at_product_ids": self.future_updated_at_product_ids,
            "updated_at_min": self.updated_at_min.isoformat() if self.updated_at_min else None,
            "updated_at_max": self.updated_at_max.isoformat() if self.updated_at_max else None,
            "updated_at_reference_now": self.updated_at_reference_now.isoformat(),
            "updated_at_max_future_skew_seconds": MAX_FUTURE_UPDATED_AT_SKEW_SECONDS,
            "active_missing_category_count": self.active_missing_category_count,
            "active_missing_category_product_ids": self.active_missing_category_product_ids,
            "active_missing_image_url_count": self.active_missing_image_url_count,
            "active_missing_image_url_product_ids": self.active_missing_image_url_product_ids,
            "active_missing_product_url_count": self.active_missing_product_url_count,
            "active_missing_product_url_product_ids": self.active_missing_product_url_product_ids,
            "active_missing_mall_id_count": self.active_missing_mall_id_count,
            "active_missing_mall_id_product_ids": self.active_missing_mall_id_product_ids,
            "active_negative_price_count": self.active_negative_price_count,
            "active_negative_price_product_ids": self.active_negative_price_product_ids,
            "active_unsafe_image_url_count": self.active_unsafe_image_url_count,
            "active_unsafe_image_url_product_ids": self.active_unsafe_image_url_product_ids,
            "active_non_https_image_url_count": self.active_non_https_image_url_count,
            "active_non_https_image_url_product_ids": self.active_non_https_image_url_product_ids,
            "active_unsafe_product_url_count": self.active_unsafe_product_url_count,
            "active_unsafe_product_url_product_ids": self.active_unsafe_product_url_product_ids,
            "active_product_url_product_id_mismatch_count": self.active_product_url_product_id_mismatch_count,
            "active_product_url_product_id_mismatch_product_ids": (
                self.active_product_url_product_id_mismatch_product_ids
            ),
            "parse_error_count": self.parse_error_count,
            "parse_errors": self.parse_errors,
            "streamed_product_csv": True,
            "retained_product_rows": 0,
        }

    def ok(self, column_report: dict[str, Any]) -> bool:
        fields = self.report_fields()
        return (
            self.parse_error_count == 0
            and not fields["duplicate_product_ids"]
            and fields["missing_updated_at_count"] == 0
            and fields["invalid_updated_at_count"] == 0
            and fields["future_updated_at_count"] == 0
            and fields["active_missing_category_count"] == 0
            and fields["active_missing_image_url_count"] == 0
            and fields["active_missing_product_url_count"] == 0
            and fields["active_missing_mall_id_count"] == 0
            and fields["active_negative_price_count"] == 0
            and fields["active_unsafe_image_url_count"] == 0
            and fields["active_non_https_image_url_count"] == 0
            and fields["active_unsafe_product_url_count"] == 0
            and fields["active_product_url_product_id_mismatch_count"] == 0
            and fields["mall_config_alignment"]["ok"]
            and fields["domain_filter_coverage"]["ok"]
            and fields["source_deletion_signal_ok"]
            and column_report["ok"]
        )


def export_csv(
    connection_string: str,
    query: str,
    output_csv: Path,
    limit: int = 0,
    since: str | None = None,
    product_id_column: str = "product_id",
    updated_at_column: str = "updated_at",
    mall_config_path: str | Path = "",
    fetch_size: int = DEFAULT_FETCH_SIZE,
) -> dict[str, Any]:
    started = time.perf_counter()
    product_id_column = validate_sql_identifier_value(product_id_column, "HAEORUM_MSSQL_PRODUCT_ID_COLUMN")
    updated_at_column = validate_sql_identifier_value(updated_at_column, "HAEORUM_MSSQL_UPDATED_AT_COLUMN")
    normalized_fetch_size = normalize_fetch_size(fetch_size)
    try:
        fetch_rows.last_stats = {}
    except Exception:
        pass
    source_columns: list[str] = []
    quality = ExportQualityAccumulator(mall_config_path)
    rows_read = 0
    streaming_parse = True

    def consume_rows(batch_rows: list[dict[str, Any]], csv_writer: StreamingProductCsvWriter) -> None:
        nonlocal rows_read, source_columns
        if not batch_rows:
            return
        if not source_columns:
            source_columns = list(batch_rows[0].keys())
        batch_products, batch_errors = parse_product_rows(batch_rows, start_index=rows_read + 1)
        quality.add_parse_errors(batch_errors)
        for product in batch_products:
            csv_writer.write(product)
            quality.add_product(product)
        rows_read += len(batch_rows)

    with StreamingProductCsvWriter(output_csv) as csv_writer:
        rows = fetch_rows(
            connection_string,
            query,
            limit=limit,
            since=since,
            product_id_column=product_id_column,
            updated_at_column=updated_at_column,
            fetch_size=normalized_fetch_size,
            row_callback=lambda batch_rows: consume_rows(batch_rows, csv_writer),
        )
        if rows:
            streaming_parse = False
            consume_rows(rows, csv_writer)
        csv_rows_written = csv_writer.rows_written
    fetch_stats = current_fetch_stats(normalized_fetch_size, rows_read)
    column_report = validate_columns(source_columns)
    quality_fields = quality.report_fields()
    report_ok = (
        quality.parse_error_count == 0
        and not quality_fields["duplicate_product_ids"]
        and quality_fields["missing_updated_at_count"] == 0
        and quality_fields["invalid_updated_at_count"] == 0
        and quality_fields["future_updated_at_count"] == 0
        and quality_fields["active_missing_category_count"] == 0
        and quality_fields["active_missing_image_url_count"] == 0
        and quality_fields["active_missing_product_url_count"] == 0
        and quality_fields["active_missing_mall_id_count"] == 0
        and quality_fields["active_negative_price_count"] == 0
        and quality_fields["active_unsafe_image_url_count"] == 0
        and quality_fields["active_non_https_image_url_count"] == 0
        and quality_fields["active_unsafe_product_url_count"] == 0
        and quality_fields["active_product_url_product_id_mismatch_count"] == 0
        and quality_fields["mall_config_alignment"]["ok"]
        and quality_fields["domain_filter_coverage"]["ok"]
        and quality_fields["source_deletion_signal_ok"]
        and column_report["ok"]
    )
    return {
        "ok": report_ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "query_configured": True,
        "query_fingerprint": query_fingerprint_report(query),
        "source_columns": source_columns,
        "column_report": column_report,
        "limit": limit,
        "since_configured": bool(str(since or "").strip()),
        "output_csv": str(output_csv),
        "output_csv_fingerprint": file_fingerprint(output_csv),
        "product_id_column": product_id_column,
        "updated_at_column": updated_at_column,
        "fetch_size": fetch_stats["fetch_size"],
        "fetch_batches": fetch_stats["fetch_batches"],
        "max_fetch_batch_rows": fetch_stats["max_fetch_batch_rows"],
        "batched_fetch": fetch_stats["batched_fetch"],
        "streaming_parse": streaming_parse,
        "rows_read": rows_read,
        "csv_rows_written": csv_rows_written,
        **quality_fields,
    }


def sanitize_error_message(error: Exception | str, sensitive_values: list[str] | None = None) -> str:
    text = str(error)
    for value in sorted({str(item) for item in sensitive_values or [] if len(str(item)) >= 4}, key=len, reverse=True):
        text = text.replace(value, "***")
    text = SECRET_ASSIGNMENT_PATTERN.sub(lambda match: match.group(1) + "***", text)
    text = URL_CREDENTIAL_PATTERN.sub(lambda match: match.group(1) + "[redacted]@", text)
    return text


def failure_report(
    error: Exception | str,
    *,
    started: float,
    connection_string: str = "",
    query: str = "",
    output_csv: str | Path = "",
    product_id_column: str = "",
    updated_at_column: str = "",
    mall_config_path: str | Path = "",
    fetch_size: int = DEFAULT_FETCH_SIZE,
) -> dict[str, Any]:
    return {
        "ok": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "output_csv": str(output_csv) if output_csv else "",
        "product_id_column": product_id_column,
        "updated_at_column": updated_at_column,
        "fetch_size": normalize_fetch_size(fetch_size),
        "mall_config_path": str(mall_config_path) if mall_config_path else "",
        "connection_string_configured": bool(str(connection_string or "").strip()),
        "query_configured": bool(str(query or "").strip()),
        "error_type": type(error).__name__ if isinstance(error, Exception) else "RuntimeError",
        "error": sanitize_error_message(error, [connection_string]),
    }


def write_report(report: dict[str, Any], path: str | Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the read-only MSSQL AI search View to a normalized CSV.")
    parser.add_argument(
        "--connection-string",
        default=os.environ.get("HAEORUM_MSSQL_READONLY_CONNECTION_STRING")
        or os.environ.get("HAEORUM_MSSQL_CONNECTION_STRING", ""),
    )
    parser.add_argument("--query", default=os.environ.get("HAEORUM_MSSQL_QUERY", ""))
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--limit", type=int, default=0, help="Optional TOP (N) export limit. 0 exports all rows.")
    parser.add_argument("--since", default=None, help="Optional updated_at lower bound.")
    parser.add_argument("--product-id-column", default=os.environ.get("HAEORUM_MSSQL_PRODUCT_ID_COLUMN", ""))
    parser.add_argument("--updated-at-column", default=os.environ.get("HAEORUM_MSSQL_UPDATED_AT_COLUMN", ""))
    parser.add_argument(
        "--fetch-size",
        type=int,
        default=normalize_fetch_size(os.environ.get("HAEORUM_MSSQL_FETCH_SIZE", DEFAULT_FETCH_SIZE)),
        help="Rows to fetch from MSSQL per ODBC batch. Increase cautiously for high-latency links.",
    )
    parser.add_argument("--mall-config", default=os.environ.get("HAEORUM_MALL_CONFIG_PATH", ""))
    parser.add_argument("--report-output", default="")
    args = parser.parse_args()

    started = time.perf_counter()
    connection_string = args.connection_string
    query = args.query
    product_id_column = args.product_id_column
    updated_at_column = args.updated_at_column
    mall_config_path = args.mall_config
    try:
        settings = load_settings()
        connection_string = args.connection_string or settings.mssql_connection_string
        query = args.query or settings.mssql_query
        product_id_column = args.product_id_column or settings.mssql_product_id_column
        updated_at_column = args.updated_at_column or settings.mssql_updated_at_column
        mall_config_path = args.mall_config or (str(settings.mall_config_path) if settings.mall_config_path else "")
    except Exception as exc:
        report = failure_report(
            exc,
            started=started,
            connection_string=connection_string,
            query=query,
            output_csv=args.output_csv,
            product_id_column=product_id_column,
            updated_at_column=updated_at_column,
            mall_config_path=mall_config_path,
            fetch_size=args.fetch_size,
        )
        write_report(report, args.report_output)
        return 1
    if not connection_string:
        report = failure_report(
            "MSSQL connection string is required",
            started=started,
            connection_string=connection_string,
            query=query,
            output_csv=args.output_csv,
            product_id_column=product_id_column,
            updated_at_column=updated_at_column,
            mall_config_path=mall_config_path,
            fetch_size=args.fetch_size,
        )
        write_report(report, args.report_output)
        return 2
    try:
        validate_mssql_connection_string_value(
            connection_string,
            "HAEORUM_MSSQL_READONLY_CONNECTION_STRING",
            allow_trust_server_certificate=str(
                os.environ.get("HAEORUM_MSSQL_ALLOW_TRUST_SERVER_CERTIFICATE", "")
            ).strip().lower()
            in {"1", "true", "yes", "y", "on"},
        )
    except ValueError as exc:
        report = failure_report(
            exc,
            started=started,
            connection_string=connection_string,
            query=query,
            output_csv=args.output_csv,
            product_id_column=product_id_column,
            updated_at_column=updated_at_column,
            mall_config_path=mall_config_path,
            fetch_size=args.fetch_size,
        )
        write_report(report, args.report_output)
        return 2
    try:
        report = export_csv(
            connection_string,
            query,
            Path(args.output_csv),
            limit=args.limit,
            since=args.since,
            product_id_column=product_id_column,
            updated_at_column=updated_at_column,
            mall_config_path=mall_config_path,
            fetch_size=args.fetch_size,
        )
    except Exception as exc:
        report = failure_report(
            exc,
            started=started,
            connection_string=connection_string,
            query=query,
            output_csv=args.output_csv,
            product_id_column=product_id_column,
            updated_at_column=updated_at_column,
            mall_config_path=mall_config_path,
            fetch_size=args.fetch_size,
        )
    write_report(report, args.report_output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
