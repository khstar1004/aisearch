#!/usr/bin/env python3
import argparse
import csv
import json
import pathlib
import statistics
import time
import urllib.request
from collections import Counter, defaultdict


DEFAULT_QWEN_URL = "http://localhost:8111"
DEFAULT_CATALOG = pathlib.Path(__file__).resolve().parent / "data" / "gift_url_products.csv"
DEFAULT_OUTPUT_DIR = pathlib.Path(__file__).resolve().parent / "results"


def read_catalog(path, max_docs):
    docs = []
    with pathlib.Path(path).open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if not row.get("image_url") or not row.get("title") or not row.get("category"):
                continue
            docs.append(
                {
                    "id": row.get("id") or f"doc-{len(docs) + 1}",
                    "title": row["title"],
                    "category": row["category"],
                    "tags": row.get("tags", ""),
                    "image_url": row["image_url"],
                    "source_url": row.get("source_url", ""),
                }
            )
            if max_docs and len(docs) >= max_docs:
                break
    return docs


def read_queries(path):
    if not path:
        return None
    queries = []
    with pathlib.Path(path).open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if row.get("query") and row.get("category"):
                queries.append({"query": row["query"], "category": row["category"]})
    return queries


def generate_queries(docs, min_docs_per_category, max_categories):
    counts = Counter(doc["category"] for doc in docs)
    categories = [
        category
        for category, count in counts.most_common()
        if count >= min_docs_per_category
    ][:max_categories]
    return [
        {"query": f"{category} 판촉물", "category": category}
        for category in categories
    ]


def doc_text(doc):
    return "\n".join(
        value
        for value in [
            doc.get("title", ""),
            doc.get("category", ""),
            doc.get("tags", ""),
        ]
        if value
    )


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def post_embed(qwen_url, inputs, prompt=None, prompt_name=None, batch_size=8, timeout=1800):
    vectors = []
    elapsed = 0.0
    for batch in chunks(inputs, batch_size):
        payload = {"inputs": batch}
        if prompt is not None:
            payload["prompt"] = prompt
        if prompt_name is not None:
            payload["promptName"] = prompt_name
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            qwen_url.rstrip("/") + "/embed",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        vectors.extend(data["embeddings"])
        elapsed += float(data.get("elapsedMs", 0.0))
    return vectors, round(elapsed, 1)


def optional_arg(value):
    return value if value else None


def dot(lhs, rhs):
    return sum(a * b for a, b in zip(lhs, rhs))


def rank(scores):
    return sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)


def reciprocal_rank(order, relevant):
    for result_rank, idx in enumerate(order, start=1):
        if idx in relevant:
            return 1.0 / result_rank
    return 0.0


def recall_at(order, relevant, k):
    return 1.0 if any(idx in relevant for idx in order[:k]) else 0.0


def precision_at(order, relevant, k):
    return sum(1 for idx in order[:k] if idx in relevant) / min(k, len(order))


def average(values):
    return round(sum(values) / len(values), 4) if values else 0.0


