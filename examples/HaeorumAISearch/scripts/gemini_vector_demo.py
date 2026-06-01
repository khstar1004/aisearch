from __future__ import annotations

import argparse
import json
import math
import os
import threading
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
import numpy as np
import uvicorn


DEFAULT_PROMPT = "Retrieve relevant ecommerce product images for the user query."
TEXT_CACHE_LIMIT = 1024
IMAGE_CACHE_LIMIT = 256
DEFAULT_DEMO_RATE_LIMIT_RPM = 3000
DEFAULT_DEMO_RATE_LIMIT_BURST = 800
DEFAULT_UNCACHED_EMBED_CONCURRENCY = 40
DEFAULT_UNCACHED_EMBED_QUEUE_TIMEOUT_SECONDS = 20.0
DEFAULT_SINGLEFLIGHT_WAIT_TIMEOUT_SECONDS = 120.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a tiny Gemini vector search demo over a smoke index.")
    parser.add_argument("--index", default="logs/gemini-focused-vector-index.json")
    parser.add_argument("--embed-url", default="http://127.0.0.1:8098/embed")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    app = create_app(Path(args.index), args.embed_url)
    uvicorn.run(app, host=args.host, port=args.port, backlog=2048, limit_concurrency=1000)
    return 0


def create_app(index_path: Path, embed_url: str) -> FastAPI:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    items = index.get("items") or []
    for item in items:
        item["vector"] = normalize_vector([float(value) for value in item.get("text_vector") or item["vector"]])
        if item.get("image_vector"):
            item["image_vector"] = normalize_vector([float(value) for value in item["image_vector"]])
    text_matrix = np.asarray([item["vector"] for item in items], dtype=np.float32)
    image_items = [item for item in items if item.get("image_vector")]
    image_matrix = np.asarray([item["image_vector"] for item in image_items], dtype=np.float32)
    app = FastAPI(title="Haeorum Gemini Vector Demo")
    text_cache: OrderedDict[str, list[float]] = OrderedDict()
    image_cache: OrderedDict[str, list[float]] = OrderedDict()
    cache_lock = threading.RLock()
    singleflight = SingleFlight()
    uncached_embed_slots = threading.BoundedSemaphore(
        int_env("GEMINI_DEMO_UNCACHED_EMBED_CONCURRENCY", DEFAULT_UNCACHED_EMBED_CONCURRENCY)
    )
    uncached_queue_timeout = float_env(
        "GEMINI_DEMO_UNCACHED_EMBED_QUEUE_TIMEOUT_SECONDS",
        DEFAULT_UNCACHED_EMBED_QUEUE_TIMEOUT_SECONDS,
    )
    singleflight_wait_timeout = float_env(
        "GEMINI_DEMO_SINGLEFLIGHT_WAIT_TIMEOUT_SECONDS",
        DEFAULT_SINGLEFLIGHT_WAIT_TIMEOUT_SECONDS,
    )
    request_limiter = PerClientRateLimiter(
        rpm=int_env("GEMINI_DEMO_RATE_LIMIT_RPM", DEFAULT_DEMO_RATE_LIMIT_RPM),
        burst=int_env("GEMINI_DEMO_RATE_LIMIT_BURST", DEFAULT_DEMO_RATE_LIMIT_BURST),
    )

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return HTML

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "model": index.get("model"),
            "products": len(items),
            "text_vectors": int(text_matrix.shape[0]),
            "image_vectors": int(image_matrix.shape[0]),
            "embedding_url_configured": bool(embed_url),
            "limits": {
                "rate_limit_rpm_per_client": request_limiter.rpm,
                "rate_limit_burst_per_client": request_limiter.burst,
                "uncached_embed_concurrency": int_env(
                    "GEMINI_DEMO_UNCACHED_EMBED_CONCURRENCY",
                    DEFAULT_UNCACHED_EMBED_CONCURRENCY,
                ),
                "uncached_embed_queue_timeout_seconds": uncached_queue_timeout,
                "singleflight_wait_timeout_seconds": singleflight_wait_timeout,
                "text_cache_limit": TEXT_CACHE_LIMIT,
                "image_cache_limit": IMAGE_CACHE_LIMIT,
            },
        }

    @app.get("/api/search")
    def search(request: Request, q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=30)) -> JSONResponse:
        if not request_limiter.consume(request):
            return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
        started = time.perf_counter()
        try:
            query_vector, embed_ms, cache_hit, singleflight_wait = cached_embed_text(
                embed_url,
                q,
                text_cache,
                cache_lock,
                singleflight,
                uncached_embed_slots,
                uncached_queue_timeout,
                singleflight_wait_timeout,
            )
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        scored = top_matches(text_matrix, items, query_vector, limit)
        total_ms = (time.perf_counter() - started) * 1000
        return JSONResponse(
            {
                "query": q,
                "count": len(scored),
                "model": index.get("model"),
                "product_count": len(items),
                "image_product_count": sum(1 for item in items if item.get("image_vector")),
                "embed_ms": round(embed_ms, 3),
                "cache_hit": cache_hit,
                "singleflight_wait": singleflight_wait,
                "total_ms": round(total_ms, 3),
                "items": [response_item(score, item) for score, item in scored],
            }
        )

    @app.post("/api/search-image")
    async def search_image(request: Request, limit: int = Query(10, ge=1, le=30)) -> JSONResponse:
        if not request_limiter.consume(request):
            return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
        payload = await request.json()
        image = str(payload.get("image") or "").strip() if isinstance(payload, dict) else ""
        if not image:
            return JSONResponse({"error": "image is required"}, status_code=400)
        started = time.perf_counter()
        try:
            query_vector, embed_ms, cache_hit, singleflight_wait = cached_embed_image(
                embed_url,
                image,
                image_cache,
                cache_lock,
                singleflight,
                uncached_embed_slots,
                uncached_queue_timeout,
                singleflight_wait_timeout,
            )
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        scored = top_matches(image_matrix, image_items, query_vector, limit)
        total_ms = (time.perf_counter() - started) * 1000
        return JSONResponse(
            {
                "count": len(scored),
                "model": index.get("model"),
                "product_count": len(items),
                "image_product_count": len(image_items),
                "embed_ms": round(embed_ms, 3),
                "cache_hit": cache_hit,
                "singleflight_wait": singleflight_wait,
                "total_ms": round(total_ms, 3),
                "items": [response_item(score, item) for score, item in scored],
            }
        )

    @app.get("/api/sample-image")
    def sample_image() -> JSONResponse:
        item = next((candidate for candidate in items if candidate.get("image_vector")), None)
        if item is None:
            return JSONResponse({"available": False})
        return JSONResponse({"available": True, **item["product"]})

    return app


