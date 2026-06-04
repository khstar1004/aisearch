from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_settings, validate_mssql_connection_string_value
from app.identifiers import product_identity_key, product_identity_label
from app.sync import (
    PRODUCT_FIELD_ALIASES,
    build_wrapped_mssql_query,
    normalize_external_field_name,
    parse_sync_datetime,
    row_to_product,
    validate_readonly_query,
)
from app.url_safety import (
    product_url_contains_product_id,
    safe_absolute_http_url,
    safe_absolute_http_url_uses_https,
    safe_product_source_url,
)

SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b((?:password|passwd|pwd|api[_-]?key|admin[_-]?key|token|secret)\s*[:=]\s*)([^;\s,&]+)",
    re.IGNORECASE,
)
URL_CREDENTIAL_PATTERN = re.compile(r"\b(https?://)([^/@\s]+)@", re.IGNORECASE)

REQUIRED_COLUMN_GROUPS = {
    "product_id": set(PRODUCT_FIELD_ALIASES["product_id"]),
    "product_name": set(PRODUCT_FIELD_ALIASES["product_name"]),
    "price": set(PRODUCT_FIELD_ALIASES["price"]),
    "price_min": set(PRODUCT_FIELD_ALIASES["price_min"]),
    "price_max": set(PRODUCT_FIELD_ALIASES["price_max"]),
    "category_name": set(PRODUCT_FIELD_ALIASES["category_name"]),
    "print_methods": set(PRODUCT_FIELD_ALIASES["print_methods"]),
    "materials": set(PRODUCT_FIELD_ALIASES["materials"]),
    "colors": set(PRODUCT_FIELD_ALIASES["colors"]),
    "min_order_qty": set(PRODUCT_FIELD_ALIASES["min_order_qty"]),
    "delivery_days": set(PRODUCT_FIELD_ALIASES["delivery_days"]),
    "main_image_url": set(PRODUCT_FIELD_ALIASES["main_image_url"]),
    "product_url": set(PRODUCT_FIELD_ALIASES["product_url"]),
    "status": set(PRODUCT_FIELD_ALIASES["status"]),
    "updated_at": set(PRODUCT_FIELD_ALIASES["updated_at"]),
    "is_deleted_or_display_yn": set(PRODUCT_FIELD_ALIASES["is_deleted"]) | set(PRODUCT_FIELD_ALIASES["display_yn"]),
    "mall_id": set(PRODUCT_FIELD_ALIASES["mall_id"]),
}
OPTIONAL_COLUMN_GROUPS = {
    "description": set(PRODUCT_FIELD_ALIASES["description"]),
    "keywords": set(PRODUCT_FIELD_ALIASES["keywords"]),
}


def canonical_output_aliases(canonical: str) -> tuple[str, ...]:
    if canonical == "is_deleted_or_display_yn":
        return ("is_deleted", "display_yn")
    return (canonical,)


def output_alias_for_matched_column(canonical: str, column: str) -> str:
    if canonical != "is_deleted_or_display_yn":
        return canonical
    normalized = normalize_external_field_name(column)
    display_aliases = {normalize_external_field_name(alias) for alias in PRODUCT_FIELD_ALIASES["display_yn"]}
    return "display_yn" if normalized in display_aliases else "is_deleted"


def quote_mssql_identifier(identifier: str) -> str:
    return "[" + str(identifier).replace("]", "]]") + "]"


def projection_expression(source_column: str, output_column: str) -> str:
    return f"{quote_mssql_identifier(source_column)} AS {output_column}"


