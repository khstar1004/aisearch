from __future__ import annotations

import asyncio
import math
import os
import secrets
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .gemini_embeddings import (
    DEFAULT_GEMINI_EMBEDDING_DIMENSIONS,
    DEFAULT_GEMINI_EMBEDDING_MODEL,
    GeminiEmbeddingSettings,
    GeminiProviderError,
    embed_inputs_with_gemini,
    load_gemini_embedding_settings_from_env,
    normalize_embedding_inputs,
    validate_gemini_auth_settings,
)
from .request_body import read_json_object_limited


MAX_EMBED_JSON_BODY_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_INPUTS_PER_REQUEST = 128
DEFAULT_RATE_LIMIT_RPM = 3000
DEFAULT_RATE_LIMIT_BURST = 600
DEFAULT_MAX_CONCURRENT_GEMINI_CALLS = 50
DEFAULT_QUEUE_TIMEOUT_SECONDS = 15.0
PROXY_API_KEY_HEADER = "X-Embedding-Proxy-Key"


def int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0.001:
        raise ValueError(f"{name} must be at least 0.001")
    return value


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


MAX_INPUTS_PER_REQUEST = int_env("GEMINI_PROXY_MAX_INPUTS_PER_REQUEST", DEFAULT_MAX_INPUTS_PER_REQUEST)
RATE_LIMIT_RPM = int_env("GEMINI_PROXY_RATE_LIMIT_RPM", DEFAULT_RATE_LIMIT_RPM)
RATE_LIMIT_BURST = int_env("GEMINI_PROXY_RATE_LIMIT_BURST", DEFAULT_RATE_LIMIT_BURST)
MAX_CONCURRENT_GEMINI_CALLS = int_env("GEMINI_PROXY_MAX_CONCURRENT_CALLS", DEFAULT_MAX_CONCURRENT_GEMINI_CALLS)
QUEUE_TIMEOUT_SECONDS = float_env("GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS", DEFAULT_QUEUE_TIMEOUT_SECONDS)
_gemini_call_slots = asyncio.Semaphore(MAX_CONCURRENT_GEMINI_CALLS)
_rate_lock = threading.RLock()
_rate_buckets: dict[str, TokenBucket] = {}


