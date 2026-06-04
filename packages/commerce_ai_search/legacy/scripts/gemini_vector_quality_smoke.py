from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


TEXT_FIELDS = ["product_name", "category_name", "description", "keywords", "image_tags"]
DEFAULT_PROMPT = "Retrieve relevant ecommerce product images for the user query."


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Gemini text-vector smoke index and compare JCL quality cases.")
    parser.add_argument("--csv", default="logs/jclgift-products-1800-supported.csv")
    parser.add_argument("--cases", default="logs/jclgift-quality-cases-1800.json")
    parser.add_argument("--qwen-report", default="logs/jclgift-qwen-quality-1800.json")
    parser.add_argument("--embed-url", default="http://127.0.0.1:8098/embed")
    parser.add_argument("--mode", choices=["focused", "full"], default="focused")
    parser.add_argument("--focused-max-products", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--text-min-interval-seconds", type=float, default=0.0)
    parser.add_argument("--include-images", action="store_true")
    parser.add_argument("--image-products", type=int, default=60)
    parser.add_argument("--image-batch-size", type=int, default=5)
    parser.add_argument("--image-min-interval-seconds", type=float, default=5.0)
    parser.add_argument("--output", default="logs/gemini-focused-quality-smoke.json")
    parser.add_argument("--index-output", default="logs/gemini-focused-vector-index.json")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    case_path = Path(args.cases)
    products = load_products(csv_path)
    cases = json.loads(case_path.read_text(encoding="utf-8"))["cases"]
    selected_products = products if args.mode == "full" else select_focused_products(
        products,
        cases,
        max_products=max(1, int(args.focused_max_products)),
    )

    product_vectors: list[list[float]] = []
    index_latencies: list[float] = []
    for offset in range(0, len(selected_products), max(1, int(args.batch_size))):
        batch = selected_products[offset: offset + max(1, int(args.batch_size))]
        vectors, elapsed_ms, _stats = embed(
            args.embed_url,
            [{"text": product_text(row)} for row in batch],
            timeout=180,
        )
        product_vectors.extend(normalize_vector(vector) for vector in vectors)
        index_latencies.append(elapsed_ms)
        print(f"indexed {min(offset + len(batch), len(selected_products))}/{len(selected_products)} batch_ms={elapsed_ms:.1f}", flush=True)
        sleep_to_interval(elapsed_ms, float(args.text_min_interval_seconds))

    query_vectors, query_batch_ms, query_stats = embed(
        args.embed_url,
        [{"text": str(case["query"]["q"])} for case in cases],
        prompt=DEFAULT_PROMPT,
        timeout=120,
    )
    query_vectors = [normalize_vector(vector) for vector in query_vectors]

    case_results = evaluate_cases(cases, selected_products, product_vectors, query_vectors)
    image_vector_by_id: dict[str, list[float]] = {}
    image_failures: list[dict[str, str]] = []
    image_index_latencies: list[float] = []
    image_query_smoke: dict[str, Any] = {"enabled": False}
    if args.include_images:
        image_products = [
            row
            for row in selected_products
            if str(row.get("main_image_url") or "").strip()
        ][: max(1, int(args.image_products))]
        for offset in range(0, len(image_products), max(1, int(args.image_batch_size))):
            batch = image_products[offset: offset + max(1, int(args.image_batch_size))]
            try:
                vectors, elapsed_ms, _stats = embed(
                    args.embed_url,
                    [{"image": str(row.get("main_image_url") or "").strip()} for row in batch],
                    timeout=240,
                )
            except RuntimeError as exc:
                print(f"image_batch_failed fallback_to_single offset={offset} error={str(exc)[:220]}", flush=True)
                vectors = []
                elapsed_ms = 0.0
                for row in batch:
                    product_id = str(row.get("product_id") or "")
                    try:
                        one_vectors, one_elapsed_ms, _stats = embed(
                            args.embed_url,
                            [{"image": str(row.get("main_image_url") or "").strip()}],
                            timeout=240,
                        )
                    except RuntimeError as one_exc:
                        image_failures.append(
                            {
                                "product_id": product_id,
                                "main_image_url": str(row.get("main_image_url") or ""),
                                "error": str(one_exc)[:500],
                            }
                        )
                        print(f"image_failed product_id={product_id} error={str(one_exc)[:220]}", flush=True)
                        continue
                    elapsed_ms += one_elapsed_ms
                    vectors.extend(one_vectors)
                    image_vector_by_id[product_id] = normalize_vector(one_vectors[0])
                image_index_latencies.append(elapsed_ms)
                print(
                    f"indexed_images {min(offset + len(batch), len(image_products))}/{len(image_products)} "
                    f"batch_ms={elapsed_ms:.1f} failures={len(image_failures)}",
                    flush=True,
                )
                sleep_to_interval(elapsed_ms, float(args.image_min_interval_seconds))
                continue
            image_index_latencies.append(elapsed_ms)
            for row, vector in zip(batch, vectors):
                image_vector_by_id[str(row.get("product_id") or "")] = normalize_vector(vector)
            print(
                f"indexed_images {min(offset + len(batch), len(image_products))}/{len(image_products)} "
                f"batch_ms={elapsed_ms:.1f} failures={len(image_failures)}",
                flush=True,
            )
            sleep_to_interval(elapsed_ms, float(args.image_min_interval_seconds))
        image_query_smoke = run_image_query_smoke(args.embed_url, image_products[:5], image_vector_by_id, selected_products)
    ok_count = sum(1 for item in case_results if item["ok"])
    qwen_summary = summarize_qwen_report(Path(args.qwen_report))
    search_latencies = [float(item["search_ms"]) for item in case_results]
    report = {
        "ok": ok_count == len(case_results),
        "model": "gemini-embedding-2",
        "dimensions": len(product_vectors[0]) if product_vectors else 0,
        "mode": args.mode,
        "csv": str(csv_path),
        "case_source": str(case_path),
        "product_count": len(selected_products),
        "source_product_count": len(products),
        "case_count": len(case_results),
        "ok_count": ok_count,
        "fail_count": len(case_results) - ok_count,
        "qwen_baseline": qwen_summary,
        "indexing": {
            "batch_size": max(1, int(args.batch_size)),
            "batch_count": len(index_latencies),
            "total_ms": round(sum(index_latencies), 3),
            "avg_batch_ms": round(statistics.fmean(index_latencies), 3) if index_latencies else 0.0,
            "max_batch_ms": round(max(index_latencies), 3) if index_latencies else 0.0,
        },
        "query_embedding": {
            "batch_ms": round(query_batch_ms, 3),
            "avg_per_query_ms_if_batched": round(query_batch_ms / max(len(cases), 1), 3),
            "stats": query_stats,
        },
        "vector_search_cpu": {
            "avg_ms": round(statistics.fmean(search_latencies), 3) if search_latencies else 0.0,
            "max_ms": round(max(search_latencies), 3) if search_latencies else 0.0,
        },
        "image_indexing": {
            "enabled": bool(args.include_images),
            "image_product_count": len(image_vector_by_id),
            "batch_size": max(1, int(args.image_batch_size)),
            "batch_count": len(image_index_latencies),
            "total_ms": round(sum(image_index_latencies), 3) if image_index_latencies else 0.0,
            "avg_batch_ms": round(statistics.fmean(image_index_latencies), 3) if image_index_latencies else 0.0,
            "max_batch_ms": round(max(image_index_latencies), 3) if image_index_latencies else 0.0,
            "failure_count": len(image_failures),
            "failures": image_failures[:50],
        },
        "image_query_smoke": image_query_smoke,
        "cases": case_results,
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_index(Path(args.index_output), selected_products, product_vectors, image_vector_by_id, report)
    print(json.dumps(summary_for_console(report), ensure_ascii=False, indent=2))
    print("report=" + str(args.output))
    print("index=" + str(args.index_output))
    return 0


def load_products(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if str(row.get("status") or "").strip().lower() == "active"
        ]


def select_focused_products(products: list[dict[str, str]], cases: list[dict[str, Any]], *, max_products: int) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(row: dict[str, str]) -> None:
        product_id = str(row.get("product_id") or "").strip()
        if product_id and product_id not in seen:
            seen.add(product_id)
            selected.append(row)

    for case in cases:
        expected = str(case.get("expected_category") or "").strip()
        query = str((case.get("query") or {}).get("q") or "").strip()
        if expected:
            for row in [item for item in products if expected in str(item.get("category_name") or "")][:18]:
                add(row)
        for row in [item for item in products if query and query in product_text(item)][:10]:
            add(row)
    random.seed(7)
    remaining = [row for row in products if str(row.get("product_id") or "") not in seen]
    random.shuffle(remaining)
    for row in remaining:
        if len(selected) >= max_products:
            break
        add(row)
    return selected[:max_products]


def product_text(row: dict[str, str]) -> str:
    parts = []
    for field in TEXT_FIELDS:
        value = str(row.get(field) or "").strip()
        if value:
            parts.append(value)
    return " | ".join(parts)[:3000]


def embed(
    url: str,
    inputs: list[dict[str, str]],
    *,
    prompt: str | None = None,
    timeout: int = 120,
) -> tuple[list[list[float]], float, dict[str, Any]]:
    payload: dict[str, Any] = {"inputs": inputs}
    if prompt:
        payload["prompt"] = prompt
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(20):
        started = time.perf_counter()
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.load(response)
            return data["embeddings"], (time.perf_counter() - started) * 1000, data.get("stats") or {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if "RESOURCE_EXHAUSTED" in detail or '"code": 429' in detail:
                sleep_seconds = retry_seconds(detail)
                print(f"rate_limited sleep={sleep_seconds:.1f}s attempt={attempt + 1}", flush=True)
                time.sleep(sleep_seconds)
                continue
            raise RuntimeError(detail[:2000]) from exc
    raise RuntimeError("Gemini embedding retry exhausted")


def retry_seconds(text: str) -> float:
    match = re.search(r'"retryDelay"\s*:\s*"([0-9.]+)s"', text, re.IGNORECASE)
    if match:
        return float(match.group(1)) + 2.0
    match = re.search(r"retry in ([0-9.]+)s", text, re.IGNORECASE)
    if match:
        return float(match.group(1)) + 1.5
    return 20.0


def sleep_to_interval(elapsed_ms: float, min_interval_seconds: float) -> None:
    if min_interval_seconds <= 0:
        return
    remaining = min_interval_seconds - max(0.0, elapsed_ms / 1000)
    if remaining > 0:
        time.sleep(remaining)


def normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if not norm:
        return [float(value) for value in vector]
    return [float(value) / norm for value in vector]


def score(query_vector: list[float], product_vector: list[float]) -> float:
    return sum(a * b for a, b in zip(query_vector, product_vector))


def evaluate_cases(
    cases: list[dict[str, Any]],
    products: list[dict[str, str]],
    product_vectors: list[list[float]],
    query_vectors: list[list[float]],
) -> list[dict[str, Any]]:
    results = []
    for case, query_vector in zip(cases, query_vectors):
        started = time.perf_counter()
        scored = sorted(
            ((score(query_vector, product_vector), product) for product, product_vector in zip(products, product_vectors)),
            key=lambda item: item[0],
            reverse=True,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        top = scored[:5]
        top3 = top[:3]
        expected = str(case.get("expected_category") or "").strip()
        category_hit = True if not expected else any(expected in str(row.get("category_name") or "") for _, row in top3)
        min_results_ok = len(top) >= int(case.get("expected_min_results") or 1)
        results.append(
            {
                "name": case.get("name"),
                "query": str((case.get("query") or {}).get("q") or ""),
                "expected_category": expected or None,
                "ok": bool(category_hit and min_results_ok),
                "category_hit_top3": bool(category_hit),
                "search_ms": round(elapsed_ms, 3),
                "top_ids": [row.get("product_id") for _, row in top3],
                "top_categories": [row.get("category_name") for _, row in top3],
                "top_names": [row.get("product_name") for _, row in top3],
                "top_scores": [round(value, 6) for value, _ in top3],
            }
        )
    return results


def run_image_query_smoke(
    embed_url: str,
    image_products: list[dict[str, str]],
    image_vector_by_id: dict[str, list[float]],
    products: list[dict[str, str]],
) -> dict[str, Any]:
    if not image_products or not image_vector_by_id:
        return {"enabled": True, "case_count": 0, "top1_exact_count": 0, "cases": []}
    vectors, elapsed_ms, stats = embed(
        embed_url,
        [{"image": str(row.get("main_image_url") or "").strip()} for row in image_products],
        prompt=DEFAULT_PROMPT,
        timeout=240,
    )
    cases = []
    searchable = [
        (product, image_vector_by_id[str(product.get("product_id") or "")])
        for product in products
        if str(product.get("product_id") or "") in image_vector_by_id
    ]
    for row, vector in zip(image_products, vectors):
        query_vector = normalize_vector(vector)
        scored = sorted(
            ((score(query_vector, image_vector), product) for product, image_vector in searchable),
            key=lambda item: item[0],
            reverse=True,
        )[:3]
        top_ids = [product.get("product_id") for _, product in scored]
        expected_id = row.get("product_id")
        cases.append(
            {
                "product_id": expected_id,
                "product_name": row.get("product_name"),
                "category_name": row.get("category_name"),
                "top1_exact": bool(top_ids and top_ids[0] == expected_id),
                "top_ids": top_ids,
                "top_categories": [product.get("category_name") for _, product in scored],
                "top_scores": [round(float(value), 6) for value, _ in scored],
            }
        )
    return {
        "enabled": True,
        "case_count": len(cases),
        "top1_exact_count": sum(1 for item in cases if item["top1_exact"]),
        "query_embedding_ms": round(elapsed_ms, 3),
        "stats": stats,
        "cases": cases,
    }


def summarize_qwen_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    case_results = data.get("case_results") or data.get("cases") or []
    if not isinstance(case_results, list):
        case_results = []
    ok_count = sum(1 for item in case_results if isinstance(item, dict) and item.get("ok") is True)
    return {
        "available": True,
        "path": str(path),
        "overall_ok": data.get("ok"),
        "quality_ok": data.get("quality_ok"),
        "response_time_ok": data.get("response_time_ok"),
        "product_count": ((data.get("source") or {}).get("product_id_count") if isinstance(data.get("source"), dict) else None),
        "case_count": len(case_results) or data.get("case_count"),
        "ok_count": ok_count or None,
        "text_avg_ms": ((data.get("response_time") or {}).get("text") or {}).get("avg_ms") if isinstance(data.get("response_time"), dict) else None,
    }


def write_index(
    path: Path,
    products: list[dict[str, str]],
    vectors: list[list[float]],
    image_vector_by_id: dict[str, list[float]],
    report: dict[str, Any],
) -> None:
    items = []
    for product, vector in zip(products, vectors):
        product_id = str(product.get("product_id") or "")
        item: dict[str, Any] = {
            "product": {key: product.get(key) for key in [
                "product_id",
                "product_name",
                "category_name",
                "price",
                "main_image_url",
                "product_url",
                "mall_id",
            ]},
            "vector": vector,
            "text_vector": vector,
            "search_text": product_text(product),
        }
        image_vector = image_vector_by_id.get(product_id)
        if image_vector is not None:
            item["image_vector"] = image_vector
        items.append(item)
    path.write_text(
        json.dumps(
            {
                "model": report["model"],
                "dimensions": report["dimensions"],
                "product_count": len(items),
                "items": items,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def summary_for_console(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": report["ok"],
        "mode": report["mode"],
        "product_count": report["product_count"],
        "source_product_count": report["source_product_count"],
        "case_count": report["case_count"],
        "ok_count": report["ok_count"],
        "fail_count": report["fail_count"],
        "indexing": report["indexing"],
        "query_embedding": report["query_embedding"],
        "vector_search_cpu": report["vector_search_cpu"],
        "image_indexing": report["image_indexing"],
        "image_query_smoke": {
            "enabled": report["image_query_smoke"].get("enabled"),
            "case_count": report["image_query_smoke"].get("case_count"),
            "top1_exact_count": report["image_query_smoke"].get("top1_exact_count"),
            "query_embedding_ms": report["image_query_smoke"].get("query_embedding_ms"),
        },
        "qwen_baseline": report["qwen_baseline"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
