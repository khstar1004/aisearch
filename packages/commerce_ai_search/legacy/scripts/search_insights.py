from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEARCH_LOG = ROOT / "logs" / "search.jsonl"
DEFAULT_SLOW_LATENCY_MS_BY_QUERY_TYPE = {
    "text": 3000.0,
    "image": 5000.0,
    "text_image": 5000.0,
    "unknown": 3000.0,
}


@dataclass
class QueryAggregate:
    query: str
    searches: int = 0
    clicks: int = 0
    raw_queries: Counter[str] = field(default_factory=Counter)
    zero_result_count: int = 0
    low_confidence_count: int = 0
    top_scores: list[float] = field(default_factory=list)
    query_types: Counter[str] = field(default_factory=Counter)
    malls: Counter[str] = field(default_factory=Counter)
    product_ids: Counter[str] = field(default_factory=Counter)
    categories: Counter[str] = field(default_factory=Counter)
    inferred_categories: Counter[str] = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)
    slow_count: int = 0


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "lines": 0,
        "malformed_lines": 0,
    }
    if not path.exists():
        return [], stats

    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            stats["lines"] += 1
            text = line.strip()
            if not text:
                continue
            try:
                entry = json.loads(text)
            except json.JSONDecodeError:
                stats["malformed_lines"] += 1
                continue
            if isinstance(entry, dict):
                entries.append(entry)
            else:
                stats["malformed_lines"] += 1
    return entries, stats