def evaluate_text(docs, queries, query_vectors, vector_sets):
    relevant_by_category = defaultdict(set)
    for idx, doc in enumerate(docs):
        relevant_by_category[doc["category"]].add(idx)

    scorers = {
        "text_vector_only": lambda q_idx: [dot(query_vectors[q_idx], vector) for vector in vector_sets["text"]],
        "image_vector_cross_modal": lambda q_idx: [dot(query_vectors[q_idx], vector) for vector in vector_sets["image"]],
        "fused_text_0.75_image_0.25": lambda q_idx: [
            0.75 * dot(query_vectors[q_idx], text_vector) + 0.25 * dot(query_vectors[q_idx], image_vector)
            for text_vector, image_vector in zip(vector_sets["text"], vector_sets["image"])
        ],
    }
    if "multimodal" in vector_sets:
        scorers["multimodal_text_image_vector"] = lambda q_idx: [
            dot(query_vectors[q_idx], vector) for vector in vector_sets["multimodal"]
        ]

    results = {}
    for name, scorer in scorers.items():
        mrr = []
        r1 = []
        r5 = []
        p5 = []
        examples = []
        for q_idx, query in enumerate(queries):
            relevant = relevant_by_category[query["category"]]
            order = rank(scorer(q_idx))
            mrr.append(reciprocal_rank(order, relevant))
            r1.append(recall_at(order, relevant, 1))
            r5.append(recall_at(order, relevant, 5))
            p5.append(precision_at(order, relevant, 5))
            examples.append(
                {
                    "query": query["query"],
                    "category": query["category"],
                    "top5": [
                        {
                            "id": docs[idx]["id"],
                            "category": docs[idx]["category"],
                            "title": docs[idx]["title"],
                        }
                        for idx in order[:5]
                    ],
                }
            )
        results[name] = {
            "mrr": average(mrr),
            "recall@1": average(r1),
            "recall@5": average(r5),
            "precision@5": average(p5),
            "examples": examples,
        }
    return results


def evaluate_image(docs, vector_sets):
    candidates = {
        "image_vector_only": vector_sets["image"],
        "text_vector_only": vector_sets["text"],
    }
    if "multimodal" in vector_sets:
        candidates["multimodal_text_image_vector"] = vector_sets["multimodal"]
    results = {}
    for name, candidate_vectors in candidates.items():
        exact_at_1 = []
        self_mrr = []
        category_r5 = []
        self_scores = []
        for query_idx, query_vector in enumerate(vector_sets["image"]):
            scores = [dot(query_vector, vector) for vector in candidate_vectors]
            order = rank(scores)
            relevant_category = {
                idx for idx, doc in enumerate(docs)
                if doc["category"] == docs[query_idx]["category"]
            }
            exact_at_1.append(1.0 if order[0] == query_idx else 0.0)
            self_mrr.append(reciprocal_rank(order, {query_idx}))
            category_r5.append(recall_at(order, relevant_category, 5))
            self_scores.append(scores[query_idx])
        results[name] = {
            "exact@1": average(exact_at_1),
            "self_mrr": average(self_mrr),
            "category_recall@5": average(category_r5),
            "mean_self_score": round(statistics.mean(self_scores), 4),
            "min_self_score": round(min(self_scores), 4),
        }
    return results


def best_by(results, metric):
    return max(results.items(), key=lambda item: item[1][metric])