class GeminiProxyUsage:
    def __init__(self) -> None:
        self.started_at = time.time()
        self._lock = threading.RLock()
        self.requests_total = 0
        self.success_total = 0
        self.failed_total = 0
        self.rate_limited_total = 0
        self.queue_full_total = 0
        self.provider_call_total = 0
        self.input_total = 0
        self.text_input_total = 0
        self.image_input_total = 0
        self.text_char_total = 0
        self.estimated_image_byte_total = 0
        self.image_download_total = 0
        self.provider_elapsed_ms_total = 0.0
        self.provider_elapsed_ms_max = 0.0
        self.elapsed_ms_total = 0.0
        self.elapsed_ms_max = 0.0
        self.active_calls = 0
        self.max_active_calls_observed = 0
        self.last_success_at: float | None = None
        self.last_error_at: float | None = None
        self.last_error: str | None = None
        self.last_status_code: int | None = None

    def request_started(self) -> None:
        with self._lock:
            self.requests_total += 1

    def call_started(self) -> None:
        with self._lock:
            self.active_calls += 1
            self.max_active_calls_observed = max(self.max_active_calls_observed, self.active_calls)

    def call_finished(self) -> None:
        with self._lock:
            self.active_calls = max(0, self.active_calls - 1)

    def record_success(self, stats: Any, elapsed_ms: float, input_summary: dict[str, int]) -> None:
        provider_elapsed_ms = float(getattr(stats, "provider_elapsed_ms", 0.0) or 0.0)
        with self._lock:
            self.success_total += 1
            self.provider_call_total += 1
            self.input_total += int(input_summary.get("inputs", 0))
            self.text_input_total += int(input_summary.get("text_inputs", 0))
            self.image_input_total += int(input_summary.get("image_inputs", 0))
            self.text_char_total += int(input_summary.get("text_chars", 0))
            self.estimated_image_byte_total += int(input_summary.get("estimated_image_bytes", 0))
            self.image_download_total += int(getattr(stats, "image_downloads", 0) or 0)
            self.provider_elapsed_ms_total += provider_elapsed_ms
            self.provider_elapsed_ms_max = max(self.provider_elapsed_ms_max, provider_elapsed_ms)
            self.elapsed_ms_total += float(elapsed_ms)
            self.elapsed_ms_max = max(self.elapsed_ms_max, float(elapsed_ms))
            self.last_success_at = time.time()
            self.last_status_code = 200

    def record_failure(self, status_code: int, detail: Any, *, rate_limited: bool = False, queue_full: bool = False) -> None:
        with self._lock:
            self.failed_total += 1
            if rate_limited:
                self.rate_limited_total += 1
            if queue_full:
                self.queue_full_total += 1
            self.last_error_at = time.time()
            self.last_status_code = int(status_code)
            self.last_error = str(detail)[:500]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg_provider = self.provider_elapsed_ms_total / self.success_total if self.success_total else 0.0
            avg_elapsed = self.elapsed_ms_total / self.success_total if self.success_total else 0.0
            return {
                "started_at": iso_utc(self.started_at),
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "requests_total": self.requests_total,
                "success_total": self.success_total,
                "failed_total": self.failed_total,
                "rate_limited_total": self.rate_limited_total,
                "queue_full_total": self.queue_full_total,
                "provider_call_total": self.provider_call_total,
                "input_total": self.input_total,
                "text_input_total": self.text_input_total,
                "image_input_total": self.image_input_total,
                "text_char_total": self.text_char_total,
                "estimated_image_byte_total": self.estimated_image_byte_total,
                "image_download_total": self.image_download_total,
                "provider_elapsed_ms_total": round(self.provider_elapsed_ms_total, 3),
                "provider_elapsed_ms_avg": round(avg_provider, 3),
                "provider_elapsed_ms_max": round(self.provider_elapsed_ms_max, 3),
                "elapsed_ms_total": round(self.elapsed_ms_total, 3),
                "elapsed_ms_avg": round(avg_elapsed, 3),
                "elapsed_ms_max": round(self.elapsed_ms_max, 3),
                "active_calls": self.active_calls,
                "max_active_calls_observed": self.max_active_calls_observed,
                "rate_limit_bucket_count": len(_rate_buckets),
                "last_success_at": iso_utc(self.last_success_at),
                "last_error_at": iso_utc(self.last_error_at),
                "last_error": self.last_error,
                "last_status_code": self.last_status_code,
            }


_usage = GeminiProxyUsage()


app = FastAPI(title="Haeorum Gemini Embedding Proxy", version="1.0.0")