def response_item(score: float, item: dict) -> dict:
    return {
        **item["product"],
        "score": round(float(score), 6),
    }


def embed_text(embed_url: str, text: str) -> tuple[list[float], float]:
    payload = {"inputs": [{"text": text}], "prompt": DEFAULT_PROMPT}
    return embed_one(embed_url, payload)


def embed_image(embed_url: str, image: str) -> tuple[list[float], float]:
    payload = {"inputs": [{"image": image}], "prompt": DEFAULT_PROMPT}
    return embed_one(embed_url, payload, timeout=120)


def cached_embed_text(
    embed_url: str,
    text: str,
    cache: OrderedDict[str, list[float]],
    lock: threading.RLock,
    singleflight: "SingleFlight",
    uncached_embed_slots: threading.BoundedSemaphore,
    queue_timeout: float,
    wait_timeout: float,
) -> tuple[list[float], float, bool, bool]:
    key = "text:" + text.strip()
    cached = cache_get(cache, key, lock)
    if cached is not None:
        return cached, 0.0, True, False
    return singleflight.run(
        key,
        lambda: embed_text_with_limit(embed_url, text, uncached_embed_slots, queue_timeout),
        cache=cache,
        cache_limit=TEXT_CACHE_LIMIT,
        cache_lock=lock,
        wait_timeout=wait_timeout,
    )


def cached_embed_image(
    embed_url: str,
    image: str,
    cache: OrderedDict[str, list[float]],
    lock: threading.RLock,
    singleflight: "SingleFlight",
    uncached_embed_slots: threading.BoundedSemaphore,
    queue_timeout: float,
    wait_timeout: float,
) -> tuple[list[float], float, bool, bool]:
    key = "image:" + image.strip()
    cached = cache_get(cache, key, lock)
    if cached is not None:
        return cached, 0.0, True, False
    return singleflight.run(
        key,
        lambda: embed_image_with_limit(embed_url, image, uncached_embed_slots, queue_timeout),
        cache=cache,
        cache_limit=IMAGE_CACHE_LIMIT,
        cache_lock=lock,
        wait_timeout=wait_timeout,
    )


def cache_get(cache: OrderedDict[str, list[float]], key: str, lock: threading.RLock) -> list[float] | None:
    with lock:
        vector = cache.get(key)
        if vector is None:
            return None
        cache.move_to_end(key)
        return list(vector)