def build_report(
    search_log_path: Path | str = DEFAULT_SEARCH_LOG,
    click_log_path: Path | str | None = None,
    *,
    limit: int = 20,
    min_searches: int = 1,
    slow_text_ms: float = 3000.0,
    slow_image_ms: float = 5000.0,
    slow_mixed_ms: float = 5000.0,
) -> dict[str, Any]:
    search_path = Path(search_log_path)
    click_path = Path(click_log_path) if click_log_path else search_path
    same_file = search_path.resolve(strict=False) == click_path.resolve(strict=False)
    slow_latency_threshold_ms = slow_latency_thresholds(slow_text_ms, slow_image_ms, slow_mixed_ms)

    search_entries, search_stats = read_jsonl(search_path)
    if same_file:
        all_entries = search_entries
        click_stats = search_stats
    else:
        click_entries, click_stats = read_jsonl(click_path)
        all_entries = [*search_entries, *click_entries]

    searches = [entry for entry in all_entries if entry.get("type") == "search"]
    clicks = [entry for entry in all_entries if entry.get("type") == "click"]
    query_aggregates: dict[str, QueryAggregate] = {}
    clicked_products: Counter[str] = Counter()
    clicked_positions: dict[str, list[float]] = defaultdict(list)
    clicked_scores: dict[str, list[float]] = defaultdict(list)
    clicked_product_queries: dict[str, Counter[str]] = defaultdict(Counter)
    mixed_weight_searches: Counter[str] = Counter()
    mixed_weight_scores: dict[str, list[float]] = defaultdict(list)
    mixed_weight_low_confidence: Counter[str] = Counter()
    mixed_weight_zero_result: Counter[str] = Counter()
    mixed_weight_values: dict[str, tuple[float | None, float | None]] = {}
    image_quality_warnings: Counter[str] = Counter()
    image_quality_warning_events = 0
    image_normalized_events = 0
    image_sizes = []
    search_latencies_ms = []
    query_type_latencies_ms: dict[str, list[float]] = defaultdict(list)
    cache_latencies_ms: dict[str, list[float]] = defaultdict(list)
    slow_search_samples: list[dict[str, Any]] = []
    slow_search_events = 0
    unattributed_clicks = 0

    timestamps = [str(entry["timestamp"]) for entry in all_entries if entry.get("timestamp")]
    top_scores = []
    result_counts = []
    query_type_counts = Counter(str(entry.get("query_type") or "unknown") for entry in searches)

    for entry in searches:
        query_type = str(entry.get("query_type") or "unknown")
        elapsed_ms = as_float(entry.get("elapsed_ms"))
        if elapsed_ms is not None:
            search_latencies_ms.append(elapsed_ms)
            query_type_latencies_ms[query_type].append(elapsed_ms)
            cache_latencies_ms[cache_bucket(entry.get("cached"))].append(elapsed_ms)
            if elapsed_ms >= slow_latency_threshold(query_type, slow_latency_threshold_ms):
                slow_search_events += 1
                slow_search_samples.append(slow_search_sample(entry, elapsed_ms))
        score = as_float(entry.get("top_score_percent"))
        if score is not None:
            top_scores.append(score)
        result_count = as_int(entry.get("result_count"))
        if result_count is not None:
            result_counts.append(result_count)
        if query_type == "text_image":
            weight_key, text_weight, image_weight = mixed_weight_key(entry.get("text_weight"), entry.get("image_weight"))
            mixed_weight_values[weight_key] = (text_weight, image_weight)
            mixed_weight_searches[weight_key] += 1
            if score is not None:
                mixed_weight_scores[weight_key].append(score)
            if result_count == 0:
                mixed_weight_zero_result[weight_key] += 1
            if entry.get("low_confidence") is True:
                mixed_weight_low_confidence[weight_key] += 1

        if query_type in {"image", "text_image"}:
            warnings = list_strings(entry.get("image_quality_warnings"))
            if warnings:
                image_quality_warning_events += 1
                image_quality_warnings.update(warnings)
            if entry.get("image_normalized") is True:
                image_normalized_events += 1
            image_size = as_int(entry.get("image_size_bytes"))
            if image_size is not None:
                image_sizes.append(image_size)

        key, query = query_key(entry.get("normalized_query") or entry.get("q"))
        if key is None:
            continue
        aggregate = query_aggregates.setdefault(key, QueryAggregate(query=query))
        aggregate.searches += 1
        raw_query = string_value(entry.get("q"))
        if raw_query:
            aggregate.raw_queries[raw_query] += 1
        aggregate.query_types[query_type] += 1
        aggregate.malls[str(entry.get("mall_id") or "unknown")] += 1
        if result_count == 0:
            aggregate.zero_result_count += 1
        if entry.get("low_confidence") is True:
            aggregate.low_confidence_count += 1
        if score is not None:
            aggregate.top_scores.append(score)
        if elapsed_ms is not None:
            aggregate.latencies_ms.append(elapsed_ms)
            if elapsed_ms >= slow_latency_threshold(query_type, slow_latency_threshold_ms):
                aggregate.slow_count += 1
        aggregate.product_ids.update(list_strings(entry.get("top_product_ids")))
        aggregate.categories.update(list_strings(entry.get("suggested_categories")))
        aggregate.inferred_categories.update(list_strings(entry.get("inferred_categories")))

    for entry in clicks:
        product_id = string_value(entry.get("product_id"))
        key, query = query_key(entry.get("query"))
        if key is None:
            unattributed_clicks += 1
        else:
            aggregate = query_aggregates.setdefault(key, QueryAggregate(query=query))
            aggregate.clicks += 1
        if product_id:
            clicked_products[product_id] += 1
            if query:
                clicked_product_queries[product_id][query] += 1
            position = as_float(entry.get("position"))
            if position is not None:
                clicked_positions[product_id].append(position)
            score = as_float(entry.get("score_percent"))
            if score is not None:
                clicked_scores[product_id].append(score)

    query_rows = [query_to_dict(aggregate) for aggregate in query_aggregates.values() if aggregate.searches >= min_searches]
    query_rows.sort(key=lambda item: (-int(item["searches"]), -int(item["clicks"]), item["query"]))

    low_confidence_queries = sorted(
        [item for item in query_rows if item["low_confidence_count"] > 0],
        key=lambda item: (
            -int(item["low_confidence_count"]),
            score_sort_value(item["avg_top_score_percent"]),
            -int(item["searches"]),
            item["query"],
        ),
    )
    zero_result_queries = sorted(
        [item for item in query_rows if item["zero_result_count"] > 0],
        key=lambda item: (-int(item["zero_result_count"]), -int(item["searches"]), item["query"]),
    )
    no_click_queries = sorted(
        [item for item in query_rows if item["clicks"] == 0],
        key=lambda item: (-int(item["searches"]), score_sort_value(item["avg_top_score_percent"]), item["query"]),
    )
    slow_queries = sorted(
        [item for item in query_rows if int(item.get("slow_count") or 0) > 0],
        key=lambda item: (
            -(as_float(item.get("p95_latency_ms")) or 0.0),
            -(as_float(item.get("max_latency_ms")) or 0.0),
            -int(item.get("slow_count") or 0),
            -int(item.get("searches") or 0),
            item["query"],
        ),
    )
    slow_search_samples.sort(key=lambda item: (-(as_float(item.get("elapsed_ms")) or 0.0), str(item.get("timestamp") or "")))

    read_errors = []
    if not search_stats["exists"]:
        read_errors.append(f"search log not found: {search_path}")
    if not same_file and not click_stats["exists"]:
        read_errors.append(f"click log not found: {click_path}")

    search_count = len(searches)
    click_count = len(clicks)
    report = {
        "ok": not read_errors and search_count > 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "search_log_path": str(search_path),
        "click_log_path": str(click_path),
        "same_log_file": same_file,
        "read_errors": read_errors,
        "read_stats": {
            "search": search_stats,
            "click": click_stats,
        },
        "malformed_lines": int(search_stats["malformed_lines"])
        + (0 if same_file else int(click_stats["malformed_lines"])),
        "first_event_at": min(timestamps) if timestamps else None,
        "last_event_at": max(timestamps) if timestamps else None,
        "search_events": search_count,
        "click_events": click_count,
        "attributed_click_events": click_count - unattributed_clicks,
        "unattributed_click_events": unattributed_clicks,
        "click_through_rate": ratio(click_count, search_count),
        "click_through_rate_percent": percent(click_count, search_count),
        "text_search_events": query_type_counts.get("text", 0),
        "image_search_events": query_type_counts.get("image", 0) + query_type_counts.get("text_image", 0),
        "image_only_search_events": query_type_counts.get("image", 0),
        "mixed_search_events": query_type_counts.get("text_image", 0),
        "image_normalized_events": image_normalized_events,
        "image_quality_warning_events": image_quality_warning_events,
        "image_quality_warning_counts": dict(image_quality_warnings.most_common(limit)),
        "average_image_size_bytes": average(image_sizes),
        "query_type_counts": dict(sorted(query_type_counts.items())),
        "slow_latency_threshold_ms": slow_latency_threshold_ms,
        "latency_ms": latency_summary(search_latencies_ms),
        "query_type_latency_ms": {
            query_type: latency_summary(values)
            for query_type, values in sorted(query_type_latencies_ms.items())
        },
        "cache_latency_ms": {
            cache_state: latency_summary(values)
            for cache_state, values in sorted(cache_latencies_ms.items())
        },
        "slow_search_events": slow_search_events,
        "slow_search_event_rate_percent": percent(slow_search_events, search_count),
        "mixed_weight_performance": mixed_weight_rows(
            mixed_weight_searches,
            mixed_weight_scores,
            mixed_weight_low_confidence,
            mixed_weight_zero_result,
            mixed_weight_values,
            limit,
        ),
        "zero_result_events": sum(1 for count in result_counts if count == 0),
        "low_confidence_events": sum(1 for entry in searches if entry.get("low_confidence") is True),
        "average_result_count": average(result_counts),
        "average_top_score_percent": average(top_scores),
        "min_searches": min_searches,
        "limit": limit,
        "top_queries": query_rows[:limit],
        "low_confidence_queries": low_confidence_queries[:limit],
        "zero_result_queries": zero_result_queries[:limit],
        "no_click_queries": no_click_queries[:limit],
        "slow_queries": slow_queries[:limit],
        "slow_search_samples": slow_search_samples[:limit],
        "top_clicked_products": clicked_products_to_rows(
            clicked_products,
            clicked_positions,
            clicked_scores,
            clicked_product_queries,
            limit,
        ),
    }
    report["synonym_seed_candidates"] = build_synonym_seed_candidates(report, limit=min(limit, 20))
    report["quality_case_candidates"] = build_quality_case_candidates(report, limit=min(limit, 20))
    report["mixed_weight_recommendation"] = build_mixed_weight_recommendation(report)
    report["recommendations"] = build_recommendations(report, limit=min(limit, 20))
    return report


