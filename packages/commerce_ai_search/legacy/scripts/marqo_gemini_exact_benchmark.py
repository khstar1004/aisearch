from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import mimetypes
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8120"
DEFAULT_CSV = "logs/jclgift-products-1800-supported.csv"
DEFAULT_JSON_OUTPUT = "logs/marqo-gemini-exact-benchmark.json"
DEFAULT_MARKDOWN_OUTPUT = "logs/marqo-gemini-exact-benchmark.md"
DEFAULT_IMAGE_CACHE_DIR = "logs/marqo-gemini-image-cache"
DEFAULT_MALL_ID = "shop001"
DEFAULT_API_KEY = "public-shop001-dev-key"
DEFAULT_ORIGIN = "http://127.0.0.1:3000"


TEXT_CASES: list[dict[str, Any]] = [
    {"name": "text_stanley_tumbler", "q": "\uc2a4\ud0e0\ub9ac \ud140\ube14\ub7ec", "expected": ["\ud140\ube14\ub7ec"]},
    {"name": "text_black_umbrella", "q": "\uac80\uc740 \uc6b0\uc0b0", "expected": ["\uc6b0\uc0b0"]},
    {"name": "text_yellow_long_umbrella", "q": "\ub178\ub780\uc0c9 \uc7a5\uc6b0\uc0b0", "expected": ["\uc6b0\uc0b0"]},
    {
        "name": "text_premium_pen",
        "q": "\uace0\uae09 \ubcfc\ud39c",
        "expected": ["\ubcfc\ud39c", "\uace0\uae09\ubcfc\ud39c(1\ub9cc\uc6d0\uc774\uc0c1)", "\uad6d\uc0b0\ube0c\ub79c\ub4dc\ud39c"],
    },
    {"name": "text_hotel_towel_2p", "q": "\ud638\ud154 \ud0c0\uc62c 2P", "expected": ["\ud0c0\uc62c"]},
    {
        "name": "text_crystal_award",
        "q": "\ud06c\ub9ac\uc2a4\ud0c8 \uac10\uc0ac\ud328",
        "expected": ["\uc0c1\ud328", "\uace8\ud504 \uc0c1\ud328", "\ub098\ubb34 \uc0c1\ud328", "\uace8\ud504\ud2b8\ub85c\ud53c/\uc0c1\ud328"],
    },
    {"name": "text_band_first_aid", "q": "\ubc34\ub4dc \uad6c\uae09\ud568", "expected": ["\uad6c\uae09\ud568", "\uad6c\uae09\ud568/\uc548\uc804\uc6a9\ud488"]},
    {"name": "text_golf_ball_12", "q": "\uace8\ud504\uacf5 12\uad6c", "expected": ["\uace8\ud504\uacf5", "\uace8\ud504\uacf5\uc138\ud2b8"]},
    {"name": "text_mushroom_gift_set", "q": "\ud45c\uace0\ubc84\uc12f \uc120\ubb3c\uc138\ud2b8", "expected": ["\uae30\ud0c0\uc2dd\ud488(\uc138\ud2b8\ub958)", "\uac74\uac15\ubcf4\uc870\uc2dd\ud488", "\uac74\uac15\uae30\ub2a5\uc2dd\ud488(\uc778\uc99d)"]},
    {"name": "text_tarpaulin_bag", "q": "\ud0c0\ud3ec\ub9b0 \uc218\ub0a9 \uac00\ubc29", "expected": ["\uac00\ubc29"]},
    {"name": "text_desk_calendar", "q": "\ud0c1\uc0c1\uc6a9 \ub2ec\ub825", "expected": ["\ub2ec\ub825"]},
    {"name": "text_tool_set", "q": "\uacf5\uad6c \uc138\ud2b8", "expected": ["\uacf5\uad6c\uc6a9\ud488"]},
    {"name": "text_calculator", "q": "\uacc4\uc0b0\uae30", "expected": ["\uacc4\uc0b0\uae30"]},
    {"name": "text_humidifier", "q": "\uac00\uc2b5\uae30", "expected": ["\uac00\uc2b5\uae30", "\uac00\uc2b5\uae30/\uacf5\uae30\uccad\uc815\uae30"]},
    {"name": "text_napkin_chopstick", "q": "\ub124\ud504\ud0a8 \uc813\uac00\ub77d\uc9d1", "expected": ["\ub124\ud504\ud0a8/\uc813\uac00\ub77d\uc9d1"]},
    {"name": "text_korean_flag", "q": "\ud0dc\uadf9\uae30 \uad6d\uae30", "expected": ["\uad6d\uae30"]},
    {"name": "text_pot", "q": "\ub0c4\ube44", "expected": ["\ub0c4\ube44"]},
    {"name": "text_diary_planner", "q": "\ub2e4\uc774\uc5b4\ub9ac \ud50c\ub798\ub108", "expected": ["\ub2e4\uc774\uc5b4\ub9ac"]},
    {"name": "text_mother_of_pearl_usb", "q": "\uc790\uac1c USB", "expected": ["\uace0\uae09\ud615/\uc790\uac1c USB"]},
    {"name": "text_winter_neck_warmer", "q": "\ub125\uc6cc\uba38 \ubc29\ud55c", "expected": ["\ub125\uc6cc\uba38", "\ub125\uc6cc\uba38/\ubaa9\ub3c4\ub9ac", "\uaca8\uc6b8\ud310\ucd09\ubb3c"]},
    {"name": "text_sticky_memo", "q": "\uc810\ucc29 \uba54\ubaa8\uc9c0", "expected": ["\uc810\ucc29\uba54\ubaa8\uc9c0"]},
]


