#!/usr/bin/env python3
import argparse
import csv
import hashlib
import http.server
import io
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parent
DEFAULT_INDEX = os.environ.get("IMAGE_INDEX_NAME", "marqo-ecommerce-image-demo")
MODEL_NAME = os.environ.get(
    "IMAGE_DEMO_MODEL_NAME", "Marqo/marqo-ecommerce-embeddings-L"
)
EMBEDDING_BACKEND = os.environ.get("IMAGE_EMBEDDING_BACKEND", "marqo").lower()
QWEN_URL = os.environ.get("QWEN_EMBEDDING_URL", "http://qwen:8098").rstrip("/")
QWEN_DIMENSIONS = int(os.environ.get("QWEN_EMBEDDING_DIMENSIONS", "2048"))
QWEN_TEXT_VECTOR_FIELD = "qwen_text_vector"
QWEN_IMAGE_VECTOR_FIELD = "qwen_image_vector"
CATALOG_URL = os.environ.get(
    "IMAGE_CATALOG_URL",
    "https://marqo-overall-demo-assets.s3.us-west-2.amazonaws.com/ecommerce_meta_data.csv",
)
PRODUCT_BUCKETS = [
    "yellow",
    "red",
    "blue",
    "green",
    "black",
    "white",
    "beige",
    "brown",
    "pink",
    "shirt",
    "shoe",
    "sneaker",
    "belt",
    "handbag",
    "dress",
    "watch",
]
KOREAN_QUERY_TERMS = [
    ("노란색", "yellow"),
    ("노랑", "yellow"),
    ("노란", "yellow"),
    ("빨간색", "red"),
    ("빨강", "red"),
    ("빨간", "red"),
    ("파란색", "blue"),
    ("파랑", "blue"),
    ("파란", "blue"),
    ("초록색", "green"),
    ("초록", "green"),
    ("녹색", "green"),
    ("검은색", "black"),
    ("검정", "black"),
    ("검은", "black"),
    ("하얀색", "white"),
    ("흰색", "white"),
    ("하얀", "white"),
    ("흰", "white"),
    ("베이지", "beige"),
    ("갈색", "brown"),
    ("분홍", "pink"),
    ("핑크", "pink"),
    ("보라", "purple"),
    ("주황", "orange"),
    ("남성", "men"),
    ("남자", "men"),
    ("여성", "women"),
    ("여자", "women"),
    ("신발", "shoes"),
    ("운동화", "sneakers"),
    ("스니커즈", "sneakers"),
    ("구두", "shoes"),
    ("벨트", "belt"),
    ("셔츠", "shirt"),
    ("상의", "topwear"),
    ("원피스", "dress"),
    ("드레스", "dress"),
    ("가방", "bag"),
    ("핸드백", "handbag"),
    ("백팩", "backpack"),
    ("시계", "watch"),
    ("캐주얼", "casual"),
    ("겨울", "winter"),
    ("여름", "summer"),
    ("가을", "fall"),
    ("봄", "spring"),
]


