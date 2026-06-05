from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager
from urllib.parse import quote, urljoin, urlparse

from .cache import MemorySearchCache, SearchCache
from .category_intent import infer_category_intents
from .config import MAX_OPERATIONAL_SEARCH_OFFSET, Settings
from .engine import EngineHit, EngineQuery, MARQO_MAX_SEARCH_CANDIDATES, SearchEngine
from .identifiers import normalize_mall_id, product_identity_key
from .image_validation import ValidatedImage, validate_image_base64
from .models import ClickLogRequest, QueryType, SearchMeta, SearchRequest, SearchResponse, SearchResultItem
from .query_normalizer import build_search_query, normalize_query_text
from .url_safety import product_url_contains_product_id, safe_absolute_http_url


MAX_LOG_STRING_LENGTH = 1000
MAX_LOG_COLLECTION_LENGTH = 100
MAX_LOG_DEPTH = 8
MAX_LOG_TAIL_LIMIT = 1000
DEFAULT_LOG_KEEP_OPEN_SECONDS = 0.0
CACHE_POLICY_VERSION = 12
PRODUCT_GROUP_COLLAPSE_OVERFETCH = 2
DEFAULT_RESPONSE_CATEGORY = "기타"
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"\b(?:\+?82[-\s]?)?0?1[016789][-\s]?\d{3,4}[-\s]?\d{4}\b")
LANDLINE_PATTERN = re.compile(r"\b0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}\b")
KOREAN_ID_PATTERN = re.compile(r"\b\d{6}-[1-4]\d{6}\b")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(password|passwd|pwd|api[_-]?key|admin[_-]?key|x[_-]?api[_-]?key|x[_-]?admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|token|secret)\s*[:=]\s*([^\s,;&]+)",
    re.IGNORECASE,
)
AUTHORIZATION_PATTERN = re.compile(r"\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE)
IMAGE_DATA_URL_PATTERN = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
SENSITIVE_LOG_KEYS = {
    "authorization",
    "api-key",
    "api_key",
    "apikey",
    "admin-key",
    "admin_key",
    "adminkey",
    "connection-string",
    "connection_string",
    "connectionstring",
    "image-base64",
    "image_base64",
    "imagebase64",
    "mssql-connection-string",
    "mssql_connection_string",
    "mssqlconnectionstring",
    "image-data-url",
    "image_data_url",
    "imagedataurl",
    "password",
    "passwd",
    "public-api-key",
    "public_api_key",
    "publicapikey",
    "pwd",
    "secret",
    "token",
    "x-admin-key",
    "x-api-key",
}
SENSITIVE_LOG_KEY_COMPACT_ALIASES = {
    "accesskey",
    "accesstoken",
    "adminkey",
    "adminapikey",
    "apikey",
    "authorization",
    "clientsecret",
    "connectionstring",
    "idtoken",
    "imagebase64",
    "imagedataurl",
    "mssqlconnectionstring",
    "password",
    "passwd",
    "publicapikey",
    "pwd",
    "refreshtoken",
    "secret",
    "secretkey",
    "token",
    "xadminkey",
    "xapikey",
}
SENSITIVE_LOG_KEY_SUFFIXES = (
    "accesskey",
    "accesstoken",
    "adminkey",
    "adminapikey",
    "apikey",
    "clientsecret",
    "connectionstring",
    "idtoken",
    "mssqlconnectionstring",
    "password",
    "passwd",
    "secret",
    "secretkey",
    "token",
)


@dataclass(frozen=True)
class PreparedSearch:
    limit: int
    offset: int
    engine_limit: int
    image: ValidatedImage | None
    normalized_query: str | None
    search_query: str | None
    inferred_categories: tuple[str, ...]
    text_weight: float
    image_weight: float
    cache_key: str


@dataclass(frozen=True)
class SearchExecutionStats:
    candidate_limits: tuple[int, ...]
    raw_candidate_counts: tuple[int, ...]
    collapsed_candidate_counts: tuple[int, ...]
    max_candidate_limit: int
    required_count: int

    @property
    def search_attempts(self) -> int:
        return len(self.candidate_limits)

    @property
    def adaptive_refetches(self) -> int:
        return max(0, self.search_attempts - 1)

    @property
    def final_candidate_limit(self) -> int | None:
        return self.candidate_limits[-1] if self.candidate_limits else None

    @property
    def final_raw_candidate_count(self) -> int | None:
        return self.raw_candidate_counts[-1] if self.raw_candidate_counts else None

    @property
    def final_collapsed_candidate_count(self) -> int | None:
        return self.collapsed_candidate_counts[-1] if self.collapsed_candidate_counts else None

    @property
    def underfilled_after_max_candidates(self) -> bool:
        collapsed = self.final_collapsed_candidate_count
        final_limit = self.final_candidate_limit
        return (
            collapsed is not None
            and final_limit is not None
            and collapsed <= self.required_count
            and final_limit >= self.max_candidate_limit
        )


@dataclass
class InflightSearch:
    event: threading.Event
    response: SearchResponse | None = None
    exception: Exception | None = None


@dataclass
class InflightImageValidation:
    event: threading.Event
    image: ValidatedImage | None = None
    exception: BaseException | None = None


class SearchCacheMissInFlight(RuntimeError):
    pass


class SearchLogger:
    _locks: dict[Path, threading.RLock] = {}
    _locks_guard = threading.RLock()

    def __init__(self, path: Path, keep_open_seconds: float = DEFAULT_LOG_KEEP_OPEN_SECONDS):
        self.path = path
        self.keep_open_seconds = max(0.0, float(keep_open_seconds or 0.0))
        self._lock = self._lock_for(path)
        self._writers_lock = threading.RLock()
        self._active_writers = 0
        self._output: Any | None = None
        self._last_output_used_at: float | None = None
        self._parent_ready = False
        self._output_opens = 0
        self._output_reuses = 0
        self._output_closes = 0
        self._idle_closes = 0
        self._write_events = 0
        self._write_total_seconds = 0.0
        self._write_max_seconds = 0.0
        self._last_write_seconds = 0.0
        self._write_errors = 0
        self._last_write_error: str | None = None
        self._last_write_error_type: str | None = None
        self._last_write_error_at: str | None = None

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        key = path.expanduser().resolve()
        with cls._locks_guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._locks[key] = lock
            return lock

    def write(self, entry: dict[str, Any]) -> None:
        try:
            safe_entry = sanitize_log_entry(entry)
            line = (json.dumps(safe_entry, ensure_ascii=False) + "\n").encode("utf-8")
        except Exception as exc:
            self._record_write_error(exc)
            return
        started = time.perf_counter()
        self._begin_write()
        try:
            with self._lock:
                try:
                    if self.keep_open_seconds <= 0:
                        self._write_line_once_locked(line)
                    else:
                        output = self._ensure_output_locked()
                        output.write(line)
                        output.flush()
                    self._record_write_success_locked(time.perf_counter() - started)
                except Exception as exc:
                    if isinstance(exc, FileNotFoundError):
                        self._parent_ready = False
                    self._close_output_locked(record_errors=False)
                    self._record_write_error_locked(exc)
        finally:
            self._finish_write()

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            self._flush_output_locked()
            return read_jsonl_tail(self.path, limit)

    def status(self) -> dict[str, Any]:
        with self._writers_lock:
            active_writers = self._active_writers
        with self._lock:
            self._close_idle_output_locked()
            total_ms = self._write_total_seconds * 1000.0
            max_ms = self._write_max_seconds * 1000.0
            last_ms = self._last_write_seconds * 1000.0
            avg_ms = total_ms / self._write_events if self._write_events else 0.0
            return {
                "keep_open_seconds": self.keep_open_seconds,
                "write_events": self._write_events,
                "write_total_ms": round(total_ms, 3),
                "write_avg_ms": round(avg_ms, 3),
                "write_max_ms": round(max_ms, 3),
                "last_write_ms": round(last_ms, 3),
                "active_writers": active_writers,
                "buffer_open": self._output is not None,
                "output_opens": self._output_opens,
                "output_reuses": self._output_reuses,
                "output_closes": self._output_closes,
                "idle_closes": self._idle_closes,
                "write_errors": self._write_errors,
                "last_write_error": self._last_write_error,
                "last_write_error_type": self._last_write_error_type,
                "last_write_error_at": self._last_write_error_at,
            }

    def close(self) -> None:
        with self._lock:
            self._close_output_locked(record_errors=False)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _begin_write(self) -> None:
        with self._writers_lock:
            self._active_writers += 1

    def _finish_write(self) -> None:
        should_close = False
        with self._writers_lock:
            self._active_writers = max(0, self._active_writers - 1)
            should_close = self._active_writers == 0
        if should_close:
            with self._lock:
                if self.keep_open_seconds <= 0:
                    self._close_output_locked(record_errors=True)

    def _ensure_output_locked(self) -> Any:
        self._close_idle_output_locked()
        if self._output is None:
            if not self._parent_ready:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._parent_ready = True
            self._output = self.path.open("ab", buffering=0)
            self._output_opens += 1
        else:
            self._output_reuses += 1
        return self._output

    def _flush_output_locked(self) -> None:
        if self._output is None:
            return
        try:
            self._output.flush()
        except Exception as exc:
            self._close_output_locked(record_errors=False)
            self._record_write_error_locked(exc)

    def _close_output_locked(self, *, record_errors: bool) -> None:
        output = self._output
        self._output = None
        self._last_output_used_at = None
        if output is None:
            return
        try:
            output.close()
            self._output_closes += 1
        except Exception as exc:
            if record_errors:
                self._record_write_error_locked(exc)

    def _close_idle_output_locked(self) -> None:
        if self._output is None or self.keep_open_seconds <= 0:
            return
        with self._writers_lock:
            if self._active_writers > 0:
                return
        last_used_at = self._last_output_used_at
        if last_used_at is None:
            return
        if time.monotonic() - last_used_at < self.keep_open_seconds:
            return
            self._idle_closes += 1
        self._close_output_locked(record_errors=True)

    def _write_line_once_locked(self, line: bytes) -> None:
        if not self._parent_ready:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._parent_ready = True
        fd: int | None = None
        try:
            fd = os.open(os.fspath(self.path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            self._output_opens += 1
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("search log append wrote zero bytes")
                view = view[written:]
        finally:
            if fd is not None:
                os.close(fd)
                self._output_closes += 1

    def _record_write_success_locked(self, elapsed_seconds: float) -> None:
        self._write_events += 1
        self._write_total_seconds += max(0.0, elapsed_seconds)
        self._last_write_seconds = max(0.0, elapsed_seconds)
        self._write_max_seconds = max(self._write_max_seconds, self._last_write_seconds)
        self._last_output_used_at = time.monotonic()

    def _record_write_error(self, exc: Exception) -> None:
        with self._lock:
            self._record_write_error_locked(exc)

    def _record_write_error_locked(self, exc: Exception) -> None:
        self._write_errors += 1
        self._last_write_error = str(exc)
        self._last_write_error_type = exc.__class__.__name__
        self._last_write_error_at = datetime.now(timezone.utc).isoformat()


def normalize_tail_limit(limit: int, max_limit: int = MAX_LOG_TAIL_LIMIT) -> int:
    return min(max(int(limit), 1), max_limit)


def read_jsonl_tail(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = read_tail_lines(path, normalize_tail_limit(limit))
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def read_tail_lines(path: Path, limit: int, chunk_size: int = 64 * 1024) -> list[str]:
    normalized_limit = normalize_tail_limit(limit)
    with path.open("rb") as source:
        source.seek(0, 2)
        position = source.tell()
        buffer = b""
        newline_count = 0
        while position > 0 and newline_count <= normalized_limit:
            read_size = min(chunk_size, position)
            position -= read_size
            source.seek(position)
            buffer = source.read(read_size) + buffer
            newline_count = buffer.count(b"\n")
    raw_lines = buffer.splitlines()
    if len(raw_lines) > normalized_limit:
        raw_lines = raw_lines[-normalized_limit:]
    return [line.decode("utf-8", errors="replace") for line in raw_lines]


def read_reverse_lines(path: Path, chunk_size: int = 64 * 1024):
    if not path.exists():
        return
    with path.open("rb") as source:
        source.seek(0, 2)
        position = source.tell()
        remainder = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            source.seek(position)
            chunk = source.read(read_size) + remainder
            lines = chunk.split(b"\n")
            remainder = lines[0]
            for raw_line in reversed(lines[1:]):
                if not raw_line:
                    continue
                if raw_line.endswith(b"\r"):
                    raw_line = raw_line[:-1]
                yield raw_line.decode("utf-8", errors="replace")
        if remainder:
            if remainder.endswith(b"\r"):
                remainder = remainder[:-1]
            yield remainder.decode("utf-8", errors="replace")


def sanitize_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_log_value(entry)
    return sanitized if isinstance(sanitized, dict) else {}


def log_detail_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): log_detail_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [log_detail_value(item) for item in value]
    if isinstance(value, tuple):
        return [log_detail_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def sanitize_log_value(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_LOG_DEPTH:
        return "[truncated-depth]"
    if isinstance(value, dict):
        sanitized = {}
        items = list(value.items())
        for key, item in items[:MAX_LOG_COLLECTION_LENGTH]:
            key_text = str(key)
            if is_sensitive_log_key(key_text):
                sanitized[key_text] = "[redacted-secret]"
            else:
                sanitized[key_text] = sanitize_log_value(item, depth + 1)
        if len(items) > MAX_LOG_COLLECTION_LENGTH:
            sanitized["__truncated_keys__"] = len(items) - MAX_LOG_COLLECTION_LENGTH
        return sanitized
    if isinstance(value, list):
        sanitized_list = [sanitize_log_value(item, depth + 1) for item in value[:MAX_LOG_COLLECTION_LENGTH]]
        if len(value) > MAX_LOG_COLLECTION_LENGTH:
            sanitized_list.append(f"...[truncated {len(value) - MAX_LOG_COLLECTION_LENGTH} items]")
        return sanitized_list
    if isinstance(value, tuple):
        sanitized_list = [sanitize_log_value(item, depth + 1) for item in value[:MAX_LOG_COLLECTION_LENGTH]]
        if len(value) > MAX_LOG_COLLECTION_LENGTH:
            sanitized_list.append(f"...[truncated {len(value) - MAX_LOG_COLLECTION_LENGTH} items]")
        return sanitized_list
    if isinstance(value, str):
        redacted = EMAIL_PATTERN.sub("[redacted-email]", value)
        redacted = PHONE_PATTERN.sub("[redacted-phone]", redacted)
        redacted = LANDLINE_PATTERN.sub("[redacted-phone]", redacted)
        redacted = KOREAN_ID_PATTERN.sub("[redacted-id]", redacted)
        redacted = SECRET_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=[redacted-secret]", redacted)
        redacted = AUTHORIZATION_PATTERN.sub("Authorization: Bearer [redacted-secret]", redacted)
        redacted = IMAGE_DATA_URL_PATTERN.sub("[redacted-image]", redacted)
        if len(redacted) > MAX_LOG_STRING_LENGTH:
            return redacted[:MAX_LOG_STRING_LENGTH] + "...[truncated]"
        return redacted
    return value


def is_sensitive_log_key(key: str) -> bool:
    normalized = str(key or "").strip().lower().replace("_", "-")
    compact = normalized.replace("-", "")
    return (
        normalized in SENSITIVE_LOG_KEYS
        or compact in SENSITIVE_LOG_KEY_COMPACT_ALIASES
        or any(compact.endswith(suffix) for suffix in SENSITIVE_LOG_KEY_SUFFIXES)
    )


class AISearchService:
    def __init__(
        self,
        engine: SearchEngine,
        settings: Settings,
        logger: SearchLogger | None = None,
        cache: SearchCache | None = None,
    ):
        self.engine = engine
        self.settings = settings
        self.logger = logger or SearchLogger(settings.search_log_path, keep_open_seconds=settings.log_keep_open_seconds)
        self.cache = cache or MemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries)
        self._inflight_lock = threading.RLock()
        self._inflight_searches: dict[str, InflightSearch] = {}
        self._singleflight_stats_lock = threading.RLock()
        self._singleflight_wait_events = 0
        self._singleflight_wait_timeouts = 0
        self._singleflight_total_wait_seconds = 0.0
        self._singleflight_max_wait_seconds = 0.0
        self._policy_fingerprint_lock = threading.RLock()
        self._policy_fingerprint_tokens: dict[str, str] = {}
        self._base_policy_token = cache_policy_fingerprint_digest(cache_policy_base_fingerprint(settings))
        self._image_validation_lock = threading.RLock()
        self._image_validation_inflight: dict[str, InflightImageValidation] = {}
        self._image_validation_stats_lock = threading.RLock()
        self._image_validation_wait_events = 0
        self._image_validation_wait_timeouts = 0
        self._image_validation_total_wait_seconds = 0.0
        self._image_validation_max_wait_seconds = 0.0
        self._image_validation_cache_ttl_seconds = max(0.0, float(settings.image_validation_cache_ttl_seconds))
        self._image_validation_cache_max_entries = max(1, int(settings.image_validation_cache_max_entries))
        self._image_validation_cache: OrderedDict[str, tuple[float, ValidatedImage]] = OrderedDict()
        self._image_validation_cache_hits = 0
        self._image_validation_cache_misses = 0
        self._image_validation_cache_evictions = 0

    def search(
        self,
        request: SearchRequest,
        compute_context: Callable[[], ContextManager[None]] | None = None,
    ) -> SearchResponse:
        started = time.perf_counter()
        return self._search(request, started, compute_context)

    def _search(
        self,
        request: SearchRequest,
        started: float,
        compute_context: Callable[[], ContextManager[None]] | None,
    ) -> SearchResponse:
        prepared = self._prepare_search(request, compute_context)
        cached = self._cached_response(request, prepared, started)
        if cached is not None:
            return cached
        if not self._singleflight_enabled():
            return self._execute_store_and_log(request, prepared, started, compute_context)
        return self._search_with_singleflight(request, prepared, started, compute_context)

    def _singleflight_enabled(self) -> bool:
        try:
            return int(getattr(self.cache, "ttl_seconds", 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    def _search_with_singleflight(
        self,
        request: SearchRequest,
        prepared: PreparedSearch,
        started: float,
        compute_context: Callable[[], ContextManager[None]] | None,
    ) -> SearchResponse:
        with self._inflight_lock:
            inflight = self._inflight_searches.get(prepared.cache_key)
            if inflight is None:
                inflight = InflightSearch(threading.Event())
                self._inflight_searches[prepared.cache_key] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            completed = self._wait_for_inflight_search(inflight)
            if not completed:
                cached = self._cached_response(request, prepared, started)
                if cached is not None:
                    return cached
                raise SearchCacheMissInFlight("identical search is still running")
            if inflight.exception is not None:
                raise inflight.exception
            cached = self._cached_response(request, prepared, started)
            if cached is not None:
                return cached
            if inflight.response is not None:
                return self._cached_response_copy(request, prepared, started, inflight.response)
            return self._search_with_singleflight(request, prepared, started, compute_context)

        distributed_claimed = False
        try:
            distributed_claim = self._claim_distributed_miss_owner(prepared.cache_key)
            if distributed_claim is False:
                cached, wait_completed, wait_elapsed_seconds = self._wait_for_distributed_cache_fill(
                    request,
                    prepared,
                    started,
                )
                self._record_distributed_miss_wait(wait_completed, wait_elapsed_seconds)
                if cached is not None:
                    inflight.response = cached
                    return cached
                distributed_claim = self._claim_distributed_miss_owner(prepared.cache_key)
                if distributed_claim is False:
                    raise SearchCacheMissInFlight("identical search is still running on another API worker")
            if distributed_claim is True:
                distributed_claimed = True
            response = self._execute_store_and_log(request, prepared, started, compute_context)
            inflight.response = response
            return response
        except Exception as exc:
            inflight.exception = exc
            raise
        finally:
            if distributed_claimed:
                self._release_distributed_miss_owner(prepared.cache_key)
            with self._inflight_lock:
                self._inflight_searches.pop(prepared.cache_key, None)
                inflight.event.set()

    def _wait_for_inflight_search(self, inflight: InflightSearch) -> bool:
        wait_seconds = self._singleflight_wait_timeout_seconds()
        started = time.perf_counter()
        completed = inflight.event.wait(timeout=wait_seconds)
        elapsed_seconds = time.perf_counter() - started
        with self._singleflight_stats_lock:
            self._singleflight_wait_events += 1
            self._singleflight_total_wait_seconds += elapsed_seconds
            self._singleflight_max_wait_seconds = max(self._singleflight_max_wait_seconds, elapsed_seconds)
            if not completed:
                self._singleflight_wait_timeouts += 1
        return completed

    def _singleflight_wait_timeout_seconds(self) -> float:
        try:
            return max(float(self.settings.cache_miss_wait_seconds), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def singleflight_status(self) -> dict[str, Any]:
        with self._inflight_lock:
            in_flight = len(self._inflight_searches)
        with self._singleflight_stats_lock:
            wait_events = self._singleflight_wait_events
            total_wait_ms = round(self._singleflight_total_wait_seconds * 1000, 3)
            max_wait_ms = round(self._singleflight_max_wait_seconds * 1000, 3)
            avg_wait_ms = round(total_wait_ms / wait_events, 3) if wait_events else 0.0
            wait_timeouts = self._singleflight_wait_timeouts
        return {
            "enabled": self._singleflight_enabled(),
            "in_flight": in_flight,
            "wait_timeout_seconds": self._singleflight_wait_timeout_seconds(),
            "wait_events": wait_events,
            "wait_timeouts": wait_timeouts,
            "total_wait_ms": total_wait_ms,
            "avg_wait_ms": avg_wait_ms,
            "max_wait_ms": max_wait_ms,
        }

    def _claim_distributed_miss_owner(self, cache_key: str) -> bool | None:
        claim = getattr(self.cache, "claim_miss_owner", None)
        if not callable(claim):
            return None
        try:
            return bool(claim(cache_key, self.settings.cache_miss_lock_seconds))
        except Exception:
            return None

    def _release_distributed_miss_owner(self, cache_key: str) -> None:
        release = getattr(self.cache, "release_miss_owner", None)
        if not callable(release):
            return
        try:
            release(cache_key)
        except Exception:
            return

    def _record_distributed_miss_wait(self, completed: bool, elapsed_seconds: float) -> None:
        record = getattr(self.cache, "record_miss_wait", None)
        if not callable(record):
            return
        try:
            record(completed=completed, elapsed_seconds=elapsed_seconds)
        except TypeError:
            try:
                record(completed, elapsed_seconds)
            except Exception:
                return
        except Exception:
            return

    def _wait_for_distributed_cache_fill(
        self,
        request: SearchRequest,
        prepared: PreparedSearch,
        started: float,
    ) -> tuple[SearchResponse | None, bool, float]:
        wait_seconds = max(float(self.settings.cache_miss_wait_seconds), 0.0)
        if wait_seconds <= 0:
            return None, False, 0.0
        poll_seconds = max(float(self.settings.cache_miss_poll_seconds), 0.001)
        wait_started = time.perf_counter()
        deadline = time.perf_counter() + wait_seconds
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None, False, time.perf_counter() - wait_started
            time.sleep(min(poll_seconds, remaining))
            cached = self._cached_response(request, prepared, started)
            if cached is not None:
                return cached, True, time.perf_counter() - wait_started

    def _execute_store_and_log(
        self,
        request: SearchRequest,
        prepared: PreparedSearch,
        started: float,
        compute_context: Callable[[], ContextManager[None]] | None,
    ) -> SearchResponse:
        if compute_context is None:
            response, execution_stats = self._execute_search(request, prepared, started)
        else:
            with compute_context():
                response, execution_stats = self._execute_search(request, prepared, started)
        self.cache.set(prepared.cache_key, response)
        self._log_search(
            request,
            response,
            image=prepared.image,
            normalized_query=prepared.normalized_query,
            inferred_categories=prepared.inferred_categories,
            cached=False,
            execution_stats=execution_stats,
        )
        return response

    def _execute_search(
        self,
        request: SearchRequest,
        prepared: PreparedSearch,
        started: float,
    ) -> tuple[SearchResponse, SearchExecutionStats]:
        engine_query = EngineQuery(
            q=prepared.search_query,
            image_data_url=prepared.image.data_url if prepared.image else None,
            image_hash=prepared.image.sha256 if prepared.image else None,
            mall_id=request.mall_id,
            category=request.category,
            print_method=request.print_method,
            material=request.material,
            color=request.color,
            min_price=request.min_price,
            max_price=request.max_price,
            quantity=request.quantity,
            max_delivery_days=request.max_delivery_days,
            inferred_categories=prepared.inferred_categories,
            limit=prepared.engine_limit,
            text_weight=prepared.text_weight,
            image_weight=prepared.image_weight,
            strict_mall_filter=should_strictly_filter_mall(self.settings, request.mall_id),
            query_synonyms=self.settings.query_synonyms,
        )
        related_start = 3 + prepared.offset
        related_end = related_start + prepared.limit
        hits, execution_stats = self._search_collapsed_hits(engine_query, request.mall_id, required_count=related_end)
        top_items = [self._to_item(hit, request.mall_id) for hit in hits[:3]]
        related_items = [self._to_item(hit, request.mall_id) for hit in hits[related_start:related_end]]
        has_more = len(hits) > related_end
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return (
            SearchResponse(
                top=top_items,
                items=related_items,
                suggested_categories=suggest_categories(
                    hits,
                    self.settings.category_suggestion_limit,
                    inferred_categories=prepared.inferred_categories,
                ),
                meta=SearchMeta(
                    query_type=request.query_type,
                    elapsed_ms=elapsed_ms,
                    engine=self.engine.name,
                    embedding_backend=self.settings.embedding_backend,
                    limit=prepared.limit,
                    offset=prepared.offset,
                    has_more=has_more,
                    next_offset=prepared.offset + len(related_items) if has_more else None,
                    mall_id=request.mall_id,
                    text_weight=prepared.text_weight if request.query_type != QueryType.IMAGE else None,
                    image_weight=prepared.image_weight if request.query_type != QueryType.TEXT else None,
                    low_confidence=is_low_confidence(top_items, self.settings.low_score_threshold),
                    notice=low_confidence_notice(top_items, self.settings.low_score_threshold),
                ),
            ),
            execution_stats,
        )

    def _search_collapsed_hits(
        self,
        engine_query: EngineQuery,
        mall_id: str | None,
        *,
        required_count: int,
    ) -> tuple[list[EngineHit], SearchExecutionStats]:
        max_candidate_limit = max(1, int(engine_query.max_candidates or 1))
        candidate_limit = min(max(1, int(engine_query.limit or 1)), max_candidate_limit)
        candidate_limits: list[int] = []
        raw_candidate_counts: list[int] = []
        collapsed_candidate_counts: list[int] = []
        while True:
            query = replace(engine_query, limit=candidate_limit)
            raw_hits = self.engine.search(query)
            raw_hit_count = len(raw_hits)
            visible_hits = apply_mall_visibility_policy(raw_hits, self.settings, mall_id)
            if should_require_display_image(self.engine):
                visible_hits = filter_hits_with_display_image(visible_hits)
            hits = collapse_product_groups(
                enforce_response_mall_filter(
                    visible_hits,
                    self.settings,
                    mall_id,
                )
            )
            candidate_limits.append(candidate_limit)
            raw_candidate_counts.append(raw_hit_count)
            collapsed_candidate_counts.append(len(hits))
            if len(hits) > required_count or candidate_limit >= max_candidate_limit:
                return hits, SearchExecutionStats(
                    tuple(candidate_limits),
                    tuple(raw_candidate_counts),
                    tuple(collapsed_candidate_counts),
                    max_candidate_limit=max_candidate_limit,
                    required_count=required_count,
                )
            if raw_hit_count < candidate_limit:
                return hits, SearchExecutionStats(
                    tuple(candidate_limits),
                    tuple(raw_candidate_counts),
                    tuple(collapsed_candidate_counts),
                    max_candidate_limit=max_candidate_limit,
                    required_count=required_count,
                )
            if raw_hit_count <= len(hits):
                return hits, SearchExecutionStats(
                    tuple(candidate_limits),
                    tuple(raw_candidate_counts),
                    tuple(collapsed_candidate_counts),
                    max_candidate_limit=max_candidate_limit,
                    required_count=required_count,
                )
            next_limit = adaptive_response_candidate_limit(
                candidate_limit,
                max_candidate_limit,
                collapsed_count=len(hits),
                required_count=required_count,
            )
            if next_limit <= candidate_limit:
                return hits, SearchExecutionStats(
                    tuple(candidate_limits),
                    tuple(raw_candidate_counts),
                    tuple(collapsed_candidate_counts),
                    max_candidate_limit=max_candidate_limit,
                    required_count=required_count,
                )
            candidate_limit = next_limit

    def cached_search(self, request: SearchRequest) -> SearchResponse | None:
        started = time.perf_counter()
        prepared = self._prepare_search(request)
        return self._cached_response(request, prepared, started)

    def _prepare_search(
        self,
        request: SearchRequest,
        compute_context: Callable[[], ContextManager[None]] | None = None,
    ) -> PreparedSearch:
        limit = min(max(request.limit, 1), self.settings.max_limit)
        offset = max(request.offset, 0)
        max_offset = min(self.settings.max_offset, MAX_OPERATIONAL_SEARCH_OFFSET)
        if offset > max_offset:
            raise ValueError(f"offset exceeds {max_offset}")
        engine_limit = response_candidate_limit(self.settings, request.mall_id, limit=limit, offset=offset)
        image = None
        if request.image_base64:
            image = self._validate_request_image(request, compute_context)
        normalized_query = normalize_query_text(request.q)
        search_query = build_search_query(request.q, normalized_query)
        inferred_categories = infer_category_intents(normalized_query or request.q)
        text_weight, image_weight = self._weights(request)
        cache_key = self._cache_key(
            request,
            image.sha256 if image else None,
            normalized_query,
            search_query,
            inferred_categories,
            text_weight,
            image_weight,
            limit,
            offset,
        )
        return PreparedSearch(
            limit=limit,
            offset=offset,
            engine_limit=engine_limit,
            image=image,
            normalized_query=normalized_query,
            search_query=search_query,
            inferred_categories=inferred_categories,
            text_weight=text_weight,
            image_weight=image_weight,
            cache_key=cache_key,
        )

    def _validate_request_image(
        self,
        request: SearchRequest,
        compute_context: Callable[[], ContextManager[None]] | None = None,
    ) -> ValidatedImage:
        validation_key = self._image_validation_key(request.image_base64 or "")
        cached = self._get_cached_validated_image(validation_key)
        if cached is not None:
            return cached
        with self._image_validation_lock:
            inflight = self._image_validation_inflight.get(validation_key)
            if inflight is None:
                inflight = InflightImageValidation(threading.Event())
                self._image_validation_inflight[validation_key] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            wait_started = time.perf_counter()
            completed = inflight.event.wait(timeout=self._image_validation_wait_timeout_seconds())
            elapsed_seconds = time.perf_counter() - wait_started
            self._record_image_validation_wait(completed, elapsed_seconds)
            if not completed:
                raise SearchCacheMissInFlight("identical image validation is still running")
            if inflight.exception is not None:
                raise inflight.exception
            if inflight.image is not None:
                return inflight.image
            cached = self._get_cached_validated_image(validation_key)
            if cached is not None:
                return cached
            return self._validate_request_image(request, compute_context)

        try:
            if compute_context is None:
                image = self._validate_request_image_uncached(request)
            else:
                with compute_context():
                    image = self._validate_request_image_uncached(request)
            inflight.image = image
            self._set_cached_validated_image(validation_key, image)
            return image
        except BaseException as exc:
            inflight.exception = exc
            raise
        finally:
            with self._image_validation_lock:
                self._image_validation_inflight.pop(validation_key, None)
                inflight.event.set()

    def _validate_request_image_uncached(self, request: SearchRequest) -> ValidatedImage:
        return validate_image_base64(
            request.image_base64 or "",
            max_bytes=self.settings.max_image_mb * 1024 * 1024,
            max_dimension=self.settings.max_image_dimension,
            min_dimension=self.settings.min_image_dimension,
            resize_dimension=self.settings.query_image_max_dimension,
            analyze_features=self.settings.query_image_analysis,
        )

    def _image_validation_key(self, image_base64: str) -> str:
        payload = str(image_base64 or "").strip()
        return json.dumps(
            {
                "version": 1,
                "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "max_bytes": self.settings.max_image_mb * 1024 * 1024,
                "max_dimension": self.settings.max_image_dimension,
                "query_image_max_dimension": self.settings.query_image_max_dimension,
                "query_image_analysis": self.settings.query_image_analysis,
                "min_dimension": self.settings.min_image_dimension,
            },
            sort_keys=True,
        )

    def _get_cached_validated_image(self, validation_key: str) -> ValidatedImage | None:
        if self._image_validation_cache_ttl_seconds <= 0:
            return None
        now = time.time()
        with self._image_validation_lock:
            cached = self._image_validation_cache.get(validation_key)
            if cached is None:
                self._image_validation_cache_misses += 1
                return None
            expires_at, image = cached
            if expires_at < now:
                self._image_validation_cache.pop(validation_key, None)
                self._image_validation_cache_misses += 1
                return None
            self._image_validation_cache.move_to_end(validation_key)
            self._image_validation_cache_hits += 1
            return image

    def _set_cached_validated_image(self, validation_key: str, image: ValidatedImage) -> None:
        if self._image_validation_cache_ttl_seconds <= 0:
            return
        expires_at = time.time() + self._image_validation_cache_ttl_seconds
        with self._image_validation_lock:
            self._image_validation_cache[validation_key] = (expires_at, image)
            self._image_validation_cache.move_to_end(validation_key)
            while len(self._image_validation_cache) > self._image_validation_cache_max_entries:
                self._image_validation_cache.popitem(last=False)
                self._image_validation_cache_evictions += 1

    def _record_image_validation_wait(self, completed: bool, elapsed_seconds: float) -> None:
        elapsed_seconds = max(float(elapsed_seconds or 0.0), 0.0)
        with self._image_validation_stats_lock:
            self._image_validation_wait_events += 1
            self._image_validation_total_wait_seconds += elapsed_seconds
            self._image_validation_max_wait_seconds = max(self._image_validation_max_wait_seconds, elapsed_seconds)
            if not completed:
                self._image_validation_wait_timeouts += 1

    def _image_validation_wait_timeout_seconds(self) -> float:
        try:
            return max(float(self.settings.image_validation_wait_seconds), 0.0)
        except (TypeError, ValueError):
            return self._singleflight_wait_timeout_seconds()

    def image_validation_status(self) -> dict[str, Any]:
        with self._image_validation_lock:
            in_flight = len(self._image_validation_inflight)
            now = time.time()
            expired = [key for key, (expires_at, _) in self._image_validation_cache.items() if expires_at < now]
            for key in expired:
                self._image_validation_cache.pop(key, None)
            cache_entry_count = len(self._image_validation_cache)
            cache_hits = self._image_validation_cache_hits
            cache_misses = self._image_validation_cache_misses
            cache_evictions = self._image_validation_cache_evictions
        with self._image_validation_stats_lock:
            wait_events = self._image_validation_wait_events
            wait_timeouts = self._image_validation_wait_timeouts
            total_wait_ms = round(self._image_validation_total_wait_seconds * 1000, 3)
            max_wait_ms = round(self._image_validation_max_wait_seconds * 1000, 3)
        return {
            "in_flight": in_flight,
            "wait_timeout_seconds": self._image_validation_wait_timeout_seconds(),
            "wait_events": wait_events,
            "wait_timeouts": wait_timeouts,
            "total_wait_ms": total_wait_ms,
            "avg_wait_ms": round(total_wait_ms / wait_events, 3) if wait_events else 0.0,
            "max_wait_ms": max_wait_ms,
            "cache_enabled": self._image_validation_cache_ttl_seconds > 0,
            "cache_ttl_seconds": self._image_validation_cache_ttl_seconds,
            "cache_max_entries": self._image_validation_cache_max_entries,
            "cache_entry_count": cache_entry_count,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_evictions": cache_evictions,
        }

    def _cached_response(
        self,
        request: SearchRequest,
        prepared: PreparedSearch,
        started: float,
    ) -> SearchResponse | None:
        cached = self.cache.get(prepared.cache_key)
        if cached is None:
            return None
        return self._cached_response_copy(request, prepared, started, cached)

    def _cached_response_copy(
        self,
        request: SearchRequest,
        prepared: PreparedSearch,
        started: float,
        response: SearchResponse,
    ) -> SearchResponse:
        cached = response.model_copy(
            update={
                "meta": response.meta.model_copy(
                    update={"elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
                )
            }
        )
        self._log_search(
            request,
            cached,
            image=prepared.image,
            normalized_query=prepared.normalized_query,
            inferred_categories=prepared.inferred_categories,
            cached=True,
        )
        return cached

    def _weights(self, request: SearchRequest) -> tuple[float, float]:
        if request.query_type == QueryType.TEXT:
            return 1.0, 0.0
        if request.query_type == QueryType.IMAGE:
            return 0.0, 1.0
        text = request.text_weight if request.text_weight is not None else self.settings.mixed_text_weight
        image = request.image_weight if request.image_weight is not None else self.settings.mixed_image_weight
        total = text + image
        if total <= 0:
            raise ValueError("text_weight and image_weight must be positive")
        return text / total, image / total

    def _to_item(self, hit: EngineHit, mall_id: str | None) -> SearchResultItem:
        product = hit.document
        score = result_score_to_unit_interval(hit.score)
        source_mall_id = response_policy_mall_id(self.settings, mall_id, product.mall_id)
        return SearchResultItem(
            product_id=product.product_id,
            name=product.name,
            category=response_category(product.category),
            price=resolve_product_price(product.price, source_mall_id, self.settings),
            image_url=safe_absolute_http_url(product.image_url),
            product_url=resolve_product_url(product.product_url, product.product_id, source_mall_id, self.settings),
            score=round(score, 6),
            score_percent=round(score * 100, 1),
            mall_id=source_mall_id,
            source_scores=hit.source_scores,
        )

    def _cache_key(
        self,
        request: SearchRequest,
        image_hash: str | None,
        normalized_query: str | None,
        search_query: str | None,
        inferred_categories: tuple[str, ...],
        text_weight: float,
        image_weight: float,
        limit: int,
        offset: int,
    ) -> str:
        return json.dumps(
            {
                "mall_id": request.mall_id,
                "normalized_query": normalized_query,
                "search_query": search_query,
                "inferred_categories": list(inferred_categories),
                "image_hash": image_hash,
                "limit": limit,
                "offset": offset,
                "category": request.category,
                "print_method": request.print_method,
                "material": request.material,
                "color": request.color,
                "min_price": request.min_price,
                "max_price": request.max_price,
                "quantity": request.quantity,
                "max_delivery_days": request.max_delivery_days,
                "text_weight": round(text_weight, 4),
                "image_weight": round(image_weight, 4),
                "engine": self.engine.name,
                "policy": self._cache_policy_token(request.mall_id),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _cache_policy_token(self, mall_id: str | None) -> str:
        normalized_mall_id = normalize_mall_id(mall_id, required=False) or ""
        with self._policy_fingerprint_lock:
            token = self._policy_fingerprint_tokens.get(normalized_mall_id)
            if token is not None:
                return token
        policy = cache_policy_mall_fingerprint(self.settings, normalized_mall_id or None)
        token = self._base_policy_token
        if policy is not None:
            token = cache_policy_fingerprint_digest(
                {
                    "version": CACHE_POLICY_VERSION,
                    "base_policy_token": self._base_policy_token,
                    "mall": policy,
                }
            )
        else:
            return token
        with self._policy_fingerprint_lock:
            return self._policy_fingerprint_tokens.setdefault(normalized_mall_id, token)

    def _log_search(
        self,
        request: SearchRequest,
        response: SearchResponse,
        image: ValidatedImage | None,
        normalized_query: str | None,
        inferred_categories: tuple[str, ...],
        cached: bool,
        execution_stats: SearchExecutionStats | None = None,
    ) -> None:
        result_items = response.top + response.items
        result_mall_ids = [item.mall_id for item in result_items]
        engine_search_attempts = execution_stats.search_attempts if execution_stats is not None else 0
        engine_adaptive_refetches = execution_stats.adaptive_refetches if execution_stats is not None else 0
        self.logger.write(
            {
                "type": "search",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mall_id": request.mall_id,
                "query_type": response.meta.query_type.value,
                "q": request.q,
                "normalized_query": normalized_query,
                "inferred_categories": list(inferred_categories),
                "image_hash": image.sha256 if image else None,
                "image_perceptual_hash": image.perceptual_hash if image else None,
                "image_size_bytes": image.size_bytes if image else None,
                "image_width": image.width if image else None,
                "image_height": image.height if image else None,
                "image_normalized": image.normalized if image else None,
                "image_quality_warnings": list(image.quality_warnings) if image else [],
                "limit": response.meta.limit,
                "offset": response.meta.offset,
                "has_more": response.meta.has_more,
                "next_offset": response.meta.next_offset,
                "elapsed_ms": response.meta.elapsed_ms,
                "cached": cached,
                "result_count": len(result_items),
                "engine_search_attempts": engine_search_attempts,
                "engine_adaptive_refetches": engine_adaptive_refetches,
                "engine_candidate_limits": list(execution_stats.candidate_limits) if execution_stats else [],
                "engine_raw_candidate_counts": list(execution_stats.raw_candidate_counts) if execution_stats else [],
                "engine_collapsed_candidate_counts": (
                    list(execution_stats.collapsed_candidate_counts) if execution_stats else []
                ),
                "engine_final_candidate_limit": execution_stats.final_candidate_limit if execution_stats else None,
                "engine_final_raw_candidate_count": execution_stats.final_raw_candidate_count if execution_stats else None,
                "engine_final_collapsed_candidate_count": (
                    execution_stats.final_collapsed_candidate_count if execution_stats else None
                ),
                "engine_max_candidate_limit": execution_stats.max_candidate_limit if execution_stats else None,
                "engine_required_result_count": execution_stats.required_count + 1 if execution_stats else None,
                "engine_underfilled_after_max_candidates": (
                    execution_stats.underfilled_after_max_candidates if execution_stats else False
                ),
                "result_mall_ids": result_mall_ids,
                "result_mall_id_mismatch_count": sum(
                    1 for mall_id in result_mall_ids if request.mall_id and mall_id != request.mall_id
                ),
                "top_product_ids": [item.product_id for item in response.top],
                "suggested_categories": response.suggested_categories,
                "filters": search_filter_log_values(request),
                "top_score_percent": response.top[0].score_percent if response.top else None,
                "top_source_scores": response.top[0].source_scores if response.top else {},
                "text_weight": response.meta.text_weight,
                "image_weight": response.meta.image_weight,
                "low_confidence": response.meta.low_confidence,
            }
        )

    def log_click(self, payload: ClickLogRequest | dict[str, Any]) -> None:
        click = payload if isinstance(payload, ClickLogRequest) else ClickLogRequest.model_validate(payload)
        self.logger.write(
            {
                "type": "click",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mall_id": click.mall_id,
                "product_id": click.product_id,
                "position": click.position,
                "query": click.query,
                "query_type": click.query_type.value if click.query_type else None,
                "score_percent": click.score_percent,
                "product_url": click.product_url,
            }
        )


def suggest_categories(
    hits: list[EngineHit],
    limit: int = 15,
    inferred_categories: tuple[str, ...] = (),
) -> list[str]:
    weighted = Counter()
    for rank, hit in enumerate(hits, start=1):
        category = hit.document.category
        if not category:
            continue
        weighted[category] += max(hit.score, 0.01) + (1 / (rank + 1))
    suggestions = []
    seen = set()
    for category in inferred_categories:
        category_text = str(category or "").strip()
        if not category_text or category_text in seen:
            continue
        suggestions.append(category_text)
        seen.add(category_text)
        if len(suggestions) >= limit:
            return suggestions
    for category, _ in weighted.most_common(limit):
        if category in seen:
            continue
        suggestions.append(category)
        seen.add(category)
        if len(suggestions) >= limit:
            break
    return suggestions


def result_score_to_unit_interval(score: float) -> float:
    value = max(0.0, float(score))
    if value <= 1.0:
        return value
    return value / (value + 1.0)


def apply_mall_visibility_policy(hits: list[EngineHit], settings: Settings, mall_id: str | None) -> list[EngineHit]:
    if not mall_id:
        return hits
    mall = settings.malls.get(mall_id)
    if mall is None:
        return hits
    excluded_ids = set(mall.excluded_product_ids)
    excluded_categories = set(mall.excluded_categories)
    if not excluded_ids and not excluded_categories:
        return hits
    return [
        hit
        for hit in hits
        if hit.document.product_id not in excluded_ids and hit.document.category not in excluded_categories
    ]


def enforce_response_mall_filter(hits: list[EngineHit], settings: Settings, mall_id: str | None) -> list[EngineHit]:
    if not should_strictly_filter_mall(settings, mall_id):
        return hits
    return [hit for hit in hits if hit.document.mall_id == mall_id]


def collapse_product_groups(hits: list[EngineHit]) -> list[EngineHit]:
    collapsed = []
    seen_groups = set()
    for hit in hits:
        group_id = str(hit.document.product_group_id or "").strip()
        if group_id:
            mall_key, product_key = product_identity_key(hit.document.mall_id, group_id)
            group_key = f"{mall_key}\0{product_key}"
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
        collapsed.append(hit)
    return collapsed


def should_require_display_image(engine: SearchEngine) -> bool:
    return getattr(engine, "name", "") == "marqo"


def filter_hits_with_display_image(hits: list[EngineHit]) -> list[EngineHit]:
    return [hit for hit in hits if safe_absolute_http_url(hit.document.image_url) is not None]


def mall_policy_overfetch(settings: Settings, mall_id: str | None) -> int:
    if not mall_id:
        return 1
    mall = settings.malls.get(mall_id)
    if mall and (mall.excluded_product_ids or mall.excluded_categories):
        return 2
    return 1


def response_candidate_limit(settings: Settings, mall_id: str | None, *, limit: int, offset: int) -> int:
    base = 3 + max(0, int(offset)) + max(1, int(limit)) + 1
    return min(
        base * mall_policy_overfetch(settings, mall_id) * PRODUCT_GROUP_COLLAPSE_OVERFETCH,
        MARQO_MAX_SEARCH_CANDIDATES,
    )


def adaptive_response_candidate_limit(
    current_limit: int,
    max_candidate_limit: int,
    *,
    collapsed_count: int,
    required_count: int,
) -> int:
    current = max(1, int(current_limit))
    maximum = max(current, int(max_candidate_limit or current))
    target_count = max(1, int(required_count) + 1)
    collapsed = max(1, int(collapsed_count))
    if collapsed >= target_count:
        return current
    estimated = (current * target_count + collapsed - 1) // collapsed
    return min(maximum, max(current + 1, current * 2, estimated))


def should_strictly_filter_mall(settings: Settings, mall_id: str | None) -> bool:
    return bool(mall_id) and settings.filter_by_mall_id


def response_policy_mall_id(settings: Settings, requested_mall_id: str | None, product_mall_id: str | None) -> str | None:
    if settings.filter_by_mall_id and product_mall_id:
        return product_mall_id
    return requested_mall_id or product_mall_id


def search_filter_log_values(request: SearchRequest) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "category": request.category,
            "print_method": request.print_method,
            "material": request.material,
            "color": request.color,
            "min_price": request.min_price,
            "max_price": request.max_price,
            "quantity": request.quantity,
            "max_delivery_days": request.max_delivery_days,
        }.items()
        if value is not None
    }


def cache_policy_base_fingerprint(settings: Settings) -> dict[str, Any]:
    return {
        "version": CACHE_POLICY_VERSION,
        "engine_backend": settings.engine_backend,
        "index_name": settings.index_name,
        "marqo_model": settings.marqo_model,
        "embedding_backend": settings.embedding_backend,
        "qwen_embedding_url": settings.qwen_embedding_url,
        "qwen_embedding_dimensions": settings.qwen_embedding_dimensions,
        "qwen_model": settings.qwen_model,
        "qwen_query_embedding_cache_path": (
            str(settings.qwen_query_embedding_cache_path) if settings.qwen_query_embedding_cache_path else None
        ),
        "filter_by_mall_id": settings.filter_by_mall_id,
        "product_url_template": settings.product_url_template,
        "low_score_threshold": settings.low_score_threshold,
        "category_suggestion_limit": settings.category_suggestion_limit,
        "text_auxiliary_weight": settings.text_auxiliary_weight,
        "text_auxiliary_candidate_multiplier": settings.text_auxiliary_candidate_multiplier,
        "query_synonyms": settings.query_synonyms,
    }


def cache_policy_mall_fingerprint(settings: Settings, mall_id: str | None) -> dict[str, Any] | None:
    mall = settings.malls.get(mall_id or "") if mall_id else None
    if mall is not None:
        return {
            "enabled": mall.enabled,
            "product_url_template": mall.product_url_template,
            "excluded_product_ids": list(mall.excluded_product_ids),
            "excluded_categories": list(mall.excluded_categories),
            "hide_prices": mall.hide_prices,
            "price_multiplier": mall.price_multiplier,
            "price_adjustment": mall.price_adjustment,
            "price_round_to": mall.price_round_to,
        }
    return None


def cache_policy_fingerprint(settings: Settings, mall_id: str | None) -> dict[str, Any]:
    data = cache_policy_base_fingerprint(settings)
    data["mall"] = cache_policy_mall_fingerprint(settings, mall_id)
    return data


def cache_policy_fingerprint_digest(policy: dict[str, Any]) -> str:
    payload = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_low_confidence(items: list[SearchResultItem], threshold: float) -> bool:
    if not items:
        return True
    return items[0].score < threshold


def low_confidence_notice(items: list[SearchResultItem], threshold: float) -> str | None:
    if not is_low_confidence(items, threshold):
        return None
    if not items:
        return "검색 결과가 없습니다. 다른 검색어를 추가하거나 더 선명한 이미지를 사용해 주세요."
    return "유사도가 낮은 결과입니다. 다른 검색어를 추가하거나 더 선명한 이미지를 사용해 주세요."


def response_category(category: str | None) -> str:
    text = str(category or "").strip()
    return text or DEFAULT_RESPONSE_CATEGORY


def resolve_product_url(product_url: str | None, product_id: str, mall_id: str | None, settings: Settings) -> str | None:
    values = product_url_format_values(product_id, mall_id)
    if product_url:
        try:
            formatted = product_url.format(**values)
        except (KeyError, ValueError):
            formatted = product_url
        absolute = absolute_product_url(formatted, mall_id, settings, values)
        if absolute and product_url_contains_product_id(absolute, product_id):
            return absolute
    mall = settings.malls.get(mall_id or "") if mall_id else None
    if mall and mall.product_url_template:
        try:
            fallback = safe_absolute_http_url(mall.product_url_template.format(**values))
        except (KeyError, ValueError):
            return None
        return fallback if product_url_contains_product_id(fallback, product_id) else None
    try:
        fallback = safe_absolute_http_url(settings.product_url_template.format(**values))
    except (KeyError, ValueError):
        return None
    return fallback if product_url_contains_product_id(fallback, product_id) else None


def product_url_format_values(product_id: str, mall_id: str | None) -> dict[str, str]:
    return {
        "product_id": quote(str(product_id or ""), safe=""),
        "mall_id": normalize_mall_id(mall_id, required=False) or "www",
    }


def absolute_product_url(product_url: str, mall_id: str | None, settings: Settings, values: dict[str, str]) -> str | None:
    product_url = str(product_url or "").strip()
    if not product_url:
        return None
    parsed = urlparse(product_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        if safe_absolute_http_url(product_url) is None:
            return None
        base = product_url_base(mall_id, settings, values)
        if not base:
            return None
        base_parsed = urlparse(base)
        if parsed.scheme.lower() == base_parsed.scheme.lower() and parsed.netloc.lower() == base_parsed.netloc.lower():
            return product_url
        return None
    if parsed.scheme or parsed.netloc:
        return None
    base = product_url_base(mall_id, settings, values)
    if not base:
        return None
    return safe_absolute_http_url(urljoin(base, product_url))


def product_url_base(mall_id: str | None, settings: Settings, values: dict[str, str]) -> str | None:
    mall = settings.malls.get(mall_id or "") if mall_id else None
    template = mall.product_url_template if mall and mall.product_url_template else settings.product_url_template
    try:
        example_url = template.format(**values)
    except (KeyError, ValueError):
        return None
    parsed = urlparse(example_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return safe_absolute_http_url(f"{parsed.scheme}://{parsed.netloc}/")


def resolve_product_price(price: float | None, mall_id: str | None, settings: Settings) -> float | None:
    if price is None:
        return None
    base_price = max(0.0, float(price))
    mall = settings.malls.get(mall_id or "") if mall_id else None
    if mall is None:
        return round(base_price, 2)
    if mall.hide_prices:
        return None
    adjusted = (base_price * mall.price_multiplier) + mall.price_adjustment
    if mall.price_round_to > 1:
        adjusted = round(adjusted / mall.price_round_to) * mall.price_round_to
    adjusted = max(0.0, adjusted)
    return round(adjusted, 2)