IMAGE_CASES: list[dict[str, Any]] = [
    {"name": "image_bag_nametag", "product_id": "JCL433495", "expected": ["\uac00\ubc29"]},
    {"name": "image_golf_ball", "product_id": "JCL439288", "expected": ["\uace8\ud504\uacf5", "\uace8\ud504\uacf5\uc138\ud2b8"]},
    {"name": "image_award_trophy", "product_id": "JCL203052", "expected": ["\uc0c1\ud328", "\uace8\ud504 \uc0c1\ud328", "\uace8\ud504\ud2b8\ub85c\ud53c/\uc0c1\ud328"]},
    {"name": "image_first_aid", "product_id": "JCL190861", "expected": ["\uad6c\uae09\ud568", "\uad6c\uae09\ud568/\uc548\uc804\uc6a9\ud488"]},
    {"name": "image_towel", "product_id": "JCL369209", "expected": ["\ud0c0\uc62c"]},
    {"name": "image_umbrella", "product_id": "JCL482616", "expected": ["\uc6b0\uc0b0", "\uace8\ud504\uc6b0\uc0b0(70cm~)"]},
    {"name": "image_tumbler", "product_id": "JCL471818", "expected": ["\ud140\ube14\ub7ec"]},
    {"name": "image_calendar", "product_id": "JCL481491", "expected": ["\ub2ec\ub825"]},
]