def build_synonyms_seed_payload(report: dict[str, Any]) -> dict[str, Any]:
    candidates = [dict(item) for item in report.get("synonym_seed_candidates") or []]
    synonyms = {
        str(item.get("term") or ""): list_strings(item.get("related_terms"))
        for item in candidates
        if str(item.get("term") or "").strip() and list_strings(item.get("related_terms"))
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "search_insights",
        "review_required": True,
        "not_operational_readiness": True,
        "candidate_count": len(candidates),
        "synonyms": synonyms,
        "candidates": candidates,
        "review_note": (
            "Review each synonym group before merging into query-synonyms.json; log-derived terms can include typos, "
            "brand names, seasonal phrases, or false positives."
        ),
    }


def build_quality_cases_seed_payload(report: dict[str, Any]) -> dict[str, Any]:
    cases = [dict(item) for item in report.get("quality_case_candidates") or []]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "search_insights",
        "review_required": True,
        "not_operational_readiness": True,
        "case_count": len(cases),
        "cases": cases,
        "review_note": (
            "Review expected categories and image inputs before merging into quality-cases.json; these are regression "
            "drafts derived from weak search-log outcomes."
        ),
    }


def mixed_weight_key(text_weight_value: Any, image_weight_value: Any) -> tuple[str, float | None, float | None]:
    text_weight = as_float(text_weight_value)
    image_weight = as_float(image_weight_value)
    if text_weight is None or image_weight is None:
        return "unknown", text_weight, image_weight
    return f"text={text_weight:.2f}, image={image_weight:.2f}", text_weight, image_weight


def mixed_weight_rows(
    searches: Counter[str],
    scores: dict[str, list[float]],
    low_confidence: Counter[str],
    zero_result: Counter[str],
    values: dict[str, tuple[float | None, float | None]],
    limit: int,
) -> list[dict[str, Any]]:
    rows = []
    for key, search_count in searches.most_common(limit):
        text_weight, image_weight = values.get(key, (None, None))
        rows.append(
            {
                "weights": key,
                "text_weight": text_weight,
                "image_weight": image_weight,
                "searches": search_count,
                "avg_top_score_percent": average(scores.get(key, [])),
                "low_confidence_count": low_confidence.get(key, 0),
                "low_confidence_rate_percent": percent(low_confidence.get(key, 0), search_count),
                "zero_result_count": zero_result.get(key, 0),
                "zero_result_rate_percent": percent(zero_result.get(key, 0), search_count),
            }
        )
    return rows


