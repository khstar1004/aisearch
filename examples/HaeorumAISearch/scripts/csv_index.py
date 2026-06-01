from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT))

from app.config import DEPLOYABLE_SEARCH_ENGINES, Settings, load_settings, validate_marqo_url_value
from app.engine_factory import create_search_engine
from app.identifiers import product_identity_key, product_identity_label
from app.models import ProductDocument
from app.sync import CsvProductSource, SyncService
from app.url_safety import (
    product_url_contains_product_id,
    safe_absolute_http_url,
    safe_absolute_http_url_uses_https,
    safe_product_source_url,
)
from scripts.sample_data_guard import builtin_sample_dataset_profile, file_fingerprint, is_local_sample_evidence


def empty_product_summary() -> dict[str, Any]:
    return {
        "total_products": 0,
        "active_products": 0,
        "inactive_products": 0,
        "duplicate_product_ids": [],
        "missing_active_image_count": 0,
        "missing_active_image_product_ids": [],
        "active_unsafe_image_url_count": 0,
        "active_unsafe_image_url_product_ids": [],
        "active_non_https_image_url_count": 0,
        "active_non_https_image_url_product_ids": [],
        "missing_active_product_url_count": 0,
        "missing_active_product_url_product_ids": [],
        "missing_active_mall_id_count": 0,
        "missing_active_mall_id_product_ids": [],
        "active_unsafe_product_url_count": 0,
        "active_unsafe_product_url_product_ids": [],
        "active_product_url_product_id_mismatch_count": 0,
        "active_product_url_product_id_mismatch_product_ids": [],
        "category_count": 0,
        "top_categories": [],
    }


def summarize_products(products: list[ProductDocument]) -> dict[str, Any]:
    product_keys = [product_identity_key(product.mall_id, product.product_id) for product in products]
    duplicate_ids = sorted(product_identity_label(*key) for key, count in Counter(product_keys).items() if count > 1)
    active = [product for product in products if product.active]
    inactive = [product for product in products if not product.active]
    category_counts = Counter(product.category for product in active if product.category)
    missing_image_ids = sorted(product.product_id for product in active if not product.image_url)
    unsafe_image_url_ids = sorted(
        product.product_id
        for product in active
        if product.image_url and safe_absolute_http_url(product.image_url) is None
    )
    non_https_image_url_ids = sorted(
        product.product_id
        for product in active
        if product.image_url
        and safe_absolute_http_url(product.image_url) is not None
        and not safe_absolute_http_url_uses_https(product.image_url)
    )
    missing_product_url_ids = sorted(product.product_id for product in active if not product.product_url)
    missing_mall_id_ids = sorted(product.product_id for product in active if not product.mall_id)
    unsafe_product_url_ids = sorted(
        product.product_id
        for product in active
        if product.product_url and safe_product_source_url(product.product_url) is None
    )
    product_url_product_id_mismatch_ids = sorted(
        product.product_id
        for product in active
        if product.product_url
        and safe_product_source_url(product.product_url) is not None
        and not product_url_contains_product_id(product.product_url, product.product_id)
    )
    return {
        "total_products": len(products),
        "active_products": len(active),
        "inactive_products": len(inactive),
        "duplicate_product_ids": duplicate_ids,
        "missing_active_image_count": len(missing_image_ids),
        "missing_active_image_product_ids": missing_image_ids[:20],
        "active_unsafe_image_url_count": len(unsafe_image_url_ids),
        "active_unsafe_image_url_product_ids": unsafe_image_url_ids[:20],
        "active_non_https_image_url_count": len(non_https_image_url_ids),
        "active_non_https_image_url_product_ids": non_https_image_url_ids[:20],
        "missing_active_product_url_count": len(missing_product_url_ids),
        "missing_active_product_url_product_ids": missing_product_url_ids[:20],
        "missing_active_mall_id_count": len(missing_mall_id_ids),
        "missing_active_mall_id_product_ids": missing_mall_id_ids[:20],
        "active_unsafe_product_url_count": len(unsafe_product_url_ids),
        "active_unsafe_product_url_product_ids": unsafe_product_url_ids[:20],
        "active_product_url_product_id_mismatch_count": len(product_url_product_id_mismatch_ids),
        "active_product_url_product_id_mismatch_product_ids": product_url_product_id_mismatch_ids[:20],
        "category_count": len(category_counts),
        "top_categories": [
            {"category": category, "count": count}
            for category, count in category_counts.most_common(10)
        ],
    }