def write_report(path, payload):
    text_name, text_metrics = best_by(payload["text_search"], "mrr")
    image_name, image_metrics = best_by(payload["image_search"], "exact@1")
    lines = [
        "# Catalog Search Experiment",
        "",
        f"- Catalog: `{payload['catalog']}`",
        f"- Model: `{payload['model']}`",
        f"- Documents: `{payload['document_count']}`",
        f"- Text queries: `{payload['query_count']}`",
        f"- Best text architecture: `{text_name}` "
        f"(MRR {text_metrics['mrr']}, R@1 {text_metrics['recall@1']}, P@5 {text_metrics['precision@5']})",
        f"- Best image architecture: `{image_name}` "
        f"(Exact@1 {image_metrics['exact@1']}, category R@5 {image_metrics['category_recall@5']}, "
        f"mean self score {image_metrics['mean_self_score']})",
        "",
        "## Text Search",
        "",
        "| architecture | MRR | R@1 | R@5 | P@5 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in sorted(payload["text_search"].items()):
        lines.append(
            f"| `{name}` | {metrics['mrr']} | {metrics['recall@1']} | "
            f"{metrics['recall@5']} | {metrics['precision@5']} |"
        )
    lines += [
        "",
        "## Image Search",
        "",
        "| architecture | Exact@1 | Self MRR | Category R@5 | Mean self score | Min self score |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in sorted(payload["image_search"].items()):
        lines.append(
            f"| `{name}` | {metrics['exact@1']} | {metrics['self_mrr']} | "
            f"{metrics['category_recall@5']} | {metrics['mean_self_score']} | {metrics['min_self_score']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-csv", default=str(DEFAULT_CATALOG))
    parser.add_argument("--queries-csv")
    parser.add_argument("--qwen-url", default=DEFAULT_QWEN_URL)
    parser.add_argument("--model-label", default="Qwen/Qwen3-VL-Embedding-2B")
    parser.add_argument(
        "--query-prompt",
        default="Retrieve relevant ecommerce product images for the user query.",
    )
    parser.add_argument("--query-prompt-name")
    parser.add_argument(
        "--multimodal-prompt",
        default="Represent the ecommerce product for multilingual image and product search.",
    )
    parser.add_argument("--multimodal-prompt-name")
    parser.add_argument("--max-docs", type=int, default=80)
    parser.add_argument("--min-docs-per-category", type=int, default=2)
    parser.add_argument("--max-categories", type=int, default=20)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--skip-multimodal", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    docs = read_catalog(args.catalog_csv, args.max_docs)
    queries = read_queries(args.queries_csv) or generate_queries(
        docs,
        args.min_docs_per_category,
        args.max_categories,
    )
    if not docs:
        raise RuntimeError("catalog has no usable documents")
    if not queries:
        raise RuntimeError("no text queries generated; lower --min-docs-per-category or provide --queries-csv")

    query_vectors, query_ms = post_embed(
        args.qwen_url,
        [{"text": query["query"]} for query in queries],
        prompt=None if args.query_prompt_name else optional_arg(args.query_prompt),
        prompt_name=optional_arg(args.query_prompt_name),
        batch_size=8,
    )
    text_vectors, text_doc_ms = post_embed(
        args.qwen_url,
        [{"text": doc_text(doc)} for doc in docs],
        batch_size=16,
    )
    image_vectors, image_doc_ms = post_embed(
        args.qwen_url,
        [{"image": doc["image_url"]} for doc in docs],
        batch_size=4,
    )
    vector_sets = {"text": text_vectors, "image": image_vectors}
    multimodal_doc_ms = None
    if not args.skip_multimodal:
        multimodal_vectors, multimodal_doc_ms = post_embed(
            args.qwen_url,
            [{"text": doc_text(doc), "image": doc["image_url"]} for doc in docs],
            prompt=None if args.multimodal_prompt_name else optional_arg(args.multimodal_prompt),
            prompt_name=optional_arg(args.multimodal_prompt_name),
            batch_size=4,
        )
        vector_sets["multimodal"] = multimodal_vectors

    payload = {
        "catalog": str(args.catalog_csv),
        "model": args.model_label,
        "document_count": len(docs),
        "query_count": len(queries),
        "categories": sorted(Counter(doc["category"] for doc in docs).items(), key=lambda item: (-item[1], item[0])),
        "timingMs": {
            "queryTextVectors": query_ms,
            "docTextVectors": text_doc_ms,
            "docImageVectors": image_doc_ms,
            "docMultimodalVectors": multimodal_doc_ms,
            "totalWall": round((time.perf_counter() - started) * 1000, 1),
        },
        "text_search": evaluate_text(docs, queries, query_vectors, vector_sets),
        "image_search": evaluate_image(docs, vector_sets),
    }

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "catalog_eval_results.json"
    report_path = output_dir / "catalog_eval_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(report_path, payload)

    text_name, text_metrics = best_by(payload["text_search"], "mrr")
    image_name, image_metrics = best_by(payload["image_search"], "exact@1")
    print(f"wrote {json_path}")
    print(f"wrote {report_path}")
    print(
        f"best text={text_name} mrr={text_metrics['mrr']} "
        f"r1={text_metrics['recall@1']} p5={text_metrics['precision@5']}"
    )
    print(
        f"best image={image_name} exact@1={image_metrics['exact@1']} "
        f"mean_self={image_metrics['mean_self_score']}"
    )
    print(json.dumps(payload["timingMs"], ensure_ascii=False))


if __name__ == "__main__":
    main()