@app.get("/health")
def health() -> dict[str, Any]:
    settings, ready, load_error = load_proxy_status()
    return {
        "ready": ready,
        "ok": ready,
        "provider": "gemini",
        "model": settings.model,
        "dimensions": settings.dimensions,
        "auth_mode": settings.auth_mode,
        "quota_project_configured": bool(settings.quota_project),
        "api_key_configured": bool(settings.api_key),
        "proxy_auth_configured": bool(proxy_api_key()),
        "loadError": load_error,
        "max_image_bytes": settings.max_image_bytes,
        "max_response_bytes": settings.max_response_bytes,
        "timeout_seconds": settings.timeout_seconds,
        "image_download_timeout_seconds": settings.image_download_timeout_seconds,
        "limits": {
            "max_inputs_per_request": MAX_INPUTS_PER_REQUEST,
            "rate_limit_rpm_per_client": RATE_LIMIT_RPM,
            "rate_limit_burst_per_client": RATE_LIMIT_BURST,
            "max_concurrent_gemini_calls": MAX_CONCURRENT_GEMINI_CALLS,
            "queue_timeout_seconds": QUEUE_TIMEOUT_SECONDS,
            "provider_retry_count": settings.provider_retry_count,
            "provider_retry_delay_seconds": settings.provider_retry_delay_seconds,
            "provider_retry_max_delay_seconds": settings.provider_retry_max_delay_seconds,
            "max_response_bytes": settings.max_response_bytes,
        },
        "usage": _usage.snapshot(),
    }


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    settings, ready, load_error = load_proxy_status()
    return {
        "provider": "gemini",
        "ready": ready,
        "ok": ready,
        "loadError": load_error,
        "model": settings.model,
        "dimensions": settings.dimensions,
        "auth_mode": settings.auth_mode,
        "quota_project_configured": bool(settings.quota_project),
        "api_key_configured": bool(settings.api_key),
        "proxy_auth_configured": bool(proxy_api_key()),
        "max_response_bytes": settings.max_response_bytes,
        "usage": _usage.snapshot(),
        "limits": {
            "max_inputs_per_request": MAX_INPUTS_PER_REQUEST,
            "rate_limit_rpm_per_client": RATE_LIMIT_RPM,
            "rate_limit_burst_per_client": RATE_LIMIT_BURST,
            "max_concurrent_gemini_calls": MAX_CONCURRENT_GEMINI_CALLS,
            "queue_timeout_seconds": QUEUE_TIMEOUT_SECONDS,
            "provider_retry_count": settings.provider_retry_count,
            "provider_retry_delay_seconds": settings.provider_retry_delay_seconds,
            "provider_retry_max_delay_seconds": settings.provider_retry_max_delay_seconds,
            "max_response_bytes": settings.max_response_bytes,
        },
    }