def settings_from_args(args: argparse.Namespace, base_settings: Settings | None = None) -> Settings:
    settings = base_settings or load_settings()
    updates: dict[str, Any] = {
        "product_csv_path": Path(args.csv),
        "validate_product_images": bool(args.validate_images),
    }
    if args.engine:
        updates["engine_backend"] = args.engine
    if args.index_name:
        updates["index_name"] = args.index_name
    if args.marqo_url:
        updates["marqo_url"] = validate_marqo_url_value(args.marqo_url, "--marqo-url")
    if args.marqo_model:
        updates["marqo_model"] = args.marqo_model
    if args.sync_log:
        updates["sync_log_path"] = Path(args.sync_log)
    return replace(settings, **updates)


def parse_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def index_document_count_from_health(health: dict[str, Any]) -> int | None:
    if not isinstance(health, dict):
        return None
    if "products" in health:
        return parse_int(health.get("products"))
    stats = health.get("stats") if isinstance(health.get("stats"), dict) else {}
    stats_data = stats.get("data") if isinstance(stats.get("data"), dict) else stats
    return parse_int(stats_data.get("numberOfDocuments", stats_data.get("number_of_documents")))


def run(args: argparse.Namespace, base_settings: Settings | None = None) -> dict[str, Any]:
    try:
        settings = settings_from_args(args, base_settings=base_settings)
    except ValueError as exc:
        engine_name = args.engine or (base_settings.engine_backend if base_settings is not None else "")
        return {
            "ok": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": bool(args.dry_run),
            "mode": args.mode,
            "csv": str(Path(args.csv)),
            "csv_fingerprint": file_fingerprint(args.csv),
            "engine": engine_name,
            "index": args.index_name or (base_settings.index_name if base_settings is not None else ""),
            "persistent_index": bool(engine_name and engine_name != "local"),
            "validate_images": bool(args.validate_images),
            "summary": empty_product_summary(),
            "indexed": 0,
            "deleted": 0,
            "failed": 1,
            "error": str(exc),
        }
    source = CsvProductSource(settings.product_csv_path)
    report: dict[str, Any] = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(args.dry_run),
        "mode": args.mode,
        "csv": str(settings.product_csv_path),
        "csv_fingerprint": file_fingerprint(settings.product_csv_path),
        "engine": settings.engine_backend,
        "index": settings.index_name,
        "persistent_index": settings.engine_backend != "local",
        "validate_images": settings.validate_product_images,
    }
    if settings.engine_backend == "marqo":
        report.update(
            {
                "marqo_url": settings.marqo_url,
                "marqo_model": settings.marqo_model,
            }
        )
    try:
        products = source.fetch_all()
    except Exception as exc:
        report.update(
            {
                "ok": False,
                "summary": empty_product_summary(),
                "indexed": 0,
                "deleted": 0,
                "failed": 1,
                "error": str(exc),
            }
        )
        return report
    summary = summarize_products(products)
    source_profile = builtin_sample_dataset_profile(settings.product_csv_path, (product.product_id for product in products))
    report["summary"] = summary
    report["local_only"] = is_local_sample_evidence(source_profile)
    report["not_operational_readiness"] = report["local_only"]
    report["source"] = source_profile
    if args.dry_run:
        report.update(
            {
                "indexed": 0,
                "deleted": 0,
                "failed": 0,
                "would_index": summary["active_products"],
                "would_delete": summary["inactive_products"],
            }
        )
        return report

    engine = create_search_engine(settings, preload_local_products=False)
    service = SyncService(engine, source, settings)
    if args.mode == "sync":
        result = service.sync_changed(args.since)
    else:
        result = service.reindex_all()
    post_index_document_count: int | None = None
    post_index_document_count_ok: bool | None = None
    post_index_document_count_error = ""
    expected_index_document_count = summary["active_products"] if args.mode == "reindex" else None
    if args.mode == "reindex":
        try:
            post_index_document_count = index_document_count_from_health(engine.health())
        except Exception as exc:
            post_index_document_count_error = str(exc)
        post_index_document_count_ok = (
            post_index_document_count is not None
            and expected_index_document_count is not None
            and post_index_document_count == expected_index_document_count
        )
    report.update(
        {
            "ok": result.failed == 0
            and (post_index_document_count_ok is not False),
            "indexed": result.indexed,
            "deleted": result.deleted,
            "failed": result.failed,
            "elapsed_ms": result.elapsed_ms,
            "status": result.status.model_dump(mode="json"),
            "indexing": getattr(engine, "last_upsert_stats", {}) or {},
        }
    )
    if args.mode == "reindex":
        report.update(
            {
                "expected_index_document_count": expected_index_document_count,
                "post_index_document_count": post_index_document_count,
                "post_index_document_count_ok": post_index_document_count_ok,
            }
        )
        if post_index_document_count_error:
            report["post_index_document_count_error"] = post_index_document_count_error
        if post_index_document_count_ok is not True:
            report["post_index_document_count_problem"] = (
                "post-index document count did not match active CSV products"
            )
    return report