MIXED_CASES: list[dict[str, Any]] = [
    {"name": "mixed_auto_umbrella", "product_id": "JCL482616", "q": "\uc790\ub3d9 \uc6b0\uc0b0", "expected": ["\uc6b0\uc0b0", "\uace8\ud504\uc6b0\uc0b0(70cm~)"]},
    {"name": "mixed_golf_ball_12", "product_id": "JCL439288", "q": "\uace8\ud504\uacf5 12\uad6c", "expected": ["\uace8\ud504\uacf5", "\uace8\ud504\uacf5\uc138\ud2b8"]},
    {"name": "mixed_crystal_award", "product_id": "JCL203052", "q": "\ud06c\ub9ac\uc2a4\ud0c8 \uc0c1\ud328", "expected": ["\uc0c1\ud328", "\uace8\ud504 \uc0c1\ud328", "\uace8\ud504\ud2b8\ub85c\ud53c/\uc0c1\ud328"]},
    {"name": "mixed_tumbler_bottle", "product_id": "JCL471818", "q": "\ud140\ube14\ub7ec \ubb3c\ud1b5", "expected": ["\ud140\ube14\ub7ec"]},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument("--image-cache-dir", default=DEFAULT_IMAGE_CACHE_DIR)
    parser.add_argument("--mall-id", default=DEFAULT_MALL_ID)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--warm-repeat", action="store_true", default=True)
    parser.add_argument(
        "--case-types",
        default="text,image,text_image",
        help="Comma-separated case types to run: text, image, text_image",
    )
    parser.add_argument(
        "--include-legacy-qwen-reference",
        action="store_true",
        help="Include old local Qwen baseline files for one-off comparison. Hidden by default in operator handoff reports.",
    )
    return parser.parse_args()


def read_products(csv_path: Path) -> dict[str, dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("product_id") or ""): row for row in csv.DictReader(handle)}


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict[str, Any], float]:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    data = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
    return status, data, elapsed_ms


