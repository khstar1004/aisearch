from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass

from commerce_ai_search.engine import EngineQuery, LocalSearchEngine
from commerce_ai_search.models import ProductDocument


PRODUCT_TEMPLATES = (
    ("UM", "검정 3단 자동 우산", "우산", ["검정", "블랙", "접이식"]),
    ("TB", "스텐 진공 보온 텀블러", "텀블러", ["스텐", "보틀", "보온병"]),
    ("TW", "송월 타올 답례품 세트", "타올", ["송월", "수건", "답례품"]),
    ("PEN", "로고 인쇄 볼펜", "볼펜", ["펜", "실크인쇄"]),
    ("BAG", "무지 에코백 장바구니", "에코백", ["면", "캔버스"]),
    ("CAL", "탁상 달력 캘린더", "달력", ["카렌다", "calendar"]),
    ("FAN", "휴대용 손선풍기", "선풍기", ["핸디선풍기"]),
    ("BAT", "고속충전 보조배터리", "보조배터리", ["powerbank"]),
)
DEFAULT_QUERIES = ("검정우산", "스텐텀블러", "송월타올", "에코백", "보조배터리")


@dataclass(frozen=True)
class QueryBenchmark:
    query: str
    iterations: int
    p50_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float
    hit_count: int
    first_product_id: str | None


@dataclass(frozen=True)
class SizeBenchmark:
    product_count: int
    build_ms: float
    queries: list[QueryBenchmark]


def build_products(count: int) -> list[ProductDocument]:
    products = []
    for index in range(max(0, count)):
        prefix, name, category, keywords = PRODUCT_TEMPLATES[index % len(PRODUCT_TEMPLATES)]
        product_id = f"{prefix}-{index:06d}"
        products.append(
            ProductDocument.model_validate(
                {
                    "product_id": product_id,
                    "product_name": f"{name} {index}",
                    "category_name": category,
                    "price": 1000 + (index % 100) * 100,
                    "main_image_url": f"https://cdn.example.com/{product_id}.jpg",
                    "product_url": f"https://shop001.example.com/product/{product_id}",
                    "mall_id": "shop001",
                    "status": "active",
                    "display_yn": "Y",
                    "keywords": keywords,
                }
            )
        )
    return products


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percent)))
    return ordered[index]


def benchmark_query(engine: LocalSearchEngine, query: str, iterations: int, limit: int) -> QueryBenchmark:
    durations = []
    hit_count = 0
    first_product_id = None
    for _ in range(iterations):
        started = time.perf_counter()
        hits = engine.search(EngineQuery(q=query, mall_id="shop001", strict_mall_filter=True, limit=limit))
        durations.append((time.perf_counter() - started) * 1000)
        hit_count = len(hits)
        first_product_id = hits[0].document.product_id if hits else None
    return QueryBenchmark(
        query=query,
        iterations=iterations,
        p50_ms=round(statistics.median(durations), 3),
        p95_ms=round(percentile(durations, 0.95), 3),
        min_ms=round(min(durations), 3),
        max_ms=round(max(durations), 3),
        hit_count=hit_count,
        first_product_id=first_product_id,
    )


def run_benchmark(sizes: list[int], queries: tuple[str, ...], iterations: int, limit: int) -> list[SizeBenchmark]:
    results = []
    for size in sizes:
        started = time.perf_counter()
        engine = LocalSearchEngine(build_products(size))
        build_ms = round((time.perf_counter() - started) * 1000, 3)
        results.append(
            SizeBenchmark(
                product_count=size,
                build_ms=build_ms,
                queries=[benchmark_query(engine, query, iterations, limit) for query in queries],
            )
        )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark LocalSearchEngine p50/p95 query latency.")
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10000])
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--queries", nargs="+", default=list(DEFAULT_QUERIES))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = run_benchmark(
        sizes=[size for size in args.sizes if size > 0],
        queries=tuple(str(query) for query in args.queries if str(query).strip()),
        iterations=max(1, int(args.iterations)),
        limit=max(1, int(args.limit)),
    )
    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