def preferred_projection_entries(canonical: str, matched_columns: list[str]) -> list[dict[str, str]]:
    if not matched_columns:
        return []
    entries: list[dict[str, str]] = []
    for output_column in canonical_output_aliases(canonical):
        output_aliases = {normalize_external_field_name(alias) for alias in PRODUCT_FIELD_ALIASES[output_column]}
        matches = [
            column
            for column in matched_columns
            if normalize_external_field_name(column) in output_aliases
        ]
        if not matches:
            continue
        canonical_matches = [
            column
            for column in matches
            if normalize_external_field_name(column) == normalize_external_field_name(output_column)
        ]
        source_column = canonical_matches[0] if canonical_matches else matches[0]
        entries.append(
            {
                "source_column": source_column,
                "output_column": output_column,
                "expression": projection_expression(source_column, output_column),
            }
        )
    if entries:
        return entries
    source_column = matched_columns[0]
    output_column = output_alias_for_matched_column(canonical, source_column)
    return [
        {
            "source_column": source_column,
            "output_column": output_column,
            "expression": projection_expression(source_column, output_column),
        }
    ]


def column_group_match_report(
    columns: list[str],
    column_groups: dict[str, set[str]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    reports: dict[str, dict[str, Any]] = {}
    projection_entries: list[dict[str, str]] = []
    for canonical, aliases in column_groups.items():
        normalized_aliases = {normalize_external_field_name(alias) for alias in aliases}
        matched_columns = [
            column
            for column in columns
            if normalize_external_field_name(column) in normalized_aliases
        ]
        canonical_aliases = {
            normalize_external_field_name(output_alias)
            for output_alias in canonical_output_aliases(canonical)
        }
        canonical_column_present = any(
            normalize_external_field_name(column) in canonical_aliases for column in matched_columns
        )
        entries = preferred_projection_entries(canonical, matched_columns)
        projection_entries.extend(entries)
        selected_columns = {entry["source_column"] for entry in entries}
        reports[canonical] = {
            "present": bool(matched_columns),
            "matched_columns": matched_columns,
            "canonical_column_present": canonical_column_present,
            "recommended_output_columns": [entry["output_column"] for entry in entries],
            "needs_select_alias": any(
                normalize_external_field_name(entry["source_column"])
                != normalize_external_field_name(entry["output_column"])
                for entry in entries
            ),
            "ambiguous_columns": [column for column in matched_columns if column not in selected_columns],
        }
    return reports, projection_entries


def dedupe_projection_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry["source_column"], entry["output_column"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped
DANGEROUS_DATABASE_ROLES = {
    "db_accessadmin",
    "db_backupoperator",
    "db_datawriter",
    "db_ddladmin",
    "db_owner",
    "db_securityadmin",
}
DANGEROUS_DATABASE_PERMISSIONS = {
    "ADMINISTER DATABASE BULK OPERATIONS",
    "BACKUP DATABASE",
    "BACKUP LOG",
    "CHECKPOINT",
    "CONTROL",
    "DELETE",
    "EXECUTE",
    "IMPERSONATE",
    "INSERT",
    "TAKE OWNERSHIP",
    "UPDATE",
}
DANGEROUS_PERMISSION_PREFIXES = ("ALTER", "CREATE")
DATABASE_ROLE_QUERY = """
SELECT
    IS_ROLEMEMBER('db_datareader') AS db_datareader,
    IS_ROLEMEMBER('db_datawriter') AS db_datawriter,
    IS_ROLEMEMBER('db_owner') AS db_owner,
    IS_ROLEMEMBER('db_ddladmin') AS db_ddladmin,
    IS_ROLEMEMBER('db_securityadmin') AS db_securityadmin,
    IS_ROLEMEMBER('db_accessadmin') AS db_accessadmin,
    IS_ROLEMEMBER('db_backupoperator') AS db_backupoperator
"""
DATABASE_PERMISSION_QUERY = """
SELECT permission_name, CAST('GRANT' AS varchar(32)) AS state_desc
FROM fn_my_permissions(NULL, 'DATABASE')
ORDER BY permission_name
"""


def validate_columns(columns: list[str]) -> dict[str, Any]:
    required_matches, required_projection = column_group_match_report(columns, REQUIRED_COLUMN_GROUPS)
    optional_matches, optional_projection = column_group_match_report(columns, OPTIONAL_COLUMN_GROUPS)
    missing = sorted(
        canonical
        for canonical, report in required_matches.items()
        if not report["present"]
    )
    optional = sorted(
        canonical
        for canonical, report in optional_matches.items()
        if not report["present"]
    )
    projection_entries = dedupe_projection_entries(required_projection + optional_projection)
    noncanonical_required_aliases = {
        canonical: {
            "matched_columns": report["matched_columns"],
            "recommended_output_columns": report["recommended_output_columns"],
        }
        for canonical, report in required_matches.items()
        if report["present"] and report["needs_select_alias"]
    }
    ambiguous_required_aliases = {
        canonical: report["ambiguous_columns"]
        for canonical, report in required_matches.items()
        if report["ambiguous_columns"]
    }
    return {
        "ok": not missing,
        "columns": columns,
        "missing_required_columns": missing,
        "missing_optional_columns": optional,
        "matched_required_columns": required_matches,
        "matched_optional_columns": optional_matches,
        "noncanonical_required_aliases": noncanonical_required_aliases,
        "ambiguous_required_aliases": ambiguous_required_aliases,
        "suggested_select_aliases": projection_entries,
        "suggested_select_list": ",\n".join(f"    {entry['expression']}" for entry in projection_entries),
    }


MAX_FUTURE_UPDATED_AT_SKEW_SECONDS = 300


def domain_filter_coverage_report(products: list[Any], minimum_price_filterable_products: int = 300) -> dict[str, Any]:
    active = [product for product in products if product.active]
    price_filterable_ids = sorted(
        product.product_id
        for product in active
        if product.price is not None or product.price_min is not None or product.price_max is not None
    )
    price_range_ids = sorted(
        product.product_id for product in active if product.price_min is not None or product.price_max is not None
    )
    min_order_qty_ids = sorted(product.product_id for product in active if product.min_order_qty is not None)
    delivery_days_ids = sorted(product.product_id for product in active if product.delivery_days is not None)
    print_methods_ids = sorted(product.product_id for product in active if product.print_methods)
    materials_ids = sorted(product.product_id for product in active if product.materials)
    colors_ids = sorted(product.product_id for product in active if product.colors)
    required_price_count = min(max(1, int(minimum_price_filterable_products or 0)), len(active)) if active else 0
    problems: list[str] = []
    if active and len(price_filterable_ids) < required_price_count:
        problems.append("price_filterable_count")
    if active and not price_range_ids:
        problems.append("price_range_count")
    if active and not min_order_qty_ids:
        problems.append("min_order_qty_count")
    if active and not delivery_days_ids:
        problems.append("delivery_days_count")
    if active and not print_methods_ids:
        problems.append("print_methods_count")
    if active and not materials_ids:
        problems.append("materials_count")
    if active and not colors_ids:
        problems.append("colors_count")
    return {
        "ok": not problems,
        "active_products": len(active),
        "minimum_price_filterable_products": required_price_count,
        "price_filterable_count": len(price_filterable_ids),
        "price_range_count": len(price_range_ids),
        "min_order_qty_count": len(min_order_qty_ids),
        "delivery_days_count": len(delivery_days_ids),
        "print_methods_count": len(print_methods_ids),
        "materials_count": len(materials_ids),
        "colors_count": len(colors_ids),
        "price_filterable_product_ids": price_filterable_ids[:20],
        "price_range_product_ids": price_range_ids[:20],
        "min_order_qty_product_ids": min_order_qty_ids[:20],
        "delivery_days_product_ids": delivery_days_ids[:20],
        "print_methods_product_ids": print_methods_ids[:20],
        "materials_product_ids": materials_ids[:20],
        "colors_product_ids": colors_ids[:20],
        "problems": problems,
    }


def analyze_sample(
    rows: list[dict[str, Any]],
    now: datetime | None = None,
    max_future_skew_seconds: int = MAX_FUTURE_UPDATED_AT_SKEW_SECONDS,
) -> dict[str, Any]:
    parsed = []
    errors = []
    raw_product_ids = [str(row_value(row, *PRODUCT_FIELD_ALIASES["product_id"])).strip() for row in rows]
    raw_product_names = [str(row_value(row, *PRODUCT_FIELD_ALIASES["product_name"])).strip() for row in rows]
    for index, row in enumerate(rows, start=1):
        try:
            parsed.append(row_to_product(row))
        except Exception as exc:
            errors.append({"row": index, "message": str(exc)})
    active = [product for product in parsed if product.active]
    parsed_product_keys = [product_identity_key(product.mall_id, product.product_id) for product in parsed]
    duplicate_product_ids = sorted(
        product_identity_label(*key)
        for key, count in Counter(parsed_product_keys).items()
        if key[1] and count > 1
    )
    invalid_updated_at_rows = 0
    future_updated_at_ids = []
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    else:
        reference_now = reference_now.astimezone(timezone.utc)
    future_limit = reference_now + timedelta(seconds=max_future_skew_seconds)
    for product in parsed:
        if not product.updated_at:
            continue
        try:
            parsed_updated_at = parse_sync_datetime(product.updated_at, f"updated_at for product {product.product_id}")
        except ValueError:
            invalid_updated_at_rows += 1
            continue
        if parsed_updated_at > future_limit:
            future_updated_at_ids.append(product.product_id)
    return {
        "sample_rows": len(rows),
        "parsed_rows": len(parsed),
        "active_rows": len(active),
        "inactive_rows": len(parsed) - len(active),
        "parse_errors": errors,
        "missing_product_id_rows": sum(1 for product_id in raw_product_ids if not product_id),
        "missing_product_name_rows": sum(1 for product_name in raw_product_names if not product_name),
        "missing_image_url_rows": sum(1 for product in parsed if not product.image_url),
        "active_missing_category_rows": sum(1 for product in active if not product.category),
        "active_missing_image_url_rows": sum(1 for product in active if not product.image_url),
        "active_missing_product_url_rows": sum(1 for product in active if not product.product_url),
        "active_missing_mall_id_rows": sum(1 for product in active if not product.mall_id),
        "active_negative_price_rows": sum(
            1 for product in active if product.price is not None and float(product.price) < 0
        ),
        "active_unsafe_image_url_rows": sum(
            1 for product in active if product.image_url and safe_absolute_http_url(product.image_url) is None
        ),
        "active_non_https_image_url_rows": sum(
            1
            for product in active
            if product.image_url
            and safe_absolute_http_url(product.image_url) is not None
            and not safe_absolute_http_url_uses_https(product.image_url)
        ),
        "active_unsafe_product_url_rows": sum(
            1 for product in active if product.product_url and safe_product_source_url(product.product_url) is None
        ),
        "active_product_url_product_id_mismatch_rows": sum(
            1
            for product in active
            if product.product_url
            and safe_product_source_url(product.product_url) is not None
            and not product_url_contains_product_id(product.product_url, product.product_id)
        ),
        "active_product_url_product_id_mismatch_product_ids": sorted(
            product.product_id
            for product in active
            if product.product_url
            and safe_product_source_url(product.product_url) is not None
            and not product_url_contains_product_id(product.product_url, product.product_id)
        )[:20],
        "missing_updated_at_rows": sum(1 for product in parsed if not product.updated_at),
        "invalid_updated_at_rows": invalid_updated_at_rows,
        "future_updated_at_rows": len(future_updated_at_ids),
        "future_updated_at_product_ids": sorted(future_updated_at_ids)[:20],
        "updated_at_reference_now": reference_now.isoformat(),
        "updated_at_max_future_skew_seconds": max_future_skew_seconds,
        "duplicate_product_ids": duplicate_product_ids[:50],
        "sample_product_ids": [product.product_id for product in parsed[:10]],
        "domain_filter_coverage": domain_filter_coverage_report(parsed),
    }


def row_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    lower = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lower and lower[name.lower()] not in (None, ""):
            return lower[name.lower()]
    normalized = {normalize_external_field_name(key): value for key, value in row.items()}
    for name in names:
        key = normalize_external_field_name(name)
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return ""


def validate_sample_report(
    sample_report: dict[str, Any],
    *,
    require_domain_filter_coverage: bool = True,
) -> dict[str, Any]:
    problems = []
    if int(sample_report.get("sample_rows", 0) or 0) <= 0:
        problems.append("sample query returned no rows")
    if int(sample_report.get("parsed_rows", 0) or 0) <= 0:
        problems.append("sample query parsed no products")
    if int(sample_report.get("active_rows", 0) or 0) <= 0:
        problems.append("sample rows contain no active products")
    if sample_report.get("parse_errors"):
        problems.append("sample rows contain parse errors")
    missing_product_id = int(sample_report.get("missing_product_id_rows", 0) or 0)
    if missing_product_id > 0:
        problems.append(f"{missing_product_id} sample rows are missing product_id")
    missing_product_name = int(sample_report.get("missing_product_name_rows", 0) or 0)
    if missing_product_name > 0:
        problems.append(f"{missing_product_name} sample rows are missing product_name")
    missing_updated_at = int(sample_report.get("missing_updated_at_rows", 0) or 0)
    if missing_updated_at > 0:
        problems.append(f"{missing_updated_at} sample rows are missing updated_at")
    invalid_updated_at = int(sample_report.get("invalid_updated_at_rows", 0) or 0)
    if invalid_updated_at > 0:
        problems.append(f"{invalid_updated_at} sample rows have invalid updated_at")
    future_updated_at = int(sample_report.get("future_updated_at_rows", 0) or 0)
    if future_updated_at > 0:
        problems.append(f"{future_updated_at} sample rows have future updated_at")
    active_missing_image_url = int(sample_report.get("active_missing_image_url_rows", 0) or 0)
    active_missing_category = int(sample_report.get("active_missing_category_rows", 0) or 0)
    if active_missing_category > 0:
        problems.append(f"{active_missing_category} active sample rows are missing category_name")
    if active_missing_image_url > 0:
        problems.append(f"{active_missing_image_url} active sample rows are missing main_image_url")
    active_missing_product_url = int(sample_report.get("active_missing_product_url_rows", 0) or 0)
    if active_missing_product_url > 0:
        problems.append(f"{active_missing_product_url} active sample rows are missing product_url")
    active_missing_mall_id = int(sample_report.get("active_missing_mall_id_rows", 0) or 0)
    if active_missing_mall_id > 0:
        problems.append(f"{active_missing_mall_id} active sample rows are missing mall_id/site_id")
    active_unsafe_image_url = int(sample_report.get("active_unsafe_image_url_rows", 0) or 0)
    if active_unsafe_image_url > 0:
        problems.append(f"{active_unsafe_image_url} active sample rows have unsafe main_image_url")
    active_non_https_image_url = int(sample_report.get("active_non_https_image_url_rows", 0) or 0)
    if active_non_https_image_url > 0:
        problems.append(f"{active_non_https_image_url} active sample rows have non-HTTPS main_image_url")
    active_unsafe_product_url = int(sample_report.get("active_unsafe_product_url_rows", 0) or 0)
    if active_unsafe_product_url > 0:
        problems.append(f"{active_unsafe_product_url} active sample rows have unsafe product_url")
    active_product_url_product_id_mismatch = int(
        sample_report.get("active_product_url_product_id_mismatch_rows", 0) or 0
    )
    if active_product_url_product_id_mismatch > 0:
        problems.append(
            f"{active_product_url_product_id_mismatch} active sample rows have product_url that does not contain product_id"
        )
    active_negative_price = int(sample_report.get("active_negative_price_rows", 0) or 0)
    if active_negative_price > 0:
        problems.append(f"{active_negative_price} active sample rows have negative price")
    duplicate_product_ids = sample_report.get("duplicate_product_ids") or []
    if duplicate_product_ids:
        problems.append("sample rows contain duplicate product_id values")
    domain_filter_coverage = (
        sample_report.get("domain_filter_coverage")
        if isinstance(sample_report.get("domain_filter_coverage"), dict)
        else {}
    )
    if require_domain_filter_coverage and domain_filter_coverage.get("ok") is not True:
        for problem in domain_filter_coverage.get("problems") or ["domain_filter_coverage"]:
            problems.append(f"domain_filter_coverage: {problem}")
    return {
        "ok": not problems,
        "problems": problems,
    }


def analyze_readonly_permissions(
    role_membership: dict[str, Any],
    permission_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    roles = {str(name).strip().lower(): role_value(value) for name, value in role_membership.items()}
    permissions = [
        {
            "permission_name": str(row.get("permission_name") or "").strip().upper(),
            "state_desc": str(row.get("state_desc") or "").strip().upper(),
        }
        for row in permission_rows
        if str(row.get("permission_name") or "").strip()
    ]
    dangerous_roles = sorted(role for role in DANGEROUS_DATABASE_ROLES if roles.get(role) is True)
    dangerous_permissions = sorted(
        {
            permission["permission_name"]
            for permission in permissions
            if permission_state_grants_access(permission["state_desc"])
            and is_dangerous_database_permission(permission["permission_name"])
        }
    )
    return {
        "ok": not dangerous_roles and not dangerous_permissions,
        "checked": True,
        "roles": roles,
        "database_permissions": permissions,
        "dangerous_roles": dangerous_roles,
        "dangerous_permissions": dangerous_permissions,
        "select_permission_reported": any(
            permission["permission_name"] == "SELECT" and permission_state_grants_access(permission["state_desc"])
            for permission in permissions
        ),
    }


def role_value(value: Any) -> bool | None:
    if value is None:
        return None
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return True
        if text in {"0", "false", "no", "n"}:
            return False
    return None


def permission_state_grants_access(state_desc: str) -> bool:
    return str(state_desc or "").upper() in {"GRANT", "GRANT_WITH_GRANT_OPTION"}


def is_dangerous_database_permission(permission_name: str) -> bool:
    normalized = str(permission_name or "").strip().upper()
    return normalized in DANGEROUS_DATABASE_PERMISSIONS or normalized.startswith(DANGEROUS_PERMISSION_PREFIXES)


def rows_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def fetch_readonly_permissions(connection_string: str) -> dict[str, Any]:
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError("pyodbc is required for MSSQL permission checks") from exc
    with pyodbc.connect(connection_string, readonly=True, autocommit=True) as connection:
        cursor = connection.cursor()
        cursor.execute(DATABASE_ROLE_QUERY)
        role_rows = rows_from_cursor(cursor)
        cursor.execute(DATABASE_PERMISSION_QUERY)
        permission_rows = rows_from_cursor(cursor)
    return analyze_readonly_permissions(role_rows[0] if role_rows else {}, permission_rows)


def fetch_sample(connection_string: str, query: str, sample_size: int) -> tuple[list[str], list[dict[str, Any]]]:
    sql = build_sample_query(query, sample_size)
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError("pyodbc is required for MSSQL view checks") from exc
    with pyodbc.connect(connection_string, readonly=True, autocommit=True) as connection:
        cursor = connection.cursor()
        cursor.execute(sql)
        rows = rows_from_cursor(cursor)
        columns = list(rows[0].keys()) if rows else [column[0] for column in cursor.description]
    return columns, rows


def build_sample_query(query: str, sample_size: int) -> str:
    validate_readonly_query(query)
    if sample_size <= 0:
        raise ValueError("sample_size must be at least 1")
    return build_wrapped_mssql_query(query, top=sample_size, order_by=active_first_order_by(query))


def active_first_order_by(query: str) -> str | None:
    normalized = normalize_external_field_name(query)
    clauses: list[str] = []
    if "is_deleted" in normalized:
        clauses.append("CASE WHEN TRY_CONVERT(int, [is_deleted]) = 0 THEN 0 ELSE 1 END")
    if "display_yn" in normalized:
        clauses.append(
            "CASE WHEN [display_yn] IN ('Y', 'y', '1', 'true', 'TRUE', 'active', 'ACTIVE', NCHAR(49849) + NCHAR(51064)) "
            "THEN 0 ELSE 1 END"
        )
    if "status" in normalized:
        clauses.append(
            "CASE WHEN TRY_CONVERT(int, [status]) = 1 "
            "OR [status] IN ('Y', 'y', '1', 'true', 'TRUE', 'active', 'ACTIVE', NCHAR(49849) + NCHAR(51064)) "
            "THEN 0 ELSE 1 END"
        )
    if "updated_at" in normalized:
        clauses.append("[updated_at] DESC")
    return ", ".join(clauses) if clauses else None


def normalized_query_text(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().rstrip(";")).strip()


def query_fingerprint_report(query: str) -> dict[str, Any]:
    normalized = normalized_query_text(query)
    return {
        "algorithm": "sha256",
        "digest": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "normalized_length": len(normalized),
    }


def run(
    connection_string: str,
    query: str,
    sample_size: int,
    *,
    require_domain_filter_coverage: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    build_sample_query(query, sample_size)
    permission_report = fetch_readonly_permissions(connection_string)
    columns, rows = fetch_sample(connection_string, query, sample_size)
    column_report = validate_columns(columns)
    sample_report = analyze_sample(rows)
    sample_quality_report = validate_sample_report(
        sample_report,
        require_domain_filter_coverage=require_domain_filter_coverage,
    )
    return {
        "ok": column_report["ok"] and sample_quality_report["ok"] and permission_report["ok"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "query_configured": True,
        "domain_filter_coverage_required": require_domain_filter_coverage,
        "query_fingerprint": query_fingerprint_report(query),
        "sample_size": sample_size,
        "permission_report": permission_report,
        "column_report": column_report,
        "sample_report": sample_report,
        "sample_quality_report": sample_quality_report,
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
    sample_size: int = 0,
) -> dict[str, Any]:
    return {
        "ok": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "connection_string_configured": bool(str(connection_string or "").strip()),
        "query_configured": bool(str(query or "").strip()),
        "sample_size": sample_size,
        "error_type": type(error).__name__ if isinstance(error, Exception) else "RuntimeError",
        "error": sanitize_error_message(error, [connection_string]),
        "permission_report": {"ok": False, "checked": False, "skipped": True},
        "column_report": {"ok": False, "skipped": True},
        "sample_report": {"sample_rows": 0, "parsed_rows": 0, "skipped": True},
        "sample_quality_report": {"ok": False, "problems": ["MSSQL view check did not complete"]},
    }


def write_report(report: dict[str, Any], path: str | Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the MSSQL read-only AI search product view.")
    parser.add_argument(
        "--connection-string",
        default=os.environ.get("HAEORUM_MSSQL_READONLY_CONNECTION_STRING")
        or os.environ.get("HAEORUM_MSSQL_CONNECTION_STRING", ""),
    )
    parser.add_argument("--query", default=os.environ.get("HAEORUM_MSSQL_QUERY", ""))
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument(
        "--allow-missing-domain-filter-fields",
        action="store_true",
        help="Report, but do not fail on missing print/material/color/min-order/delivery filter coverage.",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    started = time.perf_counter()
    connection_string = args.connection_string
    query = args.query
    try:
        settings = load_settings()
        connection_string = args.connection_string or settings.mssql_connection_string
        query = args.query or settings.mssql_query
    except Exception as exc:
        report = failure_report(
            exc,
            started=started,
            connection_string=connection_string,
            query=query,
            sample_size=args.sample_size,
        )
        write_report(report, args.output)
        return 1
    if not connection_string:
        report = failure_report(
            "MSSQL connection string is required",
            started=started,
            connection_string=connection_string,
            query=query,
            sample_size=args.sample_size,
        )
        write_report(report, args.output)
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
            sample_size=args.sample_size,
        )
        write_report(report, args.output)
        return 2
    try:
        report = run(
            connection_string,
            query,
            args.sample_size,
            require_domain_filter_coverage=not args.allow_missing_domain_filter_fields,
        )
    except Exception as exc:
        report = failure_report(
            exc,
            started=started,
            connection_string=connection_string,
            query=query,
            sample_size=args.sample_size,
        )
    write_report(report, args.output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