def query_to_dict(aggregate: QueryAggregate) -> dict[str, Any]:
    searches = aggregate.searches
    clicks = aggregate.clicks
    latency = latency_summary(aggregate.latencies_ms)
    return {
        "query": aggregate.query,
        "top_raw_queries": [query for query, _ in aggregate.raw_queries.most_common(10)],
        "searches": searches,
        "clicks": clicks,
        "click_through_rate": ratio(clicks, searches),
        "click_through_rate_percent": percent(clicks, searches),
        "zero_result_count": aggregate.zero_result_count,
        "zero_result_rate_percent": percent(aggregate.zero_result_count, searches),
        "low_confidence_count": aggregate.low_confidence_count,
        "low_confidence_rate_percent": percent(aggregate.low_confidence_count, searches),
        "avg_top_score_percent": average(aggregate.top_scores),
        "latency_ms": latency,
        "avg_latency_ms": latency.get("avg_ms"),
        "p95_latency_ms": latency.get("p95_ms"),
        "max_latency_ms": latency.get("max_ms"),
        "slow_count": aggregate.slow_count,
        "slow_rate_percent": percent(aggregate.slow_count, searches),
        "query_type_counts": dict(sorted(aggregate.query_types.items())),
        "mall_counts": dict(aggregate.malls.most_common(10)),
        "top_product_ids": [product_id for product_id, _ in aggregate.product_ids.most_common(10)],
        "top_categories": [category for category, _ in aggregate.categories.most_common(10)],
        "inferred_categories": [category for category, _ in aggregate.inferred_categories.most_common(10)],
    }


def clicked_products_to_rows(
    clicked_products: Counter[str],
    clicked_positions: dict[str, list[float]],
    clicked_scores: dict[str, list[float]],
    clicked_product_queries: dict[str, Counter[str]],
    limit: int,
) -> list[dict[str, Any]]:
    rows = []
    for product_id, clicks in clicked_products.most_common(limit):
        rows.append(
            {
                "product_id": product_id,
                "clicks": clicks,
                "avg_position": average(clicked_positions[product_id]),
                "avg_score_percent": average(clicked_scores[product_id]),
                "top_queries": [query for query, _ in clicked_product_queries[product_id].most_common(10)],
            }
        )
    return rows


def build_synonym_seed_candidates(report: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for source, priority in [
        ("zero_result_queries", "high"),
        ("low_confidence_queries", "medium"),
        ("no_click_queries", "medium"),
    ]:
        for item in report.get(source, [])[:limit]:
            query = string_value(item.get("query"))
            if not query:
                continue
            raw_variants = [
                raw_query
                for raw_query in list_strings(item.get("top_raw_queries"))
                if raw_query.casefold() != query.casefold()
            ]
            terms = raw_variants or [query]
            related_terms = ordered_unique(
                [
                    *list_strings(item.get("inferred_categories")),
                    *list_strings(item.get("top_categories")),
                    query,
                ]
            )
            for term in terms:
                related = [value for value in related_terms if value.casefold() != term.casefold()]
                if not related:
                    continue
                candidate = candidates.setdefault(
                    term.casefold(),
                    {
                        "term": term,
                        "related_terms": [],
                        "priority": priority,
                        "sources": [],
                        "searches": 0,
                        "zero_result_count": 0,
                        "low_confidence_count": 0,
                        "clicks": 0,
                    },
                )
                candidate["related_terms"] = ordered_unique([*candidate["related_terms"], *related])[:10]
                candidate["sources"] = ordered_unique([*candidate["sources"], source])
                candidate["searches"] += int(item.get("searches") or 0)
                candidate["zero_result_count"] += int(item.get("zero_result_count") or 0)
                candidate["low_confidence_count"] += int(item.get("low_confidence_count") or 0)
                candidate["clicks"] += int(item.get("clicks") or 0)
                if candidate["priority"] != "high" and priority == "high":
                    candidate["priority"] = "high"
    rows = list(candidates.values())
    rows.sort(
        key=lambda item: (
            0 if item["priority"] == "high" else 1,
            -int(item["zero_result_count"]),
            -int(item["low_confidence_count"]),
            -int(item["searches"]),
            item["term"],
        )
    )
    for item in rows:
        item["suggested_synonyms_entry"] = {item["term"]: item["related_terms"]}
    return rows[:limit]


def build_quality_case_candidates(report: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for source, tag in [
        ("zero_result_queries", "zero_result_regression"),
        ("low_confidence_queries", "low_confidence_regression"),
        ("no_click_queries", "no_click_review"),
    ]:
        for item in report.get(source, [])[:limit]:
            query = string_value(first_value(list_strings(item.get("top_raw_queries"))) or item.get("query"))
            if not query:
                continue
            categories = ordered_unique(
                [*list_strings(item.get("inferred_categories")), *list_strings(item.get("top_categories"))]
            )
            case_tags = [tag]
            normalized_query = string_value(item.get("query"))
            if normalized_query and query.casefold() != normalized_query.casefold():
                case_tags.append("typo_or_synonym")
            candidate: dict[str, Any] = {
                "name": quality_case_name(query, tag),
                "source": source,
                "query": {"q": query},
                "tags": case_tags,
                "expected_min_results": 3,
                "evidence": {
                    "searches": int(item.get("searches") or 0),
                    "clicks": int(item.get("clicks") or 0),
                    "zero_result_count": int(item.get("zero_result_count") or 0),
                    "low_confidence_count": int(item.get("low_confidence_count") or 0),
                    "avg_top_score_percent": item.get("avg_top_score_percent"),
                },
            }
            if categories:
                candidate["expected_category"] = categories[0]
            if source == "low_confidence_queries":
                candidate["review_note"] = "Use this as a positive regression case after product text/image or synonym fixes raise confidence."
            candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            0 if item["source"] == "zero_result_queries" else 1,
            -int((item.get("evidence") or {}).get("zero_result_count") or 0),
            -int((item.get("evidence") or {}).get("low_confidence_count") or 0),
            item["name"],
        )
    )
    return candidates[:limit]