def cache_put(
    cache: OrderedDict[str, list[float]],
    key: str,
    vector: list[float],
    limit: int,
    lock: threading.RLock,
) -> None:
    with lock:
        cache[key] = list(vector)
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)


def embed_text_with_limit(
    embed_url: str,
    text: str,
    uncached_embed_slots: threading.BoundedSemaphore,
    queue_timeout: float,
) -> tuple[list[float], float]:
    if not uncached_embed_slots.acquire(timeout=queue_timeout):
        raise RuntimeError("uncached embedding queue is full")
    try:
        return embed_text(embed_url, text)
    finally:
        uncached_embed_slots.release()


def embed_image_with_limit(
    embed_url: str,
    image: str,
    uncached_embed_slots: threading.BoundedSemaphore,
    queue_timeout: float,
) -> tuple[list[float], float]:
    if not uncached_embed_slots.acquire(timeout=queue_timeout):
        raise RuntimeError("uncached embedding queue is full")
    try:
        return embed_image(embed_url, image)
    finally:
        uncached_embed_slots.release()


class SingleFlight:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._inflight: dict[str, dict] = {}

    def run(
        self,
        key: str,
        fn,
        *,
        cache: OrderedDict[str, list[float]],
        cache_limit: int,
        cache_lock: threading.RLock,
        wait_timeout: float,
    ) -> tuple[list[float], float, bool, bool]:
        with self._lock:
            existing = self._inflight.get(key)
            if existing is None:
                entry = {"event": threading.Event(), "result": None, "error": None}
                self._inflight[key] = entry
                owner = True
            else:
                entry = existing
                owner = False
        if not owner:
            if not entry["event"].wait(timeout=wait_timeout):
                raise RuntimeError("singleflight wait timeout")
            if entry["error"] is not None:
                raise RuntimeError(str(entry["error"]))
            vector = cache_get(cache, key, cache_lock)
            if vector is not None:
                return vector, 0.0, True, True
            result = entry["result"]
            if result is None:
                raise RuntimeError("singleflight result missing")
            return list(result[0]), 0.0, True, True
        try:
            vector, elapsed_ms = fn()
            cache_put(cache, key, vector, cache_limit, cache_lock)
            entry["result"] = (list(vector), elapsed_ms)
            return vector, elapsed_ms, False, False
        except Exception as exc:
            entry["error"] = exc
            raise
        finally:
            entry["event"].set()
            with self._lock:
                self._inflight.pop(key, None)


class TokenBucket:
    def __init__(self, *, rpm: int, burst: int) -> None:
        self.capacity = float(max(1, burst))
        self.refill_per_second = float(max(1, rpm)) / 60.0
        self.tokens = self.capacity
        self.updated_at = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = max(0.0, now - self.updated_at)
        self.updated_at = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


class PerClientRateLimiter:
    def __init__(self, *, rpm: int, burst: int) -> None:
        self.rpm = rpm
        self.burst = burst
        self._lock = threading.RLock()
        self._buckets: dict[str, TokenBucket] = {}

    def consume(self, request: Request) -> bool:
        host = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(host)
            if bucket is None:
                bucket = TokenBucket(rpm=self.rpm, burst=self.burst)
                self._buckets[host] = bucket
            if len(self._buckets) > 4096:
                stale_before = now - 600
                for key, value in list(self._buckets.items()):
                    if value.updated_at < stale_before:
                        self._buckets.pop(key, None)
            return bucket.consume()


def int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        return default


def float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0.001, float(str(raw).strip()))
    except ValueError:
        return default


