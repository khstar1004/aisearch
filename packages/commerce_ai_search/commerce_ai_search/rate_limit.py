from __future__ import annotations

import math
import threading
import time

from .redis_guard import RedisFailureBackoff


DEFAULT_MAX_RATE_LIMIT_BUCKETS = 10000
DEFAULT_RATE_LIMIT_PRUNE_INTERVAL_SECONDS = 1.0

REDIS_RATE_LIMIT_HIT_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
redis.call("EXPIRE", KEYS[1], ARGV[1])
return current
"""


def record_rate_limit_hit(
    buckets: dict[str, list[float]],
    key: str,
    limit: int,
    now: float | None = None,
    window_seconds: int = 60,
    max_buckets: int = DEFAULT_MAX_RATE_LIMIT_BUCKETS,
) -> tuple[bool, int]:
    if limit <= 0:
        return True, 0
    current = time.time() if now is None else now
    window_start = current - window_seconds
    recent = buckets.get(key)
    if recent is None:
        recent = []
        buckets[key] = recent
    else:
        prune_expired_timestamps_in_place(recent, window_start)
    if len(recent) >= limit:
        prune_rate_limit_buckets_if_needed(
            buckets,
            now=current,
            window_seconds=window_seconds,
            max_buckets=max_buckets,
            protected_key=key,
        )
        return False, len(recent)
    recent.append(current)
    prune_rate_limit_buckets_if_needed(
        buckets,
        now=current,
        window_seconds=window_seconds,
        max_buckets=max_buckets,
        protected_key=key,
    )
    return True, len(recent)


def prune_expired_timestamps_in_place(stamps: list[float], window_start: float) -> int:
    keep_index = 0
    original_count = len(stamps)
    for stamp in stamps:
        if stamp >= window_start:
            stamps[keep_index] = stamp
            keep_index += 1
    if keep_index < original_count:
        del stamps[keep_index:]
    return original_count - keep_index


def prune_rate_limit_buckets_if_needed(
    buckets: dict[str, list[float]],
    now: float,
    window_seconds: int,
    max_buckets: int,
    protected_key: str | None = None,
) -> int:
    bucket_limit = max(int(max_buckets), 1)
    if len(buckets) <= bucket_limit:
        return 0
    return prune_rate_limit_buckets(
        buckets,
        now=now,
        window_seconds=window_seconds,
        max_buckets=bucket_limit,
        protected_key=protected_key,
    )


def prune_rate_limit_buckets(
    buckets: dict[str, list[float]],
    now: float | None = None,
    window_seconds: int = 60,
    max_buckets: int = DEFAULT_MAX_RATE_LIMIT_BUCKETS,
    protected_key: str | None = None,
) -> int:
    if not buckets:
        return 0
    current = time.time() if now is None else now
    window_start = current - window_seconds
    removed = 0
    for bucket_key, stamps in list(buckets.items()):
        recent = [stamp for stamp in stamps if stamp >= window_start]
        if recent:
            buckets[bucket_key] = recent
        else:
            buckets.pop(bucket_key, None)
            removed += 1
    bucket_limit = max(int(max_buckets), 1)
    overflow = len(buckets) - bucket_limit
    if overflow <= 0:
        return removed
    candidates = [
        (max(stamps), bucket_key)
        for bucket_key, stamps in buckets.items()
        if bucket_key != protected_key and stamps
    ]
    for _, bucket_key in sorted(candidates)[:overflow]:
        if bucket_key in buckets:
            buckets.pop(bucket_key, None)
            removed += 1
    return removed


class RateLimitBucketStore:
    def __init__(
        self,
        max_buckets: int = DEFAULT_MAX_RATE_LIMIT_BUCKETS,
        prune_interval_seconds: float = DEFAULT_RATE_LIMIT_PRUNE_INTERVAL_SECONDS,
    ) -> None:
        self.buckets: dict[str, list[float]] = {}
        self.max_buckets = max(int(max_buckets), 1)
        self.prune_interval_seconds = max(float(prune_interval_seconds), 0.0)
        self._next_prune_at = 0.0
        self._last_pruned_at = 0.0
        self._pruned_buckets = 0

    def hit(
        self,
        key: str,
        limit: int,
        now: float | None = None,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        current = time.time() if now is None else now
        self._prune_if_due(current, window_seconds, protected_key=key)
        before_bucket_count = len(self.buckets)
        had_key_before = key in self.buckets
        result = record_rate_limit_hit(
            self.buckets,
            key,
            limit,
            now=current,
            window_seconds=window_seconds,
            max_buckets=self.max_buckets,
        )
        expected_bucket_count_without_hit_pruning = before_bucket_count
        if limit > 0 and not had_key_before and key in self.buckets:
            expected_bucket_count_without_hit_pruning += 1
        removed = max(0, expected_bucket_count_without_hit_pruning - len(self.buckets))
        self._pruned_buckets += removed
        return result

    def prune(
        self,
        now: float | None = None,
        window_seconds: int = 60,
        protected_key: str | None = None,
    ) -> int:
        current = time.time() if now is None else now
        removed = prune_rate_limit_buckets(
            self.buckets,
            now=current,
            window_seconds=window_seconds,
            max_buckets=self.max_buckets,
            protected_key=protected_key,
        )
        self._last_pruned_at = current
        self._pruned_buckets += removed
        return removed

    def status(self) -> dict[str, object]:
        return {
            "bucket_count": len(self.buckets),
            "max_buckets": self.max_buckets,
            "prune_interval_seconds": self.prune_interval_seconds,
            "last_pruned_at": self._last_pruned_at,
            "pruned_buckets": self._pruned_buckets,
        }

    def _prune_if_due(self, now: float, window_seconds: int, protected_key: str | None = None) -> None:
        if self.prune_interval_seconds <= 0:
            return
        if now < self._next_prune_at:
            return
        self.prune(now=now, window_seconds=window_seconds, protected_key=protected_key)
        self._next_prune_at = now + self.prune_interval_seconds


class RedisRateLimiter:
    def __init__(
        self,
        redis_url: str,
        key_prefix: str,
        fallback_max_buckets: int = DEFAULT_MAX_RATE_LIMIT_BUCKETS,
        fallback_prune_interval_seconds: float = DEFAULT_RATE_LIMIT_PRUNE_INTERVAL_SECONDS,
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
        self.fallback_max_buckets = max(int(fallback_max_buckets), 1)
        self.fallback_prune_interval_seconds = max(float(fallback_prune_interval_seconds), 0.0)
        self._redis_backoff = RedisFailureBackoff(self.redis_failure_backoff_seconds)
        self._fallback_store = RateLimitBucketStore(
            self.fallback_max_buckets,
            prune_interval_seconds=self.fallback_prune_interval_seconds,
        )
        self._fallback_lock = threading.RLock()
        self._fallback_events = 0
        self._fallback_skipped_redis_events = 0
        self._fallback_last_error: str | None = None

    def hit(
        self,
        key: str,
        limit: int,
        now: float | None = None,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        current = time.time() if now is None else now
        window = int(math.floor(current / window_seconds))
        redis_key = f"{self.key_prefix}:rate-limit:{key}:{window}"
        if not self._redis_allowed("hit"):
            return self._fallback_hit(
                key,
                limit,
                now=current,
                window_seconds=window_seconds,
                skipped_redis=True,
            )
        try:
            count = self._redis_fixed_window_hit(redis_key, window_seconds * 2)
            self._record_redis_success()
            return count <= limit, count
        except Exception as exc:
            self._record_redis_failure("hit")
            return self._fallback_hit(key, limit, now=current, window_seconds=window_seconds, error=exc)

    def _redis_fixed_window_hit(self, redis_key: str, ttl_seconds: int | float) -> int:
        ttl = max(int(ttl_seconds), 1)
        eval_method = getattr(self.client, "eval", None)
        if callable(eval_method):
            return int(eval_method(REDIS_RATE_LIMIT_HIT_SCRIPT, 1, redis_key, ttl))
        pipeline_method = getattr(self.client, "pipeline", None)
        if callable(pipeline_method):
            pipe = pipeline_method()
            pipe.incr(redis_key)
            pipe.expire(redis_key, ttl)
            result = pipe.execute()
            return int(result[0])
        count = int(self.client.incr(redis_key))
        self.client.expire(redis_key, ttl)
        return count

    def _fallback_hit(
        self,
        key: str,
        limit: int,
        now: float,
        window_seconds: int,
        error: Exception | None = None,
        skipped_redis: bool = False,
    ) -> tuple[bool, int]:
        self._ensure_fallback_state()
        with self._fallback_lock:
            self._fallback_events += 1
            if skipped_redis:
                self._fallback_skipped_redis_events += 1
            if error is not None:
                self._fallback_last_error = str(error)
            return self._fallback_store.hit(
                key,
                limit,
                now=now,
                window_seconds=window_seconds,
            )

    def status(self) -> dict[str, object]:
        self._ensure_fallback_state()
        with self._fallback_lock:
            fallback_status = self._fallback_store.status()
            guard_status = self._redis_guard().status()
            return {
                "backend": "redis",
                "redis_enabled": True,
                "redis_socket_timeout_seconds": getattr(self, "redis_socket_timeout_seconds", None),
                "redis_socket_connect_timeout_seconds": getattr(self, "redis_socket_connect_timeout_seconds", None),
                "fallback_events": self._fallback_events,
                "fallback_active": self._fallback_events > 0,
                "fallback_skipped_redis_events": self._fallback_skipped_redis_events,
                "fallback_bucket_count": fallback_status["bucket_count"],
                "fallback_max_buckets": self.fallback_max_buckets,
                "fallback_prune_interval_seconds": fallback_status["prune_interval_seconds"],
                "fallback_pruned_buckets": fallback_status["pruned_buckets"],
                "last_error": self._fallback_last_error,
                **guard_status,
            }

    def _ensure_fallback_state(self) -> None:
        if not hasattr(self, "fallback_max_buckets"):
            self.fallback_max_buckets = DEFAULT_MAX_RATE_LIMIT_BUCKETS
        if not hasattr(self, "fallback_prune_interval_seconds"):
            self.fallback_prune_interval_seconds = DEFAULT_RATE_LIMIT_PRUNE_INTERVAL_SECONDS
        if not hasattr(self, "redis_failure_backoff_seconds"):
            self.redis_failure_backoff_seconds = 0.0
        if not hasattr(self, "_fallback_store"):
            self._fallback_store = RateLimitBucketStore(
                self.fallback_max_buckets,
                prune_interval_seconds=self.fallback_prune_interval_seconds,
            )
        if not hasattr(self, "_fallback_lock"):
            self._fallback_lock = threading.RLock()
        if not hasattr(self, "_fallback_events"):
            self._fallback_events = 0
        if not hasattr(self, "_fallback_skipped_redis_events"):
            self._fallback_skipped_redis_events = 0
        if not hasattr(self, "_fallback_last_error"):
            self._fallback_last_error = None

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


def make_rate_limiter(
    redis_url: str | None,
    key_prefix: str,
    fallback_max_buckets: int = DEFAULT_MAX_RATE_LIMIT_BUCKETS,
    fallback_prune_interval_seconds: float = DEFAULT_RATE_LIMIT_PRUNE_INTERVAL_SECONDS,
    socket_timeout_seconds: float = 0.5,
    socket_connect_timeout_seconds: float = 0.5,
    failure_backoff_seconds: float = 2.0,
) -> RedisRateLimiter | None:
    if not redis_url:
        return None
    return RedisRateLimiter(
        redis_url,
        key_prefix,
        fallback_max_buckets=fallback_max_buckets,
        fallback_prune_interval_seconds=fallback_prune_interval_seconds,
        socket_timeout_seconds=socket_timeout_seconds,
        socket_connect_timeout_seconds=socket_connect_timeout_seconds,
        failure_backoff_seconds=failure_backoff_seconds,
    )
