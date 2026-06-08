from __future__ import annotations

import threading
import time

import pytest

from commerce_ai_search.concurrency import ImageSearchGate, ImageSearchQueueFull, SearchExecutionGate, SearchQueueFull
from commerce_ai_search.config import Settings, required_api_threadpool_tokens, validate_production_settings, validate_settings
from commerce_ai_search.rate_limit import RateLimitBucketStore, RedisRateLimiter, record_rate_limit_hit


def test_search_execution_gate_rejects_when_queue_timeout_expires():
    gate = SearchExecutionGate(max_concurrency=1, queue_timeout_seconds=0.01)

    with gate.slot():
        with pytest.raises(SearchQueueFull, match="search queue is full"):
            with gate.slot():
                pass

    status = gate.status()
    assert status["in_flight"] == 0
    assert status["acquired_events"] == 1
    assert status["queue_full_events"] == 1
    assert status["wait_events"] == 2


def test_image_search_gate_uses_specific_error_type():
    gate = ImageSearchGate(max_concurrency=1, queue_timeout_seconds=0.01)

    with gate.slot():
        with pytest.raises(ImageSearchQueueFull, match="image search queue is full"):
            with gate.slot():
                pass


def test_search_execution_gate_allows_waiting_request_when_slot_is_released():
    gate = SearchExecutionGate(max_concurrency=1, queue_timeout_seconds=0.5)
    acquired = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with gate.slot():
            acquired.set()
            release.wait(timeout=1)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert acquired.wait(timeout=1)
        wait_started = time.perf_counter()
        release.set()
        with gate.slot():
            pass
        assert time.perf_counter() - wait_started < 0.5
    finally:
        release.set()
        thread.join(timeout=1)

    assert gate.status()["queue_full_events"] == 0


def test_rate_limiter_blocks_at_limit_and_recovers_after_window():
    buckets: dict[str, list[float]] = {}

    assert record_rate_limit_hit(buckets, "client", limit=2, now=100.0, window_seconds=60) == (True, 1)
    assert record_rate_limit_hit(buckets, "client", limit=2, now=101.0, window_seconds=60) == (True, 2)
    assert record_rate_limit_hit(buckets, "client", limit=2, now=102.0, window_seconds=60) == (False, 2)
    assert record_rate_limit_hit(buckets, "client", limit=2, now=161.0, window_seconds=60) == (True, 2)


def test_rate_limit_bucket_store_prunes_old_overflow_without_dropping_protected_key():
    store = RateLimitBucketStore(max_buckets=2, prune_interval_seconds=0)
    store.hit("old-1", limit=10, now=0.0, window_seconds=60)
    store.hit("old-2", limit=10, now=1.0, window_seconds=60)
    store.hit("active", limit=10, now=120.0, window_seconds=60)

    assert "active" in store.buckets
    assert len(store.buckets) <= 2
    assert store.status()["pruned_buckets"] >= 1


def test_redis_rate_limiter_falls_back_and_skips_redis_during_backoff(monkeypatch):
    limiter = RedisRateLimiter(
        "redis://localhost:6379/0",
        "test",
        fallback_max_buckets=4,
        fallback_prune_interval_seconds=0,
        failure_backoff_seconds=30,
    )
    redis_calls = 0

    def fail_redis_hit(*_args, **_kwargs):
        nonlocal redis_calls
        redis_calls += 1
        raise TimeoutError("redis down")

    monkeypatch.setattr(limiter, "_redis_fixed_window_hit", fail_redis_hit)

    assert limiter.hit("client", limit=2, now=100.0, window_seconds=60) == (True, 1)
    assert redis_calls == 1
    first_status = limiter.status()
    assert first_status["fallback_events"] == 1
    assert first_status["fallback_active"] is True
    assert first_status["fallback_bucket_count"] == 1
    assert first_status["last_error"] == "redis down"
    assert first_status["redis_backoff_active"] is True
    assert first_status["redis_backoff_failure_events"] == 1

    assert limiter.hit("client", limit=2, now=101.0, window_seconds=60) == (True, 2)
    assert redis_calls == 1
    backoff_status = limiter.status()
    assert backoff_status["fallback_events"] == 2
    assert backoff_status["fallback_skipped_redis_events"] == 1
    assert backoff_status["redis_backoff_skipped_operations"] == 1
    assert backoff_status["redis_backoff_last_skipped_operation"] == "hit"


def test_redis_rate_limiter_fallback_is_per_process_when_multiple_replicas_lose_redis(monkeypatch):
    redis_calls = 0

    def make_failing_limiter(name: str) -> RedisRateLimiter:
        limiter = RedisRateLimiter(
            "redis://localhost:6379/0",
            f"test-{name}",
            fallback_max_buckets=4,
            fallback_prune_interval_seconds=0,
            failure_backoff_seconds=30,
        )

        def fail_redis_hit(*_args, **_kwargs):
            nonlocal redis_calls
            redis_calls += 1
            raise TimeoutError("redis down")

        monkeypatch.setattr(limiter, "_redis_fixed_window_hit", fail_redis_hit)
        return limiter

    replica_a = make_failing_limiter("a")
    replica_b = make_failing_limiter("b")

    first_hits = [
        replica_a.hit("client", limit=1, now=100.0, window_seconds=60),
        replica_b.hit("client", limit=1, now=100.0, window_seconds=60),
    ]
    second_hits = [
        replica_a.hit("client", limit=1, now=101.0, window_seconds=60),
        replica_b.hit("client", limit=1, now=101.0, window_seconds=60),
    ]

    assert first_hits == [(True, 1), (True, 1)]
    assert second_hits == [(False, 1), (False, 1)]
    assert redis_calls == 2
    assert replica_a.status()["fallback_skipped_redis_events"] == 1
    assert replica_b.status()["fallback_skipped_redis_events"] == 1


def test_required_api_threadpool_tokens_tracks_search_and_image_concurrency():
    assert required_api_threadpool_tokens(search_max_concurrency=64, image_search_max_concurrency=8) == 80
    assert required_api_threadpool_tokens(search_max_concurrency=0, image_search_max_concurrency=0) == 8


def test_settings_reject_mixed_weight_sum_overflow():
    settings = Settings(mixed_text_weight=1e308, mixed_image_weight=1e308)

    with pytest.raises(
        ValueError,
        match="HAEORUM_MIXED_TEXT_WEIGHT and HAEORUM_MIXED_IMAGE_WEIGHT sum must be finite",
    ):
        validate_settings(settings)


def test_production_settings_reject_underprovisioned_api_threadpool():
    settings = Settings(
        environment="production",
        engine_backend="marqo",
        embedding_backend="gemini",
        qwen_embedding_proxy_api_key="strong-embedding-proxy-key",
        admin_api_key="strong-admin-key-123",
        cors_origins=("https://shop.example.com",),
        product_url_template="https://{mall_id}.example.com/product/{product_id}",
        search_max_concurrency=64,
        image_search_max_concurrency=8,
        api_threadpool_tokens=79,
        sync_interval_seconds=3600,
        filter_by_mall_id=True,
        redis_url="redis://cache.example.com:6379/0",
        mssql_connection_string=(
            "Server=db.example.com;"
            "Database=shop;"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
            "ApplicationIntent=ReadOnly;"
        ),
    )

    with pytest.raises(ValueError, match="HAEORUM_API_THREADPOOL_TOKENS must be at least 80"):
        validate_production_settings(settings)