def download_image_data_url(row: dict[str, str], cache_dir: Path) -> str:
    url = str(row.get("main_image_url") or "").strip()
    if not url:
        raise ValueError(f"{row.get('product_id')} has no image URL")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(urllib.parse.urlparse(url).path).suffix or ".img"
    cache_path = cache_dir / f"{row.get('product_id')}-{digest}{suffix}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "haeorum-marqo-gemini-exact-benchmark/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            cache_path.write_bytes(response.read())
    mime = mimetypes.guess_type(str(cache_path))[0] or "image/jpeg"
    encoded = base64.b64encode(cache_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_payload(case: dict[str, Any], *, args: argparse.Namespace, products: dict[str, dict[str, str]], image_cache_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {"mall_id": args.mall_id, "limit": args.limit}
    if case.get("q"):
        payload["q"] = case["q"]
    if case.get("product_id"):
        row = products.get(str(case["product_id"]))
        if not row:
            raise ValueError(f"missing product_id in CSV: {case['product_id']}")
        payload["image_base64"] = download_image_data_url(row, image_cache_dir)
    return payload


def top_result_rows(data: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    rows = []
    for item in list(data.get("top") or []) + list(data.get("items") or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "product_id": item.get("product_id"),
                "name": item.get("name"),
                "category": item.get("category"),
                "score_percent": item.get("score_percent"),
                "source_scores": item.get("source_scores") or {},
            }
        )
        if len(rows) >= limit:
            break
    return rows


def expected_category_ok(rows: list[dict[str, Any]], expected: list[str], top_n: int = 8) -> bool:
    actual = [str(row.get("category") or "") for row in rows[:top_n]]
    expected_set = {str(item) for item in expected}
    return any(category in expected_set for category in actual)


def run_case(
    case: dict[str, Any],
    *,
    args: argparse.Namespace,
    products: dict[str, dict[str, str]],
    image_cache_dir: Path,
    headers: dict[str, str],
) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    payload = build_payload(case, args=args, products=products, image_cache_dir=image_cache_dir)
    cold_status, cold_data, cold_wall_ms = request_json(
        "POST",
        f"{base_url}/api/ai-search",
        payload=payload,
        headers=headers,
        timeout=90,
    )
    warm_status = None
    warm_data: dict[str, Any] | None = None
    warm_wall_ms = None
    if args.warm_repeat:
        warm_status, warm_data, warm_wall_ms = request_json(
            "POST",
            f"{base_url}/api/ai-search",
            payload=payload,
            headers=headers,
            timeout=90,
        )
    rows = top_result_rows(cold_data, limit=max(args.limit, 8))
    expected = list(case.get("expected") or [])
    ok = cold_status == 200 and expected_category_ok(rows, expected)
    row = products.get(str(case.get("product_id") or ""))
    return {
        "name": case["name"],
        "query_type": cold_data.get("meta", {}).get("query_type") or case_type(case),
        "q": case.get("q"),
        "product_id": case.get("product_id"),
        "source_product": {
            "name": row.get("product_name") if row else None,
            "category": row.get("category_name") if row else None,
            "image_url": row.get("main_image_url") if row else None,
        }
        if row
        else None,
        "expected_categories": expected,
        "ok": ok,
        "cold": {
            "status": cold_status,
            "wall_ms": cold_wall_ms,
            "api_elapsed_ms": cold_data.get("meta", {}).get("elapsed_ms"),
            "low_confidence": cold_data.get("meta", {}).get("low_confidence"),
            "notice": cold_data.get("meta", {}).get("notice"),
        },
        "warm": {
            "status": warm_status,
            "wall_ms": warm_wall_ms,
            "api_elapsed_ms": warm_data.get("meta", {}).get("elapsed_ms") if warm_data else None,
        }
        if args.warm_repeat
        else None,
        "top_results": rows,
        "suggested_categories": cold_data.get("suggested_categories") or [],
        "error": cold_data.get("detail") if cold_status != 200 else None,
    }


def case_type(case: dict[str, Any]) -> str:
    if case.get("q") and case.get("product_id"):
        return "text_image"
    if case.get("product_id"):
        return "image"
    return "text"


def pct(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 3)


def timing_summary(results: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    values = [
        float(result[phase]["wall_ms"])
        for result in results
        if isinstance(result.get(phase), dict) and isinstance(result[phase].get("wall_ms"), (int, float))
    ]
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min_ms": round(min(values), 3),
        "avg_ms": round(statistics.mean(values), 3),
        "p50_ms": pct(values, 0.50),
        "p95_ms": pct(values, 0.95),
        "max_ms": round(max(values), 3),
    }


def grouped_summary(results: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        groups.setdefault(str(result.get("query_type") or "unknown"), []).append(result)
    return {name: timing_summary(items, phase) for name, items in sorted(groups.items())}


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def qwen_comparison(project_root: Path) -> dict[str, Any]:
    quality = load_json(project_root / "logs/jclgift-qwen-quality-1800.json")
    text_load = load_json(project_root / "logs/jclgift-qwen-load-text-100c-query-cache.json")
    image_load = load_json(project_root / "logs/jclgift-qwen-load-image-30c-optimized.json")
    mixed_load = load_json(project_root / "logs/jclgift-qwen-load-mixed-traffic-850-query-cache.json")
    cases = list(quality.get("cases") or []) if quality else []
    return {
        "quality": {
            "source": "logs/jclgift-qwen-quality-1800.json",
            "case_count": len(cases) if cases else quality.get("case_count") if quality else None,
            "passed": sum(1 for case in cases if case.get("ok")) if cases else None,
        },
        "load": {
            "text": load_summary(text_load, "logs/jclgift-qwen-load-text-100c-query-cache.json"),
            "image": load_summary(image_load, "logs/jclgift-qwen-load-image-30c-optimized.json"),
            "text_image": load_summary(mixed_load, "logs/jclgift-qwen-load-mixed-traffic-850-query-cache.json"),
        },
    }


def load_summary(data: dict[str, Any] | None, source: str) -> dict[str, Any] | None:
    if not data:
        return None
    return {
        "source": source,
        "requests": data.get("requests"),
        "concurrency": data.get("concurrency"),
        "rps": data.get("requests_per_second"),
        "error_rate": data.get("error_rate"),
        "latency_ms": data.get("latency_ms"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Marqo Gemini Exact Path Benchmark",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Base URL: `{report['base_url']}`",
        f"- Dataset: `{report['csv']}`",
        f"- Products indexed: `{report['health'].get('stats', {}).get('numberOfDocuments')}`",
        f"- Engine: `{report['health'].get('engine')}`",
        f"- Embedding backend: `{report['health'].get('embedding_backend')}`",
        f"- Gemini service provider: `{(report['health'].get('gemini') or {}).get('provider')}`",
        f"- Gemini service model: `{report['health'].get('gemini_model')}`",
        f"- Gemini dimensions: `{report['health'].get('gemini_embedding_dimensions')}`",
        f"- Cold pass: `{summary['passed']} / {summary['case_count']}`",
        "",
        "## Timing",
        "",
        "| Phase | Type | Count | Avg ms | p50 ms | p95 ms | Max ms |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for phase in ["cold", "warm"]:
        for query_type, stats in report["timing_by_type"][phase].items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        phase,
                        query_type,
                        str(stats.get("count", 0)),
                        str(stats.get("avg_ms")),
                        str(stats.get("p50_ms")),
                        str(stats.get("p95_ms")),
                        str(stats.get("max_ms")),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | OK | Type | Cold ms | Warm ms | Expected | Top categories | Top IDs |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for item in report["results"]:
        categories = ", ".join(str(row.get("category") or "") for row in item["top_results"][:5])
        ids = ", ".join(str(row.get("product_id") or "") for row in item["top_results"][:3])
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item["name"]),
                    str(item["ok"]),
                    str(item["query_type"]),
                    str(item["cold"].get("wall_ms")),
                    str((item.get("warm") or {}).get("wall_ms")),
                    ", ".join(item.get("expected_categories") or []),
                    categories,
                    ids,
                ]
            )
            + " |"
        )
    comparison = report.get("legacy_qwen_comparison") or {}
    if comparison:
        lines.extend(["", "## Legacy Qwen Baseline (Comparison Only)", ""])
        lines.append("- This section is historical evidence only. The current runtime path is Marqo + Gemini.")
        quality = comparison.get("quality") or {}
        if quality:
            lines.append(
                f"- Legacy Qwen quality reference: `{quality.get('passed')} / {quality.get('case_count')}` from `{quality.get('source')}`"
            )
        load = comparison.get("load") or {}
        lines.append("")
        lines.append("| Type | Requests/Concurrency | RPS | Avg ms | p95 ms | Source |")
        lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
        for query_type, data in load.items():
            if not data:
                continue
            latency = data.get("latency_ms") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        query_type,
                        f"{data.get('requests')} / {data.get('concurrency')}",
                        str(data.get("rps")),
                        str(latency.get("avg")),
                        str(latency.get("p95")),
                        str(data.get("source")),
                    ]
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    project_root = Path.cwd()
    csv_path = Path(args.csv)
    products = read_products(csv_path)
    headers = {
        "Accept": "application/json",
        "X-API-Key": args.api_key,
        "Origin": args.origin,
    }
    health_status, health, health_wall_ms = request_json(
        "GET",
        f"{args.base_url.rstrip()}/health",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    allowed_case_types = {
        item.strip()
        for item in str(args.case_types or "").split(",")
        if item.strip()
    }
    all_cases = [*TEXT_CASES, *IMAGE_CASES, *MIXED_CASES]
    cases = [case for case in all_cases if case_type(case) in allowed_case_types]
    if not cases:
        raise ValueError("--case-types did not match any benchmark cases")
    results = [
        run_case(
            case,
            args=args,
            products=products,
            image_cache_dir=Path(args.image_cache_dir),
            headers=headers,
        )
        for case in cases
    ]
    passed = sum(1 for item in results if item.get("ok"))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url.rstrip("/"),
        "csv": str(csv_path),
        "health_status": health_status,
        "health_wall_ms": health_wall_ms,
        "health": health,
        "summary": {
            "case_count": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": round(passed / len(results), 4) if results else 0.0,
        },
        "timing": {
            "cold": timing_summary(results, "cold"),
            "warm": timing_summary(results, "warm"),
        },
        "timing_by_type": {
            "cold": grouped_summary(results, "cold"),
            "warm": grouped_summary(results, "warm"),
        },
        "results": results,
    }
    if args.include_legacy_qwen_reference:
        report["legacy_qwen_comparison"] = qwen_comparison(project_root)
    json_path = Path(args.json_output)
    md_path = Path(args.markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(json_path), "markdown": str(md_path), **report["summary"], "timing": report["timing"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
