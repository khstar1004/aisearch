from __future__ import annotations

import json
import math
import socket
import threading
import time
import gzip
import hashlib
import http.client
import urllib.error
import urllib.parse
from email.utils import parsedate_to_datetime
from abc import ABC, abstractmethod
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, NoReturn

from .category_intent import append_inferred_categories
from .config import validate_marqo_url_value
from .identifiers import legacy_product_document_id, product_document_id, public_product_id_from_document_id
from .models import ProductDocument
from .url_safety import safe_absolute_http_url, safe_product_source_url


TEXT_FIELDS = ["product_name", "category_name", "description", "keywords", "print_methods", "materials", "colors"]
IMAGE_FIELD = "main_image_url"
SEARCH_TEXT_FIELD = "search_text"
QWEN_TEXT_VECTOR_FIELD = "qwen_text_vector"
QWEN_IMAGE_VECTOR_FIELD = "qwen_image_vector"
GEMINI_TEXT_VECTOR_FIELD = "gemini_text_vector"
GEMINI_IMAGE_VECTOR_FIELD = "gemini_image_vector"
QWEN_QUERY_PROMPT = "Retrieve relevant ecommerce product images for the user query."
GEMINI_QUERY_PROMPT = "Retrieve relevant ecommerce products for the user query."
MARQO_ADD_DOCUMENTS_BATCH_SIZE = 128
MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES = 8 * 1024 * 1024
MARQO_DELETE_DOCUMENTS_BATCH_SIZE = 512
MARQO_BATCH_RESPONSE_SAMPLE_LIMIT = 10
MARQO_BATCH_FAILURE_SAMPLE_LIMIT = 100
MARQO_TEXT_RERANK_MIN_CANDIDATES = 100
MARQO_MAX_SEARCH_CANDIDATES = 2000
JSON_DUMP_SEPARATORS = (",", ":")
ATTRIBUTES_TO_RETRIEVE = [
    "document_id",
    "product_id",
    "product_name",
    "category_name",
    "price",
    "main_image_url",
    "product_url",
    "status",
    "updated_at",
    "is_deleted",
    "display_yn",
    "mall_id",
    "product_group_id",
]
TEXT_RERANK_ATTRIBUTES_TO_RETRIEVE = [
    SEARCH_TEXT_FIELD,
]
ATTRIBUTE_FILTER_FIELDS_TO_RETRIEVE = [
    "print_methods",
    "materials",
    "colors",
]
NUMERIC_FILTER_FIELDS_TO_RETRIEVE = [
    "min_order_qty",
    "price_min",
    "price_max",
    "delivery_days",
]


@dataclass(frozen=True)
class EngineQuery:
    q: str | None = None
    image_data_url: str | None = None
    image_hash: str | None = None
    mall_id: str | None = None
    category: str | None = None
    print_method: str | None = None
    material: str | None = None
    color: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    quantity: int | None = None
    max_delivery_days: int | None = None
    inferred_categories: tuple[str, ...] = ()
    limit: int = 20
    text_weight: float = 1.0
    image_weight: float = 0.0
    strict_mall_filter: bool = False
    query_synonyms: Mapping[str, Iterable[str]] = field(default_factory=dict)
    max_candidates: int = MARQO_MAX_SEARCH_CANDIDATES


@dataclass(frozen=True)
class EngineHit:
    document: ProductDocument
    score: float
    source_scores: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalProductRecord:
    product: ProductDocument
    doc_text: str
    doc_terms: frozenset[str]
    category_text: str
    category_terms: frozenset[str]
    image_terms: frozenset[str]
    image_hash: str
    has_image_url: bool


@dataclass(frozen=True)
class LocalTextQuery:
    normalized: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class TextRelevanceQuery:
    normalized: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class PreparedTextFilter:
    normalized: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class PreparedProductFilters:
    strict_mall_id: str | None = None
    normalized_category: str = ""
    print_method: PreparedTextFilter | None = None
    material: PreparedTextFilter | None = None
    color: PreparedTextFilter | None = None
    min_price: float | None = None
    max_price: float | None = None
    quantity: int | None = None
    max_delivery_days: int | None = None


@dataclass
class InflightQwenQueryVector:
    event: threading.Event
    vector: list[float] | None = None
    exception: BaseException | None = None


class SearchEngine(ABC):
    name: str

    def close(self) -> None:
        return None

    @abstractmethod
    def search(self, query: EngineQuery) -> list[EngineHit]:
        raise NotImplementedError

    @abstractmethod
    def upsert_products(self, products: Iterable[ProductDocument]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_products(self, product_ids: Iterable[str]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError


class ReservedSearchEngineUnavailable(RuntimeError):
    """Raised when a reserved future adapter is selected for runtime work."""


class BackendRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        service: str = "backend",
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.service = service
        self.retry_after_seconds = retry_after_seconds


class BackendProtocolError(BackendRequestError):
    def __init__(self, message: str, *, service: str = "backend"):
        super().__init__(message, status_code=502, service=service)


class BackendConnectionPoolTimeoutError(BackendRequestError):
    def __init__(self, service: str, timeout_seconds: float):
        timeout = max(0.0, float(timeout_seconds))
        super().__init__(
            f"{service} backend HTTP connection slots exhausted; retry after {timeout:.3f}s",
            status_code=503,
            service=service,
            retry_after_seconds=timeout,
        )
        self.timeout_seconds = timeout


class BackendCircuitOpenError(RuntimeError):
    def __init__(self, service: str, retry_after_seconds: float):
        retry_after = max(0.0, float(retry_after_seconds))
        super().__init__(f"{service} circuit breaker is open; retry after {retry_after:.3f}s")
        self.service = service
        self.retry_after_seconds = retry_after


TRANSIENT_BACKEND_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def transient_backend_error(exc: BaseException) -> bool:
    if isinstance(exc, BackendCircuitOpenError):
        return False
    if isinstance(exc, BackendConnectionPoolTimeoutError):
        return False
    if isinstance(exc, BackendRequestError):
        return exc.status_code in TRANSIENT_BACKEND_STATUS_CODES
    return isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError, http.client.HTTPException, ConnectionError))