def json_dumps(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class ImageDemoHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, marqo_url: str, **kwargs):
        self.marqo_url = marqo_url.rstrip("/")
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self.handle_api()
            return
        if self.path.startswith("/marqo/"):
            self.proxy_marqo()
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self.handle_api()
            return
        self.proxy_marqo()

    def do_DELETE(self):
        self.proxy_marqo()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, status, data):
        payload = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def handle_api(self):
        path = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(path.query)
        try:
            if path.path == "/api/health":
                _, data, ms = self.marqo_request("GET", "/", timeout=20)
                stats = None
                try:
                    _, stats, _ = self.marqo_request("GET", f"/indexes/{DEFAULT_INDEX}/stats", timeout=30)
                except Exception:
                    pass
                embedding = None
                if EMBEDDING_BACKEND == "qwen":
                    try:
                        embedding = self.qwen_request("GET", "/health", timeout=20)
                    except Exception as exc:
                        embedding = {"ready": False, "message": str(exc)}
                self.send_json(
                    200,
                    {
                        "marqo": data,
                        "ms": ms,
                        "index": DEFAULT_INDEX,
                        "model": MODEL_NAME,
                        "backend": EMBEDDING_BACKEND,
                        "embedding": embedding,
                        "stats": stats,
                    },
                )
            elif path.path == "/api/crawl":
                count = int(query.get("count", ["100"])[0])
                self.send_json(200, {"documents": crawl_product_catalog(count)})
            elif path.path == "/api/setup":
                body = self.read_json()
                documents = body.get("documents") or crawl_product_catalog(int(body.get("count", 100)))
                reset = bool(body.get("reset", True))
                result = self.setup_index(body.get("index") or DEFAULT_INDEX, documents, reset=reset)
                self.send_json(200, result)
            elif path.path == "/api/search":
                body = self.read_json()
                result = self.search_index(
                    body.get("index") or DEFAULT_INDEX,
                    body.get("q"),
                    int(body.get("limit", 12)),
                    bool(body.get("imageQuery", False)),
                )
                self.send_json(200, result)
            else:
                self.send_json(404, {"message": "unknown api endpoint"})
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {"message": payload}
            self.send_json(exc.code, data)
        except Exception as exc:
            self.send_json(500, {"message": str(exc), "type": exc.__class__.__name__})

    def setup_index(self, index_name, documents, reset=True):
        if EMBEDDING_BACKEND == "qwen":
            return self.setup_qwen_index(index_name, documents, reset=reset)
        return self.setup_marqo_index(index_name, documents, reset=reset)

    def setup_marqo_index(self, index_name, documents, reset=True):
        started = time.perf_counter()
        events = []
        if reset:
            try:
                _, _, ms = self.marqo_request("DELETE", f"/indexes/{index_name}", timeout=180)
                events.append({"step": "delete", "ms": ms})
                time.sleep(2)
            except Exception as exc:
                events.append({"step": "delete-skipped", "message": str(exc)})

        settings = {
            "model": MODEL_NAME,
            "treatUrlsAndPointersAsImages": True,
            "normalizeEmbeddings": True,
        }
        _, created, ms = self.marqo_request("POST", f"/indexes/{index_name}", settings, timeout=600)
        events.append({"step": "create-index", "ms": ms, "response": created})

        batch_results = []
        for number, batch in enumerate(chunks(documents, 8), start=1):
            payload = {
                "documents": batch,
                "tensorFields": ["image_url", "title", "caption", "category"],
            }
            _, data, ms = self.marqo_request(
                "POST",
                f"/indexes/{index_name}/documents",
                payload,
                timeout=1800,
            )
            batch_results.append(
                {
                    "batch": number,
                    "docs": len(batch),
                    "ms": ms,
                    "errors": data.get("errors", False),
                    "items": len(data.get("items", [])),
                }
            )

        _, stats, stats_ms = self.marqo_request("GET", f"/indexes/{index_name}/stats", timeout=180)
        return {
            "index": index_name,
            "model": MODEL_NAME,
            "documents": len(documents),
            "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
            "events": events,
            "batches": batch_results,
            "stats": stats,
            "statsMs": stats_ms,
        }

    def setup_qwen_index(self, index_name, documents, reset=True):
        started = time.perf_counter()
        events = []
        if reset:
            try:
                _, _, ms = self.marqo_request("DELETE", f"/indexes/{index_name}", timeout=180)
                events.append({"step": "delete", "ms": ms})
                time.sleep(2)
            except Exception as exc:
                events.append({"step": "delete-skipped", "message": str(exc)})

        settings = {
            "type": "structured",
            "model": "no_model",
            "modelProperties": {
                "dimensions": QWEN_DIMENSIONS,
                "type": "no_model",
            },
            "normalizeEmbeddings": False,
            "allFields": [
                {"name": QWEN_TEXT_VECTOR_FIELD, "type": "custom_vector", "features": ["lexical_search", "filter"]},
                {"name": QWEN_IMAGE_VECTOR_FIELD, "type": "custom_vector", "features": ["lexical_search", "filter"]},
                {"name": "image_url", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "title", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "source", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "category", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "caption", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "gender", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "season", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "usage", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "price", "type": "float"},
                {"name": "aesthetic_score", "type": "float"},
            ],
            "tensorFields": [QWEN_TEXT_VECTOR_FIELD, QWEN_IMAGE_VECTOR_FIELD],
            "annParameters": {
                "spaceType": "prenormalized-angular",
                "parameters": {"efConstruction": 512, "m": 16},
            },
        }
        _, created, ms = self.marqo_request("POST", f"/indexes/{index_name}", settings, timeout=600)
        events.append({"step": "create-index", "ms": ms, "response": created})

        batch_results = []
        for number, batch in enumerate(chunks(documents, 4), start=1):
            vector_started = time.perf_counter()
            text_vectors = self.qwen_embed_text_documents(batch)
            text_vector_ms = round((time.perf_counter() - vector_started) * 1000, 1)
            image_vector_started = time.perf_counter()
            image_vectors = self.qwen_embed_image_documents(batch)
            image_vector_ms = round((time.perf_counter() - image_vector_started) * 1000, 1)
            vector_ms = round((time.perf_counter() - vector_started) * 1000, 1)
            vector_docs = []
            for doc, text_vector, image_vector in zip(batch, text_vectors, image_vectors):
                enriched = dict(doc)
                enriched[QWEN_TEXT_VECTOR_FIELD] = {
                    "content": product_embedding_text(doc),
                    "vector": text_vector,
                }
                enriched[QWEN_IMAGE_VECTOR_FIELD] = {
                    "content": doc["image_url"],
                    "vector": image_vector,
                }
                vector_docs.append(enriched)
            _, data, add_ms = self.marqo_request(
                "POST",
                f"/indexes/{index_name}/documents",
                {"documents": vector_docs},
                timeout=1800,
            )
            batch_results.append(
                {
                    "batch": number,
                    "docs": len(batch),
                    "textVectorMs": text_vector_ms,
                    "imageVectorMs": image_vector_ms,
                    "vectorMs": vector_ms,
                    "addMs": add_ms,
                    "ms": round(vector_ms + add_ms, 1),
                    "errors": data.get("errors", False),
                    "items": len(data.get("items", [])),
                }
            )

        _, stats, stats_ms = self.marqo_request("GET", f"/indexes/{index_name}/stats", timeout=180)
        return {
            "index": index_name,
            "model": MODEL_NAME,
            "backend": EMBEDDING_BACKEND,
            "documents": len(documents),
            "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
            "events": events,
            "batches": batch_results,
            "stats": stats,
            "statsMs": stats_ms,
        }

    def search_index(self, index_name, query, limit, image_query):
        if not query:
            raise ValueError("query is required")
        if EMBEDDING_BACKEND == "qwen":
            return self.search_qwen_index(index_name, query, limit, image_query)
        effective_query = query if image_query else normalize_product_query(query)
        q = {effective_query: 1.0} if image_query else effective_query
        payload = {
            "q": q,
            "searchMethod": "TENSOR",
            "limit": limit,
            "attributesToRetrieve": [
                "image_url",
                "title",
                "source",
                "category",
                "caption",
                "gender",
                "season",
                "usage",
                "price",
                "aesthetic_score",
            ],
        }
        _, data, ms = self.marqo_request("POST", f"/indexes/{index_name}/search", payload, timeout=600)
        return {"ms": ms, "query": query, "effectiveQuery": effective_query, "result": data}

    def search_qwen_index(self, index_name, query, limit, image_query):
        started = time.perf_counter()
        vector_started = time.perf_counter()
        vector = self.qwen_embed_query(query, image_query)
        vector_ms = round((time.perf_counter() - vector_started) * 1000, 1)
        vector_field = QWEN_IMAGE_VECTOR_FIELD if image_query else QWEN_TEXT_VECTOR_FIELD
        payload = {
            "searchMethod": "TENSOR",
            "context": {"tensor": [{"vector": vector, "weight": 1}]},
            "searchableAttributes": [vector_field],
            "limit": limit,
            "attributesToRetrieve": [
                "image_url",
                "title",
                "source",
                "category",
                "caption",
                "gender",
                "season",
                "usage",
                "price",
                "aesthetic_score",
            ],
        }
        _, data, search_ms = self.marqo_request("POST", f"/indexes/{index_name}/search", payload, timeout=600)
        return {
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "query": query,
            "effectiveQuery": query,
            "backend": EMBEDDING_BACKEND,
            "vectorField": vector_field,
            "vectorMs": vector_ms,
            "searchMs": search_ms,
            "result": data,
        }

    def marqo_request(self, method, path, payload=None, timeout=300):
        url = self.marqo_url + path
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return response.status, data, round((time.perf_counter() - start) * 1000, 1)

    def qwen_request(self, method, path, payload=None, timeout=900):
        url = QWEN_URL + path
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def qwen_embed_text_documents(self, documents):
        data = self.qwen_request(
            "POST",
            "/embed",
            {
                "inputs": [
                    {"text": product_embedding_text(doc)}
                    for doc in documents
                ],
            },
            timeout=1800,
        )
        return data["embeddings"]

    def qwen_embed_image_documents(self, documents):
        data = self.qwen_request(
            "POST",
            "/embed",
            {
                "inputs": [
                    {"image": doc["image_url"]}
                    for doc in documents
                ],
            },
            timeout=1800,
        )
        return data["embeddings"]

    def qwen_embed_query(self, query, image_query):
        if image_query:
            payload = {"inputs": [{"image": query}]}
        else:
            payload = {
                "inputs": [{"text": query}],
                "prompt": "Retrieve relevant ecommerce product images for the user query.",
            }
        data = self.qwen_request(
            "POST",
            "/embed",
            payload,
            timeout=900,
        )
        return data["embeddings"][0]

    def proxy_marqo(self):
        if not self.path.startswith("/marqo/"):
            self.send_error(404)
            return

        target_path = self.path[len("/marqo") :]
        target_url = self.marqo_url + target_path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        request = urllib.request.Request(target_url, data=body, method=self.command, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            self.send_json(502, {"message": str(exc), "type": "proxy_error"})


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def crawl_product_catalog(count):
    rows = list(csv.DictReader(io.StringIO(read_catalog_csv())))
    buckets = {bucket: [] for bucket in PRODUCT_BUCKETS}
    for row in rows:
        text = product_search_text(row)
        for bucket in PRODUCT_BUCKETS:
            if bucket in text and len(buckets[bucket]) < count:
                buckets[bucket].append(row)

    docs = []
    seen = set()
    while len(docs) < count:
        added = False
        for bucket in PRODUCT_BUCKETS:
            while buckets[bucket]:
                row = buckets[bucket].pop(0)
                if add_product_doc(docs, seen, row):
                    added = True
                    break
            if len(docs) >= count:
                break
        if not added:
            break

    for row in rows:
        if len(docs) >= count:
            break
        add_product_doc(docs, seen, row)
    return docs


def read_catalog_csv():
    if CATALOG_URL.startswith(("http://", "https://")):
        req = urllib.request.Request(
            CATALOG_URL,
            headers={
                "User-Agent": "MarqoProductImageSearchDemo/1.0 (local demo; contact: none)"
            },
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.read().decode("utf-8-sig")
    path = pathlib.Path(CATALOG_URL)
    if not path.is_absolute():
        path = ROOT / path
    return path.read_text(encoding="utf-8-sig")


def product_search_text(row):
    return " ".join(
        [
            row.get("title", ""),
            row.get("basename", ""),
            row.get("blip_large_caption", ""),
            row.get("category", ""),
            row.get("tags", ""),
        ]
    ).lower()


def product_embedding_text(doc):
    return "\n".join(
        str(value)
        for value in [
            doc.get("title"),
            doc.get("category"),
            doc.get("caption"),
            doc.get("gender"),
            doc.get("season"),
            doc.get("usage"),
        ]
        if value
    )


def add_product_doc(docs, seen, row):
    image_url = row.get("s3_http") or row.get("image_url")
    if not image_url or image_url in seen:
        return False
    seen.add(image_url)
    title = row.get("title") or clean_title(row.get("basename", "product image"))
    category = row.get("category") or clean_title(row.get("basename", ""))
    caption = row.get("blip_large_caption") or row.get("tags") or title
    source = row.get("source_url") or image_url
    doc = {
        "_id": row.get("id") or "product-" + hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:16],
        "image_url": image_url,
        "title": title,
        "source": source,
        "category": category,
        "caption": caption,
        "gender": row.get("gender", ""),
        "season": row.get("season", ""),
        "usage": row.get("usage", ""),
    }
    price = parse_number(row.get("price"))
    aesthetic_score = parse_number(row.get("aesthetic_score"))
    if price is not None:
        doc["price"] = price
    if aesthetic_score is not None:
        doc["aesthetic_score"] = aesthetic_score
    docs.append(doc)
    return True


def normalize_product_query(query):
    if not isinstance(query, str):
        return query
    translated = []
    for korean, english in KOREAN_QUERY_TERMS:
        if korean in query:
            translated.append(english)
    if not translated:
        return query
    deduped = []
    for term in translated:
        if term not in deduped:
            deduped.append(term)
    return " ".join(deduped)


def parse_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_title(title):
    title = title.removeprefix("File:")
    title = title.replace("_", " ")
    for suffix in [".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP"]:
        if title.endswith(suffix):
            title = title[: -len(suffix)]
    return title.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--marqo-url", default=os.environ.get("MARQO_URL", "http://localhost:8882"))
    args = parser.parse_args()

    def handler(*handler_args, **handler_kwargs):
        return ImageDemoHandler(*handler_args, marqo_url=args.marqo_url, **handler_kwargs)

    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Image UI: http://{args.host}:{args.port}", flush=True)
    print(f"Proxying Marqo API: {args.marqo_url}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