def embed_one(embed_url: str, payload: dict, timeout: int = 60) -> tuple[list[float], float]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(embed_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return normalize_vector(data["embeddings"][0]), elapsed_ms


def normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def top_matches(matrix: np.ndarray, candidates: list[dict], query_vector: list[float], limit: int) -> list[tuple[float, dict]]:
    if not candidates:
        return []
    query = np.asarray(query_vector, dtype=np.float32)
    scores = matrix @ query
    count = min(max(1, int(limit)), len(candidates))
    if count < len(candidates):
        indexes = np.argpartition(-scores, count - 1)[:count]
        indexes = indexes[np.argsort(-scores[indexes])]
    else:
        indexes = np.argsort(-scores)
    return [(float(scores[index]), candidates[int(index)]) for index in indexes[:count]]


HTML = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Haeorum Gemini Search Demo</title>
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #111827; background: #f6f7f9; }
    main { max-width: 1120px; margin: 0 auto; padding: 28px 20px; }
    h1 { margin: 0 0 16px; font-size: 24px; }
    .panel { background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; margin-bottom: 14px; }
    .bar { display: flex; gap: 8px; margin-bottom: 12px; align-items: center; }
    input { flex: 1; height: 44px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 12px; font-size: 16px; min-width: 0; }
    input[type="file"] { height: auto; padding: 10px; background: white; }
    button { height: 44px; border: 0; border-radius: 6px; padding: 0 16px; background: #0f766e; color: white; font-weight: 700; cursor: pointer; white-space: nowrap; }
    button.secondary { background: #334155; }
    .meta { margin: 8px 0 16px; color: #475569; font-size: 13px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
    .item { background: white; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }
    .thumb { aspect-ratio: 1 / 1; background: #e5e7eb; display: grid; place-items: center; }
    .thumb img { width: 100%; height: 100%; object-fit: cover; }
    .body { padding: 10px; }
    .name { font-size: 14px; line-height: 1.35; min-height: 38px; }
    .cat { color: #0f766e; font-size: 13px; margin-top: 8px; }
    .score { color: #64748b; font-size: 12px; margin-top: 4px; }
    @media (max-width: 720px) { .bar { flex-direction: column; align-items: stretch; } button { width: 100%; } }
  </style>
</head>
<body>
<main>
  <h1>Haeorum Gemini Search Demo</h1>
  <div class="panel">
    <div class="bar">
      <input id="q" value="검은 우산" autocomplete="off">
      <button id="go">텍스트 검색</button>
    </div>
    <div class="bar">
      <input id="imageUrl" placeholder="이미지 URL">
      <input id="imageFile" type="file" accept="image/png,image/jpeg,image/webp">
      <button id="imageGo" class="secondary">이미지 검색</button>
    </div>
  </div>
  <div id="meta" class="meta"></div>
  <div id="items" class="grid"></div>
</main>
<script>
const q = document.getElementById('q');
const go = document.getElementById('go');
const imageUrl = document.getElementById('imageUrl');
const imageFile = document.getElementById('imageFile');
const imageGo = document.getElementById('imageGo');
const meta = document.getElementById('meta');
const items = document.getElementById('items');

async function search() {
  const text = q.value.trim();
  if (!text) return;
  meta.textContent = '검색 중...';
  items.innerHTML = '';
  const res = await fetch('/api/search?q=' + encodeURIComponent(text) + '&limit=12');
  const data = await res.json();
  meta.textContent = `${data.product_count}개 상품 | ${data.image_product_count}개 이미지 벡터 | 임베딩 ${data.embed_ms}ms | 캐시 ${data.cache_hit ? 'hit' : 'miss'} | 중복대기 ${data.singleflight_wait ? 'yes' : 'no'} | 전체 ${data.total_ms}ms`;
  renderItems(data.items);
}

async function searchImage() {
  meta.textContent = '이미지 검색 중...';
  items.innerHTML = '';
  let image = imageUrl.value.trim();
  if (!image && imageFile.files && imageFile.files[0]) {
    image = await readFileAsDataUrl(imageFile.files[0]);
  }
  if (!image) {
    meta.textContent = '이미지 URL을 넣거나 파일을 선택하세요.';
    return;
  }
  const res = await fetch('/api/search-image?limit=12', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({image})
  });
  const data = await res.json();
  if (!res.ok) {
    meta.textContent = data.error || '이미지 검색 실패';
    return;
  }
  meta.textContent = `${data.product_count}개 상품 | ${data.image_product_count}개 이미지 벡터 | 이미지 임베딩 ${data.embed_ms}ms | 캐시 ${data.cache_hit ? 'hit' : 'miss'} | 중복대기 ${data.singleflight_wait ? 'yes' : 'no'} | 전체 ${data.total_ms}ms`;
  renderItems(data.items);
}

function renderItems(list) {
  items.innerHTML = list.map(item => `
    <article class="item">
      <div class="thumb">${item.main_image_url ? `<img src="${item.main_image_url}" alt="">` : ''}</div>
      <div class="body">
        <div class="name">${escapeHtml(item.product_name || '')}</div>
        <div class="cat">${escapeHtml(item.category_name || '')}</div>
        <div class="score">score ${item.score}</div>
      </div>
    </article>
  `).join('');
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

go.addEventListener('click', search);
imageGo.addEventListener('click', searchImage);
q.addEventListener('keydown', event => { if (event.key === 'Enter') search(); });
fetch('/api/sample-image').then(r => r.json()).then(data => {
  if (data.available && data.main_image_url) imageUrl.value = data.main_image_url;
});
search();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
