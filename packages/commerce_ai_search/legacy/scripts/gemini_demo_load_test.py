from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


DEFAULT_QUERIES = [
    "검은 우산",
    "노란색 우산",
    "파란색 우산",
    "텀블러",
    "볼펜",
    "타올",
    "가방",
    "상패",
    "골프공",
    "구급함",
    "소금 선물세트",
    "티셔츠",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Load test the local Gemini vector demo API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--mode", choices=["text", "image", "mixed"], default="text")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=500)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--unique-queries", action="store_true")
    parser.add_argument("--prewarm", action="store_true")
    parser.add_argument("--image", default="")
    parser.add_argument("--output", default="logs/gemini-load.json")
    parser.add_argument("--markdown", default="logs/gemini-load.md")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    image = args.image.strip() or sample_image(base_url)
    if args.prewarm:
        prewarm(base_url, image=image, limit=args.limit, timeout=args.timeout)

    jobs = [
        build_job(
            index,
            base_url=base_url,
            mode=args.mode,
            limit=args.limit,
            unique_queries=bool(args.unique_queries),
            image=image,
        )
        for index in range(max(1, int(args.requests)))
    ]
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as pool:
        futures = [pool.submit(run_job, job, args.timeout) for job in jobs]
        for future in as_completed(futures):
            results.append(future.result())
    wall_ms = (time.perf_counter() - started) * 1000
    report = summarize(
        results,
        wall_ms=wall_ms,
        base_url=base_url,
        mode=args.mode,
        requests=len(jobs),
        concurrency=max(1, int(args.concurrency)),
        prewarm=bool(args.prewarm),
        unique_queries=bool(args.unique_queries),
    )
    output = Path(args.output)
    markdown = Path(args.markdown)
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(console_summary(report), ensure_ascii=False, indent=2))
    print("report=" + str(output))
    print("markdown=" + str(markdown))
    return 0 if report["error_rate"] <= 0.01 else 1


def sample_image(base_url: str) -> str:
    with urllib.request.urlopen(base_url + "/api/sample-image", timeout=30) as response:
        data = json.load(response)
    image = str(data.get("main_image_url") or "").strip()
    if not image:
        raise RuntimeError("sample image is not available")
    return image


def prewarm(base_url: str, *, image: str, limit: int, timeout: float) -> None:
    for query in DEFAULT_QUERIES:
        run_job(
            {
                "method": "GET",
                "url": search_url(base_url, query, limit),
                "body": None,
                "query_type": "text",
                "signature": query,
            },
            timeout,
        )
    if image:
        run_job(
            {
                "method": "POST",
                "url": base_url + "/api/search-image?limit=" + str(limit),
                "body": json.dumps({"image": image}, ensure_ascii=False).encode("utf-8"),
                "query_type": "image",
                "signature": image,
            },
            timeout,
        )


def build_job(
    index: int,
    *,
    base_url: str,
    mode: str,
    limit: int,
    unique_queries: bool,
    image: str,
) -> dict[str, Any]:
    if mode == "mixed":
        query_type = "image" if index % 5 == 0 else "text"
    else:
        query_type = mode
    if query_type == "image":
        return {
            "method": "POST",
            "url": base_url + "/api/search-image?limit=" + str(limit),
            "body": json.dumps({"image": image}, ensure_ascii=False).encode("utf-8"),
            "query_type": "image",
            "signature": image,
        }
    query = DEFAULT_QUERIES[index % len(DEFAULT_QUERIES)]
    if unique_queries:
        query = f"{query} #{index:04d}"
    return {
        "method": "GET",
        "url": search_url(base_url, query, limit),
        "body": None,
        "query_type": "text",
        "signature": query,
    }


def search_url(base_url: str, query: str, limit: int) -> str:
    params = urllib.parse.urlencode({"q": query, "limit": str(limit)})
    return base_url + "/api/search?" + params


