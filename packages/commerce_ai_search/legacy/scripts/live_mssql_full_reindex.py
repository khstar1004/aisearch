from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.cache import make_search_cache
from app.config import DEFAULT_MSSQL_QUERY, load_settings
from app.engine_factory import create_search_engine
from app.sync import build_wrapped_mssql_query, row_to_product


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def fetch_json(url: str, timeout: float = 10.0) -> dict[str, Any] | None:
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def read_products(connection: Any, query: str, fetch_size: int):
    cursor = connection.cursor()
    cursor.execute(query)
    columns = [column[0] for column in cursor.description]
    while True:
        rows = cursor.fetchmany(fetch_size)
        if not rows:
            break
        products = []
        for row in rows:
            products.append(sanitize_product_for_gemini_image(row_to_product(dict(zip(columns, row)))))
        yield products


def sanitize_product_for_gemini_image(product: Any) -> Any:
    image_url = str(product.image_url or "").strip()
    if not image_url:
        return product
    path = urlparse(image_url).path.lower()
    if any(path.endswith(extension) for extension in SUPPORTED_IMAGE_EXTENSIONS):
        return product
    product.extra["unsupported_image_url"] = image_url
    product.image_url = None
    return product


def main() -> int:
    parser = argparse.ArgumentParser(description="Full reindex live Haeorum MSSQL products with progress output.")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--fetch-size", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--batch-retries", type=int, default=8)
    parser.add_argument("--batch-retry-delay-seconds", type=float, default=10.0)
    parser.add_argument("--output", default="")
    parser.add_argument("--progress-every", type=int, default=1)
    args = parser.parse_args()

    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError("pyodbc is required for live MSSQL reindex") from exc

    settings = load_settings()
    if not settings.mssql_connection_string:
        raise RuntimeError("HAEORUM_MSSQL_READONLY_CONNECTION_STRING is required")

    engine = create_search_engine(settings, preload_local_products=False)
    search_cache = make_search_cache(settings)
    active_query = build_wrapped_mssql_query(
        settings.mssql_query or DEFAULT_MSSQL_QUERY,
        filters=["display_yn = 'Y'"],
        top=max(0, int(args.limit)),
        order_by="updated_at DESC",
    )

    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    indexed = 0
    failed = 0
    batches = 0
    max_batch_size = 0
    failures: list[dict[str, Any]] = []
    unsupported_image_urls = 0
    fallback_stats = {"image_fallback_batches": 0, "image_fallback_products": 0}

    print(
        json.dumps(
            {
                "event": "live_mssql_full_reindex_started",
                "started_at": started_at,
                "index": settings.index_name,
                "batch_size": args.batch_size,
                "fetch_size": args.fetch_size,
                "limit": args.limit,
                "workers": args.workers,
                "batch_retries": args.batch_retries,
                "batch_retry_delay_seconds": args.batch_retry_delay_seconds,
                "marqo_url": settings.marqo_url,
                "gemini_url": settings.qwen_embedding_url,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    if hasattr(engine, "ensure_index"):
        engine.ensure_index()

    with pyodbc.connect(settings.mssql_connection_string, readonly=True, autocommit=True) as connection:
        if args.workers <= 1:
            buffer = []
            for products in read_products(connection, active_query, max(1, args.fetch_size)):
                for product in products:
                    if product.extra.get("unsupported_image_url"):
                        unsupported_image_urls += 1
                    buffer.append(product)
                    if len(buffer) >= args.batch_size:
                        batch_report = upsert_batch_once(
                            engine,
                            buffer,
                            max_retries=args.batch_retries,
                            retry_delay_seconds=args.batch_retry_delay_seconds,
                        )
                        batches, indexed, failed = apply_batch_report(
                            batch_report,
                            batches,
                            indexed,
                            failed,
                            failures,
                            fallback_stats,
                            started,
                            args.progress_every,
                            settings.qwen_embedding_url,
                        )
                        max_batch_size = max(max_batch_size, len(buffer))
                        buffer = []
            if buffer:
                batch_report = upsert_batch_once(
                    engine,
                    buffer,
                    max_retries=args.batch_retries,
                    retry_delay_seconds=args.batch_retry_delay_seconds,
                )
                batches, indexed, failed = apply_batch_report(
                    batch_report,
                    batches,
                    indexed,
                    failed,
                    failures,
                    fallback_stats,
                    started,
                    args.progress_every,
                    settings.qwen_embedding_url,
                    force_progress=True,
                )
                max_batch_size = max(max_batch_size, len(buffer))
        else:
            pending: set[Future] = set()
            max_pending = max(1, args.workers * 2)

            def submit_batch(executor: ThreadPoolExecutor, batch: list[Any]) -> None:
                nonlocal max_batch_size
                max_batch_size = max(max_batch_size, len(batch))
                pending.add(
                    executor.submit(
                        upsert_batch_once,
                        engine,
                        list(batch),
                        max_retries=args.batch_retries,
                        retry_delay_seconds=args.batch_retry_delay_seconds,
                    )
                )

            def collect_completed(force: bool = False) -> None:
                nonlocal batches, indexed, failed
                if not pending:
                    return
                done, remaining = wait(
                    pending,
                    timeout=None if force else 0,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    return
                pending.clear()
                pending.update(remaining)
                for future in done:
                    batch_report = future.result()
                    batches, indexed, failed = apply_batch_report(
                        batch_report,
                        batches,
                        indexed,
                        failed,
                        failures,
                        fallback_stats,
                        started,
                        args.progress_every,
                        settings.qwen_embedding_url,
                    )

            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
                buffer = []
                for products in read_products(connection, active_query, max(1, args.fetch_size)):
                    for product in products:
                        if product.extra.get("unsupported_image_url"):
                            unsupported_image_urls += 1
                        buffer.append(product)
                        if len(buffer) >= args.batch_size:
                            submit_batch(executor, buffer)
                            buffer = []
                            while len(pending) >= max_pending:
                                collect_completed(force=True)
                if buffer:
                    submit_batch(executor, buffer)
                while pending:
                    collect_completed(force=True)

    cache_report = search_cache.clear() if search_cache else {}
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    result = {
        "event": "live_mssql_full_reindex_finished",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms,
        "index": settings.index_name,
        "indexed": indexed,
        "failed": failed,
        "batches": batches,
        "max_batch_size": max_batch_size,
        "failures": failures[:20],
        "failures_truncated": len(failures) > 20,
        "unsupported_image_urls": unsupported_image_urls,
        **fallback_stats,
        "cache_clear": cache_report,
        "gemini_health": fetch_json(settings.qwen_embedding_url.rstrip("/") + "/health"),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


def upsert_batch_once(
    engine: Any,
    batch: list[Any],
    *,
    max_retries: int,
    retry_delay_seconds: float,
) -> dict[str, Any]:
    fallback_batches = 0
    fallback_products = 0
    last_error = ""
    for attempt in range(max(0, max_retries) + 1):
        try:
            result = engine.upsert_products(batch)
            break
        except Exception as exc:
            last_error = str(exc)
            if (
                fallback_batches == 0
                and "Gemini embedding request failed" in last_error
                and any(product.image_url for product in batch)
            ):
                fallback_batches = 1
                for product in batch:
                    if product.image_url:
                        product.extra["image_embedding_fallback_error"] = last_error
                        product.image_url = None
                        fallback_products += 1
            if attempt >= max(0, max_retries) or not is_retriable_embedding_error(last_error):
                raise
            sleep_seconds = min(90.0, max(1.0, retry_delay_seconds) * (attempt + 1))
            time.sleep(sleep_seconds)
    else:
        raise RuntimeError(last_error or "upsert batch failed")
    return {
        "batch_size": len(batch),
        "result": result,
        "fallback_batches": fallback_batches,
        "fallback_products": fallback_products,
    }


def is_retriable_embedding_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "backendcircuitopenerror",
            "circuit breaker is open",
            "gemini embedding request failed",
            "timed out",
            "timeout",
            "too many requests",
            "429",
            "502",
            "503",
            "504",
        )
    )


def apply_batch_report(
    batch_report: dict[str, Any],
    batches: int,
    indexed: int,
    failed: int,
    failures: list[dict[str, Any]],
    fallback_stats: dict[str, int],
    started: float,
    progress_every: int,
    gemini_url: str,
    force_progress: bool = False,
) -> tuple[int, int, int]:
    result = batch_report.get("result") or {}
    batch_size = int(batch_report.get("batch_size") or 0)
    fallback_stats["image_fallback_batches"] = (
        fallback_stats.get("image_fallback_batches", 0) + int(batch_report.get("fallback_batches") or 0)
    )
    fallback_stats["image_fallback_products"] = (
        fallback_stats.get("image_fallback_products", 0) + int(batch_report.get("fallback_products") or 0)
    )
    batches += 1
    indexed += int(result.get("indexed", 0) or 0)
    batch_failed = max(int(result.get("failed", 0) or 0), len(result.get("failed_products") or []))
    failed += batch_failed
    for item in result.get("failed_products") or []:
        if isinstance(item, dict) and len(failures) < 100:
            failures.append(item)
    if force_progress or batches % max(1, progress_every) == 0:
        gemini = fetch_json(gemini_url.rstrip("/") + "/health")
        usage = (gemini or {}).get("usage") if isinstance(gemini, dict) else None
        print(
            json.dumps(
                {
                    "event": "live_mssql_full_reindex_progress",
                    "batch": batches,
                    "batch_size": batch_size,
                    "indexed": indexed,
                    "failed": failed,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "gemini_provider_call_total": (usage or {}).get("provider_call_total"),
                    "gemini_input_total": (usage or {}).get("input_total"),
                    "gemini_text_input_total": (usage or {}).get("text_input_total"),
                    "gemini_image_input_total": (usage or {}).get("image_input_total"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return batches, indexed, failed


if __name__ == "__main__":
    raise SystemExit(main())