def build_mixed_weight_recommendation(report: dict[str, Any]) -> dict[str, Any] | None:
    rows = [item for item in report.get("mixed_weight_performance", []) if int(item.get("searches") or 0) > 0]
    if not rows:
        return None
    ranked = sorted(
        rows,
        key=lambda item: (
            item.get("zero_result_rate_percent") if item.get("zero_result_rate_percent") is not None else 101.0,
            item.get("low_confidence_rate_percent") if item.get("low_confidence_rate_percent") is not None else 101.0,
            -(item.get("avg_top_score_percent") if item.get("avg_top_score_percent") is not None else -1.0),
            -int(item.get("searches") or 0),
            item.get("weights") or "",
        ),
    )
    best = ranked[0]
    return {
        "weights": best.get("weights"),
        "text_weight": best.get("text_weight"),
        "image_weight": best.get("image_weight"),
        "searches": best.get("searches"),
        "avg_top_score_percent": best.get("avg_top_score_percent"),
        "low_confidence_rate_percent": best.get("low_confidence_rate_percent"),
        "zero_result_rate_percent": best.get("zero_result_rate_percent"),
        "action": "Use as the next mixed-search weight candidate only after validating against quality cases and representative-site logs.",
    }


def build_recommendations(report: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    slow_thresholds = report.get("slow_latency_threshold_ms") if isinstance(report.get("slow_latency_threshold_ms"), dict) else {}
    for query_type, summary in (report.get("query_type_latency_ms") or {}).items():
        p95_ms = as_float((summary or {}).get("p95_ms"))
        threshold_ms = slow_latency_threshold(str(query_type), slow_thresholds)
        if p95_ms is not None and p95_ms >= threshold_ms:
            recommendations.append(
                {
                    "type": "query_type_latency",
                    "priority": "high",
                    "query": str(query_type),
                    "searches": int((summary or {}).get("count") or 0),
                    "evidence": f"p95 {p95_ms}ms is at or above {threshold_ms}ms threshold",
                    "action": "Check backend latency, image preprocessing/Gemini path, cache hit ratio, queue wait metrics, and representative input size for this query type.",
                }
            )
    for item in report.get("slow_queries", [])[:limit]:
        recommendations.append(
            {
                "type": "slow_query",
                "priority": "high",
                "query": item["query"],
                "searches": item["searches"],
                "evidence": f"{item['slow_count']} slow searches; p95 {item['p95_latency_ms']}ms; max {item['max_latency_ms']}ms",
                "action": "Replay this query against Marqo with admin metrics enabled, then inspect cache status, result count, filters, and backend request timing.",
            }
        )
    for item in report.get("zero_result_queries", [])[:limit]:
        recommendations.append(
            {
                "type": "zero_result_query",
                "priority": "high",
                "query": item["query"],
                "searches": item["searches"],
                "evidence": f"{item['zero_result_count']} zero-result searches",
                "action": "Add synonyms/category aliases or confirm that matching active products are indexed.",
            }
        )
    for item in report.get("low_confidence_queries", [])[:limit]:
        recommendations.append(
            {
                "type": "low_confidence_query",
                "priority": "medium",
                "query": item["query"],
                "searches": item["searches"],
                "evidence": f"{item['low_confidence_count']} low-confidence searches; avg top score {item['avg_top_score_percent']}",
                "action": "Review product names, category labels, keywords, descriptions, and representative images.",
            }
        )
    for warning, count in dict(report.get("image_quality_warning_counts") or {}).items():
        recommendations.append(
            {
                "type": "image_quality_warning",
                "priority": "medium",
                "query": "",
                "searches": count,
                "evidence": f"{count} image searches logged {warning}",
                "action": "Review upload examples and representative product images for preprocessing or guidance updates.",
            }
        )
    for item in report.get("no_click_queries", [])[:limit]:
        if int(item.get("searches") or 0) < int(report.get("min_searches") or 1):
            continue
        recommendations.append(
            {
                "type": "no_click_query",
                "priority": "medium",
                "query": item["query"],
                "searches": item["searches"],
                "evidence": f"{item['searches']} searches and no attributed clicks",
                "action": "Inspect top products, thumbnails, prices, and detail URLs for search intent mismatch.",
            }
        )
    if report.get("synonym_seed_candidates"):
        recommendations.append(
            {
                "type": "synonym_seed_candidate",
                "priority": "high",
                "query": str(report["synonym_seed_candidates"][0]["term"]),
                "searches": int(report["synonym_seed_candidates"][0]["searches"]),
                "evidence": "Candidate generated from zero-result, low-confidence, or no-click search logs",
                "action": "Review and merge safe entries into query-synonyms.json, then rerun quality_report.py.",
            }
        )
    if report.get("quality_case_candidates"):
        recommendations.append(
            {
                "type": "quality_case_candidate",
                "priority": "medium",
                "query": str(report["quality_case_candidates"][0]["query"].get("q") or ""),
                "searches": int((report["quality_case_candidates"][0].get("evidence") or {}).get("searches") or 0),
                "evidence": "Candidate generated from logged search failures or weak engagement",
                "action": "Add reviewed cases to quality-cases.json so future tuning is regression-tested.",
            }
        )
    if report.get("mixed_weight_recommendation"):
        recommendation = report["mixed_weight_recommendation"]
        recommendations.append(
            {
                "type": "mixed_weight_candidate",
                "priority": "low",
                "query": str(recommendation.get("weights") or ""),
                "searches": int(recommendation.get("searches") or 0),
                "evidence": (
                    f"avg top score {recommendation.get('avg_top_score_percent')}; "
                    f"low-confidence {recommendation.get('low_confidence_rate_percent')}%; "
                    f"zero-result {recommendation.get('zero_result_rate_percent')}%"
                ),
                "action": "Use only as an A/B candidate and confirm with representative quality cases before changing defaults.",
            }
        )
    return recommendations[: max(limit, 20)]


def ordered_unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = string_value(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def first_value(values: list[str]) -> str | None:
    return values[0] if values else None


def quality_case_name(query: str, tag: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "_" for char in query.strip())
    normalized = "_".join(part for part in normalized.split("_") if part)
    return f"{tag}_{normalized or 'query'}"[:80]


def query_key(value: Any) -> tuple[str | None, str | None]:
    text = string_value(value)
    if not text:
        return None, None
    display = " ".join(text.split())
    return display.casefold(), display


def string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (string_value(item) for item in value) if text]


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    if number is None:
        return None
    return int(number)


def slow_latency_thresholds(slow_text_ms: Any, slow_image_ms: Any, slow_mixed_ms: Any) -> dict[str, float]:
    return {
        "text": positive_float(slow_text_ms, DEFAULT_SLOW_LATENCY_MS_BY_QUERY_TYPE["text"]),
        "image": positive_float(slow_image_ms, DEFAULT_SLOW_LATENCY_MS_BY_QUERY_TYPE["image"]),
        "text_image": positive_float(slow_mixed_ms, DEFAULT_SLOW_LATENCY_MS_BY_QUERY_TYPE["text_image"]),
        "unknown": DEFAULT_SLOW_LATENCY_MS_BY_QUERY_TYPE["unknown"],
    }


def positive_float(value: Any, default: float) -> float:
    number = as_float(value)
    if number is None or number <= 0:
        return default
    return float(number)


def slow_latency_threshold(query_type: str, thresholds: dict[str, Any]) -> float:
    normalized = str(query_type or "unknown")
    value = thresholds.get(normalized)
    if as_float(value) is not None:
        return float(value)
    return float(thresholds.get("unknown") or DEFAULT_SLOW_LATENCY_MS_BY_QUERY_TYPE["unknown"])


def cache_bucket(value: Any) -> str:
    if value is True:
        return "cached"
    if value is False:
        return "uncached"
    return "unknown"


def slow_search_sample(entry: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
    return {
        "timestamp": entry.get("timestamp"),
        "query": string_value(entry.get("normalized_query") or entry.get("q")),
        "raw_query": string_value(entry.get("q")),
        "query_type": str(entry.get("query_type") or "unknown"),
        "mall_id": string_value(entry.get("mall_id")),
        "elapsed_ms": round(elapsed_ms, 1),
        "cached": entry.get("cached"),
        "result_count": as_int(entry.get("result_count")),
        "top_score_percent": as_float(entry.get("top_score_percent")),
        "low_confidence": entry.get("low_confidence") is True,
    }


def percentile(values: list[float], percent_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, round((percent_value / 100.0) * (len(ordered) - 1))))
    return round(ordered[index], 1)


def latency_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min_ms": None, "avg_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    numeric = [float(value) for value in values]
    return {
        "count": len(numeric),
        "min_ms": round(min(numeric), 1),
        "avg_ms": average(numeric),
        "p50_ms": percentile(numeric, 50),
        "p95_ms": percentile(numeric, 95),
        "p99_ms": percentile(numeric, 99),
        "max_ms": round(max(numeric), 1),
    }


def average(values: list[float] | list[int]) -> float | None:
    if not values:
        return None
    return round(sum(float(value) for value in values) / len(values), 1)


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def percent(numerator: int, denominator: int) -> float | None:
    value = ratio(numerator, denominator)
    return round(value * 100, 1) if value is not None else None


def score_sort_value(value: Any) -> float:
    number = as_float(value)
    return number if number is not None else 101.0


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Haeorum AI Search Log Insights",
        "",
        f"- OK: `{report['ok']}`",
        f"- Search events: `{report['search_events']}`",
        f"- Click events: `{report['click_events']}`",
        f"- Click-through rate: `{report['click_through_rate_percent']}`%",
        f"- Zero-result events: `{report['zero_result_events']}`",
        f"- Low-confidence events: `{report['low_confidence_events']}`",
        f"- Slow search events: `{report.get('slow_search_events')}`",
        f"- Search p95 latency ms: `{(report.get('latency_ms') or {}).get('p95_ms')}`",
        f"- Image normalized events: `{report['image_normalized_events']}`",
        f"- Image quality warning events: `{report['image_quality_warning_events']}`",
        f"- Malformed lines ignored: `{report['malformed_lines']}`",
        "",
    ]
    append_query_table(lines, "Top Queries", report["top_queries"])
    append_query_table(lines, "Zero Result Queries", report["zero_result_queries"])
    append_query_table(lines, "Low Confidence Queries", report["low_confidence_queries"])
    append_query_table(lines, "No Click Queries", report["no_click_queries"])
    append_latency_table(lines, "Query Type Latency", report.get("query_type_latency_ms") or {})
    append_latency_table(lines, "Cache Latency", report.get("cache_latency_ms") or {})
    append_query_table(lines, "Slow Queries", report.get("slow_queries") or [])
    append_slow_sample_table(lines, report.get("slow_search_samples") or [])
    append_mixed_weight_table(lines, report["mixed_weight_performance"])
    append_mixed_weight_recommendation(lines, report.get("mixed_weight_recommendation"))
    append_image_quality_table(lines, report["image_quality_warning_counts"])
    append_synonym_seed_table(lines, report.get("synonym_seed_candidates") or [])
    append_quality_case_table(lines, report.get("quality_case_candidates") or [])
    lines.extend(["## Recommendations", "", "| Type | Priority | Query | Evidence | Action |", "| --- | --- | --- | --- | --- |"])
    for item in report["recommendations"]:
        lines.append(
            "| {type} | {priority} | {query} | {evidence} | {action} |".format(
                type=item["type"],
                priority=item["priority"],
                query=item["query"],
                evidence=item["evidence"],
                action=item["action"],
            )
        )
    lines.append("")
    lines.extend(["## Top Clicked Products", "", "| Product | Clicks | Avg Position | Top Queries |", "| --- | ---: | ---: | --- |"])
    for item in report["top_clicked_products"]:
        lines.append(
            "| {product_id} | {clicks} | {avg_position} | {queries} |".format(
                product_id=item["product_id"],
                clicks=item["clicks"],
                avg_position=item["avg_position"],
                queries=", ".join(item["top_queries"]),
            )
        )
    return "\n".join(lines) + "\n"


