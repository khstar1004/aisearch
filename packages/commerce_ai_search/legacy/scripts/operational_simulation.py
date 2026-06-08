from __future__ import annotations

import argparse
import base64
import copy
import csv
import hashlib
import html
import json
import math
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.cache import MemorySearchCache
from app.category_intent import infer_category_intents
from app.concurrency import SearchExecutionGate, SearchQueueFull
from app.config import (
    MAX_OPERATIONAL_SEARCH_OFFSET,
    MallConfig,
    Settings,
    load_mall_configs,
    load_settings,
    validate_settings,
)
from app.engine import (
    BackendConnectionPoolTimeoutError,
    BackendJsonHttpClient,
    EngineHit,
    EngineQuery,
    LocalSearchEngine,
    MARQO_MAX_SEARCH_CANDIDATES,
    MARQO_TEXT_RERANK_MIN_CANDIDATES,
    MarqoSearchEngine,
    QWEN_IMAGE_VECTOR_FIELD,
    SEARCH_TEXT_FIELD,
    json_body_size,
)
from app.identifiers import product_document_id, product_identity_key, product_identity_label
from app.image_probe import SafeImageRedirectHandler, UnsafeImageHostError, UnsafeImageRedirectError
from app.image_probe import validate_image_url_resolves_to_public_network
from app.image_validation import validate_image_bytes, validate_multipart_content_length
from app.models import MAX_PRODUCT_ID_LENGTH, ProductDocument, SearchRequest
from app.query_normalizer import build_search_query, normalize_query_spacing, normalize_query_text
from app.rate_limit import RateLimitBucketStore, record_rate_limit_hit
from app.security import mall_access_index, public_header_access_index, validate_mall_access, validate_public_header_access
import app.engine as engine_module
import app.search_service as search_service_module
from app.search_service import AISearchService, SearchCacheMissInFlight, SearchLogger
from app.sync import CsvProductSource, SyncService, parse_sync_datetime
from app.url_safety import (
    normalize_public_http_base_url,
    normalize_public_http_origin,
    product_url_contains_product_id,
    safe_absolute_http_url,
    safe_absolute_http_url_uses_https,
    safe_product_source_url,
    validate_http_url_resolves_to_public_network,
)
from scripts.api_smoke_test import validate_search_response as validate_api_smoke_search_response
from scripts.csv_index import run as run_csv_index, to_markdown as csv_index_to_markdown
from scripts.env_check import build_report as build_env_check_report, to_markdown as env_check_to_markdown
from scripts.load_compare import build_report as build_load_compare_report, to_markdown as load_compare_to_markdown
from scripts.load_test import (
    build_payloads as build_load_payloads,
    build_request_specs as build_load_request_specs,
    load_identity_summary,
    load_mall_identities,
    select_mall_identity_sample,
    validate_search_response as validate_load_search_response,
)
from scripts.collect_operational_evidence import (
    input_file_validation_problems,
    representative_site_mall_config_problem_messages,
)
from scripts.mall_config_builder import build_mall_config_from_csv
from scripts.mall_config_check import api_key_hash, product_url_template_prefix, validate_mall_config
from scripts.mssql_export_csv import mall_config_alignment_report, parse_products, updated_at_quality
from scripts.mssql_view_check import (
    analyze_readonly_permissions,
    analyze_sample,
    domain_filter_coverage_report,
    query_fingerprint_report,
    validate_columns,
    validate_sample_report,
)
from scripts.marqo_resource_check import (
    index_settings_contract,
    qwen_embedding_contract,
    resource_threshold_report,
    storage_threshold_report,
)
from scripts.operational_readiness import (
    check_csv_index,
    check_data_lineage,
    check_load_client_transport,
    check_load_query_type_latency,
    check_load_server_metrics,
    check_marqo_resource,
    check_mssql_export,
    check_mssql_view,
    check_poc_dataset,
    check_server_preflight,
)
from scripts.poc_dataset_builder import build_poc_dataset, write_products_csv
from scripts.quality_report import build_report as build_quality_report, to_markdown as quality_report_to_markdown
from scripts.representative_site_check import (
    check_all_product_url_rules,
    check_saved_widget_probe_sources,
    configured_widget_selector_config,
    validate_meta_shape as validate_representative_meta_shape,
    validate_result_item_shape as validate_representative_result_item_shape,
    validate_site_collection,
    verify_widget_selectors,
)
from scripts.sample_data_guard import file_fingerprint
from scripts.search_insights import (
    build_quality_cases_seed_payload,
    build_report as build_search_insights_report,
    build_synonyms_seed_payload,
    to_markdown as search_insights_to_markdown,
)
from scripts.security_check import (
    build_security_report,
    patched_environ as patched_security_environ,
    to_markdown as security_report_to_markdown,
)
from scripts.widget_integration_probe import (
    analyze_html_source,
    to_markdown as widget_integration_probe_to_markdown,
    write_snippet_bundle,
)


SIMULATION_MARKER = "SIMULATED_ONLY_NOT_OPERATIONAL_EVIDENCE"
SIMULATED_MIXED_TRAFFIC_MALL_SAMPLE_SIZE = 50
SIMULATED_MSSQL_QUERY = (
    "SELECT product_id, product_name, price, price_min, price_max, category_name, print_methods, materials, "
    "colors, min_order_qty, delivery_days, product_group_id, main_image_url, product_url, status, updated_at, "
    "is_deleted, display_yn, mall_id FROM dbo.v_ai_search_products"
)
PRODUCT_COLUMNS = [
    "product_id",
    "product_name",
    "price",
    "price_min",
    "price_max",
    "category_name",
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
MSSQL_ALIAS_SCHEMA_VARIANTS = {
    "legacy_english": {
        "product_id": "goods_no",
        "product_name": "goods_name",
        "price": "sell_price",
        "price_min": "min_price",
        "price_max": "max_price",
        "category_name": "category",
        "print_methods": "print_method",
        "materials": "material",
        "colors": "color",
        "min_order_qty": "moq",
        "delivery_days": "lead_time_days",
        "product_group_id": "group_id",
        "main_image_url": "main_img_url",
        "product_url": "detail_url",
        "status": "goods_status",
        "updated_at": "update_dt",
        "is_deleted": "del_yn",
        "display_yn": "show_yn",
        "mall_id": "shop_code",
        "description": "goods_description",
        "keywords": "tags",
    },
    "korean_export": {
        "product_id": "상품번호",
        "product_name": "상품명",
        "price": "판매가",
        "price_min": "최저가",
        "price_max": "최고가",
        "category_name": "카테고리명",
        "print_methods": "인쇄방법",
        "materials": "소재",
        "colors": "색상",
        "min_order_qty": "최소주문수량",
        "delivery_days": "납기일수",
        "product_group_id": "상품그룹ID",
        "main_image_url": "대표이미지URL",
        "product_url": "상품상세URL",
        "status": "상품상태",
        "updated_at": "수정일시",
        "is_deleted": "삭제여부",
        "display_yn": "노출여부",
        "mall_id": "가맹점ID",
        "description": "상품설명",
        "keywords": "키워드",
    },
}
REQUIRED_MSSQL_VIEW_COLUMNS = [
    "product_id",
    "product_name",
    "price",
    "category_name",
    "main_image_url",
    "product_url",
    "status",
    "display_yn",
    "updated_at",
    "mall_id",
]
RECOMMENDED_CATEGORIES = [
    "\uc6b0\uc0b0",
    "\ud140\ube14\ub7ec",
    "\ubcfc\ud39c",
    "\ud0c0\uc62c",
    "\uc810\ucc29\uba54\ubaa8\uc9c0",
    "\uc0c1\ud328",
    "\ub2ec\ub825",
    "\uac00\ubc29",
    "\ub9c8\uc6b0\uc2a4\ud328\ub4dc",
    "\uc0dd\ud65c\uc6a9\ud488",
]
EDGE_PRODUCT_IDS = {
    1: "SIM-UMBRELLA-00001/SLASH",
    2: "SIM-TUMBLER-00002&AMP",
    3: "SIM-PEN-00003 SPACE",
    4: "SIM-TOWEL-00004?QUERY",
    5: "SIM-STICKY-00005#FRAG",
}


@dataclass(frozen=True)
class CategorySpec:
    slug: str
    category: str
    terms: tuple[str, ...]
    colors: tuple[str, ...]
    materials: tuple[str, ...]
    print_methods: tuple[str, ...]


@dataclass(frozen=True)
class SimulationPaths:
    output_dir: Path
    products_csv: Path
    poc_products_csv: Path
    mall_source_csv: Path
    malls_json: Path
    built_mall_config_json: Path
    simulated_mall_config_build_report: Path
    env_file: Path
    cors_origins_file: Path
    quality_cases_json: Path
    representative_sites_json: Path
    representative_site_pages_dir: Path
    simulated_representative_sites_report: Path
    operational_evidence_config_json: Path
    simulated_mssql_db: Path
    simulated_mssql_view_report: Path
    simulated_mssql_export_report: Path
    simulated_mssql_alias_compatibility_report: Path
    simulated_api_scale_report: Path
    simulated_security_report: Path
    simulated_csv_index_report: Path
    simulated_quality_report: Path
    simulated_api_smoke_report: Path
    simulated_image_url_report: Path
    simulated_marqo_resource_report: Path
    simulated_server_preflight_report: Path
    simulated_load_text_report: Path
    simulated_load_image_report: Path
    simulated_load_mixed_report: Path
    simulated_load_mixed_traffic_report: Path
    simulated_widget_dom_report: Path
    simulated_widget_integration_probe_report: Path
    simulated_widget_snippets_dir: Path
    simulated_operational_risk_probe_report: Path
    simulated_mixed_weight_sweep_report: Path
    simulated_qwen_query_vector_probe_report: Path
    simulated_sync_lifecycle_report: Path
    sync_lifecycle_products_csv: Path
    sync_lifecycle_log: Path
    api_scale_single_report: Path
    api_scale_multi_report: Path
    quality_image_dir: Path
    search_log: Path
    simulated_search_insights_report: Path
    simulated_search_synonyms_seed: Path
    simulated_search_quality_cases_seed: Path


CATEGORY_SPECS = [
    CategorySpec(
        slug="umbrella",
        category="\uc6b0\uc0b0",
        terms=("\uc6b0\uc0b0", "\uac80\uc740", "3\ub2e8", "\uc790\ub3d9"),
        colors=("\uac80\uc740", "\ub0a8\uc0c9", "\ud30c\ub791"),
        materials=("\ud3f4\ub9ac", "\uc54c\ub8e8\ubbf8\ub284"),
        print_methods=("\uc2e4\ud06c", "\ub85c\uace0\uc778\uc1c4"),
    ),
    CategorySpec(
        slug="tumbler",
        category="\ud140\ube14\ub7ec",
        terms=("\ud140\ube14\ub7ec", "\uc2a4\ud150", "\ubcf4\uc628", "\ubcf4\ub0c9"),
        colors=("\uac80\uc740", "\uc740\uc0c9", "\ud770\uc0c9"),
        materials=("\uc2a4\ud150", "\uc2a4\ud14c\uc778\ub9ac\uc2a4"),
        print_methods=("\ub808\uc774\uc800", "\uc2e4\ud06c"),
    ),
    CategorySpec(
        slug="pen",
        category="\ubcfc\ud39c",
        terms=("\ubcfc\ud39c", "\uc720\uc131", "\ub178\ud2b8"),
        colors=("\ud30c\ub791", "\uac80\uc815", "\ube68\uac15"),
        materials=("\ud50c\ub77c\uc2a4\ud2f1", "\uba54\ud0c8"),
        print_methods=("\ud328\ub4dc", "\uc2e4\ud06c"),
    ),
    CategorySpec(
        slug="towel",
        category="\ud0c0\uc62c",
        terms=("\ud0c0\uc62c", "\uc218\uac74", "\uc790\uc218"),
        colors=("\ud770\uc0c9", "\ud30c\ub791", "\ud68c\uc0c9"),
        materials=("\uba74", "\ucf54\ud2bc"),
        print_methods=("\uc790\uc218", "\ub77c\ubca8"),
    ),
    CategorySpec(
        slug="sticky",
        category="\uc810\ucc29\uba54\ubaa8\uc9c0",
        terms=("\uc810\ucc29\uba54\ubaa8\uc9c0", "\ud3ec\uc2a4\ud2b8\uc787", "\uba54\ubaa8"),
        colors=("\ub178\ub791", "\ud30c\ub791", "\ud770\uc0c9"),
        materials=("\uc885\uc774",),
        print_methods=("\uc624\ud504\uc14b", "\ub514\uc9c0\ud138"),
    ),
    CategorySpec(
        slug="plaque",
        category="\uc0c1\ud328",
        terms=("\uc0c1\ud328", "\ud06c\ub9ac\uc2a4\ud0c8", "\uae30\ub150"),
        colors=("\ud22c\uba85", "\uae08\uc0c9"),
        materials=("\ud06c\ub9ac\uc2a4\ud0c8", "\uc6d0\ubaa9"),
        print_methods=("\ub808\uc774\uc800",),
    ),
    CategorySpec(
        slug="calendar",
        category="\ub2ec\ub825",
        terms=("\ub2ec\ub825", "\ud0c1\uc0c1", "\ubcbd\uac78\uc774"),
        colors=("\ud770\uc0c9", "\ud30c\ub791"),
        materials=("\uc885\uc774",),
        print_methods=("\uc624\ud504\uc14b",),
    ),
    CategorySpec(
        slug="bag",
        category="\uac00\ubc29",
        terms=("\uac00\ubc29", "\uc5d0\ucf54\ubc31", "\uc1fc\ud37c\ubc31"),
        colors=("\ud770\uc0c9", "\uac80\uc815", "\uce74\ud0a4"),
        materials=("\uce94\ubc84\uc2a4", "\ubd80\uc9c1\ud3ec"),
        print_methods=("\uc2e4\ud06c", "\uc804\uc0ac"),
    ),
    CategorySpec(
        slug="mousepad",
        category="\ub9c8\uc6b0\uc2a4\ud328\ub4dc",
        terms=("\ub9c8\uc6b0\uc2a4\ud328\ub4dc", "\uc7a5\ud328\ub4dc", "\uc0ac\ubb34"),
        colors=("\uac80\uc815", "\ud68c\uc0c9"),
        materials=("\uace0\ubb34", "\ud328\ube0c\ub9ad"),
        print_methods=("\uc804\uc0ac",),
    ),
    CategorySpec(
        slug="daily",
        category="\uc0dd\ud65c\uc6a9\ud488",
        terms=("\uc0dd\ud65c\uc6a9\ud488", "\ud310\ucd09", "\uae30\uc5c5"),
        colors=("\ud770\uc0c9", "\uac80\uc815", "\ub179\uc0c9"),
        materials=("\ud50c\ub77c\uc2a4\ud2f1", "\uc2e4\ub9ac\ucf58"),
        print_methods=("\uc2e4\ud06c", "\ud328\ub4dc"),
    ),
]


def make_paths(output_dir: Path) -> SimulationPaths:
    quality_image_dir = output_dir / "quality-images"
    return SimulationPaths(
        output_dir=output_dir,
        products_csv=output_dir / "products-full.csv",
        poc_products_csv=output_dir / "poc-products.csv",
        mall_source_csv=output_dir / "malls-source.csv",
        malls_json=output_dir / "malls.json",
        built_mall_config_json=output_dir / "malls-built.json",
        simulated_mall_config_build_report=output_dir / "mall-config-build.json",
        env_file=output_dir / "haeorum-ai-search.env",
        cors_origins_file=output_dir / "cors-origins.txt",
        quality_cases_json=output_dir / "quality-cases.json",
        representative_sites_json=output_dir / "representative-sites.config.json",
        representative_site_pages_dir=output_dir / "representative-site-pages",
        simulated_representative_sites_report=output_dir / "representative-sites.json",
        operational_evidence_config_json=output_dir / "operational-evidence.config.json",
        simulated_mssql_db=output_dir / "simulated-mssql.sqlite",
        simulated_mssql_view_report=output_dir / "mssql-view.json",
        simulated_mssql_export_report=output_dir / "mssql-export.json",
        simulated_mssql_alias_compatibility_report=output_dir / "mssql-alias-compatibility.json",
        simulated_api_scale_report=output_dir / "api-scale.json",
        simulated_security_report=output_dir / "security.json",
        simulated_csv_index_report=output_dir / "csv-index.json",
        simulated_quality_report=output_dir / "quality-report.json",
        simulated_api_smoke_report=output_dir / "api-smoke.json",
        simulated_image_url_report=output_dir / "image-url-check.json",
        simulated_marqo_resource_report=output_dir / "marqo-resource.json",
        simulated_server_preflight_report=output_dir / "server-preflight.json",
        simulated_load_text_report=output_dir / "load-text.json",
        simulated_load_image_report=output_dir / "load-image.json",
        simulated_load_mixed_report=output_dir / "load-mixed.json",
        simulated_load_mixed_traffic_report=output_dir / "load-mixed-traffic.json",
        simulated_widget_dom_report=output_dir / "widget-dom.json",
        simulated_widget_integration_probe_report=output_dir / "widget-integration-probe.json",
        simulated_widget_snippets_dir=output_dir / "widget-snippets",
        simulated_operational_risk_probe_report=output_dir / "operational-risk-probes.json",
        simulated_mixed_weight_sweep_report=output_dir / "mixed-weight-sweep.json",
        simulated_qwen_query_vector_probe_report=output_dir / "qwen-query-vector-probe.json",
        simulated_sync_lifecycle_report=output_dir / "sync-lifecycle.json",
        sync_lifecycle_products_csv=output_dir / "sync-lifecycle-products.csv",
        sync_lifecycle_log=output_dir / "sync-lifecycle.jsonl",
        api_scale_single_report=output_dir / "api-scale-single-load.json",
        api_scale_multi_report=output_dir / "api-scale-multi-load.json",
        quality_image_dir=quality_image_dir,
        search_log=output_dir / "search-simulation.jsonl",
        simulated_search_insights_report=output_dir / "search-insights.json",
        simulated_search_synonyms_seed=output_dir / "query-synonyms.seed.json",
        simulated_search_quality_cases_seed=output_dir / "quality-cases.seed.json",
    )


def write_reference_images(image_dir: Path) -> dict[str, dict[str, Any]]:
    from PIL import Image

    image_dir.mkdir(parents=True, exist_ok=True)
    specs = {
        "umbrella": ("umbrella-reference.jpg", (20, 40, 75)),
        "tumbler": ("tumbler-reference.jpg", (150, 150, 145)),
        "off_topic": ("off-topic-reference.jpg", (235, 170, 40)),
        "load": ("load-reference.jpg", (80, 120, 180)),
        "load_2": ("load-reference-2.jpg", (120, 80, 180)),
        "load_3": ("load-reference-3.jpg", (180, 120, 80)),
    }
    output: dict[str, dict[str, Any]] = {}
    for name, (filename, color) in specs.items():
        path = image_dir / filename
        Image.new("RGB", (96, 96), color=color).save(path, format="JPEG", quality=88)
        raw = path.read_bytes()
        validated = validate_image_bytes(raw, max_bytes=5 * 1024 * 1024, min_dimension=16)
        output[name] = {
            "path": str(path),
            "sha256": validated.sha256,
            "size_bytes": len(raw),
            "mime_type": validated.mime_type,
        }
    return output


def deterministic_key(prefix: str, value: str, length: int = 32) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"


def mall_id_for_number(number: int) -> str:
    return f"shop{number:04d}"


def generate_product_rows(
    product_count: int,
    mall_count: int,
    reference_images: dict[str, dict[str, Any]],
    representative_mall_catalog_size: int = 300,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    representative_size = max(0, min(product_count, representative_mall_catalog_size))
    for index in range(product_count):
        spec = CATEGORY_SPECS[index % len(CATEGORY_SPECS)]
        ordinal = index + 1
        if index < representative_size:
            mall_number = 1
        else:
            other_position = index - representative_size
            if other_position < max(0, mall_count - 1):
                mall_number = other_position + 2
            else:
                mall_number = ((other_position - max(0, mall_count - 1)) % mall_count) + 1
        mall_id = mall_id_for_number(mall_number)
        product_id = EDGE_PRODUCT_IDS.get(ordinal, f"SIM-{spec.slug.upper()}-{ordinal:05d}")
        encoded_product_id = quote(product_id, safe="")
        color = spec.colors[index % len(spec.colors)]
        material = spec.materials[index % len(spec.materials)]
        print_method = spec.print_methods[index % len(spec.print_methods)]
        inactive = ordinal % 37 == 0
        deleted = ordinal % 97 == 0
        display_yn = "N" if ordinal % 43 == 0 else "Y"
        missing_image = ordinal % 149 == 0 and (inactive or deleted or display_yn == "N")
        image_hash = ""
        if spec.slug == "umbrella":
            image_hash = str(reference_images["umbrella"]["sha256"])
        elif spec.slug == "tumbler":
            image_hash = str(reference_images["tumbler"]["sha256"])
        image_tags = ",".join((*spec.terms, color, material))
        base_price = 1200 + ((ordinal * 137) % 85000)
        row = {
            "product_id": product_id,
            "product_name": f"{color} {material} {spec.category} {ordinal:04d}",
            "price": base_price,
            "price_min": max(0, base_price - 300),
            "price_max": base_price + 500,
            "category_name": spec.category,
            "print_methods": ",".join(spec.print_methods),
            "materials": ",".join(spec.materials),
            "colors": ",".join(spec.colors),
            "min_order_qty": 50 + (ordinal % 10) * 10,
            "delivery_days": 2 + (ordinal % 12),
            "product_group_id": f"GRP-{spec.slug.upper()}-{ordinal:05d}",
            "main_image_url": "" if missing_image else f"https://cdn.haeorumgift.com/sim/products/{encoded_product_id}.jpg",
            "image_hash": image_hash,
            "image_tags": image_tags,
            "product_url": f"https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={encoded_product_id}",
            "status": "soldout" if inactive else "active",
            "updated_at": f"2026-05-{((ordinal - 1) % 20) + 1:02d}T{(ordinal % 20):02d}:00:00Z",
            "is_deleted": "true" if deleted else "false",
            "display_yn": display_yn,
            "mall_id": mall_id,
            "description": f"{spec.category} {color} {material} {print_method} B2B promotion product {ordinal:04d}",
            "keywords": ",".join((*spec.terms, "\ud310\ucd09", "\uae30\uc5c5", "\uae30\ub150\ud488")),
        }
        rows.append(row)
    return rows


def write_products(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=PRODUCT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def sqlite_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def sqlite_column_type(column: str) -> str:
    if column in {"price", "price_min", "price_max", "min_order_qty", "delivery_days"}:
        return "INTEGER"
    return "TEXT"


def write_simulated_mssql_database(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    columns_sql = ", ".join(f"{sqlite_identifier(column)} {sqlite_column_type(column)}" for column in PRODUCT_COLUMNS)
    column_names = ", ".join(sqlite_identifier(column) for column in PRODUCT_COLUMNS)
    placeholders = ", ".join("?" for _ in PRODUCT_COLUMNS)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"CREATE TABLE products ({columns_sql})")
        connection.executemany(
            f"INSERT INTO products ({column_names}) VALUES ({placeholders})",
            [[row.get(column, "") for column in PRODUCT_COLUMNS] for row in rows],
        )
        connection.execute(f"CREATE VIEW v_ai_search_products AS SELECT {column_names} FROM products")
        connection.execute("CREATE INDEX idx_products_updated_at ON products(updated_at)")
        connection.execute("CREATE INDEX idx_products_product_id ON products(product_id)")
        connection.commit()


def generate_mall_config(mall_count: int) -> dict[str, Any]:
    malls = []
    for index in range(1, mall_count + 1):
        mall_id = mall_id_for_number(index)
        malls.append(
            {
                "mall_id": mall_id,
                "enabled": True,
                "api_key": deterministic_key("pk_live", mall_id, 40),
                "allowed_origins": [f"https://{mall_id}.haeorumgift.com"],
                "product_url_template": f"https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={{product_id}}",
                "excluded_product_ids": [],
                "excluded_categories": [],
                "hide_prices": False,
                "price_multiplier": 1.0,
                "price_adjustment": 0,
                "price_round_to": 10,
            }
        )
    return {"malls": malls}


def write_mall_source_csv(path: Path, mall_config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["mall_id", "origin", "api_key", "product_url_template", "enabled"]
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for mall in mall_config.get("malls") or []:
            origins = mall.get("allowed_origins") if isinstance(mall.get("allowed_origins"), list) else []
            writer.writerow(
                {
                    "mall_id": mall.get("mall_id") or "",
                    "origin": origins[0] if origins else "",
                    "api_key": mall.get("api_key") or "",
                    "product_url_template": mall.get("product_url_template") or "",
                    "enabled": "Y" if mall.get("enabled", True) else "N",
                }
            )


def build_simulated_mall_config_builder_report(paths: SimulationPaths, mall_count: int) -> dict[str, Any]:
    builder_report = build_mall_config_from_csv(
        paths.mall_source_csv,
        sort_by_mall_id=True,
        min_count=mall_count,
    )
    write_json(paths.built_mall_config_json, builder_report.get("config") or {"malls": []})
    validation_report = validate_mall_config(paths.built_mall_config_json, min_count=mall_count)
    checks = {
        "builder_ok": builder_report.get("ok") is True,
        "builder_enabled_count": int(builder_report.get("enabled_count") or 0) >= mall_count,
        "builder_generated_no_api_keys": int(builder_report.get("generated_api_key_count") or 0) == 0,
        "builder_no_duplicate_allowed_origins": not builder_report.get("duplicate_allowed_origins"),
        "builder_no_duplicate_product_url_prefixes": not builder_report.get("duplicate_product_url_prefixes"),
        "validation_ok": validation_report.get("ok") is True,
        "validation_enabled_count": int(validation_report.get("enabled_count") or 0) >= mall_count,
        "validation_strong_api_keys": not validation_report.get("weak_api_key_mall_ids"),
    }
    safe_builder_report = {
        key: value
        for key, value in builder_report.items()
        if key != "config"
    }
    validation_summary = {
        "ok": validation_report.get("ok"),
        "mall_count": validation_report.get("mall_count"),
        "enabled_count": validation_report.get("enabled_count"),
        "api_key_strength": validation_report.get("api_key_strength"),
        "weak_api_key_mall_ids": validation_report.get("weak_api_key_mall_ids") or [],
        "duplicate_mall_ids": validation_report.get("duplicate_mall_ids") or [],
        "duplicate_api_keys": validation_report.get("duplicate_api_keys") or [],
        "duplicate_allowed_origins": validation_report.get("duplicate_allowed_origins") or {},
        "duplicate_product_url_prefixes": validation_report.get("duplicate_product_url_prefixes") or {},
        "enabled_origin_count": len(validation_report.get("enabled_mall_origins") or {}),
        "enabled_product_url_prefix_count": len(validation_report.get("enabled_mall_product_url_prefixes") or {}),
        "enabled_api_key_hash_count": len(validation_report.get("enabled_mall_api_key_hashes") or {}),
        "problems": validation_report.get("problems") or [],
    }
    return mark_simulated(
        {
            "ok": all(checks.values()),
            "source_csv": str(paths.mall_source_csv),
            "built_config": str(paths.built_mall_config_json),
            "minimum_malls": mall_count,
            "checks": checks,
            "builder_report": safe_builder_report,
            "validation_report": validation_summary,
            "simulation_note": (
                "Exercises the operator path from mall export CSV to production malls.json without exposing raw "
                "public API keys in this report."
            ),
        }
    )


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def mark_simulated(report: dict[str, Any]) -> dict[str, Any]:
    report["simulation_marker"] = SIMULATION_MARKER
    report["simulation_only"] = True
    report["not_operational_readiness"] = True
    return report


def write_env_file(path: Path, paths: SimulationPaths, mall_config: dict[str, Any]) -> dict[str, str]:
    origins = [
        origin
        for mall in mall_config["malls"]
        for origin in mall.get("allowed_origins", [])
    ]
    paths.cors_origins_file.write_text("\n".join(origins) + "\n", encoding="utf-8")
    values = {
        "HAEORUM_ENV": "production",
        "HAEORUM_SEARCH_ENGINE": "marqo",
        "MARQO_URL": "http://marqo-api:8882",
        "HAEORUM_MARQO_MODEL": "Marqo/marqo-ecommerce-embeddings-L",
        "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS": "15",
        "HAEORUM_MARQO_SEARCH_RETRY_COUNT": "1",
        "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS": "0.1",
        "HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS": "2",
        "HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD": "5",
        "HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS": "5",
        "HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS": "1",
        "HAEORUM_EMBEDDING_BACKEND": "qwen",
        "HAEORUM_QWEN_EMBEDDING_URL": "http://qwen-embedding:8098",
        "HAEORUM_QWEN_EMBEDDING_DIMENSIONS": "2048",
        "HAEORUM_QWEN_MODEL": "Qwen/Qwen3-VL-Embedding-2B",
        "HAEORUM_QWEN_EMBEDDING_PROXY_API_KEY": deterministic_key(
            "qwen_proxy", "haeorum-qwen-proxy-simulation", 32
        ),
        "HAEORUM_QWEN_QUERY_TIMEOUT_SECONDS": "15",
        "HAEORUM_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES": "2048",
        "HAEORUM_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES": "512",
        "HAEORUM_INDEX_NAME": "haeorum-products-simulation",
        "HAEORUM_ADMIN_API_KEY": deterministic_key("adm_live", "haeorum-admin-simulation", 48),
        "HAEORUM_PUBLIC_API_KEY": str(mall_config["malls"][0]["api_key"]),
        "HAEORUM_MSSQL_READONLY_CONNECTION_STRING": (
            "Driver={ODBC Driver 18 for SQL Server};"
            "Server=simulated-mssql.invalid;Database=HaeorumGift;"
            "UID=readonly_ai_search;PWD="
            + deterministic_key("pwd", "haeorum-mssql-simulation", 32)
            + ";Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly"
        ),
        "HAEORUM_MALL_CONFIG_PATH": str(paths.malls_json),
        "HAEORUM_CORS_ORIGINS_FILE": str(paths.cors_origins_file),
        "HAEORUM_PRODUCT_CSV": str(paths.products_csv),
        "HAEORUM_PRODUCT_URL_TEMPLATE": "https://www.haeorumgift.com/product_view.asp?p_idx={product_id}",
        "HAEORUM_REDIS_URL": "redis://redis:6379/0",
        "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE": "900",
        "HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE": "2000",
        "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE": "300",
        "HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE": "600",
        "HAEORUM_RATE_LIMIT_MAX_BUCKETS": "10000",
        "HAEORUM_SEARCH_MAX_CONCURRENCY": "64",
        "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS": "2",
        "HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY": "8",
        "HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS": "5",
        "HAEORUM_API_THREADPOOL_TOKENS": "96",
        "MARQO_API_WORKERS": "2",
        "MARQO_API_KEEPALIVE_TIMEOUT": "75",
        "MARQO_API_GZIP_MINIMUM_SIZE": "1024",
        "MARQO_MAX_CONCURRENT_SEARCH": "100",
        "VESPA_POOL_SIZE": "128",
        "VESPA_SEARCH_TIMEOUT_MS": "5000",
        "MARQO_INFERENCE_POOL_SIZE": "128",
        "HAEORUM_TRUSTED_PROXY_IPS": "127.0.0.1,::1,10.0.0.0/8",
        "HAEORUM_SYNC_INTERVAL_SECONDS": "3600",
        "HAEORUM_SYNC_ALERT_WEBHOOK_URL": "https://ops.example.com/haeorum-sync-alert",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{name}={value}" for name, value in values.items()) + "\n", encoding="utf-8")
    return values


def write_quality_cases(path: Path, reference_images: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cases = {
        "cases": [
            {
                "name": "text_black_umbrella",
                "query": {"q": "\uac80\uc740 \uc6b0\uc0b0"},
                "expected_category": "\uc6b0\uc0b0",
                "expected_min_results": 3,
            },
            {
                "name": "text_stainless_tumbler",
                "query": {"q": "\uc2a4\ud150 \ud140\ube14\ub7ec"},
                "expected_category": "\ud140\ube14\ub7ec",
                "expected_min_results": 3,
            },
            {
                "name": "text_typo_tumbler",
                "query": {"q": "\ud150\ube14\ub7ec"},
                "tags": ["typo_or_synonym"],
                "expected_category": "\ud140\ube14\ub7ec",
                "expected_min_results": 3,
            },
            {
                "name": "image_reference_umbrella",
                "query": {"image": True},
                "image_path": reference_images["umbrella"]["path"],
                "expected_category": "\uc6b0\uc0b0",
                "expected_min_results": 3,
            },
            {
                "name": "mixed_black_tumbler",
                "query": {"q": "\uac80\uc740\uc0c9", "image": True},
                "image_path": reference_images["tumbler"]["path"],
                "expected_category": "\ud140\ube14\ub7ec",
                "expected_min_results": 3,
            },
            {
                "name": "low_confidence_off_topic_image",
                "query": {"image": True},
                "image_path": reference_images["off_topic"]["path"],
                "expected_low_confidence": True,
                "expected_min_results": 0,
            },
        ]
    }
    write_json(path, cases)
    return cases


def write_representative_sites(path: Path, mall_config: dict[str, Any]) -> dict[str, Any]:
    sites = []
    for mall in mall_config["malls"][:3]:
        mall_id = str(mall["mall_id"])
        origin = str(mall["allowed_origins"][0])
        sites.append(
            {
                "name": f"{mall_id}-simulated-site",
                "mall_id": mall_id,
                "url": origin + "/",
                "origin": origin,
                "api_base_url": "https://ai-search.haeorumgift.com",
                "api_key": mall["api_key"],
                "expected_product_url_prefix": origin,
                "widget_target": "",
                "attach_to_search_input": "#keyword",
                "attach_after_selector": "#searchForm button[type='submit']",
                "required_markers": ["HaeorumAISearch.init", "haeorum-ai-search"],
            }
        )
    data = {"sites": sites}
    write_json(path, data)
    return data


def widget_init_html(site: dict[str, Any]) -> str:
    mall_id = str(site["mall_id"])
    api_key = str(site["api_key"])
    api_base_url = str(site["api_base_url"])
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(mall_id)} simulated mall</title>
  <script src="{html.escape(api_base_url)}/widget.js"></script>
</head>
<body>
  <form id="searchForm">
    <input id="keyword" name="keyword" value="검은 우산">
    <button type="submit">검색</button>
  </form>
  <script>
    window.addEventListener("DOMContentLoaded", function () {{
      HaeorumAISearch.init({{
        attachToSearchInput: "#keyword",
        attachAfterSelector: "#searchForm button[type='submit']",
        mallId: "{html.escape(mall_id)}",
        siteId: "{html.escape(mall_id)}",
        apiKey: "{html.escape(api_key)}",
        apiBaseUrl: "{html.escape(api_base_url)}",
        limit: 20,
        maxImageMb: 5,
        minImageDimension: 16,
        triggerTitle: "AI검색",
        triggerAriaLabel: "AI 상품 검색",
        accentColor: "#0f766e",
        zIndex: 2147483000
      }});
    }});
  </script>
</body>
</html>
"""


def write_representative_site_pages(paths: SimulationPaths, representative_sites: dict[str, Any]) -> dict[str, str]:
    paths.representative_site_pages_dir.mkdir(parents=True, exist_ok=True)
    output: dict[str, str] = {}
    for site in representative_sites.get("sites", []):
        mall_id = str(site["mall_id"])
        pc_page = paths.representative_site_pages_dir / f"{mall_id}-pc.html"
        mobile_page = paths.representative_site_pages_dir / f"{mall_id}-mobile.html"
        body = widget_init_html(site)
        pc_page.write_text(body, encoding="utf-8")
        mobile_page.write_text(body, encoding="utf-8")
        site["widget_probe_sources"] = [
            {"variant": "pc", "source": str(pc_page)},
            {"variant": "mobile", "source": str(mobile_page)},
        ]
        output[mall_id] = str(pc_page)
    return output


def build_simulated_widget_integration_probe_report(
    representative_sites: dict[str, Any],
    site_pages: dict[str, str],
) -> dict[str, Any]:
    pages = []
    for site in representative_sites.get("sites", []):
        mall_id = str(site["mall_id"])
        page_path = Path(site_pages[mall_id])
        page_report = analyze_html_source(
            str(page_path),
            page_path.read_text(encoding="utf-8"),
            api_base_url=str(site["api_base_url"]),
            widget_src=f"{str(site['api_base_url']).rstrip('/')}/widget.js",
            mall_id=mall_id,
            api_key=str(site["api_key"]),
        )
        page_report["source"] = str(page_path)
        page_report["source_type"] = "file"
        pages.append(page_report)
    return mark_simulated(
        {
            "ok": all(page.get("ok") is True for page in pages) if pages else False,
            "page_count": len(pages),
            "pages_with_candidates": sum(1 for page in pages if int(page.get("candidate_count") or 0) > 0),
            "pages_ready_for_data_auto_init": sum(1 for page in pages if page.get("data_auto_init_ready") is True),
            "pages_with_blocking_risks": sum(1 for page in pages if page.get("blocking_risks")),
            "pages_with_external_widget_csp_risk": sum(
                1 for page in pages if "external_widget_src_blocked_or_risky" in (page.get("risks") or [])
            ),
            "pages_with_api_connect_csp_risk": sum(
                1 for page in pages if "api_connect_src_blocked_or_risky" in (page.get("risks") or [])
            ),
            "pages_with_unsafe_widget_url_risk": sum(
                1
                for page in pages
                if {"unsafe_api_base_url", "unsafe_widget_src", "unsafe_page_url"} & set(page.get("risks") or [])
            ),
            "pages": pages,
            "simulation_note": (
                "Local saved HTML probe checks search input selector discovery, CSP/widget.js risks, "
                "safe HTTPS API/widget URLs, and data-auto-init snippet generation before real representative-site access is available."
            ),
        }
    )


def simulated_site_search_check(mode: str, mall_id: str) -> dict[str, Any]:
    expected_query_type = "text_image" if mode == "mixed" else mode
    return {
        "name": f"{mode}_search",
        "ok": True,
        "status": 200,
        "elapsed_ms": 120.0,
        "query_type": expected_query_type,
        "engine": "marqo",
        "engine_ok": True,
        "expected_mall_id": mall_id,
        "meta_mall_id": mall_id,
        "top_count": 3,
        "item_count": 20,
        "category_count": 4,
        "suggested_categories": ["우산", "텀블러", "볼펜", "타올"],
        "missing_meta_fields": [],
        "meta_contract_problems": [],
        "missing_top_fields": [],
        "missing_item_fields": [],
        "source_scores_ok": True,
        "result_contract_problems": [],
        "result_mall_ids": [mall_id],
        "result_mall_id_mismatches": [],
        "related_items_exclude_top": True,
        "related_items_limit_ok": True,
        "next_offset_consistent": True,
        "repeated_product_ids": [],
        "categories_unique": True,
    }


def build_simulated_representative_site_report(
    paths: SimulationPaths,
    representative_sites: dict[str, Any],
    site_pages: dict[str, str],
    reference_images: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    image_file = str(reference_images["load"]["path"])
    sites = []
    for site in representative_sites.get("sites", []):
        mall_id = str(site["mall_id"])
        origin = str(site["origin"]).rstrip("/")
        product_url = f"{origin}/product_view.asp?p_idx=SIM-{mall_id.upper()}-00001"
        checks = [
            {"name": "site_config", "ok": True, "problems": []},
            {
                "name": "desktop_page",
                "ok": True,
                "url": site["url"],
                "local_page_file": site_pages[mall_id],
                "status": 200,
                "missing_markers": [],
            },
            {
                "name": "mobile_page",
                "ok": True,
                "url": site["url"],
                "local_page_file": site_pages[mall_id],
                "status": 200,
                "missing_markers": [],
            },
            check_saved_widget_probe_sources(site, str(site["api_base_url"])),
            {
                "name": "widget_init",
                "ok": True,
                "url": site["url"],
                "local_page_file": site_pages[mall_id],
                "init_call_found": True,
                "manual_init_call_found": True,
                "auto_init_found": False,
                "widget_script_found": True,
                "mall_or_site_id_found": True,
                "id_keys_found": ["mallId", "siteId"],
                "api_base_url_marker_found": True,
                "api_base_url_page_marker_found": True,
                "api_base_url_init_option": site["api_base_url"],
                "api_base_url_script_src": site["api_base_url"],
                "api_base_url_resolved": site["api_base_url"],
                "api_base_url_source": "init_option",
                "api_base_url_required": True,
                "csp": {
                    "ok": True,
                    "problems": [],
                    "policy_count": 0,
                    "inline_init_allowed": True,
                    "inline_init_blocking_directives": [],
                    "widget_src_allowed": True,
                    "widget_src_blocking_directives": [],
                    "widget_src": f"{site['api_base_url'].rstrip('/')}/widget.js",
                    "widget_src_origin": site["api_base_url"],
                    "api_connect_allowed": True,
                    "api_connect_blocking_directives": [],
                    "api_base_url_origin": site["api_base_url"],
                    "page_origin": origin,
                },
                "csp_problems": [],
                "selectors": {
                    "attachToSearchInput": "#keyword",
                    "attachAfterSelector": "#searchForm button[type='submit']",
                },
                "selector_found": {
                    "attachToSearchInput": True,
                    "attachAfterSelector": True,
                },
                "missing_selectors": [],
                "mount_selector_ok": True,
                "missing_markers": [],
            },
            {
                "name": "widget_script_asset",
                "ok": True,
                "url": f"{site['api_base_url'].rstrip('/')}/widget.js",
                "page_url": site["url"],
                "script_url_source": "widget_init.csp.widget_src",
                "status": 200,
                "content_type": "application/javascript",
                "content_type_ok": True,
                "body_looks_html": False,
                "widget_global_found": True,
                "init_marker_found": True,
                "missing_markers": [],
            },
            simulated_site_search_check("text", mall_id),
            simulated_site_search_check("image", mall_id),
            simulated_site_search_check("mixed", mall_id),
            {
                "name": "result_image_csp",
                "ok": True,
                "page_url": site["url"],
                "policy_count": 0,
                "checked": 23,
                "image_origins": ["https://cdn.haeorumgift.com"],
                "image_urls_by_mode": {"image": 23, "mixed": 23, "text": 23},
                "blocked_image_urls": [],
                "unsafe_image_urls": [],
                "message": "",
            },
            {
                "name": "text_category_refetch",
                "ok": True,
                "status": 200,
                "category": "우산",
                "result_count": 20,
                "mismatched_categories": [],
            },
        ]
        for mode in ("text", "image", "mixed"):
            checks.extend(
                [
                    {
                        "name": f"{mode}_product_url_rule",
                        "ok": True,
                        "url": product_url,
                        "expected_prefixes": [origin],
                        "matched_prefixes": [origin],
                        "expected_contains": [],
                        "expected_pattern": None,
                    },
                    {
                        "name": f"{mode}_all_product_url_rules",
                        "ok": True,
                        "checked": 23,
                        "failed": 0,
                        "failures": [],
                        "failure_overflow": 0,
                    },
                    {
                        "name": f"{mode}_detail_url",
                        "ok": True,
                        "url": product_url,
                        "status": 200,
                        "product_url_rule": {"ok": True},
                    },
                    {
                        "name": f"{mode}_click_log",
                        "ok": True,
                        "status": 200,
                        "product_id": f"SIM-{mall_id.upper()}-00001",
                        "query_type": "text_image" if mode == "mixed" else mode,
                    },
                ]
            )
        sites.append(
            {
                "name": site["name"],
                "mall_id": mall_id,
                "url": site["url"],
                "origin": site["origin"],
                "api_base_url": site["api_base_url"],
                "api_key_hash": api_key_hash(site.get("api_key")),
                "local_page_file": site_pages[mall_id],
                "ok": all(check["ok"] is True for check in checks),
                "checks": checks,
            }
        )
    return mark_simulated(
        {
            "ok": all(site["ok"] is True for site in sites),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "site_count": len(sites),
            "api_base_url": "https://ai-search.haeorumgift.com",
            "image_input": {
                "ok": True,
                "source": "file",
                "file": image_file,
                "mime_type": reference_images["load"]["mime_type"],
                "size_bytes": reference_images["load"]["size_bytes"],
                "width": 96,
                "height": 96,
                "sha256": reference_images["load"]["sha256"],
                "normalized": False,
                "quality_warnings": [],
            },
            "sites": sites,
            "simulation_note": "Local HTML pages exercise representative widget insertion markers and result contracts without proving real mall deployment.",
        }
    )


def simulated_load_mall_identity(
    mall_config: dict[str, Any],
    requested_sample_size: int = SIMULATED_MIXED_TRAFFIC_MALL_SAMPLE_SIZE,
) -> dict[str, Any]:
    enabled_malls = [
        mall
        for mall in mall_config.get("malls", [])
        if isinstance(mall, dict) and mall.get("enabled", True) is not False
    ]
    sample_size = min(max(0, requested_sample_size), len(enabled_malls))
    identities = []
    for mall in enabled_malls:
        mall_id = str(mall.get("mall_id") or "").strip()
        origins = [
            str(origin).strip()
            for origin in mall.get("allowed_origins", [])
            if str(origin).strip() and str(origin).strip() != "*"
        ]
        prefix = product_url_template_prefix(str(mall.get("product_url_template") or ""), mall_id)
        identities.append(
            namespace(
                mall_id=mall_id,
                api_key=str(mall.get("api_key") or "").strip(),
                origin=origins[0] if origins else "",
                expected_product_url_prefix=prefix,
            )
        )
    sampled_identities = select_mall_identity_sample(identities, sample_size, "spread")
    return load_identity_summary(
        sampled_identities,
        requested_sample_size,
        sampling_strategy="spread",
        source_enabled_count=len(enabled_malls),
        eligible_mall_count=len(identities),
    )


def simulated_single_mall_identity(mall_id: str, origin: str, expected_product_url_prefix: str) -> dict[str, Any]:
    return load_identity_summary(
        [
            namespace(
                mall_id=mall_id,
                api_key="pk_live_single_mall_simulated_primary_key",
                origin=origin,
                expected_product_url_prefix=expected_product_url_prefix,
            )
        ],
        0,
    )


def simulated_load_mall_counts(
    mall_identity: dict[str, Any] | None,
    requests: int,
    fallback_mall_id: str,
) -> dict[str, int]:
    sampled_mall_ids = (
        mall_identity.get("sampled_mall_ids")
        if isinstance(mall_identity, dict) and isinstance(mall_identity.get("sampled_mall_ids"), list)
        else []
    )
    mall_ids = [str(mall_id) for mall_id in sampled_mall_ids if str(mall_id or "").strip()] or [fallback_mall_id]
    base = requests // len(mall_ids)
    remainder = requests % len(mall_ids)
    return {
        mall_id: base + (1 if index < remainder else 0)
        for index, mall_id in enumerate(mall_ids)
    }


def simulated_load_product_url_prefix_counts(
    mall_identity: dict[str, Any] | None,
    requests: int,
    fallback_prefix: str,
) -> dict[str, int]:
    mall_counts = simulated_load_mall_counts(mall_identity, requests, "")
    sampled_mall_ids = list(mall_counts)
    if not sampled_mall_ids or sampled_mall_ids == [""]:
        return {fallback_prefix: requests}
    return {
        f"https://{mall_id}.haeorumgift.com/product_view.asp": count
        for mall_id, count in mall_counts.items()
        if mall_id
    }


def simulated_request_profile(
    mode_counts: dict[str, int],
    mall_count: int,
    mall_ids: list[str] | None = None,
    unique_image_inputs: int = 3,
) -> dict[str, Any]:
    mall_count = max(1, int(mall_count or 1))
    normalized_mall_ids = [
        str(mall_id)
        for mall_id in (mall_ids or [])
        if str(mall_id or "").strip()
    ]
    if not normalized_mall_ids:
        normalized_mall_ids = [f"mall{i:04d}" for i in range(1, mall_count + 1)]
    text_requests = int(mode_counts.get("text", 0) or 0)
    image_requests = int(mode_counts.get("image", 0) or 0)
    mixed_requests = int(mode_counts.get("mixed", 0) or 0)
    unique_image_inputs = max(0, int(unique_image_inputs or 0))
    text_unique_per_mall = min(7, text_requests) if text_requests else 0
    image_unique_per_mall = min(unique_image_inputs, image_requests) if image_requests else 0
    mixed_query_variants = min(7, mixed_requests) if mixed_requests else 0
    mixed_image_variants = min(unique_image_inputs, mixed_requests) if mixed_requests else 0
    mixed_unique_per_mall = min(mixed_requests, mixed_query_variants * mixed_image_variants) if mixed_requests else 0
    text_unique = min(text_requests, text_unique_per_mall * mall_count)
    image_unique = min(image_requests, image_unique_per_mall * mall_count)
    mixed_unique = min(mixed_requests, mixed_unique_per_mall * mall_count)
    effective_unique_image_inputs = unique_image_inputs if image_requests or mixed_requests else 0
    total_requests = sum(int(value or 0) for value in mode_counts.values())
    unique_request_signatures = min(total_requests, text_unique + image_unique + mixed_unique)
    unique_by_mall_id_count = {
        mall_id: unique_request_signatures // mall_count + (1 if index < unique_request_signatures % mall_count else 0)
        for index, mall_id in enumerate(normalized_mall_ids[:mall_count])
    }
    unique_by_query_type = {}
    if text_unique:
        unique_by_query_type["text"] = text_unique
    if image_unique:
        unique_by_query_type["image"] = image_unique
    if mixed_unique:
        unique_by_query_type["text_image"] = mixed_unique
    return {
        "total_requests": total_requests,
        "unique_request_signatures": unique_request_signatures,
        "repeated_request_count": max(0, total_requests - unique_request_signatures),
        "unique_by_query_type": unique_by_query_type,
        "unique_by_mall_id_count": unique_by_mall_id_count,
        "distinct_mall_count": mall_count,
        "unique_text_queries": min(7, text_requests + mixed_requests) if text_requests or mixed_requests else 0,
        "unique_image_inputs": effective_unique_image_inputs,
        "min_backend_marqo_request_attempts": unique_request_signatures,
        "min_backend_qwen_request_attempts": effective_unique_image_inputs,
    }


def simulated_load_image_input(image_file: str, image_sha256: str) -> dict[str, Any]:
    primary = Path(image_file)
    candidates = [
        primary,
        primary.with_name("load-reference-2.jpg"),
        primary.with_name("load-reference-3.jpg"),
    ]
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        raw = candidate.read_bytes()
        records.append(
            {
                "file": str(candidate),
                "size_bytes": len(raw),
                "width": 96,
                "height": 96,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    if len(records) < 3:
        return {
            "source": "file",
            "file": image_file,
            "size_bytes": primary.stat().st_size if primary.exists() else 0,
            "width": 96,
            "height": 96,
            "sha256": image_sha256,
        }
    first = records[0]
    return {
        "source": "files",
        "file": first["file"],
        "files": [record["file"] for record in records],
        "file_count": len(records),
        "unique_sha256_count": len({record["sha256"] for record in records}),
        "size_bytes": first["size_bytes"],
        "total_size_bytes": sum(int(record["size_bytes"]) for record in records),
        "width": first["width"],
        "height": first["height"],
        "sha256": first["sha256"],
        "sha256_values": [record["sha256"] for record in records],
        "images": records,
    }


def simulated_client_transport(requests: int, concurrency: int) -> dict[str, Any]:
    requests = max(0, int(requests))
    concurrency = max(0, int(concurrency))
    connections_opened = min(requests, concurrency)
    response_body_bytes = requests * 8192
    decoded_response_body_bytes = requests * 16384
    return {
        "connection_reuse": "thread_local_keep_alive",
        "search_requests": {
            "requests_sent": requests,
            "request_attempts": requests,
            "connections_opened": connections_opened,
            "connection_reuses": max(0, requests - connections_opened),
            "stale_reconnects": 0,
            "connection_close_responses": 0,
            "gzip_responses": requests,
            "total_response_body_bytes": response_body_bytes,
            "max_response_body_bytes": 8192 if requests else 0,
            "last_response_body_bytes": 8192 if requests else 0,
            "total_decoded_response_body_bytes": decoded_response_body_bytes,
            "max_decoded_response_body_bytes": 16384 if requests else 0,
            "last_decoded_response_body_bytes": 16384 if requests else 0,
        },
    }


def simulated_api_instance_coverage(requests: int, api_server_count: int) -> dict[str, Any]:
    requests = max(0, int(requests))
    api_server_count = max(1, int(api_server_count or 1))
    base = requests // api_server_count
    remainder = requests % api_server_count
    counts = {
        f"hai-sim-api-{index + 1}": base + (1 if index < remainder else 0)
        for index in range(api_server_count)
    }
    return {
        "ok": True,
        "expected_api_server_count": api_server_count,
        "required_distinct_api_instances": api_server_count,
        "distinct_api_instance_count": len(counts),
        "successful_responses": requests,
        "missing_header_count": 0,
        "minimum_instance_responses": max(1, int(requests * 0.05)) if api_server_count >= 2 and requests else 0,
        "api_instance_counts": counts,
        "under_minimum_api_instances": [],
        "problems": [],
    }


def simulated_admin_metrics_source_coverage(api_server_count: int) -> dict[str, Any]:
    api_server_count = max(1, int(api_server_count or 1))
    instance_ids = [f"hai-sim-api-{index + 1}" for index in range(api_server_count)]
    return {
        "ok": True,
        "expected_api_server_count": api_server_count,
        "successful_source_count": api_server_count,
        "distinct_instance_count": api_server_count,
        "instance_ids": instance_ids,
        "problems": [],
    }


def simulated_mixed_traffic_report(
    *,
    api_server_count: int,
    image_file: str,
    image_sha256: str,
    base_url: str,
    mall_id: str,
    origin: str,
    expected_product_url_prefix: str,
    mall_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requests = 850
    image_searches = 255
    mode_counts = {"text": 595, "image": 85, "mixed": 170}
    mall_counts = simulated_load_mall_counts(mall_identity, requests, mall_id)
    result_mall_counts = {sampled_mall_id: count * 2 for sampled_mall_id, count in mall_counts.items()}
    prefix_counts = simulated_load_product_url_prefix_counts(mall_identity, requests, expected_product_url_prefix)
    request_profile = simulated_request_profile(mode_counts, len(mall_counts), list(mall_counts))
    p99_limit = 8000
    text_p95 = 950 if api_server_count == 1 else 760
    image_p95 = 1700 if api_server_count == 1 else 1360
    mixed_p95 = 1800 if api_server_count == 1 else 1450
    query_type_latency_ms = {
        "text": {"count": 595, "min": 120, "avg": 420, "p50": 390, "p95": text_p95, "p99": text_p95 + 280, "max": text_p95 + 420},
        "image": {"count": 85, "min": 260, "avg": 760, "p50": 710, "p95": image_p95, "p99": image_p95 + 420, "max": image_p95 + 560},
        "text_image": {"count": 170, "min": 300, "avg": 820, "p50": 780, "p95": mixed_p95, "p99": mixed_p95 + 520, "max": mixed_p95 + 680},
    }
    return mark_simulated(
        {
            "ok": True,
            "base_url": base_url,
            "mall_id": mall_id,
            "origin": origin,
            "target_validation": {"ok": True, "base_url": base_url, "origin": origin},
            "api_server_count": api_server_count,
            "scenario": "mixed-traffic",
            "active_users": 850,
            "requests": requests,
            "concurrency": 100,
            "mode": "mixed",
            "mode_counts": mode_counts,
            "request_profile": request_profile,
            "error_rate": 0.0,
            "requests_per_second": 90.0 * max(1, api_server_count),
            "latency_ms": {"p95": 1800 if api_server_count == 1 else 1450, "p99": 2600 if api_server_count == 1 else 2200},
            "client_transport": simulated_client_transport(requests, 100),
            "api_instance_coverage": simulated_api_instance_coverage(requests, api_server_count),
            "expected_query_type_latency_ms": query_type_latency_ms,
            "response_query_type_latency_ms": query_type_latency_ms,
            "thresholds": {
                "p95_ms": 5000,
                "p99_ms": p99_limit,
                "request_timeout_seconds": max(10, int(round((float(p99_limit) / 1000.0) * 2.0))),
                "min_requests_per_second": 5.0,
                "max_server_wait_avg_ms": 1000,
                "max_process_rss_growth_mb": 512,
                "max_error_rate": 1.0,
            },
            "image_input": simulated_load_image_input(image_file, image_sha256),
            "response_contract": {
                "ok": True,
                "successful_responses": requests,
                "valid_successful_responses": requests,
                "invalid_successful_responses": 0,
                "expected_query_type_counts": {"text": 595, "image": 85, "text_image": 170},
                "query_type_counts": {"text": 595, "image": 85, "text_image": 170},
                "engine_counts": {"marqo": requests},
                "non_marqo_engine_responses": 0,
                "expected_mall_id_counts": mall_counts,
                "expected_product_url_prefix_counts": prefix_counts,
                "product_url_prefix_required": True,
                "product_url_prefix_mismatch_count": 0,
                "meta_mall_id_counts": mall_counts,
                "result_mall_id_counts": result_mall_counts,
                "mall_id_mismatch_count": 0,
                "min_top_count": 1,
                "min_item_count": 1,
                "min_category_count": 1,
            },
            "mall_identity": mall_identity or simulated_single_mall_identity(mall_id, origin, expected_product_url_prefix),
            "server_metrics": simulated_server_metrics(requests, image_searches, api_server_count=api_server_count),
        }
    )


def write_api_scale_inputs(paths: SimulationPaths, reference_images: dict[str, dict[str, Any]], mall_config: dict[str, Any]) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    base_url = "https://ai-search.haeorumgift.com"
    origin = first_mall["allowed_origins"][0]
    image_file = str(reference_images["load"]["path"])
    image_sha256 = str(reference_images["load"]["sha256"])
    mall_identity = simulated_load_mall_identity(
        mall_config,
        requested_sample_size=SIMULATED_MIXED_TRAFFIC_MALL_SAMPLE_SIZE,
    )
    expected_product_url_prefix = product_url_template_prefix(
        str(first_mall.get("product_url_template") or ""),
        str(first_mall.get("mall_id") or ""),
    ) or f"https://{first_mall['mall_id']}.haeorumgift.com/product_view.asp"
    single = simulated_mixed_traffic_report(
        api_server_count=1,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=first_mall["mall_id"],
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
        mall_identity=mall_identity,
    )
    multi = simulated_mixed_traffic_report(
        api_server_count=2,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=first_mall["mall_id"],
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
        mall_identity=mall_identity,
    )
    write_json(paths.api_scale_single_report, single)
    write_json(paths.api_scale_multi_report, multi)
    return {
        "single_report": str(paths.api_scale_single_report),
        "multi_report": str(paths.api_scale_multi_report),
    }


def build_simulated_api_scale_report(paths: SimulationPaths) -> dict[str, Any]:
    single = json.loads(paths.api_scale_single_report.read_text(encoding="utf-8"))
    multi = json.loads(paths.api_scale_multi_report.read_text(encoding="utf-8"))
    report = build_load_compare_report(single, multi)
    report["simulation_note"] = "Synthetic one-API and two-API mixed traffic reports exercise load_compare.py without proving real deployed scale."
    return mark_simulated(report)


def build_simulated_security_report(
    paths: SimulationPaths,
    env_values: dict[str, str],
    *,
    base_url: str = "https://ai-search.haeorumgift.com",
) -> dict[str, Any]:
    with patched_security_environ(env_values, clear=True):
        report = build_security_report(
            load_settings(),
            base_url=base_url,
            mssql_ip_restricted=True,
            sync_alerting_configured=True,
            nginx_config_path=ROOT / "deploy" / "nginx" / "haeorum-ai-search.conf",
            logrotate_config_path=ROOT / "deploy" / "logrotate" / "haeorum-ai-search",
            systemd_service_path=ROOT / "deploy" / "systemd" / "haeorum-ai-search.service",
            sync_systemd_service_path=ROOT / "deploy" / "systemd" / "haeorum-ai-sync.service",
            reindex_systemd_service_path=ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.service",
            reindex_systemd_timer_path=ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.timer",
            service_env_file_path=paths.env_file,
        )
    report["simulation_note"] = "Deployment templates and simulated production env were checked locally; target-host installation is still unproven."
    return mark_simulated(report)


def simulated_server_metrics(requests: int, image_requests: int = 0, api_server_count: int = 1) -> dict[str, Any]:
    api_server_count = max(1, int(api_server_count or 1))
    source_coverage = simulated_admin_metrics_source_coverage(api_server_count)
    report = {
        "requested": True,
        "ok": True,
        "admin_metrics_source_coverage": source_coverage,
        "coverage_ok": True,
        "run_log_coverage": {
            "ok": True,
            "search_events": requests,
            "image_search_events": image_requests,
        },
        "after": {
            "snapshot": {
                "engine_ok": True,
                "engine_backend": "marqo",
                "engine_index": "haeorum-products-sim",
                "marqo_model": "Marqo/marqo-ecommerce-embeddings-L",
                "embedding_backend": "qwen",
                "qwen_model": "Qwen/Qwen3-VL-Embedding-2B",
                "qwen_embedding_dimensions": 2048,
                "qwen_query_vector_runtime_entries": max(1, requests // 20),
                "qwen_query_vector_runtime_text_entries": max(1, requests // 30),
                "qwen_query_vector_runtime_image_entries": max(0, image_requests // 15),
                "qwen_query_vector_runtime_max_entries": 2560,
                "qwen_query_vector_runtime_text_max_entries": 2048,
                "qwen_query_vector_runtime_image_max_entries": 512,
                "qwen_query_vector_mixed_parallelism": 8,
                "qwen_query_vector_in_flight": 0,
                "qwen_query_vector_wait_timeout_seconds": 31.1,
                "qwen_query_vector_wait_events": max(0, requests // 12),
                "qwen_query_vector_wait_timeouts": 0,
                "qwen_query_vector_total_wait_ms": round(requests * 0.04, 3),
                "qwen_query_vector_avg_wait_ms": 1.2 if requests else 0.0,
                "qwen_query_vector_max_wait_ms": 7.0 if requests else 0.0,
                "engine_search_attempts": requests,
                "engine_adaptive_refetches": 0,
                "engine_adaptive_refetch_searches": 0,
                "engine_underfilled_after_max_candidates_events": 0,
                "engine_average_search_attempts": 1.0 if requests else 0.0,
                "engine_max_search_attempts": 1 if requests else 0,
                "engine_average_final_candidate_limit": 24.0 if requests else 0.0,
                "engine_max_final_candidate_limit": 24 if requests else 0,
                "admin_metrics_source_count": api_server_count,
                "admin_metrics_instance_ids": source_coverage["instance_ids"],
                "process_instance_id": source_coverage["instance_ids"][0],
                "backend_marqo_request_attempts": requests,
                "backend_marqo_connections_opened": min(requests, 100),
                "backend_marqo_connection_reuses": max(0, requests - min(requests, 100)),
                "backend_marqo_idle_reconnects": 0,
                "backend_marqo_stale_reconnects": 0,
                "backend_marqo_error_responses": 0,
                "backend_marqo_connection_close_responses": 0,
                "backend_marqo_gzip_responses": requests,
                "backend_marqo_retry_after_responses": 0,
                "backend_marqo_max_retry_after_seconds": 0.0,
                "backend_marqo_last_retry_after_seconds": 0.0,
                "backend_marqo_total_elapsed_ms": round(requests * 95.0, 3),
                "backend_marqo_avg_elapsed_ms": 95.0 if requests else 0.0,
                "backend_marqo_max_elapsed_ms": 420.0 if requests else 0.0,
                "backend_marqo_last_elapsed_ms": 88.0 if requests else 0.0,
                "backend_marqo_total_request_body_bytes": requests * 1536,
                "backend_marqo_max_request_body_bytes": 4096 if requests else 0,
                "backend_marqo_last_request_body_bytes": 1536 if requests else 0,
                "backend_marqo_total_response_body_bytes": requests * 8192,
                "backend_marqo_max_response_body_bytes": 32768 if requests else 0,
                "backend_marqo_last_response_body_bytes": 8192 if requests else 0,
                "backend_marqo_total_decoded_response_body_bytes": requests * 16384,
                "backend_marqo_max_decoded_response_body_bytes": 65536 if requests else 0,
                "backend_marqo_last_decoded_response_body_bytes": 16384 if requests else 0,
                "backend_qwen_request_attempts": requests,
                "backend_qwen_connections_opened": min(requests, 100),
                "backend_qwen_connection_reuses": max(0, requests - min(requests, 100)),
                "backend_qwen_idle_reconnects": 0,
                "backend_qwen_stale_reconnects": 0,
                "backend_qwen_error_responses": 0,
                "backend_qwen_connection_close_responses": 0,
                "backend_qwen_gzip_responses": requests,
                "backend_qwen_retry_after_responses": 0,
                "backend_qwen_max_retry_after_seconds": 0.0,
                "backend_qwen_last_retry_after_seconds": 0.0,
                "backend_qwen_total_elapsed_ms": round(requests * 45.0, 3),
                "backend_qwen_avg_elapsed_ms": 45.0 if requests else 0.0,
                "backend_qwen_max_elapsed_ms": 180.0 if requests else 0.0,
                "backend_qwen_last_elapsed_ms": 42.0 if requests else 0.0,
                "backend_qwen_total_request_body_bytes": requests * 2048,
                "backend_qwen_max_request_body_bytes": 8192 if requests else 0,
                "backend_qwen_last_request_body_bytes": 2048 if requests else 0,
                "backend_qwen_total_response_body_bytes": requests * 8192,
                "backend_qwen_max_response_body_bytes": 32768 if requests else 0,
                "backend_qwen_last_response_body_bytes": 8192 if requests else 0,
                "backend_qwen_total_decoded_response_body_bytes": requests * 32768,
                "backend_qwen_max_decoded_response_body_bytes": 65536 if requests else 0,
                "backend_qwen_last_decoded_response_body_bytes": 32768 if requests else 0,
                "result_mall_id_mismatch_events": 0,
                "result_mall_id_mismatch_count": 0,
                "rate_limit_backend": "redis",
                "rate_limit_redis_enabled": True,
                "rate_limit_search_per_minute": 900,
                "rate_limit_mall_search_per_minute": 2000,
                "rate_limit_image_per_minute": 300,
                "rate_limit_mall_image_per_minute": 600,
                "rate_limit_fallback_events": 0,
                "cache_backend": "redis",
                "cache_redis_enabled": True,
                "cache_ttl_seconds": 300,
                "cache_error_count": 0,
                "cache_clear_errors": 0,
                "cache_lock_claims": max(1, requests // 25),
                "cache_lock_contention_events": max(0, requests // 50),
                "cache_lock_errors": 0,
                "cache_lock_release_errors": 0,
                "cache_lock_wait_events": max(0, requests // 50),
                "cache_lock_wait_timeouts": 0,
                "cache_lock_total_wait_ms": round(requests * 0.03, 3),
                "cache_lock_avg_wait_ms": 1.5 if requests else 0.0,
                "cache_lock_max_wait_ms": 8.0 if requests else 0.0,
                "singleflight_enabled": True,
                "singleflight_in_flight": 0,
                "singleflight_wait_timeout_seconds": 5.0,
                "singleflight_wait_events": max(0, requests // 20),
                "singleflight_wait_timeouts": 0,
                "singleflight_total_wait_ms": round(requests * 0.05, 3),
                "singleflight_avg_wait_ms": 1.0 if requests else 0.0,
                "singleflight_max_wait_ms": 6.0 if requests else 0.0,
                "process_memory_rss_bytes": 512 * 1024 * 1024,
                "system_memory_used_percent": 47.5,
                "disk_used_percent": 31.2,
                "search_queue_enabled": True,
                "search_queue_max_concurrency": 64,
                "search_queue_timeout_seconds": 2.0,
                "search_queue_in_flight": 0,
                "search_queue_full_events": 0,
                "search_queue_wait_events": requests,
                "search_queue_total_wait_ms": round(requests * 0.4, 3),
                "search_queue_avg_wait_ms": 0.4,
                "search_queue_max_wait_ms": 8.0,
                "search_queue_last_wait_ms": 0.1,
                "image_queue_enabled": True,
                "image_queue_max_concurrency": 8,
                "image_queue_timeout_seconds": 5.0,
                "image_queue_in_flight": 0,
                "image_queue_full_events": 0,
                "image_queue_wait_events": image_requests,
                "image_queue_total_wait_ms": round(image_requests * 0.8, 3),
                "image_queue_avg_wait_ms": 0.8 if image_requests else 0.0,
                "image_queue_max_wait_ms": 12.0 if image_requests else 0.0,
                "image_queue_last_wait_ms": 0.1 if image_requests else 0.0,
                "api_threadpool_ok": True,
                "api_threadpool_configured_tokens": 96,
                "api_threadpool_runtime_tokens": 96,
                "api_threadpool_required_tokens": 80,
                "search_log_write_errors": 0,
                "error_log_write_errors": 0,
            }
        },
        "delta": {
            "search_events": requests,
            "image_search_events": image_requests,
            "engine_search_attempts": requests,
            "engine_adaptive_refetches": 0,
            "engine_adaptive_refetch_searches": 0,
            "engine_underfilled_after_max_candidates_events": 0,
            "result_mall_id_mismatch_events": 0,
            "result_mall_id_mismatch_count": 0,
            "backend_marqo_request_attempts": requests,
            "backend_marqo_connections_opened": min(requests, 100),
            "backend_marqo_connection_reuses": max(0, requests - min(requests, 100)),
            "backend_marqo_idle_reconnects": 0,
            "backend_marqo_stale_reconnects": 0,
            "backend_marqo_error_responses": 0,
            "backend_marqo_connection_close_responses": 0,
            "backend_marqo_gzip_responses": requests,
            "backend_marqo_retry_after_responses": 0,
            "backend_marqo_connection_acquire_wait_events": requests,
            "backend_marqo_connection_acquire_wait_timeouts": 0,
            "backend_marqo_total_connection_acquire_wait_ms": round(requests * 0.08, 3),
            "backend_marqo_connection_acquire_run_avg_wait_ms": 0.08 if requests else 0.0,
            "backend_marqo_total_elapsed_ms": round(requests * 95.0, 3),
            "backend_marqo_run_avg_elapsed_ms": 95.0 if requests else 0.0,
            "backend_marqo_total_request_body_bytes": requests * 1536,
            "backend_marqo_run_avg_request_body_bytes": 1536.0 if requests else 0.0,
            "backend_marqo_total_response_body_bytes": requests * 8192,
            "backend_marqo_run_avg_response_body_bytes": 8192.0 if requests else 0.0,
            "backend_marqo_total_decoded_response_body_bytes": requests * 16384,
            "backend_marqo_run_avg_decoded_response_body_bytes": 16384.0 if requests else 0.0,
            "backend_qwen_request_attempts": requests,
            "backend_qwen_connections_opened": min(requests, 100),
            "backend_qwen_connection_reuses": max(0, requests - min(requests, 100)),
            "backend_qwen_idle_reconnects": 0,
            "backend_qwen_stale_reconnects": 0,
            "backend_qwen_error_responses": 0,
            "backend_qwen_connection_close_responses": 0,
            "backend_qwen_gzip_responses": requests,
            "backend_qwen_retry_after_responses": 0,
            "backend_qwen_connection_acquire_wait_events": requests,
            "backend_qwen_connection_acquire_wait_timeouts": 0,
            "backend_qwen_total_connection_acquire_wait_ms": round(requests * 0.04, 3),
            "backend_qwen_connection_acquire_run_avg_wait_ms": 0.04 if requests else 0.0,
            "backend_qwen_total_elapsed_ms": round(requests * 45.0, 3),
            "backend_qwen_run_avg_elapsed_ms": 45.0 if requests else 0.0,
            "backend_qwen_total_request_body_bytes": requests * 2048,
            "backend_qwen_run_avg_request_body_bytes": 2048.0 if requests else 0.0,
            "backend_qwen_total_response_body_bytes": requests * 8192,
            "backend_qwen_run_avg_response_body_bytes": 8192.0 if requests else 0.0,
            "backend_qwen_total_decoded_response_body_bytes": requests * 32768,
            "backend_qwen_run_avg_decoded_response_body_bytes": 32768.0 if requests else 0.0,
            "qwen_query_vector_wait_events": max(0, requests // 12),
            "qwen_query_vector_wait_timeouts": 0,
            "qwen_query_vector_total_wait_ms": round(requests * 0.04, 3),
            "qwen_query_vector_run_avg_wait_ms": (
                round(round(requests * 0.04, 3) / max(1, requests // 12), 3)
                if max(0, requests // 12) > 0
                else 0.0
            ),
            "rate_limited_events": 0,
            "rate_limit_fallback_events": 0,
            "cache_error_count": 0,
            "cache_clear_errors": 0,
            "cache_lock_claims": max(1, requests // 25),
            "cache_lock_contention_events": max(0, requests // 50),
            "cache_lock_errors": 0,
            "cache_lock_release_errors": 0,
            "cache_lock_wait_events": max(0, requests // 50),
            "cache_lock_wait_timeouts": 0,
            "cache_lock_total_wait_ms": round(requests * 0.03, 3),
            "cache_lock_run_avg_wait_ms": (
                round(round(requests * 0.03, 3) / max(1, requests // 50), 3)
                if max(0, requests // 50) > 0
                else 0.0
            ),
            "singleflight_wait_events": max(0, requests // 20),
            "singleflight_wait_timeouts": 0,
            "singleflight_total_wait_ms": round(requests * 0.05, 3),
            "singleflight_run_avg_wait_ms": (
                round(round(requests * 0.05, 3) / max(1, requests // 20), 3)
                if max(0, requests // 20) > 0
                else 0.0
            ),
            "search_queue_acquired_events": requests,
            "search_queue_full_events": 0,
            "search_queue_wait_events": requests,
            "search_queue_total_wait_ms": round(requests * 0.4, 3),
            "search_queue_run_avg_wait_ms": 0.4 if requests else 0.0,
            "image_queue_full_events": 0,
            "image_queue_wait_events": image_requests,
            "image_queue_total_wait_ms": round(image_requests * 0.8, 3),
            "image_queue_run_avg_wait_ms": 0.8 if image_requests else 0.0,
            "search_log_write_errors": 0,
            "error_log_write_errors": 0,
        },
    }
    after_snapshot = report["after"]["snapshot"]
    before_snapshot = {
        **after_snapshot,
        "process_memory_rss_bytes": 384 * 1024 * 1024,
        "search_events": 0,
        "image_search_events": 0,
        "engine_search_attempts": 0,
        "engine_adaptive_refetches": 0,
        "engine_adaptive_refetch_searches": 0,
        "engine_underfilled_after_max_candidates_events": 0,
        "backend_marqo_request_attempts": 0,
        "backend_qwen_request_attempts": 0,
        "search_queue_wait_events": 0,
        "search_queue_total_wait_ms": 0,
        "image_queue_wait_events": 0,
        "image_queue_total_wait_ms": 0,
        "cache_lock_wait_events": 0,
        "cache_lock_total_wait_ms": 0,
        "singleflight_wait_events": 0,
        "singleflight_total_wait_ms": 0,
    }
    report["before"] = {"snapshot": before_snapshot}
    report["process_memory_rss_growth_mb"] = 128.0
    return report


def simulated_load_report(
    *,
    mode: str,
    requests: int,
    concurrency: int,
    p95_ms: int,
    p99_ms: int | None = None,
    reference_images: dict[str, dict[str, Any]],
    mall_config: dict[str, Any],
    active_users: int = 0,
) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    effective_p99_ms = p99_ms if p99_ms is not None else max(p95_ms, int(round(p95_ms * 1.6)))
    image_file = str(reference_images["load"]["path"])
    image_requests = requests if mode in {"image", "mixed"} else 0
    mode_counts = {mode: requests}
    query_type_counts = {mode if mode != "mixed" else "text_image": requests}
    scenario = None
    if mode == "mixed-traffic":
        scenario = "mixed-traffic"
        mode_counts = {"text": 595, "image": 85, "mixed": 170}
        query_type_counts = {"text": 595, "image": 85, "text_image": 170}
        image_requests = 255
    mall_identity = (
        simulated_load_mall_identity(
            mall_config,
            requested_sample_size=SIMULATED_MIXED_TRAFFIC_MALL_SAMPLE_SIZE,
        )
        if mode == "mixed-traffic"
        else None
    )
    mall_counts = simulated_load_mall_counts(mall_identity, requests, first_mall["mall_id"])
    request_profile = simulated_request_profile(mode_counts, len(mall_counts), list(mall_counts))
    result_mall_counts = {sampled_mall_id: count * 2 for sampled_mall_id, count in mall_counts.items()}
    expected_product_url_prefix = product_url_template_prefix(
        str(first_mall.get("product_url_template") or ""),
        str(first_mall.get("mall_id") or ""),
    ) or f"https://{first_mall['mall_id']}.haeorumgift.com/product_view.asp"
    prefix_counts = (
        simulated_load_product_url_prefix_counts(mall_identity, requests, expected_product_url_prefix)
        if mode == "mixed-traffic"
        else {expected_product_url_prefix: requests}
    )
    query_type_latency_ms = {
        query_type: {
            "count": count,
            "min": max(1, p95_ms - 1800),
            "avg": max(1, p95_ms - 1200),
            "p50": max(1, p95_ms - 1000),
            "p95": p95_ms - 750,
            "p99": min(effective_p99_ms - 500, p95_ms + 450),
            "max": min(effective_p99_ms - 250, p95_ms + 700),
        }
        for query_type, count in query_type_counts.items()
    }
    report = {
        "ok": True,
        "base_url": "https://ai-search.haeorumgift.com",
        "mall_id": first_mall["mall_id"],
        "origin": first_mall["allowed_origins"][0],
        "target_validation": {
            "ok": True,
            "base_url": "https://ai-search.haeorumgift.com",
            "origin": first_mall["allowed_origins"][0],
        },
        "api_server_count": 1,
        "mode": "mixed" if mode == "mixed-traffic" else mode,
        "scenario": scenario,
        "active_users": active_users,
        "requests": requests,
        "concurrency": concurrency,
        "mode_counts": mode_counts,
        "request_profile": request_profile,
        "error_rate": 0.0,
        "requests_per_second": round(max(1, requests) / 10.0, 2),
        "latency_ms": {"p95": p95_ms - 750, "p99": min(effective_p99_ms - 500, p95_ms + 450)},
        "client_transport": simulated_client_transport(requests, concurrency),
        "api_instance_coverage": simulated_api_instance_coverage(requests, 1),
        "expected_query_type_latency_ms": query_type_latency_ms,
        "response_query_type_latency_ms": query_type_latency_ms,
        "thresholds": {
            "p95_ms": p95_ms,
            "p99_ms": effective_p99_ms,
            "request_timeout_seconds": max(10, int(round((float(effective_p99_ms) / 1000.0) * 2.0))),
            "min_requests_per_second": max(1.0, round(float(concurrency) / (float(p95_ms) / 1000.0) * 0.25, 2)),
            "max_server_wait_avg_ms": max(250, int(round(float(p95_ms) * 0.2))),
            "max_process_rss_growth_mb": 512,
            "max_error_rate": 1.0,
        },
        "response_contract": {
            "ok": True,
            "successful_responses": requests,
            "valid_successful_responses": requests,
            "invalid_successful_responses": 0,
            "expected_query_type_counts": query_type_counts,
            "query_type_counts": query_type_counts,
            "engine_counts": {"marqo": requests},
            "non_marqo_engine_responses": 0,
            "expected_mall_id_counts": mall_counts,
            "expected_product_url_prefix_counts": prefix_counts,
            "product_url_prefix_required": True,
            "product_url_prefix_mismatch_count": 0,
            "meta_mall_id_counts": mall_counts,
            "result_mall_id_counts": result_mall_counts,
            "mall_id_mismatch_count": 0,
            "min_top_count": 1,
            "min_item_count": 1,
            "min_category_count": 1,
        },
        "server_metrics": simulated_server_metrics(requests, image_requests),
        "simulation_note": "Synthetic load report shape exercises operational readiness contracts without hitting the deployed API.",
    }
    if mode in {"image", "mixed", "mixed-traffic"}:
        report["image_input"] = simulated_load_image_input(image_file, str(reference_images["load"]["sha256"]))
    if mall_identity is not None:
        report["mall_identity"] = mall_identity
    return mark_simulated(report)


def build_simulated_api_smoke_report(mall_config: dict[str, Any]) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    expected_product_url_prefix = "https://shop0001.haeorumgift.com/product_view.asp"
    expected_click_product_url_prefix = f"{expected_product_url_prefix}?p_idx="
    check_names = [
        "health",
        "cors_preflight",
        "invalid_cors_preflight_rejected",
        "text_search",
        "image_search",
        "multipart_image_search",
        "mixed_search",
        "click_log",
        "site_id_click_log",
        "unsafe_click_product_url_rejected",
        "foreign_click_product_url_rejected",
        "click_product_url_template_prefix_mismatch_rejected",
        "click_product_url_product_id_mismatch_rejected",
        "sync_status",
        "search_log",
        "sync_log",
        "error_log",
        "sensitive_log_redaction",
        "metrics",
        "prometheus_metrics",
        "invalid_admin_key_rejected",
        "admin_mutation_endpoints_protected",
    ]
    checks = [{"name": name, "ok": True} for name in check_names]
    for check in checks:
        if check["name"] in {"text_search", "site_id_search", "image_search", "multipart_image_search", "site_id_multipart_image_search", "mixed_search"}:
            check.update(
                {
                    "response_contract_ok": True,
                    "engine": "marqo",
                    "expected_mall_id": first_mall["mall_id"],
                    "expected_product_url_prefix": expected_product_url_prefix,
                    "meta_mall_id": first_mall["mall_id"],
                    "result_mall_ids": [first_mall["mall_id"], first_mall["mall_id"]],
                    "result_product_urls": [
                        f"{expected_product_url_prefix}?p_idx=SIM-API-SMOKE-001",
                        f"{expected_product_url_prefix}?p_idx=SIM-API-SMOKE-002",
                    ],
                    "product_url_prefix_mismatch_count": 0,
                }
            )
        if check["name"] in {"click_log", "site_id_click_log"}:
            check.update(
                {
                    "expected_product_url_prefix": expected_product_url_prefix,
                    "expected_click_product_url_prefix": expected_click_product_url_prefix,
                    "click_product_id": "SIM-API-SMOKE-001",
                    "click_product_url": f"{expected_click_product_url_prefix}SIM-API-SMOKE-001",
                    "click_product_url_prefix_matches": True,
                    "click_product_url_template_prefix_matches": True,
                    "click_product_url_contains_product_id": True,
                }
            )
        if check["name"] == "metrics":
            check.update(
                {
                    "engine_ok": True,
                    "engine_backend": "marqo",
                    "search_queue_enabled": True,
                    "search_queue_max_concurrency": 64,
                    "image_queue_enabled": True,
                    "image_queue_max_concurrency": 8,
                    "api_threadpool_ok": True,
                    "api_threadpool_configured_tokens": 96,
                    "api_threadpool_runtime_tokens": 96,
                    "api_threadpool_required_tokens": 80,
                }
            )
        if check["name"] == "sync_status":
            check.update({"sync_status_engine_ok": True, "sync_status_engine": "marqo", "sync_status_index_ok": True, "sync_status_index": "haeorum-products-simulation"})
    return mark_simulated(
        {
            "ok": True,
            "base_url": "https://ai-search.haeorumgift.com",
            "mall_id": first_mall["mall_id"],
            "origin": first_mall["allowed_origins"][0],
            "expected_product_url_prefix": expected_product_url_prefix,
            "expected_click_product_url_prefix": expected_click_product_url_prefix,
            "target_validation": {
                "ok": True,
                "base_url": "https://ai-search.haeorumgift.com",
                "origin": first_mall["allowed_origins"][0],
                "expected_product_url_prefix": expected_product_url_prefix,
                "expected_click_product_url_prefix": expected_click_product_url_prefix,
            },
            "checks": checks,
            "simulation_note": "Only a compact smoke-shape report is generated here; real api_smoke_test.py must hit the deployed API.",
        }
    )


def build_simulated_image_url_report(paths: SimulationPaths) -> dict[str, Any]:
    products = load_products(paths.products_csv)
    active_products = [product for product in products if product.active]
    missing_active_image_url_ids = sorted(product.product_id for product in active_products if not product.image_url)
    non_https_active_image_url_ids = sorted(
        product.product_id
        for product in active_products
        if product.image_url
        and safe_absolute_http_url(product.image_url) is not None
        and not safe_absolute_http_url_uses_https(product.image_url)
    )
    return mark_simulated(
        {
            "ok": not missing_active_image_url_ids and not non_https_active_image_url_ids,
            "require_https": True,
            "csv": str(paths.products_csv),
            "csv_fingerprint": file_fingerprint(paths.products_csv),
            "source": {
                "csv_is_builtin_sample": False,
                "dataset_is_builtin_sample_derived": False,
                "builtin_sample_product_id_overlap": 0,
                "product_id_count": len(active_products),
                "builtin_sample_product_id_ratio": 0.0,
            },
            "active_products": len(active_products),
            "missing_active_image_url_count": len(missing_active_image_url_ids),
            "missing_active_image_url_product_ids": missing_active_image_url_ids[:50],
            "non_https_active_image_url_count": len(non_https_active_image_url_ids),
            "non_https_active_image_url_product_ids": non_https_active_image_url_ids[:50],
            "checked": 100,
            "failed": 0,
            "warning_count": 0,
            "failure_category_counts": {},
            "warning_type_counts": {},
            "blocking_warning_count": 0,
            "blocking_warning_type_counts": {},
            "attempts": {"max": 1, "retried_successes": 0, "retried_failures": 0},
            "limit": 100,
            "concurrency": 5,
            "timeout_seconds": 10,
            "retry_count": 1,
            "retry_delay_seconds": 0.25,
            "max_mb": 5,
            "min_dimension": 16,
            "failures": [],
            "warnings": [],
            "blocking_warnings": [],
            "simulation_note": "Image URLs are not fetched during simulation; real image_url_check.py must validate production CDN URLs.",
        }
    )


def build_simulated_marqo_resource_report() -> dict[str, Any]:
    settings_data = {
        "type": "structured",
        "model": "no_model",
        "modelProperties": {"dimensions": 2048, "type": "no_model"},
        "normalizeEmbeddings": False,
        "tensorFields": ["qwen_text_vector", "qwen_image_vector"],
        "allFields": [
            {"name": "qwen_text_vector", "type": "custom_vector"},
            {"name": "qwen_image_vector", "type": "custom_vector"},
        ],
    }
    qwen_health = {
        "ok": True,
        "status": 200,
        "elapsed_ms": 12.0,
        "data": {
            "ready": True,
            "model": "Qwen/Qwen3-VL-Embedding-2B",
            "device": "cuda",
            "dimensions": 2048,
            "loadError": None,
        },
    }
    qwen_embedding_probe = {
        "ok": True,
        "status": 200,
        "elapsed_ms": 45.0,
        "data": {
            "model": "Qwen/Qwen3-VL-Embedding-2B",
            "device": "cuda",
            "dimensions": 2048,
            "count": 1,
            "embedding_count": 1,
            "embedding_sample_dimensions": 2048,
        },
    }
    qwen_image_embedding_probe = {
        "ok": True,
        "status": 200,
        "elapsed_ms": 55.0,
        "data": {
            "model": "Qwen/Qwen3-VL-Embedding-2B",
            "device": "cuda",
            "dimensions": 2048,
            "count": 1,
            "embedding_count": 1,
            "embedding_sample_dimensions": 2048,
        },
    }
    return mark_simulated(
        {
            "ok": True,
            "marqo_url": "http://marqo-api:8882",
            "index": "haeorum-products-simulation",
            "container": "marqo-api",
            "storage_container": "vespa",
            "storage_path": "/opt/vespa/var",
            "qwen_embedding_url": "http://qwen-embedding:8098",
            "checks": [
                {"name": "marqo_health", "ok": True},
                {"name": "marqo_index_stats", "ok": True},
                {"name": "marqo_index_settings", "ok": True},
                {"name": "marqo_index_settings_contract", "ok": True},
                {"name": "qwen_health", "ok": True},
                {"name": "qwen_embedding_probe", "ok": True},
                {"name": "qwen_image_embedding_probe", "ok": True},
                {"name": "qwen_embedding_contract", "ok": True},
                {"name": "docker_stats", "ok": True},
                {"name": "resource_thresholds", "ok": True},
                {"name": "storage_usage", "ok": True},
                {"name": "storage_thresholds", "ok": True},
            ],
            "health": {"ok": True, "status": "ok", "elapsed_ms": 35.0},
            "index_stats": {"ok": True, "data": {"numberOfDocuments": 300}},
            "index_settings": {"ok": True, "data": settings_data},
            "index_settings_contract": index_settings_contract(
                settings_data,
                expected_model="Marqo/marqo-ecommerce-embeddings-L",
                embedding_backend="qwen",
                qwen_model="Qwen/Qwen3-VL-Embedding-2B",
                qwen_embedding_dimensions=2048,
            ),
            "qwen_health": qwen_health,
            "qwen_embedding_probe": qwen_embedding_probe,
            "qwen_image_embedding_probe": qwen_image_embedding_probe,
            "qwen_embedding_contract": qwen_embedding_contract(
                qwen_health,
                qwen_embedding_probe,
                qwen_image_embedding_probe,
                expected_model="Qwen/Qwen3-VL-Embedding-2B",
                expected_dimensions=2048,
            ),
            "docker_stats": {
                "ok": True,
                "cpu_percent": 35.0,
                "memory_usage_bytes": 2_147_483_648,
                "memory_limit_bytes": 8_589_934_592,
                "memory_percent": 25.0,
            },
            "resource_thresholds": {
                "ok": True,
                "max_cpu_percent": 90.0,
                "max_memory_percent": 85.0,
                "cpu_percent": 35.0,
                "memory_percent": 25.0,
                "problems": [],
            },
            "storage_usage": {
                "ok": True,
                "container": "vespa",
                "path": "/opt/vespa/var",
                "filesystem": "/dev/simulated-vespa",
                "total_bytes": 274_877_906_944,
                "used_bytes": 96_207_267_430,
                "available_bytes": 178_670_639_514,
                "used_percent": 35.0,
                "mounted_on": "/opt/vespa/var",
                "problems": [],
            },
            "storage_thresholds": {
                "ok": True,
                "max_storage_percent": 85.0,
                "min_available_bytes": 10 * 1024**3,
                "used_percent": 35.0,
                "available_bytes": 178_670_639_514,
                "problems": [],
            },
            "simulation_note": "Synthetic Marqo resource report; real check must query Marqo and docker stats on the target host.",
        }
    )


def build_simulated_server_preflight_report() -> dict[str, Any]:
    checks = [
        {"name": "linux_host", "ok": True, "required": True, "platform": "Linux"},
        {"name": "supported_linux_release", "ok": True, "required": True, "id": "ubuntu", "version_id": "22.04"},
        {"name": "python_version", "ok": True, "version": "3.11.9", "minimum": "3.11"},
        {"name": "python_modules", "ok": True, "required": ["fastapi", "uvicorn", "pydantic", "multipart", "PIL", "redis", "psutil", "pyodbc"], "missing": []},
        {"name": "odbc_driver", "ok": True, "required": True, "expected_driver": "ODBC Driver 18 for SQL Server", "drivers": ["ODBC Driver 18 for SQL Server"]},
        {
            "name": "host_resources",
            "ok": True,
            "cpu_count": 8,
            "memory_total_gb": 16,
            "disk_free_gb": 120,
            "open_file_limit_soft": 65535,
            "open_file_limit_hard": 65535,
            "requirements": {
                "min_cpu": 4,
                "min_memory_gb": 8,
                "min_disk_free_gb": 20,
                "min_open_files": 65535,
            },
            "problems": [],
        },
        {"name": "docker", "ok": True, "required": True, "version": "26.1.0", "minimum": "24.0.0"},
        {"name": "docker_compose", "ok": True, "required": True, "output": "Docker Compose version v2.27.0"},
    ]
    return mark_simulated(
        {
            "ok": True,
            "role": "api",
            "requirements": {"min_cpu": 4, "min_memory_gb": 8, "min_disk_free_gb": 20, "min_open_files": 65535},
            "failed_checks": [],
            "checks": checks,
            "simulation_note": "Target host preflight is synthesized because this workstation is not the Linux deployment host.",
        }
    )


def build_simulated_widget_dom_report(mall_config: dict[str, Any]) -> dict[str, Any]:
    checked_sites = []
    for mall in mall_config["malls"][:3]:
        checked_sites.append(
            {
                "name": f"{mall['mall_id']}-simulated-widget",
                "mallId": mall["mall_id"],
                "responsiveLayout": True,
                "dragDropUpload": True,
                "oversizedImageRejected": True,
                "smallImageRejected": True,
                "damagedImageRejected": True,
                "validImagePreview": True,
                "imageRemoveClearsPayload": True,
                "keyboardCloseRestoresFocus": True,
                "keyboardTrapCyclesFocus": True,
                "loadingState": True,
                "modalCloseControls": True,
                "rateLimitErrorClearsStaleResults": True,
                "resultFieldsRendered": True,
                "modalCopyComplete": True,
                "supportedImageFormats": True,
                "resultSectionHeadings": True,
                "imageAndDetailClickLogging": True,
                "cameraIconRendered": True,
                "triggerTitle": "AI검색",
                "triggerAriaLabel": "AI 상품 검색",
                "prefilledQuery": "검은 우산",
                "refreshedQuery": "스텐 텀블러",
            }
        )
    return mark_simulated(
        {
            "ok": True,
            "widget_config": {
                "conflictingMallSiteIdRejected": True,
                "unsafeMallIdRejected": True,
                "unsafeApiBaseUrlRejected": True,
                "scriptSrcApiBaseUrlFallback": True,
                "scriptDataAttributeAutoInit": True,
                "unsafeProductUrlsNeutralized": True,
                "deferredInitUntilDomReady": True,
                "repeatedInitReplacesWidget": True,
                "cssSpecialIdSelectorFallback": True,
                "complexCssSpecialIdSelectorFallback": True,
                "ambiguousExplicitSelectorRejected": True,
                "dynamicAutoAttachAfterDomMutation": True,
            },
            "checked_sites": checked_sites,
            "simulation_note": "Synthetic widget DOM report; node scripts/widget_dom_check.js still verifies the actual widget implementation locally.",
        }
    )


def simulated_standard_evidence_paths(paths: SimulationPaths) -> dict[str, Path]:
    return {
        "api_smoke": paths.simulated_api_smoke_report,
        "mssql_export": paths.simulated_mssql_export_report,
        "poc_dataset": paths.output_dir / "poc-dataset.json",
        "mssql_view": paths.simulated_mssql_view_report,
        "image_urls": paths.simulated_image_url_report,
        "quality_report": paths.simulated_quality_report,
        "csv_poc_index": paths.simulated_csv_index_report,
        "mall_config_build": paths.simulated_mall_config_build_report,
        "mall_config": paths.output_dir / "mall-config-check.json",
        "marqo_resource": paths.simulated_marqo_resource_report,
        "server_preflight": paths.simulated_server_preflight_report,
        "env_preflight": paths.output_dir / "env-check.json",
        "load_text_100_concurrent": paths.simulated_load_text_report,
        "load_image_30_concurrent": paths.simulated_load_image_report,
        "load_mixed_30_concurrent": paths.simulated_load_mixed_report,
        "load_mixed_traffic_850_active_users": paths.simulated_load_mixed_traffic_report,
        "api_scale_comparison": paths.simulated_api_scale_report,
        "representative_mall_sites": paths.simulated_representative_sites_report,
        "security": paths.simulated_security_report,
    }


def build_simulated_evidence_slot_report(paths: SimulationPaths) -> dict[str, Any]:
    slots = {}
    for name, path in simulated_standard_evidence_paths(paths).items():
        exists = path.exists()
        marked = False
        ok = None
        if exists:
            data = json.loads(path.read_text(encoding="utf-8"))
            ok = data.get("ok")
            marked = data.get("simulation_only") is True and data.get("simulation_marker") == SIMULATION_MARKER
        slots[name] = {"path": str(path), "exists": exists, "simulation_marked": marked, "ok": ok}
    missing = sorted(name for name, slot in slots.items() if not slot["exists"])
    unmarked = sorted(name for name, slot in slots.items() if slot["exists"] and not slot["simulation_marked"])
    return {
        "ok": not missing and not unmarked,
        "slot_count": len(slots),
        "missing": missing,
        "unmarked": unmarked,
        "slots": slots,
    }


def write_operational_evidence_config(
    path: Path,
    paths: SimulationPaths,
    mall_config: dict[str, Any],
    reference_images: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    data = {
        "simulation_marker": SIMULATION_MARKER,
        "simulation_only": True,
        "not_operational_readiness": True,
        "base_url": "https://ai-search.haeorumgift.com",
        "mall_id": first_mall["mall_id"],
        "api_key_env": "HAEORUM_PUBLIC_API_KEY",
        "admin_key_env": "HAEORUM_ADMIN_API_KEY",
        "origin": first_mall["allowed_origins"][0],
        "click_rate_limit_probe_count": 601,
        "expected_malls": len(mall_config["malls"]),
        "required_sites": 3,
        "products_csv": str(paths.products_csv),
        "poc_products_csv": str(paths.poc_products_csv),
        "mall_config_source": str(paths.mall_source_csv),
        "mall_config": str(paths.malls_json),
        "representative_sites_config": str(paths.representative_sites_json),
        "mssql_connection_string_env": "HAEORUM_MSSQL_READONLY_CONNECTION_STRING",
        "mssql_query": SIMULATED_MSSQL_QUERY,
        "marqo": {
            "url": "http://marqo-api:8882",
            "index": "haeorum-products-simulation",
            "container": "marqo-api",
            "qwen_embedding_url": "http://qwen-embedding:8098",
        },
        "env_check": {
            "env_file": str(paths.env_file),
            "role": "api",
            "api_server_count": 2,
        },
        "quality": {
            "cases_file": str(paths.quality_cases_json),
            "min_products": 300,
            "max_text_ms": 3000,
            "max_image_ms": 5000,
            "max_mixed_ms": 5000,
        },
        "load": {
            "image_file": reference_images["load"]["path"],
            "image_files": [
                reference_images["load_2"]["path"],
                reference_images["load_3"]["path"],
            ],
            "mixed_traffic": {
                "active_users": 850,
                "requests": 850,
                "concurrency": 100,
                "mall_sample_size": 50,
                "p95_ms": 5000,
            },
        },
        "api_scale": {
            "single_report": str(paths.api_scale_single_report),
            "multi_report": str(paths.api_scale_multi_report),
        },
        "security": {
            "sync_alerting_configured": True,
            "mssql_ip_restricted": True,
            "nginx_config": str(ROOT / "deploy" / "nginx" / "haeorum-ai-search.conf"),
            "systemd_service": str(ROOT / "deploy" / "systemd" / "haeorum-ai-search.service"),
            "sync_systemd_service": str(ROOT / "deploy" / "systemd" / "haeorum-ai-sync.service"),
            "reindex_systemd_service": str(ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.service"),
            "reindex_systemd_timer": str(ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.timer"),
            "logrotate_config": str(ROOT / "deploy" / "logrotate" / "haeorum-ai-search"),
            "note": "simulation config only; install real Nginx/systemd/logrotate files on the target host",
        },
    }
    write_json(path, data)
    return data


def analyze_generated_view(rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns = set(PRODUCT_COLUMNS)
    missing_columns = [column for column in REQUIRED_MSSQL_VIEW_COLUMNS if column not in columns]
    parsed_products, parse_errors = parse_products(rows)
    product_ids = [str(row["product_id"]) for row in rows]
    product_keys = [product_identity_key(product.mall_id, product.product_id) for product in parsed_products]
    duplicate_ids = sorted(
        product_identity_label(*key)
        for key, count in Counter(product_keys).items()
        if key[1] and count > 1
    )
    edge_product_ids = sorted(
        product_id
        for product_id in product_ids
        if any(char in product_id for char in ["/", "&", " ", "?", "#"])
    )
    active_products = [product for product in parsed_products if product.active]
    active_missing_category = [product.product_id for product in active_products if not product.category]
    active_missing_image = [product.product_id for product in active_products if not product.image_url]
    active_missing_product_url = [product.product_id for product in active_products if not product.product_url]
    active_missing_mall_id = [product.product_id for product in active_products if not product.mall_id]
    oversized_product_ids = [
        row["product_id"] for row in rows if len(str(row.get("product_id") or "").strip()) > MAX_PRODUCT_ID_LENGTH
    ]
    active_unsafe_image_url = [
        product.product_id
        for product in active_products
        if product.image_url and safe_absolute_http_url(product.image_url) is None
    ]
    active_non_https_image_url = [
        product.product_id
        for product in active_products
        if product.image_url
        and safe_absolute_http_url(product.image_url) is not None
        and not safe_absolute_http_url_uses_https(product.image_url)
    ]
    active_unsafe_product_url = [
        product.product_id
        for product in active_products
        if product.product_url and safe_product_source_url(product.product_url) is None
    ]
    active_product_url_product_id_mismatch = [
        product.product_id
        for product in active_products
        if product.product_url
        and safe_product_source_url(product.product_url) is not None
        and not product_url_contains_product_id(product.product_url, product.product_id)
    ]
    active_negative_price = [
        product.product_id
        for product in active_products
        if product.price is not None and float(product.price) < 0
    ]
    missing_updated_at = [row["product_id"] for row in rows if not str(row.get("updated_at") or "").strip()]
    invalid_updated_at = []
    future_updated_at = []
    future_limit = datetime.now(timezone.utc) + timedelta(seconds=300)
    for row in rows:
        if not str(row.get("updated_at") or "").strip():
            continue
        try:
            parsed_updated_at = parse_sync_datetime(row.get("updated_at"), f"updated_at for product {row.get('product_id')}")
        except ValueError:
            invalid_updated_at.append(row["product_id"])
            continue
        if parsed_updated_at > future_limit:
            future_updated_at.append(row["product_id"])
    category_counts: dict[str, int] = {}
    mall_counts: dict[str, int] = {}
    for product in active_products:
        category_counts[product.category] = category_counts.get(product.category, 0) + 1
        mall_counts[str(product.mall_id or "")] = mall_counts.get(str(product.mall_id or ""), 0) + 1
    domain_filter_coverage = domain_filter_coverage_report(parsed_products)
    problems = []
    if missing_columns:
        problems.append("missing required view columns")
    if parse_errors:
        problems.append("rows contain parse errors")
    if duplicate_ids:
        problems.append("duplicate product IDs")
    if len(active_products) < 300:
        problems.append("fewer than 300 active rows")
    if len(category_counts) < 10:
        problems.append("fewer than 10 active categories")
    if active_missing_image:
        problems.append("active rows missing main_image_url")
    if active_missing_category:
        problems.append("active rows missing category_name")
    if active_missing_product_url:
        problems.append("active rows missing product_url")
    if active_missing_mall_id:
        problems.append("active rows missing mall_id")
    if active_negative_price:
        problems.append("active rows have negative price")
    if oversized_product_ids:
        problems.append("rows have oversized product_id")
    if active_unsafe_image_url:
        problems.append("active rows have unsafe main_image_url")
    if active_non_https_image_url:
        problems.append("active rows have non-HTTPS main_image_url")
    if active_unsafe_product_url:
        problems.append("active rows have unsafe product_url")
    if active_product_url_product_id_mismatch:
        problems.append("active rows have product_url that does not contain product_id")
    if not domain_filter_coverage["ok"]:
        problems.append("active rows missing domain filter fields")
    if missing_updated_at:
        problems.append("rows missing updated_at")
    if invalid_updated_at:
        problems.append("rows have invalid updated_at")
    if future_updated_at:
        problems.append("rows have future updated_at")
    return {
        "ok": not problems,
        "simulation_only": True,
        "required_columns": REQUIRED_MSSQL_VIEW_COLUMNS,
        "missing_columns": missing_columns,
        "total_rows": len(rows),
        "parsed_rows": len(parsed_products),
        "parse_error_count": len(parse_errors),
        "parse_errors": parse_errors[:20],
        "active_rows": len(active_products),
        "inactive_or_hidden_rows": len(parsed_products) - len(active_products),
        "category_count": len(category_counts),
        "mall_count": len(mall_counts),
        "duplicate_product_ids": duplicate_ids[:20],
        "edge_product_id_count": len(edge_product_ids),
        "edge_product_ids": edge_product_ids[:20],
        "active_missing_category_count": len(active_missing_category),
        "active_missing_category_product_ids": active_missing_category[:20],
        "active_missing_image_url_count": len(active_missing_image),
        "active_missing_image_url_product_ids": active_missing_image[:20],
        "active_missing_product_url_count": len(active_missing_product_url),
        "active_missing_product_url_product_ids": active_missing_product_url[:20],
        "active_missing_mall_id_count": len(active_missing_mall_id),
        "active_missing_mall_id_product_ids": active_missing_mall_id[:20],
        "active_negative_price_count": len(active_negative_price),
        "active_negative_price_product_ids": active_negative_price[:20],
        "oversized_product_id_count": len(oversized_product_ids),
        "oversized_product_ids": oversized_product_ids[:20],
        "active_unsafe_image_url_count": len(active_unsafe_image_url),
        "active_unsafe_image_url_product_ids": active_unsafe_image_url[:20],
        "active_non_https_image_url_count": len(active_non_https_image_url),
        "active_non_https_image_url_product_ids": active_non_https_image_url[:20],
        "active_unsafe_product_url_count": len(active_unsafe_product_url),
        "active_unsafe_product_url_product_ids": active_unsafe_product_url[:20],
        "active_product_url_product_id_mismatch_count": len(active_product_url_product_id_mismatch),
        "active_product_url_product_id_mismatch_product_ids": active_product_url_product_id_mismatch[:20],
        "missing_updated_at_count": len(missing_updated_at),
        "missing_updated_at_product_ids": missing_updated_at[:20],
        "invalid_updated_at_count": len(invalid_updated_at),
        "invalid_updated_at_product_ids": invalid_updated_at[:20],
        "future_updated_at_count": len(future_updated_at),
        "future_updated_at_product_ids": future_updated_at[:20],
        "domain_filter_coverage": domain_filter_coverage,
        "problems": problems,
    }


def readonly_sqlite_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def build_simulated_mssql_view_report(paths: SimulationPaths, sample_size: int = 50) -> dict[str, Any]:
    started = time.perf_counter()
    permission_report = analyze_readonly_permissions(
        {
            "db_datareader": 1,
            "db_datawriter": 0,
            "db_owner": 0,
            "db_ddladmin": 0,
            "db_securityadmin": 0,
            "db_accessadmin": 0,
            "db_backupoperator": 0,
        },
        [{"permission_name": "SELECT", "state_desc": "GRANT"}],
    )
    readonly_write_probe = {"ok": False, "attempted": True, "blocked": False, "error": ""}
    incremental_probe = {"ok": False, "updated_rows": 0, "single_product_lookup_ok": False}
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    with closing(sqlite3.connect(readonly_sqlite_uri(paths.simulated_mssql_db), uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute("SELECT * FROM v_ai_search_products LIMIT ?", (sample_size,))
        columns = [column[0] for column in cursor.description]
        rows = [dict(row) for row in cursor.fetchall()]
        cutoff = "2026-05-15T00:00:00Z"
        updated_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM v_ai_search_products WHERE updated_at >= ?",
                (cutoff,),
            ).fetchone()[0]
        )
        first_product_id = str(rows[0].get("product_id") or "") if rows else ""
        single_product_count = 0
        if first_product_id:
            single_product_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM v_ai_search_products WHERE product_id = ?",
                    (first_product_id,),
                ).fetchone()[0]
            )
        incremental_probe = {
            "ok": updated_rows > 0 and single_product_count == 1,
            "updated_at_cutoff": cutoff,
            "updated_rows": updated_rows,
            "product_id_column": "product_id",
            "updated_at_column": "updated_at",
            "single_product_lookup_product_id": first_product_id,
            "single_product_lookup_count": single_product_count,
            "single_product_lookup_ok": single_product_count == 1,
        }
        try:
            connection.execute("CREATE TABLE readonly_write_probe (id INTEGER)")
            connection.commit()
        except sqlite3.DatabaseError as exc:
            readonly_write_probe = {
                "ok": True,
                "attempted": True,
                "blocked": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
    permission_report["readonly_write_probe"] = readonly_write_probe
    permission_report["ok"] = permission_report["ok"] and readonly_write_probe["ok"]
    column_report = validate_columns(columns)
    sample_report = analyze_sample(rows)
    sample_quality_report = validate_sample_report(sample_report)
    return mark_simulated(
        {
            "ok": (
                column_report["ok"]
                and sample_quality_report["ok"]
                and permission_report["ok"]
                and incremental_probe["ok"]
            ),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "connection_kind": "sqlite_readonly_simulated_mssql_contract",
            "database_file": str(paths.simulated_mssql_db),
            "mssql_view_name": "dbo.v_ai_search_products",
            "sqlite_view_name": "v_ai_search_products",
            "query_configured": True,
            "query_fingerprint": query_fingerprint_report(SIMULATED_MSSQL_QUERY),
            "sample_size": sample_size,
            "permission_report": permission_report,
            "column_report": column_report,
            "sample_report": sample_report,
            "sample_quality_report": sample_quality_report,
            "incremental_probe": incremental_probe,
            "simulation_note": "SQLite is used only to exercise the expected MSSQL View shape, sample parsing, read-only posture, and incremental lookup logic.",
        }
    )


def build_simulated_mssql_export_report(
    paths: SimulationPaths,
    rows: list[dict[str, Any]],
    mall_config_path: str | Path = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    source_columns = list(rows[0].keys()) if rows else []
    column_report = validate_columns(source_columns)
    products, parse_errors = parse_products(rows)
    duplicate_product_ids = sorted(
        product_identity_label(*key)
        for key, count in Counter(product_identity_key(product.mall_id, product.product_id) for product in products).items()
        if key[1] and count > 1
    )
    updated_at_report = updated_at_quality(products)
    active_products = [product for product in products if product.active]
    active_missing_category_ids = sorted(product.product_id for product in active_products if not product.category)
    active_missing_image_url_ids = sorted(product.product_id for product in active_products if not product.image_url)
    active_missing_product_url_ids = sorted(product.product_id for product in active_products if not product.product_url)
    active_missing_mall_id_ids = sorted(product.product_id for product in active_products if not product.mall_id)
    active_negative_price_ids = sorted(
        product.product_id
        for product in active_products
        if product.price is not None and float(product.price) < 0
    )
    active_unsafe_image_url_ids = sorted(
        product.product_id
        for product in active_products
        if product.image_url and safe_absolute_http_url(product.image_url) is None
    )
    active_non_https_image_url_ids = sorted(
        product.product_id
        for product in active_products
        if product.image_url
        and safe_absolute_http_url(product.image_url) is not None
        and not safe_absolute_http_url_uses_https(product.image_url)
    )
    active_unsafe_product_url_ids = sorted(
        product.product_id
        for product in active_products
        if product.product_url and safe_product_source_url(product.product_url) is None
    )
    active_product_url_product_id_mismatch_ids = sorted(
        product.product_id
        for product in active_products
        if product.product_url
        and safe_product_source_url(product.product_url) is not None
        and not product_url_contains_product_id(product.product_url, product.product_id)
    )
    mall_alignment = mall_config_alignment_report(products, mall_config_path)
    domain_filter_coverage = domain_filter_coverage_report(products)
    inactive_product_ids = sorted(product.product_id for product in products if not product.active)
    source_deletion_signal_ok = bool(inactive_product_ids)
    fetch_size = 1000
    fetch_batches = math.ceil(len(rows) / fetch_size) if rows else 0
    return mark_simulated(
        {
            "ok": not parse_errors
            and not duplicate_product_ids
            and not updated_at_report["missing"]
            and not updated_at_report["invalid"]
            and not updated_at_report["future"]
            and not active_missing_category_ids
            and not active_missing_image_url_ids
            and not active_missing_product_url_ids
            and not active_missing_mall_id_ids
            and not active_negative_price_ids
            and not active_unsafe_image_url_ids
            and not active_non_https_image_url_ids
            and not active_unsafe_product_url_ids
            and not active_product_url_product_id_mismatch_ids
            and mall_alignment["ok"]
            and domain_filter_coverage["ok"]
            and source_deletion_signal_ok
            and column_report["ok"],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "query_configured": True,
            "query_fingerprint": query_fingerprint_report(SIMULATED_MSSQL_QUERY),
            "source_columns": source_columns,
            "column_report": column_report,
            "limit": 0,
            "since_configured": False,
            "output_csv": str(paths.products_csv),
            "output_csv_fingerprint": file_fingerprint(paths.products_csv),
            "product_id_column": "product_id",
            "updated_at_column": "updated_at",
            "fetch_size": fetch_size,
            "fetch_batches": fetch_batches,
            "max_fetch_batch_rows": min(fetch_size, len(rows)) if rows else 0,
            "batched_fetch": True,
            "streaming_parse": True,
            "mall_config_alignment": mall_alignment,
            "domain_filter_coverage": domain_filter_coverage,
            "active_unknown_mall_id_count": mall_alignment["active_unknown_mall_id_count"],
            "active_unknown_mall_ids": mall_alignment["active_unknown_mall_ids"],
            "active_product_url_mismatch_count": mall_alignment["active_product_url_mismatch_count"],
            "active_product_url_mismatches": mall_alignment["active_product_url_mismatches"],
            "rows_read": len(rows),
            "csv_rows_written": len(products),
            "streamed_product_csv": True,
            "retained_product_rows": 0,
            "exported_products": len(products),
            "active_products": len(active_products),
            "inactive_products": len(inactive_product_ids),
            "source_deletion_signal_ok": source_deletion_signal_ok,
            "source_deletion_signal_count": len(inactive_product_ids),
            "source_deletion_signal_product_ids": inactive_product_ids[:20],
            "duplicate_product_ids": duplicate_product_ids[:50],
            "missing_updated_at_count": len(updated_at_report["missing"]),
            "missing_updated_at_product_ids": updated_at_report["missing"][:20],
            "invalid_updated_at_count": len(updated_at_report["invalid"]),
            "invalid_updated_at_product_ids": updated_at_report["invalid"][:20],
            "future_updated_at_count": len(updated_at_report["future"]),
            "future_updated_at_product_ids": updated_at_report["future"][:20],
            "updated_at_min": updated_at_report["min"],
            "updated_at_max": updated_at_report["max"],
            "updated_at_reference_now": updated_at_report["reference_now"],
            "updated_at_max_future_skew_seconds": updated_at_report["max_future_skew_seconds"],
            "active_missing_category_count": len(active_missing_category_ids),
            "active_missing_category_product_ids": active_missing_category_ids[:20],
            "active_missing_image_url_count": len(active_missing_image_url_ids),
            "active_missing_image_url_product_ids": active_missing_image_url_ids[:20],
            "active_missing_product_url_count": len(active_missing_product_url_ids),
            "active_missing_product_url_product_ids": active_missing_product_url_ids[:20],
            "active_missing_mall_id_count": len(active_missing_mall_id_ids),
            "active_missing_mall_id_product_ids": active_missing_mall_id_ids[:20],
            "active_negative_price_count": len(active_negative_price_ids),
            "active_negative_price_product_ids": active_negative_price_ids[:20],
            "active_unsafe_image_url_count": len(active_unsafe_image_url_ids),
            "active_unsafe_image_url_product_ids": active_unsafe_image_url_ids[:20],
            "active_non_https_image_url_count": len(active_non_https_image_url_ids),
            "active_non_https_image_url_product_ids": active_non_https_image_url_ids[:20],
            "active_unsafe_product_url_count": len(active_unsafe_product_url_ids),
            "active_unsafe_product_url_product_ids": active_unsafe_product_url_ids[:20],
            "active_product_url_product_id_mismatch_count": len(active_product_url_product_id_mismatch_ids),
            "active_product_url_product_id_mismatch_product_ids": active_product_url_product_id_mismatch_ids[:20],
            "parse_errors": parse_errors[:50],
            "simulation_note": "Rows were generated locally and parsed through the same product normalization path used by MSSQL export.",
        }
    )


def remap_mssql_alias_row(row: dict[str, Any], aliases: dict[str, str], variant: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for canonical, alias in aliases.items():
        value = row.get(canonical, "")
        if variant == "korean_export":
            if canonical == "status":
                value = "품절" if str(row.get("status") or "").strip().lower() in {"soldout", "sold_out"} else "판매중"
            elif canonical == "is_deleted":
                value = "예" if str(row.get("is_deleted") or "").strip().lower() in {"1", "true", "yes", "y"} else "아니오"
            elif canonical == "display_yn":
                value = "아니오" if str(row.get("display_yn") or "").strip().upper() == "N" else "예"
        output[alias] = value
    return output


def product_alias_signature(product: ProductDocument) -> dict[str, Any]:
    return {
        "product_id": product.product_id,
        "name": product.name,
        "category": product.category,
        "price": product.price,
        "price_min": product.price_min,
        "price_max": product.price_max,
        "image_url": product.image_url,
        "product_url": product.product_url,
        "mall_id": product.mall_id,
        "active": product.active,
        "min_order_qty": product.min_order_qty,
        "delivery_days": product.delivery_days,
        "print_methods": product.print_methods,
        "materials": product.materials,
        "colors": product.colors,
    }


def build_simulated_mssql_alias_compatibility_report(
    rows: list[dict[str, Any]],
    sample_size: int = 80,
) -> dict[str, Any]:
    started = time.perf_counter()
    source_rows = rows[:sample_size]
    canonical_products, canonical_errors = parse_products(source_rows)
    canonical_by_identity = {
        product_identity_label(product.mall_id, product.product_id): product_alias_signature(product)
        for product in canonical_products
    }
    variants = []
    for name, aliases in MSSQL_ALIAS_SCHEMA_VARIANTS.items():
        alias_rows = [remap_mssql_alias_row(row, aliases, name) for row in source_rows]
        columns = list(aliases.values())
        products, parse_errors = parse_products(alias_rows)
        parsed_by_identity = {
            product_identity_label(product.mall_id, product.product_id): product_alias_signature(product)
            for product in products
        }
        missing_identities = sorted(set(canonical_by_identity) - set(parsed_by_identity))
        extra_identities = sorted(set(parsed_by_identity) - set(canonical_by_identity))
        mismatched_identities = sorted(
            identity
            for identity in set(canonical_by_identity).intersection(parsed_by_identity)
            if canonical_by_identity[identity] != parsed_by_identity[identity]
        )
        column_report = validate_columns(columns)
        sample_report = analyze_sample(alias_rows)
        sample_quality_report = validate_sample_report(sample_report)
        checks = {
            "column_report_ok": column_report.get("ok") is True,
            "sample_quality_ok": sample_quality_report.get("ok") is True,
            "parse_errors_empty": not parse_errors,
            "identity_sets_match": not missing_identities and not extra_identities,
            "product_signatures_match": not mismatched_identities,
            "active_count_matches": sum(1 for product in products if product.active)
            == sum(1 for product in canonical_products if product.active),
        }
        variants.append(
            {
                "name": name,
                "ok": all(checks.values()),
                "row_count": len(alias_rows),
                "column_count": len(columns),
                "columns": columns,
                "checks": checks,
                "column_report": column_report,
                "sample_report": {
                    "sample_rows": sample_report.get("sample_rows"),
                    "parsed_rows": sample_report.get("parsed_rows"),
                    "active_rows": sample_report.get("active_rows"),
                    "parse_error_count": len(sample_report.get("parse_errors") or []),
                    "missing_product_id_rows": sample_report.get("missing_product_id_rows"),
                    "missing_product_name_rows": sample_report.get("missing_product_name_rows"),
                    "missing_updated_at_rows": sample_report.get("missing_updated_at_rows"),
                    "domain_filter_coverage": sample_report.get("domain_filter_coverage"),
                },
                "sample_quality_report": sample_quality_report,
                "parse_error_count": len(parse_errors),
                "parse_errors": parse_errors[:10],
                "missing_identities": missing_identities[:20],
                "extra_identities": extra_identities[:20],
                "mismatched_identities": mismatched_identities[:20],
            }
        )
    return mark_simulated(
        {
            "ok": not canonical_errors and all(variant["ok"] for variant in variants),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "sample_size": sample_size,
            "canonical_parse_error_count": len(canonical_errors),
            "canonical_product_count": len(canonical_products),
            "variant_count": len(variants),
            "variants": variants,
            "simulation_note": (
                "Exercises schema-drift handling for legacy English and Korean MSSQL/export column names through "
                "the same row parser and View sample validator used by operational evidence collection."
            ),
        }
    )


def build_representative_response_mall_identity_risk_report() -> dict[str, Any]:
    expected_mall_id = "shop0001"
    wrong_mall_id = "shop9999"
    meta = {
        "query_type": "text",
        "elapsed_ms": 120.0,
        "engine": "marqo",
        "limit": 20,
        "offset": 0,
        "has_more": False,
        "next_offset": None,
        "mall_id": wrong_mall_id,
        "text_weight": 1.0,
        "image_weight": None,
        "low_confidence": False,
        "notice": None,
    }
    products = [
        {
            "section": "top",
            "index": 0,
            "product": {
                "product_id": "SIM-WRONG-META-MALL",
                "name": "대표 사이트 mall_id 불일치 상품",
                "category": "우산",
                "price": 12000,
                "image_url": "https://cdn.haeorumgift.com/sim/products/SIM-WRONG-META-MALL.jpg",
                "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-WRONG-META-MALL",
                "score": 0.91,
                "score_percent": 91.0,
                "mall_id": wrong_mall_id,
                "source_scores": {"text": 0.91},
            },
        },
        {
            "section": "items",
            "index": 0,
            "product": {
                "product_id": "SIM-MISSING-MALL",
                "name": "대표 사이트 mall_id 누락 상품",
                "category": "우산",
                "price": 8500,
                "image_url": "https://cdn.haeorumgift.com/sim/products/SIM-MISSING-MALL.jpg",
                "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-MISSING-MALL",
                "score": 0.82,
                "score_percent": 82.0,
                "mall_id": None,
                "source_scores": {"text": 0.82},
            },
        },
    ]
    result_contract_problems: list[str] = []
    result_mall_id_mismatches = []
    for item in products:
        product = item["product"]
        section = str(item["section"])
        index = int(item["index"])
        result_contract_problems.extend(
            validate_representative_result_item_shape(product, section, index, expected_mall_id)
        )
        result_mall_id = str(product.get("mall_id") or "").strip()
        if result_mall_id != expected_mall_id:
            result_mall_id_mismatches.append(
                {
                    "section": section,
                    "index": index,
                    "product_id": product.get("product_id"),
                    "mall_id": result_mall_id or None,
                    "expected_mall_id": expected_mall_id,
                }
            )
    meta_contract_problems = validate_representative_meta_shape(meta, "text", expected_mall_id)
    return {
        "name": "representative_response_mall_identity",
        "ok": not meta_contract_problems and not result_contract_problems and not result_mall_id_mismatches,
        "expected_mall_id": expected_mall_id,
        "meta_mall_id": meta.get("mall_id"),
        "meta_contract_problems": meta_contract_problems,
        "result_contract_problems": result_contract_problems,
        "result_mall_id_mismatches": result_mall_id_mismatches,
    }


def build_api_load_response_mall_identity_risk_report() -> dict[str, Any]:
    expected_mall_id = "shop0001"
    wrong_mall_id = "shop9999"
    payload = {"mall_id": expected_mall_id, "q": "검은 우산", "limit": 20}
    expected_product_url_prefix = "https://shop0001.haeorumgift.com/product_view.asp"

    def product(product_id: str, mall_id: str, product_url: str | None = None) -> dict[str, Any]:
        return {
            "product_id": product_id,
            "name": "API mall_id 불일치 상품",
            "category": "우산",
            "price": 12000,
            "image_url": f"https://cdn.haeorumgift.com/sim/products/{product_id}.jpg",
            "product_url": product_url
            or f"https://{expected_mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
            "score": 0.91,
            "score_percent": 91.0,
            "mall_id": mall_id,
            "source_scores": {"text": 0.91},
        }

    def response(meta_mall_id: str, top_mall_id: str) -> dict[str, Any]:
        return {
            "top": [product("SIM-WRONG-API-MALL", top_mall_id)],
            "items": [product("SIM-RELATED-API-MALL", expected_mall_id)],
            "suggested_categories": ["우산"],
            "meta": {
                "query_type": "text",
                "elapsed_ms": 120.0,
                "engine": "marqo",
                "limit": 20,
                "offset": 0,
                "has_more": False,
                "next_offset": None,
                "mall_id": meta_mall_id,
                "text_weight": 1.0,
                "image_weight": None,
                "low_confidence": False,
                "notice": None,
            },
        }

    def api_smoke_error(data: dict[str, Any]) -> str:
        try:
            validate_api_smoke_search_response(
                data,
                "text",
                "api smoke mall identity",
                expected_mall_id,
                expected_product_url_prefix,
            )
        except AssertionError as exc:
            return str(exc)
        return ""

    def load_error(data: dict[str, Any]) -> str:
        ok, message = validate_load_search_response(data, payload, expected_product_url_prefix)
        return "" if ok else message

    meta_mismatch = response(wrong_mall_id, expected_mall_id)
    result_mismatch = response(expected_mall_id, wrong_mall_id)
    product_url_mismatch = response(expected_mall_id, expected_mall_id)
    product_url_mismatch["top"] = [
        product(
            "SIM-WRONG-API-URL",
            expected_mall_id,
            "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-OTHER-API-URL",
        )
    ]
    product_url_prefix_mismatch = response(expected_mall_id, expected_mall_id)
    product_url_prefix_mismatch["top"] = [
        product(
            "SIM-WRONG-API-PREFIX",
            expected_mall_id,
            "https://shop9999.haeorumgift.com/product_view.asp?p_idx=SIM-WRONG-API-PREFIX",
        )
    ]
    api_smoke_meta_error = api_smoke_error(meta_mismatch)
    api_smoke_result_error = api_smoke_error(result_mismatch)
    api_smoke_product_url_error = api_smoke_error(product_url_mismatch)
    api_smoke_product_url_prefix_error = api_smoke_error(product_url_prefix_mismatch)
    load_meta_error = load_error(meta_mismatch)
    load_result_error = load_error(result_mismatch)
    load_product_url_error = load_error(product_url_mismatch)
    load_product_url_prefix_error = load_error(product_url_prefix_mismatch)

    return {
        "name": "api_load_response_mall_identity",
        "ok": not any(
            [
                api_smoke_meta_error,
                api_smoke_result_error,
                api_smoke_product_url_error,
                api_smoke_product_url_prefix_error,
                load_meta_error,
                load_result_error,
                load_product_url_error,
                load_product_url_prefix_error,
            ]
        ),
        "expected_mall_id": expected_mall_id,
        "wrong_mall_id": wrong_mall_id,
        "api_smoke_meta_error": api_smoke_meta_error,
        "api_smoke_result_error": api_smoke_result_error,
        "api_smoke_product_url_error": api_smoke_product_url_error,
        "api_smoke_product_url_prefix_error": api_smoke_product_url_prefix_error,
        "load_meta_error": load_meta_error,
        "load_result_error": load_result_error,
        "load_product_url_error": load_product_url_error,
        "load_product_url_prefix_error": load_product_url_prefix_error,
        "api_smoke_meta_mismatch_blocked": "meta mall_id must match requested mall_id" in api_smoke_meta_error,
        "api_smoke_result_mismatch_blocked": "top[0] mall_id must match requested mall_id" in api_smoke_result_error,
        "api_smoke_product_url_mismatch_blocked": "product_url must contain product_id" in api_smoke_product_url_error,
        "api_smoke_product_url_prefix_mismatch_blocked": "product_url must match expected product URL prefix" in api_smoke_product_url_prefix_error,
        "load_meta_mismatch_blocked": "meta mall_id must match requested mall_id" in load_meta_error,
        "load_result_mismatch_blocked": "top[0] mall_id must match requested mall_id" in load_result_error,
        "load_product_url_mismatch_blocked": "product_url must contain product_id" in load_product_url_error,
        "load_product_url_prefix_mismatch_blocked": (
            "product_url must match expected product URL prefix" in load_product_url_prefix_error
        ),
    }


def build_client_transport_reuse_risk_report(
    reference_images: dict[str, dict[str, Any]],
    mall_config: dict[str, Any],
) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    mall_id = str(first_mall["mall_id"])
    base_url = "https://ai-search.haeorumgift.com"
    origin = str((first_mall.get("allowed_origins") or ["https://shop0001.haeorumgift.com"])[0])
    image_file = str(reference_images["load"]["path"])
    image_sha256 = str(reference_images["load"]["sha256"])
    mall_identity = simulated_load_mall_identity(
        mall_config,
        requested_sample_size=SIMULATED_MIXED_TRAFFIC_MALL_SAMPLE_SIZE,
    )
    expected_product_url_prefix = product_url_template_prefix(
        str(first_mall.get("product_url_template") or ""),
        mall_id,
    ) or f"https://{mall_id}.haeorumgift.com/product_view.asp"
    single = simulated_mixed_traffic_report(
        api_server_count=1,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
        mall_identity=mall_identity,
    )
    multi_without_reuse = simulated_mixed_traffic_report(
        api_server_count=2,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
        mall_identity=mall_identity,
    )
    multi_without_reuse["client_transport"]["search_requests"]["connection_reuses"] = 0
    load_client_transport_ok, load_client_transport_problems = check_load_client_transport(multi_without_reuse)
    api_scale_report = build_load_compare_report(single, multi_without_reuse)
    multi_without_gzip = simulated_mixed_traffic_report(
        api_server_count=2,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
        mall_identity=mall_identity,
    )
    multi_without_gzip["client_transport"]["search_requests"]["gzip_responses"] = 0
    multi_without_gzip["client_transport"]["search_requests"]["total_response_body_bytes"] = (
        multi_without_gzip["client_transport"]["search_requests"]["total_decoded_response_body_bytes"]
    )
    load_client_gzip_ok, load_client_gzip_problems = check_load_client_transport(multi_without_gzip)
    api_scale_gzip_report = build_load_compare_report(single, multi_without_gzip)
    multi_with_weaker_request_profile = simulated_mixed_traffic_report(
        api_server_count=2,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
        mall_identity=mall_identity,
    )
    profile = multi_with_weaker_request_profile["request_profile"]
    profile["unique_request_signatures"] = 42
    profile["repeated_request_count"] = 808
    profile["unique_by_query_type"] = {"text": 18, "image": 3, "text_image": 21}
    profile["unique_by_mall_id_count"] = {
        mall_id_key: 14
        for mall_id_key in (profile.get("unique_by_mall_id_count") or {})
    }
    profile["min_backend_marqo_request_attempts"] = 42
    api_scale_profile_report = build_load_compare_report(single, multi_with_weaker_request_profile)
    multi_with_single_api_instance = simulated_mixed_traffic_report(
        api_server_count=2,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
        mall_identity=mall_identity,
    )
    multi_with_single_api_instance["api_instance_coverage"] = {
        "ok": False,
        "expected_api_server_count": 2,
        "required_distinct_api_instances": 2,
        "distinct_api_instance_count": 1,
        "successful_responses": 850,
        "missing_header_count": 0,
        "minimum_instance_responses": 42,
        "api_instance_counts": {"hai-sim-api-1": 850},
        "under_minimum_api_instances": [],
        "problems": ["api_instance.distinct_count"],
    }
    api_scale_instance_report = build_load_compare_report(single, multi_with_single_api_instance)
    multi_with_single_admin_metrics_source = simulated_mixed_traffic_report(
        api_server_count=2,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
        mall_identity=mall_identity,
    )
    multi_with_single_admin_metrics_source["server_metrics"]["ok"] = False
    multi_with_single_admin_metrics_source["server_metrics"]["admin_metrics_source_coverage"] = {
        "ok": False,
        "expected_api_server_count": 2,
        "successful_source_count": 1,
        "distinct_instance_count": 1,
        "instance_ids": ["hai-sim-api-1"],
        "problems": [
            "admin_metrics.source_count_below_api_server_count",
            "admin_metrics.distinct_instance_count_below_api_server_count",
        ],
    }
    api_scale_admin_metrics_report = build_load_compare_report(single, multi_with_single_admin_metrics_source)
    return {
        "name": "client_transport_reuse",
        "ok": (
            load_client_transport_ok
            and api_scale_report.get("ok") is True
            and load_client_gzip_ok
            and api_scale_gzip_report.get("ok") is True
            and api_scale_profile_report.get("ok") is True
            and api_scale_instance_report.get("ok") is True
            and api_scale_admin_metrics_report.get("ok") is True
        ),
        "load_client_transport_ok": load_client_transport_ok,
        "load_client_transport_problems": load_client_transport_problems,
        "api_scale_ok": api_scale_report.get("ok") is True,
        "api_scale_problems": api_scale_report.get("problems") or [],
        "api_scale_multi_client_transport_missing": (
            (api_scale_report.get("multi") or {}).get("client_transport_missing") or []
        ),
        "load_client_gzip_ok": load_client_gzip_ok,
        "load_client_gzip_problems": load_client_gzip_problems,
        "api_scale_gzip_ok": api_scale_gzip_report.get("ok") is True,
        "api_scale_gzip_problems": api_scale_gzip_report.get("problems") or [],
        "api_scale_gzip_multi_client_transport_missing": (
            (api_scale_gzip_report.get("multi") or {}).get("client_transport_missing") or []
        ),
        "api_scale_request_profile_ok": api_scale_profile_report.get("ok") is True,
        "api_scale_request_profile_problems": api_scale_profile_report.get("problems") or [],
        "api_scale_request_profile_comparable": (
            (api_scale_profile_report.get("comparison") or {}).get("request_profile_comparable")
        ),
        "api_scale_instance_coverage_ok": api_scale_instance_report.get("ok") is True,
        "api_scale_instance_coverage_problems": api_scale_instance_report.get("problems") or [],
        "api_scale_instance_coverage_multi": (
            (api_scale_instance_report.get("multi") or {}).get("api_instance_coverage") or {}
        ),
        "api_scale_admin_metrics_source_ok": api_scale_admin_metrics_report.get("ok") is True,
        "api_scale_admin_metrics_source_problems": api_scale_admin_metrics_report.get("problems") or [],
        "api_scale_admin_metrics_source_multi": (
            (api_scale_admin_metrics_report.get("multi") or {}).get("admin_metrics_source_coverage") or {}
        ),
        "mutated_client_transport": multi_without_reuse.get("client_transport") or {},
        "mutated_gzip_client_transport": multi_without_gzip.get("client_transport") or {},
        "mutated_request_profile": multi_with_weaker_request_profile.get("request_profile") or {},
        "mutated_api_instance_coverage": multi_with_single_api_instance.get("api_instance_coverage") or {},
        "mutated_admin_metrics_source_coverage": (
            multi_with_single_admin_metrics_source.get("server_metrics") or {}
        ).get("admin_metrics_source_coverage") or {},
    }


def build_cache_coordination_risk_report(
    reference_images: dict[str, dict[str, Any]],
    mall_config: dict[str, Any],
) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    mall_id = str(first_mall["mall_id"])
    base_url = "https://ai-search.haeorumgift.com"
    origin = str((first_mall.get("allowed_origins") or ["https://shop0001.haeorumgift.com"])[0])
    image_file = str(reference_images["load"]["path"])
    image_sha256 = str(reference_images["load"]["sha256"])
    expected_product_url_prefix = product_url_template_prefix(
        str(first_mall.get("product_url_template") or ""),
        mall_id,
    ) or f"https://{mall_id}.haeorumgift.com/product_view.asp"
    single = simulated_mixed_traffic_report(
        api_server_count=1,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
    )

    def multi_report_with_delta(delta_key: str, value: int | float) -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        report["server_metrics"]["delta"][delta_key] = value
        after_snapshot = report["server_metrics"]["after"]["snapshot"]
        if delta_key in after_snapshot and isinstance(value, (int, float)):
            after_snapshot[delta_key] = value
        return report

    def multi_report_with_slow_wait(prefix: str, wait_events_key: str, total_wait_key: str) -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        wait_events = 10
        total_wait_ms = 15000.0
        report["server_metrics"]["delta"][wait_events_key] = wait_events
        report["server_metrics"]["delta"][total_wait_key] = total_wait_ms
        report["server_metrics"]["delta"][f"{prefix}_run_avg_wait_ms"] = round(total_wait_ms / wait_events, 3)
        after_snapshot = report["server_metrics"]["after"]["snapshot"]
        after_snapshot[wait_events_key] = wait_events
        after_snapshot[total_wait_key] = total_wait_ms
        after_snapshot[f"{prefix}_avg_wait_ms"] = round(total_wait_ms / wait_events, 3)
        after_snapshot[f"{prefix}_max_wait_ms"] = round(total_wait_ms / wait_events, 3)
        return report

    def multi_report_with_after_snapshot(snapshot_key: str, value: int | float) -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        report["server_metrics"]["after"]["snapshot"][snapshot_key] = value
        return report

    def multi_report_with_process_rss_growth(before_mb: int, after_mb: int) -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        before_snapshot = report["server_metrics"].setdefault("before", {}).setdefault("snapshot", {})
        after_snapshot = report["server_metrics"]["after"]["snapshot"]
        before_snapshot["process_memory_rss_bytes"] = before_mb * 1024 * 1024
        after_snapshot["process_memory_rss_bytes"] = after_mb * 1024 * 1024
        report["server_metrics"]["process_memory_rss_growth_mb"] = float(after_mb - before_mb)
        return report

    cache_lock_error = multi_report_with_delta("cache_lock_errors", 1)
    cache_lock_wait_timeout = multi_report_with_delta("cache_lock_wait_timeouts", 1)
    singleflight_timeout = multi_report_with_delta("singleflight_wait_timeouts", 1)
    cache_lock_slow_wait = multi_report_with_slow_wait("cache_lock", "cache_lock_wait_events", "cache_lock_total_wait_ms")
    search_queue_slow_wait = multi_report_with_slow_wait("search_queue", "search_queue_wait_events", "search_queue_total_wait_ms")
    search_queue_stuck = multi_report_with_after_snapshot("search_queue_in_flight", 3)
    system_memory_high = multi_report_with_after_snapshot("system_memory_used_percent", 91.0)
    process_rss_growth_high = multi_report_with_process_rss_growth(512, 1153)
    load_cache_lock_ok, load_cache_lock_problems = check_load_server_metrics(cache_lock_error, "mixed-traffic")
    load_cache_lock_wait_ok, load_cache_lock_wait_problems = check_load_server_metrics(cache_lock_wait_timeout, "mixed-traffic")
    load_singleflight_ok, load_singleflight_problems = check_load_server_metrics(singleflight_timeout, "mixed-traffic")
    load_cache_lock_slow_wait_ok, load_cache_lock_slow_wait_problems = check_load_server_metrics(cache_lock_slow_wait, "mixed-traffic")
    load_search_queue_slow_wait_ok, load_search_queue_slow_wait_problems = check_load_server_metrics(search_queue_slow_wait, "mixed-traffic")
    load_search_queue_stuck_ok, load_search_queue_stuck_problems = check_load_server_metrics(search_queue_stuck, "mixed-traffic")
    load_system_memory_high_ok, load_system_memory_high_problems = check_load_server_metrics(system_memory_high, "mixed-traffic")
    load_process_rss_growth_ok, load_process_rss_growth_problems = check_load_server_metrics(
        process_rss_growth_high,
        "mixed-traffic",
    )
    api_scale_cache_lock_report = build_load_compare_report(single, cache_lock_error)
    api_scale_cache_lock_wait_report = build_load_compare_report(single, cache_lock_wait_timeout)
    api_scale_singleflight_report = build_load_compare_report(single, singleflight_timeout)
    api_scale_cache_lock_slow_wait_report = build_load_compare_report(single, cache_lock_slow_wait)
    api_scale_search_queue_slow_wait_report = build_load_compare_report(single, search_queue_slow_wait)
    api_scale_search_queue_stuck_report = build_load_compare_report(single, search_queue_stuck)
    api_scale_system_memory_high_report = build_load_compare_report(single, system_memory_high)
    api_scale_process_rss_growth_report = build_load_compare_report(single, process_rss_growth_high)
    return {
        "name": "cache_coordination",
        "ok": (
            load_cache_lock_ok
            and load_cache_lock_wait_ok
            and load_singleflight_ok
            and load_cache_lock_slow_wait_ok
            and load_search_queue_slow_wait_ok
            and api_scale_cache_lock_report.get("ok") is True
            and api_scale_cache_lock_wait_report.get("ok") is True
            and api_scale_singleflight_report.get("ok") is True
            and api_scale_cache_lock_slow_wait_report.get("ok") is True
            and api_scale_search_queue_slow_wait_report.get("ok") is True
        ),
        "load_cache_lock_ok": load_cache_lock_ok,
        "load_cache_lock_problems": load_cache_lock_problems,
        "load_cache_lock_wait_ok": load_cache_lock_wait_ok,
        "load_cache_lock_wait_problems": load_cache_lock_wait_problems,
        "load_singleflight_ok": load_singleflight_ok,
        "load_singleflight_problems": load_singleflight_problems,
        "load_cache_lock_slow_wait_ok": load_cache_lock_slow_wait_ok,
        "load_cache_lock_slow_wait_problems": load_cache_lock_slow_wait_problems,
        "load_search_queue_slow_wait_ok": load_search_queue_slow_wait_ok,
        "load_search_queue_slow_wait_problems": load_search_queue_slow_wait_problems,
        "load_search_queue_stuck_ok": load_search_queue_stuck_ok,
        "load_search_queue_stuck_problems": load_search_queue_stuck_problems,
        "load_system_memory_high_ok": load_system_memory_high_ok,
        "load_system_memory_high_problems": load_system_memory_high_problems,
        "load_process_rss_growth_ok": load_process_rss_growth_ok,
        "load_process_rss_growth_problems": load_process_rss_growth_problems,
        "api_scale_cache_lock_ok": api_scale_cache_lock_report.get("ok") is True,
        "api_scale_cache_lock_problems": api_scale_cache_lock_report.get("problems") or [],
        "api_scale_cache_lock_wait_ok": api_scale_cache_lock_wait_report.get("ok") is True,
        "api_scale_cache_lock_wait_problems": api_scale_cache_lock_wait_report.get("problems") or [],
        "api_scale_singleflight_ok": api_scale_singleflight_report.get("ok") is True,
        "api_scale_singleflight_problems": api_scale_singleflight_report.get("problems") or [],
        "api_scale_cache_lock_slow_wait_ok": api_scale_cache_lock_slow_wait_report.get("ok") is True,
        "api_scale_cache_lock_slow_wait_problems": api_scale_cache_lock_slow_wait_report.get("problems") or [],
        "api_scale_search_queue_slow_wait_ok": api_scale_search_queue_slow_wait_report.get("ok") is True,
        "api_scale_search_queue_slow_wait_problems": api_scale_search_queue_slow_wait_report.get("problems") or [],
        "api_scale_search_queue_stuck_ok": api_scale_search_queue_stuck_report.get("ok") is True,
        "api_scale_search_queue_stuck_problems": api_scale_search_queue_stuck_report.get("problems") or [],
        "api_scale_system_memory_high_ok": api_scale_system_memory_high_report.get("ok") is True,
        "api_scale_system_memory_high_problems": api_scale_system_memory_high_report.get("problems") or [],
        "api_scale_process_rss_growth_ok": api_scale_process_rss_growth_report.get("ok") is True,
        "api_scale_process_rss_growth_problems": api_scale_process_rss_growth_report.get("problems") or [],
        "api_scale_cache_lock_slow_wait_missing": (
            (api_scale_cache_lock_slow_wait_report.get("multi") or {}).get("server_metrics_missing") or []
        ),
        "api_scale_search_queue_slow_wait_missing": (
            (api_scale_search_queue_slow_wait_report.get("multi") or {}).get("server_metrics_missing") or []
        ),
        "api_scale_search_queue_stuck_missing": (
            (api_scale_search_queue_stuck_report.get("multi") or {}).get("server_metrics_missing") or []
        ),
        "api_scale_system_memory_high_missing": (
            (api_scale_system_memory_high_report.get("multi") or {}).get("server_metrics_missing") or []
        ),
        "api_scale_process_rss_growth_missing": (
            (api_scale_process_rss_growth_report.get("multi") or {}).get("server_metrics_missing") or []
        ),
        "mutations": {
            "cache_lock_errors_delta": 1,
            "cache_lock_wait_timeouts_delta": 1,
            "singleflight_wait_timeouts_delta": 1,
            "cache_lock_run_avg_wait_ms": 1500.0,
            "search_queue_run_avg_wait_ms": 1500.0,
            "search_queue_in_flight_after": 3,
            "system_memory_used_percent_after": 91.0,
            "process_memory_rss_growth_mb": 641.0,
        },
    }


def build_backend_transport_risk_report(
    reference_images: dict[str, dict[str, Any]],
    mall_config: dict[str, Any],
) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    mall_id = str(first_mall["mall_id"])
    base_url = "https://ai-search.haeorumgift.com"
    origin = str((first_mall.get("allowed_origins") or ["https://shop0001.haeorumgift.com"])[0])
    image_file = str(reference_images["load"]["path"])
    image_sha256 = str(reference_images["load"]["sha256"])
    expected_product_url_prefix = product_url_template_prefix(
        str(first_mall.get("product_url_template") or ""),
        mall_id,
    ) or f"https://{mall_id}.haeorumgift.com/product_view.asp"
    single = simulated_mixed_traffic_report(
        api_server_count=1,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
    )

    def multi_report_with_transport_delta(delta_key: str) -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        report["server_metrics"]["delta"][delta_key] = 1
        after_snapshot = report["server_metrics"]["after"]["snapshot"]
        if delta_key in after_snapshot:
            after_snapshot[delta_key] = 1
        return report

    def multi_report_with_slow_backend(service: str) -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        attempts_key = f"backend_{service}_request_attempts"
        elapsed_key = f"backend_{service}_total_elapsed_ms"
        attempts = int(report["server_metrics"]["delta"].get(attempts_key) or 850)
        report["server_metrics"]["delta"][elapsed_key] = float(attempts * 6000)
        report["server_metrics"]["delta"][f"backend_{service}_run_avg_elapsed_ms"] = 6000.0
        report["server_metrics"]["after"]["snapshot"][elapsed_key] = float(attempts * 6000)
        report["server_metrics"]["after"]["snapshot"][f"backend_{service}_avg_elapsed_ms"] = 6000.0
        report["server_metrics"]["after"]["snapshot"][f"backend_{service}_max_elapsed_ms"] = 7000.0
        return report

    def multi_report_without_marqo_gzip() -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        report["server_metrics"]["delta"]["backend_marqo_gzip_responses"] = 0
        report["server_metrics"]["after"]["snapshot"]["backend_marqo_gzip_responses"] = 0
        return report

    def multi_report_without_backend_attempts(service: str) -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        report["server_metrics"]["delta"][f"backend_{service}_request_attempts"] = 0
        return report

    def multi_report_with_backend_attempts_below_profile(service: str) -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        profile = report.get("request_profile") if isinstance(report.get("request_profile"), dict) else {}
        if service == "marqo":
            minimum = int(profile.get("min_backend_marqo_request_attempts") or 2)
            attempts = max(1, minimum - 1)
        else:
            profile["min_backend_qwen_request_attempts"] = max(2, int(profile.get("min_backend_qwen_request_attempts") or 0) + 1)
            minimum = int(profile.get("min_backend_qwen_request_attempts") or 2)
            attempts = max(1, minimum - 1)
        report["server_metrics"]["delta"][f"backend_{service}_request_attempts"] = attempts
        report["server_metrics"]["after"]["snapshot"][f"backend_{service}_request_attempts"] = attempts
        return report

    def multi_report_with_uncompressed_marqo_payload() -> dict[str, Any]:
        report = simulated_mixed_traffic_report(
            api_server_count=2,
            image_file=image_file,
            image_sha256=image_sha256,
            base_url=base_url,
            mall_id=mall_id,
            origin=origin,
            expected_product_url_prefix=expected_product_url_prefix,
        )
        delta = report["server_metrics"]["delta"]
        after_snapshot = report["server_metrics"]["after"]["snapshot"]
        decoded_delta = int(delta.get("backend_marqo_total_decoded_response_body_bytes") or 0)
        decoded_after = int(after_snapshot.get("backend_marqo_total_decoded_response_body_bytes") or 0)
        delta["backend_marqo_total_response_body_bytes"] = decoded_delta
        delta["backend_marqo_run_avg_response_body_bytes"] = delta.get("backend_marqo_run_avg_decoded_response_body_bytes")
        after_snapshot["backend_marqo_total_response_body_bytes"] = decoded_after
        after_snapshot["backend_marqo_max_response_body_bytes"] = after_snapshot.get(
            "backend_marqo_max_decoded_response_body_bytes"
        )
        after_snapshot["backend_marqo_last_response_body_bytes"] = after_snapshot.get(
            "backend_marqo_last_decoded_response_body_bytes"
        )
        return report

    marqo_error = multi_report_with_transport_delta("backend_marqo_error_responses")
    marqo_close = multi_report_with_transport_delta("backend_marqo_connection_close_responses")
    marqo_retry_after = multi_report_with_transport_delta("backend_marqo_retry_after_responses")
    marqo_slow = multi_report_with_slow_backend("marqo")
    marqo_gzip_missing = multi_report_without_marqo_gzip()
    marqo_payload_uncompressed = multi_report_with_uncompressed_marqo_payload()
    marqo_zero_attempts = multi_report_without_backend_attempts("marqo")
    marqo_below_profile = multi_report_with_backend_attempts_below_profile("marqo")
    marqo_circuit_open = multi_report_with_transport_delta("backend_marqo_circuit_open_events")
    qwen_error = multi_report_with_transport_delta("backend_qwen_error_responses")
    qwen_close = multi_report_with_transport_delta("backend_qwen_connection_close_responses")
    qwen_retry_after = multi_report_with_transport_delta("backend_qwen_retry_after_responses")
    qwen_slow = multi_report_with_slow_backend("qwen")
    qwen_zero_attempts = multi_report_without_backend_attempts("qwen")
    qwen_below_profile = multi_report_with_backend_attempts_below_profile("qwen")
    qwen_circuit_open = multi_report_with_transport_delta("backend_qwen_circuit_open_events")
    load_marqo_error_ok, load_marqo_error_problems = check_load_server_metrics(marqo_error, "mixed-traffic")
    load_marqo_close_ok, load_marqo_close_problems = check_load_server_metrics(marqo_close, "mixed-traffic")
    load_marqo_retry_after_ok, load_marqo_retry_after_problems = check_load_server_metrics(
        marqo_retry_after,
        "mixed-traffic",
    )
    load_marqo_slow_ok, load_marqo_slow_problems = check_load_server_metrics(marqo_slow, "mixed-traffic")
    load_marqo_gzip_ok, load_marqo_gzip_problems = check_load_server_metrics(marqo_gzip_missing, "mixed-traffic")
    load_marqo_payload_ok, load_marqo_payload_problems = check_load_server_metrics(
        marqo_payload_uncompressed,
        "mixed-traffic",
    )
    load_marqo_zero_attempts_ok, load_marqo_zero_attempts_problems = check_load_server_metrics(
        marqo_zero_attempts,
        "mixed-traffic",
    )
    load_marqo_below_profile_ok, load_marqo_below_profile_problems = check_load_server_metrics(
        marqo_below_profile,
        "mixed-traffic",
    )
    load_marqo_circuit_ok, load_marqo_circuit_problems = check_load_server_metrics(marqo_circuit_open, "mixed-traffic")
    load_qwen_error_ok, load_qwen_error_problems = check_load_server_metrics(qwen_error, "mixed-traffic")
    load_qwen_close_ok, load_qwen_close_problems = check_load_server_metrics(qwen_close, "mixed-traffic")
    load_qwen_retry_after_ok, load_qwen_retry_after_problems = check_load_server_metrics(
        qwen_retry_after,
        "mixed-traffic",
    )
    load_qwen_slow_ok, load_qwen_slow_problems = check_load_server_metrics(qwen_slow, "mixed-traffic")
    load_qwen_zero_attempts_ok, load_qwen_zero_attempts_problems = check_load_server_metrics(
        qwen_zero_attempts,
        "mixed-traffic",
    )
    load_qwen_below_profile_ok, load_qwen_below_profile_problems = check_load_server_metrics(
        qwen_below_profile,
        "mixed-traffic",
    )
    load_qwen_circuit_ok, load_qwen_circuit_problems = check_load_server_metrics(qwen_circuit_open, "mixed-traffic")
    api_scale_marqo_error_report = build_load_compare_report(single, marqo_error)
    api_scale_marqo_close_report = build_load_compare_report(single, marqo_close)
    api_scale_marqo_retry_after_report = build_load_compare_report(single, marqo_retry_after)
    api_scale_marqo_slow_report = build_load_compare_report(single, marqo_slow)
    api_scale_marqo_gzip_report = build_load_compare_report(single, marqo_gzip_missing)
    api_scale_marqo_payload_report = build_load_compare_report(single, marqo_payload_uncompressed)
    api_scale_marqo_zero_attempts_report = build_load_compare_report(single, marqo_zero_attempts)
    api_scale_marqo_below_profile_report = build_load_compare_report(single, marqo_below_profile)
    api_scale_marqo_circuit_report = build_load_compare_report(single, marqo_circuit_open)
    api_scale_qwen_error_report = build_load_compare_report(single, qwen_error)
    api_scale_qwen_close_report = build_load_compare_report(single, qwen_close)
    api_scale_qwen_retry_after_report = build_load_compare_report(single, qwen_retry_after)
    api_scale_qwen_slow_report = build_load_compare_report(single, qwen_slow)
    api_scale_qwen_zero_attempts_report = build_load_compare_report(single, qwen_zero_attempts)
    api_scale_qwen_below_profile_report = build_load_compare_report(single, qwen_below_profile)
    api_scale_qwen_circuit_report = build_load_compare_report(single, qwen_circuit_open)
    return {
        "name": "backend_transport",
        "ok": (
            load_marqo_error_ok
            and load_marqo_close_ok
            and load_marqo_retry_after_ok
            and load_marqo_slow_ok
            and load_marqo_gzip_ok
            and load_marqo_payload_ok
            and load_marqo_zero_attempts_ok
            and load_marqo_below_profile_ok
            and load_marqo_circuit_ok
            and load_qwen_error_ok
            and load_qwen_close_ok
            and load_qwen_retry_after_ok
            and load_qwen_slow_ok
            and load_qwen_zero_attempts_ok
            and load_qwen_below_profile_ok
            and load_qwen_circuit_ok
            and api_scale_marqo_error_report.get("ok") is True
            and api_scale_marqo_close_report.get("ok") is True
            and api_scale_marqo_retry_after_report.get("ok") is True
            and api_scale_marqo_slow_report.get("ok") is True
            and api_scale_marqo_gzip_report.get("ok") is True
            and api_scale_marqo_payload_report.get("ok") is True
            and api_scale_marqo_zero_attempts_report.get("ok") is True
            and api_scale_marqo_below_profile_report.get("ok") is True
            and api_scale_marqo_circuit_report.get("ok") is True
            and api_scale_qwen_error_report.get("ok") is True
            and api_scale_qwen_close_report.get("ok") is True
            and api_scale_qwen_retry_after_report.get("ok") is True
            and api_scale_qwen_slow_report.get("ok") is True
            and api_scale_qwen_zero_attempts_report.get("ok") is True
            and api_scale_qwen_below_profile_report.get("ok") is True
            and api_scale_qwen_circuit_report.get("ok") is True
        ),
        "load_marqo_error_ok": load_marqo_error_ok,
        "load_marqo_error_problems": load_marqo_error_problems,
        "load_marqo_close_ok": load_marqo_close_ok,
        "load_marqo_close_problems": load_marqo_close_problems,
        "load_marqo_retry_after_ok": load_marqo_retry_after_ok,
        "load_marqo_retry_after_problems": load_marqo_retry_after_problems,
        "load_marqo_slow_ok": load_marqo_slow_ok,
        "load_marqo_slow_problems": load_marqo_slow_problems,
        "load_marqo_gzip_ok": load_marqo_gzip_ok,
        "load_marqo_gzip_problems": load_marqo_gzip_problems,
        "load_marqo_payload_ok": load_marqo_payload_ok,
        "load_marqo_payload_problems": load_marqo_payload_problems,
        "load_marqo_zero_attempts_ok": load_marqo_zero_attempts_ok,
        "load_marqo_zero_attempts_problems": load_marqo_zero_attempts_problems,
        "load_marqo_below_profile_ok": load_marqo_below_profile_ok,
        "load_marqo_below_profile_problems": load_marqo_below_profile_problems,
        "load_marqo_circuit_ok": load_marqo_circuit_ok,
        "load_marqo_circuit_problems": load_marqo_circuit_problems,
        "load_qwen_error_ok": load_qwen_error_ok,
        "load_qwen_error_problems": load_qwen_error_problems,
        "load_qwen_close_ok": load_qwen_close_ok,
        "load_qwen_close_problems": load_qwen_close_problems,
        "load_qwen_retry_after_ok": load_qwen_retry_after_ok,
        "load_qwen_retry_after_problems": load_qwen_retry_after_problems,
        "load_qwen_slow_ok": load_qwen_slow_ok,
        "load_qwen_slow_problems": load_qwen_slow_problems,
        "load_qwen_zero_attempts_ok": load_qwen_zero_attempts_ok,
        "load_qwen_zero_attempts_problems": load_qwen_zero_attempts_problems,
        "load_qwen_below_profile_ok": load_qwen_below_profile_ok,
        "load_qwen_below_profile_problems": load_qwen_below_profile_problems,
        "load_qwen_circuit_ok": load_qwen_circuit_ok,
        "load_qwen_circuit_problems": load_qwen_circuit_problems,
        "api_scale_marqo_error_ok": api_scale_marqo_error_report.get("ok") is True,
        "api_scale_marqo_error_problems": api_scale_marqo_error_report.get("problems") or [],
        "api_scale_marqo_close_ok": api_scale_marqo_close_report.get("ok") is True,
        "api_scale_marqo_close_problems": api_scale_marqo_close_report.get("problems") or [],
        "api_scale_marqo_retry_after_ok": api_scale_marqo_retry_after_report.get("ok") is True,
        "api_scale_marqo_retry_after_problems": api_scale_marqo_retry_after_report.get("problems") or [],
        "api_scale_marqo_slow_ok": api_scale_marqo_slow_report.get("ok") is True,
        "api_scale_marqo_slow_problems": api_scale_marqo_slow_report.get("problems") or [],
        "api_scale_marqo_gzip_ok": api_scale_marqo_gzip_report.get("ok") is True,
        "api_scale_marqo_gzip_problems": api_scale_marqo_gzip_report.get("problems") or [],
        "api_scale_marqo_payload_ok": api_scale_marqo_payload_report.get("ok") is True,
        "api_scale_marqo_payload_problems": api_scale_marqo_payload_report.get("problems") or [],
        "api_scale_marqo_zero_attempts_ok": api_scale_marqo_zero_attempts_report.get("ok") is True,
        "api_scale_marqo_zero_attempts_problems": api_scale_marqo_zero_attempts_report.get("problems") or [],
        "api_scale_marqo_below_profile_ok": api_scale_marqo_below_profile_report.get("ok") is True,
        "api_scale_marqo_below_profile_problems": api_scale_marqo_below_profile_report.get("problems") or [],
        "api_scale_marqo_circuit_ok": api_scale_marqo_circuit_report.get("ok") is True,
        "api_scale_marqo_circuit_problems": api_scale_marqo_circuit_report.get("problems") or [],
        "api_scale_qwen_error_ok": api_scale_qwen_error_report.get("ok") is True,
        "api_scale_qwen_error_problems": api_scale_qwen_error_report.get("problems") or [],
        "api_scale_qwen_close_ok": api_scale_qwen_close_report.get("ok") is True,
        "api_scale_qwen_close_problems": api_scale_qwen_close_report.get("problems") or [],
        "api_scale_qwen_retry_after_ok": api_scale_qwen_retry_after_report.get("ok") is True,
        "api_scale_qwen_retry_after_problems": api_scale_qwen_retry_after_report.get("problems") or [],
        "api_scale_qwen_slow_ok": api_scale_qwen_slow_report.get("ok") is True,
        "api_scale_qwen_slow_problems": api_scale_qwen_slow_report.get("problems") or [],
        "api_scale_qwen_zero_attempts_ok": api_scale_qwen_zero_attempts_report.get("ok") is True,
        "api_scale_qwen_zero_attempts_problems": api_scale_qwen_zero_attempts_report.get("problems") or [],
        "api_scale_qwen_below_profile_ok": api_scale_qwen_below_profile_report.get("ok") is True,
        "api_scale_qwen_below_profile_problems": api_scale_qwen_below_profile_report.get("problems") or [],
        "api_scale_qwen_circuit_ok": api_scale_qwen_circuit_report.get("ok") is True,
        "api_scale_qwen_circuit_problems": api_scale_qwen_circuit_report.get("problems") or [],
        "mutations": {
            "backend_marqo_error_responses_delta": 1,
            "backend_marqo_connection_close_responses_delta": 1,
            "backend_marqo_retry_after_responses_delta": 1,
            "backend_marqo_run_avg_elapsed_ms": 6000.0,
            "backend_marqo_gzip_responses_delta": 0,
            "backend_marqo_response_body_bytes_equals_decoded": True,
            "backend_marqo_request_attempts_delta": 0,
            "backend_marqo_request_attempts_below_unique_profile": True,
            "backend_marqo_circuit_open_events_delta": 1,
            "backend_qwen_error_responses_delta": 1,
            "backend_qwen_connection_close_responses_delta": 1,
            "backend_qwen_retry_after_responses_delta": 1,
            "backend_qwen_run_avg_elapsed_ms": 6000.0,
            "backend_qwen_request_attempts_delta": 0,
            "backend_qwen_request_attempts_below_unique_profile": True,
            "backend_qwen_circuit_open_events_delta": 1,
        },
    }


def build_query_type_latency_risk_report(
    reference_images: dict[str, dict[str, Any]],
    mall_config: dict[str, Any],
) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    mall_id = str(first_mall["mall_id"])
    base_url = "https://ai-search.haeorumgift.com"
    origin = str((first_mall.get("allowed_origins") or ["https://shop0001.haeorumgift.com"])[0])
    image_file = str(reference_images["load"]["path"])
    image_sha256 = str(reference_images["load"]["sha256"])
    expected_product_url_prefix = product_url_template_prefix(
        str(first_mall.get("product_url_template") or ""),
        mall_id,
    ) or f"https://{mall_id}.haeorumgift.com/product_view.asp"
    single = simulated_mixed_traffic_report(
        api_server_count=1,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
    )
    slow_image = simulated_mixed_traffic_report(
        api_server_count=2,
        image_file=image_file,
        image_sha256=image_sha256,
        base_url=base_url,
        mall_id=mall_id,
        origin=origin,
        expected_product_url_prefix=expected_product_url_prefix,
    )
    slow_image["response_query_type_latency_ms"]["image"]["p95"] = 4800
    slow_image["response_query_type_latency_ms"]["image"]["p99"] = 8100
    slow_image["response_query_type_latency_ms"]["image"]["max"] = 8400
    slow_image["latency_ms"]["p95"] = 4800
    slow_image["latency_ms"]["p99"] = 8100
    slow_image["ok"] = True
    load_latency_ok, load_latency_problems, load_latency_details = check_load_query_type_latency(slow_image)
    api_scale_report = build_load_compare_report(single, slow_image)
    return {
        "name": "query_type_latency",
        "ok": load_latency_ok and api_scale_report.get("ok") is True,
        "load_query_type_latency_ok": load_latency_ok,
        "load_query_type_latency_problems": load_latency_problems,
        "load_query_type_latency": load_latency_details,
        "api_scale_ok": api_scale_report.get("ok") is True,
        "api_scale_problems": api_scale_report.get("problems") or [],
        "api_scale_multi_query_type_latency_problems": (
            (api_scale_report.get("multi") or {}).get("query_type_latency_problems") or []
        ),
        "mutations": {
            "response_query_type_latency_ms.image.p95": 4800,
            "response_query_type_latency_ms.image.p99": 8100,
            "response_query_type_latency_ms.image.max": 8400,
            "latency_ms.p99": 8100,
        },
    }


def build_duplicate_saved_widget_html_risk_report(paths: SimulationPaths, mall_config: dict[str, Any]) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    mall_id = str(first_mall["mall_id"])
    origin = str((first_mall.get("allowed_origins") or ["https://shop0001.haeorumgift.com"])[0])
    expected_product_url_prefix = product_url_template_prefix(
        str(first_mall.get("product_url_template") or ""),
        mall_id,
    ) or f"{origin.rstrip('/')}/product_view.asp"
    site = {
        "name": f"{mall_id}-duplicate-saved-html-risk",
        "mall_id": mall_id,
        "url": f"{origin.rstrip('/')}/search",
        "origin": origin,
        "api_base_url": "https://ai-search.haeorumgift.com",
        "api_key": str(first_mall["api_key"]),
        "expected_product_url_prefix": expected_product_url_prefix,
    }
    source_dir = paths.output_dir / "risk-probe-widget-sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    pc_html = source_dir / f"{mall_id}-pc.html"
    mobile_html = source_dir / f"{mall_id}-mobile.html"
    duplicated_html = widget_init_html(site)
    pc_html.write_text(duplicated_html, encoding="utf-8")
    mobile_html.write_text(duplicated_html, encoding="utf-8")
    site_config_path = paths.output_dir / "representative-sites-risk-probe-duplicate-saved-html.json"
    write_json(
        site_config_path,
        {
            "sites": [
                {
                    **site,
                    "widget_probe_sources": [
                        {"variant": "pc", "source": str(pc_html)},
                        {"variant": "mobile", "source": str(mobile_html)},
                    ],
                }
            ]
        },
    )
    validation_problems = input_file_validation_problems(
        {
            "required_files": {"representative_sites_config": str(site_config_path)},
            "file_validation_context": {
                "base_url": "https://ai-search.haeorumgift.com",
                "api_key_available": "true",
                "required_sites": "1",
                "mall_config": str(paths.malls_json),
                "require_saved_widget_probe_sources": "true",
            },
        }
    )
    messages = [
        str(message)
        for problem in validation_problems
        for message in problem.get("problems", [])
    ]
    duplicate_messages = [
        message
        for message in messages
        if "saved HTML source content duplicates" in message
    ]
    return {
        "name": "representative_saved_widget_html_duplicate",
        "ok": not duplicate_messages,
        "config": str(site_config_path),
        "source_files": [str(pc_html), str(mobile_html)],
        "problem_count": len(messages),
        "problems": messages,
        "duplicate_messages": duplicate_messages,
    }


def build_service_mall_filter_risk_report() -> dict[str, Any]:
    class CrossMallEngine:
        name = "cross-mall-risk"

        def search(self, query: EngineQuery) -> list[EngineHit]:
            return [
                EngineHit(
                    document=ProductDocument(
                        product_id="SIM-MALL-FILTER-X001",
                        name="샵0002 오염 우산",
                        category="우산",
                        status="active",
                        mall_id=other_mall_id,
                    ),
                    score=0.99,
                )
            ]

        def upsert_products(self, products):  # type: ignore[no-untyped-def]
            return {"indexed": 0}

        def delete_products(self, product_ids):  # type: ignore[no-untyped-def]
            return {"deleted": 0}

        def health(self):  # type: ignore[no-untyped-def]
            return {"ready": True}

    expected_mall_id = "shop0001"
    other_mall_id = "shop0002"
    settings = Settings(
        engine_backend="local",
        product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
        filter_by_mall_id=True,
        malls={
            expected_mall_id: MallConfig(
                mall_id=expected_mall_id,
                product_url_template=f"https://{expected_mall_id}.haeorumgift.com/product_view.asp?p_idx={{product_id}}",
            ),
            other_mall_id: MallConfig(
                mall_id=other_mall_id,
                product_url_template=f"https://{other_mall_id}.haeorumgift.com/product_view.asp?p_idx={{product_id}}",
            ),
        },
    )
    service = AISearchService(
        LocalSearchEngine(
            [
                ProductDocument(
                    product_id="SIM-MALL-FILTER-001",
                    name="샵0001 검정 우산",
                    category="우산",
                    status="active",
                    mall_id=expected_mall_id,
                ),
                ProductDocument(
                    product_id="SIM-MALL-FILTER-002",
                    name="샵0002 검정 우산",
                    category="우산",
                    status="active",
                    mall_id=other_mall_id,
                ),
            ]
        ),
        settings,
    )
    response = service.search(SearchRequest(mall_id=expected_mall_id, q="검정 우산", limit=10))
    items = response.top + response.items
    product_ids = [item.product_id for item in items]
    mall_ids = [item.mall_id for item in items]
    cross_mall_service = AISearchService(CrossMallEngine(), settings)
    cross_mall_response = cross_mall_service.search(SearchRequest(mall_id=expected_mall_id, q="검정 우산", limit=10))
    cross_mall_items = cross_mall_response.top + cross_mall_response.items
    return {
        "name": "service_mall_config_search_filter",
        "ok": (
            product_ids == ["SIM-MALL-FILTER-001"]
            and mall_ids == [expected_mall_id]
            and not cross_mall_items
            and cross_mall_response.meta.low_confidence is True
        ),
        "filter_by_mall_id": settings.filter_by_mall_id,
        "mall_config_present": bool(settings.malls),
        "requested_mall_id": expected_mall_id,
        "result_product_ids": product_ids,
        "result_mall_ids": mall_ids,
        "backend_cross_mall_result_blocked": not cross_mall_items,
        "backend_cross_mall_result_count": len(cross_mall_items),
        "backend_cross_mall_low_confidence": cross_mall_response.meta.low_confidence,
    }


def build_api_scale_runtime_identity_risk_report(
    paths: SimulationPaths,
    reference_images: dict[str, dict[str, Any]],
    mall_config: dict[str, Any],
) -> dict[str, Any]:
    first_mall = mall_config["malls"][0]
    image_file = str(reference_images["load"]["path"])
    image_sha256 = str(reference_images["load"]["sha256"])
    common = {
        "image_file": image_file,
        "image_sha256": image_sha256,
        "base_url": "https://ai-search.haeorumgift.com",
        "mall_id": first_mall["mall_id"],
        "origin": first_mall["allowed_origins"][0],
        "expected_product_url_prefix": product_url_template_prefix(
            str(first_mall.get("product_url_template") or ""),
            str(first_mall.get("mall_id") or ""),
        )
        or f"https://{first_mall['mall_id']}.haeorumgift.com/product_view.asp",
    }
    single = simulated_mixed_traffic_report(api_server_count=1, **common)
    multi = simulated_mixed_traffic_report(api_server_count=2, **common)
    multi["server_metrics"]["after"]["snapshot"]["marqo_model"] = "stale-marqo-model"
    multi["server_metrics"]["after"]["snapshot"]["engine_index"] = "haeorum-products-stale"
    report = build_load_compare_report(single, multi)
    report["name"] = "api_scale_runtime_identity"
    report["simulation_note"] = (
        "Negative control intentionally compares one-API and two-API load reports from different "
        "index/model runtime identities."
    )
    return report


def build_multipart_request_guard_probe(paths: SimulationPaths) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    errors: list[str] = []
    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    image_validation_source = (ROOT / "app" / "image_validation.py").read_text(encoding="utf-8")
    cache_source = (ROOT / "app" / "cache.py").read_text(encoding="utf-8")
    rate_limit_source = (ROOT / "app" / "rate_limit.py").read_text(encoding="utf-8")
    try:
        public_route = main_source.split("async def ai_search", 1)[1].split("async def click_log", 1)[0]
        preparse_branch = public_route.split("parsed = await parse_search_request", 1)[0]
        click_route = main_source.split("async def click_log", 1)[1].split("async def admin_sync", 1)[0]
        click_preparse_branch = click_route.split("payload = await read_json_object", 1)[0]
        parse_branch = main_source.split("async def parse_search_request", 1)[1].split(
            "async def read_multipart_image_data_url",
            1,
        )[0]
        checks["preparse_header_before_client_rate_limit"] = (
            preparse_branch.index("validate_public_access_headers")
            < preparse_branch.index("enforce_search_client_rate_limit")
        )
        checks["preparse_client_rate_limit_before_form_parse"] = (
            "await run_in_threadpool(enforce_search_client_rate_limit, request)" in preparse_branch
            and "read_multipart_form" not in preparse_branch
            and "read_json_object" not in preparse_branch
        )
        checks["json_search_header_before_body_parse"] = (
            "validate_public_access_headers(request)" in preparse_branch
            and "await run_in_threadpool(enforce_search_client_rate_limit, request)" in preparse_branch
        )
        checks["click_header_before_body_parse"] = (
            click_preparse_branch.index("validate_public_access_headers")
            < click_preparse_branch.index("enforce_click_client_rate_limit")
        )
        checks["click_client_rate_limit_before_body_parse"] = (
            "await run_in_threadpool(enforce_click_client_rate_limit, request)" in click_preparse_branch
            and "read_json_object" not in click_preparse_branch
        )
        checks["content_length_before_form_parse"] = (
            parse_branch.index("validate_multipart_content_length") < parse_branch.index("read_multipart_form")
        )
        checks["image_queue_not_held_during_form_parse"] = "enter_image_search_slot_context" not in preparse_branch
        image_branch = public_route.split("if parsed.has_image:", 1)[1].split(
            "search_request = SearchRequest.model_validate",
            1,
        )[0]
        checks["image_upload_slot_entered_before_upload_read"] = (
            image_branch.index("enter_image_search_slot_context") < image_branch.index("read_multipart_image_data_url")
        )
        checks["image_search_service_uses_image_gate"] = (
            "await run_in_threadpool(service.search, search_request, acquire_image_search_slot)" in public_route
        )
        checks["image_upload_slot_exited_in_finally"] = (
            "finally:" in public_route
            and "await run_in_threadpool(exit_image_search_slot_context, upload_slot_context)" in public_route
        )
        validate_image_body = image_validation_source.split("def validate_image_bytes", 1)[1].split(
            "async def read_upload_bytes_limited",
            1,
        )[0]
        checks["image_validation_single_feature_decode"] = (
            "features = analyze_image_features(raw)" in validate_image_body
            and "analyze_image_quality(raw)" not in validate_image_body
            and "compute_average_hash(raw)" not in validate_image_body
        )
        checks["redis_cache_socket_timeout_configured"] = (
            "socket_timeout=self.redis_socket_timeout_seconds" in cache_source
            and "socket_connect_timeout=self.redis_socket_connect_timeout_seconds" in cache_source
            and "RedisFailureBackoff" in cache_source
        )
        checks["redis_cache_failure_backoff_skips_hot_path"] = (
            "if not self._redis_allowed(\"get\")" in cache_source
            and "if not self._redis_allowed(\"set\")" in cache_source
            and "if not self._redis_allowed(\"lock_claim\")" in cache_source
        )
        checks["redis_rate_limit_socket_timeout_configured"] = (
            "socket_timeout=self.redis_socket_timeout_seconds" in rate_limit_source
            and "socket_connect_timeout=self.redis_socket_connect_timeout_seconds" in rate_limit_source
            and "RedisFailureBackoff" in rate_limit_source
        )
        checks["redis_rate_limit_failure_backoff_uses_local_fallback"] = (
            "if not self._redis_allowed(\"hit\")" in rate_limit_source
            and "skipped_redis=True" in rate_limit_source
            and "fallback_skipped_redis_events" in rate_limit_source
        )
    except Exception as exc:
        errors.append(f"source_order:{exc}")
        checks["preparse_header_before_client_rate_limit"] = False
        checks["preparse_client_rate_limit_before_form_parse"] = False
        checks["json_search_header_before_body_parse"] = False
        checks["click_header_before_body_parse"] = False
        checks["click_client_rate_limit_before_body_parse"] = False
        checks["content_length_before_form_parse"] = False
        checks["image_queue_not_held_during_form_parse"] = False
        checks["image_upload_slot_entered_before_upload_read"] = False
        checks["image_search_service_uses_image_gate"] = False
        checks["image_upload_slot_exited_in_finally"] = False
        checks["image_validation_single_feature_decode"] = False
        checks["redis_cache_socket_timeout_configured"] = False
        checks["redis_cache_failure_backoff_skips_hot_path"] = False
        checks["redis_rate_limit_socket_timeout_configured"] = False
        checks["redis_rate_limit_failure_backoff_uses_local_fallback"] = False

    try:
        validate_multipart_content_length(None, max_image_bytes=5, overhead_bytes=1)
        checks["missing_content_length_blocked"] = False
    except ValueError as exc:
        details["missing_content_length_error"] = str(exc)
        checks["missing_content_length_blocked"] = "Content-Length is required" in str(exc)

    try:
        validate_multipart_content_length("7", max_image_bytes=5, overhead_bytes=1)
        checks["oversized_content_length_blocked"] = False
    except ValueError as exc:
        details["oversized_content_length_error"] = str(exc)
        checks["oversized_content_length_blocked"] = "multipart body exceeds" in str(exc)

    try:
        malls = load_mall_configs(paths.malls_json)
        settings = Settings(malls=malls)
        first_mall = next(mall for mall in malls.values() if mall.enabled)
        valid_origin = first_mall.allowed_origins[0]
        first_index = public_header_access_index(settings)
        second_index = public_header_access_index(settings)
        first_mall_index = mall_access_index(settings)
        second_mall_index = mall_access_index(settings)
        details["header_access_index"] = {
            "enabled_count": first_index.enabled_count,
            "configured_mall_count": len(malls),
            "api_key_count": len(first_index.origins_by_api_key) + len(first_index.any_origin_api_keys),
            "reused": first_index is second_index,
        }
        details["mall_access_index"] = {
            "enabled_count": first_mall_index.enabled_count,
            "configured_mall_count": len(malls),
            "origin_mall_count": len(first_mall_index.origins_by_mall_id),
            "any_origin_mall_count": len(first_mall_index.any_origin_mall_ids),
            "reused": first_mall_index is second_mall_index,
        }
        checks["header_access_index_reused"] = first_index is second_index
        checks["header_access_index_covers_malls"] = first_index.enabled_count == len(malls)
        checks["header_access_index_covers_api_keys"] = (
            first_mall.api_key in first_index.origins_by_api_key
            or first_mall.api_key in first_index.any_origin_api_keys
        )
        checks["mall_access_index_reused"] = first_mall_index is second_mall_index
        checks["mall_access_index_covers_malls"] = first_mall_index.enabled_count == len(malls)
        checks["mall_access_index_covers_origins"] = first_mall.mall_id in first_mall_index.origins_by_mall_id
        validate_public_header_access(settings, first_mall.api_key, origin=valid_origin)
        validate_mall_access(settings, first_mall.mall_id, first_mall.api_key, origin=valid_origin)
        details["valid_header_mall_id"] = first_mall.mall_id
        checks["valid_header_candidate_allowed"] = True
        checks["valid_mall_candidate_allowed"] = True

        bucket_products = [
            ProductDocument(
                product_id=f"BUCKET-{index:04d}",
                name="공통 우산",
                category="우산",
                status="active",
                mall_id=mall.mall_id,
            )
            for index, mall in enumerate(malls.values(), start=1)
            if mall.enabled
        ]
        bucket_engine = LocalSearchEngine(bucket_products)
        filter_prepare_calls = 0
        filter_match_calls = 0
        original_prepare_product_filters = engine_module.prepare_product_filters
        original_product_matches_prepared_filters = engine_module.product_matches_prepared_filters

        def counting_prepare_product_filters(query):  # type: ignore[no-untyped-def]
            nonlocal filter_prepare_calls
            filter_prepare_calls += 1
            return original_prepare_product_filters(query)

        def counting_product_matches_prepared_filters(product, filters):  # type: ignore[no-untyped-def]
            nonlocal filter_match_calls
            filter_match_calls += 1
            return original_product_matches_prepared_filters(product, filters)

        try:
            engine_module.prepare_product_filters = counting_prepare_product_filters
            engine_module.product_matches_prepared_filters = counting_product_matches_prepared_filters
            bucket_hits = bucket_engine.search(
                EngineQuery(
                    q="우산",
                    mall_id=first_mall.mall_id,
                    category="우산",
                    strict_mall_filter=True,
                    limit=10,
                )
            )
        finally:
            engine_module.prepare_product_filters = original_prepare_product_filters
            engine_module.product_matches_prepared_filters = original_product_matches_prepared_filters
        bucket_health = bucket_engine.health()
        details["local_engine_mall_bucket"] = {
            "configured_mall_count": len(bucket_products),
            "mall_buckets": bucket_health.get("mall_buckets"),
            "strict_search_filter_prepare_calls": filter_prepare_calls,
            "strict_search_filter_match_calls": filter_match_calls,
            "strict_search_result_count": len(bucket_hits),
            "target_mall_id": first_mall.mall_id,
        }
        checks["local_engine_mall_bucket_covers_malls"] = bucket_health.get("mall_buckets") == len(bucket_products)
        checks["local_engine_mall_bucket_limits_strict_search"] = (
            len(bucket_hits) == 1
            and bucket_hits[0].document.mall_id == first_mall.mall_id
            and filter_match_calls == 1
        )
        checks["local_engine_prepares_filters_once_per_search"] = filter_prepare_calls == 1
        try:
            validate_public_header_access(settings, "pk_live_wrong_multipart_guard_key", origin=valid_origin)
            checks["invalid_header_candidate_blocked"] = False
        except Exception as exc:
            details["invalid_header_error"] = str(exc)
            checks["invalid_header_candidate_blocked"] = "invalid API key" in str(exc)
        try:
            validate_public_header_access(settings, first_mall.api_key, origin="https://evil.example.test")
            checks["invalid_origin_candidate_blocked"] = False
        except Exception as exc:
            details["invalid_origin_error"] = str(exc)
            checks["invalid_origin_candidate_blocked"] = "origin is not allowed" in str(exc)
        try:
            validate_mall_access(settings, first_mall.mall_id, first_mall.api_key, origin="https://evil.example.test")
            checks["invalid_mall_origin_candidate_blocked"] = False
        except Exception as exc:
            details["invalid_mall_origin_error"] = str(exc)
            checks["invalid_mall_origin_candidate_blocked"] = "origin is not allowed" in str(exc)
    except Exception as exc:
        errors.append(f"header_candidate:{exc}")
        checks["header_access_index_reused"] = False
        checks["header_access_index_covers_malls"] = False
        checks["header_access_index_covers_api_keys"] = False
        checks["mall_access_index_reused"] = False
        checks["mall_access_index_covers_malls"] = False
        checks["mall_access_index_covers_origins"] = False
        checks["valid_header_candidate_allowed"] = False
        checks["valid_mall_candidate_allowed"] = False
        checks["invalid_header_candidate_blocked"] = False
        checks["invalid_origin_candidate_blocked"] = False
        checks["invalid_mall_origin_candidate_blocked"] = False
        checks["local_engine_mall_bucket_covers_malls"] = False
        checks["local_engine_mall_bucket_limits_strict_search"] = False
        checks["local_engine_prepares_filters_once_per_search"] = False

    buckets: dict[str, list[float]] = {}
    first_allowed, first_count = record_rate_limit_hit(buckets, "search:ip:203.0.113.10", 1, now=1000.0)
    second_allowed, second_count = record_rate_limit_hit(buckets, "search:ip:203.0.113.10", 1, now=1000.1)
    details["ip_rate_limit_counts"] = {"first": first_count, "second": second_count}
    checks["ip_rate_limit_blocks_second_request"] = first_allowed is True and second_allowed is False
    memory_bucket_store = RateLimitBucketStore(max_buckets=10, prune_interval_seconds=1.0)
    memory_bucket_store.buckets.update(
        {
            "search:ip:expired": [900.0],
            "search:ip:active": [1040.0],
        }
    )
    memory_allowed, memory_count = memory_bucket_store.hit(
        "search:ip:new",
        5,
        now=1100.0,
        window_seconds=60,
    )
    memory_status = memory_bucket_store.status()
    details["memory_rate_limit_fallback_prune"] = {
        "allowed": memory_allowed,
        "new_count": memory_count,
        "bucket_count": memory_status["bucket_count"],
        "pruned_buckets": memory_status["pruned_buckets"],
        "bucket_keys": sorted(memory_bucket_store.buckets),
    }
    checks["memory_rate_limit_fallback_prunes_stale_buckets"] = (
        memory_allowed is True
        and memory_count == 1
        and "search:ip:expired" not in memory_bucket_store.buckets
        and "search:ip:active" in memory_bucket_store.buckets
        and memory_status["bucket_count"] == 2
        and memory_status["pruned_buckets"] == 1
    )
    try:
        for cached_function in [
            normalize_query_spacing,
            normalize_query_text,
            build_search_query,
            infer_category_intents,
        ]:
            cached_function.cache_clear()
        for _ in range(8):
            normalize_query_text("텐블러")
            normalize_query_spacing("호텔 수건")
            build_search_query("텐블러", "텀블러")
            infer_category_intents("호텔 수건", limit=1)
        spacing_cache = normalize_query_spacing.cache_info()
        normalization_cache = normalize_query_text.cache_info()
        search_query_cache = build_search_query.cache_info()
        category_cache = infer_category_intents.cache_info()
        details["query_normalization_cache"] = {
            "normalize_query_spacing": spacing_cache._asdict(),
            "normalize_query_text": normalization_cache._asdict(),
            "build_search_query": search_query_cache._asdict(),
            "infer_category_intents": category_cache._asdict(),
        }
        checks["query_normalization_lru_cache_bounded"] = (
            spacing_cache.maxsize == 4096
            and normalization_cache.maxsize == 4096
            and search_query_cache.maxsize == 4096
            and category_cache.maxsize == 4096
        )
        checks["query_normalization_repeated_terms_hit_cache"] = (
            spacing_cache.hits >= 7
            and normalization_cache.hits >= 7
            and search_query_cache.hits >= 7
            and category_cache.hits >= 7
        )
    except Exception as exc:
        errors.append(f"query_normalization_cache:{exc}")
        checks["query_normalization_lru_cache_bounded"] = False
        checks["query_normalization_repeated_terms_hit_cache"] = False
    checks["invalid_header_blocked_before_form_parse"] = (
        checks["preparse_header_before_client_rate_limit"]
        and checks["invalid_header_candidate_blocked"]
        and checks["content_length_before_form_parse"]
    )
    checks["ip_rate_limit_before_form_parse"] = (
        checks["preparse_client_rate_limit_before_form_parse"]
        and checks["ip_rate_limit_blocks_second_request"]
    )
    checks["json_search_ip_rate_limit_before_body_parse"] = (
        checks["json_search_header_before_body_parse"]
        and checks["ip_rate_limit_blocks_second_request"]
    )
    checks["click_ip_rate_limit_before_body_parse"] = (
        checks["click_client_rate_limit_before_body_parse"]
        and checks["ip_rate_limit_blocks_second_request"]
    )
    details["image_validation_feature_analysis"] = {
        "single_decode_path": checks["image_validation_single_feature_decode"],
    }
    details["redis_failure_backoff"] = {
        "cache_socket_timeout_configured": checks["redis_cache_socket_timeout_configured"],
        "cache_backoff_skips_hot_path": checks["redis_cache_failure_backoff_skips_hot_path"],
        "rate_limit_socket_timeout_configured": checks["redis_rate_limit_socket_timeout_configured"],
        "rate_limit_backoff_uses_local_fallback": checks["redis_rate_limit_failure_backoff_uses_local_fallback"],
    }
    return mark_simulated(
        {
            "ok": all(checks.values()) and not errors,
            "checks": checks,
            "details": details,
            "errors": errors,
            "simulation_note": (
                "Local source and helper checks prove JSON and multipart search/click requests perform "
                "header candidate validation plus IP rate limiting before body parsing, and multipart requests "
                "also require Content-Length and acquire the image queue before form parsing."
            ),
        }
    )


def build_backend_request_slot_saturation_probe() -> dict[str, Any]:
    release_response = threading.Event()
    first_request_entered = threading.Event()
    first_request_done = threading.Event()
    first_result: dict[str, Any] = {}
    timeout_error: BackendConnectionPoolTimeoutError | None = None
    saturated_stats: dict[str, Any] = {}
    errors: list[str] = []

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            first_request_entered.set()
            if not release_response.wait(timeout=3):
                errors.append("first request response was not released before timeout")
            return b'{"ok":true}'

        def getheader(self, name: str) -> str:
            return "keep-alive" if str(name or "").lower() == "connection" else ""

    class FakeConnection:
        opened = 0
        requests = 0
        closed = 0

        def __init__(self, host: str, port: int | None = None, timeout: int | float | None = None):
            self.host = host
            self.port = port
            self.timeout = timeout
            FakeConnection.opened += 1

        def request(
            self,
            method: str,
            path: str,
            body: bytes | None = None,
            headers: dict[str, str] | None = None,
        ) -> None:
            FakeConnection.requests += 1

        def getresponse(self) -> FakeResponse:
            first_request_entered.set()
            return FakeResponse()

        def close(self) -> None:
            FakeConnection.closed += 1

    client = BackendJsonHttpClient(
        "http://backend-slot-probe.example.test",
        "Marqo",
        max_active_requests=1,
        connection_acquire_timeout_seconds=0.02,
        circuit_failure_threshold=1,
        circuit_cooldown_seconds=0.5,
    )
    original_connection_class = engine_module.http.client.HTTPConnection
    engine_module.http.client.HTTPConnection = FakeConnection

    def first_request() -> None:
        try:
            status, data, elapsed_ms = client.request("POST", "/indexes/products/search", {"q": "slow"}, timeout=1)
            first_result.update({"status": status, "data": data, "elapsed_ms": elapsed_ms})
        except Exception as exc:
            first_result.update({"error": f"{exc.__class__.__name__}: {exc}"})
        finally:
            first_request_done.set()

    thread = threading.Thread(target=first_request, daemon=True)
    try:
        thread.start()
        if not first_request_entered.wait(timeout=3):
            errors.append("first request did not enter backend connection")
        try:
            client.request("POST", "/indexes/products/search", {"q": "overflow"}, timeout=1)
        except BackendConnectionPoolTimeoutError as exc:
            timeout_error = exc
        except Exception as exc:
            errors.append(f"unexpected overflow exception: {exc.__class__.__name__}: {exc}")
        else:
            errors.append("overflow request unexpectedly reached backend")
        saturated_stats = client.stats()
    finally:
        release_response.set()
        first_request_done.wait(timeout=3)
        thread.join(timeout=1)
        engine_module.http.client.HTTPConnection = original_connection_class
        client.close_all()

    final_stats = client.stats()
    circuit = final_stats.get("circuit_breaker") or {}
    checks = {
        "slot_timeout_raises_503": isinstance(timeout_error, BackendConnectionPoolTimeoutError)
        and timeout_error.status_code == 503
        and timeout_error.service == "marqo",
        "slot_timeout_has_retry_after": isinstance(timeout_error, BackendConnectionPoolTimeoutError)
        and timeout_error.retry_after_seconds is not None
        and timeout_error.retry_after_seconds >= 0.02,
        "overflow_does_not_start_backend_attempt": int(saturated_stats.get("request_attempts") or 0) == 1
        and FakeConnection.opened == 1
        and FakeConnection.requests == 1,
        "active_request_capped_at_configured_limit": int(saturated_stats.get("active_requests") or 0) == 1
        and int(final_stats.get("max_active_requests_observed") or 0) == 1,
        "slot_timeout_metric_recorded": int(final_stats.get("connection_acquire_wait_timeouts") or 0) == 1,
        "slot_wait_events_recorded": int(final_stats.get("connection_acquire_wait_events") or 0) >= 2,
        "slot_released_after_backend_response": int(final_stats.get("active_requests") or 0) == 0,
        "slot_timeout_does_not_open_circuit": circuit.get("state") == "closed"
        and int(circuit.get("open_events") or 0) == 0
        and int(circuit.get("consecutive_failures") or 0) == 0,
        "first_request_completed": first_result.get("status") == 200 and first_result.get("data") == {"ok": True},
    }
    return mark_simulated(
        {
            "ok": all(checks.values()) and not errors,
            "checks": checks,
            "errors": errors,
            "saturated_stats": saturated_stats,
            "final_stats": final_stats,
            "fake_connection": {
                "opened": FakeConnection.opened,
                "requests": FakeConnection.requests,
                "closed": FakeConnection.closed,
            },
            "timeout_error": {
                "type": timeout_error.__class__.__name__ if timeout_error else "",
                "status_code": getattr(timeout_error, "status_code", None),
                "service": getattr(timeout_error, "service", None),
                "retry_after_seconds": getattr(timeout_error, "retry_after_seconds", None),
                "message": str(timeout_error or ""),
            },
            "simulation_note": (
                "Exercises backend HTTP active-request slot saturation without production Marqo. "
                "The overflow request must fail fast before opening another backend connection and must not "
                "advance the backend circuit breaker."
            ),
        }
    )


def build_operational_risk_probe_report(
    paths: SimulationPaths,
    rows: list[dict[str, Any]],
    reference_images: dict[str, dict[str, Any]],
    mall_config: dict[str, Any],
) -> dict[str, Any]:
    multipart_guard_report = build_multipart_request_guard_probe(paths)
    backend_request_slot_report = build_backend_request_slot_saturation_probe()
    bad_rows = [dict(row) for row in rows[:16]]
    if len(bad_rows) >= 12:
        bad_rows[0]["product_id"] = "X" * (MAX_PRODUCT_ID_LENGTH + 1)
        bad_rows[0]["product_url"] = "https://shop0001.haeorumgift.com/product_view.asp?p_idx=oversized"
        bad_rows[1]["product_id"] = bad_rows[2]["product_id"]
        bad_rows[3]["updated_at"] = ""
        bad_rows[4]["updated_at"] = "not-a-date"
        bad_rows[5]["updated_at"] = "2999-01-01T00:00:00Z"
        bad_rows[5]["main_image_url"] = "https://token@cdn.haeorumgift.com/private.jpg"
        bad_rows[6]["product_url"] = "javascript:alert(1)"
        bad_rows[7]["mall_id"] = ""
        bad_rows[8]["main_image_url"] = ""
        bad_rows[9]["price_min"] = 9000
        bad_rows[9]["price_max"] = 1000
        bad_rows[10]["mall_id"] = "shop0001/evil"
        bad_rows[11]["product_url"] = "https://127.0.0.1/private/product"
    if len(bad_rows) >= 14:
        bad_rows[12]["mall_id"] = "shop0001"
        bad_rows[12]["product_url"] = "https://shop0001.haeorumgift.com/product_view.asp?p_idx=wrong-product"
        bad_rows[13]["main_image_url"] = "http://cdn.haeorumgift.com/products/non-https-image.jpg"
    if len(bad_rows) >= 16:
        bad_rows[14]["category_name"] = ""
        bad_rows[15]["price"] = -1000
    view_report = analyze_generated_view(bad_rows)
    sample_report = analyze_sample(bad_rows)
    sample_quality_report = validate_sample_report(sample_report)
    undersized_view_report = build_simulated_mssql_view_report(paths, sample_size=1)
    undersized_view_report["ok"] = True
    undersized_view_ok, _, undersized_view_details = check_mssql_view(undersized_view_report)
    low_nofile_server_preflight_report = build_simulated_server_preflight_report()
    for item in low_nofile_server_preflight_report.get("checks") or []:
        if isinstance(item, dict) and item.get("name") == "host_resources":
            item["open_file_limit_soft"] = 1024
            item["open_file_limit_hard"] = 4096
            item["requirements"] = {
                **(item.get("requirements") or {}),
                "min_open_files": 65535,
            }
    low_nofile_server_preflight_ok, _, low_nofile_server_preflight_details = check_server_preflight(
        low_nofile_server_preflight_report
    )
    if len(bad_rows) >= 13:
        bad_rows[12]["mall_id"] = "shop0001"
        bad_rows[12]["product_url"] = "https://shop9999.haeorumgift.com/product_view.asp?p_idx=wrong-mall"
    export_report = build_simulated_mssql_export_report(paths, bad_rows, paths.malls_json)
    inconsistent_export_report = build_simulated_mssql_export_report(paths, rows[:400], paths.malls_json)
    inconsistent_export_report["ok"] = True
    inconsistent_export_report["inactive_products"] = int(inconsistent_export_report.get("inactive_products") or 0) + 1
    inconsistent_export_report["mall_config_alignment"] = {
        **(inconsistent_export_report.get("mall_config_alignment") or {}),
        "active_products_checked": max(0, int(inconsistent_export_report.get("active_products") or 0) - 1),
    }
    inconsistent_export_ok, _, inconsistent_export_details = check_mssql_export(inconsistent_export_report)
    filterless_rows = [dict(row) for row in rows[:400]]
    for row in filterless_rows:
        row["price_min"] = ""
        row["price_max"] = ""
        row["print_methods"] = ""
        row["materials"] = ""
        row["colors"] = ""
        row["min_order_qty"] = ""
        row["delivery_days"] = ""
    filterless_export_report = build_simulated_mssql_export_report(paths, filterless_rows, paths.malls_json)
    filterless_export_ok, _, filterless_export_details = check_mssql_export(filterless_export_report)
    active_only_rows = []
    for row in rows[:400]:
        active_row = dict(row)
        active_row["status"] = "active"
        active_row["display_yn"] = "Y"
        active_row["is_deleted"] = "false"
        active_only_rows.append(active_row)
    active_only_export_report = build_simulated_mssql_export_report(paths, active_only_rows, paths.malls_json)
    active_only_export_ok, _, active_only_export_details = check_mssql_export(active_only_export_report)
    partial_export_report = build_simulated_mssql_export_report(paths, rows[:400], paths.malls_json)
    partial_export_report["limit"] = 400
    partial_export_report["since_configured"] = True
    partial_export_ok, _, partial_export_details = check_mssql_export(partial_export_report)
    fingerprint_path_mismatch_export_report = build_simulated_mssql_export_report(paths, rows[:400], paths.malls_json)
    fingerprint_path_mismatch_export_report["output_csv_fingerprint"] = {
        **(fingerprint_path_mismatch_export_report.get("output_csv_fingerprint") or {}),
        "path": str(paths.output_dir / "wrong-products.csv"),
    }
    fingerprint_path_mismatch_export_ok, _, fingerprint_path_mismatch_export_details = check_mssql_export(
        fingerprint_path_mismatch_export_report
    )
    writer_permission_report = analyze_readonly_permissions(
        {
            "db_datareader": 1,
            "db_datawriter": 1,
            "db_owner": 0,
            "db_ddladmin": 0,
            "db_securityadmin": 0,
            "db_accessadmin": 0,
            "db_backupoperator": 0,
        },
        [
            {"permission_name": "SELECT", "state_desc": "GRANT"},
            {"permission_name": "UPDATE", "state_desc": "GRANT"},
        ],
    )
    bad_mall_config_path = paths.output_dir / "mall-config-risk-probe-invalid-mall-id.json"
    write_json(
        bad_mall_config_path,
        {
            "malls": [
                {
                    "mall_id": "shop0001",
                    "api_key": "public-shop0001-dev-key",
                    "product_url_template": "http://127.0.0.1/product_view.asp?p_idx={product_id}",
                    "allowed_origins": ["*"],
                    "price_multiplier": 0,
                },
                {
                    "mall_id": "shop0001",
                    "api_key": "public-shop0001-dev-key",
                    "product_url_template": "https://shop0001.haeorumgift.com/product_view.asp",
                    "allowed_origins": ["http://shop0001.haeorumgift.com"],
                    "price_round_to": 0,
                },
                {
                    "mall_id": "shop0001/evil",
                    "api_key": "pk_live_valid_but_bad_mall_id",
                    "product_url_template": "https://shop0001.haeorumgift.com/product_view.asp?p_idx={product_id}",
                    "allowed_origins": ["https://shop0001.haeorumgift.com"],
                },
                {
                    "mall_id": "shop0002",
                    "api_key": "pk_live_duplicate_endpoint_a",
                    "product_url_template": "https://shared.haeorumgift.com/product_view.asp?p_idx={product_id}",
                    "allowed_origins": ["https://shared.haeorumgift.com"],
                },
                {
                    "mall_id": "shop0003",
                    "api_key": "pk_live_duplicate_endpoint_b",
                    "product_url_template": "https://shared.haeorumgift.com/product_view.asp?p_idx={product_id}",
                    "allowed_origins": ["https://shared.haeorumgift.com"],
                },
                {
                    "mall_id": "shop0004",
                    "api_key": "short-key",
                    "product_url_template": "https://shop0004.haeorumgift.com/product_view.asp?p_idx={product_id}",
                    "allowed_origins": ["https://shop0004.haeorumgift.com"],
                }
            ]
        },
    )
    bad_mall_config_report = validate_mall_config(bad_mall_config_path, min_count=1, require_strong_api_keys=True)
    mall_config_problems = bad_mall_config_report.get("problems", [])
    mall_problem_fields = sorted(
        {
            str(problem.get("field") or "")
            for problem in mall_config_problems
            if isinstance(problem, dict) and str(problem.get("field") or "")
        }
    )
    mall_problem_messages = [
        str(problem.get("message") or "")
        for problem in mall_config_problems
        if isinstance(problem, dict)
    ]
    widget_candidate_html = """
    <html>
      <body>
        <form id="legacyForm" action="/goods/goods_search.asp">
          <input id="keyword" name="sword" type="search" placeholder="상품명 검색">
          <button type="submit">검색</button>
        </form>
      </body>
    </html>
    """
    unsafe_widget_url_report = analyze_html_source(
        "http://shop0001.haeorumgift.com/search",
        widget_candidate_html,
        api_base_url="http://localhost:8000",
        mall_id="shop0001",
        api_key="public-shop0001",
    )
    csp_widget_risk_report = analyze_html_source(
        "https://shop0001.haeorumgift.com/search",
        widget_candidate_html,
        api_base_url="https://ai-search.haeorumgift.com",
        mall_id="shop0001",
        api_key="public-shop0001",
        response_csp_policies=["default-src 'self'; script-src 'self'"],
    )
    no_search_candidate_report = analyze_html_source(
        "https://shop0001.haeorumgift.com/search",
        "<html><body><nav>상품 카테고리만 있는 페이지</nav></body></html>",
        api_base_url="https://ai-search.haeorumgift.com",
        mall_id="shop0001",
        api_key="public-shop0001",
    )
    duplicate_selector_widget_report = analyze_html_source(
        "https://shop0001.haeorumgift.com/search",
        """
        <html>
          <body>
            <form id="legacyForm" action="/legacy-search">
              <input id="keyword" name="keyword" type="search" placeholder="상품명 검색">
              <button id="submit" type="submit">검색</button>
            </form>
            <form id="legacyForm" action="/goods/goods_search.asp">
              <input id="keyword" name="sword" type="search" placeholder="상품명 검색">
              <button id="submit" type="submit">검색</button>
            </form>
          </body>
        </html>
        """,
        api_base_url="https://ai-search.haeorumgift.com",
        mall_id="shop0001",
        api_key="public-shop0001",
    )
    ambiguous_selector_fallback_widget_report = analyze_html_source(
        "https://shop0001.haeorumgift.com/search",
        """
        <html>
          <body>
            <form id="legacyForm" action="/legacy-search">
              <input id="keyword" name="keyword" type="search" placeholder="상품명 검색">
              <button id="submit" type="submit">검색</button>
            </form>
            <form id="legacyForm" action="/goods/goods_search.asp">
              <input id="keyword" name="sword" type="search" placeholder="상품명 검색">
              <button id="submit" type="submit">검색</button>
            </form>
          </body>
        </html>
        """,
        api_base_url="https://ai-search.haeorumgift.com",
        mall_id="shop0001",
        api_key="public-shop0001",
        allow_fallback_floating=True,
    )
    multi_match_selector_widget_report = analyze_html_source(
        "https://shop0001.haeorumgift.com/search",
        """
        <html>
          <body>
            <form action="/legacy-search">
              <input class="search-box" type="search" placeholder="상품명 검색">
              <button class="search-submit" type="submit">검색</button>
            </form>
            <form action="/goods/goods_search.asp">
              <input class="search-box" type="search" placeholder="상품명 검색">
              <button class="search-submit" type="submit">검색</button>
            </form>
          </body>
        </html>
        """,
        api_base_url="https://ai-search.haeorumgift.com",
        mall_id="shop0001",
        api_key="public-shop0001",
    )
    disconnected_selector_html = """
    <html>
      <body>
        <form id="searchForm" action="/goods/goods_search.asp">
          <input id="otherKeyword" name="keyword" type="search" placeholder="상품명 검색">
          <button id="submit" type="submit">검색</button>
        </form>
        <input id="keyword" name="orphanKeyword" type="search" placeholder="분리된 검색창">
        <script src="/widget.js"></script>
        <script>HaeorumAISearch.init({attachToSearchInput:"#searchForm #keyword",
        attachAfterSelector:"#searchForm #submit",mallId:"shop0001",
        apiBaseUrl:"https://ai-search.haeorumgift.com"})</script>
      </body>
    </html>
    """
    disconnected_selector_report = verify_widget_selectors(
        disconnected_selector_html,
        configured_widget_selector_config({"mall_id": "shop0001"}, disconnected_selector_html),
    )
    multiple_match_selector_html = """
    <html>
      <body>
        <form id="searchForm" action="/goods/goods_search.asp">
          <input id="keyword-a" class="shared-search" name="keyword" type="search" placeholder="상품명 검색">
          <button id="submit" type="submit">검색</button>
        </form>
        <input id="keyword-b" class="shared-search" name="orphanKeyword" type="search" placeholder="보조 검색창">
        <script src="/widget.js"></script>
        <script>HaeorumAISearch.init({attachToSearchInput:".shared-search",
        attachAfterSelector:"#submit",mallId:"shop0001",
        apiBaseUrl:"https://ai-search.haeorumgift.com"})</script>
      </body>
    </html>
    """
    multiple_match_selector_report = verify_widget_selectors(
        multiple_match_selector_html,
        configured_widget_selector_config({"mall_id": "shop0001"}, multiple_match_selector_html),
    )
    representative_related_url_report = check_all_product_url_rules(
        {
            "origin": "https://shop0001.haeorumgift.com",
            "expected_product_url_prefix": "https://shop0001.haeorumgift.com/product_view.asp",
        },
        [
            {
                "section": "top",
                "index": 0,
                "product": {
                    "product_id": "SIM-GOOD-URL",
                    "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-GOOD-URL",
                },
            },
            {
                "section": "items",
                "index": 0,
                "product": {
                    "product_id": "SIM-WRONG-MALL-URL",
                    "product_url": "https://shop9999.haeorumgift.com/product_view.asp?p_idx=SIM-WRONG-MALL-URL",
                },
            },
        ],
        name="text_all_product_url_rules",
    )
    representative_product_id_url_report = check_all_product_url_rules(
        {
            "origin": "https://shop0001.haeorumgift.com",
            "expected_product_url_prefix": "https://shop0001.haeorumgift.com/product_view.asp",
        },
        [
            {
                "section": "top",
                "index": 0,
                "product": {
                    "product_id": "SIM-GOOD-URL",
                    "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-GOOD-URL",
                },
            },
            {
                "section": "items",
                "index": 0,
                "product": {
                    "product_id": "SIM-WRONG-PRODUCT-ID",
                    "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-OTHER-PRODUCT",
                },
            },
        ],
        name="text_all_product_url_rules",
    )
    representative_response_mall_identity_report = build_representative_response_mall_identity_risk_report()
    api_load_response_mall_identity_report = build_api_load_response_mall_identity_risk_report()
    client_transport_reuse_risk_report = build_client_transport_reuse_risk_report(reference_images, mall_config)
    cache_coordination_risk_report = build_cache_coordination_risk_report(reference_images, mall_config)
    backend_transport_risk_report = build_backend_transport_risk_report(reference_images, mall_config)
    query_type_latency_risk_report = build_query_type_latency_risk_report(reference_images, mall_config)
    duplicate_saved_widget_html_report = build_duplicate_saved_widget_html_risk_report(paths, mall_config)
    service_mall_filter_report = build_service_mall_filter_risk_report()
    log_keep_open_path = paths.output_dir / "log-keep-open-probe.jsonl"
    if log_keep_open_path.exists():
        log_keep_open_path.unlink()
    log_keep_open_logger = SearchLogger(log_keep_open_path, keep_open_seconds=60)
    for index in range(10):
        log_keep_open_logger.write({"type": "search", "index": index})
    log_keep_open_tail = log_keep_open_logger.tail(20)
    log_keep_open_status = log_keep_open_logger.status()
    log_keep_open_logger.close()
    log_keep_open_closed_status = log_keep_open_logger.status()
    log_keep_open_report = {
        "path": str(log_keep_open_path),
        "status_before_close": log_keep_open_status,
        "status_after_close": log_keep_open_closed_status,
        "tail_count": len(log_keep_open_tail),
        "tail_indexes": [entry.get("index") for entry in log_keep_open_tail],
        "checks": {
            "flushes_visible_entries": len(log_keep_open_tail) == 10
            and [entry.get("index") for entry in log_keep_open_tail] == list(range(10)),
            "reuses_single_output_for_burst": log_keep_open_status.get("output_opens") == 1
            and int(log_keep_open_status.get("output_reuses") or 0) >= 9
            and log_keep_open_status.get("buffer_open") is True,
            "close_releases_output": log_keep_open_closed_status.get("buffer_open") is False
            and int(log_keep_open_closed_status.get("output_closes") or 0) >= 1,
        },
    }
    duplicate_representative_site_config = validate_site_collection(
        [
            {
                "name": "shop0001-a",
                "mall_id": "shop0001",
                "url": "https://shop0001.haeorumgift.com/",
                "origin": "https://shop0001.haeorumgift.com",
                "api_key": "pk_live_duplicate_representative_site_key",
            },
            {
                "name": "shop0001-b",
                "mall_id": "shop0001",
                "url": "https://shop0001.haeorumgift.com/",
                "origin": "https://shop0001.haeorumgift.com",
                "api_key": "pk_live_duplicate_representative_site_key",
            },
        ]
    )
    representative_api_key_mismatch_messages = representative_site_mall_config_problem_messages(
        [
            {
                "name": "shop0001-wrong-api-key",
                "mall_id": "shop0001",
                "url": "https://shop0001.haeorumgift.com/",
                "origin": "https://shop0001.haeorumgift.com",
                "api_base_url": "https://ai-search.haeorumgift.com",
                "api_key": "pk_live_wrong_representative_site_key",
                "expected_product_url_prefix": "https://shop0001.haeorumgift.com/product_view.asp",
            }
        ],
        {"mall_config": str(paths.malls_json)},
    )
    primary_api_key_mismatch_problems = input_file_validation_problems(
        {
            "required_files": {"mall_config": str(paths.malls_json)},
            "file_validation_context": {
                "expected_malls": "1",
                "mall_id": "shop0001",
                "origin": "https://shop0001.haeorumgift.com",
                "api_key_hash": api_key_hash("pk_live_wrong_primary_mall_key"),
            },
        }
    )
    primary_api_key_mismatch_messages = [
        str(message)
        for problem in primary_api_key_mismatch_problems
        for message in problem.get("problems", [])
    ]
    quality_case_mall_mismatch_messages: list[str] = []
    poc_products_for_quality = CsvProductSource(paths.poc_products_csv).fetch_all()
    wrong_mall_quality_product = next(
        (
            product
            for product in poc_products_for_quality
            if product.active
            and str(product.mall_id or "").strip()
            and str(product.mall_id or "").strip() != "shop0001"
            and str(product.category or "").strip()
        ),
        None,
    )
    if wrong_mall_quality_product is not None:
        quality_case_mall_mismatch_path = paths.output_dir / "quality-cases-risk-probe-wrong-mall.json"
        write_json(
            quality_case_mall_mismatch_path,
            {
                "cases": [
                    {
                        "name": "wrong_mall_expected_top",
                        "query": {"q": str(wrong_mall_quality_product.name or wrong_mall_quality_product.category)},
                        "expected_category": str(wrong_mall_quality_product.category or ""),
                        "expected_top_product_id": wrong_mall_quality_product.product_id,
                        "expected_min_results": 3,
                    }
                ]
            },
        )
        quality_case_mall_mismatch_problems = input_file_validation_problems(
            {
                "required_files": {"quality_cases_file": str(quality_case_mall_mismatch_path)},
                "file_validation_context": {
                    "poc_products_csv": str(paths.poc_products_csv),
                    "mall_id": "shop0001",
                },
            }
        )
        quality_case_mall_mismatch_messages = [
            str(message)
            for problem in quality_case_mall_mismatch_problems
            for message in problem.get("problems", [])
        ]
    image_probe_unsafe_redirect_blocked = False
    try:
        SafeImageRedirectHandler().redirect_request(
            urllib.request.Request("https://cdn.haeorumgift.com/products/safe-start.jpg"),
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data",
        )
    except UnsafeImageRedirectError:
        image_probe_unsafe_redirect_blocked = True
    image_probe_private_dns_blocked = False
    try:
        validate_image_url_resolves_to_public_network(
            "https://cdn.haeorumgift.com/products/rebound.jpg",
            resolver=lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.15", 443))
            ],
        )
    except UnsafeImageHostError:
        image_probe_private_dns_blocked = True
    private_target_validation_errors: list[str] = []
    for normalizer, value, field_name in [
        (normalize_public_http_base_url, "https://10.0.0.15", "--base-url"),
        (normalize_public_http_origin, "https://192.168.1.20", "--origin"),
    ]:
        try:
            normalizer(value, field_name)
        except ValueError as exc:
            private_target_validation_errors.append(str(exc))
    public_evidence_private_target_blocked = (
        len(private_target_validation_errors) == 2
        and all("non-public hosts" in error for error in private_target_validation_errors)
    )
    private_dns_target_validation_errors: list[str] = []
    try:
        validate_http_url_resolves_to_public_network(
            "https://ai-search.haeorumgift.com/api/ai-search",
            "request URL",
            resolver=lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.25", 443))
            ],
        )
    except ValueError as exc:
        private_dns_target_validation_errors.append(str(exc))
    public_evidence_private_dns_blocked = (
        len(private_dns_target_validation_errors) == 1
        and "non-public address" in private_dns_target_validation_errors[0]
    )
    placeholder_image_url_report = {
        "ok": False,
        "checked": 100,
        "failed": 0,
        "warning_count": 1,
        "warning_type_counts": {"placeholder_or_sample_image": 1},
        "blocking_warning_count": 1,
        "blocking_warning_type_counts": {"placeholder_or_sample_image": 1},
        "blocking_warnings": [
            {
                "product_id": "SIM-PLACEHOLDER-IMAGE",
                "image_url": "https://cdn.haeorumgift.com/noimage-placeholder.jpg",
                "warnings": ["placeholder_or_sample_image"],
                "attempts": 1,
            }
        ],
    }
    products, _lineage_parse_errors = parse_products(rows)
    active_product_count = len([product for product in products if product.active])
    inactive_product_count = len(products) - active_product_count
    poc_report = json.loads(paths.poc_products_csv.with_name("poc-dataset.json").read_text(encoding="utf-8"))
    poc_product_count = int(poc_report.get("selected_products") or 0)
    bad_poc_image_report = copy.deepcopy(poc_report)
    bad_poc_image_report["selected_unsafe_image_url_count"] = 1
    bad_poc_image_report["selected_unsafe_image_url_product_ids"] = ["SIM-RISK-UNSAFE-IMAGE"]
    bad_poc_image_report["selected_non_https_image_url_count"] = 1
    bad_poc_image_report["selected_non_https_image_url_product_ids"] = ["SIM-RISK-HTTP-IMAGE"]
    bad_poc_image_ok, _bad_poc_image_message, bad_poc_image_details = check_poc_dataset(bad_poc_image_report)
    missing_image_validation_csv_index_report = {
        "ok": True,
        "dry_run": False,
        "mode": "reindex",
        "csv": str(paths.poc_products_csv),
        "csv_fingerprint": file_fingerprint(paths.poc_products_csv),
        "engine": "marqo",
        "index": "haeorum-products-sim",
        "marqo_url": "http://marqo-api:8882",
        "marqo_model": "Marqo/marqo-ecommerce-embeddings-L",
        "persistent_index": True,
        "validate_images": False,
        "source": {
            "csv_is_builtin_sample": False,
            "dataset_is_builtin_sample_derived": False,
        },
        "summary": {
            "total_products": poc_product_count,
            "active_products": poc_product_count,
            "inactive_products": 0,
            "duplicate_product_ids": [],
            "missing_active_image_count": 0,
            "active_unsafe_image_url_count": 1,
            "active_unsafe_image_url_product_ids": ["SIM-RISK-UNSAFE-IMAGE"],
            "active_non_https_image_url_count": 1,
            "active_non_https_image_url_product_ids": ["SIM-RISK-HTTP-IMAGE"],
            "missing_active_product_url_count": 0,
            "missing_active_mall_id_count": 0,
            "active_unsafe_product_url_count": 0,
            "active_product_url_product_id_mismatch_count": 0,
        },
        "indexed": poc_product_count,
        "deleted": 0,
        "failed": 0,
        "expected_index_document_count": poc_product_count,
        "post_index_document_count": poc_product_count,
        "post_index_document_count_ok": True,
    }
    bad_csv_image_ok, _bad_csv_image_message, bad_csv_image_details = check_csv_index(
        missing_image_validation_csv_index_report
    )
    api_scale_runtime_identity_report = build_api_scale_runtime_identity_risk_report(
        paths,
        reference_images,
        mall_config,
    )
    lineage_reports = {
        "mssql_view": {
            "query_fingerprint": query_fingerprint_report(SIMULATED_MSSQL_QUERY),
        },
        "mssql_export": {
            "query_fingerprint": query_fingerprint_report(SIMULATED_MSSQL_QUERY),
            "output_csv_fingerprint": file_fingerprint(paths.products_csv),
            "exported_products": len(products),
            "active_products": active_product_count,
            "inactive_products": inactive_product_count,
        },
        "poc_dataset": {
            "source_csv_fingerprint": file_fingerprint(paths.products_csv),
            "output_csv_fingerprint": file_fingerprint(paths.poc_products_csv),
            "total_products": len(products),
            "active_products": active_product_count,
            "inactive_products": inactive_product_count,
            "selected_products": poc_product_count,
        },
        "image_urls": {
            "csv_fingerprint": file_fingerprint(paths.products_csv),
            "active_products": max(0, active_product_count - 1),
        },
        "quality_report": {
            "csv_fingerprint": file_fingerprint(paths.poc_products_csv),
            "source": {
                "index_name": "haeorum-products-sim",
                "marqo_url": "http://marqo-api:8882",
                "marqo_model": "Marqo/marqo-ecommerce-embeddings-L",
            },
            "dataset": {"active_products": poc_product_count},
        },
        "csv_poc_index": {
            "csv_fingerprint": file_fingerprint(paths.poc_products_csv),
            "index": "haeorum-products-sim",
            "marqo_url": "http://marqo-api:8882",
            "marqo_model": "Marqo/marqo-ecommerce-embeddings-L",
            "summary": {
                "total_products": poc_product_count,
                "active_products": poc_product_count,
                "inactive_products": 0,
            },
            "indexed": poc_product_count,
        },
    }
    lineage_ok, _lineage_message, lineage_details = check_data_lineage(lineage_reports)
    stale_marqo_lineage_reports = {
        "mssql_view": dict(lineage_reports["mssql_view"]),
        "mssql_export": dict(lineage_reports["mssql_export"]),
        "poc_dataset": dict(lineage_reports["poc_dataset"]),
        "image_urls": {
            **dict(lineage_reports["image_urls"]),
            "active_products": active_product_count,
        },
        "quality_report": dict(lineage_reports["quality_report"]),
        "csv_poc_index": dict(lineage_reports["csv_poc_index"]),
        "marqo_resource": {
            "index": "haeorum-products-sim",
            "marqo_url": "http://marqo-api:8882",
            "index_stats": {"ok": True, "data": {"numberOfDocuments": poc_product_count + 1}},
        },
    }
    stale_marqo_lineage_ok, _stale_marqo_message, stale_marqo_lineage_details = check_data_lineage(
        stale_marqo_lineage_reports
    )
    qwen_runtime_lineage_reports = {
        "mssql_view": dict(lineage_reports["mssql_view"]),
        "mssql_export": dict(lineage_reports["mssql_export"]),
        "poc_dataset": dict(lineage_reports["poc_dataset"]),
        "image_urls": {
            **dict(lineage_reports["image_urls"]),
            "active_products": active_product_count,
        },
        "quality_report": dict(lineage_reports["quality_report"]),
        "csv_poc_index": dict(lineage_reports["csv_poc_index"]),
        "marqo_resource": {
            "index": "haeorum-products-sim",
            "marqo_url": "http://marqo-api:8882",
            "index_stats": {"ok": True, "data": {"numberOfDocuments": poc_product_count}},
            "index_settings_contract": {
                "ok": True,
                "embedding_backend": "qwen",
                "qwen_model": "Qwen/Qwen3-VL-Embedding-2B",
                "actual_qwen_embedding_dimensions": 2048,
            },
            "qwen_embedding_contract": {
                "ok": True,
                "expected_model": "Qwen/Qwen3-VL-Embedding-2B",
                "health_model": "Qwen/Qwen3-VL-Embedding-2B",
                "probe_model": "Qwen/Qwen3-VL-Embedding-2B",
                "image_probe_model": "Qwen/Qwen3-VL-Embedding-2B",
                "expected_dimensions": 2048,
                "health_dimensions": 2048,
                "probe_dimensions": 2048,
                "image_probe_dimensions": 2048,
                "problems": [],
            },
        },
        "load_image": {
            "base_url": "https://ai-search.haeorumgift.com",
            "origin": "https://shop0001.haeorumgift.com",
            "mall_id": "shop0001",
            "server_metrics": {
                "after": {
                    "snapshot": {
                        "embedding_backend": "qwen",
                        "qwen_model": "Qwen/Qwen3-VL-Embedding-2B-old",
                        "qwen_embedding_dimensions": 1024,
                    }
                }
            },
        },
    }
    qwen_runtime_lineage_ok, _qwen_runtime_lineage_message, qwen_runtime_lineage_details = check_data_lineage(
        qwen_runtime_lineage_reports
    )
    bad_marqo_settings_report = build_simulated_marqo_resource_report()
    bad_marqo_settings_data = copy.deepcopy(bad_marqo_settings_report["index_settings"]["data"])
    bad_marqo_settings_data["modelProperties"]["dimensions"] = 1024
    bad_marqo_settings_data["tensorFields"] = ["qwen_text_vector"]
    bad_marqo_settings_report["index_settings"] = {"ok": True, "data": bad_marqo_settings_data}
    bad_marqo_settings_report["index_settings_contract"] = index_settings_contract(
        bad_marqo_settings_data,
        expected_model="Marqo/marqo-ecommerce-embeddings-L",
        embedding_backend="qwen",
        qwen_model="Qwen/Qwen3-VL-Embedding-2B",
        qwen_embedding_dimensions=2048,
    )
    bad_marqo_settings_report["ok"] = True
    bad_marqo_settings_ok, _bad_marqo_settings_message, bad_marqo_settings_details = check_marqo_resource(
        bad_marqo_settings_report
    )
    bad_qwen_contract_report = build_simulated_marqo_resource_report()
    bad_qwen_contract_report["qwen_health"] = {
        **bad_qwen_contract_report["qwen_health"],
        "data": {
            **bad_qwen_contract_report["qwen_health"]["data"],
            "model": "Qwen/Qwen3-VL-Embedding-2B-old",
            "dimensions": 1024,
        },
    }
    bad_qwen_contract_report["qwen_embedding_probe"] = {
        **bad_qwen_contract_report["qwen_embedding_probe"],
        "data": {
            **bad_qwen_contract_report["qwen_embedding_probe"]["data"],
            "model": "Qwen/Qwen3-VL-Embedding-2B-old",
            "dimensions": 1024,
            "embedding_sample_dimensions": 1024,
        },
    }
    bad_qwen_contract_report["qwen_image_embedding_probe"] = {
        **bad_qwen_contract_report["qwen_image_embedding_probe"],
        "data": {
            **bad_qwen_contract_report["qwen_image_embedding_probe"]["data"],
            "model": "Qwen/Qwen3-VL-Embedding-2B-old",
            "dimensions": 1024,
            "embedding_sample_dimensions": 1024,
        },
    }
    bad_qwen_contract_report["qwen_embedding_contract"] = qwen_embedding_contract(
        bad_qwen_contract_report["qwen_health"],
        bad_qwen_contract_report["qwen_embedding_probe"],
        bad_qwen_contract_report["qwen_image_embedding_probe"],
        expected_model="Qwen/Qwen3-VL-Embedding-2B",
        expected_dimensions=2048,
    )
    bad_qwen_contract_report["ok"] = True
    bad_qwen_contract_ok, _bad_qwen_contract_message, bad_qwen_contract_details = check_marqo_resource(
        bad_qwen_contract_report
    )
    saturated_marqo_resource_report = build_simulated_marqo_resource_report()
    saturated_marqo_resource_report["docker_stats"] = {
        **saturated_marqo_resource_report["docker_stats"],
        "cpu_percent": 96.0,
        "memory_percent": 92.0,
    }
    saturated_marqo_resource_report["resource_thresholds"] = resource_threshold_report(
        saturated_marqo_resource_report["docker_stats"],
        max_cpu_percent=90.0,
        max_memory_percent=85.0,
    )
    saturated_marqo_resource_report["ok"] = True
    saturated_marqo_ok, _saturated_marqo_message, saturated_marqo_details = check_marqo_resource(
        saturated_marqo_resource_report
    )
    full_storage_marqo_resource_report = build_simulated_marqo_resource_report()
    full_storage_marqo_resource_report["storage_usage"] = {
        **full_storage_marqo_resource_report["storage_usage"],
        "used_percent": 93.0,
        "available_bytes": 20 * 1024**3,
    }
    full_storage_marqo_resource_report["storage_thresholds"] = storage_threshold_report(
        full_storage_marqo_resource_report["storage_usage"],
        max_storage_percent=85.0,
        min_available_bytes=10 * 1024**3,
    )
    full_storage_marqo_resource_report["ok"] = True
    full_storage_marqo_ok, _full_storage_marqo_message, full_storage_marqo_details = check_marqo_resource(
        full_storage_marqo_resource_report
    )
    low_free_storage_marqo_resource_report = build_simulated_marqo_resource_report()
    low_free_storage_marqo_resource_report["storage_usage"] = {
        **low_free_storage_marqo_resource_report["storage_usage"],
        "used_percent": 42.0,
        "available_bytes": 1_073_741_824,
    }
    low_free_storage_marqo_resource_report["storage_thresholds"] = storage_threshold_report(
        low_free_storage_marqo_resource_report["storage_usage"],
        max_storage_percent=85.0,
        min_available_bytes=10 * 1024**3,
    )
    low_free_storage_marqo_resource_report["ok"] = True
    low_free_storage_marqo_ok, _low_free_storage_marqo_message, low_free_storage_marqo_details = check_marqo_resource(
        low_free_storage_marqo_resource_report
    )
    checks = {
        "multipart_missing_content_length_blocked": multipart_guard_report["checks"]["missing_content_length_blocked"],
        "multipart_invalid_header_blocked_before_form_parse": multipart_guard_report["checks"][
            "invalid_header_blocked_before_form_parse"
        ],
        "multipart_ip_rate_limit_before_form_parse": multipart_guard_report["checks"]["ip_rate_limit_before_form_parse"],
        "json_search_ip_rate_limit_before_body_parse": multipart_guard_report["checks"][
            "json_search_ip_rate_limit_before_body_parse"
        ],
        "click_ip_rate_limit_before_body_parse": multipart_guard_report["checks"][
            "click_ip_rate_limit_before_body_parse"
        ],
        "public_header_access_index_reused": multipart_guard_report["checks"]["header_access_index_reused"],
        "public_header_access_index_covers_malls": multipart_guard_report["checks"][
            "header_access_index_covers_malls"
        ],
        "public_mall_access_index_reused": multipart_guard_report["checks"]["mall_access_index_reused"],
        "public_mall_access_index_covers_malls": multipart_guard_report["checks"]["mall_access_index_covers_malls"],
        "public_mall_access_index_covers_origins": multipart_guard_report["checks"][
            "mall_access_index_covers_origins"
        ],
        "local_engine_mall_bucket_covers_malls": multipart_guard_report["checks"][
            "local_engine_mall_bucket_covers_malls"
        ],
        "local_engine_mall_bucket_limits_strict_search": multipart_guard_report["checks"][
            "local_engine_mall_bucket_limits_strict_search"
        ],
        "local_engine_prepares_filters_once_per_search": multipart_guard_report["checks"][
            "local_engine_prepares_filters_once_per_search"
        ],
        "multipart_image_queue_not_held_during_form_parse": multipart_guard_report["checks"][
            "image_queue_not_held_during_form_parse"
        ],
        "multipart_image_upload_slot_guarded": multipart_guard_report["checks"][
            "image_upload_slot_entered_before_upload_read"
        ],
        "image_search_service_uses_image_gate": multipart_guard_report["checks"]["image_search_service_uses_image_gate"],
        "image_validation_single_feature_decode": multipart_guard_report["checks"][
            "image_validation_single_feature_decode"
        ],
        "redis_cache_socket_timeout_configured": multipart_guard_report["checks"][
            "redis_cache_socket_timeout_configured"
        ],
        "redis_cache_failure_backoff_skips_hot_path": multipart_guard_report["checks"][
            "redis_cache_failure_backoff_skips_hot_path"
        ],
        "redis_rate_limit_socket_timeout_configured": multipart_guard_report["checks"][
            "redis_rate_limit_socket_timeout_configured"
        ],
        "redis_rate_limit_failure_backoff_uses_local_fallback": multipart_guard_report["checks"][
            "redis_rate_limit_failure_backoff_uses_local_fallback"
        ],
        "query_normalization_lru_cache_bounded": multipart_guard_report["checks"][
            "query_normalization_lru_cache_bounded"
        ],
        "query_normalization_repeated_terms_hit_cache": multipart_guard_report["checks"][
            "query_normalization_repeated_terms_hit_cache"
        ],
        "log_keep_open_flushes_visible_entries": log_keep_open_report["checks"]["flushes_visible_entries"],
        "log_keep_open_reuses_output_for_burst": log_keep_open_report["checks"]["reuses_single_output_for_burst"],
        "log_keep_open_close_releases_output": log_keep_open_report["checks"]["close_releases_output"],
        "bad_view_shape_blocked": view_report["ok"] is False,
        "bad_sample_quality_blocked": sample_quality_report["ok"] is False,
        "undersized_mssql_view_sample_blocked": undersized_view_ok is False
        and {"sample_size", "parsed_rows", "sample_rows"}.issubset(set(undersized_view_details.get("problems", []))),
        "server_preflight_open_file_limit_blocked": low_nofile_server_preflight_ok is False
        and "host_resources.open_file_limit_soft"
        in (low_nofile_server_preflight_details.get("problems") or []),
        "bad_export_blocked": export_report["ok"] is False,
        "future_updated_at_blocked": export_report["ok"] is False
        and int(export_report.get("future_updated_at_count") or 0) > 0
        and view_report["ok"] is False
        and int(view_report.get("future_updated_at_count") or 0) > 0,
        "bad_mall_id_blocked": bad_mall_config_report["ok"] is False
        and any(problem.get("field") == "mall_id" for problem in bad_mall_config_report.get("problems", [])),
        "mall_origin_policy_blocked": bad_mall_config_report["ok"] is False
        and any(problem.get("field") == "allowed_origins" for problem in mall_config_problems),
        "mall_duplicate_api_key_blocked": bad_mall_config_report["ok"] is False
        and bool(bad_mall_config_report.get("duplicate_api_keys")),
        "mall_duplicate_origin_blocked": bad_mall_config_report["ok"] is False
        and bool(bad_mall_config_report.get("duplicate_allowed_origins")),
        "mall_duplicate_product_url_prefix_blocked": bad_mall_config_report["ok"] is False
        and bool(bad_mall_config_report.get("duplicate_product_url_prefixes")),
        "mall_placeholder_api_key_blocked": bad_mall_config_report["ok"] is False
        and any(
            problem.get("field") == "api_key"
            and "sample or placeholder" in str(problem.get("message") or "")
            for problem in mall_config_problems
        ),
        "mall_weak_api_key_blocked": bad_mall_config_report["ok"] is False
        and "shop0004" in (bad_mall_config_report.get("weak_api_key_mall_ids") or []),
        "mall_product_template_blocked": bad_mall_config_report["ok"] is False
        and any(problem.get("field") == "product_url_template" for problem in mall_config_problems),
        "writer_permission_blocked": writer_permission_report["ok"] is False
        and "db_datawriter" in writer_permission_report.get("dangerous_roles", [])
        and "UPDATE" in writer_permission_report.get("dangerous_permissions", []),
        "export_mall_config_alignment_blocked": export_report["ok"] is False
        and (export_report.get("mall_config_alignment") or {}).get("ok") is False
        and (
            int(export_report.get("active_unknown_mall_id_count") or 0) > 0
            or int(export_report.get("active_product_url_mismatch_count") or 0) > 0
        ),
        "inconsistent_export_counts_blocked": inconsistent_export_ok is False
        and "active_inactive_exported_products_mismatch" in inconsistent_export_details.get("problems", [])
        and "mall_config_alignment.active_products_checked" in inconsistent_export_details.get("problems", []),
        "domain_filter_coverage_blocked": filterless_export_ok is False
        and "domain_filter_coverage" in filterless_export_details.get("problems", [])
        and {"price_range_count", "min_order_qty_count", "delivery_days_count", "print_methods_count", "materials_count", "colors_count"}.issubset(
            set(filterless_export_details.get("domain_filter_coverage_problems", []))
        ),
        "active_only_export_deletion_signal_blocked": active_only_export_report["ok"] is False
        and active_only_export_report.get("source_deletion_signal_ok") is False,
        "active_only_export_readiness_blocked": active_only_export_ok is False
        and "inactive_products" in active_only_export_details.get("problems", []),
        "partial_mssql_export_blocked": partial_export_ok is False
        and {"limit", "since_configured"}.issubset(set(partial_export_details.get("problems", []))),
        "mssql_export_fingerprint_path_mismatch_blocked": fingerprint_path_mismatch_export_ok is False
        and "output_csv_fingerprint.path_mismatch" in fingerprint_path_mismatch_export_details.get("problems", []),
        "widget_probe_unsafe_url_blocked": unsafe_widget_url_report["ok"] is False
        and {"unsafe_api_base_url", "unsafe_widget_src", "unsafe_page_url"}.issubset(
            set(unsafe_widget_url_report.get("risks") or [])
        ),
        "widget_probe_csp_risk_flagged": "external_widget_src_blocked_or_risky"
        in (csp_widget_risk_report.get("risks") or [])
        and csp_widget_risk_report.get("csp", {}).get("widget_src_allowed") is False,
        "widget_probe_api_connect_csp_risk_flagged": "api_connect_src_blocked_or_risky"
        in (csp_widget_risk_report.get("risks") or [])
        and csp_widget_risk_report.get("csp", {}).get("api_connect_allowed") is False,
        "widget_probe_missing_search_candidate_blocked": no_search_candidate_report["ok"] is False
        and "no_search_input_candidate" in (no_search_candidate_report.get("risks") or []),
        "widget_probe_duplicate_selector_blocked": duplicate_selector_widget_report["ok"] is False
        and "ambiguous_recommended_selectors" in (duplicate_selector_widget_report.get("risks") or [])
        and bool((duplicate_selector_widget_report.get("recommendation") or {}).get("selector_duplicate_ids")),
        "widget_probe_ambiguous_selector_fallback_ready": ambiguous_selector_fallback_widget_report["ok"] is True
        and (ambiguous_selector_fallback_widget_report.get("recommendation") or {}).get("mode") == "fallback_floating"
        and "ambiguous_recommended_selectors" in (ambiguous_selector_fallback_widget_report.get("review_risks") or [])
        and "ambiguous_recommended_selectors"
        not in (ambiguous_selector_fallback_widget_report.get("blocking_risks") or []),
        "widget_probe_multi_match_selector_blocked": multi_match_selector_widget_report["ok"] is False
        and "ambiguous_recommended_selectors" in (multi_match_selector_widget_report.get("risks") or [])
        and bool((multi_match_selector_widget_report.get("recommendation") or {}).get("selector_multiple_matches")),
        "representative_selector_relationship_blocked": disconnected_selector_report["selector_found"].get(
            "attachToSearchInput"
        )
        is False
        and "attachToSearchInput" in disconnected_selector_report.get("missing_selectors", []),
        "representative_multiple_match_selector_blocked": "attachToSearchInput"
        in multiple_match_selector_report.get("multiple_match_selectors", [])
        and "attachToSearchInput_selector_multiple_matches:2"
        in multiple_match_selector_report.get("selector_problems", []),
        "representative_all_result_product_url_blocked": representative_related_url_report["ok"] is False
        and representative_related_url_report.get("failed") == 1,
        "representative_product_url_product_id_mismatch_blocked": representative_product_id_url_report["ok"] is False
        and representative_product_id_url_report.get("failed") == 1
        and (representative_product_id_url_report.get("failures") or [{}])[0].get("product_id_match") is False,
        "representative_response_mall_id_mismatch_blocked": representative_response_mall_identity_report["ok"] is False
        and "mall_id_matches_request" in representative_response_mall_identity_report.get("meta_contract_problems", [])
        and any(
            str(problem).endswith("_mall_id_matches_request")
            for problem in representative_response_mall_identity_report.get("result_contract_problems", [])
        ),
        "api_smoke_response_mall_id_mismatch_blocked": api_load_response_mall_identity_report.get("ok") is False
        and api_load_response_mall_identity_report.get("api_smoke_meta_mismatch_blocked") is True
        and api_load_response_mall_identity_report.get("api_smoke_result_mismatch_blocked") is True,
        "load_response_mall_id_mismatch_blocked": api_load_response_mall_identity_report.get("ok") is False
        and api_load_response_mall_identity_report.get("load_meta_mismatch_blocked") is True
        and api_load_response_mall_identity_report.get("load_result_mismatch_blocked") is True,
    "api_smoke_product_url_product_id_mismatch_blocked": api_load_response_mall_identity_report.get("ok") is False
        and api_load_response_mall_identity_report.get("api_smoke_product_url_mismatch_blocked") is True,
    "api_smoke_product_url_prefix_mismatch_blocked": api_load_response_mall_identity_report.get("ok") is False
        and api_load_response_mall_identity_report.get("api_smoke_product_url_prefix_mismatch_blocked") is True,
        "load_product_url_product_id_mismatch_blocked": api_load_response_mall_identity_report.get("ok") is False
        and api_load_response_mall_identity_report.get("load_product_url_mismatch_blocked") is True,
        "load_product_url_prefix_mismatch_blocked": api_load_response_mall_identity_report.get("ok") is False
        and api_load_response_mall_identity_report.get("load_product_url_prefix_mismatch_blocked") is True,
        "load_client_transport_keep_alive_reuse_blocked": client_transport_reuse_risk_report.get(
            "load_client_transport_ok"
        )
        is False
        and "client_transport.search_requests.connection_reuses"
        in (client_transport_reuse_risk_report.get("load_client_transport_problems") or []),
        "api_scale_client_transport_keep_alive_reuse_blocked": client_transport_reuse_risk_report.get(
            "api_scale_ok"
        )
        is False
        and "multi.client_transport.search_requests.connection_reuses"
        in (client_transport_reuse_risk_report.get("api_scale_problems") or [])
        and "client_transport.search_requests.connection_reuses"
        in (client_transport_reuse_risk_report.get("api_scale_multi_client_transport_missing") or []),
        "load_client_transport_gzip_missing_blocked": client_transport_reuse_risk_report.get("load_client_gzip_ok")
        is False
        and "client_transport.search_requests.gzip_responses_zero"
        in (client_transport_reuse_risk_report.get("load_client_gzip_problems") or [])
        and "client_transport.search_requests.response_body_bytes_not_below_decoded"
        in (client_transport_reuse_risk_report.get("load_client_gzip_problems") or []),
        "api_scale_client_transport_gzip_missing_blocked": client_transport_reuse_risk_report.get(
            "api_scale_gzip_ok"
        )
        is False
        and "multi.client_transport.search_requests.gzip_responses_zero"
        in (client_transport_reuse_risk_report.get("api_scale_gzip_problems") or [])
        and "client_transport.search_requests.gzip_responses_zero"
        in (client_transport_reuse_risk_report.get("api_scale_gzip_multi_client_transport_missing") or []),
        "api_scale_request_profile_mismatch_blocked": client_transport_reuse_risk_report.get(
            "api_scale_request_profile_ok"
        )
        is False
        and client_transport_reuse_risk_report.get("api_scale_request_profile_comparable") is False
        and "comparison.request_profile"
        in (client_transport_reuse_risk_report.get("api_scale_request_profile_problems") or []),
        "api_scale_instance_distribution_blocked": client_transport_reuse_risk_report.get(
            "api_scale_instance_coverage_ok"
        )
        is False
        and "multi.api_instance.distinct_count"
        in (client_transport_reuse_risk_report.get("api_scale_instance_coverage_problems") or []),
        "api_scale_admin_metrics_source_coverage_blocked": client_transport_reuse_risk_report.get(
            "api_scale_admin_metrics_source_ok"
        )
        is False
        and "multi.admin_metrics.source_count_below_api_server_count"
        in (client_transport_reuse_risk_report.get("api_scale_admin_metrics_source_problems") or [])
        and "multi.admin_metrics.distinct_instance_count_below_api_server_count"
        in (client_transport_reuse_risk_report.get("api_scale_admin_metrics_source_problems") or []),
        "load_cache_lock_error_blocked": cache_coordination_risk_report.get("load_cache_lock_ok") is False
        and "server_metrics.delta.cache_lock_errors_nonzero"
        in (cache_coordination_risk_report.get("load_cache_lock_problems") or []),
        "api_scale_cache_lock_error_blocked": cache_coordination_risk_report.get("api_scale_cache_lock_ok") is False
        and "multi.server_metrics.delta.cache_lock_errors_nonzero"
        in (cache_coordination_risk_report.get("api_scale_cache_lock_problems") or []),
        "load_cache_lock_wait_timeout_blocked": cache_coordination_risk_report.get("load_cache_lock_wait_ok") is False
        and "server_metrics.delta.cache_lock_wait_timeouts_nonzero"
        in (cache_coordination_risk_report.get("load_cache_lock_wait_problems") or []),
        "api_scale_cache_lock_wait_timeout_blocked": cache_coordination_risk_report.get(
            "api_scale_cache_lock_wait_ok"
        )
        is False
        and "multi.server_metrics.delta.cache_lock_wait_timeouts_nonzero"
        in (cache_coordination_risk_report.get("api_scale_cache_lock_wait_problems") or []),
        "load_singleflight_wait_timeout_blocked": cache_coordination_risk_report.get("load_singleflight_ok") is False
        and "server_metrics.delta.singleflight_wait_timeouts_nonzero"
        in (cache_coordination_risk_report.get("load_singleflight_problems") or []),
        "api_scale_singleflight_wait_timeout_blocked": cache_coordination_risk_report.get(
            "api_scale_singleflight_ok"
        )
        is False
        and "multi.server_metrics.delta.singleflight_wait_timeouts_nonzero"
        in (cache_coordination_risk_report.get("api_scale_singleflight_problems") or []),
        "load_cache_lock_slow_wait_blocked": cache_coordination_risk_report.get(
            "load_cache_lock_slow_wait_ok"
        )
        is False
        and "server_metrics.delta.cache_lock_run_avg_wait_ms_above_threshold"
        in (cache_coordination_risk_report.get("load_cache_lock_slow_wait_problems") or []),
        "api_scale_cache_lock_slow_wait_blocked": cache_coordination_risk_report.get(
            "api_scale_cache_lock_slow_wait_ok"
        )
        is False
        and "multi.server_metrics.delta.cache_lock_run_avg_wait_ms_above_threshold"
        in (cache_coordination_risk_report.get("api_scale_cache_lock_slow_wait_problems") or [])
        and "server_metrics.delta.cache_lock_run_avg_wait_ms_above_threshold"
        in (cache_coordination_risk_report.get("api_scale_cache_lock_slow_wait_missing") or []),
        "load_search_queue_slow_wait_blocked": cache_coordination_risk_report.get(
            "load_search_queue_slow_wait_ok"
        )
        is False
        and "server_metrics.delta.search_queue_run_avg_wait_ms_above_threshold"
        in (cache_coordination_risk_report.get("load_search_queue_slow_wait_problems") or []),
        "api_scale_search_queue_slow_wait_blocked": cache_coordination_risk_report.get(
            "api_scale_search_queue_slow_wait_ok"
        )
        is False
        and "multi.server_metrics.delta.search_queue_run_avg_wait_ms_above_threshold"
        in (cache_coordination_risk_report.get("api_scale_search_queue_slow_wait_problems") or [])
        and "server_metrics.delta.search_queue_run_avg_wait_ms_above_threshold"
        in (cache_coordination_risk_report.get("api_scale_search_queue_slow_wait_missing") or []),
        "load_search_queue_in_flight_leak_blocked": cache_coordination_risk_report.get(
            "load_search_queue_stuck_ok"
        )
        is False
        and "server_metrics.after.search_queue_in_flight_nonzero"
        in (cache_coordination_risk_report.get("load_search_queue_stuck_problems") or []),
        "api_scale_search_queue_in_flight_leak_blocked": cache_coordination_risk_report.get(
            "api_scale_search_queue_stuck_ok"
        )
        is False
        and "multi.server_metrics.after.search_queue_in_flight_nonzero"
        in (cache_coordination_risk_report.get("api_scale_search_queue_stuck_problems") or [])
        and "server_metrics.after.search_queue_in_flight_nonzero"
        in (cache_coordination_risk_report.get("api_scale_search_queue_stuck_missing") or []),
        "load_system_memory_high_blocked": cache_coordination_risk_report.get("load_system_memory_high_ok")
        is False
        and "server_metrics.after.system_memory_used_percent_high"
        in (cache_coordination_risk_report.get("load_system_memory_high_problems") or []),
        "api_scale_system_memory_high_blocked": cache_coordination_risk_report.get(
            "api_scale_system_memory_high_ok"
        )
        is False
        and "multi.server_metrics.after.system_memory_used_percent_high"
        in (cache_coordination_risk_report.get("api_scale_system_memory_high_problems") or [])
        and "server_metrics.after.system_memory_used_percent_high"
        in (cache_coordination_risk_report.get("api_scale_system_memory_high_missing") or []),
        "load_process_rss_growth_blocked": cache_coordination_risk_report.get("load_process_rss_growth_ok")
        is False
        and "server_metrics.after.process_memory_rss_growth_mb_above_threshold"
        in (cache_coordination_risk_report.get("load_process_rss_growth_problems") or []),
        "api_scale_process_rss_growth_blocked": cache_coordination_risk_report.get(
            "api_scale_process_rss_growth_ok"
        )
        is False
        and "multi.server_metrics.after.process_memory_rss_growth_mb_above_threshold"
        in (cache_coordination_risk_report.get("api_scale_process_rss_growth_problems") or [])
        and "server_metrics.after.process_memory_rss_growth_mb_above_threshold"
        in (cache_coordination_risk_report.get("api_scale_process_rss_growth_missing") or []),
        "load_backend_marqo_error_response_blocked": backend_transport_risk_report.get("load_marqo_error_ok") is False
        and "server_metrics.delta.backend_marqo_error_responses_nonzero"
        in (backend_transport_risk_report.get("load_marqo_error_problems") or []),
        "api_scale_backend_marqo_error_response_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_error_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_error_responses_nonzero"
        in (backend_transport_risk_report.get("api_scale_marqo_error_problems") or []),
        "load_backend_marqo_connection_close_blocked": backend_transport_risk_report.get("load_marqo_close_ok") is False
        and "server_metrics.delta.backend_marqo_connection_close_responses_nonzero"
        in (backend_transport_risk_report.get("load_marqo_close_problems") or []),
        "api_scale_backend_marqo_connection_close_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_close_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_connection_close_responses_nonzero"
        in (backend_transport_risk_report.get("api_scale_marqo_close_problems") or []),
        "load_backend_marqo_retry_after_blocked": backend_transport_risk_report.get(
            "load_marqo_retry_after_ok"
        )
        is False
        and "server_metrics.delta.backend_marqo_retry_after_responses_nonzero"
        in (backend_transport_risk_report.get("load_marqo_retry_after_problems") or []),
        "api_scale_backend_marqo_retry_after_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_retry_after_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_retry_after_responses_nonzero"
        in (backend_transport_risk_report.get("api_scale_marqo_retry_after_problems") or []),
        "load_backend_marqo_latency_regression_blocked": backend_transport_risk_report.get("load_marqo_slow_ok")
        is False
        and "server_metrics.delta.backend_marqo_run_avg_elapsed_ms_above_p95"
        in (backend_transport_risk_report.get("load_marqo_slow_problems") or []),
        "api_scale_backend_marqo_latency_regression_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_slow_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_run_avg_elapsed_ms_above_p95"
        in (backend_transport_risk_report.get("api_scale_marqo_slow_problems") or []),
        "load_backend_marqo_gzip_missing_blocked": backend_transport_risk_report.get("load_marqo_gzip_ok") is False
        and "server_metrics.delta.backend_marqo_gzip_responses_zero"
        in (backend_transport_risk_report.get("load_marqo_gzip_problems") or []),
        "api_scale_backend_marqo_gzip_missing_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_gzip_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_gzip_responses_zero"
        in (backend_transport_risk_report.get("api_scale_marqo_gzip_problems") or []),
        "load_backend_marqo_payload_uncompressed_blocked": backend_transport_risk_report.get(
            "load_marqo_payload_ok"
        )
        is False
        and "server_metrics.delta.backend_marqo_response_body_bytes_not_below_decoded"
        in (backend_transport_risk_report.get("load_marqo_payload_problems") or []),
        "api_scale_backend_marqo_payload_uncompressed_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_payload_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_response_body_bytes_not_below_decoded"
        in (backend_transport_risk_report.get("api_scale_marqo_payload_problems") or []),
        "load_backend_marqo_zero_attempts_blocked": backend_transport_risk_report.get(
            "load_marqo_zero_attempts_ok"
        )
        is False
        and "server_metrics.delta.backend_marqo_request_attempts_zero"
        in (backend_transport_risk_report.get("load_marqo_zero_attempts_problems") or []),
        "api_scale_backend_marqo_zero_attempts_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_zero_attempts_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_request_attempts_zero"
        in (backend_transport_risk_report.get("api_scale_marqo_zero_attempts_problems") or []),
        "load_backend_marqo_below_unique_profile_blocked": backend_transport_risk_report.get(
            "load_marqo_below_profile_ok"
        )
        is False
        and "server_metrics.delta.backend_marqo_request_attempts_below_unique_requests"
        in (backend_transport_risk_report.get("load_marqo_below_profile_problems") or []),
        "api_scale_backend_marqo_below_unique_profile_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_below_profile_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_request_attempts_below_unique_requests"
        in (backend_transport_risk_report.get("api_scale_marqo_below_profile_problems") or []),
        "load_backend_marqo_circuit_open_blocked": backend_transport_risk_report.get("load_marqo_circuit_ok")
        is False
        and "server_metrics.delta.backend_marqo_circuit_open_events_nonzero"
        in (backend_transport_risk_report.get("load_marqo_circuit_problems") or []),
        "api_scale_backend_marqo_circuit_open_blocked": backend_transport_risk_report.get(
            "api_scale_marqo_circuit_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_marqo_circuit_open_events_nonzero"
        in (backend_transport_risk_report.get("api_scale_marqo_circuit_problems") or []),
        "load_backend_qwen_error_response_blocked": backend_transport_risk_report.get("load_qwen_error_ok") is False
        and "server_metrics.delta.backend_qwen_error_responses_nonzero"
        in (backend_transport_risk_report.get("load_qwen_error_problems") or []),
        "api_scale_backend_qwen_error_response_blocked": backend_transport_risk_report.get(
            "api_scale_qwen_error_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_qwen_error_responses_nonzero"
        in (backend_transport_risk_report.get("api_scale_qwen_error_problems") or []),
        "load_backend_qwen_connection_close_blocked": backend_transport_risk_report.get("load_qwen_close_ok") is False
        and "server_metrics.delta.backend_qwen_connection_close_responses_nonzero"
        in (backend_transport_risk_report.get("load_qwen_close_problems") or []),
        "api_scale_backend_qwen_connection_close_blocked": backend_transport_risk_report.get(
            "api_scale_qwen_close_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_qwen_connection_close_responses_nonzero"
        in (backend_transport_risk_report.get("api_scale_qwen_close_problems") or []),
        "load_backend_qwen_retry_after_blocked": backend_transport_risk_report.get("load_qwen_retry_after_ok")
        is False
        and "server_metrics.delta.backend_qwen_retry_after_responses_nonzero"
        in (backend_transport_risk_report.get("load_qwen_retry_after_problems") or []),
        "api_scale_backend_qwen_retry_after_blocked": backend_transport_risk_report.get(
            "api_scale_qwen_retry_after_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_qwen_retry_after_responses_nonzero"
        in (backend_transport_risk_report.get("api_scale_qwen_retry_after_problems") or []),
        "load_backend_qwen_latency_regression_blocked": backend_transport_risk_report.get("load_qwen_slow_ok")
        is False
        and "server_metrics.delta.backend_qwen_run_avg_elapsed_ms_above_p95"
        in (backend_transport_risk_report.get("load_qwen_slow_problems") or []),
        "api_scale_backend_qwen_latency_regression_blocked": backend_transport_risk_report.get(
            "api_scale_qwen_slow_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_qwen_run_avg_elapsed_ms_above_p95"
        in (backend_transport_risk_report.get("api_scale_qwen_slow_problems") or []),
        "load_backend_qwen_zero_attempts_blocked": backend_transport_risk_report.get("load_qwen_zero_attempts_ok")
        is False
        and "server_metrics.delta.backend_qwen_request_attempts_zero"
        in (backend_transport_risk_report.get("load_qwen_zero_attempts_problems") or []),
        "api_scale_backend_qwen_zero_attempts_blocked": backend_transport_risk_report.get(
            "api_scale_qwen_zero_attempts_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_qwen_request_attempts_zero"
        in (backend_transport_risk_report.get("api_scale_qwen_zero_attempts_problems") or []),
        "load_backend_qwen_below_unique_image_profile_blocked": backend_transport_risk_report.get(
            "load_qwen_below_profile_ok"
        )
        is False
        and "server_metrics.delta.backend_qwen_request_attempts_below_unique_image_inputs"
        in (backend_transport_risk_report.get("load_qwen_below_profile_problems") or []),
        "api_scale_backend_qwen_below_unique_image_profile_blocked": backend_transport_risk_report.get(
            "api_scale_qwen_below_profile_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_qwen_request_attempts_below_unique_image_inputs"
        in (backend_transport_risk_report.get("api_scale_qwen_below_profile_problems") or []),
        "load_backend_qwen_circuit_open_blocked": backend_transport_risk_report.get("load_qwen_circuit_ok")
        is False
        and "server_metrics.delta.backend_qwen_circuit_open_events_nonzero"
        in (backend_transport_risk_report.get("load_qwen_circuit_problems") or []),
        "api_scale_backend_qwen_circuit_open_blocked": backend_transport_risk_report.get(
            "api_scale_qwen_circuit_ok"
        )
        is False
        and "multi.server_metrics.delta.backend_qwen_circuit_open_events_nonzero"
        in (backend_transport_risk_report.get("api_scale_qwen_circuit_problems") or []),
        "backend_request_slot_saturation_guarded": backend_request_slot_report.get("ok") is True,
        "backend_request_slot_saturation_fast_fails": (
            backend_request_slot_report.get("checks") or {}
        ).get("slot_timeout_raises_503")
        is True
        and (backend_request_slot_report.get("checks") or {}).get("slot_timeout_metric_recorded") is True,
        "backend_request_slot_saturation_no_extra_backend_attempt": (
            backend_request_slot_report.get("checks") or {}
        ).get("overflow_does_not_start_backend_attempt")
        is True,
        "backend_request_slot_saturation_does_not_open_circuit": (
            backend_request_slot_report.get("checks") or {}
        ).get("slot_timeout_does_not_open_circuit")
        is True,
        "load_query_type_latency_regression_blocked": query_type_latency_risk_report.get(
            "load_query_type_latency_ok"
        )
        is False
        and "response_query_type_latency_ms.image.p99"
        in (query_type_latency_risk_report.get("load_query_type_latency_problems") or []),
        "api_scale_query_type_latency_regression_blocked": query_type_latency_risk_report.get("api_scale_ok")
        is False
        and "multi.query_type_latency" in (query_type_latency_risk_report.get("api_scale_problems") or [])
        and "response_query_type_latency_ms.image.p99"
        in (query_type_latency_risk_report.get("api_scale_multi_query_type_latency_problems") or []),
    "service_mall_config_search_filter_blocks_cross_mall": service_mall_filter_report.get("ok") is True
        and service_mall_filter_report.get("filter_by_mall_id") is True
        and service_mall_filter_report.get("mall_config_present") is True,
        "representative_duplicate_site_config_blocked": duplicate_representative_site_config["ok"] is False
        and {"duplicate_mall_ids", "duplicate_urls", "duplicate_origins", "duplicate_api_keys"}.issubset(
            set(duplicate_representative_site_config.get("problems") or [])
        ),
        "representative_saved_widget_html_duplicate_blocked": duplicate_saved_widget_html_report.get("ok") is False
        and any(
            "saved HTML source content duplicates" in message
            and "this representative site" in message
            for message in duplicate_saved_widget_html_report.get("duplicate_messages") or []
        ),
        "representative_api_key_mismatch_blocked": any(
            "api_key does not match mall_config" in message
            for message in representative_api_key_mismatch_messages
        ),
        "collector_primary_api_key_mismatch_blocked": any(
            "configured api_key does not match mall_config api_key" in message
            for message in primary_api_key_mismatch_messages
        ),
        "quality_case_mall_id_mismatch_blocked": any(
            "not configured mall_id" in message
            or "for configured mall_id" in message
            for message in quality_case_mall_mismatch_messages
        ),
        "placeholder_image_url_blocked": placeholder_image_url_report["ok"] is False
        and int(placeholder_image_url_report.get("blocking_warning_count") or 0) > 0,
        "poc_dataset_image_url_safety_blocked": bad_poc_image_ok is False
        and {"selected_unsafe_image_url_count", "selected_non_https_image_url_count"}.issubset(
            set(bad_poc_image_details.get("problems", []))
        ),
        "csv_index_image_validation_blocked": bad_csv_image_ok is False
        and {"validate_images", "active_unsafe_image_url_count", "active_non_https_image_url_count"}.issubset(
            set(bad_csv_image_details.get("problems", []))
        ),
        "api_scale_runtime_identity_blocked": api_scale_runtime_identity_report.get("ok") is False
        and {"comparison.marqo_model", "comparison.engine_index"}.issubset(
            set(api_scale_runtime_identity_report.get("problems") or [])
        ),
        "lineage_full_active_count_blocked": lineage_ok is False
        and "full_active_product_count_mismatch" in lineage_details.get("problems", []),
        "stale_marqo_document_count_blocked": stale_marqo_lineage_ok is False
        and "marqo_resource_documents_exceed_csv_index" in stale_marqo_lineage_details.get("problems", []),
        "qwen_runtime_identity_lineage_blocked": qwen_runtime_lineage_ok is False
        and {"qwen_model_mismatch", "qwen_embedding_dimensions_mismatch"}.issubset(
            set(qwen_runtime_lineage_details.get("problems") or [])
        ),
        "marqo_index_settings_mismatch_blocked": bad_marqo_settings_ok is False
        and "index_settings_contract" in bad_marqo_settings_details.get("missing_or_false", [])
        and {"modelProperties.dimensions", "tensorFields.qwen"}.issubset(
            set((bad_marqo_settings_details.get("index_settings_contract") or {}).get("problems") or [])
        ),
        "qwen_embedding_contract_mismatch_blocked": bad_qwen_contract_ok is False
        and "qwen_embedding_contract" in bad_qwen_contract_details.get("missing_or_false", [])
        and {
            "qwen_health.model",
            "qwen_embedding_probe.dimensions",
            "qwen_image_embedding_probe.dimensions",
        }.issubset(
            set((bad_qwen_contract_details.get("qwen_embedding_contract") or {}).get("problems") or [])
        ),
        "marqo_resource_saturation_blocked": saturated_marqo_ok is False
        and "resource_thresholds" in saturated_marqo_details.get("missing_or_false", [])
        and {"cpu_percent_high", "memory_percent_high"}.issubset(
            set((saturated_marqo_details.get("resource_thresholds") or {}).get("problems") or [])
        ),
        "marqo_storage_saturation_blocked": full_storage_marqo_ok is False
        and "storage_thresholds" in full_storage_marqo_details.get("missing_or_false", [])
        and "used_percent_high" in (
            (full_storage_marqo_details.get("storage_thresholds") or {}).get("problems") or []
        ),
        "marqo_storage_low_free_space_blocked": low_free_storage_marqo_ok is False
        and "storage_thresholds" in low_free_storage_marqo_details.get("missing_or_false", [])
        and "available_bytes_low" in (
            (low_free_storage_marqo_details.get("storage_thresholds") or {}).get("problems") or []
        ),
        "non_https_image_url_blocked": export_report["ok"] is False
        and int(export_report.get("active_non_https_image_url_count") or 0) > 0
        and view_report["ok"] is False
        and int(view_report.get("active_non_https_image_url_count") or 0) > 0,
        "missing_category_blocked": export_report["ok"] is False
        and int(export_report.get("active_missing_category_count") or 0) > 0
        and view_report["ok"] is False
        and int(view_report.get("active_missing_category_count") or 0) > 0,
        "negative_price_blocked": export_report["ok"] is False
        and int(export_report.get("active_negative_price_count") or 0) > 0
        and view_report["ok"] is False
        and int(view_report.get("active_negative_price_count") or 0) > 0,
        "source_product_url_product_id_mismatch_blocked": export_report["ok"] is False
        and int(export_report.get("active_product_url_product_id_mismatch_count") or 0) > 0
        and view_report["ok"] is False
        and int(view_report.get("active_product_url_product_id_mismatch_count") or 0) > 0,
        "image_probe_unsafe_redirect_blocked": image_probe_unsafe_redirect_blocked,
        "image_probe_private_dns_blocked": image_probe_private_dns_blocked,
        "public_evidence_private_target_blocked": public_evidence_private_target_blocked,
        "public_evidence_private_dns_blocked": public_evidence_private_dns_blocked,
    }
    export_problem_keys = [
        "duplicate_product_ids",
        "missing_updated_at_count",
        "invalid_updated_at_count",
        "future_updated_at_count",
        "active_missing_category_count",
        "active_missing_image_url_count",
        "active_missing_mall_id_count",
        "active_negative_price_count",
        "active_unsafe_image_url_count",
        "active_non_https_image_url_count",
        "active_unsafe_product_url_count",
        "active_product_url_product_id_mismatch_count",
        "active_unknown_mall_id_count",
        "active_product_url_mismatch_count",
    ]
    export_problems = ["parse_errors"] if export_report.get("parse_errors") else []
    export_problems.extend(key for key in export_problem_keys if export_report.get(key))
    if (export_report.get("domain_filter_coverage") or {}).get("ok") is not True:
        export_problems.append("domain_filter_coverage")
    return mark_simulated(
        {
            "ok": all(checks.values()),
            "checks": checks,
            "bad_row_count": len(bad_rows),
            "multipart_request_guard": multipart_guard_report,
            "view_problems": view_report.get("problems", []),
            "sample_quality_problems": sample_quality_report.get("problems", []),
            "undersized_mssql_view_sample": undersized_view_details,
            "low_nofile_server_preflight": low_nofile_server_preflight_details,
            "export_problems": export_problems,
            "parse_error_messages": [
                str(error.get("message") or "") for error in export_report.get("parse_errors", [])
            ][:10],
            "mall_config_problem_fields": mall_problem_fields,
            "mall_config_problem_messages": mall_problem_messages[:20],
            "bad_mall_config_report": bad_mall_config_report,
            "writer_permission_report": writer_permission_report,
            "inconsistent_export_details": inconsistent_export_details,
            "filterless_export_details": filterless_export_details,
            "active_only_export_details": active_only_export_details,
            "partial_mssql_export": partial_export_details,
            "mssql_export_fingerprint_path_mismatch": fingerprint_path_mismatch_export_details,
            "widget_probe_risks": {
                "unsafe_url": unsafe_widget_url_report.get("risks") or [],
                "csp": csp_widget_risk_report.get("risks") or [],
                "missing_search_candidate": no_search_candidate_report.get("risks") or [],
                "duplicate_selector": duplicate_selector_widget_report.get("risks") or [],
                "ambiguous_selector_fallback": ambiguous_selector_fallback_widget_report.get("risks") or [],
                "multi_match_selector": multi_match_selector_widget_report.get("risks") or [],
            },
            "widget_probe_duplicate_selector": {
                "duplicate_html_ids": duplicate_selector_widget_report.get("duplicate_html_ids") or {},
                "selector_duplicate_ids": (
                    (duplicate_selector_widget_report.get("recommendation") or {}).get("selector_duplicate_ids")
                    or {}
                ),
            },
            "widget_probe_ambiguous_selector_fallback": {
                "ok": ambiguous_selector_fallback_widget_report.get("ok"),
                "recommendation_mode": (
                    ambiguous_selector_fallback_widget_report.get("recommendation") or {}
                ).get("mode"),
                "blocking_risks": ambiguous_selector_fallback_widget_report.get("blocking_risks") or [],
                "review_risks": ambiguous_selector_fallback_widget_report.get("review_risks") or [],
            },
            "widget_probe_multi_match_selector": {
                "selector_match_counts": (
                    (multi_match_selector_widget_report.get("recommendation") or {}).get("selector_match_counts")
                    or {}
                ),
                "selector_multiple_matches": (
                    (multi_match_selector_widget_report.get("recommendation") or {}).get("selector_multiple_matches")
                    or {}
                ),
            },
            "representative_selector_relationship": disconnected_selector_report,
            "representative_multiple_match_selector": multiple_match_selector_report,
            "widget_probe_url_validation": unsafe_widget_url_report.get("url_validation") or {},
            "widget_probe_csp": csp_widget_risk_report.get("csp") or {},
            "public_evidence_private_target_errors": private_target_validation_errors,
            "public_evidence_private_dns_errors": private_dns_target_validation_errors,
            "placeholder_image_url_report": placeholder_image_url_report,
            "poc_dataset_image_url_safety": bad_poc_image_details,
            "csv_index_image_validation": bad_csv_image_details,
            "api_scale_runtime_identity": api_scale_runtime_identity_report,
            "lineage_full_active_count": lineage_details.get("full_product_counts") or {},
            "stale_marqo_document_count": stale_marqo_lineage_details.get("marqo_document_counts") or {},
            "qwen_runtime_identity_lineage": qwen_runtime_lineage_details,
            "marqo_index_settings_mismatch": bad_marqo_settings_details,
            "qwen_embedding_contract_mismatch": bad_qwen_contract_details,
            "marqo_resource_saturation": saturated_marqo_details,
            "marqo_storage_saturation": full_storage_marqo_details,
            "marqo_storage_low_free_space": low_free_storage_marqo_details,
            "representative_site_result_url_rule": representative_related_url_report,
            "representative_site_product_id_url_rule": representative_product_id_url_report,
            "representative_response_mall_identity": representative_response_mall_identity_report,
            "api_load_response_mall_identity": api_load_response_mall_identity_report,
            "client_transport_reuse_risk": client_transport_reuse_risk_report,
            "cache_coordination_risk": cache_coordination_risk_report,
            "backend_transport_risk": backend_transport_risk_report,
            "backend_request_slot_saturation": backend_request_slot_report,
            "query_type_latency_risk": query_type_latency_risk_report,
            "log_keep_open_probe": log_keep_open_report,
            "representative_saved_widget_html_duplicate": duplicate_saved_widget_html_report,
            "service_mall_config_search_filter": service_mall_filter_report,
            "representative_duplicate_site_config": duplicate_representative_site_config,
            "representative_site_risks": {
                "wrong_related_product_url": representative_related_url_report.get("failures") or [],
                "wrong_product_id_url": representative_product_id_url_report.get("failures") or [],
                "response_mall_identity": representative_response_mall_identity_report,
                "api_load_response_mall_identity": api_load_response_mall_identity_report,
                "client_transport_reuse": client_transport_reuse_risk_report,
                "cache_coordination": cache_coordination_risk_report,
                "backend_transport": backend_transport_risk_report,
                "backend_request_slot_saturation": backend_request_slot_report,
                "query_type_latency": query_type_latency_risk_report,
                "duplicate_saved_widget_html": duplicate_saved_widget_html_report.get("duplicate_messages") or [],
                "service_mall_config_search_filter": service_mall_filter_report,
                "wrong_api_key": representative_api_key_mismatch_messages,
            },
            "collector_primary_mall_risks": {
                "wrong_api_key": primary_api_key_mismatch_messages,
            },
            "quality_case_mall_identity_risks": {
                "wrong_mall_expected_top": quality_case_mall_mismatch_messages,
            },
            "simulation_note": "Negative controls intentionally inject malformed MSSQL-like rows, undersized MSSQL View samples, partial MSSQL exports, active-only export rows with no deleted/hidden signal, mismatched export fingerprint paths, stale Marqo document counts, mismatched Marqo index settings, mismatched Qwen runtime identity across evidence, saturated Marqo CPU/RAM, saturated Marqo storage, source product URLs that do not contain their product_id, mismatched API scale runtime identity, load/API scale client transport reports with no keep-alive reuse, cache miss lock errors, cache miss lock wait timeouts, high cache/queue average wait, singleflight wait timeouts, backend HTTP error and connection-close responses, backend active request slot saturation, per-query-type image latency regressions hidden by overall p95, writer permissions, bad representative-site widget inputs, duplicate saved PC/mobile HTML captures, duplicate HTML IDs and multi-match CSS selectors that make widget selectors ambiguous in both the offline snippet probe and representative-site validator, disconnected selector chains that do not match the actual DOM, bad related-result product URL inputs including same-mall URLs that point at the wrong product_id, bad quality cases whose expected top product belongs to another mall, mall_config search isolation with filter_by_mall_id=true, and bad representative/API/load response mall identity or product URL inputs so local checks prove those risks are blocked before production.",
        }
    )


def build_simulated_sync_lifecycle_report(paths: SimulationPaths, rows: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    changed_row = dict(rows[0])
    hidden_row = dict(rows[1])
    unchanged_row = dict(rows[2])
    stale_row = dict(rows[3])
    new_row = dict(rows[4])
    boundary_row = dict(rows[5])

    changed_row.update(
        {
            "product_name": "초기 동기화 검증 우산",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
            "updated_at": "2026-05-01T00:00:00Z",
        }
    )
    hidden_row.update(
        {
            "product_name": "숨김 전환 검증 텀블러",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
            "updated_at": "2026-05-01T00:05:00Z",
        }
    )
    unchanged_row.update(
        {
            "product_name": "변경 없음 검증 볼펜",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
            "updated_at": "2026-05-01T00:10:00Z",
        }
    )
    stale_row.update(
        {
            "product_id": "SIM-SYNC-STALE-DELETE",
            "product_name": "원본 누락 삭제 검증 타올",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
            "updated_at": "2026-05-01T00:15:00Z",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-STALE-DELETE",
            "mall_id": "shop0001",
        }
    )
    new_row.update(
        {
            "product_id": "SIM-SYNC-NEW-00001",
            "product_name": "신규 변경분 검증 텀블러",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
            "updated_at": "2026-05-20T10:10:00Z",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-NEW-00001",
            "mall_id": "shop0001",
        }
    )
    boundary_row.update(
        {
            "product_id": "SIM-SYNC-CUTOFF-BOUNDARY",
            "product_name": "컷오프 경계값 변경 전 볼펜",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
            "updated_at": "2026-05-01T00:20:00Z",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-CUTOFF-BOUNDARY",
            "mall_id": "shop0001",
        }
    )

    initial_rows = [changed_row, hidden_row, unchanged_row, stale_row, boundary_row]
    write_products(paths.sync_lifecycle_products_csv, initial_rows)
    if paths.sync_lifecycle_log.exists():
        paths.sync_lifecycle_log.unlink()

    settings = Settings(
        engine_backend="local",
        index_name="haeorum-products-simulation",
        product_csv_path=paths.sync_lifecycle_products_csv,
        sync_log_path=paths.sync_lifecycle_log,
        cache_ttl_seconds=30,
    )
    engine = LocalSearchEngine([])
    cache = MemorySearchCache(ttl_seconds=30)
    sync = SyncService(engine, CsvProductSource(paths.sync_lifecycle_products_csv), settings, search_cache=cache)

    initial_result = sync.reindex_all()

    changed_row_after = dict(changed_row)
    changed_row_after.update(
        {
            "product_name": "수정 반영 검증 우산",
            "keywords": str(changed_row.get("keywords") or "") + ",수정검증",
            "updated_at": "2026-05-20T10:00:00Z",
        }
    )
    hidden_row_after = dict(hidden_row)
    hidden_row_after.update({"display_yn": "N", "updated_at": "2026-05-20T10:05:00Z"})
    boundary_row_after = dict(boundary_row)
    boundary_row_after.update(
        {
            "product_name": "컷오프 경계값 반영 검증 볼펜",
            "keywords": str(boundary_row.get("keywords") or "") + ",경계값검증",
            "updated_at": "2026-05-20T00:00:00Z",
        }
    )
    updated_rows = [changed_row_after, hidden_row_after, unchanged_row, new_row, boundary_row_after]
    write_products(paths.sync_lifecycle_products_csv, updated_rows)

    changed_result = sync.sync_changed("2026-05-20T00:00:00Z")
    stale_delete_result = sync.reindex_product(
        str(stale_row["product_id"]),
        mall_id=str(stale_row.get("mall_id") or ""),
    )
    fresh_lock_payload = {
        "mode": "sync",
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    sync.lock_path.write_text(json.dumps(fresh_lock_payload, ensure_ascii=False) + "\n", encoding="utf-8")
    busy_result = sync.sync_changed("2026-05-20T00:00:00Z")
    stale_lock_payload = {
        "mode": "sync",
        "pid": 0,
        "host": socket.gethostname(),
        "started_at": "2020-01-01T00:00:00+00:00",
    }
    sync.lock_path.write_text(json.dumps(stale_lock_payload, ensure_ascii=False) + "\n", encoding="utf-8")
    stale_lock_recovered_result = sync.sync_changed("2026-05-20T00:00:00Z")

    duplicate_row_a = dict(changed_row)
    duplicate_row_a.update(
        {
            "product_id": "SIM-SYNC-DUPLICATE",
            "product_name": "중복 상품번호 검증 우산 A",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-DUPLICATE",
            "mall_id": "shop0001",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
        }
    )
    duplicate_row_b = dict(duplicate_row_a)
    duplicate_row_b.update(
        {
            "product_name": "중복 상품번호 검증 우산 B",
            "mall_id": "shop0001",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-DUPLICATE",
        }
    )
    duplicate_ok_row = dict(new_row)
    duplicate_ok_row.update(
        {
            "product_id": "SIM-SYNC-DUPLICATE-OK",
            "product_name": "중복 검증 정상 우산",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-DUPLICATE-OK",
            "mall_id": "shop0001",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
        }
    )
    duplicate_source_csv = paths.output_dir / "sync-duplicate-source-products.csv"
    duplicate_source_log = paths.output_dir / "sync-duplicate-source.jsonl"
    duplicate_single_log = paths.output_dir / "sync-duplicate-single-source.jsonl"
    write_products(duplicate_source_csv, [duplicate_row_a, duplicate_row_b, duplicate_ok_row])
    if duplicate_source_log.exists():
        duplicate_source_log.unlink()
    if duplicate_single_log.exists():
        duplicate_single_log.unlink()
    duplicate_settings = Settings(
        engine_backend="local",
        index_name="haeorum-products-simulation",
        product_csv_path=duplicate_source_csv,
        sync_log_path=duplicate_source_log,
    )
    duplicate_engine = LocalSearchEngine([])
    duplicate_sync = SyncService(
        duplicate_engine,
        CsvProductSource(duplicate_source_csv),
        duplicate_settings,
    )
    duplicate_source_result = duplicate_sync.reindex_all()
    duplicate_source_hits = [
        hit.document.product_id for hit in duplicate_engine.search(EngineQuery(q="중복 검증 우산", limit=10))
    ]
    duplicate_single_engine = LocalSearchEngine(
        [
            ProductDocument(
                product_id="SIM-SYNC-DUPLICATE",
                name="기존 중복 상품번호 우산",
                category="우산",
                status="active",
            )
        ]
    )
    duplicate_single_settings = Settings(
        engine_backend="local",
        index_name="haeorum-products-simulation",
        product_csv_path=duplicate_source_csv,
        sync_log_path=duplicate_single_log,
    )
    duplicate_single_sync = SyncService(
        duplicate_single_engine,
        CsvProductSource(duplicate_source_csv),
        duplicate_single_settings,
    )
    duplicate_single_result = duplicate_single_sync.reindex_product("SIM-SYNC-DUPLICATE")
    duplicate_single_hits = [
        hit.document.product_id
        for hit in duplicate_single_engine.search(EngineQuery(q="기존 중복 상품번호 우산", limit=10))
    ]
    duplicate_log_entries: list[dict[str, Any]] = []
    if duplicate_source_log.exists():
        for line in duplicate_source_log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                duplicate_log_entries.append(entry)
    duplicate_source_failures = [
        entry
        for entry in duplicate_log_entries
        if entry.get("type") == "sync_product_failed"
        and entry.get("action") == "validate_source"
        and entry.get("reason") == "duplicate_product_id"
    ]
    duplicate_single_log_entries: list[dict[str, Any]] = []
    if duplicate_single_log.exists():
        for line in duplicate_single_log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                duplicate_single_log_entries.append(entry)
    duplicate_single_failures = [
        entry
        for entry in duplicate_single_log_entries
        if entry.get("type") == "sync_batch_failed"
        and entry.get("action") == "fetch_product"
        and "multiple source products found" in str(entry.get("message") or "")
    ]

    mall_scoped_admin_csv = paths.output_dir / "sync-mall-scoped-admin-products.csv"
    mall_scoped_admin_log = paths.output_dir / "sync-mall-scoped-admin.jsonl"
    mall_scoped_admin_row = dict(changed_row)
    mall_scoped_admin_row.update(
        {
            "product_id": "SIM-SYNC-MALL-SCOPED-ADMIN",
            "product_name": "몰 지정 관리자 작업 검증 우산",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-MALL-SCOPED-ADMIN",
            "mall_id": "shop0001",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
        }
    )
    write_products(mall_scoped_admin_csv, [mall_scoped_admin_row])
    if mall_scoped_admin_log.exists():
        mall_scoped_admin_log.unlink()
    mall_scoped_admin_settings = Settings(
        engine_backend="local",
        index_name="haeorum-products-simulation",
        product_csv_path=mall_scoped_admin_csv,
        sync_log_path=mall_scoped_admin_log,
        filter_by_mall_id=True,
    )
    mall_scoped_admin_engine = LocalSearchEngine(
        [
            ProductDocument(
                product_id="SIM-SYNC-MALL-SCOPED-ADMIN",
                name="몰 지정 관리자 기존 우산",
                category="우산",
                status="active",
                mall_id="shop0001",
            )
        ]
    )
    mall_scoped_admin_sync = SyncService(
        mall_scoped_admin_engine,
        CsvProductSource(mall_scoped_admin_csv),
        mall_scoped_admin_settings,
    )
    mall_scoped_reindex_without_mall = mall_scoped_admin_sync.reindex_product("SIM-SYNC-MALL-SCOPED-ADMIN")
    mall_scoped_delete_without_mall = mall_scoped_admin_sync.delete_product("SIM-SYNC-MALL-SCOPED-ADMIN")
    mall_scoped_after_failed_delete_hits = [
        hit.document.product_id
        for hit in mall_scoped_admin_engine.search(
            EngineQuery(q="몰 지정 관리자", mall_id="shop0001", strict_mall_filter=True, limit=10)
        )
    ]
    mall_scoped_reindex_with_mall = mall_scoped_admin_sync.reindex_product(
        "SIM-SYNC-MALL-SCOPED-ADMIN",
        mall_id="shop0001",
    )
    mall_scoped_delete_with_mall = mall_scoped_admin_sync.delete_product(
        "SIM-SYNC-MALL-SCOPED-ADMIN",
        mall_id="shop0001",
    )
    mall_scoped_after_delete_hits = [
        hit.document.product_id
        for hit in mall_scoped_admin_engine.search(
            EngineQuery(q="몰 지정 관리자", mall_id="shop0001", strict_mall_filter=True, limit=10)
        )
    ]

    legacy_migration_csv = paths.output_dir / "sync-legacy-composite-migration-products.csv"
    legacy_migration_log = paths.output_dir / "sync-legacy-composite-migration.jsonl"
    legacy_migration_row = dict(changed_row)
    legacy_migration_row.update(
        {
            "product_id": "SIM-SYNC-LEGACY-MIGRATION",
            "product_name": "복합키 전환 검증 우산",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-LEGACY-MIGRATION",
            "mall_id": "shop0001",
            "status": "active",
            "display_yn": "Y",
            "is_deleted": "false",
        }
    )
    write_products(legacy_migration_csv, [legacy_migration_row])
    if legacy_migration_log.exists():
        legacy_migration_log.unlink()
    legacy_migration_settings = Settings(
        engine_backend="local",
        index_name="haeorum-products-simulation",
        product_csv_path=legacy_migration_csv,
        sync_log_path=legacy_migration_log,
    )
    legacy_migration_engine = LocalSearchEngine(
        [
            ProductDocument(
                product_id="SIM-SYNC-LEGACY-MIGRATION",
                name="레거시 상품번호 문서 우산",
                category="우산",
                status="active",
            )
        ]
    )
    legacy_migration_sync = SyncService(
        legacy_migration_engine,
        CsvProductSource(legacy_migration_csv),
        legacy_migration_settings,
    )
    legacy_migration_result = legacy_migration_sync.reindex_all()
    legacy_migration_hits = legacy_migration_engine.search(EngineQuery(q="우산", limit=10))
    legacy_migration_product_names = [hit.document.name for hit in legacy_migration_hits]
    legacy_migration_mall_ids = [hit.document.mall_id for hit in legacy_migration_hits]

    class PartialDeleteFailureEngine(LocalSearchEngine):
        def __init__(self, products: list[ProductDocument], failed_document_id: str):
            super().__init__(products)
            self.failed_document_id = failed_document_id

        def delete_products(self, product_ids: list[str]) -> dict[str, Any]:  # type: ignore[override]
            deleted = 0
            failed_products = []
            for product_id in product_ids:
                if product_id == self.failed_document_id:
                    failed_products.append({"document_id": product_id, "reason": "simulated_marqo_delete_failed"})
                    continue
                deleted += int(super().delete_products([product_id]).get("deleted", 0))
            return {"deleted": deleted, "failed": len(failed_products), "failed_products": failed_products}

    partial_delete_csv = paths.output_dir / "sync-partial-delete-products.csv"
    partial_delete_log = paths.output_dir / "sync-partial-delete.jsonl"
    partial_delete_row_a = dict(duplicate_row_a)
    partial_delete_row_a.update(
        {
            "product_id": "SIM-SYNC-PARTIAL-DELETE",
            "product_name": "부분 삭제 실패 1호점 우산",
            "product_url": "https://shop0001.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-PARTIAL-DELETE",
            "mall_id": "shop0001",
            "status": "inactive",
            "display_yn": "N",
        }
    )
    partial_delete_row_b = dict(partial_delete_row_a)
    partial_delete_row_b.update(
        {
            "product_name": "부분 삭제 실패 2호점 우산",
            "product_url": "https://shop0002.haeorumgift.com/product_view.asp?p_idx=SIM-SYNC-PARTIAL-DELETE",
            "mall_id": "shop0002",
        }
    )
    write_products(partial_delete_csv, [partial_delete_row_a, partial_delete_row_b])
    if partial_delete_log.exists():
        partial_delete_log.unlink()
    partial_failed_document_id = product_document_id("shop0002", "SIM-SYNC-PARTIAL-DELETE")
    partial_delete_engine = PartialDeleteFailureEngine(
        [
            ProductDocument(
                product_id="SIM-SYNC-PARTIAL-DELETE",
                name="부분 삭제 실패 1호점 기존 우산",
                category="우산",
                status="active",
                mall_id="shop0001",
            ),
            ProductDocument(
                product_id="SIM-SYNC-PARTIAL-DELETE",
                name="부분 삭제 실패 2호점 기존 우산",
                category="우산",
                status="active",
                mall_id="shop0002",
            ),
        ],
        partial_failed_document_id,
    )
    partial_delete_settings = Settings(
        engine_backend="local",
        index_name="haeorum-products-simulation",
        product_csv_path=partial_delete_csv,
        sync_log_path=partial_delete_log,
    )
    partial_delete_sync = SyncService(
        partial_delete_engine,
        CsvProductSource(partial_delete_csv),
        partial_delete_settings,
    )
    partial_delete_result = partial_delete_sync.reindex_all()
    partial_shop0001_hits = [
        hit.document.product_id
        for hit in partial_delete_engine.search(
            EngineQuery(q="부분 삭제 실패", mall_id="shop0001", strict_mall_filter=True, limit=10)
        )
    ]
    partial_shop0002_hits = [
        hit.document.product_id
        for hit in partial_delete_engine.search(
            EngineQuery(q="부분 삭제 실패", mall_id="shop0002", strict_mall_filter=True, limit=10)
        )
    ]
    partial_delete_log_entries: list[dict[str, Any]] = []
    if partial_delete_log.exists():
        for line in partial_delete_log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                partial_delete_log_entries.append(entry)
    partial_delete_events = [
        entry for entry in partial_delete_log_entries if entry.get("action") == "delete_from_index"
    ]
    partial_delete_requested = [entry for entry in partial_delete_events if entry.get("outcome") == "requested"]
    partial_delete_failed = [entry for entry in partial_delete_events if entry.get("outcome") == "failed"]

    log_entries: list[dict[str, Any]] = []
    if paths.sync_lifecycle_log.exists():
        for line in paths.sync_lifecycle_log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                log_entries.append(entry)

    changed_id = str(changed_row["product_id"])
    hidden_id = str(hidden_row["product_id"])
    unchanged_id = str(unchanged_row["product_id"])
    stale_id = str(stale_row["product_id"])
    new_id = str(new_row["product_id"])
    boundary_id = str(boundary_row["product_id"])
    final_changed_hits = [hit.document.product_id for hit in engine.search(EngineQuery(q="수정검증 우산", limit=10))]
    final_hidden_hits = [hit.document.product_id for hit in engine.search(EngineQuery(q="숨김 전환 검증", limit=10))]
    final_unchanged_hits = [hit.document.product_id for hit in engine.search(EngineQuery(q="변경 없음 검증", limit=10))]
    final_stale_hits = [hit.document.product_id for hit in engine.search(EngineQuery(q="원본 누락 삭제", limit=10))]
    final_new_hits = [hit.document.product_id for hit in engine.search(EngineQuery(q="신규 변경분 검증", limit=10))]
    final_boundary_hits = [hit.document.product_id for hit in engine.search(EngineQuery(q="경계값검증 볼펜", limit=10))]

    cache_clear_events = [entry for entry in log_entries if entry.get("type") == "search_cache_cleared"]
    product_events = [entry for entry in log_entries if entry.get("type") == "sync_product_event"]
    hidden_delete_logged = any(
        entry.get("product_id") == hidden_id and entry.get("action") == "delete_from_index"
        for entry in product_events
    )
    stale_delete_logged = any(
        entry.get("product_id") == stale_id
        and entry.get("action") == "delete_from_index"
        and entry.get("reason") == "source_product_missing"
        for entry in product_events
    )
    lock_busy_events = [
        entry
        for entry in log_entries
        if entry.get("type") == "sync_batch_failed" and entry.get("action") == "acquire_sync_lock"
    ]

    checks = {
        "initial_reindex_indexed_active_products": initial_result.indexed == len(initial_rows) and initial_result.failed == 0,
        "incremental_since_cutoff_applied": changed_result.indexed == 3 and changed_result.deleted == 1 and changed_result.failed == 0,
        "incremental_cutoff_boundary_included": boundary_id in final_boundary_hits,
        "changed_product_searchable_after_incremental_sync": changed_id in final_changed_hits,
        "new_product_searchable_after_incremental_sync": new_id in final_new_hits,
        "unchanged_product_preserved_after_incremental_sync": unchanged_id in final_unchanged_hits,
        "hidden_product_removed_from_index": hidden_id not in final_hidden_hits,
        "missing_source_product_deleted": stale_delete_result.deleted == 1 and stale_id not in final_stale_hits,
        "product_delete_events_logged": hidden_delete_logged and stale_delete_logged,
        "search_cache_invalidation_logged": len(cache_clear_events) >= 3,
        "fresh_sync_lock_blocks_parallel_run": busy_result.failed == 1 and busy_result.indexed == 0 and busy_result.deleted == 0,
        "sync_lock_contention_logged": len(lock_busy_events) >= 1,
        "stale_sync_lock_recovered": stale_lock_recovered_result.failed == 0 and not sync.lock_path.exists(),
        "duplicate_source_product_ids_blocked": duplicate_source_result.indexed == 1
        and duplicate_source_result.failed == 2
        and "SIM-SYNC-DUPLICATE-OK" in duplicate_source_hits
        and "SIM-SYNC-DUPLICATE" not in duplicate_source_hits
        and len(duplicate_source_failures) == 2
        and all((entry.get("details") or {}).get("duplicate_count") == 2 for entry in duplicate_source_failures),
        "duplicate_single_reindex_blocked": duplicate_single_result.indexed == 0
        and duplicate_single_result.deleted == 0
        and duplicate_single_result.failed == 1
        and "SIM-SYNC-DUPLICATE" in duplicate_single_hits
        and len(duplicate_single_failures) == 1,
        "mall_scoped_product_admin_requires_mall_id": mall_scoped_reindex_without_mall.failed == 1
        and mall_scoped_reindex_without_mall.indexed == 0
        and mall_scoped_delete_without_mall.failed == 1
        and mall_scoped_delete_without_mall.deleted == 0
        and "SIM-SYNC-MALL-SCOPED-ADMIN" in mall_scoped_after_failed_delete_hits
        and mall_scoped_reindex_with_mall.indexed == 1
        and mall_scoped_delete_with_mall.deleted == 1
        and not mall_scoped_after_delete_hits,
        "legacy_product_id_document_removed_after_composite_upsert": legacy_migration_result.indexed == 1
        and legacy_migration_result.failed == 0
        and legacy_migration_product_names == ["복합키 전환 검증 우산"]
        and legacy_migration_mall_ids == ["shop0001"],
        "partial_delete_failure_keeps_document_identity": partial_delete_result.deleted == 1
        and partial_delete_result.failed == 1
        and not partial_shop0001_hits
        and "SIM-SYNC-PARTIAL-DELETE" in partial_shop0002_hits
        and [entry.get("mall_id") for entry in partial_delete_requested] == ["shop0001"]
        and [entry.get("mall_id") for entry in partial_delete_failed] == ["shop0002"]
        and (partial_delete_failed[0].get("details") or {}).get("document_id") == partial_failed_document_id,
    }
    return mark_simulated(
        {
            "ok": all(checks.values()),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "checks": checks,
            "products_csv": str(paths.sync_lifecycle_products_csv),
            "sync_log": str(paths.sync_lifecycle_log),
            "initial_result": initial_result.model_dump(mode="json"),
            "changed_result": changed_result.model_dump(mode="json"),
            "stale_delete_result": stale_delete_result.model_dump(mode="json"),
            "busy_lock_result": busy_result.model_dump(mode="json"),
            "stale_lock_recovered_result": stale_lock_recovered_result.model_dump(mode="json"),
            "duplicate_source_result": duplicate_source_result.model_dump(mode="json"),
            "duplicate_single_result": duplicate_single_result.model_dump(mode="json"),
            "mall_scoped_reindex_without_mall": mall_scoped_reindex_without_mall.model_dump(mode="json"),
            "mall_scoped_delete_without_mall": mall_scoped_delete_without_mall.model_dump(mode="json"),
            "mall_scoped_reindex_with_mall": mall_scoped_reindex_with_mall.model_dump(mode="json"),
            "mall_scoped_delete_with_mall": mall_scoped_delete_with_mall.model_dump(mode="json"),
            "legacy_migration_result": legacy_migration_result.model_dump(mode="json"),
            "partial_delete_result": partial_delete_result.model_dump(mode="json"),
            "duplicate_source_products_csv": str(duplicate_source_csv),
            "duplicate_source_sync_log": str(duplicate_source_log),
            "duplicate_single_sync_log": str(duplicate_single_log),
            "mall_scoped_admin_products_csv": str(mall_scoped_admin_csv),
            "mall_scoped_admin_sync_log": str(mall_scoped_admin_log),
            "legacy_migration_products_csv": str(legacy_migration_csv),
            "legacy_migration_sync_log": str(legacy_migration_log),
            "partial_delete_products_csv": str(partial_delete_csv),
            "partial_delete_sync_log": str(partial_delete_log),
            "product_ids": {
                "changed": changed_id,
                "hidden": hidden_id,
                "unchanged": unchanged_id,
                "stale_missing": stale_id,
                "new": new_id,
                "cutoff_boundary": boundary_id,
            },
            "final_search_hits": {
                "changed": final_changed_hits,
                "hidden": final_hidden_hits,
                "unchanged": final_unchanged_hits,
                "stale_missing": final_stale_hits,
                "new": final_new_hits,
                "cutoff_boundary": final_boundary_hits,
                "duplicate_source": duplicate_source_hits,
                "duplicate_single_reindex": duplicate_single_hits,
                "mall_scoped_after_failed_delete": mall_scoped_after_failed_delete_hits,
                "mall_scoped_after_delete": mall_scoped_after_delete_hits,
                "legacy_migration_names": legacy_migration_product_names,
                "legacy_migration_mall_ids": legacy_migration_mall_ids,
                "partial_delete_shop0001": partial_shop0001_hits,
                "partial_delete_shop0002": partial_shop0002_hits,
            },
            "cache_clear_event_count": len(cache_clear_events),
            "product_event_count": len(product_events),
            "lock_busy_event_count": len(lock_busy_events),
            "duplicate_source_failure_count": len(duplicate_source_failures),
            "duplicate_single_failure_count": len(duplicate_single_failures),
            "partial_delete_failed_document_id": partial_failed_document_id,
            "partial_delete_requested_count": len(partial_delete_requested),
            "partial_delete_failed_count": len(partial_delete_failed),
            "sync_lock_path": str(sync.lock_path),
            "hidden_delete_logged": hidden_delete_logged,
            "stale_delete_logged": stale_delete_logged,
            "simulation_note": "Exercises changed-product sync, inclusive updated_at cutoff handling, hidden product removal, missing-source product deletion, duplicate source product_id fail-closed handling, mall-scoped partial delete failure identity, cache invalidation logs, product-level sync events, sync lock contention logging, and stale lock recovery without touching production MSSQL.",
        }
    )


def load_products(path: Path) -> list[ProductDocument]:
    return CsvProductSource(path).fetch_all()


def image_base64(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((percent / 100.0) * len(ordered)) - 1))
    return round(ordered[index], 2)


def latency_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "avg_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    return {
        "count": len(values),
        "avg_ms": round(sum(values) / len(values), 2),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "max_ms": round(max(values), 2),
    }


def validate_probe_response(payload: dict[str, Any], result: Any, expected: dict[str, Any]) -> list[str]:
    products = result.top + result.items
    categories = [item.category for item in products]
    errors = []
    expected_category = expected.get("category")
    if expected_category and expected_category not in categories:
        errors.append(f"expected category {expected_category!r} not found")
    if expected.get("all_category"):
        wrong = sorted({item.category for item in products if item.category != expected["all_category"]})
        if wrong:
            errors.append(f"category filter leaked categories: {wrong}")
    if expected.get("max_price") is not None:
        max_price = float(expected["max_price"])
        high_prices = [item.product_id for item in products if item.price is not None and item.price > max_price]
        if high_prices:
            errors.append(f"max_price filter leaked products: {high_prices[:5]}")
    if expected.get("low_confidence") is not None and result.meta.low_confidence is not bool(expected["low_confidence"]):
        errors.append("low confidence expectation failed")
    if not products and not expected.get("allow_empty"):
        errors.append("search returned no products")
    return errors


def run_mixed_weight_sweep(paths: SimulationPaths, reference_images: dict[str, dict[str, Any]]) -> dict[str, Any]:
    products = load_products(paths.poc_products_csv)
    settings = Settings(
        engine_backend="local",
        product_csv_path=paths.poc_products_csv,
        search_log_path=paths.output_dir / "mixed-weight-sweep-search.jsonl",
        product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
        cache_ttl_seconds=0,
    )
    service = AISearchService(LocalSearchEngine(products), settings, SearchLogger(settings.search_log_path))
    profiles = [
        {"name": "text_heavy", "text_weight": 0.70, "image_weight": 0.30},
        {"name": "balanced", "text_weight": 0.50, "image_weight": 0.50},
        {"name": "current_default", "text_weight": settings.mixed_text_weight, "image_weight": settings.mixed_image_weight},
        {"name": "image_heavy", "text_weight": 0.25, "image_weight": 0.75},
    ]
    cases = [
        {
            "name": "aligned_umbrella",
            "q": "\uac80\uc740 \uc6b0\uc0b0",
            "image_base64": image_base64(reference_images["umbrella"]["path"]),
            "expected_category": "\uc6b0\uc0b0",
            "scored": True,
        },
        {
            "name": "aligned_tumbler",
            "q": "\uc2a4\ud150 \ud140\ube14\ub7ec",
            "image_base64": image_base64(reference_images["tumbler"]["path"]),
            "expected_category": "\ud140\ube14\ub7ec",
            "scored": True,
        },
        {
            "name": "text_rescues_off_topic_image",
            "q": "\uc2a4\ud150 \ud140\ube14\ub7ec",
            "image_base64": image_base64(reference_images["off_topic"]["path"]),
            "expected_category": "\ud140\ube14\ub7ec",
            "scored": True,
        },
        {
            "name": "conflicting_text_umbrella_image_tumbler",
            "q": "\uc6b0\uc0b0",
            "image_base64": image_base64(reference_images["tumbler"]["path"]),
            "expected_category": None,
            "scored": False,
        },
    ]
    profile_summaries: list[dict[str, Any]] = []
    scored_case_count = sum(1 for case in cases if case["scored"])
    for profile in profiles:
        case_results = []
        elapsed_values: list[float] = []
        top_category_hits = 0
        top3_category_hits = 0
        conflict_top_categories: dict[str, str] = {}
        source_margin_values: list[float] = []
        for case in cases:
            started = time.perf_counter()
            response = service.search(
                SearchRequest.model_validate(
                    {
                        "mall_id": "shop0001",
                        "q": case["q"],
                        "image_base64": case["image_base64"],
                        "limit": 20,
                        "text_weight": profile["text_weight"],
                        "image_weight": profile["image_weight"],
                    }
                )
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            elapsed_values.append(elapsed_ms)
            top_categories = [item.category for item in response.top]
            first = response.top[0] if response.top else None
            top_category = first.category if first else None
            top_product_id = first.product_id if first else None
            top_source_scores = first.source_scores if first else {}
            expected_category = case["expected_category"]
            top_category_ok = bool(expected_category and top_category == expected_category)
            top3_category_ok = bool(expected_category and expected_category in top_categories)
            if case["scored"]:
                top_category_hits += 1 if top_category_ok else 0
                top3_category_hits += 1 if top3_category_ok else 0
            else:
                conflict_top_categories[case["name"]] = str(top_category or "")
            if top_source_scores:
                source_margin_values.append(
                    round(float(top_source_scores.get("image", 0.0)) - float(top_source_scores.get("text", 0.0)), 4)
                )
            case_results.append(
                {
                    "name": case["name"],
                    "scored": case["scored"],
                    "expected_category": expected_category,
                    "top_category": top_category,
                    "top_product_id": top_product_id,
                    "top_categories": top_categories,
                    "top_category_ok": top_category_ok if case["scored"] else None,
                    "top3_category_ok": top3_category_ok if case["scored"] else None,
                    "top_score_percent": first.score_percent if first else None,
                    "top_source_scores": top_source_scores,
                    "low_confidence": response.meta.low_confidence,
                    "elapsed_ms": elapsed_ms,
                }
            )
        profile_summaries.append(
            {
                "name": profile["name"],
                "text_weight": profile["text_weight"],
                "image_weight": profile["image_weight"],
                "scored_case_count": scored_case_count,
                "top_category_hits": top_category_hits,
                "top3_category_hits": top3_category_hits,
                "top_category_rate": round(top_category_hits / scored_case_count, 4) if scored_case_count else 0,
                "top3_category_rate": round(top3_category_hits / scored_case_count, 4) if scored_case_count else 0,
                "latency_ms": latency_summary(elapsed_values),
                "avg_image_minus_text_source_margin": round(sum(source_margin_values) / len(source_margin_values), 4)
                if source_margin_values
                else None,
                "conflict_top_categories": conflict_top_categories,
                "cases": case_results,
            }
        )
    best = sorted(
        profile_summaries,
        key=lambda item: (
            -float(item["top_category_rate"]),
            -float(item["top3_category_rate"]),
            float((item["latency_ms"] or {}).get("avg_ms") or 0),
        ),
    )[0]
    current = next(profile for profile in profile_summaries if profile["name"] == "current_default")
    current_close_to_best = (
        float(best["top_category_rate"]) - float(current["top_category_rate"]) <= 0.0001
        and float(best["top3_category_rate"]) - float(current["top3_category_rate"]) <= 0.0001
    )
    conflicts = []
    conflict_case_names = [case["name"] for case in cases if not case["scored"]]
    for case_name in conflict_case_names:
        observed = {
            profile["name"]: (profile.get("conflict_top_categories") or {}).get(case_name)
            for profile in profile_summaries
        }
        unique_categories = sorted({category for category in observed.values() if category})
        conflicts.append(
            {
                "case": case_name,
                "observed_top_categories": observed,
                "unique_top_categories": unique_categories,
                "switched_top_category_by_weight": len(unique_categories) > 1,
            }
        )
    conflict_has_shift = any(conflict["switched_top_category_by_weight"] for conflict in conflicts)
    recommendations = []
    if not current_close_to_best:
        recommendations.append(
            f"current default underperformed local best profile {best['name']} on scored mixed cases"
        )
    if conflict_has_shift:
        recommendations.append("conflicting text/image signals change top category by weight; keep collecting click logs before changing defaults")
    if not recommendations:
        recommendations.append("current mixed weights are locally competitive; validate with production click-through evidence before changing")
    return mark_simulated(
        {
            "ok": all(profile["top3_category_hits"] == scored_case_count for profile in profile_summaries),
            "coverage_ok": len(profile_summaries) >= 4 and scored_case_count >= 3 and bool(conflict_case_names),
            "current_default_close_to_best": current_close_to_best,
            "best_profile": {
                "name": best["name"],
                "text_weight": best["text_weight"],
                "image_weight": best["image_weight"],
                "top_category_rate": best["top_category_rate"],
                "top3_category_rate": best["top3_category_rate"],
            },
            "current_default_profile": {
                "name": current["name"],
                "text_weight": current["text_weight"],
                "image_weight": current["image_weight"],
                "top_category_rate": current["top_category_rate"],
                "top3_category_rate": current["top3_category_rate"],
            },
            "profiles": profile_summaries,
            "conflict_sensitivity": conflicts,
            "recommendations": recommendations,
            "simulation_note": "Local mixed-search sweep compares text/image weights on aligned, off-topic, and conflicting synthetic cases; production click logs are still required before changing defaults.",
        }
    )


def run_local_search_probe(
    paths: SimulationPaths,
    reference_images: dict[str, dict[str, Any]],
    *,
    requests: int,
    concurrency: int,
    p95_ms: int = 5000,
    p99_ms: int = 8000,
) -> dict[str, Any]:
    products = load_products(paths.poc_products_csv)
    settings = Settings(
        engine_backend="local",
        product_csv_path=paths.poc_products_csv,
        search_log_path=paths.search_log,
        product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
        cache_ttl_seconds=0,
    )
    service = AISearchService(LocalSearchEngine(products), settings, SearchLogger(paths.search_log))
    umbrella_image = image_base64(reference_images["umbrella"]["path"])
    tumbler_image = image_base64(reference_images["tumbler"]["path"])
    off_topic_image = image_base64(reference_images["off_topic"]["path"])
    scenarios = [
        (
            "text_black_umbrella",
            {"mall_id": "shop0001", "q": "\uac80\uc740 \uc6b0\uc0b0", "limit": 20},
            {"category": "\uc6b0\uc0b0", "simulate_click": True},
        ),
        (
            "text_typo_tumbler",
            {"mall_id": "shop0001", "q": "\ud150\ube14\ub7ec", "limit": 20},
            {"category": "\ud140\ube14\ub7ec"},
        ),
        (
            "filtered_umbrella_price",
            {"mall_id": "shop0001", "q": "\uc6b0\uc0b0", "category": "\uc6b0\uc0b0", "max_price": 50000, "limit": 20},
            {"all_category": "\uc6b0\uc0b0", "max_price": 50000},
        ),
        (
            "image_umbrella",
            {"mall_id": "shop0001", "image_base64": umbrella_image, "limit": 20},
            {"category": "\uc6b0\uc0b0"},
        ),
        (
            "mixed_tumbler",
            {"mall_id": "shop0001", "q": "\uac80\uc740\uc0c9", "image_base64": tumbler_image, "limit": 20},
            {"category": "\ud140\ube14\ub7ec", "simulate_click": True},
        ),
        (
            "off_topic_image_low_confidence",
            {"mall_id": "shop0001", "image_base64": off_topic_image, "limit": 20},
            {"low_confidence": True},
        ),
    ]
    latencies: list[float] = []
    errors: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    scenario_counts = {name: 0 for name, _, _ in scenarios}
    scenario_latencies: dict[str, list[float]] = {name: [] for name, _, _ in scenarios}
    query_type_latencies: dict[str, list[float]] = {"text": [], "image": [], "text_image": []}
    simulated_clicks_by_scenario = {name: 0 for name, _, _ in scenarios}

    def execute(index: int) -> dict[str, Any]:
        name, payload, expected = scenarios[index % len(scenarios)]
        started = time.perf_counter()
        result = service.search(SearchRequest.model_validate(payload))
        elapsed_ms = (time.perf_counter() - started) * 1000
        validation_errors = validate_probe_response(payload, result, expected)
        simulated_click = False
        if expected.get("simulate_click") and result.top:
            top_item = result.top[0]
            service.log_click(
                {
                    "mall_id": payload["mall_id"],
                    "product_id": top_item.product_id,
                    "position": 1,
                    "query": payload.get("q"),
                    "query_type": result.meta.query_type.value,
                    "score_percent": top_item.score_percent,
                    "product_url": top_item.product_url,
                }
            )
            simulated_click = True
        return {
            "scenario": name,
            "elapsed_ms": elapsed_ms,
            "ok": not validation_errors,
            "errors": validation_errors,
            "query_type": result.meta.query_type.value,
            "result_count": len(result.top) + len(result.items),
            "top_product_ids": [item.product_id for item in result.top],
            "simulated_click": simulated_click,
        }

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = [executor.submit(execute, index) for index in range(requests)]
        for future in as_completed(futures):
            try:
                item = future.result()
            except Exception as exc:
                errors.append(
                    {
                        "scenario": "exception",
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "traceback": traceback.format_exception_only(type(exc), exc)[-1].strip(),
                        "stack": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:],
                    }
                )
                continue
            scenario_counts[item["scenario"]] += 1
            if item.get("simulated_click"):
                simulated_clicks_by_scenario[item["scenario"]] += 1
            elapsed = float(item["elapsed_ms"])
            query_type = str(item.get("query_type") or "unknown")
            latencies.append(elapsed)
            scenario_latencies.setdefault(item["scenario"], []).append(elapsed)
            query_type_latencies.setdefault(query_type, []).append(elapsed)
            samples.append(
                {
                    "scenario": item["scenario"],
                    "query_type": query_type,
                    "elapsed_ms": round(elapsed, 2),
                    "result_count": item["result_count"],
                    "top_product_ids": item["top_product_ids"],
                }
            )
            if not item["ok"]:
                errors.append(item)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    performance_warnings = []
    if p95 is not None and p95 > p95_ms:
        performance_warnings.append(f"p95_ms exceeded {p95_ms}: {p95}")
    if p99 is not None and p99 > p99_ms:
        performance_warnings.append(f"p99_ms exceeded {p99_ms}: {p99}")
    slow_samples = sorted(samples, key=lambda item: float(item["elapsed_ms"]), reverse=True)[:10]
    scenario_latency_ms = {name: latency_summary(values) for name, values in scenario_latencies.items()}
    query_type_latency_ms = {name: latency_summary(values) for name, values in sorted(query_type_latencies.items())}
    scenario_coverage_ok = all(count > 0 for count in scenario_counts.values())
    query_type_coverage_ok = all(query_type_latency_ms.get(name, {}).get("count", 0) > 0 for name in ["text", "image", "text_image"])
    coverage_warnings = []
    if not scenario_coverage_ok:
        coverage_warnings.append("not all local probe scenarios were exercised")
    if not query_type_coverage_ok:
        coverage_warnings.append("not all query types were exercised")
    return {
        "ok": not errors and len(latencies) == requests,
        "performance_ok": not performance_warnings,
        "coverage_ok": scenario_coverage_ok and query_type_coverage_ok,
        "simulation_only": True,
        "requests": requests,
        "concurrency": concurrency,
        "thresholds": {
            "p95_ms": p95_ms,
            "p99_ms": p99_ms,
        },
        "elapsed_ms": elapsed_ms,
        "throughput_rps": round((len(latencies) / elapsed_ms) * 1000, 2) if elapsed_ms else None,
        "avg_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "p95_ms": p95,
        "p99_ms": p99,
        "max_ms": round(max(latencies), 2) if latencies else None,
        "scenario_counts": scenario_counts,
        "simulated_click_events": sum(simulated_clicks_by_scenario.values()),
        "simulated_clicks_by_scenario": simulated_clicks_by_scenario,
        "scenario_latency_ms": scenario_latency_ms,
        "query_type_latency_ms": query_type_latency_ms,
        "slow_samples": slow_samples,
        "performance_warnings": performance_warnings,
        "coverage_warnings": coverage_warnings,
        "errors": errors[:20],
    }


def run_response_materialization_probe(paths: SimulationPaths) -> dict[str, Any]:
    class ManyHitsEngine(LocalSearchEngine):
        name = "local"

        def search(self, query: EngineQuery) -> list[EngineHit]:
            return [
                EngineHit(
                    document=ProductDocument(
                        product_id=f"P{index:03d}",
                        name=f"Umbrella product {index}",
                        category=f"Category{index % 20}",
                        price=1000 + index,
                        status="active",
                        mall_id="shop001",
                    ),
                    score=max(0.01, 1.0 - (index * 0.001)),
                    source_scores={"text": max(0.01, 1.0 - (index * 0.001))},
                )
                for index in range(100)
            ]

    class CountingItemService(AISearchService):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.materialized_product_ids: list[str] = []

        def _to_item(self, hit: EngineHit, mall_id: str | None):
            self.materialized_product_ids.append(hit.document.product_id)
            return super()._to_item(hit, mall_id)

    class QueryLimitRecordingEngine(LocalSearchEngine):
        def __init__(self, product_documents: list[ProductDocument]):
            super().__init__(product_documents)
            self.query_limits: list[int] = []

        def search(self, query: EngineQuery) -> list[EngineHit]:
            self.query_limits.append(query.limit)
            return super().search(query)

    settings = Settings(
        engine_backend="local",
        index_name="response-materialization-probe",
        search_log_path=paths.output_dir / "response-materialization-probe-search.jsonl",
        cache_ttl_seconds=0,
        max_offset=100,
    )
    service = CountingItemService(ManyHitsEngine([]), settings, cache=MemorySearchCache(0))
    result = service.search(SearchRequest(mall_id="shop001", q="umbrella", limit=5, offset=10))

    expected_materialized = ["P000", "P001", "P002", "P013", "P014", "P015", "P016", "P017"]
    top_product_ids = [item.product_id for item in result.top]
    related_product_ids = [item.product_id for item in result.items]
    group_probe_products = [
        ProductDocument(
            product_id=f"P{index:03d}",
            name=f"검정 우산 옵션 {index}",
            category="우산",
            status="active",
            mall_id="shop001",
            product_group_id="G-UMBRELLA",
        )
        for index in range(1, 31)
    ]
    group_probe_products.extend(
        ProductDocument(
            product_id=f"P{index:03d}",
            name=f"검정 우산 고유 {index}",
            category="우산",
            status="active",
            mall_id="shop001",
            product_group_id=f"G-UNIQUE-{index:03d}",
        )
        for index in range(31, 37)
    )
    group_probe_settings = Settings(
        engine_backend="local",
        index_name="product-group-collapse-probe",
        search_log_path=paths.output_dir / "product-group-collapse-probe-search.jsonl",
        cache_ttl_seconds=0,
        max_offset=100,
    )
    group_probe_engine = QueryLimitRecordingEngine(group_probe_products)
    group_probe_service = AISearchService(group_probe_engine, group_probe_settings, cache=MemorySearchCache(0))
    group_probe_result = group_probe_service.search(SearchRequest(mall_id="shop001", q="검정 우산", limit=2))
    group_probe_log_entry = (group_probe_service.logger.tail(1) or [{}])[-1]
    group_probe_engine_limit = group_probe_engine.query_limits[0] if group_probe_engine.query_limits else None
    group_probe_final_engine_limit = group_probe_engine.query_limits[-1] if group_probe_engine.query_limits else None
    expected_group_probe_engine_limit = search_service_module.response_candidate_limit(
        group_probe_settings,
        "shop001",
        limit=2,
        offset=0,
    )
    group_probe_top_product_ids = [item.product_id for item in group_probe_result.top]
    group_probe_related_product_ids = [item.product_id for item in group_probe_result.items]
    group_probe_all_product_ids = group_probe_top_product_ids + group_probe_related_product_ids
    checks = {
        "only_returned_items_materialized": service.materialized_product_ids == expected_materialized,
        "candidate_hits_not_fully_materialized": len(service.materialized_product_ids) < 100,
        "top_items_preserved": top_product_ids == ["P000", "P001", "P002"],
        "related_page_preserved": related_product_ids == ["P013", "P014", "P015", "P016", "P017"],
        "pagination_preserved": result.meta.has_more is True and result.meta.next_offset == 15,
        "category_suggestions_still_use_candidate_hits": len(result.suggested_categories) >= 10,
        "product_group_overfetch_expands_engine_limit": group_probe_engine_limit == expected_group_probe_engine_limit
        and expected_group_probe_engine_limit == 12,
        "product_group_adaptive_refetch_expands_candidate_limit": group_probe_engine.query_limits == [12, 72],
        "product_group_adaptive_refetch_is_logged": group_probe_log_entry.get("engine_search_attempts") == 2
        and group_probe_log_entry.get("engine_adaptive_refetches") == 1
        and group_probe_log_entry.get("engine_candidate_limits") == [12, 72],
        "product_group_collapse_preserves_top_and_related": group_probe_top_product_ids == ["P001", "P031", "P032"]
        and group_probe_related_product_ids == ["P033", "P034"],
        "product_group_collapse_preserves_pagination": group_probe_result.meta.has_more is True
        and group_probe_result.meta.next_offset == 2,
        "product_group_collapse_skips_duplicate_variants": "P002" not in group_probe_all_product_ids
        and "P030" not in group_probe_all_product_ids,
    }
    return mark_simulated(
        {
            "ok": all(checks.values()),
            "checks": checks,
            "candidate_hits": 100,
            "materialized_count": len(service.materialized_product_ids),
            "materialized_product_ids": service.materialized_product_ids,
            "top_product_ids": top_product_ids,
            "related_product_ids": related_product_ids,
            "has_more": result.meta.has_more,
            "next_offset": result.meta.next_offset,
            "suggested_category_count": len(result.suggested_categories),
            "group_probe_engine_limit": group_probe_engine_limit,
            "group_probe_engine_limits": group_probe_engine.query_limits,
            "group_probe_final_engine_limit": group_probe_final_engine_limit,
            "group_probe_log_diagnostics": {
                "engine_search_attempts": group_probe_log_entry.get("engine_search_attempts"),
                "engine_adaptive_refetches": group_probe_log_entry.get("engine_adaptive_refetches"),
                "engine_candidate_limits": group_probe_log_entry.get("engine_candidate_limits"),
                "engine_raw_candidate_counts": group_probe_log_entry.get("engine_raw_candidate_counts"),
                "engine_collapsed_candidate_counts": group_probe_log_entry.get("engine_collapsed_candidate_counts"),
            },
            "group_probe_expected_engine_limit": expected_group_probe_engine_limit,
            "group_probe_top_product_ids": group_probe_top_product_ids,
            "group_probe_related_product_ids": group_probe_related_product_ids,
            "group_probe_has_more": group_probe_result.meta.has_more,
            "group_probe_next_offset": group_probe_result.meta.next_offset,
            "simulation_note": "Search response conversion now materializes only top results and the requested related-result page while preserving pagination and category suggestions.",
        }
    )


def run_search_cache_concurrency_probe(
    paths: SimulationPaths,
    *,
    repeated_requests: int = 20,
    unique_requests: int = 80,
    unique_concurrency: int = 20,
    cache_max_entries: int = 25,
) -> dict[str, Any]:
    products = load_products(paths.poc_products_csv)
    cache_log_path = paths.output_dir / "local-search-cache-probe.jsonl"
    if cache_log_path.exists():
        cache_log_path.unlink()
    policy_log_path = paths.output_dir / "local-search-policy-probe.jsonl"
    if policy_log_path.exists():
        policy_log_path.unlink()

    class SlowCountingEngine(LocalSearchEngine):
        def __init__(self, product_documents: list[ProductDocument]):
            super().__init__(product_documents)
            self.search_count = 0
            self.lock = threading.Lock()

        def search(self, query: EngineQuery):  # type: ignore[override]
            with self.lock:
                self.search_count += 1
            time.sleep(0.05)
            return super().search(query)

    class FailingEngine(LocalSearchEngine):
        def __init__(self, product_documents: list[ProductDocument]):
            super().__init__(product_documents)
            self.search_count = 0
            self.lock = threading.Lock()

        def search(self, query: EngineQuery):  # type: ignore[override]
            with self.lock:
                self.search_count += 1
            time.sleep(0.05)
            raise RuntimeError("backend unavailable")

    class BlockingCountingEngine(LocalSearchEngine):
        def __init__(self, product_documents: list[ProductDocument]):
            super().__init__(product_documents)
            self.search_count = 0
            self.lock = threading.Lock()
            self.first_started = threading.Event()

        def search(self, query: EngineQuery):  # type: ignore[override]
            with self.lock:
                self.search_count += 1
                search_count = self.search_count
            if search_count == 1:
                self.first_started.set()
                time.sleep(0.15)
            return super().search(query)

    class SimulatedDistributedSearchCache(MemorySearchCache):
        def __init__(self, ttl_seconds: int, max_entries: int):
            super().__init__(ttl_seconds=ttl_seconds, max_entries=max_entries)
            self._miss_locks: set[str] = set()
            self._miss_lock_guard = threading.RLock()
            self.lock_claims = 0
            self.lock_contention_events = 0
            self.lock_errors = 0
            self.lock_release_errors = 0

        def claim_miss_owner(self, key: str, lock_seconds: float) -> bool:  # noqa: ARG002
            with self._miss_lock_guard:
                if key in self._miss_locks:
                    self.lock_contention_events += 1
                    return False
                self._miss_locks.add(key)
                self.lock_claims += 1
                return True

        def release_miss_owner(self, key: str) -> None:
            with self._miss_lock_guard:
                self._miss_locks.discard(key)

        def status(self) -> dict[str, object]:
            status = super().status()
            status.update(
                {
                    "lock_claims": self.lock_claims,
                    "lock_contention_events": self.lock_contention_events,
                    "lock_errors": self.lock_errors,
                    "lock_release_errors": self.lock_release_errors,
                }
            )
            return status

    settings = Settings(
        engine_backend="local",
        product_csv_path=paths.poc_products_csv,
        search_log_path=cache_log_path,
        product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
        cache_ttl_seconds=30,
        cache_max_entries=cache_max_entries,
        cache_miss_wait_seconds=1.0,
        cache_miss_poll_seconds=0.005,
    )
    engine = SlowCountingEngine(products)
    cache = SimulatedDistributedSearchCache(
        ttl_seconds=settings.cache_ttl_seconds,
        max_entries=settings.cache_max_entries,
    )
    service = AISearchService(engine, settings, SearchLogger(cache_log_path), cache=cache)
    repeated_errors: list[str] = []
    unique_errors: list[str] = []
    unique_error_details: list[str] = []
    repeated_barrier = threading.Barrier(repeated_requests)

    def repeated_search(_: int) -> str | None:
        repeated_barrier.wait(timeout=10)
        result = service.search(SearchRequest(mall_id="shop0001", q="검은 우산", limit=10))
        return result.top[0].product_id if result.top else None

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=repeated_requests) as executor:
        futures = [executor.submit(repeated_search, index) for index in range(repeated_requests)]
        repeated_results: list[str | None] = []
        for future in as_completed(futures):
            try:
                repeated_results.append(future.result())
            except Exception as exc:
                repeated_errors.append(str(exc))
    repeated_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    repeated_engine_searches = engine.search_count

    def unique_search(index: int) -> str | None:
        result = service.search(SearchRequest(mall_id="shop0001", q=f"고유 검색어 {index} 우산", limit=5))
        return result.top[0].product_id if result.top else None

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, unique_concurrency)) as executor:
        futures = [executor.submit(unique_search, index) for index in range(unique_requests)]
        unique_results: list[str | None] = []
        for future in as_completed(futures):
            try:
                unique_results.append(future.result())
            except Exception as exc:
                unique_errors.append(str(exc))
                unique_error_details.append(traceback.format_exc(limit=8))
    unique_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    distributed_requests = 12
    distributed_service_count = 3
    distributed_errors: list[str] = []
    distributed_services = [
        AISearchService(engine, settings, SearchLogger(cache_log_path), cache=cache)
        for _ in range(distributed_service_count)
    ]
    distributed_barrier = threading.Barrier(distributed_requests)
    distributed_engine_count_before = engine.search_count
    distributed_lock_claims_before = int(cache.status().get("lock_claims") or 0)
    distributed_lock_contention_before = int(cache.status().get("lock_contention_events") or 0)

    def distributed_search(index: int) -> str | None:
        distributed_barrier.wait(timeout=10)
        result = distributed_services[index % distributed_service_count].search(
            SearchRequest(mall_id="shop0001", q="공유 캐시 우산", limit=10)
        )
        return result.top[0].product_id if result.top else None

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=distributed_requests) as executor:
        futures = [executor.submit(distributed_search, index) for index in range(distributed_requests)]
        distributed_results: list[str | None] = []
        for future in as_completed(futures):
            try:
                distributed_results.append(future.result())
            except Exception as exc:
                distributed_errors.append(str(exc))
    distributed_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    distributed_engine_searches = engine.search_count - distributed_engine_count_before
    distributed_lock_claims = int(cache.status().get("lock_claims") or 0) - distributed_lock_claims_before
    distributed_lock_contention_events = (
        int(cache.status().get("lock_contention_events") or 0) - distributed_lock_contention_before
    )

    failure_requests = 12
    failure_engine = FailingEngine(products)
    failure_cache = MemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries)
    failure_service = AISearchService(
        failure_engine,
        settings,
        SearchLogger(cache_log_path),
        cache=failure_cache,
    )
    failure_barrier = threading.Barrier(failure_requests)
    failure_errors: list[str] = []
    failure_error_lock = threading.Lock()

    def failure_search(_: int) -> None:
        failure_barrier.wait(timeout=10)
        try:
            failure_service.search(SearchRequest(mall_id="shop0001", q="장애 공유 우산", limit=10))
        except RuntimeError as exc:
            with failure_error_lock:
                failure_errors.append(str(exc))

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=failure_requests) as executor:
        futures = [executor.submit(failure_search, index) for index in range(failure_requests)]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                with failure_error_lock:
                    failure_errors.append(str(exc))
    failure_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    failure_coalesced = (
        failure_engine.search_count == 1
        and len(failure_errors) == failure_requests
        and set(failure_errors) == {"backend unavailable"}
    )

    timeout_engine = BlockingCountingEngine(products)
    timeout_settings = replace(settings, cache_miss_wait_seconds=0.01)
    timeout_service = AISearchService(
        timeout_engine,
        timeout_settings,
        SearchLogger(cache_log_path),
        cache=MemorySearchCache(timeout_settings.cache_ttl_seconds, timeout_settings.cache_max_entries),
    )
    timeout_errors: list[str] = []
    timeout_product_ids: list[str] = []
    timeout_rejections: list[str] = []
    timeout_request = SearchRequest(mall_id="shop0001", q="bounded wait 우산", limit=10)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(timeout_service.search, timeout_request)
            if not timeout_engine.first_started.wait(timeout=1.0):
                timeout_errors.append("first search did not start")
            else:
                try:
                    timeout_service.search(timeout_request)
                except SearchCacheMissInFlight as exc:
                    timeout_rejections.append(str(exc))
                else:
                    timeout_errors.append("second search was not rejected after singleflight timeout")
            first_response = first.result(timeout=2.0)
            if first_response.top:
                timeout_product_ids.append(first_response.top[0].product_id)
    except Exception as exc:
        timeout_errors.append(str(exc))
    timeout_status = timeout_service.singleflight_status()
    local_wait_timeout_rejected = timeout_rejections == ["identical search is still running"]
    local_wait_timeout_bounded = (
        not timeout_errors
        and local_wait_timeout_rejected
        and timeout_engine.search_count == 1
        and int(timeout_status.get("wait_events") or 0) == 1
        and int(timeout_status.get("wait_timeouts") or 0) == 1
        and int(timeout_status.get("in_flight") or 0) == 0
        and len(timeout_product_ids) == 1
    )

    image_singleflight_errors: list[str] = []
    image_validation_count = 0
    image_active_contexts = 0
    image_active_contexts_while_waiting = -1
    image_validation_lock = threading.Lock()
    image_context_lock = threading.Lock()
    image_engine_entered = threading.Event()
    image_release_engine = threading.Event()

    class BlockingImageEngine(LocalSearchEngine):
        def __init__(self, product_documents: list[ProductDocument]):
            super().__init__(product_documents)
            self.search_count = 0
            self.lock = threading.Lock()

        def search(self, query: EngineQuery):  # type: ignore[override]
            with self.lock:
                self.search_count += 1
            image_engine_entered.set()
            image_release_engine.wait(timeout=2.0)
            return super().search(query)

    class CountingImageContext:
        def __enter__(self) -> None:
            nonlocal image_active_contexts
            with image_context_lock:
                image_active_contexts += 1

        def __exit__(self, exc_type, exc, traceback) -> bool:  # type: ignore[no-untyped-def]
            nonlocal image_active_contexts
            with image_context_lock:
                image_active_contexts -= 1
            return False

    image_path = paths.quality_image_dir / "load-reference.jpg"
    image_singleflight_engine = BlockingImageEngine(products)
    image_singleflight_service = AISearchService(
        image_singleflight_engine,
        settings,
        SearchLogger(cache_log_path),
        cache=MemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries),
    )
    image_results: list[str] = []
    try:
        image_bytes = image_path.read_bytes()
        validated_image = validate_image_bytes(image_bytes, max_bytes=5 * 1024 * 1024, min_dimension=16)
        image_request = SearchRequest(
            mall_id="shop0001",
            image_base64=base64.b64encode(image_bytes).decode("ascii"),
            limit=10,
        )
        original_validate_image_base64 = search_service_module.validate_image_base64

        def counted_validate_image(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal image_validation_count
            with image_validation_lock:
                image_validation_count += 1
            return validated_image

        def image_search() -> None:
            try:
                result = image_singleflight_service.search(image_request, lambda: CountingImageContext())
                image_results.append(result.top[0].product_id if result.top else "")
            except Exception as exc:  # pragma: no cover - surfaced in report details
                image_singleflight_errors.append(str(exc))

        try:
            search_service_module.validate_image_base64 = counted_validate_image
            first_thread = threading.Thread(target=image_search)
            second_thread = threading.Thread(target=image_search)
            first_thread.start()
            if not image_engine_entered.wait(timeout=1.0):
                image_singleflight_errors.append("first image search did not enter engine")
            second_thread.start()
            validation_deadline = time.time() + 1.0
            while time.time() < validation_deadline:
                with image_validation_lock:
                    if image_validation_count >= 1:
                        break
                time.sleep(0.01)
            with image_context_lock:
                image_active_contexts_while_waiting = image_active_contexts
            image_release_engine.set()
            first_thread.join(timeout=2.0)
            second_thread.join(timeout=2.0)
            if first_thread.is_alive() or second_thread.is_alive():
                image_singleflight_errors.append("image singleflight worker did not finish")
        finally:
            search_service_module.validate_image_base64 = original_validate_image_base64
            image_release_engine.set()
    except Exception as exc:
        image_singleflight_errors.append(str(exc))
    image_singleflight_status = image_singleflight_service.singleflight_status()
    image_singleflight_context_released_during_wait = (
        not image_singleflight_errors
        and image_validation_count >= 1
        and image_active_contexts_while_waiting == 1
        and image_singleflight_engine.search_count == 1
        and int(image_singleflight_status.get("wait_events") or 0) >= 1
        and len(image_results) == 2
    )

    image_validation_singleflight_errors: list[str] = []
    image_validation_singleflight_results: list[str] = []
    image_validation_singleflight_count = 0
    image_validation_started = threading.Event()
    image_validation_release = threading.Event()
    image_validation_count_lock = threading.Lock()

    class CountingImageValidationEngine(LocalSearchEngine):
        def __init__(self, product_documents: list[ProductDocument]):
            super().__init__(product_documents)
            self.search_count = 0
            self.lock = threading.Lock()

        def search(self, query: EngineQuery):  # type: ignore[override]
            with self.lock:
                self.search_count += 1
            return super().search(query)

    image_validation_engine = CountingImageValidationEngine([])
    image_validation_service = AISearchService(
        image_validation_engine,
        settings,
        SearchLogger(cache_log_path),
        cache=MemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries),
    )
    try:
        image_bytes = image_path.read_bytes()
        validated_image = validate_image_bytes(image_bytes, max_bytes=5 * 1024 * 1024, min_dimension=16)
        image_validation_engine.upsert_products(
            [
                ProductDocument(
                    product_id="P-IMAGE-VALIDATION",
                    name="image validation coalescing probe",
                    category="우산",
                    status="active",
                    mall_id="shop0001",
                    extra={"image_hash": validated_image.sha256},
                )
            ]
        )
        image_validation_request = SearchRequest(
            mall_id="shop0001",
            image_base64=base64.b64encode(image_bytes).decode("ascii"),
            limit=10,
        )
        original_validate_image_base64 = search_service_module.validate_image_base64

        def slow_counted_validate_image(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal image_validation_singleflight_count
            with image_validation_count_lock:
                image_validation_singleflight_count += 1
            image_validation_started.set()
            image_validation_release.wait(timeout=2.0)
            return validated_image

        def image_validation_search() -> None:
            try:
                result = image_validation_service.search(image_validation_request)
                image_validation_singleflight_results.append(result.top[0].product_id if result.top else "")
            except Exception as exc:  # pragma: no cover - surfaced in report details
                image_validation_singleflight_errors.append(str(exc))

        try:
            search_service_module.validate_image_base64 = slow_counted_validate_image
            first_thread = threading.Thread(target=image_validation_search)
            second_thread = threading.Thread(target=image_validation_search)
            first_thread.start()
            if not image_validation_started.wait(timeout=1.0):
                image_validation_singleflight_errors.append("first image validation did not start")
            second_thread.start()
            time.sleep(0.05)
            image_validation_release.set()
            first_thread.join(timeout=2.0)
            second_thread.join(timeout=2.0)
            if first_thread.is_alive() or second_thread.is_alive():
                image_validation_singleflight_errors.append("image validation singleflight worker did not finish")
        finally:
            search_service_module.validate_image_base64 = original_validate_image_base64
            image_validation_release.set()
    except Exception as exc:
        image_validation_singleflight_errors.append(str(exc))
    image_validation_singleflight_status = image_validation_service.image_validation_status()
    image_validation_singleflight_coalesced = (
        not image_validation_singleflight_errors
        and image_validation_singleflight_count == 1
        and image_validation_engine.search_count == 1
        and int(image_validation_singleflight_status.get("wait_events") or 0) >= 1
        and int(image_validation_singleflight_status.get("wait_timeouts") or 0) == 0
        and image_validation_singleflight_results == ["P-IMAGE-VALIDATION", "P-IMAGE-VALIDATION"]
    )

    image_validation_cache_errors: list[str] = []
    image_validation_cache_results: list[str] = []
    image_validation_cache_count = 0
    image_validation_cache_engine = CountingImageValidationEngine([])
    image_validation_cache_settings = replace(
        settings,
        cache_ttl_seconds=0,
        image_validation_cache_ttl_seconds=30.0,
        image_validation_cache_max_entries=2,
    )
    image_validation_cache_service = AISearchService(
        image_validation_cache_engine,
        image_validation_cache_settings,
        SearchLogger(cache_log_path),
        cache=MemorySearchCache(
            image_validation_cache_settings.cache_ttl_seconds,
            image_validation_cache_settings.cache_max_entries,
        ),
    )
    try:
        image_bytes = image_path.read_bytes()
        validated_image = validate_image_bytes(image_bytes, max_bytes=5 * 1024 * 1024, min_dimension=16)
        image_validation_cache_engine.upsert_products(
            [
                ProductDocument(
                    product_id="P-IMAGE-CACHE",
                    name="image validation cache probe",
                    category="우산",
                    status="active",
                    mall_id="shop0001",
                    extra={"image_hash": validated_image.sha256},
                )
            ]
        )
        image_validation_cache_request = SearchRequest(
            mall_id="shop0001",
            image_base64=base64.b64encode(image_bytes).decode("ascii"),
            limit=10,
        )
        original_validate_image_base64 = search_service_module.validate_image_base64

        def cache_counted_validate_image(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal image_validation_cache_count
            image_validation_cache_count += 1
            return validated_image

        try:
            search_service_module.validate_image_base64 = cache_counted_validate_image
            for _ in range(5):
                result = image_validation_cache_service.search(image_validation_cache_request)
                image_validation_cache_results.append(result.top[0].product_id if result.top else "")
        finally:
            search_service_module.validate_image_base64 = original_validate_image_base64
    except Exception as exc:
        image_validation_cache_errors.append(str(exc))
    image_validation_cache_status = image_validation_cache_service.image_validation_status()
    image_validation_cache_reused = (
        not image_validation_cache_errors
        and image_validation_cache_count == 1
        and image_validation_cache_engine.search_count == 5
        and int(image_validation_cache_status.get("cache_hits") or 0) >= 4
        and int(image_validation_cache_status.get("cache_misses") or 0) == 1
        and int(image_validation_cache_status.get("cache_entry_count") or 0) == 1
        and image_validation_cache_results == ["P-IMAGE-CACHE"] * 5
    )

    policy_calls: list[str | None] = []
    policy_base_calls = 0
    original_base_policy_fingerprint = search_service_module.cache_policy_base_fingerprint
    original_mall_policy_fingerprint = search_service_module.cache_policy_mall_fingerprint

    def counted_base_policy_fingerprint(settings_arg: Settings) -> dict[str, Any]:
        nonlocal policy_base_calls
        policy_base_calls += 1
        return original_base_policy_fingerprint(settings_arg)

    def counted_mall_policy_fingerprint(settings_arg: Settings, mall_id: str | None) -> dict[str, Any] | None:
        policy_calls.append(mall_id)
        return original_mall_policy_fingerprint(settings_arg, mall_id)

    policy_errors: list[str] = []
    policy_error_details: list[str] = []
    policy_token_count = 0
    policy_unknown_token_count = 0
    policy_unknown_mall_ids = [f"unknown{index:04d}" for index in range(20)]
    policy_unknown_mall_tokens_skipped = False
    try:
        policy_settings = replace(
            settings,
            query_synonyms={f"synthetic-term-{index}": (f"synthetic-alias-{index}",) for index in range(2000)},
            malls={
                "shop0001": MallConfig(
                    mall_id="shop0001",
                    product_url_template="https://shop0001.haeorumgift.com/product_view.asp?p_idx={product_id}",
                ),
                "shop0002": MallConfig(
                    mall_id="shop0002",
                    product_url_template="https://shop0002.haeorumgift.com/product_view.asp?p_idx={product_id}",
                ),
            },
        )
        search_service_module.cache_policy_base_fingerprint = counted_base_policy_fingerprint
        search_service_module.cache_policy_mall_fingerprint = counted_mall_policy_fingerprint
        policy_service = AISearchService(
            LocalSearchEngine(products),
            policy_settings,
            SearchLogger(policy_log_path),
            cache=MemorySearchCache(policy_settings.cache_ttl_seconds, policy_settings.cache_max_entries),
        )
        for _ in range(5):
            policy_service.search(SearchRequest(mall_id="shop0001", q="정책 캐시 우산", limit=5))
        policy_service.search(SearchRequest(mall_id="shop0002", q="정책 캐시 우산", limit=5))
        policy_token_count = len(policy_service._policy_fingerprint_tokens)
        for index, mall_id in enumerate(policy_unknown_mall_ids):
            policy_service.search(SearchRequest(mall_id=mall_id, q=f"정책 캐시 우산 {index}", limit=5))
        policy_unknown_token_count = len(policy_service._policy_fingerprint_tokens)
        policy_unknown_mall_tokens_skipped = (
            policy_unknown_token_count == policy_token_count
            and not any(mall_id in policy_service._policy_fingerprint_tokens for mall_id in policy_unknown_mall_ids)
        )
    except Exception as exc:
        policy_errors.append(str(exc))
        policy_error_details.append(traceback.format_exc(limit=8))
    finally:
        search_service_module.cache_policy_base_fingerprint = original_base_policy_fingerprint
        search_service_module.cache_policy_mall_fingerprint = original_mall_policy_fingerprint
    known_policy_calls = [mall_id for mall_id in policy_calls if mall_id in {"shop0001", "shop0002"}]
    policy_fingerprint_reused = (
        not policy_errors
        and policy_base_calls == 1
        and known_policy_calls == ["shop0001", "shop0002"]
        and policy_token_count == 2
        and policy_unknown_mall_tokens_skipped
    )

    cache_status = cache.status()
    repeated_product_ids = sorted({value for value in repeated_results if value})
    distributed_product_ids = sorted({value for value in distributed_results if value})
    cache_entries = service.logger.tail(repeated_requests + unique_requests + distributed_requests + 20)
    repeated_log_entries = [entry for entry in cache_entries if entry.get("q") == "검은 우산"]
    distributed_log_entries = [entry for entry in cache_entries if entry.get("q") == "공유 캐시 우산"]
    repeated_cached_events = sum(1 for entry in repeated_log_entries if entry.get("cached") is True)
    repeated_uncached_events = sum(1 for entry in repeated_log_entries if entry.get("cached") is False)
    distributed_cached_events = sum(1 for entry in distributed_log_entries if entry.get("cached") is True)
    distributed_uncached_events = sum(1 for entry in distributed_log_entries if entry.get("cached") is False)
    high_cardinality_bounded = int(cache_status.get("entry_count") or 0) <= cache_max_entries
    evictions = int(cache_status.get("evictions") or 0)
    repeated_coalesced = (
        repeated_engine_searches == 1
        and not repeated_errors
        and len(repeated_results) == repeated_requests
        and bool(repeated_product_ids)
    )
    unique_complete = not unique_errors and len(unique_results) == unique_requests
    distributed_coalesced = (
        distributed_engine_searches == 1
        and not distributed_errors
        and len(distributed_results) == distributed_requests
        and bool(distributed_product_ids)
    )
    return {
        "ok": (
            repeated_coalesced
            and distributed_coalesced
            and failure_coalesced
            and local_wait_timeout_bounded
            and image_singleflight_context_released_during_wait
            and image_validation_singleflight_coalesced
            and image_validation_cache_reused
            and policy_fingerprint_reused
            and policy_unknown_mall_tokens_skipped
            and unique_complete
            and high_cardinality_bounded
            and evictions > 0
        ),
        "simulation_only": True,
        "log_path": str(cache_log_path),
        "repeated_requests": repeated_requests,
        "repeated_concurrency": repeated_requests,
        "repeated_elapsed_ms": repeated_elapsed_ms,
        "repeated_engine_searches": repeated_engine_searches,
        "repeated_coalesced": repeated_coalesced,
        "repeated_cached_events": repeated_cached_events,
        "repeated_uncached_events": repeated_uncached_events,
        "repeated_product_ids": repeated_product_ids,
        "distributed_requests": distributed_requests,
        "distributed_service_count": distributed_service_count,
        "distributed_elapsed_ms": distributed_elapsed_ms,
        "distributed_engine_searches": distributed_engine_searches,
        "distributed_coalesced": distributed_coalesced,
        "distributed_cached_events": distributed_cached_events,
        "distributed_uncached_events": distributed_uncached_events,
        "distributed_product_ids": distributed_product_ids,
        "distributed_lock_claims": distributed_lock_claims,
        "distributed_lock_contention_events": distributed_lock_contention_events,
        "failure_requests": failure_requests,
        "failure_elapsed_ms": failure_elapsed_ms,
        "failure_engine_searches": failure_engine.search_count,
        "failure_coalesced": failure_coalesced,
        "failure_error_count": len(failure_errors),
        "failure_errors": failure_errors[:5],
        "local_wait_timeout_bounded": local_wait_timeout_bounded,
        "local_wait_timeout_rejected": local_wait_timeout_rejected,
        "local_wait_timeout_duplicate_backend_blocked": timeout_engine.search_count == 1,
        "local_wait_timeout_engine_searches": timeout_engine.search_count,
        "local_wait_timeout_product_ids": timeout_product_ids,
        "local_wait_timeout_status": timeout_status,
        "local_wait_timeout_rejections": timeout_rejections[:5],
        "local_wait_timeout_errors": timeout_errors[:5],
        "image_singleflight_context_released_during_wait": image_singleflight_context_released_during_wait,
        "image_singleflight_engine_searches": image_singleflight_engine.search_count,
        "image_singleflight_validation_count": image_validation_count,
        "image_singleflight_active_contexts_while_waiting": image_active_contexts_while_waiting,
        "image_singleflight_status": image_singleflight_status,
        "image_singleflight_result_count": len(image_results),
        "image_singleflight_errors": image_singleflight_errors[:5],
        "image_validation_singleflight_coalesced": image_validation_singleflight_coalesced,
        "image_validation_singleflight_validation_count": image_validation_singleflight_count,
        "image_validation_singleflight_engine_searches": image_validation_engine.search_count,
        "image_validation_singleflight_status": image_validation_singleflight_status,
        "image_validation_singleflight_results": image_validation_singleflight_results,
        "image_validation_singleflight_errors": image_validation_singleflight_errors[:5],
        "image_validation_cache_reused": image_validation_cache_reused,
        "image_validation_cache_validation_count": image_validation_cache_count,
        "image_validation_cache_engine_searches": image_validation_cache_engine.search_count,
        "image_validation_cache_status": image_validation_cache_status,
        "image_validation_cache_results": image_validation_cache_results,
        "image_validation_cache_errors": image_validation_cache_errors[:5],
        "policy_fingerprint_reused": policy_fingerprint_reused,
        "policy_base_fingerprint_calls": policy_base_calls,
        "policy_fingerprint_calls": policy_calls,
        "policy_fingerprint_known_calls": known_policy_calls,
        "policy_fingerprint_token_count": policy_token_count,
        "policy_unknown_mall_tokens_skipped": policy_unknown_mall_tokens_skipped,
        "policy_unknown_token_count": policy_unknown_token_count,
        "policy_unknown_mall_probe_count": len(policy_unknown_mall_ids),
        "policy_fingerprint_synonym_count": 2000,
        "policy_fingerprint_errors": policy_errors[:5],
        "policy_fingerprint_error_details": policy_error_details[:2],
        "unique_requests": unique_requests,
        "unique_concurrency": unique_concurrency,
        "unique_elapsed_ms": unique_elapsed_ms,
        "unique_complete": unique_complete,
        "cache_max_entries": cache_max_entries,
        "cache_status": cache_status,
        "high_cardinality_bounded": high_cardinality_bounded,
        "evictions": evictions,
        "errors": {
            "repeated": repeated_errors[:5],
            "distributed": distributed_errors[:5],
            "unique": unique_errors[:5],
        },
        "error_details": {
            "unique": unique_error_details[:2],
        },
    }


def run_search_execution_queue_probe(
    *,
    requests: int = 12,
    max_concurrency: int = 3,
    hold_seconds: float = 0.05,
) -> dict[str, Any]:
    gate = SearchExecutionGate(max_concurrency=max_concurrency, queue_timeout_seconds=0)
    barrier = threading.Barrier(requests)
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    result_lock = threading.Lock()

    def execute(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            with gate.slot():
                status = gate.status()
                time.sleep(hold_seconds)
                with result_lock:
                    results.append(
                        {
                            "index": index,
                            "status": "acquired",
                            "in_flight_at_acquire": status.get("in_flight"),
                        }
                    )
        except SearchQueueFull:
            with result_lock:
                results.append({"index": index, "status": "queue_full"})
        except Exception as exc:
            with result_lock:
                errors.append(str(exc))

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=requests) as executor:
        futures = [executor.submit(execute, index) for index in range(requests)]
        for future in as_completed(futures):
            future.result()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    final_status = gate.status()
    acquired = sum(1 for item in results if item.get("status") == "acquired")
    rejected = sum(1 for item in results if item.get("status") == "queue_full")
    max_in_flight_observed = max(
        [int(item.get("in_flight_at_acquire") or 0) for item in results if item.get("status") == "acquired"] or [0]
    )
    return {
        "ok": (
            not errors
            and acquired == max_concurrency
            and rejected == max(requests - max_concurrency, 0)
            and int(final_status.get("queue_full_events") or 0) == rejected
            and int(final_status.get("acquired_events") or 0) == acquired
            and int(final_status.get("wait_events") or 0) == requests
            and int(final_status.get("in_flight") or 0) == 0
            and max_in_flight_observed <= max_concurrency
        ),
        "simulation_only": True,
        "requests": requests,
        "max_concurrency": max_concurrency,
        "queue_timeout_seconds": 0,
        "hold_seconds": hold_seconds,
        "elapsed_ms": elapsed_ms,
        "acquired": acquired,
        "rejected": rejected,
        "max_in_flight_observed": max_in_flight_observed,
        "wait_events": int(final_status.get("wait_events") or 0),
        "max_wait_ms": final_status.get("max_wait_ms"),
        "avg_wait_ms": final_status.get("avg_wait_ms"),
        "final_status": final_status,
        "errors": errors[:5],
    }


def run_qwen_query_vector_concurrency_probe(
    *,
    text_requests: int = 16,
    image_requests: int = 12,
) -> dict[str, Any]:
    class SlowQwenEngine(MarqoSearchEngine):
        def __init__(self):
            super().__init__("http://marqo-api:8882", "haeorum-products-sim", embedding_backend="qwen")
            self.text_embed_calls = 0
            self.image_embed_calls = 0
            self.lock = threading.Lock()

        def qwen_embed_query_texts(self, texts: list[str], **_: object) -> list[list[float]]:
            with self.lock:
                self.text_embed_calls += 1
                call = self.text_embed_calls
            time.sleep(0.05)
            vector = [0.0] * self.qwen_embedding_dimensions
            vector[0] = float(call)
            return [vector for _ in texts]

        def qwen_embed_images(self, images: list[str], **_: object) -> list[list[float]]:
            with self.lock:
                self.image_embed_calls += 1
                call = self.image_embed_calls
            time.sleep(0.05)
            vector = [0.0] * self.qwen_embedding_dimensions
            vector[1] = float(call)
            return [vector for _ in images]

    class BarrierMixedQwenEngine(MarqoSearchEngine):
        def __init__(self):
            super().__init__(
                "http://marqo-api:8882",
                "haeorum-products-sim",
                embedding_backend="qwen",
                qwen_embedding_dimensions=2,
                qwen_mixed_query_parallelism=2,
            )
            self.barrier = threading.Barrier(2)
            self.text_embed_calls = 0
            self.image_embed_calls = 0

        def qwen_embed_query_texts(self, texts: list[str], **_: object) -> list[list[float]]:
            self.text_embed_calls += 1
            self.barrier.wait(timeout=2)
            return [[0.11, 0.22] for _ in texts]

        def qwen_embed_images(self, images: list[str], **_: object) -> list[list[float]]:
            self.image_embed_calls += 1
            self.barrier.wait(timeout=2)
            return [[0.33, 0.44] for _ in images]

    class InvalidQwenEmbeddingResponseEngine(MarqoSearchEngine):
        def __init__(self):
            super().__init__(
                "http://marqo-api:8882",
                "haeorum-products-sim",
                embedding_backend="qwen",
                qwen_embedding_dimensions=2,
            )

        def _qwen_request(self, method: str, path: str, payload: object = None, timeout: int = 60):
            return 200, {"embeddings": [[0.1]]}, 1.0

    engine = SlowQwenEngine()
    text_errors: list[str] = []
    image_errors: list[str] = []
    text_vectors: list[float] = []
    image_vectors: list[float] = []
    text_barrier = threading.Barrier(text_requests)
    image_barrier = threading.Barrier(image_requests)

    def text_vector(index: int) -> float:
        text_barrier.wait(timeout=10)
        vector, _field, _source = engine.qwen_query_vector(EngineQuery(q="검은 우산", limit=index + 1))
        return float(vector[0])

    def image_vector(index: int) -> float:
        image_barrier.wait(timeout=10)
        vector, _field, _source = engine.qwen_query_vector(
            EngineQuery(
                image_data_url="data:image/png;base64,QUJD",
                image_hash="same-image",
                limit=index + 1,
            )
        )
        return float(vector[1])

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=text_requests) as executor:
        futures = [executor.submit(text_vector, index) for index in range(text_requests)]
        for future in as_completed(futures):
            try:
                text_vectors.append(future.result())
            except Exception as exc:
                text_errors.append(str(exc))
    text_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=image_requests) as executor:
        futures = [executor.submit(image_vector, index) for index in range(image_requests)]
        for future in as_completed(futures):
            try:
                image_vectors.append(future.result())
            except Exception as exc:
                image_errors.append(str(exc))
    image_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    cached_text = engine.qwen_query_vector(EngineQuery(q="검은 우산", limit=99))
    cached_image = engine.qwen_query_vector(
        EngineQuery(image_data_url="data:image/png;base64,QUJD", image_hash="same-image", limit=99)
    )
    mixed_context, mixed_fields, mixed_sources = engine.qwen_query_context(
        EngineQuery(
            q="검은 우산",
            image_data_url="data:image/png;base64,QUJD",
            image_hash="same-image",
            text_weight=0.4,
            image_weight=0.6,
            limit=99,
        )
    )
    status = engine.qwen_query_vector_status()
    text_coalesced = (
        not text_errors
        and len(text_vectors) == text_requests
        and engine.text_embed_calls == 1
        and set(text_vectors) == {1.0}
        and cached_text[2] == "text_to_image_runtime_cache"
    )
    image_coalesced = (
        not image_errors
        and len(image_vectors) == image_requests
        and engine.image_embed_calls == 1
        and set(image_vectors) == {1.0}
        and cached_image[2] == "image_runtime_cache"
    )
    mixed_uses_text_and_image = (
        len(mixed_context) == 2
        and mixed_fields == [QWEN_IMAGE_VECTOR_FIELD]
        and [entry.get("weight") for entry in mixed_context] == [0.4, 0.6]
        and mixed_sources == ["text_to_image_runtime_cache", "image_runtime_cache"]
        and engine.text_embed_calls == 1
        and engine.image_embed_calls == 1
    )
    mixed_parallel_errors: list[str] = []
    mixed_parallel_context: list[dict[str, Any]] = []
    mixed_parallel_fields: list[str] = []
    mixed_parallel_sources: list[str] = []
    mixed_parallel_elapsed_ms: float | None = None
    mixed_parallel_engine = BarrierMixedQwenEngine()
    try:
        started = time.perf_counter()
        mixed_parallel_context, mixed_parallel_fields, mixed_parallel_sources = mixed_parallel_engine.qwen_query_context(
            EngineQuery(
                q="병렬 우산",
                image_data_url="data:image/png;base64,UEFSQUxMRUw=",
                image_hash="parallel-mixed-image",
                text_weight=0.4,
                image_weight=0.6,
            )
        )
        mixed_parallel_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    except Exception as exc:
        mixed_parallel_errors.append(str(exc))
    mixed_parallel_text_and_image = (
        not mixed_parallel_errors
        and len(mixed_parallel_context) == 2
        and mixed_parallel_fields == [QWEN_IMAGE_VECTOR_FIELD]
        and mixed_parallel_sources == ["text_to_image", "image"]
        and [entry.get("weight") for entry in mixed_parallel_context] == [0.4, 0.6]
        and [entry.get("vector") for entry in mixed_parallel_context] == [[0.11, 0.22], [0.33, 0.44]]
        and mixed_parallel_engine.text_embed_calls == 1
        and mixed_parallel_engine.image_embed_calls == 1
        and mixed_parallel_engine.qwen_query_vector_status().get("mixed_parallelism") == 2
    )
    invalid_qwen_embedding_response_error = ""
    try:
        InvalidQwenEmbeddingResponseEngine().qwen_embed_query_texts(["invalid"])
        invalid_qwen_embedding_response_blocked = False
    except ValueError as exc:
        invalid_qwen_embedding_response_error = str(exc)
        invalid_qwen_embedding_response_blocked = "has 1 dimensions; expected 2" in str(exc)
    invalid_qwen_precomputed_cache_error = ""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "qwen-query-cache.json"
            cache_path.write_text(
                json.dumps({"items": [{"text": "invalid", "vector": [0.1]}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            engine_module.load_qwen_query_embedding_cache(cache_path, expected_dimensions=2)
        invalid_qwen_precomputed_cache_blocked = False
    except ValueError as exc:
        invalid_qwen_precomputed_cache_error = str(exc)
        invalid_qwen_precomputed_cache_blocked = "has 1 dimensions; expected 2" in str(exc)
    return mark_simulated(
        {
            "ok": (
                text_coalesced
                and image_coalesced
                and mixed_uses_text_and_image
                and mixed_parallel_text_and_image
                and invalid_qwen_embedding_response_blocked
                and invalid_qwen_precomputed_cache_blocked
                and int(status.get("wait_timeouts") or 0) == 0
            ),
            "text_requests": text_requests,
            "image_requests": image_requests,
            "text_elapsed_ms": text_elapsed_ms,
            "image_elapsed_ms": image_elapsed_ms,
            "text_embed_calls": engine.text_embed_calls,
            "image_embed_calls": engine.image_embed_calls,
            "text_coalesced": text_coalesced,
            "image_coalesced": image_coalesced,
            "mixed_uses_text_and_image": mixed_uses_text_and_image,
            "mixed_context_weights": [entry.get("weight") for entry in mixed_context],
            "mixed_searchable_attributes": mixed_fields,
            "mixed_vector_sources": mixed_sources,
            "mixed_parallel_text_and_image": mixed_parallel_text_and_image,
            "mixed_parallel_elapsed_ms": mixed_parallel_elapsed_ms,
            "mixed_parallel_searchable_attributes": mixed_parallel_fields,
            "mixed_parallel_vector_sources": mixed_parallel_sources,
            "mixed_parallel_errors": mixed_parallel_errors[:5],
            "invalid_qwen_embedding_response_blocked": invalid_qwen_embedding_response_blocked,
            "invalid_qwen_embedding_response_error": invalid_qwen_embedding_response_error,
            "invalid_qwen_precomputed_cache_blocked": invalid_qwen_precomputed_cache_blocked,
            "invalid_qwen_precomputed_cache_error": invalid_qwen_precomputed_cache_error,
            "cached_text_source": cached_text[2],
            "cached_image_source": cached_image[2],
            "qwen_query_vector_status": status,
            "text_errors": text_errors[:5],
            "image_errors": image_errors[:5],
        }
    )


def run_marqo_filter_pushdown_probe() -> dict[str, Any]:
    engine = MarqoSearchEngine("http://marqo-api:8882", "haeorum-products-sim")
    payload = engine.build_search_payload(
        EngineQuery(
            q="텀블러",
            limit=10,
            print_method="UV",
            min_price=1000,
            max_price=10000,
            quantity=100,
            max_delivery_days=5,
        )
    )
    numeric_only_payload = engine.build_search_payload(
        EngineQuery(
            image_data_url="data:image/png;base64,AAAA",
            limit=10,
            min_price=1000,
            max_price=10000,
            quantity=100,
            max_delivery_days=5,
        )
    )
    image_only_payload = engine.build_search_payload(
        EngineQuery(
            image_data_url="data:image/png;base64,AAAA",
            limit=10,
        )
    )

    class ParseStopMarqoSearchEngine(MarqoSearchEngine):
        def __init__(self) -> None:
            super().__init__("http://marqo-api:8882", "haeorum-products-sim")

        def _request(self, method: str, path: str, payload: object = None, timeout: int = 60):
            return (
                200,
                {
                    "hits": [
                        {
                            "_id": "P001",
                            "product_id": "P001",
                            "product_name": "Umbrella 1",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "_score": 0.9,
                        },
                        {
                            "_id": "P002",
                            "product_id": "P002",
                            "product_name": "Umbrella 2",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "_score": 0.8,
                        },
                        {
                            "_id": "TOO-LATE",
                            "product_id": "X" * (MAX_PRODUCT_ID_LENGTH + 1),
                            "product_name": "This hit must not be parsed after enough non-rerank hits",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "_score": 0.7,
                        },
                    ]
                },
                1.0,
            )

    class MalformedHitMarqoSearchEngine(MarqoSearchEngine):
        def __init__(self) -> None:
            super().__init__("http://marqo-api:8882", "haeorum-products-sim")

        def _request(self, method: str, path: str, payload: object = None, timeout: int = 60):
            return (
                200,
                {
                    "hits": [
                        {
                            "_id": "BROKEN-OVERSIZED",
                            "product_id": "X" * (MAX_PRODUCT_ID_LENGTH + 1),
                            "product_name": "Broken oversized product id",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "_score": 0.99,
                        },
                        {
                            "_id": "BROKEN-SCORE",
                            "product_id": "BROKEN-SCORE",
                            "product_name": "Broken score",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "_score": "not-a-number",
                        },
                        {
                            "_id": "P001",
                            "product_id": "P001",
                            "product_name": "Umbrella valid 1",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "_score": 0.88,
                        },
                        {
                            "_id": "P002",
                            "product_id": "P002",
                            "product_name": "Umbrella valid 2",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "_score": 0.77,
                        },
                    ]
                },
                1.0,
            )

    class TextRerankCandidateWindowMarqoSearchEngine(MarqoSearchEngine):
        def __init__(self) -> None:
            super().__init__("http://marqo-api:8882", "haeorum-products-sim")
            self.payloads: list[dict[str, Any]] = []

        def _request(self, method: str, path: str, payload: object = None, timeout: int = 60):
            self.payloads.append(dict(payload or {}))
            return (
                200,
                {
                    "hits": [
                        {
                            "_id": f"P{index:03d}",
                            "product_id": f"P{index:03d}",
                            "product_name": f"검정 우산 후보 {index}",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            SEARCH_TEXT_FIELD: f"검정 우산 후보 {index}",
                            "_score": 0.95 - (index * 0.01),
                        }
                        for index in range(1, 8)
                    ]
                },
                1.0,
            )

    class PostFilterPreparationMarqoSearchEngine(MarqoSearchEngine):
        def __init__(self) -> None:
            super().__init__("http://marqo-api:8882", "haeorum-products-sim")

        def _request(self, method: str, path: str, payload: object = None, timeout: int = 60):
            return (
                200,
                {
                    "hits": [
                        {
                            "_id": f"P{index:03d}",
                            "product_id": f"P{index:03d}",
                            "product_name": f"UV umbrella candidate {index}",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "print_methods": ["UV 인쇄"],
                            "_score": 0.9 - (index * 0.01),
                        }
                        for index in range(1, 6)
                    ]
                },
                1.0,
            )

    non_rerank_short_circuit_error = ""
    try:
        non_rerank_short_circuit_hits = ParseStopMarqoSearchEngine().search(
            EngineQuery(image_data_url="data:image/png;base64,AAAA", limit=2)
        )
        non_rerank_short_circuit_product_ids = [
            hit.document.product_id for hit in non_rerank_short_circuit_hits
        ]
    except Exception as exc:
        non_rerank_short_circuit_product_ids = []
        non_rerank_short_circuit_error = f"{type(exc).__name__}: {exc}"

    malformed_hit_skip_error = ""
    try:
        malformed_hit_skip_hits = MalformedHitMarqoSearchEngine().search(
            EngineQuery(image_data_url="data:image/png;base64,AAAA", limit=2)
        )
        malformed_hit_skip_product_ids = [hit.document.product_id for hit in malformed_hit_skip_hits]
    except Exception as exc:
        malformed_hit_skip_product_ids = []
        malformed_hit_skip_error = f"{type(exc).__name__}: {exc}"

    text_rerank_query_prepare_calls = 0
    original_build_text_relevance_query = engine_module.build_text_relevance_query

    def counted_build_text_relevance_query(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal text_rerank_query_prepare_calls
        text_rerank_query_prepare_calls += 1
        return original_build_text_relevance_query(*args, **kwargs)

    text_rerank_candidate_probe = TextRerankCandidateWindowMarqoSearchEngine()
    engine_module.build_text_relevance_query = counted_build_text_relevance_query
    try:
        text_rerank_prefetched_hits = text_rerank_candidate_probe.search(EngineQuery(q="검정 우산", limit=2))
    finally:
        engine_module.build_text_relevance_query = original_build_text_relevance_query
    text_rerank_prefetched_product_ids = [hit.document.product_id for hit in text_rerank_prefetched_hits]
    text_rerank_prefetch_payload_limit = int((text_rerank_candidate_probe.payloads[0] or {}).get("limit") or 0)

    post_filter_prepare_calls = 0
    post_filter_prepare_error = ""
    original_prepare_product_filters = engine_module.prepare_product_filters

    def counted_prepare_product_filters(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal post_filter_prepare_calls
        post_filter_prepare_calls += 1
        return original_prepare_product_filters(*args, **kwargs)

    engine_module.prepare_product_filters = counted_prepare_product_filters
    try:
        post_filter_preparation_hits = PostFilterPreparationMarqoSearchEngine().search(
            EngineQuery(image_data_url="data:image/png;base64,AAAA", print_method="UV", limit=3)
        )
        post_filter_preparation_product_ids = [
            hit.document.product_id for hit in post_filter_preparation_hits
        ]
    except Exception as exc:
        post_filter_preparation_product_ids = []
        post_filter_prepare_error = f"{type(exc).__name__}: {exc}"
    finally:
        engine_module.prepare_product_filters = original_prepare_product_filters

    deep_post_filter_payload = engine.build_search_payload(
        EngineQuery(
            q="텀블러",
            limit=(3 + MAX_OPERATIONAL_SEARCH_OFFSET + 50 + 1) * 2,
            print_method="UV",
        )
    )
    filter_text = str(payload.get("filter") or "")
    numeric_only_filter_text = str(numeric_only_payload.get("filter") or "")
    deep_post_filter_text = str(deep_post_filter_payload.get("filter") or "")
    try:
        validate_settings(Settings(max_offset=MAX_OPERATIONAL_SEARCH_OFFSET))
        max_offset_cap_allows_configured_cap = True
    except Exception:
        max_offset_cap_allows_configured_cap = False
    try:
        validate_settings(Settings(max_offset=MAX_OPERATIONAL_SEARCH_OFFSET + 1))
        max_offset_cap_rejects_candidate_explosion = False
    except ValueError as exc:
        max_offset_cap_rejects_candidate_explosion = (
            f"HAEORUM_MAX_OFFSET must be at most {MAX_OPERATIONAL_SEARCH_OFFSET}" in str(exc)
        )
    runtime_service = AISearchService(
        LocalSearchEngine(
            [
                ProductDocument(
                    product_id="P-OFFSET",
                    mall_id="shop001",
                    name="offset cap probe",
                    category="probe",
                    status="active",
                )
            ]
        ),
        Settings(engine_backend="local", max_offset=MAX_OPERATIONAL_SEARCH_OFFSET + 1000),
        cache=MemorySearchCache(30),
    )
    try:
        runtime_service.search(
            SearchRequest(
                mall_id="shop001",
                q="offset cap probe",
                limit=1,
                offset=MAX_OPERATIONAL_SEARCH_OFFSET + 1,
            )
        )
        runtime_cap_blocks_misconfigured_settings = False
    except ValueError as exc:
        runtime_cap_blocks_misconfigured_settings = f"offset exceeds {MAX_OPERATIONAL_SEARCH_OFFSET}" in str(exc)

    class HealthCacheProbeMarqoSearchEngine(MarqoSearchEngine):
        def __init__(self) -> None:
            super().__init__(
                "http://marqo-api:8882",
                "haeorum-products-sim",
                admin_metrics_health_cache_seconds=30.0,
            )
            self.health_requests: list[str] = []

        def _request(self, method: str, path: str, payload: object = None, timeout: int = 60):
            self.health_requests.append(f"{method} {path}")
            if path == "/":
                return 200, {"ready": True}, 1.0
            if path == "/indexes/haeorum-products-sim/stats":
                return 200, {"numberOfDocuments": 300}, 2.0
            raise RuntimeError(f"unexpected health probe request: {method} {path}")

    health_cache_probe = HealthCacheProbeMarqoSearchEngine()
    first_health = health_cache_probe.health()
    second_health = health_cache_probe.health()
    health_cache_probe_reuses_backend_status = (
        health_cache_probe.health_requests == ["GET /", "GET /indexes/haeorum-products-sim/stats"]
        and (first_health.get("health_cache") or {}).get("hit") is False
        and (second_health.get("health_cache") or {}).get("hit") is True
        and (second_health.get("health_cache") or {}).get("enabled") is True
        and (second_health.get("stats") or {}).get("numberOfDocuments") == 300
    )

    class InvalidJsonThenValidResponse:
        status = 200
        bodies = [
            b"<html>temporary proxy error</html>",
            json.dumps(
                {
                    "hits": [
                        {
                            "_id": "P-JSON",
                            "product_id": "P-JSON",
                            "product_name": "JSON retry umbrella",
                            "category_name": "우산",
                            "status": "active",
                            "display_yn": "Y",
                            "_score": 0.91,
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        def read(self) -> bytes:
            return self.bodies.pop(0)

        def getheader(self, name: str) -> str:
            return "keep-alive" if name.lower() == "connection" else ""

    class InvalidJsonThenValidConnection:
        instances: list["InvalidJsonThenValidConnection"] = []

        def __init__(self, host: str, port: int | None = None, timeout: int | float | None = None) -> None:
            self.closed = False
            self.requests = 0
            self.__class__.instances.append(self)

        def request(
            self,
            method: str,
            path: str,
            body: bytes | None = None,
            headers: dict[str, str] | None = None,
        ) -> None:
            if self.closed:
                raise AssertionError("invalid JSON backend connection was reused")
            self.requests += 1

        def getresponse(self) -> InvalidJsonThenValidResponse:
            return InvalidJsonThenValidResponse()

        def close(self) -> None:
            self.closed = True

    invalid_backend_json_retry_error = ""
    invalid_backend_json_retry_product_ids: list[str] = []
    invalid_backend_json_retry_stats: dict[str, Any] = {}
    invalid_backend_json_retry_closed_first_connection = False
    original_http_connection = engine_module.http.client.HTTPConnection
    InvalidJsonThenValidResponse.bodies = [
        b"<html>temporary proxy error</html>",
        json.dumps(
            {
                "hits": [
                    {
                        "_id": "P-JSON",
                        "product_id": "P-JSON",
                        "product_name": "JSON retry umbrella",
                        "category_name": "우산",
                        "status": "active",
                        "display_yn": "Y",
                        "_score": 0.91,
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8"),
    ]
    InvalidJsonThenValidConnection.instances = []
    engine_module.http.client.HTTPConnection = InvalidJsonThenValidConnection
    try:
        invalid_json_probe_engine = MarqoSearchEngine(
            "http://marqo-api:8882",
            "haeorum-products-sim",
            search_retry_count=1,
            search_retry_delay_seconds=0.0,
        )
        invalid_backend_json_retry_hits = invalid_json_probe_engine.search(EngineQuery(q="우산", limit=1))
        invalid_backend_json_retry_product_ids = [
            hit.document.product_id for hit in invalid_backend_json_retry_hits
        ]
        invalid_backend_json_retry_stats = invalid_json_probe_engine._marqo_http.stats()
        invalid_backend_json_retry_closed_first_connection = (
            bool(InvalidJsonThenValidConnection.instances)
            and InvalidJsonThenValidConnection.instances[0].closed
        )
    except Exception as exc:
        invalid_backend_json_retry_error = f"{type(exc).__name__}: {exc}"
    finally:
        engine_module.http.client.HTTPConnection = original_http_connection

    checks = {
        "status_filter": "status:(active)" in filter_text,
        "min_price_range_filter": "(price:[1000 TO *] OR price_min:[1000 TO *] OR price_max:[1000 TO *])" in filter_text,
        "max_price_range_filter": "(price:[* TO 10000] OR price_min:[* TO 10000] OR price_max:[* TO 10000])" in filter_text,
        "quantity_range_filter": "min_order_qty:[* TO 100]" in filter_text,
        "delivery_range_filter": "delivery_days:[* TO 5]" in filter_text,
        "text_attribute_filter_left_for_post_filter": "print_methods:(" not in filter_text,
        "overfetch_safety_kept": int(payload.get("limit") or 0) >= 100,
        "max_offset_cap_allows_configured_cap": max_offset_cap_allows_configured_cap,
        "max_offset_cap_rejects_candidate_explosion": max_offset_cap_rejects_candidate_explosion,
        "runtime_cap_blocks_misconfigured_settings": runtime_cap_blocks_misconfigured_settings,
        "admin_metrics_health_cache_reuses_backend_status": health_cache_probe_reuses_backend_status,
        "invalid_backend_json_retried_on_new_connection": (
            invalid_backend_json_retry_product_ids == ["P-JSON"]
            and invalid_backend_json_retry_stats.get("invalid_json_responses") == 1
            and invalid_backend_json_retry_stats.get("error_responses") == 1
            and invalid_backend_json_retry_stats.get("connections_opened") == 2
            and invalid_backend_json_retry_closed_first_connection
            and not invalid_backend_json_retry_error
        ),
        "deep_post_filter_candidate_limit_capped": int(deep_post_filter_payload.get("limit") or 0)
        == MARQO_MAX_SEARCH_CANDIDATES,
        "deep_post_filter_keeps_fuzzy_filter_for_post_filter": "print_methods:(" not in deep_post_filter_text,
        "numeric_only_filter_pushdown_uses_lower_candidate_limit": int(numeric_only_payload.get("limit") or 0) == 20,
        "non_rerank_search_stops_parsing_after_requested_hits": non_rerank_short_circuit_product_ids
        == ["P001", "P002"],
        "malformed_marqo_hits_skipped": malformed_hit_skip_product_ids == ["P001", "P002"],
        "text_rerank_prefetched_candidates_returned_to_service": (
            text_rerank_prefetch_payload_limit >= MARQO_TEXT_RERANK_MIN_CANDIDATES
            and len(text_rerank_prefetched_product_ids) > 2
            and text_rerank_prefetched_product_ids == [f"P{index:03d}" for index in range(1, 8)]
        ),
        "text_rerank_query_terms_prepared_once": text_rerank_query_prepare_calls == 1,
        "marqo_post_filters_prepared_once": (
            post_filter_prepare_calls == 1
            and post_filter_preparation_product_ids == ["P001", "P002", "P003"]
        ),
        "numeric_only_filter_pushdown_keeps_range_filters": all(
            token in numeric_only_filter_text
            for token in [
                "(price:[1000 TO *] OR price_min:[1000 TO *] OR price_max:[1000 TO *])",
                "(price:[* TO 10000] OR price_min:[* TO 10000] OR price_max:[* TO 10000])",
                "min_order_qty:[* TO 100]",
                "delivery_days:[* TO 5]",
            ]
        ),
        "post_filter_fields_retrieved": all(
            field in (payload.get("attributesToRetrieve") or [])
            for field in [SEARCH_TEXT_FIELD, "print_methods", "price_min", "price_max", "min_order_qty", "delivery_days"]
        ),
        "text_rerank_uses_compact_search_text": (
            SEARCH_TEXT_FIELD in (payload.get("attributesToRetrieve") or [])
            and "description" not in (payload.get("attributesToRetrieve") or [])
            and "keywords" not in (payload.get("attributesToRetrieve") or [])
            and "materials" not in (payload.get("attributesToRetrieve") or [])
            and "colors" not in (payload.get("attributesToRetrieve") or [])
        ),
        "numeric_only_retrieves_only_needed_filter_fields": (
            "price_min" in (numeric_only_payload.get("attributesToRetrieve") or [])
            and "price_max" in (numeric_only_payload.get("attributesToRetrieve") or [])
            and "min_order_qty" in (numeric_only_payload.get("attributesToRetrieve") or [])
            and "delivery_days" in (numeric_only_payload.get("attributesToRetrieve") or [])
            and SEARCH_TEXT_FIELD not in (numeric_only_payload.get("attributesToRetrieve") or [])
            and "description" not in (numeric_only_payload.get("attributesToRetrieve") or [])
            and "keywords" not in (numeric_only_payload.get("attributesToRetrieve") or [])
        ),
        "image_only_omits_text_rerank_attributes": all(
            field not in (image_only_payload.get("attributesToRetrieve") or [])
            for field in [
                "description",
                "keywords",
                SEARCH_TEXT_FIELD,
                "print_methods",
                "materials",
                "colors",
                "min_order_qty",
                "price_min",
                "price_max",
                "delivery_days",
            ]
        ),
    }
    return mark_simulated(
        {
            "ok": all(checks.values()),
            "checks": checks,
            "filter": filter_text,
            "limit": payload.get("limit"),
            "numeric_only_filter": numeric_only_filter_text,
            "numeric_only_limit": numeric_only_payload.get("limit"),
            "numeric_only_attributes_to_retrieve": numeric_only_payload.get("attributesToRetrieve"),
            "image_only_attributes_to_retrieve": image_only_payload.get("attributesToRetrieve"),
            "non_rerank_short_circuit_product_ids": non_rerank_short_circuit_product_ids,
            "non_rerank_short_circuit_error": non_rerank_short_circuit_error,
            "malformed_hit_skip_product_ids": malformed_hit_skip_product_ids,
            "malformed_hit_skip_error": malformed_hit_skip_error,
            "text_rerank_prefetch_payload_limit": text_rerank_prefetch_payload_limit,
            "text_rerank_prefetched_product_ids": text_rerank_prefetched_product_ids,
            "text_rerank_query_prepare_calls": text_rerank_query_prepare_calls,
            "post_filter_prepare_calls": post_filter_prepare_calls,
            "post_filter_preparation_product_ids": post_filter_preparation_product_ids,
            "post_filter_prepare_error": post_filter_prepare_error,
            "health_cache_probe_requests": health_cache_probe.health_requests,
            "first_health_cache": first_health.get("health_cache"),
            "second_health_cache": second_health.get("health_cache"),
            "invalid_backend_json_retry_product_ids": invalid_backend_json_retry_product_ids,
            "invalid_backend_json_retry_stats": invalid_backend_json_retry_stats,
            "invalid_backend_json_retry_error": invalid_backend_json_retry_error,
            "deep_post_filter_limit": deep_post_filter_payload.get("limit"),
            "max_marqo_search_candidates": MARQO_MAX_SEARCH_CANDIDATES,
            "max_operational_search_offset": MAX_OPERATIONAL_SEARCH_OFFSET,
            "attributes_to_retrieve": payload.get("attributesToRetrieve"),
            "note": (
                "Numeric domain filters are pushed into Marqo range filters while fuzzy text attribute filters "
                "remain protected by application post-filtering. Numeric-only image/mixed searches keep the lower "
                "candidate limit because Marqo can apply those filters before returning hits. Non-reranked "
                "image/mixed searches stop parsing Marqo hits once enough filtered candidates are collected and skip "
                "malformed Marqo hits instead of failing the whole public search. The max offset cap and "
                "Marqo candidate limit cap keep related-item deep pagination from multiplying Marqo candidate requests "
                "without bound. Text rerank requests retrieve a compact indexed search_text field instead of "
                "separate description/keywords/attribute text fields to reduce Marqo response bytes and Python parse cost. "
                "Prefetched text rerank candidates are returned to the service collapse layer so product-group variants "
                "do not force an avoidable second Marqo request. Text rerank prepares query terms once per candidate "
                "window instead of repeating query synonym expansion for every Marqo hit. Marqo post-filter terms are "
                "also prepared once per candidate window instead of recomputing fuzzy filter tokens for every hit."
            ),
        }
    )


def run_marqo_index_batch_probe() -> dict[str, Any]:
    class BatchProbeMarqoSearchEngine(MarqoSearchEngine):
        def __init__(self, *, embedding_backend: str = "native", max_request_bytes: int = 1800) -> None:
            super().__init__(
                "http://marqo-api:8882",
                "haeorum-products-sim",
                embedding_backend=embedding_backend,
                qwen_embedding_dimensions=32,
                add_documents_batch_size=5,
                add_documents_max_request_bytes=max_request_bytes,
                delete_documents_batch_size=5,
            )
            self._index_checked = True
            self.add_requests: list[dict[str, Any]] = []
            self.delete_requests: list[int] = []
            self.text_batch_sizes: list[int] = []
            self.image_batch_sizes: list[int] = []

        def qwen_embed_product_texts(self, texts: list[str]) -> list[list[float]]:
            self.text_batch_sizes.append(len(texts))
            return [[0.12345 for _ in range(32)] for _ in texts]

        def qwen_embed_images(self, images: list[str], **_: object) -> list[list[float]]:
            self.image_batch_sizes.append(len(images))
            return [[0.54321 for _ in range(32)] for _ in images]

        def _request(self, method: str, path: str, payload: Any = None, timeout: int | float = 60):
            if path.endswith("/documents"):
                docs = list((payload or {}).get("documents") or [])
                self.add_requests.append(
                    {
                        "documents": len(docs),
                        "bytes": json_body_size(payload),
                        "default_json_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                    }
                )
                return 200, {"items": [{"_id": doc["_id"], "status": 200} for doc in docs]}, 1.0
            if path.endswith("/documents/delete-batch"):
                ids = list(payload or [])
                self.delete_requests.append(len(ids))
                return 200, {"items": [{"_id": product_id, "status": 200} for product_id in ids]}, 1.0
            return 200, {}, 1.0

    class FailingBatchProbeMarqoSearchEngine(MarqoSearchEngine):
        def __init__(self) -> None:
            super().__init__(
                "http://marqo-api:8882",
                "haeorum-products-sim",
                add_documents_batch_size=5,
                add_documents_max_request_bytes=1024 * 1024,
            )
            self._index_checked = True

        def _request(self, method: str, path: str, payload: Any = None, timeout: int | float = 60):
            if path.endswith("/documents"):
                docs = list((payload or {}).get("documents") or [])
                return 200, {
                    "items": [
                        {
                            "_id": doc["_id"],
                            "statusCode": 500,
                            "message": "simulated add document failure",
                        }
                        for doc in docs
                    ]
                }, 1.0
            return 200, {}, 1.0

    native_products = [
        ProductDocument(
            product_id=f"P-BATCH-{index}",
            name=f"색인 배치 상품 {index}",
            category="판촉물",
            status="active",
            description="대량 색인 요청 크기 probe " + ("x" * 650),
        )
        for index in range(12)
    ]
    qwen_products = [
        ProductDocument(
            product_id=f"P-QWEN-BATCH-{index}",
            name=f"Qwen 색인 배치 상품 {index}",
            category="우산",
            status="active",
            main_image_url=f"https://images.example.test/qwen-batch-{index}.jpg",
            description="Qwen 벡터 payload probe " + ("x" * 350),
        )
        for index in range(4)
    ]
    native_request_byte_limit = 2200
    native_engine = BatchProbeMarqoSearchEngine(max_request_bytes=native_request_byte_limit)
    qwen_engine = BatchProbeMarqoSearchEngine(embedding_backend="qwen", max_request_bytes=2400)
    native_result = native_engine.upsert_products(native_products)
    delete_result = native_engine.delete_products([f"P-DELETE-BATCH-{index}" for index in range(12)])
    qwen_result = qwen_engine.upsert_products(qwen_products)
    failure_product_count = engine_module.MARQO_BATCH_FAILURE_SAMPLE_LIMIT + 11
    failure_products = [
        ProductDocument(
            product_id=f"P-FAIL-BATCH-{index}",
            name=f"실패 색인 상품 {index}",
            category="판촉물",
            status="active",
        )
        for index in range(failure_product_count)
    ]
    failure_result = FailingBatchProbeMarqoSearchEngine().upsert_products(failure_products)
    native_request_bytes = [int(request["bytes"]) for request in native_engine.add_requests]
    native_default_request_bytes = [int(request["default_json_bytes"]) for request in native_engine.add_requests]
    native_batch_sizes = [int(request["documents"]) for request in native_engine.add_requests]
    qwen_request_bytes = [int(request["bytes"]) for request in qwen_engine.add_requests]
    qwen_default_request_bytes = [int(request["default_json_bytes"]) for request in qwen_engine.add_requests]
    qwen_batch_sizes = [int(request["documents"]) for request in qwen_engine.add_requests]
    checks = {
        "native_streaming_batches_created": len(native_engine.add_requests) > math.ceil(len(native_products) / 5),
        "native_batch_size_cap_kept": native_batch_sizes and max(native_batch_sizes) <= 5,
        "native_request_byte_cap_kept": native_request_bytes and max(native_request_bytes) <= native_request_byte_limit,
        "native_stats_reported": native_result.get("batch_count") == len(native_engine.add_requests)
        and native_result.get("max_request_body_bytes") == max(native_request_bytes),
        "delete_batches_created": native_engine.delete_requests == [5, 5, 2],
        "delete_stats_reported": delete_result.get("delete_batch_count") == 3
        and delete_result.get("max_delete_batch_size") == 5,
        "qwen_embedding_batch_kept": qwen_engine.text_batch_sizes == [4] and qwen_engine.image_batch_sizes == [4],
        "qwen_marqo_payload_split_after_embedding": len(qwen_engine.add_requests) > 1
        and max(qwen_batch_sizes) < 5,
        "qwen_request_byte_cap_kept": qwen_request_bytes and max(qwen_request_bytes) <= 2400,
        "qwen_stats_reported": qwen_result.get("batch_count") == len(qwen_engine.add_requests)
        and qwen_result.get("max_request_body_bytes") == max(qwen_request_bytes),
        "compact_json_request_accounting": (
            native_request_bytes
            and qwen_request_bytes
            and sum(native_request_bytes) < sum(native_default_request_bytes)
            and sum(qwen_request_bytes) < sum(qwen_default_request_bytes)
        ),
        "batch_response_retention_bounded": native_result.get("responses_truncated") is True
        and native_result.get("response_retained_count") == engine_module.MARQO_BATCH_RESPONSE_SAMPLE_LIMIT
        and len((native_result.get("response") or {}).get("responses") or [])
        == engine_module.MARQO_BATCH_RESPONSE_SAMPLE_LIMIT
        and bool((native_result.get("response") or {}).get("last_response")),
        "batch_failure_detail_retention_bounded": failure_result.get("failed") == failure_product_count
        and failure_result.get("failed_product_retained_count") == engine_module.MARQO_BATCH_FAILURE_SAMPLE_LIMIT
        and len(failure_result.get("failed_products") or []) == engine_module.MARQO_BATCH_FAILURE_SAMPLE_LIMIT
        and failure_result.get("failed_products_truncated") is True,
    }
    return mark_simulated(
        {
            "ok": all(checks.values()),
            "checks": checks,
            "native_batch_sizes": native_batch_sizes,
            "native_request_bytes": native_request_bytes,
            "native_request_byte_limit": native_request_byte_limit,
            "native_default_json_request_bytes": native_default_request_bytes,
            "native_compact_json_saved_bytes": sum(native_default_request_bytes) - sum(native_request_bytes),
            "native_result": native_result,
            "delete_batch_sizes": native_engine.delete_requests,
            "delete_result": delete_result,
            "qwen_embedding_text_batch_sizes": qwen_engine.text_batch_sizes,
            "qwen_embedding_image_batch_sizes": qwen_engine.image_batch_sizes,
            "qwen_marqo_batch_sizes": qwen_batch_sizes,
            "qwen_marqo_request_bytes": qwen_request_bytes,
            "qwen_default_json_request_bytes": qwen_default_request_bytes,
            "qwen_compact_json_saved_bytes": sum(qwen_default_request_bytes) - sum(qwen_request_bytes),
            "qwen_result": qwen_result,
            "failure_result": failure_result,
            "note": (
                "Exercises direct Marqo streaming document batches and Qwen vector add-documents byte splitting "
                "without contacting a real backend. Successful batch response payloads are sampled instead of "
                "retained without bound so large operational reindexes do not accumulate every Marqo success response. "
                "Failed product details are also sampled while the total failed count is preserved."
            ),
        }
    )


def run_load_mall_identity_probe(paths: SimulationPaths) -> dict[str, Any]:
    args = namespace(
        base_url="https://ai-search.haeorumgift.com",
        origin="https://shop0001.haeorumgift.com",
        scenario="mixed-traffic",
        traffic_mix="text=50,image=25,mixed=25",
        requests=100,
        active_users=100,
        concurrency=20,
        limit=20,
        p95_ms=5000,
        max_error_rate=1.0,
        api_server_count=2,
        image_file="",
        image_max_mb=10,
        mall_id="shop0001",
        api_key="pk_live_shop0001_simulated_primary_key_abcdef",
        admin_key="admin",
        mall_config=str(paths.malls_json),
        mall_sample_size=4,
        expected_product_url_prefix="",
        allow_local_target=False,
    )
    identities = load_mall_identities(args)
    payloads, mode_counts, traffic_mix_percent = build_load_payloads(args, "data:image/png;base64,AAAA")
    specs = build_load_request_specs(payloads, identities)
    requested_mall_ids = [spec.payload.get("mall_id") for spec in specs]
    header_origins = [spec.headers.get("Origin") for spec in specs]
    header_api_keys = [spec.headers.get("X-API-Key") for spec in specs]
    expected_prefixes = [spec.expected_product_url_prefix for spec in specs]
    checks = {
        "sampled_requested_malls": len(identities) == 4,
        "requests_cover_sampled_malls": set(requested_mall_ids) == {identity.mall_id for identity in identities},
        "per_request_api_key_headers": all(header_api_keys),
        "per_request_origin_headers": all(header_origins),
        "per_request_product_url_prefixes": all(expected_prefixes),
        "mode_mix_preserved": mode_counts == {"text": 50, "image": 25, "mixed": 25},
    }
    return mark_simulated(
        {
            "ok": all(checks.values()),
            "checks": checks,
            "mode_counts": mode_counts,
            "traffic_mix_percent": traffic_mix_percent,
            "identity_summary": load_identity_summary(
                identities,
                4,
                sampling_strategy=getattr(args, "mall_sample_strategy", ""),
                source_enabled_count=getattr(args, "mall_sample_source_enabled_count", None),
                eligible_mall_count=getattr(args, "mall_sample_eligible_count", None),
            ),
            "requested_mall_ids": requested_mall_ids,
            "request_origin_count": len(set(header_origins)),
            "request_api_key_count": len(set(header_api_keys)),
            "request_product_url_prefix_count": len(set(expected_prefixes)),
            "note": (
                "Mixed-traffic load evidence can now cycle requests across mall_config entries so 1,700-mall "
                "deployments exercise per-mall API keys, Origin allowlists, and product URL templates instead of "
                "repeating one mall identity."
            ),
        }
    )


def namespace(**values: Any) -> argparse.Namespace:
    return argparse.Namespace(**values)


def reset_runtime_jsonl_outputs(paths: SimulationPaths) -> None:
    for path in [
        paths.search_log,
        paths.output_dir / "mixed-weight-sweep-search.jsonl",
        paths.output_dir / "sync-simulation.jsonl",
    ]:
        if path.exists():
            path.unlink()


@contextmanager
def isolated_local_runtime_environment():
    original = dict(os.environ)
    try:
        for name in list(os.environ):
            if name.startswith("HAEORUM_") or name == "MARQO_URL":
                os.environ.pop(name, None)
        os.environ.update(
            {
                "HAEORUM_ENV": "development",
                "HAEORUM_SEARCH_ENGINE": "local",
                "HAEORUM_CORS_ORIGINS": "*",
            }
        )
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    paths = make_paths(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    reset_runtime_jsonl_outputs(paths)
    reference_images = write_reference_images(paths.quality_image_dir)

    product_count = max(args.products, args.malls + args.poc_size - 1)
    rows = generate_product_rows(
        product_count,
        args.malls,
        reference_images,
        representative_mall_catalog_size=args.poc_size,
    )
    write_products(paths.products_csv, rows)
    mall_config = generate_mall_config(args.malls)
    write_mall_source_csv(paths.mall_source_csv, mall_config)
    write_json(paths.malls_json, mall_config)
    mall_config_build_report = build_simulated_mall_config_builder_report(paths, args.malls)
    write_json(paths.simulated_mall_config_build_report, mall_config_build_report)
    view_report = analyze_generated_view(rows)
    write_simulated_mssql_database(paths.simulated_mssql_db, rows)
    simulated_mssql_view_report = build_simulated_mssql_view_report(paths)
    simulated_mssql_export_report = build_simulated_mssql_export_report(paths, rows, paths.malls_json)
    simulated_mssql_alias_compatibility_report = build_simulated_mssql_alias_compatibility_report(rows)
    sync_lifecycle_report = build_simulated_sync_lifecycle_report(paths, rows)
    write_json(paths.simulated_mssql_view_report, simulated_mssql_view_report)
    write_json(paths.simulated_mssql_export_report, simulated_mssql_export_report)
    write_json(paths.simulated_mssql_alias_compatibility_report, simulated_mssql_alias_compatibility_report)
    write_json(paths.simulated_sync_lifecycle_report, sync_lifecycle_report)

    mall_report = mark_simulated(validate_mall_config(paths.malls_json, min_count=args.malls))
    write_json(output_dir / "mall-config-check.json", mall_report)

    env_values = write_env_file(paths.env_file, paths, mall_config)
    env_report = mark_simulated(build_env_check_report(
        namespace(
            env_file=str(paths.env_file),
            role="api",
            api_server_count=2,
            allow_non_production=False,
            skip_path_checks=False,
        )
    ))
    write_json(output_dir / "env-check.json", env_report)
    (output_dir / "env-check.md").write_text(env_check_to_markdown(env_report), encoding="utf-8")

    products = load_products(paths.products_csv)
    selected, poc_report = build_poc_dataset(
        products,
        target_size=args.poc_size,
        min_products=args.poc_size,
        min_per_recommended_category=args.min_per_category,
        recommended_categories=RECOMMENDED_CATEGORIES,
        source_csv=paths.products_csv,
    )
    write_products_csv(paths.poc_products_csv, selected)
    poc_report["output_csv"] = str(paths.poc_products_csv)
    poc_report["output_csv_fingerprint"] = file_fingerprint(paths.poc_products_csv)
    mark_simulated(poc_report)
    write_json(output_dir / "poc-dataset.json", poc_report)
    operational_risk_probe_report = build_operational_risk_probe_report(paths, rows, reference_images, mall_config)
    write_json(paths.simulated_operational_risk_probe_report, operational_risk_probe_report)

    quality_cases = write_quality_cases(paths.quality_cases_json, reference_images)
    representative_sites = write_representative_sites(paths.representative_sites_json, mall_config)
    representative_site_pages = write_representative_site_pages(paths, representative_sites)
    write_json(paths.representative_sites_json, representative_sites)
    widget_integration_probe_report = build_simulated_widget_integration_probe_report(
        representative_sites,
        representative_site_pages,
    )
    widget_snippet_manifest = write_snippet_bundle(
        widget_integration_probe_report,
        paths.simulated_widget_snippets_dir,
        manifest_extra={
            "simulation_marker": SIMULATION_MARKER,
            "simulation_only": True,
            "not_operational_readiness": True,
            "simulation_note": (
                "Generated from local representative-site simulation pages. These snippets are review helpers, "
                "not production representative-site evidence."
            ),
        },
    )
    write_json(paths.simulated_widget_integration_probe_report, widget_integration_probe_report)
    (output_dir / "widget-integration-probe.md").write_text(
        widget_integration_probe_to_markdown(widget_integration_probe_report),
        encoding="utf-8",
    )
    representative_sites_report = build_simulated_representative_site_report(
        paths,
        representative_sites,
        representative_site_pages,
        reference_images,
    )
    write_json(paths.simulated_representative_sites_report, representative_sites_report)
    api_scale_inputs = write_api_scale_inputs(paths, reference_images, mall_config)
    api_scale_report = build_simulated_api_scale_report(paths)
    write_json(paths.simulated_api_scale_report, api_scale_report)
    (output_dir / "api-scale.md").write_text(load_compare_to_markdown(api_scale_report), encoding="utf-8")
    operational_evidence_config = write_operational_evidence_config(
        paths.operational_evidence_config_json,
        paths,
        mall_config,
        reference_images,
    )

    csv_index_args = namespace(
        csv=str(paths.poc_products_csv),
        mode="reindex",
        since=None,
        engine="local",
        index_name="haeorum-products-simulation",
        marqo_url="",
        marqo_model="",
        sync_log=str(output_dir / "sync-simulation.jsonl"),
        validate_images=False,
        dry_run=True,
    )
    csv_index_dry_run_report = mark_simulated(run_csv_index(csv_index_args, base_settings=Settings(engine_backend="local")))
    write_json(output_dir / "csv-index-dry-run.json", csv_index_dry_run_report)
    (output_dir / "csv-index-dry-run.md").write_text(csv_index_to_markdown(csv_index_dry_run_report), encoding="utf-8")
    csv_index_report = copy.deepcopy(csv_index_dry_run_report)
    csv_index_report.update(
        {
            "dry_run": False,
            "engine": "marqo",
            "marqo_url": "http://marqo-api:8882",
            "marqo_model": "Marqo/marqo-ecommerce-embeddings-L",
            "persistent_index": True,
            "validate_images": True,
            "indexed": len(selected),
            "expected_index_document_count": len(selected),
            "post_index_document_count": len(selected),
            "post_index_document_count_ok": True,
            "would_index": 0,
            "simulation_note": "Canonical simulated csv-index.json mirrors the Marqo lineage fields required by operational readiness.",
        }
    )
    write_json(paths.simulated_csv_index_report, csv_index_report)

    quality_args = namespace(
        csv=str(paths.poc_products_csv),
        mall_id="shop0001",
        limit=20,
        min_products=args.poc_size,
        recommended_products=args.poc_size,
        max_text_ms=3000,
        max_image_ms=5000,
        max_mixed_ms=5000,
        engine="local",
        marqo_url="",
        index_name="",
        marqo_model="",
        cases=str(paths.quality_cases_json),
        mall_config=str(paths.malls_json),
        strict=True,
    )
    with isolated_local_runtime_environment():
        quality_report_local = mark_simulated(build_quality_report(quality_args))
    quality_report = copy.deepcopy(quality_report_local)
    quality_report.update(
        {
            "engine": "marqo",
            "local_only": False,
            "case_source": str(paths.quality_cases_json),
            "custom_cases": True,
            "simulation_note": "Canonical simulated quality-report.json keeps local result timings but carries Marqo lineage fields for readiness rehearsal.",
        }
    )
    quality_source = quality_report.get("source") if isinstance(quality_report.get("source"), dict) else {}
    quality_source.update(
        {
            "engine": "marqo",
            "index_name": "haeorum-products-simulation",
            "marqo_url": "http://marqo-api:8882",
            "marqo_model": "Marqo/marqo-ecommerce-embeddings-L",
        }
    )
    quality_report["source"] = quality_source
    mark_simulated(quality_report)
    write_json(output_dir / "quality-report-local.json", quality_report_local)
    write_json(paths.simulated_quality_report, quality_report)
    (output_dir / "quality-report-local.md").write_text(quality_report_to_markdown(quality_report_local), encoding="utf-8")

    security_report = build_simulated_security_report(paths, env_values)
    write_json(paths.simulated_security_report, security_report)
    (output_dir / "security.md").write_text(security_report_to_markdown(security_report), encoding="utf-8")

    write_json(paths.simulated_api_smoke_report, build_simulated_api_smoke_report(mall_config))
    write_json(paths.simulated_image_url_report, build_simulated_image_url_report(paths))
    write_json(paths.simulated_marqo_resource_report, build_simulated_marqo_resource_report())
    write_json(paths.simulated_server_preflight_report, build_simulated_server_preflight_report())
    write_json(
        paths.simulated_load_text_report,
        simulated_load_report(
            mode="text",
            requests=100,
            concurrency=100,
            p95_ms=3000,
            reference_images=reference_images,
            mall_config=mall_config,
        ),
    )
    write_json(
        paths.simulated_load_image_report,
        simulated_load_report(
            mode="image",
            requests=30,
            concurrency=30,
            p95_ms=5000,
            reference_images=reference_images,
            mall_config=mall_config,
        ),
    )
    write_json(
        paths.simulated_load_mixed_report,
        simulated_load_report(
            mode="mixed",
            requests=30,
            concurrency=30,
            p95_ms=5000,
            reference_images=reference_images,
            mall_config=mall_config,
        ),
    )
    write_json(
        paths.simulated_load_mixed_traffic_report,
        simulated_load_report(
            mode="mixed-traffic",
            requests=850,
            concurrency=100,
            p95_ms=5000,
            reference_images=reference_images,
            mall_config=mall_config,
            active_users=850,
        ),
    )
    write_json(paths.simulated_widget_dom_report, build_simulated_widget_dom_report(mall_config))
    evidence_slot_report = build_simulated_evidence_slot_report(paths)

    search_probe = run_local_search_probe(
        paths,
        reference_images,
        requests=args.load_requests,
        concurrency=args.load_concurrency,
        p95_ms=getattr(args, "load_p95_ms", 5000),
        p99_ms=getattr(args, "load_p99_ms", 8000),
    )
    cache_concurrency_probe = run_search_cache_concurrency_probe(paths)
    search_execution_queue_probe = run_search_execution_queue_probe()
    qwen_query_vector_probe = run_qwen_query_vector_concurrency_probe()
    marqo_filter_pushdown_probe = run_marqo_filter_pushdown_probe()
    marqo_index_batch_probe = run_marqo_index_batch_probe()
    response_materialization_probe = run_response_materialization_probe(paths)
    load_mall_identity_probe = run_load_mall_identity_probe(paths)
    search_probe["cache_concurrency_probe"] = cache_concurrency_probe
    search_probe["search_execution_queue_probe"] = search_execution_queue_probe
    search_probe["qwen_query_vector_probe"] = qwen_query_vector_probe
    search_probe["marqo_filter_pushdown_probe"] = marqo_filter_pushdown_probe
    search_probe["marqo_index_batch_probe"] = marqo_index_batch_probe
    search_probe["response_materialization_probe"] = response_materialization_probe
    search_probe["load_mall_identity_probe"] = load_mall_identity_probe
    write_json(output_dir / "local-search-load-probe.json", search_probe)
    write_json(paths.simulated_qwen_query_vector_probe_report, qwen_query_vector_probe)
    write_json(output_dir / "marqo-filter-pushdown-probe.json", marqo_filter_pushdown_probe)
    write_json(output_dir / "marqo-index-batch-probe.json", marqo_index_batch_probe)
    write_json(output_dir / "response-materialization-probe.json", response_materialization_probe)
    write_json(output_dir / "load-mall-identity-probe.json", load_mall_identity_probe)
    mixed_weight_sweep_report = run_mixed_weight_sweep(paths, reference_images)
    write_json(paths.simulated_mixed_weight_sweep_report, mixed_weight_sweep_report)
    search_insights_report = mark_simulated(
        build_search_insights_report(paths.search_log, limit=20, min_searches=1)
    )
    write_json(paths.simulated_search_insights_report, search_insights_report)
    write_json(paths.simulated_search_synonyms_seed, mark_simulated(build_synonyms_seed_payload(search_insights_report)))
    write_json(paths.simulated_search_quality_cases_seed, mark_simulated(build_quality_cases_seed_payload(search_insights_report)))
    (output_dir / "search-insights.md").write_text(
        search_insights_to_markdown(search_insights_report),
        encoding="utf-8",
    )

    checks = {
        "generated_mssql_view_shape": view_report["ok"],
        "simulated_mssql_view_contract": simulated_mssql_view_report["ok"],
        "simulated_mssql_export_contract": simulated_mssql_export_report["ok"],
        "simulated_mssql_export_streamed_csv": (
            simulated_mssql_export_report.get("streamed_product_csv") is True
            and simulated_mssql_export_report.get("retained_product_rows") == 0
            and simulated_mssql_export_report.get("csv_rows_written")
            == simulated_mssql_export_report.get("exported_products")
        ),
        "simulated_mssql_alias_compatibility": simulated_mssql_alias_compatibility_report["ok"],
        "poc_dataset": poc_report["ok"],
        "mall_config": mall_report["ok"],
        "mall_config_builder_roundtrip": mall_config_build_report["ok"],
        "env_preflight_shape": env_report["ok"],
        "csv_index_dry_run": csv_index_report["ok"],
        "quality_report_local": quality_report["ok"],
        "local_search_load_probe": search_probe["ok"],
        "local_search_load_probe_performance": search_probe["performance_ok"],
        "local_search_cache_concurrency_probe": cache_concurrency_probe["ok"],
        "local_search_execution_queue_probe": search_execution_queue_probe["ok"],
        "qwen_query_vector_concurrency_probe": qwen_query_vector_probe["ok"],
        "marqo_filter_pushdown_probe": marqo_filter_pushdown_probe["ok"],
        "marqo_index_batch_probe": marqo_index_batch_probe["ok"],
        "response_materialization_probe": response_materialization_probe["ok"],
        "load_mall_identity_probe": load_mall_identity_probe["ok"],
        "mixed_weight_sweep": mixed_weight_sweep_report["ok"],
        "mixed_weight_sweep_coverage": mixed_weight_sweep_report["coverage_ok"],
        "search_insights_quality_loop": search_insights_report["ok"],
        "operational_risk_negative_controls": operational_risk_probe_report["ok"],
        "simulated_sync_lifecycle": sync_lifecycle_report["ok"],
        "simulated_widget_integration_probe": widget_integration_probe_report["ok"],
        "simulated_widget_snippet_bundle": bool(
            widget_snippet_manifest.get("review_required")
            and widget_snippet_manifest.get("not_operational_readiness")
            and widget_snippet_manifest.get("snippet_count")
        ),
        "simulated_widget_preview_bundle": bool(
            widget_snippet_manifest.get("preview_count")
            and widget_snippet_manifest.get("preview_count") == widget_snippet_manifest.get("snippet_count")
            and widget_snippet_manifest.get("preview_validation_ok") is True
            and Path(str(widget_snippet_manifest.get("preview_validation_json") or "")).exists()
        ),
        "simulated_widget_manual_install_plan": bool(
            widget_snippet_manifest.get("manual_install_plan_json")
            and widget_snippet_manifest.get("manual_install_plan_markdown")
            and Path(str(widget_snippet_manifest.get("manual_install_plan_json"))).exists()
            and Path(str(widget_snippet_manifest.get("manual_install_plan_markdown"))).exists()
            and widget_snippet_manifest.get("manual_install_ready_pages")
            == widget_integration_probe_report.get("pages_ready_for_data_auto_init")
        ),
        "simulated_representative_sites": representative_sites_report["ok"],
        "simulated_api_scale_comparison": api_scale_report["ok"],
        "simulated_security_templates": security_report["ok"],
        "simulated_evidence_slot_coverage": evidence_slot_report["ok"],
    }
    report = {
        "ok": all(checks.values()),
        "simulation_marker": SIMULATION_MARKER,
        "simulation_only": True,
        "not_operational_readiness": True,
        "generated_at_epoch": int(time.time()),
        "inputs": {
            "products": product_count,
            "malls": args.malls,
            "poc_size": args.poc_size,
            "min_per_category": args.min_per_category,
            "load_requests": args.load_requests,
            "load_concurrency": args.load_concurrency,
            "load_p95_ms": getattr(args, "load_p95_ms", 5000),
            "load_p99_ms": getattr(args, "load_p99_ms", 8000),
        },
        "paths": {name: str(value) for name, value in paths.__dict__.items() if isinstance(value, Path)},
        "checks": checks,
        "view_report": view_report,
        "simulated_mssql_view_report": simulated_mssql_view_report,
        "simulated_mssql_export_report": simulated_mssql_export_report,
        "simulated_mssql_alias_compatibility_report": simulated_mssql_alias_compatibility_report,
        "operational_risk_probe_report": operational_risk_probe_report,
        "sync_lifecycle_report": sync_lifecycle_report,
        "poc_report": poc_report,
        "mall_report": {
            "ok": mall_report["ok"],
            "mall_count": mall_report["mall_count"],
            "enabled_count": mall_report["enabled_count"],
            "problem_count": len(mall_report.get("problems") or []),
        },
        "mall_config_builder_report": {
            "ok": mall_config_build_report.get("ok"),
            "source_csv": mall_config_build_report.get("source_csv"),
            "built_config": mall_config_build_report.get("built_config"),
            "checks": mall_config_build_report.get("checks"),
            "builder": {
                "mall_count": (mall_config_build_report.get("builder_report") or {}).get("mall_count"),
                "enabled_count": (mall_config_build_report.get("builder_report") or {}).get("enabled_count"),
                "generated_api_key_count": (mall_config_build_report.get("builder_report") or {}).get(
                    "generated_api_key_count"
                ),
                "problem_count": len((mall_config_build_report.get("builder_report") or {}).get("problems") or []),
            },
            "validation": mall_config_build_report.get("validation_report"),
        },
        "env_report": {
            "ok": env_report["ok"],
            "failed_checks": env_report.get("failed_checks", []),
            "configured_variable_count": env_report.get("configured_variable_count"),
        },
        "csv_index_report": csv_index_report,
        "quality_report": {
            "ok": quality_report["ok"],
            "local_only": quality_report.get("local_only"),
            "not_operational_readiness": quality_report.get("not_operational_readiness"),
            "case_contract": quality_report.get("case_contract"),
            "response_time": quality_report.get("response_time"),
        },
        "search_probe": search_probe,
        "qwen_query_vector_probe": {
            "ok": qwen_query_vector_probe.get("ok"),
            "text_requests": qwen_query_vector_probe.get("text_requests"),
            "image_requests": qwen_query_vector_probe.get("image_requests"),
            "text_embed_calls": qwen_query_vector_probe.get("text_embed_calls"),
            "image_embed_calls": qwen_query_vector_probe.get("image_embed_calls"),
            "text_coalesced": qwen_query_vector_probe.get("text_coalesced"),
            "image_coalesced": qwen_query_vector_probe.get("image_coalesced"),
            "mixed_uses_text_and_image": qwen_query_vector_probe.get("mixed_uses_text_and_image"),
            "mixed_parallel_text_and_image": qwen_query_vector_probe.get("mixed_parallel_text_and_image"),
            "invalid_qwen_embedding_response_blocked": qwen_query_vector_probe.get(
                "invalid_qwen_embedding_response_blocked"
            ),
            "invalid_qwen_precomputed_cache_blocked": qwen_query_vector_probe.get(
                "invalid_qwen_precomputed_cache_blocked"
            ),
            "cached_text_source": qwen_query_vector_probe.get("cached_text_source"),
            "cached_image_source": qwen_query_vector_probe.get("cached_image_source"),
            "qwen_query_vector_status": qwen_query_vector_probe.get("qwen_query_vector_status"),
        },
        "marqo_index_batch_probe": {
            "ok": marqo_index_batch_probe.get("ok"),
            "checks": marqo_index_batch_probe.get("checks"),
            "native_batch_sizes": marqo_index_batch_probe.get("native_batch_sizes"),
            "native_request_bytes": marqo_index_batch_probe.get("native_request_bytes"),
            "delete_batch_sizes": marqo_index_batch_probe.get("delete_batch_sizes"),
            "qwen_marqo_batch_sizes": marqo_index_batch_probe.get("qwen_marqo_batch_sizes"),
            "qwen_marqo_request_bytes": marqo_index_batch_probe.get("qwen_marqo_request_bytes"),
        },
        "response_materialization_probe": {
            "ok": response_materialization_probe.get("ok"),
            "checks": response_materialization_probe.get("checks"),
            "candidate_hits": response_materialization_probe.get("candidate_hits"),
            "materialized_count": response_materialization_probe.get("materialized_count"),
            "materialized_product_ids": response_materialization_probe.get("materialized_product_ids"),
            "top_product_ids": response_materialization_probe.get("top_product_ids"),
            "related_product_ids": response_materialization_probe.get("related_product_ids"),
            "group_probe_engine_limit": response_materialization_probe.get("group_probe_engine_limit"),
            "group_probe_engine_limits": response_materialization_probe.get("group_probe_engine_limits"),
            "group_probe_final_engine_limit": response_materialization_probe.get("group_probe_final_engine_limit"),
            "group_probe_log_diagnostics": response_materialization_probe.get("group_probe_log_diagnostics"),
            "group_probe_top_product_ids": response_materialization_probe.get("group_probe_top_product_ids"),
            "group_probe_related_product_ids": response_materialization_probe.get("group_probe_related_product_ids"),
        },
        "load_mall_identity_probe": {
            "ok": load_mall_identity_probe.get("ok"),
            "checks": load_mall_identity_probe.get("checks"),
            "identity_summary": load_mall_identity_probe.get("identity_summary"),
            "request_origin_count": load_mall_identity_probe.get("request_origin_count"),
            "request_api_key_count": load_mall_identity_probe.get("request_api_key_count"),
            "request_product_url_prefix_count": load_mall_identity_probe.get("request_product_url_prefix_count"),
        },
        "mixed_weight_sweep_report": {
            "ok": mixed_weight_sweep_report.get("ok"),
            "coverage_ok": mixed_weight_sweep_report.get("coverage_ok"),
            "current_default_close_to_best": mixed_weight_sweep_report.get("current_default_close_to_best"),
            "best_profile": mixed_weight_sweep_report.get("best_profile"),
            "current_default_profile": mixed_weight_sweep_report.get("current_default_profile"),
            "conflict_sensitivity": mixed_weight_sweep_report.get("conflict_sensitivity"),
            "recommendations": mixed_weight_sweep_report.get("recommendations"),
        },
        "search_insights_report": {
            "ok": search_insights_report.get("ok"),
            "search_events": search_insights_report.get("search_events"),
            "click_events": search_insights_report.get("click_events"),
            "attributed_click_events": search_insights_report.get("attributed_click_events"),
            "zero_result_events": search_insights_report.get("zero_result_events"),
            "low_confidence_events": search_insights_report.get("low_confidence_events"),
            "slow_search_events": search_insights_report.get("slow_search_events"),
            "latency_ms": search_insights_report.get("latency_ms"),
            "query_type_latency_ms": search_insights_report.get("query_type_latency_ms"),
            "top_clicked_product_count": len(search_insights_report.get("top_clicked_products") or []),
            "synonym_seed_candidate_count": len(search_insights_report.get("synonym_seed_candidates") or []),
            "quality_case_candidate_count": len(search_insights_report.get("quality_case_candidates") or []),
            "synonyms_seed": str(paths.simulated_search_synonyms_seed),
            "quality_cases_seed": str(paths.simulated_search_quality_cases_seed),
            "mixed_weight_recommendation": search_insights_report.get("mixed_weight_recommendation"),
        },
        "quality_cases": quality_cases,
        "representative_sites": representative_sites,
        "representative_site_pages": representative_site_pages,
        "widget_integration_probe_report": {
            "ok": widget_integration_probe_report.get("ok"),
            "page_count": widget_integration_probe_report.get("page_count"),
            "pages_with_candidates": widget_integration_probe_report.get("pages_with_candidates"),
            "pages_ready_for_data_auto_init": widget_integration_probe_report.get("pages_ready_for_data_auto_init"),
            "pages_with_blocking_risks": widget_integration_probe_report.get("pages_with_blocking_risks"),
            "pages_with_external_widget_csp_risk": widget_integration_probe_report.get("pages_with_external_widget_csp_risk"),
            "pages_with_api_connect_csp_risk": widget_integration_probe_report.get("pages_with_api_connect_csp_risk"),
            "pages_with_unsafe_widget_url_risk": widget_integration_probe_report.get("pages_with_unsafe_widget_url_risk"),
            "snippet_bundle": str(paths.simulated_widget_snippets_dir / "manifest.json"),
            "manual_install_plan": str(paths.simulated_widget_snippets_dir / "manual-install-plan.json"),
            "manual_install_plan_markdown": str(paths.simulated_widget_snippets_dir / "manual-install-plan.md"),
            "preview_validation": str(paths.simulated_widget_snippets_dir / "preview-validation.json"),
            "preview_validation_markdown": str(paths.simulated_widget_snippets_dir / "preview-validation.md"),
            "snippet_count": widget_snippet_manifest.get("snippet_count"),
            "preview_count": widget_snippet_manifest.get("preview_count"),
            "preview_validation_ok": widget_snippet_manifest.get("preview_validation_ok"),
            "preview_validation_failed_count": widget_snippet_manifest.get("preview_validation_failed_count"),
            "snippet_bundle_review_required": widget_snippet_manifest.get("review_required"),
            "snippet_bundle_not_operational_readiness": widget_snippet_manifest.get("not_operational_readiness"),
            "manual_install_ready_pages": widget_snippet_manifest.get("manual_install_ready_pages"),
            "manual_install_review_pages": widget_snippet_manifest.get("manual_install_review_pages"),
            "manual_install_csp_change_pages": widget_snippet_manifest.get("manual_install_csp_change_pages"),
        },
        "representative_sites_report": representative_sites_report,
        "api_scale_inputs": api_scale_inputs,
        "api_scale_report": {
            "ok": api_scale_report.get("ok"),
            "comparison": api_scale_report.get("comparison"),
            "problems": api_scale_report.get("problems"),
        },
        "security_report": {
            "ok": security_report.get("ok"),
            "failed_checks": security_report.get("failed_checks"),
            "enabled_mall_count": security_report.get("enabled_mall_count"),
            "nginx_client_max_body_size": security_report.get("nginx_client_max_body_size"),
            "systemd_restart_policy": security_report.get("systemd_restart_policy"),
            "sync_failure_alerting": security_report.get("sync_failure_alerting"),
        },
        "evidence_slot_report": evidence_slot_report,
        "operational_evidence_config": operational_evidence_config,
        "next_operational_actions": [
            "Replace simulated CSV with MSSQL export from dbo.v_ai_search_products.",
            "Run csv_index.py and quality_report.py with --engine marqo against the production Marqo index.",
            "Run api_smoke_test.py and load_test.py against the deployed HTTPS API, not the in-process local probe.",
            "Install real Nginx, systemd, logrotate, and sync alerting files before security_check.py.",
        ],
    }
    write_json(output_dir / "operational-simulation.json", report)
    (output_dir / "operational-simulation.md").write_text(to_markdown(report), encoding="utf-8")
    return report


def to_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks", {})
    lines = [
        "# Haeorum AI Search Operational Simulation",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Simulation marker: `{report.get('simulation_marker')}`",
        f"- Simulation only: `{report.get('simulation_only')}`",
        f"- Not operational readiness: `{report.get('not_operational_readiness')}`",
        "",
        "| Check | Passed |",
        "| --- | --- |",
    ]
    for name, ok in checks.items():
        lines.append(f"| {name} | `{ok}` |")
    mssql_view = report.get("simulated_mssql_view_report") or {}
    mssql_export = report.get("simulated_mssql_export_report") or {}
    alias_compat = report.get("simulated_mssql_alias_compatibility_report") or {}
    mssql_export_alignment = mssql_export.get("mall_config_alignment") or {}
    incremental = mssql_view.get("incremental_probe") or {}
    readonly_probe = ((mssql_view.get("permission_report") or {}).get("readonly_write_probe") or {})
    search_probe = report.get("search_probe") or {}
    api_scale = report.get("api_scale_report") or {}
    security = report.get("security_report") or {}
    evidence_slots = report.get("evidence_slot_report") or {}
    risk_probe = report.get("operational_risk_probe_report") or {}
    backend_slot_probe = risk_probe.get("backend_request_slot_saturation") or {}
    backend_slot_timeout = backend_slot_probe.get("timeout_error") or {}
    backend_slot_final_stats = backend_slot_probe.get("final_stats") or {}
    backend_slot_circuit = backend_slot_final_stats.get("circuit_breaker") or {}
    sync_lifecycle = report.get("sync_lifecycle_report") or {}
    search_insights = report.get("search_insights_report") or {}
    mixed_weight_sweep = report.get("mixed_weight_sweep_report") or {}
    widget_probe = report.get("widget_integration_probe_report") or {}
    mall_builder = report.get("mall_config_builder_report") or {}
    cache_probe = search_probe.get("cache_concurrency_probe") or {}
    search_queue_probe = search_probe.get("search_execution_queue_probe") or {}
    qwen_query_vector_probe = report.get("qwen_query_vector_probe") or search_probe.get("qwen_query_vector_probe") or {}
    marqo_filter_probe = search_probe.get("marqo_filter_pushdown_probe") or {}
    marqo_index_batch_probe = report.get("marqo_index_batch_probe") or search_probe.get("marqo_index_batch_probe") or {}
    response_materialization_probe = (
        report.get("response_materialization_probe") or search_probe.get("response_materialization_probe") or {}
    )
    load_mall_identity_probe = report.get("load_mall_identity_probe") or search_probe.get("load_mall_identity_probe") or {}
    lines.extend(
        [
            "",
            "## Simulated MSSQL Contract",
            "",
            f"- View report OK: `{mssql_view.get('ok')}`",
            f"- SQLite DB: `{mssql_view.get('database_file')}`",
            f"- Read-only write blocked: `{readonly_probe.get('blocked')}`",
            f"- Incremental updated rows: `{incremental.get('updated_rows')}`",
            f"- Single product lookup OK: `{incremental.get('single_product_lookup_ok')}`",
            f"- Export mall config alignment OK: `{mssql_export_alignment.get('ok')}`",
            f"- Export product URL mismatches: `{mssql_export_alignment.get('active_product_url_mismatch_count')}`",
            f"- Export unknown mall IDs: `{mssql_export_alignment.get('active_unknown_mall_id_count')}`",
            f"- Alias compatibility OK: `{alias_compat.get('ok')}`",
            f"- Alias variants checked: `{[variant.get('name') for variant in alias_compat.get('variants', [])]}`",
            "",
            "## Simulated Mall Config Builder",
            "",
            f"- Builder roundtrip OK: `{mall_builder.get('ok')}`",
            f"- Source CSV: `{mall_builder.get('source_csv')}`",
            f"- Built config: `{mall_builder.get('built_config')}`",
            f"- Checks: `{mall_builder.get('checks')}`",
            f"- Builder summary: `{mall_builder.get('builder')}`",
            f"- Validation summary: `{mall_builder.get('validation')}`",
            "",
            "## Operational Risk Negative Controls",
            "",
            f"- Risk probe OK: `{risk_probe.get('ok')}`",
            f"- Block checks: `{risk_probe.get('checks')}`",
            f"- View problems caught: `{risk_probe.get('view_problems')}`",
            f"- Export problems caught: `{risk_probe.get('export_problems')}`",
            f"- Mall config fields caught: `{risk_probe.get('mall_config_problem_fields')}`",
            f"- Widget probe risks caught: `{risk_probe.get('widget_probe_risks')}`",
            f"- Representative site URL risks caught: `{risk_probe.get('representative_site_risks')}`",
            f"- Backend slot saturation OK: `{backend_slot_probe.get('ok')}`",
            f"- Backend slot saturation status: `{backend_slot_timeout.get('status_code')}`",
            f"- Backend slot overflow opened connections: `{(backend_slot_probe.get('fake_connection') or {}).get('opened')}`",
            f"- Backend slot timeout count: `{backend_slot_final_stats.get('connection_acquire_wait_timeouts')}`",
            f"- Backend slot circuit state/open events: `{backend_slot_circuit.get('state')}` / `{backend_slot_circuit.get('open_events')}`",
            "",
            "## Simulated Sync Lifecycle",
            "",
            f"- Sync lifecycle OK: `{sync_lifecycle.get('ok')}`",
            f"- Checks: `{sync_lifecycle.get('checks')}`",
            f"- Initial result: `{sync_lifecycle.get('initial_result')}`",
            f"- Changed result: `{sync_lifecycle.get('changed_result')}`",
            f"- Missing-source delete result: `{sync_lifecycle.get('stale_delete_result')}`",
            f"- Busy lock result: `{sync_lifecycle.get('busy_lock_result')}`",
            f"- Stale lock recovered result: `{sync_lifecycle.get('stale_lock_recovered_result')}`",
            f"- Lock busy events: `{sync_lifecycle.get('lock_busy_event_count')}`",
            f"- Cache clear events: `{sync_lifecycle.get('cache_clear_event_count')}`",
            "",
            "## Simulated Representative Sites",
            "",
            f"- Site report OK: `{(report.get('representative_sites_report') or {}).get('ok')}`",
            f"- Site count: `{(report.get('representative_sites_report') or {}).get('site_count')}`",
            f"- Local pages: `{report.get('representative_site_pages')}`",
            "",
            "## Simulated Widget Integration Probe",
            "",
            f"- Probe OK: `{widget_probe.get('ok')}`",
            f"- Pages: `{widget_probe.get('page_count')}`",
            f"- Pages with candidates: `{widget_probe.get('pages_with_candidates')}`",
            f"- Pages ready for data auto init: `{widget_probe.get('pages_ready_for_data_auto_init')}`",
            f"- Pages with blocking risks: `{widget_probe.get('pages_with_blocking_risks')}`",
            f"- Pages with external widget CSP risk: `{widget_probe.get('pages_with_external_widget_csp_risk')}`",
            f"- Pages with API connect CSP risk: `{widget_probe.get('pages_with_api_connect_csp_risk')}`",
            f"- Pages with unsafe API/widget URL risk: `{widget_probe.get('pages_with_unsafe_widget_url_risk')}`",
            "",
            "## Simulated API Scale",
            "",
            f"- Scale report OK: `{api_scale.get('ok')}`",
            f"- Comparison: `{api_scale.get('comparison')}`",
            f"- Problems: `{api_scale.get('problems')}`",
            "",
            "## Simulated Security Templates",
            "",
            f"- Security report OK: `{security.get('ok')}`",
            f"- Failed checks: `{security.get('failed_checks')}`",
            f"- Enabled malls: `{security.get('enabled_mall_count')}`",
            f"- Sync failure alerting: `{security.get('sync_failure_alerting')}`",
            "",
            "## Simulated Evidence Slots",
            "",
            f"- Slot coverage OK: `{evidence_slots.get('ok')}`",
            f"- Slot count: `{evidence_slots.get('slot_count')}`",
            f"- Missing slots: `{evidence_slots.get('missing')}`",
            f"- Unmarked slots: `{evidence_slots.get('unmarked')}`",
            "",
            "## Local Load Probe",
            "",
            f"- Requests: `{search_probe.get('requests')}`",
            f"- Concurrency: `{search_probe.get('concurrency')}`",
            f"- Throughput RPS: `{search_probe.get('throughput_rps')}`",
            f"- p95 ms: `{search_probe.get('p95_ms')}`",
            f"- p99 ms: `{search_probe.get('p99_ms')}`",
            f"- Thresholds: `{search_probe.get('thresholds')}`",
            f"- Performance OK: `{search_probe.get('performance_ok')}`",
            f"- Coverage OK: `{search_probe.get('coverage_ok')}`",
            f"- Performance warnings: `{search_probe.get('performance_warnings')}`",
            f"- Coverage warnings: `{search_probe.get('coverage_warnings')}`",
            f"- Query type latency: `{search_probe.get('query_type_latency_ms')}`",
            f"- Slow samples: `{search_probe.get('slow_samples')}`",
            f"- Cache concurrency probe OK: `{cache_probe.get('ok')}`",
            f"- Search execution queue probe OK: `{search_queue_probe.get('ok')}`",
            f"- Search queue max concurrency: `{search_queue_probe.get('max_concurrency')}`",
            f"- Search queue acquired/rejected: `{search_queue_probe.get('acquired')}` / `{search_queue_probe.get('rejected')}`",
            f"- Search queue max in-flight observed: `{search_queue_probe.get('max_in_flight_observed')}`",
            f"- Search queue wait events/max ms: `{search_queue_probe.get('wait_events')}` / `{search_queue_probe.get('max_wait_ms')}`",
            f"- Qwen query vector probe OK: `{qwen_query_vector_probe.get('ok')}`",
            f"- Qwen text requests/embed calls: `{qwen_query_vector_probe.get('text_requests')}` / `{qwen_query_vector_probe.get('text_embed_calls')}`",
            f"- Qwen image requests/embed calls: `{qwen_query_vector_probe.get('image_requests')}` / `{qwen_query_vector_probe.get('image_embed_calls')}`",
            f"- Qwen vector coalesced text/image: `{qwen_query_vector_probe.get('text_coalesced')}` / `{qwen_query_vector_probe.get('image_coalesced')}`",
            f"- Qwen mixed parallel text/image: `{qwen_query_vector_probe.get('mixed_parallel_text_and_image')}`",
            f"- Qwen invalid vectors blocked live/cache: `{qwen_query_vector_probe.get('invalid_qwen_embedding_response_blocked')}` / `{qwen_query_vector_probe.get('invalid_qwen_precomputed_cache_blocked')}`",
            f"- Qwen vector wait status: `{qwen_query_vector_probe.get('qwen_query_vector_status')}`",
            f"- Marqo numeric filter pushdown OK: `{marqo_filter_probe.get('ok')}`",
            f"- Marqo numeric filter checks: `{marqo_filter_probe.get('checks')}`",
            f"- Marqo index batch probe OK: `{marqo_index_batch_probe.get('ok')}`",
            f"- Marqo index batch checks: `{marqo_index_batch_probe.get('checks')}`",
            f"- Response materialization probe OK: `{response_materialization_probe.get('ok')}`",
            f"- Response candidates/materialized: `{response_materialization_probe.get('candidate_hits')}` / `{response_materialization_probe.get('materialized_count')}`",
            f"- Response materialized product IDs: `{response_materialization_probe.get('materialized_product_ids')}`",
            f"- Load mall identity probe OK: `{load_mall_identity_probe.get('ok')}`",
            f"- Load mall identity checks: `{load_mall_identity_probe.get('checks')}`",
            f"- Load mall identity summary: `{load_mall_identity_probe.get('identity_summary')}`",
            f"- Repeated request engine searches: `{cache_probe.get('repeated_engine_searches')}`",
            f"- Distributed request engine searches: `{cache_probe.get('distributed_engine_searches')}`",
            f"- Distributed services: `{cache_probe.get('distributed_service_count')}`",
            f"- Distributed cache miss lock claims: `{cache_probe.get('distributed_lock_claims')}`",
            f"- Distributed cache miss lock contention: `{cache_probe.get('distributed_lock_contention_events')}`",
            f"- Backend failure coalesced: `{cache_probe.get('failure_coalesced')}`",
            f"- Backend failure engine searches: `{cache_probe.get('failure_engine_searches')}`",
            f"- Local singleflight wait timeout bounded: `{cache_probe.get('local_wait_timeout_bounded')}`",
            f"- Local singleflight timeout rejected duplicate backend call: `{cache_probe.get('local_wait_timeout_rejected')}` / `{cache_probe.get('local_wait_timeout_duplicate_backend_blocked')}`",
            f"- Local singleflight timeout status: `{cache_probe.get('local_wait_timeout_status')}`",
            f"- Image singleflight releases compute context while waiting: `{cache_probe.get('image_singleflight_context_released_during_wait')}`",
            f"- Image singleflight active contexts while waiting: `{cache_probe.get('image_singleflight_active_contexts_while_waiting')}`",
            f"- Image singleflight status: `{cache_probe.get('image_singleflight_status')}`",
            f"- Image validation singleflight coalesced: `{cache_probe.get('image_validation_singleflight_coalesced')}`",
            f"- Image validation count/engine searches: `{cache_probe.get('image_validation_singleflight_validation_count')}` / `{cache_probe.get('image_validation_singleflight_engine_searches')}`",
            f"- Image validation status: `{cache_probe.get('image_validation_singleflight_status')}`",
            f"- Image validation cache reused: `{cache_probe.get('image_validation_cache_reused')}`",
            f"- Image validation cache count/engine searches: `{cache_probe.get('image_validation_cache_validation_count')}` / `{cache_probe.get('image_validation_cache_engine_searches')}`",
            f"- Image validation cache status: `{cache_probe.get('image_validation_cache_status')}`",
            f"- Cache policy fingerprint reused: `{cache_probe.get('policy_fingerprint_reused')}`",
            f"- Cache policy base fingerprint calls: `{cache_probe.get('policy_base_fingerprint_calls')}`",
            f"- Cache policy mall fingerprint calls/tokens: `{cache_probe.get('policy_fingerprint_known_calls')}` / `{cache_probe.get('policy_fingerprint_token_count')}`",
            f"- Unknown mall policy tokens skipped: `{cache_probe.get('policy_unknown_mall_tokens_skipped')}`",
            f"- Cache probe entry cap: `{cache_probe.get('cache_max_entries')}`",
            f"- Cache probe evictions: `{cache_probe.get('evictions')}`",
            f"- High-cardinality bounded: `{cache_probe.get('high_cardinality_bounded')}`",
            "",
            "## Mixed Weight Sweep",
            "",
            f"- Sweep OK: `{mixed_weight_sweep.get('ok')}`",
            f"- Coverage OK: `{mixed_weight_sweep.get('coverage_ok')}`",
            f"- Current default close to best: `{mixed_weight_sweep.get('current_default_close_to_best')}`",
            f"- Best profile: `{mixed_weight_sweep.get('best_profile')}`",
            f"- Current default profile: `{mixed_weight_sweep.get('current_default_profile')}`",
            f"- Conflict sensitivity: `{mixed_weight_sweep.get('conflict_sensitivity')}`",
            f"- Recommendations: `{mixed_weight_sweep.get('recommendations')}`",
            "",
            "## Search Insights Quality Loop",
            "",
            f"- Search insights OK: `{search_insights.get('ok')}`",
            f"- Search events: `{search_insights.get('search_events')}`",
            f"- Low-confidence events: `{search_insights.get('low_confidence_events')}`",
            f"- Slow search events: `{search_insights.get('slow_search_events')}`",
            f"- Search insights latency: `{search_insights.get('latency_ms')}`",
            f"- Search insights query type latency: `{search_insights.get('query_type_latency_ms')}`",
            f"- Synonym candidates: `{search_insights.get('synonym_seed_candidate_count')}`",
            f"- Quality case candidates: `{search_insights.get('quality_case_candidate_count')}`",
            f"- Mixed weight recommendation: `{search_insights.get('mixed_weight_recommendation')}`",
            "",
            "## Next Operational Actions",
            "",
        ]
    )
    for action in report.get("next_operational_actions", []):
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate a simulated Haeorum operational dataset.")
    parser.add_argument("--output-dir", default=str(ROOT / "logs" / "simulation"))
    parser.add_argument("--products", type=int, default=1800)
    parser.add_argument("--malls", type=int, default=1700)
    parser.add_argument("--poc-size", type=int, default=300)
    parser.add_argument("--min-per-category", type=int, default=10)
    parser.add_argument("--load-requests", type=int, default=850)
    parser.add_argument("--load-concurrency", type=int, default=100)
    parser.add_argument("--load-p95-ms", type=int, default=5000)
    parser.add_argument("--load-p99-ms", type=int, default=8000)
    args = parser.parse_args()
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