def to_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Haeorum AI Search CSV Index Report",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Dry run: `{report.get('dry_run')}`",
        f"- Mode: `{report.get('mode')}`",
        f"- CSV: `{report.get('csv')}`",
        f"- Engine: `{report.get('engine')}`",
        f"- Index: `{report.get('index')}`",
        f"- Marqo URL: `{report.get('marqo_url')}`",
        f"- Marqo model: `{report.get('marqo_model')}`",
        f"- Persistent index: `{report.get('persistent_index')}`",
        f"- Validate images: `{report.get('validate_images')}`",
        f"- Local only: `{report.get('local_only')}`",
        f"- Not operational readiness: `{report.get('not_operational_readiness')}`",
        f"- Total products: `{summary.get('total_products')}`",
        f"- Active products: `{summary.get('active_products')}`",
        f"- Inactive products: `{summary.get('inactive_products')}`",
        f"- Missing active images: `{summary.get('missing_active_image_count')}`",
        f"- Active unsafe image URLs: `{summary.get('active_unsafe_image_url_count')}`",
        f"- Active non-HTTPS image URLs: `{summary.get('active_non_https_image_url_count')}`",
        f"- Missing active product URLs: `{summary.get('missing_active_product_url_count')}`",
        f"- Active unsafe product URLs: `{summary.get('active_unsafe_product_url_count')}`",
        f"- Active product URL/product ID mismatches: `{summary.get('active_product_url_product_id_mismatch_count')}`",
        f"- Duplicate product IDs: `{', '.join(summary.get('duplicate_product_ids') or [])}`",
        f"- Indexed: `{report.get('indexed')}`",
        f"- Deleted: `{report.get('deleted')}`",
        f"- Failed: `{report.get('failed')}`",
        f"- Index batches: `{(report.get('indexing') or {}).get('batch_count')}`",
        f"- Max index batch size: `{(report.get('indexing') or {}).get('max_batch_size')}`",
        f"- Max index request body bytes: `{(report.get('indexing') or {}).get('max_request_body_bytes')}`",
        f"- Expected index document count: `{report.get('expected_index_document_count')}`",
        f"- Post-index document count: `{report.get('post_index_document_count')}`",
        f"- Post-index document count OK: `{report.get('post_index_document_count_ok')}`",
    ]
    top_categories = summary.get("top_categories") or []
    if top_categories:
        lines.extend(["", "| Category | Count |", "| --- | --- |"])
        for item in top_categories:
            lines.append(f"| {item.get('category')} | `{item.get('count')}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Index a Haeorum AI Search PoC product CSV.")
    parser.add_argument("--csv", default=str(ROOT / "sample_products.csv"), help="Product CSV to parse and index.")
    parser.add_argument("--mode", choices=["reindex", "sync"], default="reindex")
    parser.add_argument("--since", default=None, help="Optional ISO-8601 updated_at lower bound for sync mode.")
    parser.add_argument("--engine", choices=sorted(DEPLOYABLE_SEARCH_ENGINES), default="")
    parser.add_argument("--index-name", default="")
    parser.add_argument("--marqo-url", default="")
    parser.add_argument("--marqo-model", default="")
    parser.add_argument("--sync-log", default="")
    parser.add_argument("--validate-images", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Parse and summarize the CSV without touching the index.")
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