class BackendJsonHttpClient:
    def __init__(
        self,
        base_url: str,
        service: str,
        *,
        max_idle_seconds: float = 55.0,
        max_active_requests: int = 96,
        connection_acquire_timeout_seconds: float = 1.0,
        circuit_failure_threshold: int = 5,
        circuit_cooldown_seconds: float = 5.0,
        circuit_half_open_max_calls: int = 1,
        default_headers: Mapping[str, str] | None = None,
    ):
        parsed = urllib.parse.urlparse(base_url)
        self.base_url = base_url.rstrip("/")
        self.service = service
        self.service_code = service.lower().split()[0]
        self.scheme = parsed.scheme.lower()
        self.host = parsed.hostname or ""
        self.port = parsed.port
        self.base_path = parsed.path.rstrip("/")
        self.max_idle_seconds = max(0.0, float(max_idle_seconds))
        self.max_active_requests = max(0, int(max_active_requests))
        self.connection_acquire_timeout_seconds = max(0.0, float(connection_acquire_timeout_seconds))
        self.circuit_failure_threshold = max(0, int(circuit_failure_threshold))
        self.circuit_cooldown_seconds = max(0.0, float(circuit_cooldown_seconds))
        self.circuit_half_open_max_calls = max(1, int(circuit_half_open_max_calls))
        self.default_headers = {
            str(key): str(value)
            for key, value in dict(default_headers or {}).items()
            if str(key).strip() and str(value).strip()
        }
        self._local = threading.local()
        self._request_slots = threading.BoundedSemaphore(self.max_active_requests) if self.max_active_requests > 0 else None
        self._stats_lock = threading.Lock()
        self._connections_opened = 0
        self._connection_reuses = 0
        self._idle_reconnects = 0
        self._requests_started = 0
        self._active_requests = 0
        self._max_active_requests_observed = 0
        self._connection_acquire_wait_events = 0
        self._connection_acquire_wait_timeouts = 0
        self._total_connection_acquire_wait_ms = 0.0
        self._max_connection_acquire_wait_ms = 0.0
        self._last_connection_acquire_wait_ms = 0.0
        self._request_attempts = 0
        self._responses_received = 0
        self._error_responses = 0
        self._invalid_json_responses = 0
        self._stale_reconnects = 0
        self._connection_close_responses = 0
        self._gzip_responses = 0
        self._retry_after_responses = 0
        self._max_retry_after_seconds = 0.0
        self._last_retry_after_seconds = 0.0
        self._total_elapsed_ms = 0.0
        self._max_elapsed_ms = 0.0
        self._last_elapsed_ms = 0.0
        self._total_request_body_bytes = 0
        self._max_request_body_bytes = 0
        self._last_request_body_bytes = 0
        self._total_response_body_bytes = 0
        self._max_response_body_bytes = 0
        self._last_response_body_bytes = 0
        self._total_decoded_response_body_bytes = 0
        self._max_decoded_response_body_bytes = 0
        self._last_decoded_response_body_bytes = 0
        self._last_error: str | None = None
        self._circuit_state = "closed"
        self._circuit_consecutive_failures = 0
        self._circuit_opened_at: float | None = None
        self._circuit_open_until: float | None = None
        self._circuit_open_events = 0
        self._circuit_short_circuits = 0
        self._circuit_half_open_events = 0
        self._circuit_half_open_in_flight = 0
        self._circuit_recovery_events = 0
        self._connections: set[http.client.HTTPConnection] = set()
        self._connection_generation = 0

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        timeout: int | float = 60,
    ) -> tuple[int, Any, float]:
        body = json_body_bytes(payload) if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Connection": "keep-alive",
            **self.default_headers,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        target = self._target_path(path)
        started = time.perf_counter()
        last_error: BaseException | None = None
        with self._stats_lock:
            self._requests_started += 1
        circuit_context = self._enter_circuit()
        slot_acquired = False
        try:
            slot_acquired = self._acquire_request_slot()
            for attempt in range(2):
                try:
                    connection = self._connection(timeout)
                    with self._stats_lock:
                        self._request_attempts += 1
                        request_body_bytes = len(body or b"")
                        self._total_request_body_bytes += request_body_bytes
                        self._max_request_body_bytes = max(self._max_request_body_bytes, request_body_bytes)
                        self._last_request_body_bytes = request_body_bytes
                    connection.request(method, target, body=body, headers=headers)
                    response = connection.getresponse()
                    raw_body = response.read()
                    response_getheader = getattr(response, "getheader", None)
                    content_encoding = response_getheader("Content-Encoding") if response_getheader else None
                    retry_after_seconds = self._parse_retry_after_header(
                        response_getheader("Retry-After") if response_getheader else None
                    )
                    decoded_body = self._decode_response_body(raw_body, content_encoding)
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                    with self._stats_lock:
                        self._responses_received += 1
                        self._total_elapsed_ms += elapsed_ms
                        self._max_elapsed_ms = max(self._max_elapsed_ms, elapsed_ms)
                        self._last_elapsed_ms = elapsed_ms
                        response_body_bytes = len(raw_body)
                        decoded_response_body_bytes = len(decoded_body)
                        self._total_response_body_bytes += response_body_bytes
                        self._max_response_body_bytes = max(self._max_response_body_bytes, response_body_bytes)
                        self._last_response_body_bytes = response_body_bytes
                        self._total_decoded_response_body_bytes += decoded_response_body_bytes
                        self._max_decoded_response_body_bytes = max(
                            self._max_decoded_response_body_bytes,
                            decoded_response_body_bytes,
                        )
                        self._last_decoded_response_body_bytes = decoded_response_body_bytes
                        if self._is_gzip_encoded(content_encoding):
                            self._gzip_responses += 1
                        if retry_after_seconds is not None:
                            self._retry_after_responses += 1
                            self._max_retry_after_seconds = max(self._max_retry_after_seconds, retry_after_seconds)
                            self._last_retry_after_seconds = retry_after_seconds
                    self._mark_connection_used()
                    response_connection_header = response_getheader("Connection") if response_getheader else None
                    if str(response_connection_header or "").lower() == "close":
                        with self._stats_lock:
                            self._connection_close_responses += 1
                        self.close()
                    if response.status >= 400:
                        raw = decoded_body.decode("utf-8", errors="replace")
                        with self._stats_lock:
                            self._error_responses += 1
                        raise BackendRequestError(
                            f"{self.service} request failed: {response.status} {raw}",
                            status_code=response.status,
                            service=self.service_code,
                            retry_after_seconds=retry_after_seconds,
                        )
                    try:
                        data = json.loads(decoded_body) if decoded_body else {}
                    except json.JSONDecodeError as exc:
                        with self._stats_lock:
                            self._error_responses += 1
                            self._invalid_json_responses += 1
                            self._last_error = f"JSONDecodeError: {exc.msg}"
                        self.close()
                        raise BackendProtocolError(
                            f"{self.service} returned invalid JSON",
                            service=self.service_code,
                        ) from exc
                    self._record_circuit_success(circuit_context)
                    return response.status, data, elapsed_ms
                except BackendRequestError:
                    raise
                except (http.client.HTTPException, OSError, TimeoutError) as exc:
                    last_error = exc
                    with self._stats_lock:
                        self._last_error = f"{exc.__class__.__name__}: {exc}"
                        if attempt == 0:
                            self._stale_reconnects += 1
                    self.close()
                    if attempt == 0:
                        continue
                    raise exc
        except Exception as exc:
            self._record_circuit_exception(circuit_context, exc)
            raise
        finally:
            self._release_request_slot(slot_acquired)
        raise last_error or RuntimeError(f"{self.service} request failed")

    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                "connection_reuse": "thread_local_keep_alive",
                "base_url": self.base_url,
                "service": self.service_code,
                "max_idle_seconds": self.max_idle_seconds,
                "max_active_requests": self.max_active_requests,
                "connection_acquire_timeout_seconds": self.connection_acquire_timeout_seconds,
                "requests_started": self._requests_started,
                "active_requests": self._active_requests,
                "max_active_requests_observed": self._max_active_requests_observed,
                "connection_acquire_wait_events": self._connection_acquire_wait_events,
                "connection_acquire_wait_timeouts": self._connection_acquire_wait_timeouts,
                "total_connection_acquire_wait_ms": round(self._total_connection_acquire_wait_ms, 3),
                "avg_connection_acquire_wait_ms": round(
                    self._total_connection_acquire_wait_ms / self._connection_acquire_wait_events,
                    3,
                )
                if self._connection_acquire_wait_events
                else 0.0,
                "max_connection_acquire_wait_ms": round(self._max_connection_acquire_wait_ms, 3),
                "last_connection_acquire_wait_ms": round(self._last_connection_acquire_wait_ms, 3),
                "request_attempts": self._request_attempts,
                "responses_received": self._responses_received,
                "error_responses": self._error_responses,
                "invalid_json_responses": self._invalid_json_responses,
                "connections_opened": self._connections_opened,
                "open_connections": len(self._connections),
                "connection_reuses": self._connection_reuses,
                "idle_reconnects": self._idle_reconnects,
                "stale_reconnects": self._stale_reconnects,
                "connection_close_responses": self._connection_close_responses,
                "gzip_responses": self._gzip_responses,
                "retry_after_responses": self._retry_after_responses,
                "max_retry_after_seconds": round(self._max_retry_after_seconds, 3),
                "last_retry_after_seconds": round(self._last_retry_after_seconds, 3),
                "total_elapsed_ms": round(self._total_elapsed_ms, 3),
                "avg_elapsed_ms": round(self._total_elapsed_ms / self._responses_received, 3)
                if self._responses_received
                else 0.0,
                "max_elapsed_ms": round(self._max_elapsed_ms, 3),
                "last_elapsed_ms": round(self._last_elapsed_ms, 3),
                "total_request_body_bytes": self._total_request_body_bytes,
                "max_request_body_bytes": self._max_request_body_bytes,
                "last_request_body_bytes": self._last_request_body_bytes,
                "total_response_body_bytes": self._total_response_body_bytes,
                "max_response_body_bytes": self._max_response_body_bytes,
                "last_response_body_bytes": self._last_response_body_bytes,
                "total_decoded_response_body_bytes": self._total_decoded_response_body_bytes,
                "max_decoded_response_body_bytes": self._max_decoded_response_body_bytes,
                "last_decoded_response_body_bytes": self._last_decoded_response_body_bytes,
                "last_error": self._last_error,
                "circuit_breaker": self._circuit_stats_locked(),
            }

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            self._close_tracked_connection(connection)
            self._local.connection = None
            self._local.last_used_at = None
            self._local.connection_generation = None

    def close_all(self) -> None:
        with self._stats_lock:
            connections = list(self._connections)
            self._connections.clear()
            self._connection_generation += 1
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass
        self._local.connection = None
        self._local.last_used_at = None
        self._local.connection_generation = None

    def _acquire_request_slot(self) -> bool:
        slots = self._request_slots
        if slots is None:
            return False
        started = time.perf_counter()
        timeout = self.connection_acquire_timeout_seconds
        if timeout <= 0:
            acquired = slots.acquire(blocking=False)
        else:
            acquired = slots.acquire(timeout=timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000
        with self._stats_lock:
            self._connection_acquire_wait_events += 1
            self._total_connection_acquire_wait_ms += elapsed_ms
            self._max_connection_acquire_wait_ms = max(self._max_connection_acquire_wait_ms, elapsed_ms)
            self._last_connection_acquire_wait_ms = elapsed_ms
            if acquired:
                self._active_requests += 1
                self._max_active_requests_observed = max(self._max_active_requests_observed, self._active_requests)
            else:
                self._connection_acquire_wait_timeouts += 1
                self._last_error = (
                    "BackendConnectionPoolTimeoutError: "
                    f"waited {self.connection_acquire_timeout_seconds:.3f}s"
                )
        if not acquired:
            raise BackendConnectionPoolTimeoutError(self.service_code, timeout)
        return True

    def _release_request_slot(self, acquired: bool) -> None:
        if not acquired or self._request_slots is None:
            return
        with self._stats_lock:
            self._active_requests = max(0, self._active_requests - 1)
        self._request_slots.release()

    def _close_tracked_connection(self, connection: http.client.HTTPConnection) -> None:
        try:
            connection.close()
        finally:
            with self._stats_lock:
                self._connections.discard(connection)

    def _target_path(self, path: str) -> str:
        suffix = path if str(path).startswith("/") else f"/{path}"
        return f"{self.base_path}{suffix}" if self.base_path else suffix

    def _connection(self, timeout: int | float) -> http.client.HTTPConnection:
        connection = getattr(self._local, "connection", None)
        connection_generation = getattr(self._local, "connection_generation", None)
        if connection is not None and connection_generation != self._connection_generation:
            self.close()
            connection = None
        if connection is None:
            connection = self._new_connection(timeout)
            self._local.connection = connection
            self._local.connection_generation = self._connection_generation
        else:
            if self._idle_expired():
                with self._stats_lock:
                    self._idle_reconnects += 1
                self.close()
                connection = self._new_connection(timeout)
                self._local.connection = connection
                self._local.connection_generation = self._connection_generation
            else:
                with self._stats_lock:
                    self._connection_reuses += 1
                connection.timeout = timeout
                sock = getattr(connection, "sock", None)
                if sock is not None:
                    try:
                        sock.settimeout(timeout)
                    except OSError:
                        self.close()
                        connection = self._new_connection(timeout)
                        self._local.connection = connection
                        self._local.connection_generation = self._connection_generation
        return connection

    def _new_connection(self, timeout: int | float) -> http.client.HTTPConnection:
        if self.scheme == "https":
            connection = http.client.HTTPSConnection(self.host, self.port, timeout=timeout)
        else:
            connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        with self._stats_lock:
            self._connections_opened += 1
            self._connections.add(connection)
        return connection

    def _idle_expired(self) -> bool:
        if self.max_idle_seconds <= 0:
            return False
        last_used_at = getattr(self._local, "last_used_at", None)
        if not isinstance(last_used_at, (int, float)):
            return False
        return time.monotonic() - float(last_used_at) > self.max_idle_seconds

    def _mark_connection_used(self) -> None:
        self._local.last_used_at = time.monotonic()

    def _decode_response_body(self, raw_body: bytes, content_encoding: str | None) -> bytes:
        if self._is_gzip_encoded(content_encoding):
            return gzip.decompress(raw_body)
        return raw_body

    def _is_gzip_encoded(self, content_encoding: str | None) -> bool:
        return "gzip" in str(content_encoding or "").lower()

    def _parse_retry_after_header(self, value: str | None) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            seconds = float(text)
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError, OverflowError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
        if not math.isfinite(seconds):
            return None
        return max(0.0, seconds)

    def _circuit_enabled(self) -> bool:
        return self.circuit_failure_threshold > 0 and self.circuit_cooldown_seconds > 0

    def _enter_circuit(self) -> str:
        if not self._circuit_enabled():
            return "disabled"
        now = time.monotonic()
        with self._stats_lock:
            if self._circuit_state == "open":
                open_until = self._circuit_open_until or now
                if now >= open_until:
                    self._circuit_state = "half_open"
                    self._circuit_half_open_events += 1
                    self._circuit_half_open_in_flight = 0
                else:
                    self._circuit_short_circuits += 1
                    self._last_error = f"BackendCircuitOpenError: retry after {open_until - now:.3f}s"
                    raise BackendCircuitOpenError(self.service_code, open_until - now)
            if self._circuit_state == "half_open":
                if self._circuit_half_open_in_flight >= self.circuit_half_open_max_calls:
                    self._circuit_short_circuits += 1
                    open_until = self._circuit_open_until or now
                    retry_after = max(0.001, open_until - now)
                    self._last_error = f"BackendCircuitOpenError: half-open probe already in flight"
                    raise BackendCircuitOpenError(self.service_code, retry_after)
                self._circuit_half_open_in_flight += 1
                return "half_open"
            return "closed"

    def _record_circuit_success(self, context: str) -> None:
        if not self._circuit_enabled():
            return
        with self._stats_lock:
            if context == "half_open":
                self._circuit_half_open_in_flight = max(0, self._circuit_half_open_in_flight - 1)
            if self._circuit_state == "half_open":
                self._circuit_recovery_events += 1
            self._circuit_state = "closed"
            self._circuit_consecutive_failures = 0
            self._circuit_opened_at = None
            self._circuit_open_until = None

    def _record_circuit_exception(self, context: str, exc: BaseException) -> None:
        if not self._circuit_enabled():
            return
        with self._stats_lock:
            if context == "half_open":
                self._circuit_half_open_in_flight = max(0, self._circuit_half_open_in_flight - 1)
            if isinstance(exc, BackendConnectionPoolTimeoutError):
                return
            if not transient_backend_error(exc):
                if self._circuit_state == "half_open":
                    self._circuit_state = "closed"
                    self._circuit_consecutive_failures = 0
                    self._circuit_opened_at = None
                    self._circuit_open_until = None
                return
            self._circuit_consecutive_failures += 1
            if self._circuit_state == "half_open" or self._circuit_consecutive_failures >= self.circuit_failure_threshold:
                now = time.monotonic()
                if self._circuit_state != "open":
                    self._circuit_open_events += 1
                self._circuit_state = "open"
                self._circuit_opened_at = now
                self._circuit_open_until = now + self.circuit_cooldown_seconds
                self._circuit_half_open_in_flight = 0

    def _circuit_stats_locked(self) -> dict[str, Any]:
        now = time.monotonic()
        open_until = self._circuit_open_until
        retry_after_seconds = max(0.0, float(open_until - now)) if open_until is not None else 0.0
        return {
            "enabled": self._circuit_enabled(),
            "state": self._circuit_state if self._circuit_enabled() else "disabled",
            "failure_threshold": self.circuit_failure_threshold,
            "cooldown_seconds": self.circuit_cooldown_seconds,
            "half_open_max_calls": self.circuit_half_open_max_calls,
            "consecutive_failures": self._circuit_consecutive_failures,
            "open_events": self._circuit_open_events,
            "short_circuits": self._circuit_short_circuits,
            "half_open_events": self._circuit_half_open_events,
            "half_open_in_flight": self._circuit_half_open_in_flight,
            "recovery_events": self._circuit_recovery_events,
            "retry_after_seconds": round(retry_after_seconds, 3),
        }


@dataclass(frozen=True)
class ReservedEnginePlan:
    name: str
    replacement_path: str
    required_components: tuple[str, ...]


RESERVED_ENGINE_PLANS = {
    "typesense": ReservedEnginePlan(
        name="typesense",
        replacement_path="implement TypesenseSearchEngine behind the SearchEngine interface",
        required_components=(
            "Typesense collection schema for ProductDocument fields",
            "multimodal text and image embedding pipeline",
            "filter mapping for mall_id, status, and category",
        ),
    ),
    "qdrant": ReservedEnginePlan(
        name="qdrant",
        replacement_path="implement QdrantSearchEngine with OpenCLIP embeddings behind the SearchEngine interface",
        required_components=(
            "Qdrant collection for ProductDocument vectors and payloads",
            "OpenCLIP text and image embedding service",
            "payload filter mapping for mall_id, status, and category",
        ),
    ),
}


class LocalSearchEngine(SearchEngine):
    """Small deterministic engine for local development and unit tests."""

    name = "local"

    def __init__(self, products: Iterable[ProductDocument] | None = None):
        self._products: dict[str, ProductDocument] = {}
        self._records: dict[str, LocalProductRecord] = {}
        self._records_by_mall_id: dict[str, dict[str, LocalProductRecord]] = {}
        self._lock = threading.RLock()
        self._expanded_terms_cache: dict[tuple[int, str, int], frozenset[str]] = {}
        self._expanded_terms_synonym_digests: dict[int, str] = {}
        self._expanded_terms_lock = threading.RLock()
        if products:
            self.upsert_products(products)

    def search(self, query: EngineQuery) -> list[EngineHit]:
        hits: list[EngineHit] = []
        text_query = local_text_query(query.q, query.query_synonyms) if query.q else None
        image_query_terms = frozenset(text_query.terms) if text_query else frozenset()
        synonym_cache_token = self._custom_synonym_cache_token(query.query_synonyms)
        prepared_filters = prepare_product_filters(query)
        normalized_categories = normalized_inferred_categories(query.inferred_categories)
        with self._lock:
            if query.strict_mall_filter and query.mall_id:
                records = list(self._records_by_mall_id.get(query.mall_id, {}).values())
            else:
                records = list(self._records.values())
        for record in records:
            product = record.product
            if not product_matches_prepared_filters(product, prepared_filters):
                continue
            has_text_signal = bool(query.q or query.inferred_categories)
            text_score = self._text_score(text_query, record, query.query_synonyms, synonym_cache_token) if text_query else 0.0
            category_intent_score = self._category_intent_score(normalized_categories, record)
            if category_intent_score:
                text_score = max(text_score, category_intent_score)
            image_score = self._image_score(query, record, image_query_terms, synonym_cache_token) if query.image_data_url else 0.0
            if has_text_signal and query.image_data_url:
                score = (query.text_weight * text_score) + (query.image_weight * image_score)
            elif has_text_signal:
                score = text_score
            else:
                score = image_score
            if score <= 0:
                continue
            hits.append(
                EngineHit(
                    document=product,
                    score=round(score, 6),
                    source_scores={
                        "text": round(text_score, 6),
                        "image": round(image_score, 6),
                        "category_intent": round(category_intent_score, 6),
                    },
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.document.mall_id or "", hit.document.product_id))
        return hits[: query.limit]

    def upsert_products(self, products: Iterable[ProductDocument]) -> dict[str, Any]:
        count = 0
        legacy_deleted = 0
        with self._lock:
            for product in products:
                document_id = product_document_id(product.mall_id, product.product_id)
                existing = self._records.get(document_id)
                if existing is not None:
                    self._remove_record_from_mall_index_locked(document_id, existing)
                record = local_product_record(product)
                self._products[document_id] = product
                self._records[document_id] = record
                self._records_by_mall_id.setdefault(product.mall_id or "", {})[document_id] = record
                legacy_id = legacy_product_document_id(product.mall_id, product.product_id)
                if legacy_id and legacy_id != document_id:
                    if self._products.pop(legacy_id, None) is not None:
                        legacy_record = self._records.pop(legacy_id, None)
                        if legacy_record is not None:
                            self._remove_record_from_mall_index_locked(legacy_id, legacy_record)
                        legacy_deleted += 1
                count += 1
        if count or legacy_deleted:
            self._clear_expanded_terms_cache()
        return {"indexed": count, "legacy_deleted": legacy_deleted}

    def delete_products(self, product_ids: Iterable[str]) -> dict[str, Any]:
        deleted = 0
        with self._lock:
            for product_id in product_ids:
                if self._products.pop(product_id, None) is not None:
                    record = self._records.pop(product_id, None)
                    if record is not None:
                        self._remove_record_from_mall_index_locked(product_id, record)
                    deleted += 1
        if deleted:
            self._clear_expanded_terms_cache()
        return {"deleted": deleted}

    def health(self) -> dict[str, Any]:
        with self._lock:
            product_count = len(self._products)
            mall_bucket_count = len(self._records_by_mall_id)
        return {"engine": self.name, "products": product_count, "mall_buckets": mall_bucket_count, "ready": True}

    def _remove_record_from_mall_index_locked(self, document_id: str, record: LocalProductRecord) -> None:
        bucket_key = record.product.mall_id or ""
        bucket = self._records_by_mall_id.get(bucket_key)
        if bucket is None:
            return
        bucket.pop(document_id, None)
        if not bucket:
            self._records_by_mall_id.pop(bucket_key, None)

    def _visible_for_query(self, product: ProductDocument, query: EngineQuery) -> bool:
        return product_matches_query_filters(product, query)

    def _text_score(
        self,
        query: LocalTextQuery,
        record: LocalProductRecord,
        query_synonyms: Mapping[str, Iterable[str]] | None = None,
        synonym_cache_token: tuple[int, str] | None = None,
    ) -> float:
        query_terms = query.terms
        doc_text = record.doc_text
        doc_terms = self._expanded_record_terms(record.doc_terms, query_synonyms, synonym_cache_token)
        if not query_terms:
            return 0.0
        matched = 0
        phrase_bonus = 0.0
        for term in query_terms:
            if term_matches_document(term, doc_text, doc_terms):
                matched += 1
        if query.normalized in doc_text:
            phrase_bonus = 0.35
        category_terms = self._expanded_record_terms(record.category_terms, query_synonyms, synonym_cache_token)
        category_text = record.category_text
        category_bonus = 0.25 if any(term_matches_document(term, category_text, category_terms) for term in query_terms) else 0.0
        return min(1.0, (matched / len(query_terms)) + phrase_bonus + category_bonus)

    def _category_intent_score(self, normalized_categories: tuple[str, ...], record: LocalProductRecord) -> float:
        product_category = record.category_text
        if not product_category:
            return 0.0
        for rank, category_text in enumerate(normalized_categories):
            if product_category == category_text or category_text in product_category or product_category in category_text:
                return max(0.65, 0.88 - (rank * 0.06))
        return 0.0

    def _image_score(
        self,
        query: EngineQuery,
        record: LocalProductRecord,
        query_terms: frozenset[str],
        synonym_cache_token: tuple[int, str] | None = None,
    ) -> float:
        if query.image_hash and record.image_hash and record.image_hash == query.image_hash:
            return 1.0
        if query.q:
            image_tags = self._expanded_record_terms(record.image_terms, query.query_synonyms, synonym_cache_token)
            category_terms = self._expanded_record_terms(record.category_terms, query.query_synonyms, synonym_cache_token)
            overlap = len(query_terms & (image_tags | category_terms))
            if overlap:
                return min(0.95, 0.45 + 0.15 * overlap)
        if record.has_image_url:
            return 0.35
        return 0.0

    def _expanded_record_terms(
        self,
        terms: frozenset[str],
        custom_synonyms: Mapping[str, Iterable[str]] | None = None,
        synonym_cache_token: tuple[int, str] | None = None,
    ) -> frozenset[str]:
        if not custom_synonyms:
            return terms
        token = synonym_cache_token or self._custom_synonym_cache_token(custom_synonyms)
        if token is None:
            return terms
        key = (token[0], token[1], id(terms))
        with self._expanded_terms_lock:
            cached = self._expanded_terms_cache.get(key)
            if cached is not None:
                return cached
        expanded = expanded_record_terms(terms, custom_synonyms)
        with self._expanded_terms_lock:
            return self._expanded_terms_cache.setdefault(key, expanded)

    def _custom_synonym_cache_token(
        self,
        custom_synonyms: Mapping[str, Iterable[str]] | None = None,
    ) -> tuple[int, str] | None:
        if not custom_synonyms:
            return None
        digest = hashlib.sha256()
        for term in sorted(custom_synonyms.keys(), key=str):
            digest.update(str(term).encode("utf-8"))
            digest.update(b"\0")
            for value in custom_synonyms.get(term, []):
                digest.update(str(value).encode("utf-8"))
                digest.update(b"\0")
            digest.update(b"\1")
        digest_hex = digest.hexdigest()
        synonym_id = id(custom_synonyms)
        with self._expanded_terms_lock:
            previous_digest = self._expanded_terms_synonym_digests.get(synonym_id)
            if previous_digest is not None and previous_digest != digest_hex:
                self._expanded_terms_cache.clear()
            self._expanded_terms_synonym_digests[synonym_id] = digest_hex
        return synonym_id, digest_hex

    def _clear_expanded_terms_cache(self) -> None:
        with self._expanded_terms_lock:
            self._expanded_terms_cache.clear()
            self._expanded_terms_synonym_digests.clear()


def qwen_health_contract_problems(
    data: Any,
    *,
    expected_model: str,
    expected_dimensions: int,
) -> list[str]:
    problems: list[str] = []
    health = data if isinstance(data, dict) else {}
    if health.get("ready") is not True:
        problems.append("qwen_health.ready")
    if health.get("loadError"):
        problems.append("qwen_health.loadError")
    if str(health.get("model") or "").strip() != str(expected_model or "").strip():
        problems.append("qwen_health.model")
    try:
        actual_dimensions = int(str(health.get("dimensions")).strip())
    except (TypeError, ValueError):
        actual_dimensions = None
    if actual_dimensions != int(expected_dimensions):
        problems.append("qwen_health.dimensions")
    return sorted(set(problems))


class MarqoSearchEngine(SearchEngine):
    name = "marqo"

    def __init__(
        self,
        marqo_url: str,
        index_name: str,
        model_name: str = "Marqo/marqo-ecommerce-embeddings-L",
        embedding_backend: str = "native",
        qwen_embedding_url: str | None = None,
        qwen_embedding_dimensions: int = 2048,
        qwen_model: str = "Qwen/Qwen3-VL-Embedding-2B",
        qwen_embedding_proxy_api_key: str | None = None,
        qwen_query_embedding_cache_path: Path | None = None,
        qwen_query_runtime_text_cache_entries: int = 2048,
        qwen_query_runtime_image_cache_entries: int = 512,
        image_download_thread_count: int = 3,
        search_timeout_seconds: float = 15.0,
        qwen_query_timeout_seconds: float = 15.0,
        qwen_mixed_query_parallelism: int = 8,
        search_retry_count: int = 1,
        search_retry_delay_seconds: float = 0.1,
        backend_retry_after_max_seconds: float = 2.0,
        backend_http_max_idle_seconds: float = 55.0,
        backend_http_max_active_requests: int = 96,
        backend_http_connection_acquire_timeout_seconds: float = 1.0,
        backend_circuit_failure_threshold: int = 5,
        backend_circuit_cooldown_seconds: float = 5.0,
        backend_circuit_half_open_max_calls: int = 1,
        admin_metrics_health_cache_seconds: float = 2.0,
        add_documents_batch_size: int = MARQO_ADD_DOCUMENTS_BATCH_SIZE,
        add_documents_max_request_bytes: int = MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES,
        delete_documents_batch_size: int = MARQO_DELETE_DOCUMENTS_BATCH_SIZE,
        text_auxiliary_weight: float = 0.12,
        text_auxiliary_candidate_multiplier: float = 1.0,
        text_auxiliary_search_parallelism: int = 8,
    ):
        self.marqo_url = validate_marqo_url_value(marqo_url)
        self.index_name = index_name
        self.model_name = model_name
        requested_embedding_backend = embedding_backend.strip().lower() if embedding_backend else "native"
        if requested_embedding_backend not in {"native", "qwen", "gemini"}:
            raise ValueError("embedding_backend must be one of: native, qwen, gemini")
        self.embedding_provider = requested_embedding_backend
        self.embedding_backend = "qwen" if requested_embedding_backend == "gemini" else requested_embedding_backend
        self.embedding_service_name = "Gemini embedding" if requested_embedding_backend == "gemini" else "Qwen embedding"
        self.qwen_embedding_url = validate_marqo_url_value(qwen_embedding_url or "http://localhost:8098")
        self.qwen_embedding_dimensions = int(qwen_embedding_dimensions)
        self.qwen_model = qwen_model
        self.qwen_embedding_proxy_api_key = str(qwen_embedding_proxy_api_key or "").strip()
        self.qwen_query_embedding_cache_path = qwen_query_embedding_cache_path
        self.qwen_query_embedding_cache = load_qwen_query_embedding_cache(
            qwen_query_embedding_cache_path,
            expected_dimensions=self.qwen_embedding_dimensions,
        )
        self._qwen_runtime_query_embedding_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._qwen_runtime_query_embedding_cache_lock = threading.RLock()
        self._qwen_runtime_text_query_embedding_cache_max_entries = max(0, int(qwen_query_runtime_text_cache_entries))
        self._qwen_runtime_image_query_embedding_cache_max_entries = max(0, int(qwen_query_runtime_image_cache_entries))
        self._qwen_runtime_query_embedding_cache_max_entries = (
            self._qwen_runtime_text_query_embedding_cache_max_entries
            + self._qwen_runtime_image_query_embedding_cache_max_entries
        )
        self._qwen_query_vector_inflight: dict[str, InflightQwenQueryVector] = {}
        self._qwen_query_vector_inflight_lock = threading.RLock()
        self._qwen_query_vector_stats_lock = threading.RLock()
        self._qwen_query_vector_wait_events = 0
        self._qwen_query_vector_wait_timeouts = 0
        self._qwen_query_vector_total_wait_seconds = 0.0
        self._qwen_query_vector_max_wait_seconds = 0.0
        self.image_download_thread_count = max(1, image_download_thread_count)
        self.search_timeout_seconds = max(0.001, float(search_timeout_seconds))
        self.qwen_query_timeout_seconds = max(0.001, float(qwen_query_timeout_seconds))
        self.qwen_mixed_query_parallelism = max(0, int(qwen_mixed_query_parallelism))
        if self.qwen_mixed_query_parallelism > 0:
            self.qwen_mixed_query_parallelism = max(2, self.qwen_mixed_query_parallelism)
        self._qwen_mixed_query_executor = (
            ThreadPoolExecutor(
                max_workers=self.qwen_mixed_query_parallelism,
                thread_name_prefix="haeorum-qwen-mixed",
            )
            if self.qwen_mixed_query_parallelism > 0
            else None
        )
        self.search_retry_count = max(0, int(search_retry_count))
        self.search_retry_delay_seconds = max(0.0, float(search_retry_delay_seconds))
        self.backend_retry_after_max_seconds = max(0.0, float(backend_retry_after_max_seconds))
        self.backend_http_max_idle_seconds = max(0.0, float(backend_http_max_idle_seconds))
        self.backend_http_max_active_requests = max(0, int(backend_http_max_active_requests))
        self.backend_http_connection_acquire_timeout_seconds = max(
            0.0,
            float(backend_http_connection_acquire_timeout_seconds),
        )
        self.backend_circuit_failure_threshold = max(0, int(backend_circuit_failure_threshold))
        self.backend_circuit_cooldown_seconds = max(0.0, float(backend_circuit_cooldown_seconds))
        self.backend_circuit_half_open_max_calls = max(1, int(backend_circuit_half_open_max_calls))
        self.admin_metrics_health_cache_seconds = max(0.0, float(admin_metrics_health_cache_seconds))
        self.add_documents_batch_size = max(1, int(add_documents_batch_size))
        self.add_documents_max_request_bytes = max(0, int(add_documents_max_request_bytes))
        self.delete_documents_batch_size = max(1, int(delete_documents_batch_size))
        self.text_auxiliary_weight = max(0.0, float(text_auxiliary_weight))
        self.text_auxiliary_candidate_multiplier = max(0.1, float(text_auxiliary_candidate_multiplier))
        self.text_auxiliary_search_parallelism = max(0, int(text_auxiliary_search_parallelism))
        self._text_auxiliary_search_executor = (
            ThreadPoolExecutor(
                max_workers=max(2, self.text_auxiliary_search_parallelism),
                thread_name_prefix="haeorum-text-aux",
            )
            if self.text_auxiliary_weight > 0 and self.text_auxiliary_search_parallelism > 0
            else None
        )
        self.last_upsert_stats: dict[str, Any] = {}
        self.last_delete_stats: dict[str, Any] = {}
        self._health_cache_lock = threading.RLock()
        self._health_cache: dict[str, Any] | None = None
        self._health_cache_expires_at = 0.0
        self._marqo_http = BackendJsonHttpClient(
            self.marqo_url,
            "Marqo",
            max_idle_seconds=self.backend_http_max_idle_seconds,
            max_active_requests=self.backend_http_max_active_requests,
            connection_acquire_timeout_seconds=self.backend_http_connection_acquire_timeout_seconds,
            circuit_failure_threshold=self.backend_circuit_failure_threshold,
            circuit_cooldown_seconds=self.backend_circuit_cooldown_seconds,
            circuit_half_open_max_calls=self.backend_circuit_half_open_max_calls,
        )
        self._qwen_http = BackendJsonHttpClient(
            self.qwen_embedding_url,
            self.embedding_service_name,
            max_idle_seconds=self.backend_http_max_idle_seconds,
            max_active_requests=self.backend_http_max_active_requests,
            connection_acquire_timeout_seconds=self.backend_http_connection_acquire_timeout_seconds,
            circuit_failure_threshold=self.backend_circuit_failure_threshold,
            circuit_cooldown_seconds=self.backend_circuit_cooldown_seconds,
            circuit_half_open_max_calls=self.backend_circuit_half_open_max_calls,
            default_headers=embedding_proxy_headers(self.qwen_embedding_proxy_api_key),
        )
        self._index_checked = False

    def close(self) -> None:
        self._marqo_http.close_all()
        self._qwen_http.close_all()
        if self._qwen_mixed_query_executor is not None:
            self._qwen_mixed_query_executor.shutdown(wait=False, cancel_futures=True)
        if self._text_auxiliary_search_executor is not None:
            self._text_auxiliary_search_executor.shutdown(wait=False, cancel_futures=True)

    def search(self, query: EngineQuery) -> list[EngineHit]:
        payload = self.build_search_payload(query)
        payload_limit = int(payload.get("limit") or query.limit)
        vector_field = payload.pop("_haeorumVectorField", None)
        vector_source = payload.pop("_haeorumVectorSource", None)
        rerank_text = bool(payload.pop("_haeorumRerankText", should_rerank_text_query(query)))
        auxiliary_payload = payload.pop("_haeorumAuxTextPayload", None)
        auxiliary_weight = float(payload.pop("_haeorumAuxTextWeight", 0.0) or 0.0)
        auxiliary_field = None
        auxiliary_source = None
        if isinstance(auxiliary_payload, dict):
            auxiliary_payload = dict(auxiliary_payload)
            auxiliary_field = auxiliary_payload.pop("_haeorumVectorField", None)
            auxiliary_source = auxiliary_payload.pop("_haeorumVectorSource", None)
        else:
            auxiliary_payload = None
            auxiliary_weight = 0.0
        data, auxiliary_data = self._execute_search_payloads(payload, auxiliary_payload)
        hits = []
        text_to_image_auxiliary = bool(auxiliary_payload is not None and query.q and not query.image_data_url)
        needs_full_scan = rerank_text or text_to_image_auxiliary
        prepared_filters = prepare_product_filters(query)
        for raw_hit in data.get("hits", []):
            try:
                if not isinstance(raw_hit, dict):
                    raise ValueError("Marqo hit must be an object")
                product = marqo_hit_to_product(raw_hit)
                if not product_matches_prepared_filters(product, prepared_filters):
                    continue
                score = float(raw_hit.get("_score") or 0.0)
            except Exception:
                continue
            source_scores = {"marqo": score}
            if vector_source:
                source_scores[str(vector_source)] = score
            if vector_field:
                source_scores[self.display_vector_field_name(str(vector_field))] = score
            hits.append(
                EngineHit(
                    document=product,
                    score=score,
                    source_scores=source_scores,
                )
            )
            if not needs_full_scan and len(hits) >= query.limit:
                break
        if auxiliary_data is not None and auxiliary_weight > 0:
            hits = self.apply_text_auxiliary_scores(
                hits,
                auxiliary_data,
                auxiliary_weight=auxiliary_weight,
                auxiliary_field=str(auxiliary_field or QWEN_TEXT_VECTOR_FIELD),
                auxiliary_source=str(auxiliary_source or "text_auxiliary"),
            )
        if needs_full_scan:
            if rerank_text:
                hits = rerank_text_hits(query, hits)
                return hits[: max(query.limit, payload_limit)]
            if text_to_image_auxiliary:
                hits = rerank_text_to_image_hits(query, hits)
            return hits[: query.limit]
        return hits[: query.limit]

    def _execute_search_payloads(
        self,
        payload: dict[str, Any],
        auxiliary_payload: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if auxiliary_payload is None:
            return self._execute_search_payload(payload), None
        executor = self._text_auxiliary_search_executor
        if executor is None:
            primary = self._execute_search_payload(payload)
            auxiliary = self._execute_search_payload(auxiliary_payload)
            return primary, auxiliary
        primary_future = executor.submit(self._execute_search_payload, payload)
        auxiliary_future = executor.submit(self._execute_search_payload, auxiliary_payload)
        try:
            return primary_future.result(), auxiliary_future.result()
        except BaseException:
            primary_future.cancel()
            auxiliary_future.cancel()
            raise

    def _execute_search_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        _, data, _ = self._request_with_retries(
            "POST",
            f"/indexes/{self.index_name}/search",
            payload,
            timeout=self.search_timeout_seconds,
            retry_count=self.search_retry_count,
            retry_delay_seconds=self.search_retry_delay_seconds,
        )
        return data

    def apply_text_auxiliary_scores(
        self,
        hits: list[EngineHit],
        auxiliary_data: Mapping[str, Any],
        *,
        auxiliary_weight: float,
        auxiliary_field: str,
        auxiliary_source: str,
    ) -> list[EngineHit]:
        auxiliary_scores = auxiliary_scores_by_document_key(auxiliary_data)
        if not auxiliary_scores:
            return hits
        display_field = self.display_vector_field_name(auxiliary_field)
        weighted: list[tuple[int, EngineHit]] = []
        weight = max(0.0, float(auxiliary_weight))
        for rank, hit in enumerate(hits):
            document_key = product_document_id(hit.document.mall_id, hit.document.product_id)
            auxiliary_score = float(auxiliary_scores.get(document_key, 0.0))
            primary_score = float(hit.score)
            combined_score = (primary_score * (1.0 - weight)) + (auxiliary_score * weight)
            source_scores = dict(hit.source_scores)
            source_scores["marqo_primary"] = round(primary_score, 6)
            source_scores["marqo"] = round(combined_score, 6)
            source_scores["hybrid"] = round(combined_score, 6)
            source_scores[auxiliary_source] = round(auxiliary_score, 6)
            source_scores[display_field] = round(auxiliary_score, 6)
            weighted.append(
                (
                    rank,
                    EngineHit(
                        document=hit.document,
                        score=round(combined_score, 6),
                        source_scores=source_scores,
                    ),
                )
            )
        weighted.sort(key=lambda item: (-item[1].score, item[0]))
        return [hit for _rank, hit in weighted]

    def display_vector_field_name(self, field_name: str) -> str:
        if self.embedding_provider != "gemini":
            return field_name
        return field_name.replace(QWEN_TEXT_VECTOR_FIELD, GEMINI_TEXT_VECTOR_FIELD).replace(
            QWEN_IMAGE_VECTOR_FIELD,
            GEMINI_IMAGE_VECTOR_FIELD,
        )

    def upsert_products(self, products: Iterable[ProductDocument]) -> dict[str, Any]:
        if self.embedding_backend == "qwen":
            return self.upsert_qwen_products(products)
        product_batches = iter(chunked_products(products, self.add_documents_batch_size))
        first_batch = next(product_batches, None)
        if not first_batch:
            return {"indexed": 0}
        self.ensure_index()

        def payload_batches() -> Iterable[dict[str, Any]]:
            for product_batch in chain_first(first_batch, product_batches):
                docs = [product_to_marqo_doc(product) for product in product_batch]
                for doc_batch in self.chunk_marqo_documents(docs):
                    yield self.build_upsert_payload_from_docs(doc_batch)

        return self.upsert_document_payload_batches(payload_batches())

    def chunk_marqo_documents(self, docs: Iterable[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
        base_payload = {
            "tensorFields": [*TEXT_FIELDS, IMAGE_FIELD],
            "mediaDownloadThreadCount": self.image_download_thread_count,
        }
        return chunk_marqo_documents_by_limits(
            docs,
            self.add_documents_batch_size,
            self.add_documents_max_request_bytes,
            base_payload,
        )

    def split_upsert_payload_by_limits(self, payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
        docs = list(payload.get("documents") or [])
        if not docs:
            return
        base_payload = {key: value for key, value in payload.items() if key != "documents"}
        for doc_batch in chunk_marqo_documents_by_limits(
            docs,
            self.add_documents_batch_size,
            self.add_documents_max_request_bytes,
            base_payload,
        ):
            yield dict(base_payload, documents=doc_batch)

    def upsert_qwen_products(self, products: Iterable[ProductDocument]) -> dict[str, Any]:
        product_batches = iter(chunked_products(products, self.add_documents_batch_size))
        first_batch = next(product_batches, None)
        if not first_batch:
            return {"indexed": 0}
        self.ensure_index()

        def qwen_payload_batches() -> Iterable[dict[str, Any]]:
            for batch_payload in self.split_upsert_payload_by_limits(self.build_qwen_upsert_payload(first_batch)):
                yield batch_payload
            for batch in product_batches:
                for batch_payload in self.split_upsert_payload_by_limits(self.build_qwen_upsert_payload(batch)):
                    yield batch_payload

        return self.upsert_document_payload_batches(
            qwen_payload_batches()
        )

    def upsert_document_payload_batches(self, payload_batches: Iterable[dict[str, Any]]) -> dict[str, Any]:
        indexed = 0
        legacy_deleted = 0
        failed = 0
        failed_products: list[dict[str, Any]] = []
        response_batch_count = 0
        retained_responses: list[dict[str, Any]] = []
        last_response: dict[str, Any] | None = None
        batch_sizes: list[int] = []
        batch_request_bytes: list[int] = []
        for batch_payload in payload_batches:
            batch_docs = list(batch_payload.get("documents") or [])
            if not batch_docs:
                continue
            request_bytes = json_body_size(batch_payload)
            batch_sizes.append(len(batch_docs))
            batch_request_bytes.append(request_bytes)
            _, data, _ = self._request_with_retries(
                "POST",
                f"/indexes/{self.index_name}/documents",
                batch_payload,
                timeout=600,
                retry_count=self.search_retry_count,
                retry_delay_seconds=self.search_retry_delay_seconds,
            )
            response_batch_count += 1
            last_response = data
            if len(retained_responses) < MARQO_BATCH_RESPONSE_SAMPLE_LIMIT:
                retained_responses.append(data)
            product_ids = [doc["_id"] for doc in batch_docs]
            batch_failed = extract_marqo_item_failures(data, product_ids, "upsert_to_index")
            failed += len(batch_failed)
            append_failure_samples(failed_products, batch_failed)
            failed_document_ids = {
                str(failure.get("document_id") or "")
                for failure in batch_failed
                if str(failure.get("document_id") or "")
            }
            failed_public_ids = {failure["product_id"] for failure in batch_failed}
            successful_docs = [
                doc
                for doc in batch_docs
                if str(doc.get("_id") or "") not in failed_document_ids
                and str(doc.get("product_id") or "") not in failed_public_ids
            ]
            if batch_failed:
                indexed += max(0, len(batch_docs) - len(batch_failed))
            else:
                indexed += len(data.get("items", [])) if isinstance(data, dict) and data.get("items") else len(batch_docs)
            legacy_ids = legacy_document_ids_for_marqo_docs(successful_docs)
            if legacy_ids:
                cleanup = self.delete_products(legacy_ids)
                legacy_deleted += int(cleanup.get("deleted", 0) or 0)
                cleanup_failures = [
                    failure
                    for failure in cleanup.get("failed_products") or []
                    if isinstance(failure, dict)
                ]
                failed += max(int(cleanup.get("failed", 0) or 0), len(cleanup_failures))
                legacy_delete_failures = []
                for failure in cleanup_failures:
                    if not isinstance(failure, dict):
                        continue
                    legacy_delete_failures.append(
                        {
                            "product_id": str(failure.get("product_id") or failure.get("document_id") or ""),
                            "document_id": str(failure.get("document_id") or failure.get("product_id") or ""),
                            "reason": f"legacy_delete_from_index_failed: {failure.get('reason') or 'delete_from_index_failed'}",
                        }
                    )
                append_failure_samples(failed_products, legacy_delete_failures)
        stats = {
            "batch_count": len(batch_sizes),
            "batch_sizes": batch_sizes[:50],
            "batch_sizes_truncated": len(batch_sizes) > 50,
            "max_batch_size": max(batch_sizes) if batch_sizes else 0,
            "max_request_body_bytes": max(batch_request_bytes) if batch_request_bytes else 0,
            "request_body_limit_bytes": self.add_documents_max_request_bytes,
            "configured_batch_size": self.add_documents_batch_size,
            "response_batch_count": response_batch_count,
            "response_sample_limit": MARQO_BATCH_RESPONSE_SAMPLE_LIMIT,
            "response_retained_count": len(retained_responses),
            "responses_truncated": response_batch_count > len(retained_responses),
            "failed_product_sample_limit": MARQO_BATCH_FAILURE_SAMPLE_LIMIT,
            "failed_product_retained_count": len(failed_products),
            "failed_products_truncated": failed > len(failed_products),
        }
        self.last_upsert_stats = stats
        if not response_batch_count:
            return {"indexed": 0, **stats}
        return {
            "indexed": indexed,
            "failed": failed,
            "failed_products": failed_products,
            "legacy_deleted": legacy_deleted,
            **stats,
            "response": batch_response_payload(response_batch_count, retained_responses, last_response),
        }

    def delete_products(self, product_ids: Iterable[str]) -> dict[str, Any]:
        ids = [product_id for product_id in product_ids if product_id]
        if not ids:
            return {"deleted": 0}
        deleted = 0
        failed = 0
        failed_products: list[dict[str, Any]] = []
        response_batch_count = 0
        retained_responses: list[dict[str, Any]] = []
        last_response: dict[str, Any] | None = None
        batch_sizes = []
        for batch_ids in chunked_strings(ids, self.delete_documents_batch_size):
            _, data, _ = self._request_with_retries(
                "POST",
                f"/indexes/{self.index_name}/documents/delete-batch",
                batch_ids,
                timeout=120,
                retry_count=self.search_retry_count,
                retry_delay_seconds=self.search_retry_delay_seconds,
            )
            response_batch_count += 1
            last_response = data
            if len(retained_responses) < MARQO_BATCH_RESPONSE_SAMPLE_LIMIT:
                retained_responses.append(data)
            batch_sizes.append(len(batch_ids))
            batch_failed = extract_marqo_item_failures(data, batch_ids, "delete_from_index")
            failed += len(batch_failed)
            append_failure_samples(failed_products, batch_failed)
            deleted += max(0, len(batch_ids) - len(batch_failed))
        stats = {
            "delete_batch_count": len(batch_sizes),
            "delete_batch_sizes": batch_sizes[:50],
            "delete_batch_sizes_truncated": len(batch_sizes) > 50,
            "max_delete_batch_size": max(batch_sizes) if batch_sizes else 0,
            "configured_delete_batch_size": self.delete_documents_batch_size,
            "response_batch_count": response_batch_count,
            "response_sample_limit": MARQO_BATCH_RESPONSE_SAMPLE_LIMIT,
            "response_retained_count": len(retained_responses),
            "responses_truncated": response_batch_count > len(retained_responses),
            "failed_product_sample_limit": MARQO_BATCH_FAILURE_SAMPLE_LIMIT,
            "failed_product_retained_count": len(failed_products),
            "failed_products_truncated": failed > len(failed_products),
        }
        self.last_delete_stats = stats
        return {
            "deleted": deleted,
            "failed": failed,
            "failed_products": failed_products,
            **stats,
            "response": batch_response_payload(response_batch_count, retained_responses, last_response),
        }

    def _probe_health_backends(self) -> dict[str, Any]:
        root_error = None
        try:
            _, root, root_ms = self._request("GET", "/", timeout=10)
            marqo_ready = True
        except Exception as exc:
            root = {"ready": False, "error": str(exc), "error_type": exc.__class__.__name__}
            root_ms = None
            root_error = str(exc)
            marqo_ready = False
        stats = None
        qwen = None
        qwen_ready = True
        qwen_health_problems: list[str] = []
        if self.embedding_backend == "qwen":
            try:
                _, qwen, _ = self._qwen_request("GET", "/health", timeout=10)
                qwen_health_problems = qwen_health_contract_problems(
                    qwen,
                    expected_model=self.qwen_model,
                    expected_dimensions=self.qwen_embedding_dimensions,
                )
            except Exception as exc:
                qwen = {"ready": False, "error": str(exc)}
                qwen_health_problems = ["qwen_health"]
            qwen_ready = not qwen_health_problems
        try:
            _, stats, _ = self._request("GET", f"/indexes/{self.index_name}/stats", timeout=10)
        except Exception:
            stats = None
        provider_is_gemini = self.embedding_provider == "gemini"
        provider_health_problems = [
            problem.replace("qwen_", "gemini_", 1) for problem in qwen_health_problems
        ] if provider_is_gemini else qwen_health_problems
        ready = marqo_ready and qwen_ready
        return {
            "ready": ready,
            "ok": ready,
            "marqo_ready": marqo_ready,
            "marqo_error": root_error,
            "qwen_ready": qwen_ready if self.embedding_provider == "qwen" else None,
            "qwen_health_problems": qwen_health_problems if self.embedding_provider == "qwen" else [],
            "qwen": qwen if self.embedding_provider == "qwen" else None,
            "gemini_ready": qwen_ready if provider_is_gemini else None,
            "gemini_health_problems": provider_health_problems if provider_is_gemini else [],
            "gemini": qwen if provider_is_gemini else None,
            "marqo": root,
            "root_ms": root_ms,
            "stats": stats,
        }

    def _cached_health_backend_probe(self) -> tuple[dict[str, Any], dict[str, Any]]:
        ttl_seconds = self.admin_metrics_health_cache_seconds
        if ttl_seconds <= 0:
            data = self._probe_health_backends()
            return data, {
                "enabled": False,
                "hit": False,
                "ttl_seconds": ttl_seconds,
                "age_ms": 0.0,
            }
        now = time.monotonic()
        with self._health_cache_lock:
            cached = self._health_cache
            if cached is not None and now < self._health_cache_expires_at:
                age_ms = round((now - float(cached.get("_cached_at") or now)) * 1000, 3)
                return dict(cached["data"]), {
                    "enabled": True,
                    "hit": True,
                    "ttl_seconds": ttl_seconds,
                    "age_ms": age_ms,
                }
            data = self._probe_health_backends()
            cached_at = time.monotonic()
            self._health_cache = {"data": dict(data), "_cached_at": cached_at}
            self._health_cache_expires_at = cached_at + ttl_seconds
            return data, {
                "enabled": True,
                "hit": False,
                "ttl_seconds": ttl_seconds,
                "age_ms": 0.0,
            }

    def health(self) -> dict[str, Any]:
        backend_health, health_cache = self._cached_health_backend_probe()
        custom_embedding_enabled = self.embedding_backend == "qwen"
        provider_is_gemini = self.embedding_provider == "gemini"
        provider_transport_stats = self._qwen_http.stats() if custom_embedding_enabled else None
        query_cache_status = {
            "enabled": bool(self.qwen_query_embedding_cache_path),
            "path": str(self.qwen_query_embedding_cache_path) if self.qwen_query_embedding_cache_path else None,
            "entries": len(self.qwen_query_embedding_cache),
            **self.qwen_query_vector_status(),
        } if custom_embedding_enabled else None
        request_policy = {
            "search_timeout_seconds": self.search_timeout_seconds,
            "search_retry_count": self.search_retry_count,
            "search_retry_delay_seconds": self.search_retry_delay_seconds,
            "backend_retry_after_max_seconds": self.backend_retry_after_max_seconds,
            "backend_http_max_idle_seconds": self.backend_http_max_idle_seconds,
            "backend_http_max_active_requests": self.backend_http_max_active_requests,
            "backend_http_connection_acquire_timeout_seconds": (
                self.backend_http_connection_acquire_timeout_seconds
            ),
            "backend_circuit_failure_threshold": self.backend_circuit_failure_threshold,
            "backend_circuit_cooldown_seconds": self.backend_circuit_cooldown_seconds,
            "backend_circuit_half_open_max_calls": self.backend_circuit_half_open_max_calls,
            "admin_metrics_health_cache_seconds": self.admin_metrics_health_cache_seconds,
            "add_documents_batch_size": self.add_documents_batch_size,
            "add_documents_max_request_bytes": self.add_documents_max_request_bytes,
            "delete_documents_batch_size": self.delete_documents_batch_size,
        }
        transport = {"marqo": self._marqo_http.stats()}
        provider_fields: dict[str, Any] = {}
        if provider_is_gemini:
            request_policy["gemini_query_timeout_seconds"] = self.qwen_query_timeout_seconds
            transport["gemini"] = provider_transport_stats
            provider_fields.update(
                {
                    "gemini_model": self.qwen_model,
                    "gemini_embedding_dimensions": self.qwen_embedding_dimensions,
                    "gemini_ready": backend_health.get("gemini_ready"),
                    "gemini_health_problems": backend_health.get("gemini_health_problems") or [],
                    "gemini": backend_health.get("gemini"),
                    "gemini_query_embedding_cache": query_cache_status,
                }
            )
        elif self.embedding_provider == "qwen":
            request_policy["qwen_query_timeout_seconds"] = self.qwen_query_timeout_seconds
            transport["qwen"] = provider_transport_stats
            provider_fields.update(
                {
                    "qwen_model": self.qwen_model,
                    "qwen_embedding_dimensions": self.qwen_embedding_dimensions,
                    "qwen_ready": backend_health.get("qwen_ready"),
                    "qwen_health_problems": backend_health.get("qwen_health_problems") or [],
                    "qwen": backend_health.get("qwen"),
                    "qwen_query_embedding_cache": query_cache_status,
                }
            )
        return {
            "engine": self.name,
            "ready": backend_health.get("ready"),
            "ok": backend_health.get("ok"),
            "model": self.model_name,
            "embedding_backend": self.embedding_provider,
            "marqo_ready": backend_health.get("marqo_ready"),
            "marqo_error": backend_health.get("marqo_error"),
            **provider_fields,
            "request_policy": request_policy,
            "transport": transport,
            "health_cache": health_cache,
            "marqo": backend_health.get("marqo"),
            "root_ms": backend_health.get("root_ms"),
            "stats": backend_health.get("stats"),
        }

    def create_index(self) -> dict[str, Any]:
        settings = self.qwen_index_settings() if self.embedding_backend == "qwen" else {
            "model": self.model_name,
            "treatUrlsAndPointersAsImages": True,
            "normalizeEmbeddings": True,
        }
        _, data, _ = self._request_with_retries(
            "POST",
            f"/indexes/{self.index_name}",
            settings,
            timeout=600,
            retry_count=self.search_retry_count,
            retry_delay_seconds=self.search_retry_delay_seconds,
        )
        self._index_checked = True
        return data

    def ensure_index(self) -> None:
        if self._index_checked:
            return
        try:
            self._request_with_retries(
                "GET",
                f"/indexes/{self.index_name}/settings",
                timeout=20,
                retry_count=self.search_retry_count,
                retry_delay_seconds=self.search_retry_delay_seconds,
            )
            self._index_checked = True
        except BackendRequestError as exc:
            if exc.status_code != 404:
                raise
            self.create_index()

    def build_search_payload(self, query: EngineQuery) -> dict[str, Any]:
        if self.embedding_backend == "qwen":
            return self.build_qwen_search_payload(query)
        q: str | dict[str, float]
        searchable_attributes: list[str] | None = None
        expanded_text_query = expanded_query_text(query.q, query.query_synonyms) if query.q else None
        text_query = append_inferred_categories(expanded_text_query, query.inferred_categories)
        if text_query and query.image_data_url:
            q = {
                query.image_data_url: query.image_weight,
                text_query: query.text_weight,
            }
        elif query.image_data_url:
            q = {query.image_data_url: 1.0}
            searchable_attributes = [IMAGE_FIELD]
        else:
            q = text_query or ""
            searchable_attributes = TEXT_FIELDS

        payload: dict[str, Any] = {
            "q": q,
            "searchMethod": "TENSOR",
            "limit": marqo_candidate_limit(query),
            "attributesToRetrieve": attributes_to_retrieve_for_query(query),
        }
        filters = build_filter_terms(query)
        if filters:
            payload["filter"] = filters
        if searchable_attributes:
            payload["searchableAttributes"] = searchable_attributes
        return payload

    def build_upsert_payload(self, products: Iterable[ProductDocument]) -> dict[str, Any]:
        return self.build_upsert_payload_from_docs(product_to_marqo_doc(product) for product in products)

    def build_upsert_payload_from_docs(self, docs: Iterable[dict[str, Any]]) -> dict[str, Any]:
        return {
            "documents": list(docs),
            "tensorFields": [*TEXT_FIELDS, IMAGE_FIELD],
            "mediaDownloadThreadCount": self.image_download_thread_count,
        }

    def qwen_index_settings(self) -> dict[str, Any]:
        return {
            "type": "structured",
            "model": "no_model",
            "modelProperties": {
                "dimensions": self.qwen_embedding_dimensions,
                "type": "no_model",
            },
            "normalizeEmbeddings": False,
            "allFields": [
                {"name": QWEN_TEXT_VECTOR_FIELD, "type": "custom_vector", "features": ["lexical_search", "filter"]},
                {"name": QWEN_IMAGE_VECTOR_FIELD, "type": "custom_vector", "features": ["lexical_search", "filter"]},
                *[
                    {"name": field, "type": "text", "features": ["lexical_search", "filter"]}
                    for field in TEXT_FIELDS
                ],
                {"name": IMAGE_FIELD, "type": "text", "features": ["lexical_search", "filter"]},
                {"name": SEARCH_TEXT_FIELD, "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "product_id", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "document_id", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "product_url", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "status", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "updated_at", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "display_yn", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "mall_id", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "product_group_id", "type": "text", "features": ["lexical_search", "filter"]},
                {"name": "price", "type": "float"},
                {"name": "price_min", "type": "float"},
                {"name": "price_max", "type": "float"},
                {"name": "min_order_qty", "type": "int"},
                {"name": "delivery_days", "type": "int"},
                {"name": "is_deleted", "type": "bool"},
            ],
            "tensorFields": [QWEN_TEXT_VECTOR_FIELD, QWEN_IMAGE_VECTOR_FIELD],
            "annParameters": {
                "spaceType": "prenormalized-angular",
                "parameters": {"efConstruction": 512, "m": 16},
            },
        }

    def build_qwen_search_payload(self, query: EngineQuery) -> dict[str, Any]:
        tensor_entries, vector_fields, vector_sources = self.qwen_query_context(query)
        rerank_text = should_rerank_text_query(query) and QWEN_TEXT_VECTOR_FIELD in vector_fields
        payload: dict[str, Any] = {
            "searchMethod": "TENSOR",
            "context": {"tensor": tensor_entries},
            "searchableAttributes": vector_fields,
            "limit": qwen_marqo_candidate_limit(query, rerank_text=rerank_text),
            "attributesToRetrieve": attributes_to_retrieve_for_query(query, rerank_text=rerank_text),
        }
        filters = build_filter_terms(query, include_numeric=False)
        if filters:
            payload["filter"] = filters
        payload["_haeorumVectorField"] = "+".join(vector_fields)
        payload["_haeorumVectorSource"] = "+".join(vector_sources)
        payload["_haeorumRerankText"] = rerank_text
        auxiliary_payload = self.build_qwen_text_auxiliary_search_payload(query, vector_fields)
        if auxiliary_payload is not None:
            payload["_haeorumAuxTextPayload"] = auxiliary_payload
            payload["_haeorumAuxTextWeight"] = self.text_auxiliary_weight
        return payload

    def build_qwen_text_auxiliary_search_payload(
        self,
        query: EngineQuery,
        primary_vector_fields: Iterable[str],
    ) -> dict[str, Any] | None:
        if self.text_auxiliary_weight <= 0 or not query.q:
            return None
        if QWEN_TEXT_VECTOR_FIELD in set(primary_vector_fields):
            return None
        vector, _field, source = self.qwen_text_query_vector(query)
        payload: dict[str, Any] = {
            "searchMethod": "TENSOR",
            "context": {"tensor": [{"vector": vector, "weight": 1}]},
            "searchableAttributes": [QWEN_TEXT_VECTOR_FIELD],
            "limit": qwen_text_auxiliary_candidate_limit(
                query,
                multiplier=self.text_auxiliary_candidate_multiplier,
            ),
            "attributesToRetrieve": attributes_to_retrieve_for_query(query, rerank_text=False),
            "_haeorumVectorField": QWEN_TEXT_VECTOR_FIELD,
            "_haeorumVectorSource": qwen_auxiliary_text_source(source),
        }
        filters = build_filter_terms(query, include_numeric=False)
        if filters:
            payload["filter"] = filters
        return payload

    def build_qwen_upsert_payload(self, products: Iterable[ProductDocument]) -> dict[str, Any]:
        docs = [product_to_marqo_doc(product) for product in products]
        if not docs:
            return {"documents": []}
        text_vectors = self.qwen_embed_product_texts([product_embedding_text(doc) for doc in docs])
        image_docs = [doc for doc in docs if doc.get(IMAGE_FIELD)]
        image_vectors = self.qwen_embed_images([str(doc[IMAGE_FIELD]) for doc in image_docs]) if image_docs else []
        image_vector_by_id = {
            str(doc["_id"]): vector
            for doc, vector in zip(image_docs, image_vectors)
        }
        vector_docs = []
        for doc, text_vector in zip(docs, text_vectors):
            enriched = dict(doc)
            enriched[QWEN_TEXT_VECTOR_FIELD] = {
                "content": product_embedding_text(doc),
                "vector": text_vector,
            }
            image_vector = image_vector_by_id.get(str(doc["_id"]))
            if image_vector is not None and enriched.get(IMAGE_FIELD):
                enriched[QWEN_IMAGE_VECTOR_FIELD] = {
                    "content": enriched[IMAGE_FIELD],
                    "vector": image_vector,
                }
            vector_docs.append(enriched)
        return {"documents": vector_docs}

    def qwen_query_vector(self, query: EngineQuery) -> tuple[list[float], str, str]:
        if query.image_data_url:
            return self.qwen_image_query_vector(query)
        return self.qwen_text_to_image_query_vector(query)

    def qwen_query_context(self, query: EngineQuery) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        if query.q and query.image_data_url:
            entries: list[dict[str, Any]] = []
            fields: list[str] = []
            sources: list[str] = []
            text_weight = max(float(query.text_weight or 0.0), 0.0)
            image_weight = max(float(query.image_weight or 0.0), 0.0)
            if text_weight > 0 and image_weight > 0:
                text_result, image_result = self.qwen_mixed_query_vectors(query)
                text_vector, text_field, text_source = text_result
                image_vector, image_field, image_source = image_result
                entries.append({"vector": text_vector, "weight": text_weight})
                fields.append(text_field)
                sources.append(text_source)
                entries.append({"vector": image_vector, "weight": image_weight})
                fields.append(image_field)
                sources.append(image_source)
            elif text_weight > 0:
                vector, field, source = self.qwen_text_to_image_query_vector(query)
                entries.append({"vector": vector, "weight": text_weight})
                fields.append(field)
                sources.append(source)
            elif image_weight > 0:
                vector, field, source = self.qwen_image_query_vector(query)
                entries.append({"vector": vector, "weight": image_weight})
                fields.append(field)
                sources.append(source)
            if entries:
                return entries, list(dict.fromkeys(fields)), sources
        vector, field, source = self.qwen_query_vector(query)
        return [{"vector": vector, "weight": 1}], [field], [source]

    def qwen_mixed_query_vectors(
        self,
        query: EngineQuery,
    ) -> tuple[tuple[list[float], str, str], tuple[list[float], str, str]]:
        executor = self._qwen_mixed_query_executor
        if executor is None:
            return self.qwen_text_to_image_query_vector(query), self.qwen_image_query_vector(query)
        text_future = executor.submit(self.qwen_text_to_image_query_vector, query)
        image_future = executor.submit(self.qwen_image_query_vector, query)
        try:
            return text_future.result(), image_future.result()
        except BaseException:
            text_future.cancel()
            image_future.cancel()
            raise

    def qwen_image_query_vector(self, query: EngineQuery) -> tuple[list[float], str, str]:
        cache_key = qwen_image_query_embedding_cache_key(query.image_hash, query.image_data_url)
        cached_vector = self._get_runtime_qwen_query_vector(cache_key)
        if cached_vector is not None:
            return (
                cached_vector,
                QWEN_IMAGE_VECTOR_FIELD,
                "image_runtime_cache",
            )
        vector = self._compute_qwen_query_vector_once(
            cache_key,
            lambda: self.qwen_embed_images(
                [query.image_data_url or ""],
                timeout=self.qwen_query_timeout_seconds,
                retry_count=self.search_retry_count,
                retry_delay_seconds=self.search_retry_delay_seconds,
            )[0],
        )
        return (
            vector,
            QWEN_IMAGE_VECTOR_FIELD,
            "image",
        )

    def qwen_text_to_image_query_vector(self, query: EngineQuery) -> tuple[list[float], str, str]:
        vector, _field, source = self.qwen_text_query_vector(query)
        return (
            vector,
            QWEN_IMAGE_VECTOR_FIELD,
            qwen_text_to_image_source(source),
        )

    def qwen_text_query_vector(self, query: EngineQuery) -> tuple[list[float], str, str]:
        expanded_text_query = expanded_query_text(query.q, query.query_synonyms) if query.q else ""
        text_query = append_inferred_categories(expanded_text_query, query.inferred_categories)
        cache_key = normalize_query_embedding_cache_key(text_query or "")
        cached_vector = self.qwen_query_embedding_cache.get(cache_key)
        if cached_vector is not None:
            return (
                cached_vector,
                QWEN_TEXT_VECTOR_FIELD,
                "text_cache",
            )
        runtime_cache_key = qwen_text_query_embedding_cache_key(cache_key)
        cached_vector = self._get_runtime_qwen_query_vector(cache_key)
        if cached_vector is not None:
            return (
                cached_vector,
                QWEN_TEXT_VECTOR_FIELD,
                "text_runtime_cache",
            )
        cached_vector = self._get_runtime_qwen_query_vector(runtime_cache_key)
        if cached_vector is not None:
            return (
                cached_vector,
                QWEN_TEXT_VECTOR_FIELD,
                "text_runtime_cache",
            )
        vector = self._compute_qwen_query_vector_once(
            runtime_cache_key,
            lambda: self.qwen_embed_query_texts(
                [text_query or ""],
                timeout=self.qwen_query_timeout_seconds,
                retry_count=self.search_retry_count,
                retry_delay_seconds=self.search_retry_delay_seconds,
            )[0],
        )
        return (
            vector,
            QWEN_TEXT_VECTOR_FIELD,
            "text",
        )

    def _get_runtime_qwen_query_vector(self, key: str) -> list[float] | None:
        if not key:
            return None
        with self._qwen_runtime_query_embedding_cache_lock:
            vector = self._qwen_runtime_query_embedding_cache.get(key)
            if vector is None:
                return None
            self._qwen_runtime_query_embedding_cache.move_to_end(key)
            return list(vector)

    def _set_runtime_qwen_query_vector(self, key: str, vector: list[float]) -> None:
        if not key:
            return
        with self._qwen_runtime_query_embedding_cache_lock:
            self._qwen_runtime_query_embedding_cache[key] = list(vector)
            self._qwen_runtime_query_embedding_cache.move_to_end(key)
            self._trim_runtime_qwen_query_vector_cache(key)

    def _trim_runtime_qwen_query_vector_cache(self, key: str) -> None:
        cache_kind = runtime_qwen_query_cache_kind(key)
        max_entries = (
            self._qwen_runtime_image_query_embedding_cache_max_entries
            if cache_kind == "image"
            else self._qwen_runtime_text_query_embedding_cache_max_entries
        )
        matching_keys = [
            cache_key
            for cache_key in self._qwen_runtime_query_embedding_cache
            if runtime_qwen_query_cache_kind(cache_key) == cache_kind
        ]
        while len(matching_keys) > max_entries:
            oldest_key = matching_keys.pop(0)
            self._qwen_runtime_query_embedding_cache.pop(oldest_key, None)

    def _compute_qwen_query_vector_once(self, key: str, compute: Any) -> list[float]:
        if not key:
            vector = compute()
            return list(vector)
        cached_vector = self._get_runtime_qwen_query_vector(key)
        if cached_vector is not None:
            return cached_vector
        with self._qwen_query_vector_inflight_lock:
            inflight = self._qwen_query_vector_inflight.get(key)
            if inflight is None:
                inflight = InflightQwenQueryVector(threading.Event())
                self._qwen_query_vector_inflight[key] = inflight
                owner = True
            else:
                owner = False
        if not owner:
            completed = self._wait_for_qwen_query_vector(inflight)
            if completed:
                if inflight.exception is not None:
                    raise inflight.exception
                cached_vector = self._get_runtime_qwen_query_vector(key)
                if cached_vector is not None:
                    return cached_vector
                if inflight.vector is not None:
                    return list(inflight.vector)
            vector = compute()
            self._set_runtime_qwen_query_vector(key, vector)
            return list(vector)
        try:
            vector = list(compute())
            self._set_runtime_qwen_query_vector(key, vector)
            inflight.vector = list(vector)
            return vector
        except BaseException as exc:
            inflight.exception = exc
            raise
        finally:
            with self._qwen_query_vector_inflight_lock:
                self._qwen_query_vector_inflight.pop(key, None)
                inflight.event.set()

    def _wait_for_qwen_query_vector(self, inflight: InflightQwenQueryVector) -> bool:
        wait_started = time.perf_counter()
        completed = inflight.event.wait(timeout=self._qwen_query_vector_wait_timeout_seconds())
        elapsed_seconds = time.perf_counter() - wait_started
        with self._qwen_query_vector_stats_lock:
            self._qwen_query_vector_wait_events += 1
            self._qwen_query_vector_total_wait_seconds += elapsed_seconds
            self._qwen_query_vector_max_wait_seconds = max(self._qwen_query_vector_max_wait_seconds, elapsed_seconds)
            if not completed:
                self._qwen_query_vector_wait_timeouts += 1
        return completed

    def _qwen_query_vector_wait_timeout_seconds(self) -> float:
        attempts = max(0, int(self.search_retry_count)) + 1
        retry_delay_total = sum(self.search_retry_delay_seconds * (2 ** attempt) for attempt in range(max(attempts - 1, 0)))
        return max((self.qwen_query_timeout_seconds * attempts) + retry_delay_total + 1.0, 0.001)

    def qwen_query_vector_status(self) -> dict[str, Any]:
        with self._qwen_runtime_query_embedding_cache_lock:
            runtime_entries = len(self._qwen_runtime_query_embedding_cache)
            runtime_text_entries = sum(
                1
                for key in self._qwen_runtime_query_embedding_cache
                if runtime_qwen_query_cache_kind(key) == "text"
            )
            runtime_image_entries = sum(
                1
                for key in self._qwen_runtime_query_embedding_cache
                if runtime_qwen_query_cache_kind(key) == "image"
            )
        with self._qwen_query_vector_inflight_lock:
            in_flight = len(self._qwen_query_vector_inflight)
        with self._qwen_query_vector_stats_lock:
            wait_events = self._qwen_query_vector_wait_events
            wait_timeouts = self._qwen_query_vector_wait_timeouts
            total_wait_ms = round(self._qwen_query_vector_total_wait_seconds * 1000, 3)
            max_wait_ms = round(self._qwen_query_vector_max_wait_seconds * 1000, 3)
        return {
            "precomputed_entries": len(self.qwen_query_embedding_cache),
            "runtime_entries": runtime_entries,
            "runtime_text_entries": runtime_text_entries,
            "runtime_image_entries": runtime_image_entries,
            "runtime_max_entries": self._qwen_runtime_query_embedding_cache_max_entries,
            "runtime_text_max_entries": self._qwen_runtime_text_query_embedding_cache_max_entries,
            "runtime_image_max_entries": self._qwen_runtime_image_query_embedding_cache_max_entries,
            "in_flight": in_flight,
            "wait_timeout_seconds": self._qwen_query_vector_wait_timeout_seconds(),
            "wait_events": wait_events,
            "wait_timeouts": wait_timeouts,
            "total_wait_ms": total_wait_ms,
            "avg_wait_ms": round(total_wait_ms / wait_events, 3) if wait_events else 0.0,
            "max_wait_ms": max_wait_ms,
            "mixed_parallelism": self.qwen_mixed_query_parallelism,
        }

    def qwen_embed_product_texts(self, texts: list[str]) -> list[list[float]]:
        _, data, _ = self._qwen_request_with_retries(
            "POST",
            "/embed",
            {"inputs": [{"text": text} for text in texts]},
            timeout=1800,
            retry_count=self.search_retry_count,
            retry_delay_seconds=self.search_retry_delay_seconds,
        )
        return validate_qwen_embedding_response(
            data,
            expected_count=len(texts),
            expected_dimensions=self.qwen_embedding_dimensions,
            context="Qwen product text embedding response",
        )

    def qwen_embed_query_texts(
        self,
        texts: list[str],
        *,
        timeout: int | float = 1800,
        retry_count: int = 0,
        retry_delay_seconds: float = 0.0,
    ) -> list[list[float]]:
        _, data, _ = self._qwen_request_with_retries(
            "POST",
            "/embed",
            {"inputs": [{"text": text} for text in texts], "prompt": self.query_embedding_prompt()},
            timeout=timeout,
            retry_count=retry_count,
            retry_delay_seconds=retry_delay_seconds,
        )
        return validate_qwen_embedding_response(
            data,
            expected_count=len(texts),
            expected_dimensions=self.qwen_embedding_dimensions,
            context="Qwen query text embedding response",
        )

    def query_embedding_prompt(self) -> str:
        if self.embedding_provider == "gemini":
            return GEMINI_QUERY_PROMPT
        return QWEN_QUERY_PROMPT

    def qwen_embed_images(
        self,
        images: list[str],
        *,
        timeout: int | float = 1800,
        retry_count: int | None = None,
        retry_delay_seconds: float | None = None,
    ) -> list[list[float]]:
        _, data, _ = self._qwen_request_with_retries(
            "POST",
            "/embed",
            {"inputs": [{"image": image} for image in images]},
            timeout=timeout,
            retry_count=self.search_retry_count if retry_count is None else retry_count,
            retry_delay_seconds=self.search_retry_delay_seconds if retry_delay_seconds is None else retry_delay_seconds,
        )
        return validate_qwen_embedding_response(
            data,
            expected_count=len(images),
            expected_dimensions=self.qwen_embedding_dimensions,
            context="Qwen image embedding response",
        )

    def _request_with_retries(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        timeout: int | float = 60,
        retry_count: int = 0,
        retry_delay_seconds: float = 0.0,
    ) -> tuple[int, Any, float]:
        return self._call_with_retries(
            lambda: self._request(method, path, payload, timeout=timeout),
            retry_count=retry_count,
            retry_delay_seconds=retry_delay_seconds,
        )

    def _qwen_request_with_retries(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        timeout: int | float = 60,
        retry_count: int = 0,
        retry_delay_seconds: float = 0.0,
    ) -> tuple[int, Any, float]:
        return self._call_with_retries(
            lambda: self._qwen_request(method, path, payload, timeout=timeout),
            retry_count=retry_count,
            retry_delay_seconds=retry_delay_seconds,
        )

    def _call_with_retries(
        self,
        call: Any,
        *,
        retry_count: int,
        retry_delay_seconds: float,
    ) -> tuple[int, Any, float]:
        attempts = max(0, int(retry_count)) + 1
        delay = max(0.0, float(retry_delay_seconds))
        for attempt in range(attempts):
            try:
                return call()
            except Exception as exc:
                if attempt >= attempts - 1 or not transient_backend_error(exc):
                    raise
                sleep_seconds = self._retry_sleep_seconds(exc, attempt, delay)
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
        raise RuntimeError("unreachable retry state")

    def _retry_sleep_seconds(self, exc: BaseException, attempt: int, base_delay_seconds: float) -> float:
        sleep_seconds = max(0.0, float(base_delay_seconds)) * (2 ** max(0, int(attempt)))
        retry_after = getattr(exc, "retry_after_seconds", None)
        if retry_after is None or self.backend_retry_after_max_seconds <= 0:
            return sleep_seconds
        try:
            retry_after_seconds = float(retry_after)
        except (TypeError, ValueError):
            return sleep_seconds
        if not math.isfinite(retry_after_seconds):
            return sleep_seconds
        bounded_retry_after = min(max(0.0, retry_after_seconds), self.backend_retry_after_max_seconds)
        return max(sleep_seconds, bounded_retry_after)

    def _request(self, method: str, path: str, payload: Any = None, timeout: int | float = 60) -> tuple[int, Any, float]:
        return self._marqo_http.request(method, path, payload, timeout=timeout)

    def _qwen_request(self, method: str, path: str, payload: Any = None, timeout: int | float = 60) -> tuple[int, Any, float]:
        return self._qwen_http.request(method, path, payload, timeout=timeout)


class PlaceholderSearchEngine(SearchEngine):
    """Explicit future adapter slot for non-Marqo engines."""

    def __init__(self, plan: ReservedEnginePlan):
        self.plan = plan
        self.name = plan.name

    def search(self, query: EngineQuery) -> list[EngineHit]:
        self._raise_unavailable("search")

    def upsert_products(self, products: Iterable[ProductDocument]) -> dict[str, Any]:
        self._raise_unavailable("upsert_products")

    def delete_products(self, product_ids: Iterable[str]) -> dict[str, Any]:
        self._raise_unavailable("delete_products")

    def health(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "ready": False,
            "ok": False,
            "implemented": False,
            "reserved_adapter": True,
            "replacement_path": self.plan.replacement_path,
            "required_components": list(self.plan.required_components),
            "message": f"{self.name} adapter is reserved but not implemented for runtime search",
        }

    def _raise_unavailable(self, action: str) -> NoReturn:
        raise ReservedSearchEngineUnavailable(
            f"{self.name} search engine adapter is reserved but not implemented for {action}; "
            "use HAEORUM_SEARCH_ENGINE=marqo for runtime search or complete the adapter first"
        )


class TypesenseSearchEngine(PlaceholderSearchEngine):
    def __init__(self):
        super().__init__(RESERVED_ENGINE_PLANS["typesense"])


class QdrantSearchEngine(PlaceholderSearchEngine):
    def __init__(self):
        super().__init__(RESERVED_ENGINE_PLANS["qdrant"])


def build_filter_terms(query: EngineQuery, *, include_numeric: bool = True) -> str | None:
    terms = ["status:(active)"]
    if query.strict_mall_filter and query.mall_id:
        terms.append(f"mall_id:({escape_filter_value(query.mall_id)})")
    if query.category:
        terms.append(f"category_name:({escape_filter_value(query.category)})")
    if not include_numeric:
        return " AND ".join(terms)
    if query.min_price is not None:
        terms.append(any_numeric_range_filter(("price", "price_min", "price_max"), min_value=query.min_price))
    if query.max_price is not None:
        terms.append(any_numeric_range_filter(("price", "price_min", "price_max"), max_value=query.max_price))
    if query.quantity is not None:
        terms.append(numeric_range_filter("min_order_qty", max_value=query.quantity))
    if query.max_delivery_days is not None:
        terms.append(numeric_range_filter("delivery_days", max_value=query.max_delivery_days))
    return " AND ".join(terms)


def any_numeric_range_filter(
    fields: Iterable[str],
    *,
    min_value: int | float | None = None,
    max_value: int | float | None = None,
) -> str:
    filters = [numeric_range_filter(field, min_value=min_value, max_value=max_value) for field in fields]
    return f"({' OR '.join(filters)})"


def numeric_range_filter(
    field: str,
    *,
    min_value: int | float | None = None,
    max_value: int | float | None = None,
) -> str:
    lower = "*" if min_value is None else marqo_numeric_value(min_value)
    upper = "*" if max_value is None else marqo_numeric_value(max_value)
    return f"{field}:[{lower} TO {upper}]"


def marqo_numeric_value(value: int | float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Marqo numeric filter values must be finite")
    if number.is_integer():
        return str(int(number))
    return format(number, ".15g")


def search_payload_limit(query: EngineQuery) -> int:
    multiplier = 4 if query_has_post_filters(query) else 2
    return max(query.limit * multiplier, query.limit)


def marqo_candidate_limit(query: EngineQuery, *, rerank_text: bool | None = None) -> int:
    base_limit = search_payload_limit(query)
    should_rerank = should_rerank_text_query(query) if rerank_text is None else rerank_text
    if should_rerank:
        base_limit = max(base_limit, MARQO_TEXT_RERANK_MIN_CANDIDATES)
    return min(base_limit, max(int(query.max_candidates or 1), 1))


def qwen_marqo_candidate_limit(query: EngineQuery, *, rerank_text: bool | None = None) -> int:
    base_limit = marqo_candidate_limit(query, rerank_text=rerank_text)
    if query_has_numeric_filters(query):
        base_limit = max(base_limit, query.limit * 4)
    return min(base_limit, max(int(query.max_candidates or 1), 1))


def qwen_text_auxiliary_candidate_limit(query: EngineQuery, *, multiplier: float = 1.0) -> int:
    base_limit = qwen_marqo_candidate_limit(query, rerank_text=False)
    scaled = int(math.ceil(base_limit * max(0.1, float(multiplier))))
    return min(max(scaled, query.limit), max(int(query.max_candidates or 1), 1))


def attributes_to_retrieve_for_query(query: EngineQuery, *, rerank_text: bool | None = None) -> list[str]:
    fields = list(ATTRIBUTES_TO_RETRIEVE)
    should_rerank = should_rerank_text_query(query) if rerank_text is None else rerank_text
    if should_rerank:
        fields.extend(TEXT_RERANK_ATTRIBUTES_TO_RETRIEVE)
    attribute_filter_values = (query.print_method, query.material, query.color)
    fields.extend(
        field
        for field, value in zip(ATTRIBUTE_FILTER_FIELDS_TO_RETRIEVE, attribute_filter_values)
        if value
    )
    numeric_filter_groups = [
        (query.quantity is not None, NUMERIC_FILTER_FIELDS_TO_RETRIEVE[0:1]),
        (
            query.min_price is not None or query.max_price is not None,
            NUMERIC_FILTER_FIELDS_TO_RETRIEVE[1:3],
        ),
        (query.max_delivery_days is not None, NUMERIC_FILTER_FIELDS_TO_RETRIEVE[3:4]),
    ]
    for enabled, field_names in numeric_filter_groups:
        if enabled:
            fields.extend(field_names)
    return list(dict.fromkeys(fields))


def should_rerank_text_query(query: EngineQuery) -> bool:
    return bool(query.q and not query.image_data_url)


def qwen_text_to_image_source(source: str) -> str:
    if source == "text":
        return "text_to_image"
    if source == "text_cache":
        return "text_to_image_cache"
    if source == "text_runtime_cache":
        return "text_to_image_runtime_cache"
    return f"text_to_image:{source}"


def qwen_auxiliary_text_source(source: str) -> str:
    if source == "text":
        return "text_auxiliary"
    if source == "text_cache":
        return "text_auxiliary_cache"
    if source == "text_runtime_cache":
        return "text_auxiliary_runtime_cache"
    return f"text_auxiliary:{source}"


def auxiliary_scores_by_document_key(data: Mapping[str, Any]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for raw_hit in data.get("hits", []) if isinstance(data, Mapping) else []:
        if not isinstance(raw_hit, dict):
            continue
        try:
            product = marqo_hit_to_product(raw_hit)
            score = float(raw_hit.get("_score") or 0.0)
        except Exception:
            continue
        if not math.isfinite(score):
            continue
        document_key = product_document_id(product.mall_id, product.product_id)
        previous = scores.get(document_key)
        if previous is None or score > previous:
            scores[document_key] = score
    return scores


def rerank_text_hits(query: EngineQuery, hits: list[EngineHit]) -> list[EngineHit]:
    text_query = build_text_relevance_query(query.q or "", query.query_synonyms)
    normalized_categories = normalized_inferred_categories(query.inferred_categories)
    reranked = []
    for hit in hits:
        lexical_score = text_relevance_score_for_query(text_query, hit.document, query.query_synonyms)
        category_score = category_intent_score_for_categories(normalized_categories, hit.document)
        relevance_score = max(lexical_score, category_score)
        if relevance_score <= 0:
            combined_score = min(0.39, hit.score * 0.45)
        else:
            combined_score = min(1.0, (hit.score * 0.75) + (relevance_score * 0.25))
        source_scores = dict(hit.source_scores)
        source_scores["lexical"] = round(lexical_score, 6)
        source_scores["category_intent"] = round(category_score, 6)
        reranked.append(
            EngineHit(
                document=hit.document,
                score=round(combined_score, 6),
                source_scores=source_scores,
            )
        )
    reranked.sort(
        key=lambda hit: (
            -max(hit.source_scores.get("lexical", 0.0), hit.source_scores.get("category_intent", 0.0)),
            -hit.score,
            hit.document.product_id,
        )
    )
    return reranked


def rerank_text_to_image_hits(query: EngineQuery, hits: list[EngineHit]) -> list[EngineHit]:
    text_query = build_text_to_image_evidence_query(query)
    normalized_categories = normalized_inferred_categories(query.inferred_categories)
    scored: list[tuple[int, EngineHit, float, float, float]] = []
    for rank, hit in enumerate(hits):
        lexical_score = text_relevance_score_for_query(text_query, hit.document, query.query_synonyms)
        category_score = category_intent_score_for_categories(normalized_categories, hit.document)
        evidence_score = max(lexical_score, category_score)
        scored.append((rank, hit, lexical_score, category_score, evidence_score))
    has_evidence = any(evidence_score > 0 for *_prefix, evidence_score in scored)
    if not has_evidence:
        return hits

    reranked: list[tuple[int, EngineHit]] = []
    for rank, hit, lexical_score, category_score, evidence_score in scored:
        if evidence_score > 0:
            combined_score = min(1.0, hit.score + min(0.045, evidence_score * 0.04))
        else:
            combined_score = max(0.0, hit.score - 0.04)
        source_scores = dict(hit.source_scores)
        source_scores["lexical"] = round(lexical_score, 6)
        source_scores["category_intent"] = round(category_score, 6)
        source_scores["text_evidence"] = round(evidence_score, 6)
        reranked.append(
            (
                rank,
                EngineHit(
                    document=hit.document,
                    score=round(combined_score, 6),
                    source_scores=source_scores,
                ),
            )
        )
    reranked.sort(key=lambda item: (-item[1].score, -item[1].source_scores.get("text_evidence", 0.0), item[0]))
    return [hit for _rank, hit in reranked]


def build_text_to_image_evidence_query(query: EngineQuery) -> TextRelevanceQuery:
    expanded = expanded_query_text(query.q or "", query.query_synonyms)
    with_categories = append_inferred_categories(expanded, query.inferred_categories)
    return build_text_relevance_query(with_categories or query.q or "", query.query_synonyms)


def build_text_relevance_query(
    query: str,
    query_synonyms: Mapping[str, Iterable[str]] | None = None,
) -> TextRelevanceQuery:
    return TextRelevanceQuery(
        normalized=normalize_text(query),
        terms=tuple(expand_terms(tokenize(query), query_synonyms)),
    )


def text_relevance_score(
    query: str,
    product: ProductDocument,
    query_synonyms: Mapping[str, Iterable[str]] | None = None,
) -> float:
    text_query = build_text_relevance_query(query, query_synonyms)
    return text_relevance_score_for_query(text_query, product, query_synonyms)


def text_relevance_score_for_query(
    query: TextRelevanceQuery,
    product: ProductDocument,
    query_synonyms: Mapping[str, Iterable[str]] | None = None,
) -> float:
    query_terms = query.terms
    doc_text = normalize_text(product_rerank_text(product))
    doc_terms = set(expand_terms(tokenize(doc_text), query_synonyms))
    if not query_terms:
        return 0.0
    matched = 0
    for term in query_terms:
        if term_matches_document(term, doc_text, doc_terms):
            matched += 1
    phrase_bonus = 0.35 if query.normalized in doc_text else 0.0
    category_terms = set(expand_terms(tokenize(product.category), query_synonyms))
    category_text = normalize_text(product.category)
    category_bonus = 0.25 if any(term_matches_document(term, category_text, category_terms) for term in query_terms) else 0.0
    return min(1.0, (matched / len(query_terms)) + phrase_bonus + category_bonus)


def category_intent_score(query: EngineQuery, product: ProductDocument) -> float:
    return category_intent_score_for_categories(normalized_inferred_categories(query.inferred_categories), product)


def category_intent_score_for_categories(normalized_categories: tuple[str, ...], product: ProductDocument) -> float:
    product_category = normalize_text(product.category)
    if not product_category:
        return 0.0
    for rank, category_text in enumerate(normalized_categories):
        if product_category == category_text or category_text in product_category or product_category in category_text:
            return max(0.65, 0.88 - (rank * 0.06))
    return 0.0


def normalized_inferred_categories(categories: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        normalized
        for normalized in (normalize_text(str(category or "")) for category in categories)
        if normalized
    )


def product_rerank_text(product: ProductDocument) -> str:
    compact_text = product.extra.get(SEARCH_TEXT_FIELD) if isinstance(product.extra, dict) else None
    text = str(compact_text or "").strip()
    if text:
        return text
    return product.text_blob()


def query_has_post_filters(query: EngineQuery) -> bool:
    # Numeric filters are pushed into Marqo range filters. Only fuzzy text
    # attribute filters need extra candidates for application post-filtering.
    return any(
        bool(value)
        for value in [
            query.print_method,
            query.material,
            query.color,
        ]
    )


def query_has_numeric_filters(query: EngineQuery) -> bool:
    return any(
        value is not None
        for value in [
            query.min_price,
            query.max_price,
            query.quantity,
            query.max_delivery_days,
        ]
    )


def chunked(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def chunked_strings(values: list[str], size: int) -> Iterable[list[str]]:
    batch_size = max(int(size), 1)
    for index in range(0, len(values), batch_size):
        yield values[index : index + batch_size]


def chain_first(first: list[ProductDocument], rest: Iterable[list[ProductDocument]]) -> Iterable[list[ProductDocument]]:
    yield first
    yield from rest


def chunked_products(values: Iterable[ProductDocument], size: int) -> Iterable[list[ProductDocument]]:
    batch_size = max(int(size), 1)
    batch: list[ProductDocument] = []
    for value in values:
        batch.append(value)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def json_body_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=JSON_DUMP_SEPARATORS).encode("utf-8")


def json_body_size(payload: Any) -> int:
    return len(json_body_bytes(payload))


def batch_response_payload(
    batch_count: int,
    retained_responses: list[dict[str, Any]],
    last_response: dict[str, Any] | None,
) -> dict[str, Any]:
    if batch_count <= 1:
        return last_response or {}
    response: dict[str, Any] = {
        "batches": batch_count,
        "responses": retained_responses,
        "response_retained_count": len(retained_responses),
        "responses_truncated": batch_count > len(retained_responses),
        "response_sample_limit": MARQO_BATCH_RESPONSE_SAMPLE_LIMIT,
    }
    if response["responses_truncated"]:
        response["last_response"] = last_response or {}
    return response


def append_failure_samples(
    retained_failures: list[dict[str, Any]],
    failures: Iterable[dict[str, Any]],
    sample_limit: int = MARQO_BATCH_FAILURE_SAMPLE_LIMIT,
) -> None:
    remaining = max(int(sample_limit), 0) - len(retained_failures)
    if remaining <= 0:
        return
    for failure in failures:
        if len(retained_failures) >= sample_limit:
            break
        retained_failures.append(failure)


def chunk_marqo_documents_by_limits(
    docs: Iterable[dict[str, Any]],
    max_count: int,
    max_request_bytes: int,
    base_payload: Mapping[str, Any] | None = None,
) -> Iterable[list[dict[str, Any]]]:
    batch_size = max(1, int(max_count))
    byte_limit = max(0, int(max_request_bytes))
    base = dict(base_payload or {})
    empty_payload_bytes = json_body_size(dict(base, documents=[]))
    batch: list[dict[str, Any]] = []
    current_bytes = empty_payload_bytes
    for doc in docs:
        doc_bytes = json_body_size(doc)
        next_bytes = current_bytes + doc_bytes + (1 if batch else 0)
        if batch and (len(batch) >= batch_size or (byte_limit and next_bytes > byte_limit)):
            yield batch
            batch = []
            current_bytes = empty_payload_bytes
            next_bytes = current_bytes + doc_bytes
        batch.append(doc)
        current_bytes = next_bytes
    if batch:
        yield batch


def prepare_product_filters(query: EngineQuery) -> PreparedProductFilters:
    return PreparedProductFilters(
        strict_mall_id=query.mall_id if query.strict_mall_filter and query.mall_id else None,
        normalized_category=normalize_text(str(query.category or "")),
        print_method=prepare_text_filter(query.print_method),
        material=prepare_text_filter(query.material),
        color=prepare_text_filter(query.color),
        min_price=query.min_price,
        max_price=query.max_price,
        quantity=query.quantity,
        max_delivery_days=query.max_delivery_days,
    )


def prepare_text_filter(value: str | None) -> PreparedTextFilter | None:
    normalized = normalize_text(str(value or ""))
    if not normalized:
        return None
    return PreparedTextFilter(
        normalized=normalized,
        terms=tuple(expand_terms(tokenize(normalized))),
    )


def product_matches_query_filters(product: ProductDocument, query: EngineQuery) -> bool:
    return product_matches_prepared_filters(product, prepare_product_filters(query))


def product_matches_prepared_filters(product: ProductDocument, filters: PreparedProductFilters) -> bool:
    if not product.active:
        return False
    if filters.strict_mall_id and product.mall_id != filters.strict_mall_id:
        return False
    if filters.normalized_category and normalize_text(product.category) != filters.normalized_category:
        return False
    if filters.print_method and not text_filter_matches_prepared(filters.print_method, product.print_methods):
        return False
    if filters.material and not text_filter_matches_prepared(filters.material, product.materials):
        return False
    if filters.color and not text_filter_matches_prepared(filters.color, product.colors):
        return False
    if not price_filter_matches(product, filters.min_price, filters.max_price):
        return False
    if filters.quantity is not None and (product.min_order_qty is None or product.min_order_qty > filters.quantity):
        return False
    if filters.max_delivery_days is not None and (
        product.delivery_days is None or product.delivery_days > filters.max_delivery_days
    ):
        return False
    return True


def text_filter_matches(needle: str, values: Iterable[str]) -> bool:
    prepared = prepare_text_filter(needle)
    if prepared is None:
        return True
    return text_filter_matches_prepared(prepared, values)


def text_filter_matches_prepared(needle: PreparedTextFilter, values: Iterable[str]) -> bool:
    for value in values:
        normalized_value = normalize_text(str(value))
        if not normalized_value:
            continue
        value_terms = set(expand_terms(tokenize(normalized_value)))
        if needle.normalized in normalized_value or normalized_value in needle.normalized:
            return True
        if any(term_matches_document(term, normalized_value, value_terms) for term in needle.terms):
            return True
    return False


def price_filter_matches(product: ProductDocument, min_price: float | None, max_price: float | None) -> bool:
    if min_price is None and max_price is None:
        return True
    lower, upper = product_price_bounds(product)
    if lower is None or upper is None:
        return False
    if min_price is not None and upper < min_price:
        return False
    if max_price is not None and lower > max_price:
        return False
    return True


def product_price_bounds(product: ProductDocument) -> tuple[float | None, float | None]:
    lower = product.price_min if product.price_min is not None else product.price
    upper = product.price_max if product.price_max is not None else product.price
    if lower is None and upper is not None:
        lower = upper
    if upper is None and lower is not None:
        upper = lower
    return lower, upper


def escape_filter_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(")", "\\)")


def product_to_marqo_doc(product: ProductDocument) -> dict[str, Any]:
    document_id = product_document_id(product.mall_id, product.product_id)
    search_text = product.text_blob()
    doc = {
        "_id": document_id,
        "document_id": document_id,
        "product_id": product.product_id,
        "product_name": product.name,
        "category_name": product.category,
        SEARCH_TEXT_FIELD: search_text,
        "description": product.description or "",
        "keywords": " ".join(product.keywords),
        "print_methods": " ".join(product.print_methods),
        "materials": " ".join(product.materials),
        "colors": " ".join(product.colors),
        "min_order_qty": product.min_order_qty,
        "price_min": product.price_min,
        "price_max": product.price_max,
        "delivery_days": product.delivery_days,
        "product_group_id": product.product_group_id,
        "price": product.price,
        "main_image_url": safe_absolute_http_url(product.image_url),
        "product_url": safe_product_source_url(product.product_url),
        "status": "active" if product.active else "inactive",
        "updated_at": str(product.updated_at or ""),
        "is_deleted": product.is_deleted,
        "display_yn": product.display_yn or "Y",
        "mall_id": product.mall_id,
    }
    return {key: value for key, value in doc.items() if value is not None}


def legacy_document_ids_for_marqo_docs(docs: Iterable[Mapping[str, Any]]) -> list[str]:
    ids = []
    seen = set()
    for doc in docs:
        legacy_id = legacy_product_document_id(doc.get("mall_id"), doc.get("product_id"))
        document_id = str(doc.get("_id") or "")
        if legacy_id and legacy_id != document_id and legacy_id not in seen:
            ids.append(legacy_id)
            seen.add(legacy_id)
    return ids


def product_embedding_text(doc: Mapping[str, Any]) -> str:
    compact_text = str(doc.get(SEARCH_TEXT_FIELD) or "").strip()
    if compact_text:
        return compact_text
    parts = [
        doc.get("product_name"),
        doc.get("category_name"),
        doc.get("description"),
        doc.get("keywords"),
        doc.get("print_methods"),
        doc.get("materials"),
        doc.get("colors"),
        f"최소주문수량 {doc.get('min_order_qty')}" if doc.get("min_order_qty") else "",
        f"납기 {doc.get('delivery_days')}일" if doc.get("delivery_days") else "",
    ]
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def normalize_query_embedding_cache_key(text: str) -> str:
    return " ".join(str(text or "").split()).strip().casefold()


def qwen_text_query_embedding_cache_key(text: str) -> str:
    return f"text:{normalize_query_embedding_cache_key(text)}"


def qwen_image_query_embedding_cache_key(image_hash: str | None, image_data_url: str | None) -> str:
    token = str(image_hash or "").strip().lower()
    if not token and image_data_url:
        token = hashlib.sha256(str(image_data_url).encode("utf-8")).hexdigest()
    return f"image:{token}" if token else ""


def embedding_proxy_headers(api_key: str | None) -> dict[str, str]:
    key = str(api_key or "").strip()
    return {"X-Embedding-Proxy-Key": key} if key else {}


def runtime_qwen_query_cache_kind(key: str) -> str:
    return "image" if str(key or "").startswith("image:") else "text"


def load_qwen_query_embedding_cache(path: Path | None, expected_dimensions: int) -> dict[str, list[float]]:
    if path is None:
        return {}
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    if cache_path.suffix.lower() == ".gz":
        with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    raw_items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw_items, list):
        raise ValueError("Qwen query embedding cache must contain an items list")
    vectors: dict[str, list[float]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        vector = item.get("vector")
        if not text or not isinstance(vector, list):
            continue
        vectors[normalize_query_embedding_cache_key(text)] = validate_qwen_embedding_response(
            {"embeddings": [vector]},
            expected_count=1,
            expected_dimensions=expected_dimensions,
            context=f"Qwen query embedding cache vector for {text!r}",
        )[0]
    return vectors


def validate_qwen_embedding_response(
    data: Any,
    *,
    expected_count: int,
    expected_dimensions: int,
    context: str,
) -> list[list[float]]:
    embeddings = data.get("embeddings") if isinstance(data, dict) else None
    if not isinstance(embeddings, list):
        raise ValueError(f"{context} must contain an embeddings list")
    if len(embeddings) != expected_count:
        raise ValueError(f"{context} returned {len(embeddings)} embeddings; expected {expected_count}")
    vectors: list[list[float]] = []
    for index, vector in enumerate(embeddings):
        if not isinstance(vector, list):
            raise ValueError(f"{context} embedding {index} must be a list")
        if len(vector) != expected_dimensions:
            raise ValueError(
                f"{context} embedding {index} has {len(vector)} dimensions; expected {expected_dimensions}"
            )
        numeric_vector: list[float] = []
        for value_index, value in enumerate(vector):
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{context} embedding {index} value {value_index} is not numeric") from exc
            if not math.isfinite(number):
                raise ValueError(f"{context} embedding {index} value {value_index} is not finite")
            numeric_vector.append(number)
        vectors.append(numeric_vector)
    return vectors


def marqo_hit_to_product(hit: dict[str, Any]) -> ProductDocument:
    return ProductDocument.model_validate(
        {
            "product_id": hit.get("product_id") or hit.get("_id"),
            "product_name": hit.get("product_name") or hit.get("name") or hit.get("_id"),
            "category_name": hit.get("category_name") or hit.get("category") or "",
            "price": hit.get("price"),
            "main_image_url": hit.get("main_image_url") or hit.get("image_url"),
            "product_url": hit.get("product_url"),
            "status": hit.get("status") or "active",
            "updated_at": hit.get("updated_at"),
            "is_deleted": hit.get("is_deleted") or False,
            "display_yn": hit.get("display_yn") or "Y",
            "mall_id": hit.get("mall_id"),
            "description": hit.get("description"),
            "keywords": hit.get("keywords"),
            "print_methods": hit.get("print_methods"),
            "materials": hit.get("materials"),
            "colors": hit.get("colors"),
            "min_order_qty": hit.get("min_order_qty"),
            "price_min": hit.get("price_min"),
            "price_max": hit.get("price_max"),
            "delivery_days": hit.get("delivery_days"),
            "product_group_id": hit.get("product_group_id"),
            "extra": {SEARCH_TEXT_FIELD: hit.get(SEARCH_TEXT_FIELD)} if hit.get(SEARCH_TEXT_FIELD) else {},
        }
    )


def extract_marqo_item_failures(data: Any, fallback_ids: list[str], action: str) -> list[dict[str, str]]:
    if not isinstance(data, dict):
        return []
    failures = []
    for index, item in enumerate(extract_marqo_items(data)):
        if not isinstance(item, dict) or not marqo_item_failed(item):
            continue
        raw_id = str(
            item.get("_id")
            or item.get("document_id")
            or item.get("id")
            or (fallback_ids[index] if index < len(fallback_ids) else "")
        )
        product_id = str(item.get("product_id") or public_product_id_from_document_id(raw_id))
        if not product_id:
            continue
        failure = {"product_id": product_id, "reason": marqo_item_failure_reason(item, action)}
        if raw_id and raw_id != product_id:
            failure["document_id"] = raw_id
        failures.append(failure)
    return failures


def extract_marqo_items(data: dict[str, Any]) -> list[Any]:
    items = data.get("items")
    if isinstance(items, list):
        return items
    details = data.get("details")
    if isinstance(details, dict) and isinstance(details.get("items"), list):
        return details["items"]
    return []


def marqo_item_failed(item: dict[str, Any]) -> bool:
    if item.get("success") is False:
        return True
    if item.get("error") or item.get("errors"):
        return True
    status = str(item.get("status") or item.get("result") or "").strip().lower()
    if status in {"failed", "failure", "error", "rejected"}:
        return True
    status_code = item.get("statusCode") or item.get("status_code") or item.get("code")
    try:
        return int(status_code) >= 400
    except (TypeError, ValueError):
        return False


def marqo_item_failure_reason(item: dict[str, Any], action: str) -> str:
    for key in ("message", "error", "detail", "reason"):
        value = item.get(key)
        if value:
            return str(value)
    errors = item.get("errors")
    if errors:
        return str(errors)
    return f"{action}_failed"


def normalize_text(value: str) -> str:
    return " ".join(value.lower().replace("/", " ").replace("-", " ").split())


def tokenize(value: str) -> list[str]:
    return [term for term in normalize_text(value).replace(",", " ").split() if term]


SYNONYMS = {
    "검은": ["black", "검정", "블랙"],
    "검정": ["black", "검은", "블랙"],
    "블랙": ["black", "검은", "검정"],
    "흰": ["white", "화이트"],
    "하얀": ["white", "화이트"],
    "노란": ["노랑", "옐로우", "yellow"],
    "노랑": ["노란", "옐로우", "yellow"],
    "옐로우": ["노란", "노랑", "yellow"],
    "스텐": ["스테인리스", "stainless"],
    "텀블러": ["tumbler", "보틀", "보온병", "보냉병", "물병"],
    "보틀": ["tumbler", "텀블러", "물병", "보온병"],
    "물병": ["보틀", "텀블러", "보온병"],
    "보온병": ["텀블러", "보틀", "물병", "보냉병", "thermos"],
    "보냉병": ["텀블러", "보틀", "물병", "보온병"],
    "머그컵": ["머그", "mug"],
    "머그": ["머그컵", "mug"],
    "우산": ["umbrella", "3단", "장우산"],
    "부채": ["손부채", "전통부채", "접이식부채", "합죽선", "오죽선"],
    "포스트잇": ["점착메모지", "메모지", "점착", "sticky", "memo"],
    "점착": ["메모지", "sticky", "memo"],
    "메모지": ["점착", "sticky", "memo"],
    "상패": ["트로피", "감사패", "crystal"],
    "크리스탈": ["상패", "트로피", "crystal"],
    "볼펜": ["pen", "펜"],
    "가방": ["bag", "백", "에코백", "장바구니"],
    "에코백": ["가방", "백", "장바구니"],
    "장바구니": ["가방", "백", "에코백"],
    "달력": ["calendar", "캘린더", "카렌다", "탁상달력", "벽걸이달력"],
    "캘린더": ["달력", "카렌다", "calendar"],
    "카렌다": ["달력", "캘린더", "calendar"],
    "타올": ["타월", "수건", "towel"],
    "타월": ["타올", "수건", "towel"],
    "수건": ["타올", "타월", "towel"],
    "마우스패드": ["mousepad", "장패드"],
    "선풍기": ["손선풍기", "핸디선풍기", "휴대용선풍기"],
    "손선풍기": ["선풍기", "핸디선풍기", "휴대용선풍기"],
    "보조배터리": ["배터리", "powerbank", "power bank"],
    "물티슈": ["티슈", "위생티슈", "wet tissue"],
    "네임택": ["네임텍", "명찰", "name tag", "nametag"],
    "네임텍": ["네임택", "명찰", "name tag", "nametag"],
    "키링": ["열쇠고리", "키홀더", "keyring", "key holder"],
    "열쇠고리": ["키링", "키홀더", "keyring", "key holder"],
    "키홀더": ["키링", "열쇠고리", "keyring", "key holder"],
}


def expand_terms(terms: Iterable[str], custom_synonyms: Mapping[str, Iterable[str]] | None = None) -> list[str]:
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        expanded.extend(SYNONYMS.get(term, []))
        if custom_synonyms:
            expanded.extend(str(value).strip() for value in custom_synonyms.get(term, []) if str(value).strip())
    seen = set()
    deduped = []
    for term in expanded:
        if term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def expanded_query_text(value: str | None, custom_synonyms: Mapping[str, Iterable[str]] | None = None) -> str | None:
    if not value:
        return None
    terms = expand_terms(tokenize(value), custom_synonyms)
    if not terms:
        return normalize_text(value)
    return " ".join(terms)


def local_text_query(value: str, custom_synonyms: Mapping[str, Iterable[str]] | None = None) -> LocalTextQuery:
    return LocalTextQuery(
        normalized=normalize_text(value),
        terms=tuple(expand_terms(tokenize(value), custom_synonyms)),
    )


def local_product_record(product: ProductDocument) -> LocalProductRecord:
    doc_text = normalize_text(product.text_blob())
    category_text = normalize_text(product.category)
    image_tags = product.extra.get("image_tags", [])
    if isinstance(image_tags, str):
        image_tag_text = image_tags
    else:
        image_tag_text = " ".join(str(item) for item in image_tags)
    return LocalProductRecord(
        product=product,
        doc_text=doc_text,
        doc_terms=frozenset(expand_terms(tokenize(doc_text))),
        category_text=category_text,
        category_terms=frozenset(expand_terms(tokenize(category_text))),
        image_terms=frozenset(expand_terms(tokenize(image_tag_text))),
        image_hash=str(product.extra.get("image_hash", "") or ""),
        has_image_url=bool(product.image_url),
    )


def expanded_record_terms(
    terms: frozenset[str],
    custom_synonyms: Mapping[str, Iterable[str]] | None = None,
) -> frozenset[str]:
    if not custom_synonyms:
        return terms
    return frozenset(expand_terms(terms, custom_synonyms))


def term_matches_document(term: str, doc_text: str, doc_terms: set[str]) -> bool:
    if term in doc_text or term in doc_terms:
        return True
    term_length = len(term)
    if term_length < 2:
        return False
    first = term[0] if term else ""
    term_starts_with_hangul = starts_with_hangul(term)
    for doc_term in doc_terms:
        if abs(term_length - len(doc_term)) > 1 or min(term_length, len(doc_term)) < 2:
            continue
        if term_starts_with_hangul and starts_with_hangul(doc_term) and first != doc_term[0]:
            continue
        if edit_distance_at_most(term, doc_term, 1):
            return True
    return False


def is_near_match(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 2:
        return False
    if abs(len(left) - len(right)) > 1:
        return False
    if starts_with_hangul(left) and starts_with_hangul(right) and left[0] != right[0]:
        return False
    return edit_distance_at_most(left, right, 1)


def starts_with_hangul(value: str) -> bool:
    return bool(value and "\uac00" <= value[0] <= "\ud7a3")


def edit_distance_at_most(left: str, right: str, maximum: int) -> bool:
    if maximum < 0:
        return False
    if left == right:
        return True
    if abs(len(left) - len(right)) > maximum:
        return False
    if maximum == 0:
        return False
    if maximum == 1:
        left_length = len(left)
        right_length = len(right)
        if left_length == right_length:
            mismatches = 0
            for index in range(left_length):
                if left[index] != right[index]:
                    mismatches += 1
                    if mismatches > 1:
                        return False
            return True
        if left_length > right_length:
            longer = left
            shorter = right
        else:
            longer = right
            shorter = left
        shorter_index = 0
        longer_index = 0
        skipped = False
        while shorter_index < len(shorter) and longer_index < len(longer):
            if shorter[shorter_index] == longer[longer_index]:
                shorter_index += 1
                longer_index += 1
                continue
            if skipped:
                return False
            skipped = True
            longer_index += 1
        return True
    previous = list(range(len(right) + 1))
    right_length = len(right)
    for left_index in range(1, len(left) + 1):
        left_char = left[left_index - 1]
        current = [left_index]
        row_min = current[0]
        for right_index in range(1, right_length + 1):
            right_char = right[right_index - 1]
            insert_cost = current[right_index - 1] + 1
            delete_cost = previous[right_index] + 1
            replace_cost = previous[right_index - 1] + (0 if left_char == right_char else 1)
            value = min(insert_cost, delete_cost, replace_cost)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > maximum:
            return False
        previous = current
    return previous[-1] <= maximum


def normalize_scores(hits: list[EngineHit]) -> list[EngineHit]:
    if not hits:
        return []
    max_score = max(hit.score for hit in hits)
    if max_score <= 0 or math.isclose(max_score, 1.0):
        return hits
    return [
        EngineHit(document=hit.document, score=hit.score / max_score, source_scores=hit.source_scores)
        for hit in hits
    ]