def append_mixed_weight_table(lines: list[str], rows: list[dict[str, Any]]) -> None:
    lines.extend(
        [
            "## Mixed Weight Performance",
            "",
            "| Weights | Searches | Avg Top Score | Low Conf. | Zero |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in rows:
        lines.append(
            "| {weights} | {searches} | {score} | {low} | {zero} |".format(
                weights=item["weights"],
                searches=item["searches"],
                score=item["avg_top_score_percent"],
                low=item["low_confidence_count"],
                zero=item["zero_result_count"],
            )
        )
    lines.append("")


def append_image_quality_table(lines: list[str], counts: dict[str, int]) -> None:
    lines.extend(
        [
            "## Image Quality Warnings",
            "",
            "| Warning | Events |",
            "| --- | ---: |",
        ]
    )
    for warning, count in counts.items():
        lines.append(f"| {warning} | {count} |")
    lines.append("")


def append_latency_table(lines: list[str], title: str, rows: dict[str, dict[str, Any]]) -> None:
    lines.extend(
        [
            f"## {title}",
            "",
            "| Name | Count | Avg ms | p95 ms | p99 ms | Max ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, item in rows.items():
        lines.append(
            "| {name} | {count} | {avg} | {p95} | {p99} | {max} |".format(
                name=name,
                count=item.get("count"),
                avg=item.get("avg_ms"),
                p95=item.get("p95_ms"),
                p99=item.get("p99_ms"),
                max=item.get("max_ms"),
            )
        )
    lines.append("")


def append_slow_sample_table(lines: list[str], rows: list[dict[str, Any]]) -> None:
    lines.extend(
        [
            "## Slow Search Samples",
            "",
            "| Query Type | Query | Mall | Elapsed ms | Cached | Result Count |",
            "| --- | --- | --- | ---: | --- | ---: |",
        ]
    )
    for item in rows:
        lines.append(
            "| {query_type} | {query} | {mall_id} | {elapsed_ms} | {cached} | {result_count} |".format(
                query_type=item.get("query_type"),
                query=item.get("query") or "",
                mall_id=item.get("mall_id") or "",
                elapsed_ms=item.get("elapsed_ms"),
                cached=item.get("cached"),
                result_count=item.get("result_count"),
            )
        )
    lines.append("")


def append_mixed_weight_recommendation(lines: list[str], recommendation: dict[str, Any] | None) -> None:
    lines.extend(["## Mixed Weight Recommendation", ""])
    if not recommendation:
        lines.extend(["No mixed-search weight data.", ""])
        return
    lines.extend(
        [
            f"- Weights: `{recommendation.get('weights')}`",
            f"- Avg top score: `{recommendation.get('avg_top_score_percent')}`",
            f"- Low-confidence rate: `{recommendation.get('low_confidence_rate_percent')}`%",
            f"- Zero-result rate: `{recommendation.get('zero_result_rate_percent')}`%",
            f"- Action: {recommendation.get('action')}",
            "",
        ]
    )


def append_synonym_seed_table(lines: list[str], rows: list[dict[str, Any]]) -> None:
    lines.extend(
        [
            "## Synonym Seed Candidates",
            "",
            "| Term | Related Terms | Priority | Sources | Searches |",
            "| --- | --- | --- | --- | ---: |",
        ]
    )
    for item in rows:
        lines.append(
            "| {term} | {related} | {priority} | {sources} | {searches} |".format(
                term=item["term"],
                related=", ".join(item["related_terms"]),
                priority=item["priority"],
                sources=", ".join(item["sources"]),
                searches=item["searches"],
            )
        )
    lines.append("")


def append_quality_case_table(lines: list[str], rows: list[dict[str, Any]]) -> None:
    lines.extend(
        [
            "## Quality Case Candidates",
            "",
            "| Name | Source | Query | Expected Category | Tags |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in rows:
        lines.append(
            "| {name} | {source} | {query} | {category} | {tags} |".format(
                name=item["name"],
                source=item["source"],
                query=(item.get("query") or {}).get("q", ""),
                category=item.get("expected_category", ""),
                tags=", ".join(item.get("tags") or []),
            )
        )
    lines.append("")


def append_query_table(lines: list[str], title: str, rows: list[dict[str, Any]]) -> None:
    lines.extend(
        [
            f"## {title}",
            "",
            "| Query | Searches | Clicks | CTR % | Zero | Low Conf. | Slow | p95 ms | Max ms | Avg Top Score | Top Products | Categories |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for item in rows:
        lines.append(
            "| {query} | {searches} | {clicks} | {ctr} | {zero} | {low} | {slow} | {p95} | {max} | {score} | {products} | {categories} |".format(
                query=item["query"],
                searches=item["searches"],
                clicks=item["clicks"],
                ctr=item["click_through_rate_percent"],
                zero=item["zero_result_count"],
                low=item["low_confidence_count"],
                slow=item.get("slow_count"),
                p95=item.get("p95_latency_ms"),
                max=item.get("max_latency_ms"),
                score=item["avg_top_score_percent"],
                products=", ".join(item["top_product_ids"]),
                categories=", ".join(item["top_categories"] + item["inferred_categories"]),
            )
        )
    lines.append("")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Haeorum AI Search JSONL logs for quality tuning.")
    parser.add_argument("--search-log", default=str(DEFAULT_SEARCH_LOG))
    parser.add_argument("--click-log", default="", help="Optional separate click JSONL path. Defaults to --search-log.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-searches", type=int, default=1)
    parser.add_argument("--slow-text-ms", type=float, default=3000.0)
    parser.add_argument("--slow-image-ms", type=float, default=5000.0)
    parser.add_argument("--slow-mixed-ms", type=float, default=5000.0)
    parser.add_argument("--json-output", "--output", dest="json_output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--synonyms-output", default="", help="Write review-required query-synonyms seed JSON.")
    parser.add_argument("--quality-cases-output", default="", help="Write review-required quality-cases seed JSON.")
    args = parser.parse_args()

    report = build_report(
        Path(args.search_log),
        Path(args.click_log) if args.click_log else None,
        limit=max(args.limit, 1),
        min_searches=max(args.min_searches, 1),
        slow_text_ms=args.slow_text_ms,
        slow_image_ms=args.slow_image_ms,
        slow_mixed_ms=args.slow_mixed_ms,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_output:
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
    if args.synonyms_output:
        payload = build_synonyms_seed_payload(report)
        Path(args.synonyms_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.synonyms_output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.quality_cases_output:
        payload = build_quality_cases_seed_payload(report)
        Path(args.quality_cases_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.quality_cases_output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
