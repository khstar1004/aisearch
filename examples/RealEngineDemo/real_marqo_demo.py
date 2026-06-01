#!/usr/bin/env python3
import argparse
import json
import random
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


TENSOR_FIELDS = ["title", "description", "category"]
ATTRIBUTES = ["title", "description", "category", "brand", "color", "price", "popularity"]

PRODUCTS = {
    "outerwear": ["rain jacket", "fleece hoodie", "wool coat", "shell parka", "bomber jacket"],
    "footwear": ["trail running shoe", "leather boot", "white sneaker", "hiking sandal", "training shoe"],
    "electronics": ["wireless earbuds", "noise cancelling headphones", "smart watch", "portable speaker", "camera bag"],
    "home": ["espresso machine", "ceramic vase", "linen bedding", "desk lamp", "office chair"],
    "beauty": ["mineral sunscreen", "hydrating serum", "matte lipstick", "daily moisturizer", "cleanser gel"],
}

COLORS = ["black", "navy", "red", "white", "green", "silver", "tan", "charcoal", "blue", "cream"]
BRANDS = ["Aster", "Northline", "Kura", "Motive", "Solace", "Oakfield", "Vela", "Ridge"]
QUALITIES = [
    "lightweight", "waterproof", "breathable", "premium", "compact", "durable", "soft",
    "minimal", "high grip", "quick dry", "noise reducing", "daily use"
]
USE_CASES = [
    "for commuting", "for rainy trail runs", "for small apartments", "for sensitive skin",
    "for long flights", "for gym training", "for weekend travel", "for office work",
    "for holiday gifting", "for mountain hiking"
]

QUERIES = [
    "waterproof gear for running in rain",
    "comfortable office chair for long work days",
    "black jacket with durable pockets",
    "daily sunscreen for sensitive skin",
    "wireless noise cancelling earbuds",
    "espresso machine for home coffee",
]


class MarqoHTTP:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, payload=None, params=None, timeout=300):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                ms = (time.perf_counter() - t0) * 1000
                return resp.status, json.loads(raw) if raw else {}, ms
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {url} -> HTTP {exc.code}\n{raw}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach {url}: {exc.reason}") from exc


def make_docs(count: int, seed: int):
    rng = random.Random(seed)
    docs = []
    categories = list(PRODUCTS.keys())
    for i in range(count):
        category = rng.choice(categories)
        product = rng.choice(PRODUCTS[category])
        color = rng.choice(COLORS)
        brand = rng.choice(BRANDS)
        quality = rng.choice(QUALITIES)
        use_case = rng.choice(USE_CASES)
        suffix = "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(5))
        title = f"{brand} {color} {product}"
        description = f"{quality} {product} {use_case}. Built for search relevance demo item {i}."
        docs.append(
            {
                "_id": f"demo-{i:05d}-{suffix}",
                "title": title,
                "description": description,
                "category": category,
                "brand": brand,
                "color": color,
                "price": round(rng.uniform(12, 420), 2),
                "popularity": rng.randint(1, 1000),
            }
        )
    return docs


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def wait_for_api(client: MarqoHTTP, seconds: int):
    deadline = time.time() + seconds
    last_error = None
    while time.time() < deadline:
        try:
            status, data, ms = client.request("GET", "/", timeout=5)
            print(f"API ready: {data.get('message')} version={data.get('version')} ({ms:.0f} ms)")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(3)
    raise RuntimeError(f"Marqo API did not become ready in {seconds}s. Last error: {last_error}")


def index_settings(engine: str, model: str, dims: int):
    if engine == "random":
        return {
            "model": model,
            "modelProperties": {
                "name": model,
                "type": "random",
                "dimensions": dims,
            },
            "normalizeEmbeddings": True,
        }
    return {
        "model": model,
        "normalizeEmbeddings": True,
    }


def recreate_index(client: MarqoHTTP, index_name: str, settings: dict):
    try:
        _, _, ms = client.request("DELETE", f"/indexes/{index_name}", timeout=120)
        print(f"Deleted old index {index_name} ({ms:.0f} ms)")
        time.sleep(2)
    except Exception:
        pass
    _, data, ms = client.request("POST", f"/indexes/{index_name}", settings, timeout=300)
    print(f"Created index {data.get('index', index_name)} ({ms:.0f} ms)")


def add_documents(client: MarqoHTTP, index_name: str, docs: list, batch_size: int):
    total_start = time.perf_counter()
    batch_times = []
    for n, batch in enumerate(chunks(docs, batch_size), start=1):
        payload = {"documents": batch, "tensorFields": TENSOR_FIELDS}
        _, data, ms = client.request("POST", f"/indexes/{index_name}/documents", payload, timeout=600)
        batch_times.append(ms)
        errors = data.get("errors", False)
        print(f"Batch {n:02d}: {len(batch):4d} docs in {ms:8.1f} ms errors={errors}")
    total_s = time.perf_counter() - total_start
    print(f"Indexed {len(docs)} docs in {total_s:.2f}s ({len(docs) / total_s:.1f} docs/sec)")
    print(f"Batch latency: min={min(batch_times):.1f} ms p50={sorted(batch_times)[len(batch_times)//2]:.1f} ms max={max(batch_times):.1f} ms")


def run_searches(client: MarqoHTTP, index_name: str, methods: list[str], rounds: int):
    for method in methods:
        latencies = []
        print(f"\n{method} search")
        for i in range(rounds):
            q = QUERIES[i % len(QUERIES)]
            payload = {
                "q": q,
                "searchMethod": method,
                "limit": 5,
                "attributesToRetrieve": ATTRIBUTES,
            }
            _, data, ms = client.request("POST", f"/indexes/{index_name}/search", payload, timeout=300)
            latencies.append(ms)
            top = (data.get("hits") or [{}])[0]
            print(f"  {ms:7.1f} ms | q={q!r}")
            print(f"            top={top.get('title')} score={top.get('_score')}")
        latencies_sorted = sorted(latencies)
        print(
            f"  summary: min={latencies_sorted[0]:.1f} ms "
            f"p50={latencies_sorted[len(latencies_sorted)//2]:.1f} ms "
            f"max={latencies_sorted[-1]:.1f} ms"
        )


def main():
    parser = argparse.ArgumentParser(description="Real Marqo engine demo over REST")
    parser.add_argument("--host", default="http://localhost:8882")
    parser.add_argument("--index", default="marqo-real-engine-demo")
    parser.add_argument("--docs", type=int, default=2000)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--engine", choices=["random", "semantic"], default="random")
    parser.add_argument("--model", default=None)
    parser.add_argument("--dims", type=int, default=384)
    parser.add_argument("--wait", type=int, default=240)
    args = parser.parse_args()

    model = args.model or ("demo/random-384" if args.engine == "random" else "hf/e5-base-v2")
    client = MarqoHTTP(args.host)

    wait_for_api(client, args.wait)
    recreate_index(client, args.index, index_settings(args.engine, model, args.dims))
    docs = make_docs(args.docs, args.seed)
    add_documents(client, args.index, docs, args.batch_size)

    try:
        _, stats, ms = client.request("GET", f"/indexes/{args.index}/stats", timeout=120)
        print(f"\nStats ({ms:.0f} ms): {json.dumps(stats, indent=2)}")
    except Exception as exc:
        print(f"\nStats unavailable: {exc}")

    methods = ["TENSOR", "LEXICAL", "HYBRID"]
    run_searches(client, args.index, methods, args.rounds)
    print("\nDone. Re-run with --docs 10000 to feel larger-index behavior.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
