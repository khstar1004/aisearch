from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


os.environ.setdefault("MARQO_URL", "http://127.0.0.1:8882")
os.environ.setdefault("HAEORUM_QWEN_EMBEDDING_URL", "http://127.0.0.1:8098")
os.environ.setdefault("HAEORUM_GEMINI_EMBEDDING_URL", "http://127.0.0.1:8098")
os.environ.setdefault("HAEORUM_EMBEDDING_BACKEND", "gemini")
os.environ.setdefault("HAEORUM_GEMINI_EMBEDDING_DIMENSIONS", "1536")
os.environ.setdefault("HAEORUM_QWEN_EMBEDDING_DIMENSIONS", "1536")
os.environ.setdefault("HAEORUM_GEMINI_MODEL", "gemini-embedding-2")
os.environ.setdefault("HAEORUM_QWEN_MODEL", "gemini-embedding-2")
os.environ.setdefault("HAEORUM_INDEX_NAME", "haeorum-gemini-marqo-jclgift")
os.environ.setdefault(
    "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY",
    "local-internal-gemini-proxy-key-20260531",
)
os.environ.setdefault(
    "HAEORUM_QWEN_EMBEDDING_PROXY_API_KEY",
    "local-internal-gemini-proxy-key-20260531",
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.engine_factory import create_search_engine  # noqa: E402
from app.main import AISearchService  # noqa: E402
from app.models import SearchRequest  # noqa: E402


TOKENS = {
    "umbrella": [u(r"\uc6b0\uc0b0"), u(r"\uc7a5\uc6b0\uc0b0"), u(r"\uc591\uc0b0"), u(r"\ud30c\ub77c\uc194")],
    "fan": [u(r"\ubd80\ucc44"), u(r"\ud569\uc8fd\uc120"), u(r"\uc190\ubd80\ucc44"), u(r"\uc624\uc8fd\uc120")],
    "tumbler": [u(r"\ud140\ube14\ub7ec"), u(r"\ub9c8\uc774\ubcf4\ud2c0"), u(r"\ubb3c\ubcd1"), u(r"\ubcf4\uc628\ubcd1"), u(r"\ubcf4\ud2c0")],
    "mug": [u(r"\uba38\uadf8"), u(r"\ucef5")],
    "towel": [u(r"\uc218\uac74"), u(r"\ud0c0\uc62c"), u(r"\ud0c0\uc6d4"), u(r"\ud0c0\uc640"), u(r"\ud0c0\uc6e8"), u(r"\uc1a1\uc6d4")],
    "pen": [u(r"\ubcfc\ud39c"), u(r"\ud39c"), u(r"\uc0e4\ud504"), u(r"\ub9cc\ub144\ud544"), u(r"\ud615\uad11\ud39c")],
    "memo": [u(r"\ud3ec\uc2a4\ud2b8\uc787"), u(r"\uba54\ubaa8"), u(r"\uc810\ucc29")],
    "notebook": [u(r"\ub178\ud2b8"), u(r"\uc218\ucca9"), u(r"\ub2e4\uc774\uc5b4\ub9ac")],
    "calendar": [u(r"\ub2ec\ub825"), u(r"\uce98\ub9b0\ub354"), u(r"\uce74\ub80c\ub2e4"), u(r"\uce74\ub80c\ub354")],
    "bag": [u(r"\uac00\ubc29"), u(r"\uc5d0\ucf54\ubc31"), u(r"\uc7a5\ubc14\uad6c\ub2c8"), u(r"\ubc31"), u(r"\ubc31")],
    "pouch": [u(r"\ud30c\uc6b0\uce58"), u(r"\ud3ec\uce58")],
    "cooler": [u(r"\ubcf4\ub0c9"), u(r"\ucfe8\ub7ec")],
    "usb": [u(r"usb"), u(r"\uc720\uc5d0\uc2a4\ube44"), u(r"\uba54\ubaa8\ub9ac")],
    "powerbank": [u(r"\ubcf4\uc870\ubc30\ud130\ub9ac"), u(r"\ubc30\ud130\ub9ac")],
    "charger": [u(r"\ucda9\uc804\uae30"), u(r"\ucda9\uc804"), u(r"\ucf00\uc774\ube14")],
    "mousepad": [u(r"\ub9c8\uc6b0\uc2a4\ud328\ub4dc"), u(r"\uc7a5\ud328\ub4dc"), u(r"\ub370\uc2a4\ud06c\ub9e4\ud2b8")],
    "tissue": [u(r"\ubb3c\ud2f0\uc288"), u(r"\ud2f0\uc288"), u(r"\uac01\ud2f0\uc288")],
    "award": [u(r"\uc0c1\ud328"), u(r"\uac10\uc0ac\ud328"), u(r"\ud2b8\ub85c\ud53c"), u(r"\ud06c\ub9ac\uc2a4\ud0c8")],
    "electric_fan": [u(r"\uc120\ud48d\uae30"), u(r"\uc190\uc120\ud48d\uae30"), u(r"\ubbf8\ub2c8\ud32c"), u(r"\ud734\ub300\uc6a9")],
    "clock": [u(r"\uc2dc\uacc4"), u(r"\ud0c1\uc0c1\uc2dc\uacc4")],
    "blanket": [u(r"\ub2f4\uc694"), u(r"\ubb34\ub985\ub2f4\uc694")],
    "health": [u(r"\uad6c\uae09\ud568"), u(r"\ubc34\ub4dc"), u(r"\ud30c\uc2a4")],
    "food": [u(r"\ud55c\uc6b0"), u(r"\uc18c\uae08"), u(r"\ucee4\ud53c"), u(r"\uc120\ubb3c\uc138\ud2b8")],
    "keyring": [u(r"\ud0a4\ub9c1"), u(r"\uc5f4\uc1e0\uace0\ub9ac"), u(r"\ud0a4\ud640\ub354")],
    "phone_stand": [u(r"\ud734\ub300\ud3f0"), u(r"\uac70\uce58\ub300"), u(r"\uc2a4\ud0e0\ub4dc")],
    "speaker": [u(r"\uc2a4\ud53c\ucee4"), u(r"\ube14\ub8e8\ud22c\uc2a4")],
    "calculator": [u(r"\uacc4\uc0b0\uae30")],
    "name_tag": [u(r"\ub124\uc784\ud0dd"), u(r"\ub124\uc784\ud14d"), u(r"\uba85\ucc30")],
    "mask": [u(r"\ub9c8\uc2a4\ud06c")],
}


CASES = [
    ("umbrella_basic", u(r"\uc6b0\uc0b0"), "umbrella"),
    ("umbrella_black", u(r"\uac80\uc740 \uc6b0\uc0b0"), "umbrella"),
    ("umbrella_long", u(r"\uc7a5\uc6b0\uc0b0"), "umbrella"),
    ("umbrella_3fold", u(r"3\ub2e8 \uc6b0\uc0b0"), "umbrella"),
    ("umbrella_golf", u(r"\uace8\ud504 \uc6b0\uc0b0"), "umbrella"),
    ("umbrella_auto", u(r"\uc790\ub3d9 \uc6b0\uc0b0"), "umbrella"),
    ("umbrella_uv", u(r"UV \ucc28\ub2e8 \uc6b0\uc0b0"), "umbrella"),
    ("umbrella_parasol", u(r"\uc591\uc0b0"), "umbrella"),
    ("umbrella_opening_gift", u(r"\uac1c\uc5c5\uc120\ubb3c \uc6b0\uc0b0"), "umbrella"),
    ("umbrella_typo", u(r"\uc6b0\uc2fc"), "umbrella"),
    ("fan_basic", u(r"\ubd80\ucc44"), "fan"),
    ("fan_yellow", u(r"\ub178\ub780\ubd80\ucc44"), "fan"),
    ("fan_folding", u(r"\uc811\uc774\uc2dd \ubd80\ucc44"), "fan"),
    ("fan_traditional", u(r"\uc804\ud1b5 \ubd80\ucc44"), "fan"),
    ("fan_hand", u(r"\uc190\ubd80\ucc44"), "fan"),
    ("fan_hapjuk", u(r"\ud569\uc8fd\uc120"), "fan"),
    ("fan_ojuk", u(r"\uc624\uc8fd\uc120"), "fan"),
    ("fan_handle", u(r"\uc790\ub8e8 \ubd80\ucc44"), "fan"),
    ("tumbler_basic", u(r"\ud140\ube14\ub7ec"), "tumbler"),
    ("tumbler_typo", u(r"\ud150\ube14\ub7ec"), "tumbler"),
    ("tumbler_stainless", u(r"\uc2a4\ud150 \ud140\ube14\ub7ec"), "tumbler"),
    ("tumbler_large", u(r"\ub300\uc6a9\ub7c9 \ud140\ube14\ub7ec"), "tumbler"),
    ("tumbler_white", u(r"\ud770\uc0c9 \ud140\ube14\ub7ec"), "tumbler"),
    ("bottle_water", u(r"\ubb3c\ubcd1"), "tumbler"),
    ("bottle_my", u(r"\ub9c8\uc774\ubcf4\ud2c0"), "tumbler"),
    ("mug_basic", u(r"\uba38\uadf8\ucef5"), "mug"),
    ("mug_stainless", u(r"\uc2a4\ud150 \uba38\uadf8"), "mug"),
    ("thermos", u(r"\ubcf4\uc628\ubcd1"), "tumbler"),
    ("towel_basic", u(r"\uc218\uac74"), "towel"),
    ("towel_alt", u(r"\ud0c0\uc62c"), "towel"),
    ("towel_hotel", u(r"\ud638\ud154 \uc218\uac74"), "towel"),
    ("towel_songwol", u(r"\uc1a1\uc6d4\ud0c0\uc62c"), "towel"),
    ("towel_sports", u(r"\uc2a4\ud3ec\uce20\ud0c0\uc62c"), "towel"),
    ("towel_beach", u(r"\ube44\uce58\ud0c0\uc6d4"), "towel"),
    ("towel_hand", u(r"\ud578\ub4dc\ud0c0\uc6d4"), "towel"),
    ("towel_return_gift", u(r"\ub2f5\ub840\ud488 \uc218\uac74"), "towel"),
    ("pen_basic", u(r"\ubcfc\ud39c"), "pen"),
    ("pen_premium", u(r"\uace0\uae09 \ubcfc\ud39c"), "pen"),
    ("pen_3color", u(r"3\uc0c9 \ubcfc\ud39c"), "pen"),
    ("pen_metal", u(r"\uae08\uc18d \ubcfc\ud39c"), "pen"),
    ("pen_touch", u(r"\ud130\uce58\ud39c"), "pen"),
    ("pen_highlighter", u(r"\ud615\uad11\ud39c"), "pen"),
    ("pen_sharp", u(r"\uc0e4\ud504"), "pen"),
    ("pen_fountain", u(r"\ub9cc\ub144\ud544"), "pen"),
    ("memo_postit", u(r"\ud3ec\uc2a4\ud2b8\uc787"), "memo"),
    ("memo_sticky", u(r"\uc810\ucc29 \uba54\ubaa8\uc9c0"), "memo"),
    ("memo_basic", u(r"\uba54\ubaa8\uc9c0"), "memo"),
    ("notebook", u(r"\ub178\ud2b8"), "notebook"),
    ("pocketbook", u(r"\uc218\ucca9"), "notebook"),
    ("diary_basic", u(r"\ub2e4\uc774\uc5b4\ub9ac"), "notebook"),
    ("calendar_basic", u(r"\ub2ec\ub825"), "calendar"),
    ("calendar_desk", u(r"\ud0c1\uc0c1\ub2ec\ub825"), "calendar"),
    ("calendar_alt", u(r"\uce98\ub9b0\ub354"), "calendar"),
    ("calendar_kor_alias", u(r"\uce74\ub80c\ub2e4"), "calendar"),
    ("bag_basic", u(r"\uac00\ubc29"), "bag"),
    ("bag_eco", u(r"\uc5d0\ucf54\ubc31"), "bag"),
    ("bag_eco_typo", u(r"\uc5d0\ucf54\ube7d"), "bag"),
    ("bag_shopping", u(r"\uc7a5\ubc14\uad6c\ub2c8"), "bag"),
    ("bag_reusable", u(r"\ub9ac\uc720\uc800\ube14 \ubc31"), "bag"),
    ("pouch_basic", u(r"\ud30c\uc6b0\uce58"), "pouch"),
    ("pouch_travel", u(r"\uc5ec\ud589\uc6a9 \ud30c\uc6b0\uce58"), "pouch"),
    ("pouch_cosmetic", u(r"\ud654\uc7a5\ud488 \ud30c\uc6b0\uce58"), "pouch"),
    ("cooler_bag", u(r"\ubcf4\ub0c9\ubc31"), "cooler"),
    ("lunch_cooler", u(r"\ucfe8\ub7ec\ubc31"), "cooler"),
    ("usb_basic", u(r"USB \uba54\ubaa8\ub9ac"), "usb"),
    ("usb_korean", u(r"\uc720\uc5d0\uc2a4\ube44"), "usb"),
    ("powerbank_basic", u(r"\ubcf4\uc870\ubc30\ud130\ub9ac"), "powerbank"),
    ("powerbank_battery", u(r"\ubc30\ud130\ub9ac"), "powerbank"),
    ("charger_basic", u(r"\ucda9\uc804\uae30"), "charger"),
    ("cable_basic", u(r"\ucf00\uc774\ube14"), "charger"),
    ("wireless_charger", u(r"\ubb34\uc120\ucda9\uc804\uae30"), "charger"),
    ("mousepad_basic", u(r"\ub9c8\uc6b0\uc2a4\ud328\ub4dc"), "mousepad"),
    ("mousepad_long", u(r"\uc7a5\ud328\ub4dc"), "mousepad"),
    ("deskmat", u(r"\ub370\uc2a4\ud06c\ub9e4\ud2b8"), "mousepad"),
    ("tissue_wet", u(r"\ubb3c\ud2f0\uc288"), "tissue"),
    ("tissue_box", u(r"\uac01\ud2f0\uc288"), "tissue"),
    ("tissue_basic", u(r"\ud2f0\uc288"), "tissue"),
    ("award_basic", u(r"\uc0c1\ud328"), "award"),
    ("award_crystal", u(r"\ud06c\ub9ac\uc2a4\ud0c8 \uc0c1\ud328"), "award"),
    ("award_thanks", u(r"\uac10\uc0ac\ud328"), "award"),
    ("award_trophy", u(r"\ud2b8\ub85c\ud53c"), "award"),
    ("electric_fan_basic", u(r"\uc120\ud48d\uae30"), "electric_fan"),
    ("electric_fan_hand", u(r"\uc190\uc120\ud48d\uae30"), "electric_fan"),
    ("electric_fan_desk", u(r"\ud0c1\uc0c1\uc6a9 \uc120\ud48d\uae30"), "electric_fan"),
    ("clock_basic", u(r"\uc2dc\uacc4"), "clock"),
    ("clock_desk", u(r"\ud0c1\uc0c1\uc2dc\uacc4"), "clock"),
    ("blanket_basic", u(r"\ub2f4\uc694"), "blanket"),
    ("blanket_lap", u(r"\ubb34\ub985\ub2f4\uc694"), "blanket"),
    ("health_firstaid", u(r"\uad6c\uae09\ud568"), "health"),
    ("health_band", u(r"\ubc34\ub4dc"), "health"),
    ("health_patch", u(r"\ud30c\uc2a4"), "health"),
    ("food_hanwoo", u(r"\ud55c\uc6b0 \uc120\ubb3c\uc138\ud2b8"), "food"),
    ("food_salt", u(r"\uc18c\uae08 \uc120\ubb3c\uc138\ud2b8"), "food"),
    ("food_coffee", u(r"\ucee4\ud53c"), "food"),
    ("keyring_basic", u(r"\ud0a4\ub9c1"), "keyring"),
    ("phone_stand", u(r"\ud734\ub300\ud3f0 \uac70\uce58\ub300"), "phone_stand"),
    ("speaker_bluetooth", u(r"\ube14\ub8e8\ud22c\uc2a4 \uc2a4\ud53c\ucee4"), "speaker"),
    ("calculator_basic", u(r"\uacc4\uc0b0\uae30"), "calculator"),
    ("name_tag_basic", u(r"\ub124\uc784\ud0dd"), "name_tag"),
    ("mask_basic", u(r"\ub9c8\uc2a4\ud06c"), "mask"),
]


def text_of(hit: dict[str, Any]) -> str:
    values = [
        hit.get("product_id"),
        hit.get("name"),
        hit.get("category"),
        hit.get("category_name"),
        hit.get("mall_category_name"),
        hit.get("brand"),
        hit.get("description"),
    ]
    return " ".join(str(value or "").lower() for value in values)


def matches(hit: dict[str, Any], group: str) -> bool:
    haystack = text_of(hit)
    return any(re.search(token.lower(), haystack) for token in TOKENS[group])


def p50(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return float(ordered[index])


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(row["elapsed_ms"]) for row in rows]
    wall = [float(row["wall_ms"]) for row in rows]
    return {
        "total": len(rows),
        "top1": sum(1 for row in rows if row["top1_match"]),
        "top3": sum(1 for row in rows if row["top3_match"]),
        "top8": sum(1 for row in rows if row["top8_match"]),
        "median_elapsed_ms": p50(elapsed),
        "avg_elapsed_ms": statistics.fmean(elapsed) if elapsed else 0.0,
        "p95_elapsed_ms": p95(elapsed),
        "max_elapsed_ms": max(elapsed) if elapsed else 0.0,
        "median_wall_ms": p50(wall),
        "avg_wall_ms": statistics.fmean(wall) if wall else 0.0,
        "p95_wall_ms": p95(wall),
        "max_wall_ms": max(wall) if wall else 0.0,
    }


def evaluate(service: AISearchService, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case_id, query, group in CASES:
        request = SearchRequest(mall_id="shop001", q=query, limit=8)
        started = time.perf_counter()
        response = service.search(request)
        wall_ms = (time.perf_counter() - started) * 1000.0
        hits = response.top + response.items
        row = {
            "label": label,
            "case_id": case_id,
            "query": query,
            "expected_group": group,
            "elapsed_ms": response.meta.elapsed_ms,
            "wall_ms": wall_ms,
            "top1_match": bool(hits and matches(hits[0].model_dump(), group)),
            "top3_match": any(matches(hit.model_dump(), group) for hit in hits[:3]),
            "top8_match": any(matches(hit.model_dump(), group) for hit in hits[:8]),
            "top_results": [
                {
                    "rank": index + 1,
                    "product_id": hit.product_id,
                    "name": hit.name,
                    "category": hit.category,
                    "score": hit.score,
                }
                for index, hit in enumerate(hits[:5])
            ],
        }
        rows.append(row)
    return rows


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Gemini 100-query text-to-image evaluation",
        "",
        f"- Query cases: {report['case_count']}",
        f"- Index: `{report['index_name']}`",
        f"- Backend: `{report['embedding_backend']}`",
        "",
        "## Summary",
        "",
        "| setting | Top-1 | Top-3 | Top-8 | median elapsed | avg elapsed | p95 elapsed | max elapsed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label in ("baseline", "tuned"):
        summary = report["summary"][label]
        lines.append(
            "| {label} | {top1}/{total} | {top3}/{total} | {top8}/{total} | {median:.1f}ms | {avg:.1f}ms | {p95:.1f}ms | {maxv:.1f}ms |".format(
                label=label,
                total=summary["total"],
                top1=summary["top1"],
                top3=summary["top3"],
                top8=summary["top8"],
                median=summary["median_elapsed_ms"],
                avg=summary["avg_elapsed_ms"],
                p95=summary["p95_elapsed_ms"],
                maxv=summary["max_elapsed_ms"],
            )
        )
    lines.extend(["", "## Deltas", ""])
    delta = report["delta"]
    lines.extend(
        [
            f"- Top-1: {delta['top1']:+d}",
            f"- Top-3: {delta['top3']:+d}",
            f"- Top-8: {delta['top8']:+d}",
            f"- Median elapsed: {delta['median_elapsed_ms']:+.1f}ms",
            f"- Avg elapsed: {delta['avg_elapsed_ms']:+.1f}ms",
            f"- P95 elapsed: {delta['p95_elapsed_ms']:+.1f}ms",
            f"- Max elapsed: {delta['max_elapsed_ms']:+.1f}ms",
        ]
    )
    lines.extend(["", "## Tuned Failures", ""])
    failures = [row for row in report["rows"]["tuned"] if not row["top3_match"]]
    if failures:
        lines.append("| case | query | expected | top result |")
        lines.append("| --- | --- | --- | --- |")
        for row in failures:
            top = row["top_results"][0] if row["top_results"] else {}
            lines.append(
                f"| `{row['case_id']}` | {row['query']} | {row['expected_group']} | {top.get('name', '')} |"
            )
    else:
        lines.append("No tuned Top-3 failures.")
    lines.extend(["", "## Regressions", ""])
    regressions = report["regressions"]
    if regressions:
        lines.append("| case | query | baseline top1 | tuned top1 |")
        lines.append("| --- | --- | --- | --- |")
        for row in regressions:
            lines.append(
                f"| `{row['case_id']}` | {row['query']} | {row['baseline_top1']} | {row['tuned_top1']} |"
            )
    else:
        lines.append("No Top-3 regressions found.")
    return "\n".join(lines) + "\n"


def close_service(service: AISearchService) -> None:
    close_engine = getattr(service.engine, "close", None)
    if callable(close_engine):
        close_engine()
    close_logger = getattr(service.logger, "close", None)
    if callable(close_logger):
        close_logger()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", default="reports/gemini-hybrid-100-query-evaluation.json")
    parser.add_argument("--output-md", default="reports/gemini-hybrid-100-query-evaluation.md")
    args = parser.parse_args()

    if len(CASES) != 100:
        raise RuntimeError(f"expected 100 cases, got {len(CASES)}")

    settings = load_settings()
    baseline_settings = replace(
        settings,
        cache_ttl_seconds=0,
        text_auxiliary_weight=0.0,
        text_auxiliary_search_parallelism=0,
    )
    tuned_settings = replace(
        settings,
        cache_ttl_seconds=0,
        text_auxiliary_weight=0.12,
        text_auxiliary_candidate_multiplier=1.0,
        text_auxiliary_search_parallelism=8,
    )

    baseline = AISearchService(create_search_engine(baseline_settings), baseline_settings)
    tuned = AISearchService(create_search_engine(tuned_settings), tuned_settings)
    try:
        baseline_rows = evaluate(baseline, "baseline")
        tuned_rows = evaluate(tuned, "tuned")
    finally:
        close_service(baseline)
        close_service(tuned)

    baseline_summary = summarize(baseline_rows)
    tuned_summary = summarize(tuned_rows)
    baseline_by_id = {row["case_id"]: row for row in baseline_rows}
    regressions = []
    for row in tuned_rows:
        base = baseline_by_id[row["case_id"]]
        if base["top3_match"] and not row["top3_match"]:
            regressions.append(
                {
                    "case_id": row["case_id"],
                    "query": row["query"],
                    "baseline_top1": base["top_results"][0]["name"] if base["top_results"] else "",
                    "tuned_top1": row["top_results"][0]["name"] if row["top_results"] else "",
                }
            )

    report = {
        "case_count": len(CASES),
        "index_name": settings.index_name,
        "embedding_backend": settings.embedding_backend,
        "summary": {"baseline": baseline_summary, "tuned": tuned_summary},
        "delta": {
            "top1": tuned_summary["top1"] - baseline_summary["top1"],
            "top3": tuned_summary["top3"] - baseline_summary["top3"],
            "top8": tuned_summary["top8"] - baseline_summary["top8"],
            "median_elapsed_ms": tuned_summary["median_elapsed_ms"] - baseline_summary["median_elapsed_ms"],
            "avg_elapsed_ms": tuned_summary["avg_elapsed_ms"] - baseline_summary["avg_elapsed_ms"],
            "p95_elapsed_ms": tuned_summary["p95_elapsed_ms"] - baseline_summary["p95_elapsed_ms"],
            "max_elapsed_ms": tuned_summary["max_elapsed_ms"] - baseline_summary["max_elapsed_ms"],
        },
        "regressions": regressions,
        "rows": {"baseline": baseline_rows, "tuned": tuned_rows},
    }

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(markdown_report(report), encoding="utf-8")

    print(json.dumps({"summary": report["summary"], "delta": report["delta"], "regressions": len(regressions)}, ensure_ascii=False, indent=2))
    failures = [row for row in tuned_rows if not row["top3_match"]]
    if failures:
        print("TUNED_TOP3_FAILURES")
        for row in failures:
            top = row["top_results"][0] if row["top_results"] else {}
            print(f"{row['case_id']}\t{row['query']}\t{row['expected_group']}\t{top.get('name', '')}")


if __name__ == "__main__":
    main()
