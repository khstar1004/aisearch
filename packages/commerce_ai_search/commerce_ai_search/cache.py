from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import OrderedDict
from typing import Protocol

from .config import Settings
from .models import SearchResponse
from .redis_guard import RedisFailureBackoff


class SearchCache(Protocol):
    def get(self, key: str) -> SearchResponse | None:
        ...

    def set(self, key: str, response: SearchResponse) -> None:
        ...

    def clear(self) -> dict[str, object]:
        ...

    def status(self) -> dict[str, object]:
        ...


REDIS_MISS_LOCK_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class MemorySearchCache:
    def __init__(self, ttl_seconds: int, max_entries: int = 10000):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max(int(max_entries), 1)
        self.evictions = 0
        self.lock_wait_events = 0
        self.lock_wait_timeouts = 0
        self.lock_total_wait_seconds = 0.0
        self.lock_max_wait_seconds = 0.0
        self._cache: OrderedDict[str, tuple[float, SearchResponse]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> SearchResponse | None:
        if self.ttl_seconds <= 0:
            return None
        with self._lock:
            cached = self._cache.get(key)
            if not cached:
                return None
            expires_at, response = cached
            if expires_at < time.time():
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return response.model_copy(deep=True)

    def set(self, key: str, response: SearchResponse) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            self._cache[key] = (time.time() + self.ttl_seconds, response.model_copy(deep=True))
            self._cache.move_to_end(key)
            self._evict_over_limit()

    def _evict_over_limit(self) -> None:
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
            self.evictions += 1

    def clear(self) -> dict[str, object]:
        with self._lock:
            cleared = len(self._cache)
            self._cache.clear()
        return {
            "ok": True,
            "backend": "memory",
            "redis_enabled": False,
            "cleared": cleared,
        }

    def status(self) -> dict[str, object]:
        now = time.time()
        with self._lock:
            expired = [key for key, (expires_at, _) in self._cache.items() if expires_at < now]
            for key in expired:
                self._cache.pop(key, None)
            entry_count = len(self._cache)
            evictions = self.evictions
            lock_wait_events = self.lock_wait_events
            lock_wait_timeouts = self.lock_wait_timeouts
            lock_total_wait_seconds = self.lock_total_wait_seconds
            lock_max_wait_seconds = self.lock_max_wait_seconds
        lock_total_wait_ms = round(lock_total_wait_seconds * 1000, 3)
        return {
            "backend": "memory",
            "redis_enabled": False,
            "ttl_seconds": self.ttl_seconds,
            "max_entries": self.max_entries,
            "entry_count": entry_count,
            "evictions": evictions,
            "error_count": 0,
            "clear_errors": 0,
            "lock_wait_events": lock_wait_events,
            "lock_wait_timeouts": lock_wait_timeouts,
            "lock_total_wait_ms": lock_total_wait_ms,
            "lock_avg_wait_ms": round(lock_total_wait_ms / lock_wait_events, 3) if lock_wait_events else 0.0,
            "lock_max_wait_ms": round(lock_max_wait_seconds * 1000, 3),
            "last_error": None,
        }

    def record_miss_wait(self, completed: bool, elapsed_seconds: float) -> None:
        elapsed_seconds = max(float(elapsed_seconds or 0.0), 0.0)
        with self._lock:
            self.lock_wait_events += 1
            self.lock_total_wait_seconds += elapsed_seconds
            self.lock_max_wait_seconds = max(self.lock_max_wait_seconds, elapsed_seconds)
            if not completed:
                self.lock_wait_timeouts += 1


class RedisSearchCache:
    def __init__(
        self,
        redis_url: str,
        key_prefix: str,
        ttl_seconds: int,
        socket_timeout_seconds: float = 0.5,
        socket_connect_timeout_seconds: float = 0.5,
        failure_backoff_seconds: float = 2.0,
    ):
        try:
            import redis
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("redis is required when HAEORUM_REDIS_URL is set") from exc
        self.redis_socket_timeout_seconds = max(float(socket_timeout_seconds or 0.0), 0.001)
        self.redis_socket_connect_timeout_seconds = max(float(socket_connect_timeout_seconds or 0.0), 0.001)
        self.redis_failure_backoff_seconds = max(float(failure_backoff_seconds or 0.0), 0.0)
        self.client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=self.redis_socket_timeout_seconds,
            socket_connect_timeout=self.redis_socket_connect_timeout_seconds,
            socket_keepalive=True,
            health_check_interval=30,
            retry_on_timeout=False,
        )
        self.key_prefix = key_prefix.rstrip(":")
        self.ttl_seconds = ttl_seconds
        self._redis_backoff = RedisFailureBackoff(self.redis_failure_backoff_seconds)
        self.get_errors = 0
        self.set_errors = 0
        self.decode_errors = 0
        self.delete_errors = 0
        self.clear_errors = 0
        self.lock_claims = 0
        self.lock_contention_events = 0
        self.lock_errors = 0
        self.lock_release_errors = 0
        self.lock_wait_events = 0
        self.lock_wait_timeouts = 0
        self.lock_total_wait_seconds = 0.0
        self.lock_max_wait_seconds = 0.0
        self.last_error: str | None = None
        self.last_error_operation: str | None = None
        self._status_lock = threading.RLock()
        self._miss_lock_tokens: dict[str, str] = {}
        self._miss_lock_token_lock = threading.RLock()

    def get(self, key: str) -> SearchResponse | None:
        if self.ttl_seconds <= 0:
            return None
        if not self._redis_allowed("get"):
            return None
        cache_key = self._key(key)
        try:
            raw = self.client.get(cache_key)
        except Exception as exc:
            self._record_error("get", exc)
            return None
        self._record_redis_success()
        if not raw:
            return None
        try:
            return SearchResponse.model_validate_json(raw)
        except Exception as exc:
            self._record_error("decode", exc)
            try:
                self.client.delete(cache_key)
                self._record_redis_success()
            except Exception as delete_exc:
                self._record_error("delete", delete_exc)
            return None

    def set(self, key: str, response: SearchResponse) -> None:
        if self.ttl_seconds <= 0:
            return
        if not self._redis_allowed("set"):
            return
        try:
            self.client.setex(self._key(key), self.ttl_seconds, response.model_dump_json())
        except Exception as exc:
            self._record_error("set", exc)
            return
        self._record_redis_success()

    def claim_miss_owner(self, key: str, lock_seconds: float) -> bool:
        if self.ttl_seconds <= 0 or lock_seconds <= 0:
            return True
        if not self._redis_allowed("lock_claim"):
            return True
        token = uuid.uuid4().hex
        lock_key = self._miss_lock_key(key)
        lock_ms = max(int(float(lock_seconds) * 1000), 1)
        try:
            claimed = bool(self.client.set(lock_key, token, nx=True, px=lock_ms))
        except Exception as exc:
            self._record_error("lock_claim", exc)
            return True
        self._record_redis_success()
        if not claimed:
            self._increment_counter("lock_contention_events")
            return False
        self._increment_counter("lock_claims")
        with self._token_lock():
            self._miss_lock_tokens[key] = token
        return True

    def release_miss_owner(self, key: str) -> None:
        with self._token_lock():
            token = self._miss_lock_tokens.pop(key, None)
        if not token:
            return
        if not self._redis_allowed("lock_release"):
            return
        lock_key = self._miss_lock_key(key)
        try:
            eval_method = getattr(self.client, "eval", None)
            if callable(eval_method):
                eval_method(REDIS_MISS_LOCK_RELEASE_SCRIPT, 1, lock_key, token)
                self._record_redis_success()
                return
            current = self.client.get(lock_key)
            if current == token:
                self.client.delete(lock_key)
            self._record_redis_success()
        except Exception as exc:
            self._record_error("lock_release", exc)

    def record_miss_wait(self, completed: bool, elapsed_seconds: float) -> None:
        elapsed_seconds = max(float(elapsed_seconds or 0.0), 0.0)
        with self._counter_lock():
            self.lock_wait_events = int(getattr(self, "lock_wait_events", 0)) + 1
            self.lock_total_wait_seconds = float(getattr(self, "lock_total_wait_seconds", 0.0)) + elapsed_seconds
            self.lock_max_wait_seconds = max(float(getattr(self, "lock_max_wait_seconds", 0.0)), elapsed_seconds)
            if not completed:
                self.lock_wait_timeouts = int(getattr(self, "lock_wait_timeouts", 0)) + 1

    def clear(self) -> dict[str, object]:
        pattern = f"{self.key_prefix}:search-cache:*"
        scanned = 0
        cleared = 0
        batch: list[str] = []
        if not self._redis_allowed("clear"):
            exc = RuntimeError("redis backoff active")
            self._record_error("clear", exc)
            return {
                "ok": False,
                "backend": "redis",
                "redis_enabled": True,
                "pattern": pattern,
                "scanned": scanned,
                "cleared": cleared,
                "error": str(exc),
            }
        try:
            for key in self.client.scan_iter(match=pattern, count=1000):
                scanned += 1
                batch.append(str(key))
                if len(batch) >= 500:
                    cleared += self._delete_batch(batch)
                    batch = []
            if batch:
                cleared += self._delete_batch(batch)
            return {
                "ok": True,
                "backend": "redis",
                "redis_enabled": True,
                "pattern": pattern,
                "scanned": scanned,
                "cleared": cleared,
            }
        except Exception as exc:
            self._record_error("clear", exc)
            return {
                "ok": False,
                "backend": "redis",
                "redis_enabled": True,
                "pattern": pattern,
                "scanned": scanned,
                "cleared": cleared,
                "error": str(exc),
            }

    def _delete_batch(self, keys: list[str]) -> int:
        if not keys:
            return 0
        deleted = self.client.delete(*keys)
        self._record_redis_success()
        return int(deleted) if deleted is not None else len(keys)

    def _key(self, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}:search-cache:{digest}"

    def _miss_lock_key(self, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}:search-cache-lock:{digest}"

    def _token_lock(self) -> threading.RLock:
        lock = getattr(self, "_miss_lock_token_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._miss_lock_token_lock = lock
        if not hasattr(self, "_miss_lock_tokens"):
            self._miss_lock_tokens = {}
        return lock

    def _record_error(self, operation: str, exc: Exception) -> None:
        counter_name = {
            "get": "get_errors",
            "set": "set_errors",
            "decode": "decode_errors",
            "delete": "delete_errors",
            "clear": "clear_errors",
            "lock_claim": "lock_errors",
            "lock_release": "lock_release_errors",
        }.get(operation)
        self._record_redis_failure(operation)
        with self._counter_lock():
            if counter_name:
                setattr(self, counter_name, int(getattr(self, counter_name, 0)) + 1)
            self.last_error = str(exc)
            self.last_error_operation = operation

    def _increment_counter(self, name: str) -> None:
        with self._counter_lock():
            setattr(self, name, int(getattr(self, name, 0)) + 1)

    def _counter_lock(self) -> threading.RLock:
        lock = getattr(self, "_status_lock", None)
        if lock is None:
            lock = self.__dict__.setdefault("_status_lock", threading.RLock())
        return lock

    def _redis_guard(self) -> RedisFailureBackoff:
        guard = getattr(self, "_redis_backoff", None)
        if guard is None:
            guard = RedisFailureBackoff(float(getattr(self, "redis_failure_backoff_seconds", 0.0) or 0.0))
            self._redis_backoff = guard
        return guard

    def _redis_allowed(self, operation: str) -> bool:
        return self._redis_guard().allow(operation)

    def _record_redis_success(self) -> None:
        self._redis_guard().record_success()

    def _record_redis_failure(self, operation: str) -> None:
        self._redis_guard().record_failure(operation)

    def status(self) -> dict[str, object]:
        with self._counter_lock():
            get_errors = int(getattr(self, "get_errors", 0))
            set_errors = int(getattr(self, "set_errors", 0))
            decode_errors = int(getattr(self, "decode_errors", 0))
            delete_errors = int(getattr(self, "delete_errors", 0))
            clear_errors = int(getattr(self, "clear_errors", 0))
            lock_claims = int(getattr(self, "lock_claims", 0))
            lock_contention_events = int(getattr(self, "lock_contention_events", 0))
            lock_errors = int(getattr(self, "lock_errors", 0))
            lock_release_errors = int(getattr(self, "lock_release_errors", 0))
            lock_wait_events = int(getattr(self, "lock_wait_events", 0))
            lock_wait_timeouts = int(getattr(self, "lock_wait_timeouts", 0))
            lock_total_wait_seconds = float(getattr(self, "lock_total_wait_seconds", 0.0))
            lock_max_wait_seconds = float(getattr(self, "lock_max_wait_seconds", 0.0))
            last_error = getattr(self, "last_error", None)
            last_error_operation = getattr(self, "last_error_operation", None)
        error_count = get_errors + set_errors + decode_errors + delete_errors + clear_errors + lock_errors + lock_release_errors
        lock_total_wait_ms = round(lock_total_wait_seconds * 1000, 3)
        guard_status = self._redis_guard().status()
        return {
            "backend": "redis",
            "redis_enabled": True,
            "ttl_seconds": self.ttl_seconds,
            "max_entries": None,
            "redis_socket_timeout_seconds": getattr(self, "redis_socket_timeout_seconds", None),
            "redis_socket_connect_timeout_seconds": getattr(self, "redis_socket_connect_timeout_seconds", None),
            "evictions": 0,
            "error_count": error_count,
            "get_errors": get_errors,
            "set_errors": set_errors,
            "decode_errors": decode_errors,
            "delete_errors": delete_errors,
            "clear_errors": clear_errors,
            "lock_claims": lock_claims,
            "lock_contention_events": lock_contention_events,
            "lock_errors": lock_errors,
            "lock_release_errors": lock_release_errors,
            "lock_wait_events": lock_wait_events,
            "lock_wait_timeouts": lock_wait_timeouts,
            "lock_total_wait_ms": lock_total_wait_ms,
            "lock_avg_wait_ms": round(lock_total_wait_ms / lock_wait_events, 3) if lock_wait_events else 0.0,
            "lock_max_wait_ms": round(lock_max_wait_seconds * 1000, 3),
            "last_error": last_error,
            "last_error_operation": last_error_operation,
            **guard_status,
        }


def make_search_cache(settings: Settings) -> SearchCache:
    if settings.redis_url:
        return RedisSearchCache(
            settings.redis_url,
            settings.redis_key_prefix,
            settings.cache_ttl_seconds,
            socket_timeout_seconds=settings.redis_socket_timeout_seconds,
            socket_connect_timeout_seconds=settings.redis_socket_connect_timeout_seconds,
            failure_backoff_seconds=settings.redis_failure_backoff_seconds,
        )
    return MemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries)
