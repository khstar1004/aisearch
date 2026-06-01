from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.category_intent import infer_category_intents
from app.config import Settings, load_settings
from app.engine import MarqoSearchEngine, expanded_query_text
from app.query_normalizer import build_search_query, normalize_query_text
from app.sync import CsvProductSource
from app.category_intent import append_inferred_categories


TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
COMMON_COLOR_TERMS = [
    "검정",
    "검은색",
    "블랙",
    "흰색",
    "화이트",
    "노란색",
    "노랑",
    "옐로",
    "파란색",
    "파랑",
    "블루",
    "빨간색",
    "레드",
    "초록",
    "그린",
    "분홍",
    "핑크",
    "회색",
    "그레이",
    "투명",
]
COMMON_MODIFIER_TERMS = ["고급", "저가", "답례품", "판촉", "선물", "세트", "휴대용", "대형", "미니"]


def build_candidates(products: Iterable[object]) -> list[str]:
    terms: set[str] = set()
    for product in products:
        name = str(getattr(product, "name", "") or "").strip()
        category = str(getattr(product, "category", "") or "").strip()
        description = str(getattr(product, "description", "") or "").strip()
        keywords = [str(item).strip() for item in getattr(product, "keywords", ()) if str(item).strip()]
        colors = [str(item).strip() for item in getattr(product, "colors", ()) if str(item).strip()]
        materials = [str(item).strip() for item in getattr(product, "materials", ()) if str(item).strip()]

        add_term(terms, name)
        add_term(terms, category)
        if category:
            for color in COMMON_COLOR_TERMS:
                add_term(terms, f"{color} {category}")
            for modifier in COMMON_MODIFIER_TERMS:
                add_term(terms, f"{modifier} {category}")
        for keyword in keywords:
            add_term(terms, keyword)
        for text in [name, description, *keywords]:
            for token in TOKEN_PATTERN.findall(text):
                add_term(terms, token)
        if category:
            for color in colors:
                add_term(terms, f"{color} {category}")
            for material in materials:
                add_term(terms, f"{material} {category}")
    return sorted(terms)


def add_term(terms: set[str], text: str) -> None:
    value = " ".join(str(text or "").split()).strip()
    if 2 <= len(value) <= 100:
        terms.add(value)


def runtime_query_text(raw_text: str) -> str:
    normalized = normalize_query_text(raw_text)
    search_query = build_search_query(raw_text, normalized)
    expanded_query = expanded_query_text(search_query, {})
    inferred = infer_category_intents(normalized or raw_text)
    return append_inferred_categories(expanded_query, inferred)


def unique_runtime_queries(candidates: Iterable[str], max_queries: int | None) -> list[str]:
    seen: set[str] = set()
    queries: list[str] = []
    for candidate in candidates:
        query = runtime_query_text(candidate)
        key = query.casefold()
        if not query or key in seen:
            continue
        seen.add(key)
        queries.append(query)
        if max_queries is not None and len(queries) >= max_queries:
            break
    return queries


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def build_cache(settings: Settings, queries: list[str], chunk_size: int, precision: int | None) -> dict[str, object]:
    embedding_backend = str(settings.embedding_backend or "").strip().lower()
    if embedding_backend not in {"gemini", "qwen"}:
        raise ValueError("query embedding cache requires HAEORUM_EMBEDDING_BACKEND=gemini or qwen")
    engine = MarqoSearchEngine(
        settings.marqo_url,
        settings.index_name,
        settings.marqo_model,
        embedding_backend=embedding_backend,
        qwen_embedding_url=settings.qwen_embedding_url,
        qwen_embedding_dimensions=settings.qwen_embedding_dimensions,
        qwen_model=settings.qwen_model,
    )
    started = time.perf_counter()
    items = []
    for batch in chunks(queries, chunk_size):
        vectors = engine.qwen_embed_query_texts(batch)
        items.extend(
            {"text": text, "vector": compact_vector(vector, precision)}
            for text, vector in zip(batch, vectors)
        )
    return {
        "ok": True,
        "embedding_backend": embedding_backend,
        "provider": embedding_backend,
        "model": settings.qwen_model,
        "dimensions": settings.qwen_embedding_dimensions,
        "prompt": "Retrieve relevant ecommerce product images for the user query.",
        "query_count": len(items),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "items": items,
    }


def compact_vector(vector: list[float], precision: int | None) -> list[float]:
    if precision is None:
        return vector
    return [round(float(value), precision) for value in vector]


def write_cache(path: Path, report: dict[str, object], compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    indent = None if compact else 2
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, separators=(",", ":") if compact else None, indent=indent)
        return
    path.write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":") if compact else None, indent=indent),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a precomputed query embedding cache from product data.")
    parser.add_argument("--product-csv", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-queries", type=int, default=20000, help="Maximum cached query count. Use 0 for no cap.")
    parser.add_argument("--chunk-size", type=int, default=64)
    parser.add_argument("--precision", type=int, default=6, help="Decimal places for cached vectors. Use -1 to keep raw floats.")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON. A .gz output path writes gzip-compressed JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    product_csv = args.product_csv or settings.product_csv_path
    products = CsvProductSource(product_csv).fetch_all()
    candidates = build_candidates(products)
    max_queries = None if args.max_queries == 0 else args.max_queries
    precision = None if args.precision < 0 else args.precision
    queries = unique_runtime_queries(candidates, max_queries)
    report = build_cache(settings, queries, max(1, args.chunk_size), precision)
    write_cache(args.output, report, args.compact)
    print(json.dumps({key: value for key, value in report.items() if key != "items"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