@app.post("/embed")
async def embed(request: Request) -> JSONResponse:
    settings: GeminiEmbeddingSettings | None = None
    terminal_recorded = False
    input_summary: dict[str, int] = {}
    _usage.request_started()
    try:
        verify_proxy_api_key(request)
        try:
            settings = load_gemini_embedding_settings_from_env()
        except ValueError as exc:
            detail = f"Gemini embedding proxy is not configured: {exc}"
            _usage.record_failure(503, detail)
            terminal_recorded = True
            raise HTTPException(status_code=503, detail=detail) from exc
        try:
            validate_gemini_auth_settings(settings)
        except ValueError as exc:
            detail = f"Gemini embedding proxy is not configured: {exc}"
            _usage.record_failure(503, detail)
            terminal_recorded = True
            raise HTTPException(status_code=503, detail=detail) from exc
        payload = await read_json_object_limited(request, max_bytes=MAX_EMBED_JSON_BODY_BYTES)
        inputs = payload.get("inputs")
        if not isinstance(inputs, list):
            raise ValueError("inputs must be a list")
        if len(inputs) > MAX_INPUTS_PER_REQUEST:
            raise ValueError(f"inputs must contain at most {MAX_INPUTS_PER_REQUEST} items")
        if not consume_rate_limit(request):
            _usage.record_failure(429, "rate limit exceeded", rate_limited=True)
            terminal_recorded = True
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        normalized_inputs = normalize_embedding_inputs(inputs)
        input_summary = summarize_embedding_inputs(normalized_inputs)
        prompt = str(payload.get("prompt") or "").strip() or None
        started = time.perf_counter()
        try:
            await asyncio.wait_for(_gemini_call_slots.acquire(), timeout=QUEUE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            _usage.record_failure(503, "Gemini embedding queue is full", queue_full=True)
            terminal_recorded = True
            raise HTTPException(status_code=503, detail="Gemini embedding queue is full") from exc
        try:
            _usage.call_started()
            embeddings, stats = await asyncio.to_thread(
                embed_inputs_with_gemini,
                normalized_inputs,
                settings=settings,
                prompt=prompt,
            )
        finally:
            _usage.call_finished()
            _gemini_call_slots.release()
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        _usage.record_success(stats, elapsed_ms, input_summary)
        terminal_recorded = True
    except GeminiProviderError as exc:
        status_code = int(exc.status_code or 502)
        detail = str(exc)
        _usage.record_failure(status_code, detail, rate_limited=status_code == 429)
        headers: dict[str, str] = {}
        if exc.retry_after_seconds is not None:
            headers["Retry-After"] = str(max(0, int(math.ceil(float(exc.retry_after_seconds)))))
        raise HTTPException(status_code=status_code, detail=detail, headers=headers) from exc
    except HTTPException as exc:
        if not terminal_recorded:
            _usage.record_failure(int(exc.status_code), exc.detail)
        raise
    except ValueError as exc:
        _usage.record_failure(400, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _usage.record_failure(502, str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(
        {
            "embeddings": embeddings,
            "model": settings.model if settings else DEFAULT_GEMINI_EMBEDDING_MODEL,
            "dimensions": settings.dimensions if settings else DEFAULT_GEMINI_EMBEDDING_DIMENSIONS,
            "provider": "gemini",
            "elapsed_ms": elapsed_ms,
            "stats": stats.__dict__,
        }
    )


def verify_proxy_api_key(request: Request) -> None:
    expected = proxy_api_key()
    if not expected:
        return
    candidate = str(request.headers.get(PROXY_API_KEY_HEADER) or "").strip()
    if not candidate:
        authorization = str(request.headers.get("authorization") or "").strip()
        if authorization.lower().startswith("bearer "):
            candidate = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(candidate, expected):
        raise HTTPException(status_code=401, detail="embedding proxy authentication required")


def proxy_api_key() -> str:
    return str(os.environ.get("GEMINI_PROXY_API_KEY") or os.environ.get("GEMINI_PROXY_SHARED_SECRET") or "").strip()


def load_proxy_status() -> tuple[Any, bool, str | None]:
    try:
        settings = load_gemini_embedding_settings_from_env()
    except Exception as exc:
        return GeminiEmbeddingSettings(api_key=""), False, str(exc)
    try:
        validate_gemini_auth_settings(settings)
    except Exception as exc:
        return settings, False, str(exc)
    return settings, True, None


def consume_rate_limit(request: Request) -> bool:
    host = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets.get(host)
        if bucket is None:
            bucket = TokenBucket(rpm=RATE_LIMIT_RPM, burst=RATE_LIMIT_BURST)
            _rate_buckets[host] = bucket
        if len(_rate_buckets) > 4096:
            stale_before = now - 600
            for key, value in list(_rate_buckets.items()):
                if value.updated_at < stale_before:
                    _rate_buckets.pop(key, None)
        return bucket.consume()


def summarize_embedding_inputs(inputs: list[dict[str, str]]) -> dict[str, int]:
    text_inputs = 0
    image_inputs = 0
    text_chars = 0
    estimated_image_bytes = 0
    for item in inputs:
        text = str(item.get("text") or "")
        image = str(item.get("image") or "")
        if text:
            text_inputs += 1
            text_chars += len(text)
        elif image:
            image_inputs += 1
            estimated_image_bytes += estimate_image_input_bytes(image)
    return {
        "inputs": len(inputs),
        "text_inputs": text_inputs,
        "image_inputs": image_inputs,
        "text_chars": text_chars,
        "estimated_image_bytes": estimated_image_bytes,
    }


def estimate_image_input_bytes(value: str) -> int:
    text = str(value or "")
    if not text.lower().startswith("data:"):
        return 0
    _header, separator, encoded = text.partition(",")
    if not separator:
        return 0
    compact = "".join(encoded.split())
    padding = compact.count("=")
    return max(0, (len(compact) * 3) // 4 - padding)


def iso_utc(value: float | None) -> str | None:
    if not value:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))