def run_job(job: dict[str, Any], timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    request = urllib.request.Request(
        str(job["url"]),
        data=job.get("body"),
        headers={"Content-Type": "application/json"},
        method=str(job["method"]),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
        elapsed_ms = (time.perf_counter() - started) * 1000
        payload = json.loads(raw.decode("utf-8"))
        return {
            "ok": 200 <= status < 300,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "query_type": job["query_type"],
            "signature": job["signature"],
            "server_total_ms": payload.get("total_ms"),
            "server_embed_ms": payload.get("embed_ms"),
            "cache_hit": payload.get("cache_hit"),
            "count": payload.get("count"),
            "error": None,
        }
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return error_result(job, elapsed_ms, int(exc.code), detail)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return error_result(job, elapsed_ms, 0, repr(exc)[:500])


def error_result(job: dict[str, Any], elapsed_ms: float, status: int, detail: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "query_type": job["query_type"],
        "signature": job["signature"],
        "server_total_ms": None,
        "server_embed_ms": None,
        "cache_hit": None,
        "count": None,
        "error": detail,
    }


def summarize(
    results: list[dict[str, Any]],
    *,
    wall_ms: float,
    base_url: str,
    mode: str,
    requests: int,
    concurrency: int,
    prewarm: bool,
    unique_queries: bool,
) -> dict[str, Any]:
    success = [item for item in results if item["ok"]]
    failed = [item for item in results if not item["ok"]]
    return {
        "ok": len(failed) == 0,
        "base_url": base_url,
        "mode": mode,
        "requests": requests,
        "concurrency": concurrency,
        "prewarm": prewarm,
        "unique_queries": unique_queries,
        "wall_ms": round(wall_ms, 3),
        "rps": round(len(results) / max(wall_ms / 1000, 0.001), 3),
        "success": len(success),
        "failed": len(failed),
        "error_rate": round(len(failed) / max(len(results), 1), 6),
        "latency": metric_block([float(item["elapsed_ms"]) for item in success]),
        "server_total_ms": metric_block([float(item["server_total_ms"]) for item in success if item["server_total_ms"] is not None]),
        "server_embed_ms": metric_block([float(item["server_embed_ms"]) for item in success if item["server_embed_ms"] is not None]),
        "cache_hit_rate": round(
            sum(1 for item in success if item.get("cache_hit") is True) / max(len(success), 1),
            6,
        ),
        "query_type_counts": count_by(success, "query_type"),
        "status_counts": count_by(results, "status"),
        "top_errors": top_errors(failed),
    }


def metric_block(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "avg": None, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "min": round(ordered[0], 3),
        "avg": round(statistics.fmean(ordered), 3),
        "p50": round(percentile(ordered, 0.50), 3),
        "p95": round(percentile(ordered, 0.95), 3),
        "p99": round(percentile(ordered, 0.99), 3),
        "max": round(ordered[-1], 3),
    }


def percentile(ordered: list[float], rank: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * rank
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def top_errors(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in items:
        error = str(item.get("error") or "")[:180]
        counts[error] = counts.get(error, 0) + 1
    return [
        {"count": count, "error": error}
        for error, count in sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:8]
    ]


def console_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": report["ok"],
        "mode": report["mode"],
        "requests": report["requests"],
        "concurrency": report["concurrency"],
        "prewarm": report["prewarm"],
        "unique_queries": report["unique_queries"],
        "success": report["success"],
        "failed": report["failed"],
        "rps": report["rps"],
        "latency": report["latency"],
        "server_total_ms": report["server_total_ms"],
        "server_embed_ms": report["server_embed_ms"],
        "cache_hit_rate": report["cache_hit_rate"],
        "top_errors": report["top_errors"],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Gemini Demo Load Test",
        "",
        f"- OK: `{report['ok']}`",
        f"- Base URL: `{report['base_url']}`",
        f"- Mode: `{report['mode']}`",
        f"- Requests: `{report['requests']}`",
        f"- Concurrency: `{report['concurrency']}`",
        f"- Prewarm: `{report['prewarm']}`",
        f"- Unique queries: `{report['unique_queries']}`",
        f"- Success / failed: `{report['success']} / {report['failed']}`",
        f"- Error rate: `{report['error_rate'] * 100:.2f}%`",
        f"- RPS: `{report['rps']}`",
        f"- Cache hit rate: `{report['cache_hit_rate'] * 100:.2f}%`",
        "",
        "## Latency",
        "",
        "| Metric | Client ms | Server total ms | Server embed ms |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key in ["min", "avg", "p50", "p95", "p99", "max"]:
        lines.append(
            "| {key} | {client} | {server} | {embed} |".format(
                key=key,
                client=format_metric(report["latency"].get(key)),
                server=format_metric(report["server_total_ms"].get(key)),
                embed=format_metric(report["server_embed_ms"].get(key)),
            )
        )
    if report["top_errors"]:
        lines.extend(["", "## Top Errors", ""])
        for item in report["top_errors"]:
            lines.append(f"- `{item['count']}` {item['error']}")
    lines.append("")
    return "\n".join(lines)


def format_metric(value: Any) -> str:
    if value is None:
        return "-"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
