from __future__ import annotations

import os
import platform
import shutil
import sys
import time
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings, required_api_threadpool_tokens
from .engine import SearchEngine
from .instance import api_instance_id
from .search_service import SearchLogger
from .sync import SyncService


PROCESS_STARTED_AT = time.time()


def build_admin_metrics(
    settings: Settings,
    engine: SearchEngine,
    search_logger: SearchLogger,
    error_logger: SearchLogger,
    sync_service: SyncService,
    limit: int = 1000,
    rate_limiter: Any | None = None,
    memory_rate_limit_bucket_count: int | None = None,
    search_cache: Any | None = None,
    search_singleflight: Any | None = None,
    image_validation_singleflight: Any | None = None,
    search_execution_gate: Any | None = None,
    image_search_gate: Any | None = None,
    api_threadpool_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    search_entries = search_logger.tail(limit)
    error_entries = error_logger.tail(limit)
    sync_entries = sync_service.logger.tail(limit)
    sync_status = sync_service.current_status()
    engine_health = safe_engine_health(settings, engine)
    sync_summary = {**sync_status.model_dump(mode="json"), "events": summarize_sync_entries(sync_entries)}
    search_summary = summarize_search_entries(search_entries)
    error_summary = summarize_error_entries(error_entries)
    log_summary = {
        "search": {**file_summary(settings.search_log_path), **search_logger.status()},
        "error": {**file_summary(settings.error_log_path), **error_logger.status()},
        "sync": file_summary(settings.sync_log_path),
    }
    disk = disk_summary(settings.search_log_path.parent)
    process = process_summary()
    system = system_summary()
    rate_limit_summary = rate_limiter_summary(rate_limiter, settings, memory_bucket_count=memory_rate_limit_bucket_count)
    cache_summary = search_cache_summary(search_cache, settings)
    singleflight_summary = search_singleflight_summary(search_singleflight, settings)
    image_validation_summary = image_validation_singleflight_summary(
        image_validation_singleflight if image_validation_singleflight is not None else search_singleflight,
        settings,
    )
    search_queue_summary = search_execution_queue_summary(search_execution_gate, settings)
    image_queue_summary = image_search_queue_summary(image_search_gate, settings)
    api_threadpool = api_threadpool_summary(api_threadpool_status, settings)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": engine_health,
        "sync": sync_summary,
        "search": search_summary,
        "errors": error_summary,
        "rate_limit": rate_limit_summary,
        "cache": cache_summary,
        "singleflight": singleflight_summary,
        "image_validation": image_validation_summary,
        "search_queue": search_queue_summary,
        "image_queue": image_queue_summary,
        "api_threadpool": api_threadpool,
        "logs": log_summary,
        "disk": disk,
        "process": process,
        "system": system,
        "alerts": build_operational_alerts(
            engine_health,
            sync_summary,
            search_summary,
            error_summary,
            rate_limit_summary,
            cache_summary,
            singleflight_summary,
            search_queue_summary,
            image_queue_summary,
            log_summary,
            disk,
            system,
            api_threadpool,
            image_validation_summary,
        ),
    }


def safe_engine_health(settings: Settings, engine: SearchEngine) -> dict[str, Any]:
    try:
        raw_health = engine.health()
        engine_health = raw_health if isinstance(raw_health, dict) else {"message": str(raw_health)}
    except Exception as exc:
        engine_health = {
            "engine": getattr(engine, "name", settings.engine_backend),
            "ready": False,
            "ok": False,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }
    if "ok" not in engine_health:
        engine_health["ok"] = bool(engine_health.get("ready", False))
    engine_health.update(
        {
            "backend": settings.engine_backend,
            "index": settings.index_name,
            "model": engine_health.get("model") or engine_health.get("marqo_model") or settings.marqo_model,
            "marqo_model": engine_health.get("marqo_model") or engine_health.get("model") or settings.marqo_model,
            "embedding_backend": engine_health.get("embedding_backend") or settings.embedding_backend,
        }
    )
    if str(engine_health.get("embedding_backend") or "").lower() == "gemini":
        engine_health.update(
            {
                "gemini_model": engine_health.get("gemini_model") or settings.qwen_model,
                "gemini_embedding_dimensions": (
                    engine_health.get("gemini_embedding_dimensions") or settings.qwen_embedding_dimensions
                ),
            }
        )
    elif str(engine_health.get("embedding_backend") or "").lower() == "qwen":
        engine_health.update(
            {
                "qwen_model": engine_health.get("qwen_model") or settings.qwen_model,
                "qwen_embedding_dimensions": (
                    engine_health.get("qwen_embedding_dimensions") or settings.qwen_embedding_dimensions
                ),
            }
        )
    return engine_health


def build_operational_alerts(
    engine: dict[str, Any],
    sync: dict[str, Any],
    search: dict[str, Any],
    errors: dict[str, Any],
    rate_limit: dict[str, Any],
    cache: dict[str, Any],
    singleflight: dict[str, Any],
    search_queue: dict[str, Any],
    image_queue: dict[str, Any],
    logs: dict[str, Any],
    disk: dict[str, Any],
    system: dict[str, Any],
    api_threadpool: dict[str, Any] | None = None,
    image_validation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if engine.get("ok") is not True:
        alerts.append(alert("critical", "engine_unhealthy", "search engine health check is failing", engine.get("ok")))
    gemini_status = engine.get("gemini") if isinstance(engine.get("gemini"), dict) else {}
    if (
        str(engine.get("embedding_backend") or "").lower() == "gemini"
        and isinstance(gemini_status, dict)
        and gemini_status.get("proxy_auth_configured") is False
    ):
        alerts.append(
            alert(
                "critical",
                "gemini_proxy_auth_missing",
                "Gemini embedding proxy is reachable without the internal shared secret",
                False,
            )
        )
    transport = engine.get("transport") or {}
    if isinstance(transport, dict):
        stale_reconnects = sum(
            int((stats or {}).get("stale_reconnects", 0) or 0)
            for stats in transport.values()
            if isinstance(stats, dict)
        )
        error_responses = sum(
            int((stats or {}).get("error_responses", 0) or 0)
            for stats in transport.values()
            if isinstance(stats, dict)
        )
        connection_close_responses = sum(
            int((stats or {}).get("connection_close_responses", 0) or 0)
            for stats in transport.values()
            if isinstance(stats, dict)
        )
        retry_after_responses = sum(
            int((stats or {}).get("retry_after_responses", 0) or 0)
            for stats in transport.values()
            if isinstance(stats, dict)
        )
        circuit_open_events = sum(
            int(((stats or {}).get("circuit_breaker") or {}).get("open_events", 0) or 0)
            for stats in transport.values()
            if isinstance(stats, dict)
        )
        circuit_short_circuits = sum(
            int(((stats or {}).get("circuit_breaker") or {}).get("short_circuits", 0) or 0)
            for stats in transport.values()
            if isinstance(stats, dict)
        )
        if stale_reconnects > 0:
            alerts.append(
                alert(
                    "warning",
                    "backend_transport_stale_reconnects",
                    "backend HTTP keep-alive connections were reopened after stale connection errors",
                    stale_reconnects,
                )
            )
        if error_responses > 0:
            alerts.append(
                alert(
                    "warning",
                    "backend_transport_error_responses",
                    "backend HTTP calls returned retryable or failed status responses",
                    error_responses,
                )
            )
        if connection_close_responses > 0:
            alerts.append(
                alert(
                    "warning",
                    "backend_transport_connection_close_responses",
                    "backend HTTP responses asked clients to close keep-alive connections",
                    connection_close_responses,
                )
            )
        if retry_after_responses > 0:
            alerts.append(
                alert(
                    "warning",
                    "backend_transport_retry_after_responses",
                    "backend HTTP responses asked clients to back off with Retry-After",
                    retry_after_responses,
                )
            )
        if circuit_open_events > 0:
            alerts.append(
                alert(
                    "critical",
                    "backend_circuit_open_events",
                    "backend circuit breaker opened after repeated transient failures",
                    circuit_open_events,
                )
            )
        if circuit_short_circuits > 0:
            alerts.append(
                alert(
                    "critical",
                    "backend_circuit_short_circuits",
                    "backend calls were rejected locally while the circuit breaker was open",
                    circuit_short_circuits,
                )
            )
    if sync.get("last_error"):
        alerts.append(alert("critical", "sync_last_error", "latest sync run ended with an error", sync.get("last_error")))
    if int((sync.get("events") or {}).get("product_failed_events", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "sync_product_failures",
                "recent sync logs contain product-level indexing failures",
                (sync.get("events") or {}).get("product_failed_events"),
            )
        )
    if int((sync.get("events") or {}).get("batch_failed_events", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "sync_batch_failures",
                "recent sync logs contain batch-level failures",
                (sync.get("events") or {}).get("batch_failed_events"),
            )
        )
    if int((sync.get("events") or {}).get("sync_lock_busy_events", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "sync_lock_contention",
                "recent sync attempts were rejected because another sync operation was running",
                (sync.get("events") or {}).get("sync_lock_busy_events"),
            )
        )
    if int((sync.get("events") or {}).get("cache_invalidation_failed_events", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "search_cache_invalidation_failures",
                "recent sync runs could not clear stale search cache entries after index changes",
                (sync.get("events") or {}).get("cache_invalidation_failed_events"),
            )
        )
    if int(errors.get("api_error_events", 0) or 0) > 0:
        alerts.append(alert("warning", "api_errors_seen", "recent API error log contains failures", errors.get("api_error_events")))
    if int(errors.get("rate_limited_events", 0) or 0) > 0:
        alerts.append(
            alert("warning", "rate_limited_requests", "recent API errors include rate-limited requests", errors.get("rate_limited_events"))
        )
    if int(rate_limit.get("fallback_events", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "redis_rate_limit_fallback",
                "Redis rate limiter failed and local process buckets were used",
                rate_limit.get("fallback_events"),
            )
        )
    if int(rate_limit.get("redis_backoff_skipped_operations", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "redis_rate_limit_backoff_active",
                "Redis rate limiter calls were skipped locally after a Redis failure backoff opened",
                rate_limit.get("redis_backoff_skipped_operations"),
            )
        )
    if int(cache.get("error_count", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "search_cache_errors",
                "search cache reported get, set, decode, or delete errors",
                cache.get("error_count"),
            )
        )
    if int(cache.get("redis_backoff_skipped_operations", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "search_cache_redis_backoff_active",
                "Redis search cache calls were skipped locally after a Redis failure backoff opened",
                cache.get("redis_backoff_skipped_operations"),
            )
        )
    if int(cache.get("lock_wait_timeouts", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "search_cache_lock_wait_timeouts",
                "Redis cache miss followers waited too long for another API server to fill the cache and fell back to direct execution",
                cache.get("lock_wait_timeouts"),
            )
        )
    if int(singleflight.get("wait_timeouts", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "search_singleflight_wait_timeouts",
                "duplicate search requests waited too long for an in-flight cache fill and fell back to direct execution",
                singleflight.get("wait_timeouts"),
            )
        )
    if isinstance(image_validation, dict) and int(image_validation.get("wait_timeouts", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "image_validation_singleflight_wait_timeouts",
                "duplicate image validation requests waited too long for an in-flight validation and fell back to direct execution",
                image_validation.get("wait_timeouts"),
            )
        )
    if int(search_queue.get("queue_full_events", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "search_queue_full",
                "search execution queue rejected requests after waiting for an execution slot",
                search_queue.get("queue_full_events"),
            )
        )
    if int(image_queue.get("queue_full_events", 0) or 0) > 0:
        alerts.append(
            alert(
                "warning",
                "image_search_queue_full",
                "image search queue rejected requests after waiting for an execution slot",
                image_queue.get("queue_full_events"),
            )
        )
    if isinstance(api_threadpool, dict) and api_threadpool.get("ok") is False:
        alerts.append(
            alert(
                "warning",
                "api_threadpool_underprovisioned",
                "API threadpool tokens are below configured search and image execution concurrency",
                api_threadpool.get("runtime_tokens") or api_threadpool.get("configured_tokens"),
            )
        )
    for log_name in ("search", "error"):
        log_write_errors = int(((logs.get(log_name) or {}).get("write_errors", 0)) or 0)
        if log_write_errors > 0:
            alerts.append(
                alert(
                    "warning",
                    f"{log_name}_log_write_errors",
                    f"{log_name} log write failures were isolated from request handling",
                    log_write_errors,
                )
            )
    p95 = numeric_value(search.get("p95_elapsed_ms"))
    if p95 is not None and p95 >= 5000:
        alerts.append(alert("warning", "search_p95_high", "recent search p95 latency is above 5 seconds", p95))
    underfilled_after_cap = int(search.get("engine_underfilled_after_max_candidates_events", 0) or 0)
    if underfilled_after_cap > 0:
        alerts.append(
            alert(
                "warning",
                "search_engine_candidate_underfill",
                "search result pages were still underfilled after reaching the backend candidate cap",
                underfilled_after_cap,
            )
        )
    result_mall_mismatches = int(search.get("result_mall_id_mismatch_count", 0) or 0)
    if result_mall_mismatches > 0:
        alerts.append(
            alert(
                "critical",
                "result_mall_id_mismatch",
                "recent search responses contained products from a different mall_id",
                result_mall_mismatches,
            )
        )
    disk_used = numeric_value(disk.get("used_percent"))
    if disk_used is not None and disk_used >= 85:
        alerts.append(
            alert("critical" if disk_used >= 95 else "warning", "disk_usage_high", "log disk usage is high", disk_used)
        )
    memory_used = numeric_value(system.get("memory_used_percent"))
    if memory_used is not None and memory_used >= 85:
        alerts.append(
            alert(
                "critical" if memory_used >= 95 else "warning",
                "system_memory_high",
                "system memory usage is high",
                memory_used,
            )
        )
    return alerts


def alert(level: str, code: str, message: str, value: Any) -> dict[str, Any]:
    return {"level": level, "code": code, "message": message, "value": value}


def metrics_to_prometheus(metrics: dict[str, Any]) -> str:
    lines: list[str] = []
    emitted: set[str] = set()
    engine = metrics.get("engine") or {}
    sync = metrics.get("sync") or {}
    sync_events = sync.get("events") or {}
    search = metrics.get("search") or {}
    errors = metrics.get("errors") or {}
    rate_limit = metrics.get("rate_limit") or {}
    cache = metrics.get("cache") or {}
    singleflight = metrics.get("singleflight") or {}
    image_validation = metrics.get("image_validation") or {}
    image_queue = metrics.get("image_queue") or {}
    api_threadpool = metrics.get("api_threadpool") or {}
    logs = metrics.get("logs") or {}
    disk = metrics.get("disk") or {}
    process = metrics.get("process") or {}
    system = metrics.get("system") or {}
    alerts = metrics.get("alerts") or []
    health_cache = engine.get("health_cache") or {}

    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_engine_up",
        engine.get("ok") is True,
        "Search engine health status.",
        {"backend": engine.get("backend"), "index": engine.get("index")},
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_engine_health_cache_enabled",
        health_cache.get("enabled") is True,
        "Whether admin engine health backend probes are cached between metrics scrapes.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_engine_health_cache_hit",
        health_cache.get("hit") is True,
        "Whether this admin metrics response reused cached engine health backend probe data.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_engine_health_cache_ttl_seconds",
        health_cache.get("ttl_seconds"),
        "Configured TTL for admin engine health backend probe caching.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_engine_health_cache_age_ms",
        health_cache.get("age_ms"),
        "Age in milliseconds of cached engine health backend probe data used for this response.",
    )
    for service, transport in sorted((engine.get("transport") or {}).items()):
        if not isinstance(transport, dict):
            continue
        labels = {"service": service}
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_requests_started",
            transport.get("requests_started"),
            "Backend HTTP logical requests since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_max_active_requests",
            transport.get("max_active_requests"),
            "Configured per-process active backend HTTP request slot limit; zero disables the client-side limit.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_active_requests",
            transport.get("active_requests"),
            "Backend HTTP requests currently holding a client-side connection slot.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_max_active_requests_observed",
            transport.get("max_active_requests_observed"),
            "Maximum simultaneous backend HTTP requests observed since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_connection_acquire_wait_events",
            transport.get("connection_acquire_wait_events"),
            "Backend HTTP requests that waited for a client-side connection slot.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_connection_acquire_wait_timeouts",
            transport.get("connection_acquire_wait_timeouts"),
            "Backend HTTP requests rejected after waiting for a client-side connection slot.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_connection_acquire_wait_ms_avg",
            transport.get("avg_connection_acquire_wait_ms"),
            "Average wait time for backend HTTP client-side connection slots.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_connection_acquire_wait_ms_max",
            transport.get("max_connection_acquire_wait_ms"),
            "Maximum wait time for backend HTTP client-side connection slots.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_request_attempts",
            transport.get("request_attempts"),
            "Backend HTTP network attempts since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_responses",
            transport.get("responses_received"),
            "Backend HTTP responses received since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_error_responses",
            transport.get("error_responses"),
            "Backend HTTP error responses since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_invalid_json_responses",
            transport.get("invalid_json_responses"),
            "Backend HTTP responses with invalid JSON bodies since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_connections_opened",
            transport.get("connections_opened"),
            "Backend HTTP connections opened since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_open_connections",
            transport.get("open_connections"),
            "Backend HTTP keep-alive connections currently tracked by this process.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_connection_reuses",
            transport.get("connection_reuses"),
            "Backend HTTP keep-alive connection reuses since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_idle_reconnect_events",
            transport.get("idle_reconnects"),
            "Backend HTTP keep-alive connections proactively reopened after exceeding max idle age.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_stale_reconnect_events",
            transport.get("stale_reconnects"),
            "Backend HTTP stale keep-alive reconnect events since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_gzip_responses",
            transport.get("gzip_responses"),
            "Backend HTTP gzip-compressed responses since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_retry_after_responses",
            transport.get("retry_after_responses"),
            "Backend HTTP responses that included Retry-After.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_retry_after_seconds_max",
            transport.get("max_retry_after_seconds"),
            "Maximum Retry-After seconds observed on backend HTTP responses.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_retry_after_seconds_last",
            transport.get("last_retry_after_seconds"),
            "Most recent Retry-After seconds observed on backend HTTP responses.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_connection_close_responses",
            transport.get("connection_close_responses"),
            "Backend HTTP responses that asked the client to close the connection.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_response_ms_total",
            transport.get("total_elapsed_ms"),
            "Total backend HTTP response time in milliseconds since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_response_ms_avg",
            transport.get("avg_elapsed_ms"),
            "Average backend HTTP response time in milliseconds since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_response_ms_max",
            transport.get("max_elapsed_ms"),
            "Maximum backend HTTP response time in milliseconds since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_response_ms_last",
            transport.get("last_elapsed_ms"),
            "Most recent backend HTTP response time in milliseconds.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_request_body_bytes_total",
            transport.get("total_request_body_bytes"),
            "Total backend HTTP request body bytes sent since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_request_body_bytes_max",
            transport.get("max_request_body_bytes"),
            "Maximum backend HTTP request body size in bytes since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_request_body_bytes_last",
            transport.get("last_request_body_bytes"),
            "Most recent backend HTTP request body size in bytes.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_response_body_bytes_total",
            transport.get("total_response_body_bytes"),
            "Total backend HTTP response body bytes read on the wire since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_response_body_bytes_max",
            transport.get("max_response_body_bytes"),
            "Maximum backend HTTP response body size read on the wire since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_response_body_bytes_last",
            transport.get("last_response_body_bytes"),
            "Most recent backend HTTP response body size read on the wire.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_decoded_response_body_bytes_total",
            transport.get("total_decoded_response_body_bytes"),
            "Total decoded backend HTTP response body bytes since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_decoded_response_body_bytes_max",
            transport.get("max_decoded_response_body_bytes"),
            "Maximum decoded backend HTTP response body size since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_backend_http_decoded_response_body_bytes_last",
            transport.get("last_decoded_response_body_bytes"),
            "Most recent decoded backend HTTP response body size.",
            labels,
        )
        circuit = transport.get("circuit_breaker") or {}
        if isinstance(circuit, dict):
            add_prometheus_metric(
                lines,
                emitted,
                "haeorum_backend_http_circuit_open",
                str(circuit.get("state") or "") == "open",
                "Whether the backend HTTP circuit breaker is open.",
                labels,
            )
            add_prometheus_metric(
                lines,
                emitted,
                "haeorum_backend_http_circuit_half_open",
                str(circuit.get("state") or "") == "half_open",
                "Whether the backend HTTP circuit breaker is half-open.",
                labels,
            )
            add_prometheus_metric(
                lines,
                emitted,
                "haeorum_backend_http_circuit_consecutive_failures",
                circuit.get("consecutive_failures"),
                "Consecutive transient backend HTTP failures tracked by the circuit breaker.",
                labels,
            )
            add_prometheus_metric(
                lines,
                emitted,
                "haeorum_backend_http_circuit_open_events",
                circuit.get("open_events"),
                "Backend HTTP circuit breaker open events since process start.",
                labels,
            )
            add_prometheus_metric(
                lines,
                emitted,
                "haeorum_backend_http_circuit_short_circuits",
                circuit.get("short_circuits"),
                "Backend HTTP requests rejected locally because the circuit breaker was open.",
                labels,
            )
            add_prometheus_metric(
                lines,
                emitted,
                "haeorum_backend_http_circuit_recovery_events",
                circuit.get("recovery_events"),
                "Backend HTTP circuit breaker recoveries after successful half-open probes.",
                labels,
            )
            add_prometheus_metric(
                lines,
                emitted,
                "haeorum_backend_http_circuit_retry_after_seconds",
                circuit.get("retry_after_seconds"),
                "Seconds remaining before the backend HTTP circuit breaker can probe again.",
                labels,
            )
    gemini_query_cache = engine.get("gemini_query_embedding_cache")
    qwen_query_cache = engine.get("qwen_query_embedding_cache")
    if isinstance(gemini_query_cache, dict):
        query_cache = gemini_query_cache
        query_cache_provider = "Gemini"
        query_cache_metric_prefix = "haeorum_gemini_query_vector"
    elif isinstance(qwen_query_cache, dict):
        query_cache = qwen_query_cache
        query_cache_provider = "Qwen"
        query_cache_metric_prefix = "haeorum_qwen_query_vector"
    else:
        query_cache = {}
        query_cache_provider = "Embedding"
        query_cache_metric_prefix = "haeorum_embedding_query_vector"
    if isinstance(query_cache, dict) and query_cache:
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_entries",
            query_cache.get("runtime_entries"),
            f"Runtime {query_cache_provider} query vector cache entries.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_text_entries",
            query_cache.get("runtime_text_entries"),
            f"Runtime {query_cache_provider} text query vector cache entries.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_image_entries",
            query_cache.get("runtime_image_entries"),
            f"Runtime {query_cache_provider} image query vector cache entries.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_max_entries",
            query_cache.get("runtime_max_entries"),
            f"Maximum runtime {query_cache_provider} query vector cache entries.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_text_max_entries",
            query_cache.get("runtime_text_max_entries"),
            f"Maximum runtime {query_cache_provider} text query vector cache entries.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_image_max_entries",
            query_cache.get("runtime_image_max_entries"),
            f"Maximum runtime {query_cache_provider} image query vector cache entries.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_cache_hits",
            query_cache.get("runtime_hits"),
            f"Runtime {query_cache_provider} query vector cache hits since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_text_cache_hits",
            query_cache.get("runtime_text_hits"),
            f"Runtime {query_cache_provider} text query vector cache hits since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_image_cache_hits",
            query_cache.get("runtime_image_hits"),
            f"Runtime {query_cache_provider} image query vector cache hits since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_cache_misses",
            query_cache.get("runtime_misses"),
            f"Runtime {query_cache_provider} query vector cache misses since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_text_cache_misses",
            query_cache.get("runtime_text_misses"),
            f"Runtime {query_cache_provider} text query vector cache misses since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_image_cache_misses",
            query_cache.get("runtime_image_misses"),
            f"Runtime {query_cache_provider} image query vector cache misses since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_cache_evictions",
            query_cache.get("runtime_evictions"),
            f"Runtime {query_cache_provider} query vector cache evictions since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_text_cache_evictions",
            query_cache.get("runtime_text_evictions"),
            f"Runtime {query_cache_provider} text query vector cache evictions since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_runtime_image_cache_evictions",
            query_cache.get("runtime_image_evictions"),
            f"Runtime {query_cache_provider} image query vector cache evictions since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_in_flight",
            query_cache.get("in_flight"),
            f"Current in-flight {query_cache_provider} query vector computations.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_mixed_parallelism",
            query_cache.get("mixed_parallelism"),
            f"Configured shared workers for parallel {query_cache_provider} mixed text and image query vector computations.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_wait_timeout_seconds",
            query_cache.get("wait_timeout_seconds"),
            f"Timeout in seconds while waiting for an in-flight {query_cache_provider} query vector.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_wait_events",
            query_cache.get("wait_events"),
            f"{query_cache_provider} query vector in-flight wait events since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_wait_timeouts",
            query_cache.get("wait_timeouts"),
            f"{query_cache_provider} query vector in-flight waits that timed out since process start.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_wait_ms_total",
            query_cache.get("total_wait_ms"),
            f"Total milliseconds spent waiting for in-flight {query_cache_provider} query vectors.",
        )
        add_prometheus_metric(
            lines,
            emitted,
            f"{query_cache_metric_prefix}_wait_ms_max",
            query_cache.get("max_wait_ms"),
            f"Maximum wait in milliseconds for an in-flight {query_cache_provider} query vector.",
        )
        image_batcher = query_cache.get("image_batcher")
        if isinstance(image_batcher, dict):
            add_prometheus_metric(
                lines,
                emitted,
                f"{query_cache_metric_prefix}_image_batcher_enabled",
                1 if image_batcher.get("enabled") else 0,
                f"Whether {query_cache_provider} query image embedding micro-batching is enabled.",
            )
            add_prometheus_metric(
                lines,
                emitted,
                f"{query_cache_metric_prefix}_image_batcher_batch_count",
                image_batcher.get("batch_count"),
                f"{query_cache_provider} query image embedding micro-batches since process start.",
            )
            add_prometheus_metric(
                lines,
                emitted,
                f"{query_cache_metric_prefix}_image_batcher_batched_inputs",
                image_batcher.get("batched_input_count"),
                f"{query_cache_provider} query image inputs sent through micro-batches since process start.",
            )
            add_prometheus_metric(
                lines,
                emitted,
                f"{query_cache_metric_prefix}_image_batcher_avg_batch_size",
                image_batcher.get("avg_batch_size"),
                f"Average {query_cache_provider} query image embedding micro-batch size.",
            )
            add_prometheus_metric(
                lines,
                emitted,
                f"{query_cache_metric_prefix}_image_batcher_max_observed_batch_size",
                image_batcher.get("max_observed_batch_size"),
                f"Maximum observed {query_cache_provider} query image embedding micro-batch size.",
            )
    add_prometheus_metric(lines, emitted, "haeorum_sync_last_indexed", sync.get("indexed"), "Products indexed in the last sync run.")
    add_prometheus_metric(lines, emitted, "haeorum_sync_last_deleted", sync.get("deleted"), "Products deleted in the last sync run.")
    add_prometheus_metric(lines, emitted, "haeorum_sync_last_failed", sync.get("failed"), "Products failed in the last sync run.")
    add_prometheus_metric(lines, emitted, "haeorum_sync_recent_run_events", sync_events.get("run_events"), "Recent sync run events.")
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_failed_run_events",
        sync_events.get("failed_run_events"),
        "Recent sync runs with failures.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_product_failed_events",
        sync_events.get("product_failed_events"),
        "Recent product-level sync failures.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_batch_failed_events",
        sync_events.get("batch_failed_events"),
        "Recent batch-level sync failures.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_lock_busy_events",
        sync_events.get("sync_lock_busy_events"),
        "Recent sync attempts rejected by the operation lock.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_product_delete_events",
        sync_events.get("product_delete_events"),
        "Recent product delete events written by sync.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_search_cache_clear_events",
        sync_events.get("cache_invalidation_events"),
        "Recent sync search-cache clear events after index mutations.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_search_cache_clear_failed_events",
        sync_events.get("cache_invalidation_failed_events"),
        "Recent failed sync search-cache clear events.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_image_probe_failed_events",
        sync_events.get("image_probe_failed_events"),
        "Recent product image probe failures.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_sync_recent_image_quality_warning_events",
        sync_events.get("image_quality_warning_events"),
        "Recent product image quality warnings.",
    )
    add_mapping_prometheus_metrics(
        lines,
        emitted,
        "haeorum_sync_recent_failed_action_events",
        sync_events.get("failed_action_counts"),
        "Recent product sync failures by action.",
        "action",
    )
    add_mapping_prometheus_metrics(
        lines,
        emitted,
        "haeorum_sync_recent_batch_failed_action_events",
        sync_events.get("batch_failed_action_counts"),
        "Recent batch sync failures by action.",
        "action",
    )

    add_prometheus_metric(lines, emitted, "haeorum_recent_search_events", search.get("search_events"), "Recent search events.")
    add_prometheus_metric(lines, emitted, "haeorum_recent_click_events", search.get("click_events"), "Recent click events.")
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_image_search_events",
        search.get("image_search_events"),
        "Recent image or text-image search events.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_cached_search_events",
        search.get("cached_search_events"),
        "Recent cached search responses.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_cached_image_search_events",
        search.get("cached_image_search_events"),
        "Recent cached image search responses.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_cache_hit_rate_percent",
        search.get("cache_hit_rate_percent"),
        "Recent search cache hit rate percent.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_low_confidence_events",
        search.get("low_confidence_events"),
        "Recent low-confidence search events.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_zero_result_events",
        search.get("zero_result_events"),
        "Recent zero-result search events.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_result_mall_id_mismatch_events",
        search.get("result_mall_id_mismatch_events"),
        "Recent search events with result mall_id mismatches.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_result_mall_id_mismatches",
        search.get("result_mall_id_mismatch_count"),
        "Recent result items whose mall_id differed from the requested mall_id.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_average_elapsed_ms",
        search.get("average_elapsed_ms"),
        "Recent average search latency in milliseconds.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_p95_elapsed_ms",
        search.get("p95_elapsed_ms"),
        "Recent search p95 latency in milliseconds.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_p99_elapsed_ms",
        search.get("p99_elapsed_ms"),
        "Recent search p99 latency in milliseconds.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_average_result_count",
        search.get("average_result_count"),
        "Recent average search result count.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_search_engine_attempts",
        search.get("engine_search_attempts"),
        "Recent backend search execution attempts, including adaptive refetches.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_search_engine_adaptive_refetches",
        search.get("engine_adaptive_refetches"),
        "Recent adaptive backend search refetches after product-group collapse underfilled a page.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_search_engine_adaptive_refetch_searches",
        search.get("engine_adaptive_refetch_searches"),
        "Recent search events that needed adaptive backend refetch.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_search_engine_underfilled_after_max_candidates_events",
        search.get("engine_underfilled_after_max_candidates_events"),
        "Recent search events still underfilled after reaching the backend candidate cap.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_engine_average_attempts",
        search.get("engine_average_search_attempts"),
        "Recent average backend search attempts per logged search.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_engine_max_attempts",
        search.get("engine_max_search_attempts"),
        "Recent maximum backend search attempts for one logged search.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_engine_average_final_candidate_limit",
        search.get("engine_average_final_candidate_limit"),
        "Recent average final backend candidate limit.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_engine_max_final_candidate_limit",
        search.get("engine_max_final_candidate_limit"),
        "Recent maximum final backend candidate limit.",
    )
    add_mapping_prometheus_metrics(
        lines,
        emitted,
        "haeorum_recent_search_query_type_events",
        search.get("query_type_counts"),
        "Recent search events by query type.",
        "query_type",
    )
    add_mapping_prometheus_metrics(
        lines,
        emitted,
        "haeorum_recent_search_mall_events",
        search.get("mall_counts"),
        "Recent search events by mall.",
        "mall_id",
    )

    add_prometheus_metric(lines, emitted, "haeorum_recent_api_error_events", errors.get("api_error_events"), "Recent API error events.")
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_recent_rate_limited_events",
        errors.get("rate_limited_events"),
        "Recent API rate limit error events.",
    )
    add_mapping_prometheus_metrics(
        lines,
        emitted,
        "haeorum_recent_api_error_status_events",
        errors.get("status_code_counts"),
        "Recent API error events by status code.",
        "status_code",
    )
    add_mapping_prometheus_metrics(
        lines,
        emitted,
        "haeorum_recent_api_error_path_events",
        errors.get("path_counts"),
        "Recent API error events by path.",
        "path",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_redis_enabled",
        rate_limit.get("redis_enabled") is True,
        "Whether Redis is configured for rate limiting.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_fallback_events",
        rate_limit.get("fallback_events"),
        "Rate limiter fallback events caused by Redis failures.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_fallback_active",
        rate_limit.get("fallback_active") is True,
        "Whether this process has used local rate limit fallback since startup.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_fallback_skipped_redis_events",
        rate_limit.get("fallback_skipped_redis_events"),
        "Rate limiter fallback events that skipped Redis because failure backoff was active.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_fallback_buckets",
        rate_limit.get("fallback_bucket_count"),
        "Local fallback rate limit bucket count.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_fallback_max_buckets",
        rate_limit.get("fallback_max_buckets"),
        "Maximum local fallback rate limit buckets kept by this process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_fallback_prune_interval_seconds",
        rate_limit.get("fallback_prune_interval_seconds"),
        "Interval between periodic stale local fallback rate limit bucket sweeps.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_fallback_pruned_buckets",
        rate_limit.get("fallback_pruned_buckets"),
        "Total stale local fallback rate limit buckets pruned by this process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_redis_socket_timeout_seconds",
        rate_limit.get("redis_socket_timeout_seconds"),
        "Configured Redis socket read timeout for rate limiter operations.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_redis_socket_connect_timeout_seconds",
        rate_limit.get("redis_socket_connect_timeout_seconds"),
        "Configured Redis socket connect timeout for rate limiter operations.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_redis_backoff_active",
        rate_limit.get("redis_backoff_active") is True,
        "Whether rate limiter Redis failure backoff is currently open.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_redis_backoff_failure_events",
        rate_limit.get("redis_backoff_failure_events"),
        "Rate limiter Redis failure events that opened local failure backoff.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_redis_backoff_skipped_operations",
        rate_limit.get("redis_backoff_skipped_operations"),
        "Rate limiter Redis operations skipped while failure backoff was active.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_redis_backoff_remaining_ms",
        rate_limit.get("redis_backoff_remaining_ms"),
        "Milliseconds remaining before the rate limiter tries Redis again.",
    )
    rate_limit_limits = rate_limit.get("limits") or {}
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_search_per_minute",
        rate_limit_limits.get("search_per_minute"),
        "Configured per-client search requests per minute; 0 means disabled.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_mall_search_per_minute",
        rate_limit_limits.get("mall_search_per_minute"),
        "Configured per-mall search requests per minute; 0 means disabled.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_image_per_minute",
        rate_limit_limits.get("image_per_minute"),
        "Configured per-client image or mixed search requests per minute; 0 means disabled.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_rate_limit_mall_image_per_minute",
        rate_limit_limits.get("mall_image_per_minute"),
        "Configured per-mall image or mixed search requests per minute; 0 means disabled.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_redis_enabled",
        cache.get("redis_enabled") is True,
        "Whether Redis is configured for search result caching.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_error_events",
        cache.get("error_count"),
        "Search cache get, set, decode, delete, and clear error events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_get_error_events",
        cache.get("get_errors"),
        "Search cache get error events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_set_error_events",
        cache.get("set_errors"),
        "Search cache set error events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_decode_error_events",
        cache.get("decode_errors"),
        "Search cache decode error events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_delete_error_events",
        cache.get("delete_errors"),
        "Search cache delete error events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_clear_error_events",
        cache.get("clear_errors"),
        "Search cache clear error events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_redis_socket_timeout_seconds",
        cache.get("redis_socket_timeout_seconds"),
        "Configured Redis socket read timeout for search cache operations.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_redis_socket_connect_timeout_seconds",
        cache.get("redis_socket_connect_timeout_seconds"),
        "Configured Redis socket connect timeout for search cache operations.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_redis_backoff_active",
        cache.get("redis_backoff_active") is True,
        "Whether search cache Redis failure backoff is currently open.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_redis_backoff_failure_events",
        cache.get("redis_backoff_failure_events"),
        "Search cache Redis failure events that opened local failure backoff.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_redis_backoff_skipped_operations",
        cache.get("redis_backoff_skipped_operations"),
        "Search cache Redis operations skipped while failure backoff was active.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_redis_backoff_remaining_ms",
        cache.get("redis_backoff_remaining_ms"),
        "Milliseconds remaining before the search cache tries Redis again.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_claim_events",
        cache.get("lock_claims"),
        "Search cache distributed miss lock claims since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_contention_events",
        cache.get("lock_contention_events"),
        "Search cache distributed miss lock contention events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_error_events",
        cache.get("lock_errors"),
        "Search cache distributed miss lock claim error events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_release_error_events",
        cache.get("lock_release_errors"),
        "Search cache distributed miss lock release error events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_wait_events",
        cache.get("lock_wait_events"),
        "Search cache distributed miss follower wait attempts since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_wait_timeouts",
        cache.get("lock_wait_timeouts"),
        "Search cache distributed miss followers that timed out waiting for a cache fill since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_wait_ms_total",
        cache.get("lock_total_wait_ms"),
        "Total milliseconds spent waiting for distributed search cache fills since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_wait_ms_avg",
        cache.get("lock_avg_wait_ms"),
        "Average milliseconds spent waiting for distributed search cache fills.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_lock_wait_ms_max",
        cache.get("lock_max_wait_ms"),
        "Maximum milliseconds spent waiting for a distributed search cache fill since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_entries",
        cache.get("entry_count"),
        "In-process memory search cache entry count when memory cache is used.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_max_entries",
        cache.get("max_entries"),
        "Configured in-process memory search cache entry limit.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_cache_evictions",
        cache.get("evictions"),
        "In-process memory search cache entries evicted by the entry limit since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_singleflight_enabled",
        singleflight.get("enabled") is True,
        "Whether duplicate cache-miss search requests are coalesced in this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_singleflight_in_flight",
        singleflight.get("in_flight"),
        "Current search cache misses being computed for local duplicate request coalescing.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_singleflight_wait_timeout_seconds",
        singleflight.get("wait_timeout_seconds"),
        "Seconds a duplicate search request waits for an in-flight local cache fill before falling back to direct execution.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_singleflight_wait_events",
        singleflight.get("wait_events"),
        "Duplicate search request wait attempts for in-flight local cache fills since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_singleflight_wait_timeouts",
        singleflight.get("wait_timeouts"),
        "Duplicate search requests that timed out waiting for an in-flight local cache fill since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_singleflight_wait_ms_total",
        singleflight.get("total_wait_ms"),
        "Total milliseconds spent waiting for in-flight local search cache fills since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_singleflight_wait_ms_avg",
        singleflight.get("avg_wait_ms"),
        "Average milliseconds spent waiting for in-flight local search cache fills.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_singleflight_wait_ms_max",
        singleflight.get("max_wait_ms"),
        "Maximum milliseconds spent waiting for an in-flight local search cache fill since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_singleflight_enabled",
        image_validation.get("enabled") is True,
        "Whether duplicate image validation requests are coalesced in this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_singleflight_in_flight",
        image_validation.get("in_flight"),
        "Current image validations being computed for local duplicate request coalescing.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_singleflight_wait_timeout_seconds",
        image_validation.get("wait_timeout_seconds"),
        "Seconds a duplicate image validation request waits for an in-flight validation before falling back.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_singleflight_wait_events",
        image_validation.get("wait_events"),
        "Duplicate image validation wait attempts for in-flight local validation since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_singleflight_wait_timeouts",
        image_validation.get("wait_timeouts"),
        "Duplicate image validation requests that timed out waiting for an in-flight validation since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_singleflight_wait_ms_total",
        image_validation.get("total_wait_ms"),
        "Total milliseconds spent waiting for in-flight local image validation since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_singleflight_wait_ms_avg",
        image_validation.get("avg_wait_ms"),
        "Average milliseconds spent waiting for in-flight local image validation.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_singleflight_wait_ms_max",
        image_validation.get("max_wait_ms"),
        "Maximum milliseconds spent waiting for an in-flight local image validation since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_cache_enabled",
        image_validation.get("cache_enabled") is True,
        "Whether validated upload images are cached briefly in this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_cache_entries",
        image_validation.get("cache_entry_count"),
        "Current validated upload image entries cached in this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_cache_max_entries",
        image_validation.get("cache_max_entries"),
        "Maximum validated upload image entries retained in this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_cache_ttl_seconds",
        image_validation.get("cache_ttl_seconds"),
        "Seconds validated upload image entries remain cached in this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_cache_hits",
        image_validation.get("cache_hits"),
        "Validated upload image cache hits since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_cache_misses",
        image_validation.get("cache_misses"),
        "Validated upload image cache misses since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_cache_evictions",
        image_validation.get("cache_evictions"),
        "Validated upload image cache evictions since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_error_cache_entries",
        image_validation.get("error_cache_entry_count"),
        "Current rejected upload image validation errors cached in this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_error_cache_hits",
        image_validation.get("error_cache_hits"),
        "Rejected upload image validation error cache hits since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_validation_error_cache_evictions",
        image_validation.get("error_cache_evictions"),
        "Rejected upload image validation error cache evictions since process start.",
    )
    search_queue = metrics.get("search_queue") or {}
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_enabled",
        search_queue.get("enabled") is True,
        "Whether all search execution is bounded by the search queue gate.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_max_concurrency",
        search_queue.get("max_concurrency"),
        "Maximum concurrent search execution work allowed by this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_timeout_seconds",
        search_queue.get("queue_timeout_seconds"),
        "Seconds a search waits for an execution slot before returning 429.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_in_flight",
        search_queue.get("in_flight"),
        "Current searches holding an execution slot.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_available_slots",
        search_queue.get("available_slots"),
        "Currently available search execution slots.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_acquired_events",
        search_queue.get("acquired_events"),
        "Search queue slot acquisition events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_full_events",
        search_queue.get("queue_full_events"),
        "Search queue-full rejection events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_wait_events",
        search_queue.get("wait_events"),
        "Search queue slot wait attempts since process start, including acquired and rejected attempts.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_wait_ms_total",
        search_queue.get("total_wait_ms"),
        "Total milliseconds spent waiting for search queue slots since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_wait_ms_avg",
        search_queue.get("avg_wait_ms"),
        "Average milliseconds spent waiting for search queue slots.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_search_queue_wait_ms_max",
        search_queue.get("max_wait_ms"),
        "Maximum milliseconds spent waiting for a search queue slot since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_enabled",
        image_queue.get("enabled") is True,
        "Whether image search execution is bounded by the image search queue gate.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_max_concurrency",
        image_queue.get("max_concurrency"),
        "Maximum concurrent image or mixed search work allowed by this API process.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_timeout_seconds",
        image_queue.get("queue_timeout_seconds"),
        "Seconds an image search waits for an execution slot before returning 429.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_in_flight",
        image_queue.get("in_flight"),
        "Current image or mixed search requests holding an execution slot.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_available_slots",
        image_queue.get("available_slots"),
        "Currently available image search execution slots.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_acquired_events",
        image_queue.get("acquired_events"),
        "Image search queue slot acquisition events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_full_events",
        image_queue.get("queue_full_events"),
        "Image search queue-full rejection events since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_wait_events",
        image_queue.get("wait_events"),
        "Image search queue slot wait attempts since process start, including acquired and rejected attempts.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_wait_ms_total",
        image_queue.get("total_wait_ms"),
        "Total milliseconds spent waiting for image search queue slots since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_wait_ms_avg",
        image_queue.get("avg_wait_ms"),
        "Average milliseconds spent waiting for image search queue slots.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_image_search_queue_wait_ms_max",
        image_queue.get("max_wait_ms"),
        "Maximum milliseconds spent waiting for an image search queue slot since process start.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_api_threadpool_ok",
        api_threadpool.get("ok") is True,
        "Whether API threadpool tokens cover configured search and image execution concurrency.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_api_threadpool_configured_tokens",
        api_threadpool.get("configured_tokens"),
        "Configured API anyio threadpool token count.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_api_threadpool_runtime_tokens",
        api_threadpool.get("runtime_tokens"),
        "Runtime API anyio threadpool token count after startup configuration.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_api_threadpool_required_tokens",
        api_threadpool.get("required_tokens"),
        "Minimum API threadpool tokens required by configured search and image execution concurrency.",
    )

    for log_name, summary in sorted(logs.items()):
        if not isinstance(summary, dict):
            continue
        labels = {"log": log_name, "path": summary.get("path")}
        add_prometheus_metric(lines, emitted, "haeorum_log_file_exists", summary.get("exists") is True, "Log file existence.", labels)
        add_prometheus_metric(lines, emitted, "haeorum_log_file_bytes", summary.get("bytes"), "Log file size in bytes.", labels)
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_log_write_error_events",
            summary.get("write_errors"),
            "Log write failures isolated from request handling since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_log_write_events",
            summary.get("write_events"),
            "Successful JSONL log writes since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_log_write_ms_total",
            summary.get("write_total_ms"),
            "Total JSONL log write time in milliseconds since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_log_write_ms_avg",
            summary.get("write_avg_ms"),
            "Average JSONL log write time in milliseconds since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_log_write_ms_max",
            summary.get("write_max_ms"),
            "Maximum JSONL log write time in milliseconds since process start.",
            labels,
        )
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_log_write_ms_last",
            summary.get("last_write_ms"),
            "Last JSONL log write time in milliseconds.",
            labels,
        )

    disk_labels = {"path": disk.get("path")}
    add_prometheus_metric(lines, emitted, "haeorum_disk_total_bytes", disk.get("total_bytes"), "Log disk total bytes.", disk_labels)
    add_prometheus_metric(lines, emitted, "haeorum_disk_used_bytes", disk.get("used_bytes"), "Log disk used bytes.", disk_labels)
    add_prometheus_metric(lines, emitted, "haeorum_disk_free_bytes", disk.get("free_bytes"), "Log disk free bytes.", disk_labels)
    add_prometheus_metric(lines, emitted, "haeorum_disk_used_percent", disk.get("used_percent"), "Log disk used percent.", disk_labels)

    add_prometheus_metric(lines, emitted, "haeorum_process_uptime_seconds", process.get("uptime_seconds"), "API process uptime seconds.")
    add_prometheus_metric(lines, emitted, "haeorum_process_cpu_percent", process.get("cpu_percent"), "API process CPU percent.")
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_process_memory_rss_bytes",
        process.get("memory_rss_bytes"),
        "API process RSS memory bytes.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_process_memory_vms_bytes",
        process.get("memory_vms_bytes"),
        "API process VMS memory bytes.",
    )
    add_prometheus_metric(lines, emitted, "haeorum_process_thread_count", process.get("thread_count"), "API process thread count.")

    add_prometheus_metric(lines, emitted, "haeorum_system_cpu_count", system.get("cpu_count"), "System CPU count.")
    add_prometheus_metric(lines, emitted, "haeorum_system_cpu_percent", system.get("system_cpu_percent"), "System CPU percent.")
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_system_memory_total_bytes",
        system.get("memory_total_bytes"),
        "System memory total bytes.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_system_memory_available_bytes",
        system.get("memory_available_bytes"),
        "System memory available bytes.",
    )
    add_prometheus_metric(
        lines,
        emitted,
        "haeorum_system_memory_used_percent",
        system.get("memory_used_percent"),
        "System memory used percent.",
    )

    add_prometheus_metric(lines, emitted, "haeorum_operational_alerts", len(alerts), "Current operational alert count.")
    for item in alerts:
        if not isinstance(item, dict):
            continue
        add_prometheus_metric(
            lines,
            emitted,
            "haeorum_operational_alert",
            1,
            "Current operational alert by code and level.",
            {"code": item.get("code"), "level": item.get("level")},
        )
    return "\n".join(lines).rstrip() + "\n"


def add_mapping_prometheus_metrics(
    lines: list[str],
    emitted: set[str],
    name: str,
    values: Any,
    help_text: str,
    label_name: str,
) -> None:
    if not isinstance(values, dict):
        return
    for label_value, value in sorted(values.items(), key=lambda item: str(item[0])):
        add_prometheus_metric(lines, emitted, name, value, help_text, {label_name: label_value})


def add_prometheus_metric(
    lines: list[str],
    emitted: set[str],
    name: str,
    value: Any,
    help_text: str,
    labels: dict[str, Any] | None = None,
) -> None:
    metric_value = prometheus_number(value)
    if metric_value is None:
        return
    if name not in emitted:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        emitted.add(name)
    lines.append(f"{name}{format_prometheus_labels(labels or {})} {format_prometheus_value(metric_value)}")


def prometheus_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if not is_number(value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def format_prometheus_value(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def format_prometheus_labels(labels: dict[str, Any]) -> str:
    clean_labels = {
        str(key): str(value)
        for key, value in labels.items()
        if value is not None and str(value) != ""
    }
    if not clean_labels:
        return ""
    pairs = [
        f'{key}="{escape_prometheus_label(value)}"'
        for key, value in sorted(clean_labels.items())
    ]
    return "{" + ",".join(pairs) + "}"


def escape_prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def numeric_value(value: Any) -> float | None:
    if not is_number(value):
        return None
    return float(value)


def summarize_search_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    searches = [entry for entry in entries if entry.get("type") == "search"]
    clicks = [entry for entry in entries if entry.get("type") == "click"]
    elapsed = [float(entry["elapsed_ms"]) for entry in searches if is_number(entry.get("elapsed_ms"))]
    result_counts = [int(entry["result_count"]) for entry in searches if is_number(entry.get("result_count"))]
    engine_attempt_counts = [
        int(entry["engine_search_attempts"])
        for entry in searches
        if is_number(entry.get("engine_search_attempts"))
    ]
    engine_adaptive_refetches = [
        int(entry["engine_adaptive_refetches"])
        for entry in searches
        if is_number(entry.get("engine_adaptive_refetches"))
    ]
    engine_final_candidate_limits = [
        int(entry["engine_final_candidate_limit"])
        for entry in searches
        if is_number(entry.get("engine_final_candidate_limit"))
    ]
    engine_final_raw_candidate_counts = [
        int(entry["engine_final_raw_candidate_count"])
        for entry in searches
        if is_number(entry.get("engine_final_raw_candidate_count"))
    ]
    engine_final_collapsed_candidate_counts = [
        int(entry["engine_final_collapsed_candidate_count"])
        for entry in searches
        if is_number(entry.get("engine_final_collapsed_candidate_count"))
    ]
    low_confidence = [entry for entry in searches if entry.get("low_confidence") is True]
    cached = [entry for entry in searches if entry.get("cached") is True]
    cached_image = [entry for entry in cached if entry.get("query_type") in {"image", "text_image"}]
    result_mall_mismatches = [
        int(entry.get("result_mall_id_mismatch_count") or 0)
        for entry in searches
        if is_number(entry.get("result_mall_id_mismatch_count"))
    ]
    query_types = Counter(str(entry.get("query_type") or "unknown") for entry in searches)
    malls = Counter(str(entry.get("mall_id") or "unknown") for entry in searches)
    return {
        "sample_size": len(entries),
        "search_events": len(searches),
        "click_events": len(clicks),
        "cached_search_events": len(cached),
        "cached_image_search_events": len(cached_image),
        "cache_hit_rate_percent": round((len(cached) / len(searches)) * 100, 1) if searches else None,
        "query_type_counts": dict(sorted(query_types.items())),
        "mall_counts": dict(malls.most_common(20)),
        "image_search_events": query_types.get("image", 0) + query_types.get("text_image", 0),
        "low_confidence_events": len(low_confidence),
        "zero_result_events": sum(1 for count in result_counts if count == 0),
        "result_mall_id_mismatch_events": sum(1 for count in result_mall_mismatches if count > 0),
        "result_mall_id_mismatch_count": sum(result_mall_mismatches),
        "average_elapsed_ms": round(sum(elapsed) / len(elapsed), 1) if elapsed else None,
        "p95_elapsed_ms": percentile(elapsed, 95),
        "p99_elapsed_ms": percentile(elapsed, 99),
        "average_result_count": round(sum(result_counts) / len(result_counts), 1) if result_counts else None,
        "engine_search_attempts": sum(engine_attempt_counts),
        "engine_adaptive_refetches": sum(engine_adaptive_refetches),
        "engine_adaptive_refetch_searches": sum(1 for count in engine_adaptive_refetches if count > 0),
        "engine_underfilled_after_max_candidates_events": sum(
            1 for entry in searches if entry.get("engine_underfilled_after_max_candidates") is True
        ),
        "engine_average_search_attempts": (
            round(sum(engine_attempt_counts) / len(engine_attempt_counts), 3) if engine_attempt_counts else None
        ),
        "engine_max_search_attempts": max(engine_attempt_counts) if engine_attempt_counts else None,
        "engine_average_final_candidate_limit": (
            round(sum(engine_final_candidate_limits) / len(engine_final_candidate_limits), 1)
            if engine_final_candidate_limits
            else None
        ),
        "engine_max_final_candidate_limit": max(engine_final_candidate_limits) if engine_final_candidate_limits else None,
        "engine_average_final_raw_candidate_count": (
            round(sum(engine_final_raw_candidate_counts) / len(engine_final_raw_candidate_counts), 1)
            if engine_final_raw_candidate_counts
            else None
        ),
        "engine_average_final_collapsed_candidate_count": (
            round(sum(engine_final_collapsed_candidate_counts) / len(engine_final_collapsed_candidate_counts), 1)
            if engine_final_collapsed_candidate_counts
            else None
        ),
    }


def summarize_error_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [entry for entry in entries if entry.get("type") == "api_error"]
    status_codes = Counter(str(entry.get("status_code") or "unknown") for entry in errors)
    paths = Counter(str(entry.get("path") or "unknown") for entry in errors)
    rate_limited = [
        entry
        for entry in errors
        if str(entry.get("status_code")) == "429" or "rate limit" in str(entry.get("detail", "")).lower()
    ]
    return {
        "sample_size": len(entries),
        "api_error_events": len(errors),
        "status_code_counts": dict(sorted(status_codes.items())),
        "path_counts": dict(paths.most_common(20)),
        "rate_limited_events": len(rate_limited),
    }


def rate_limiter_summary(
    rate_limiter: Any | None,
    settings: Settings,
    memory_bucket_count: int | None = None,
) -> dict[str, Any]:
    limits = rate_limit_config_summary(settings)
    if rate_limiter is None:
        return {
            "backend": "memory",
            "redis_enabled": bool(settings.redis_url),
            "limits": limits,
            "fallback_events": 0,
            "fallback_active": False,
            "fallback_bucket_count": int(memory_bucket_count or 0),
            "fallback_max_buckets": settings.rate_limit_max_buckets,
            "fallback_prune_interval_seconds": settings.rate_limit_prune_interval_seconds,
            "fallback_pruned_buckets": 0,
            "fallback_skipped_redis_events": 0,
            "redis_socket_timeout_seconds": settings.redis_socket_timeout_seconds,
            "redis_socket_connect_timeout_seconds": settings.redis_socket_connect_timeout_seconds,
            "redis_backoff_seconds": settings.redis_failure_backoff_seconds,
            "redis_backoff_active": False,
            "redis_backoff_remaining_ms": 0.0,
            "redis_backoff_failure_events": 0,
            "redis_backoff_skipped_operations": 0,
            "last_error": None,
        }
    status = getattr(rate_limiter, "status", None)
    if not callable(status):
        return {
            "backend": rate_limiter.__class__.__name__,
            "redis_enabled": bool(settings.redis_url),
            "limits": limits,
            "fallback_events": 0,
            "fallback_active": False,
            "fallback_bucket_count": 0,
            "fallback_max_buckets": settings.rate_limit_max_buckets,
            "fallback_prune_interval_seconds": settings.rate_limit_prune_interval_seconds,
            "fallback_pruned_buckets": 0,
            "fallback_skipped_redis_events": 0,
            "redis_socket_timeout_seconds": settings.redis_socket_timeout_seconds,
            "redis_socket_connect_timeout_seconds": settings.redis_socket_connect_timeout_seconds,
            "redis_backoff_seconds": settings.redis_failure_backoff_seconds,
            "redis_backoff_active": False,
            "redis_backoff_remaining_ms": 0.0,
            "redis_backoff_failure_events": 0,
            "redis_backoff_skipped_operations": 0,
            "last_error": None,
        }
    try:
        data = status()
    except Exception as exc:
        return {
            "backend": rate_limiter.__class__.__name__,
            "redis_enabled": bool(settings.redis_url),
            "limits": limits,
            "fallback_events": 0,
            "fallback_active": False,
            "fallback_bucket_count": 0,
            "fallback_max_buckets": settings.rate_limit_max_buckets,
            "fallback_prune_interval_seconds": settings.rate_limit_prune_interval_seconds,
            "fallback_pruned_buckets": 0,
            "fallback_skipped_redis_events": 0,
            "redis_socket_timeout_seconds": settings.redis_socket_timeout_seconds,
            "redis_socket_connect_timeout_seconds": settings.redis_socket_connect_timeout_seconds,
            "redis_backoff_seconds": settings.redis_failure_backoff_seconds,
            "redis_backoff_active": False,
            "redis_backoff_remaining_ms": 0.0,
            "redis_backoff_failure_events": 0,
            "redis_backoff_skipped_operations": 0,
            "last_error": str(exc),
        }
    if isinstance(data, dict):
        data.setdefault("fallback_max_buckets", settings.rate_limit_max_buckets)
        data.setdefault("fallback_prune_interval_seconds", settings.rate_limit_prune_interval_seconds)
        data.setdefault("fallback_pruned_buckets", 0)
        data.setdefault("fallback_skipped_redis_events", 0)
        data.setdefault("redis_socket_timeout_seconds", settings.redis_socket_timeout_seconds)
        data.setdefault("redis_socket_connect_timeout_seconds", settings.redis_socket_connect_timeout_seconds)
        data.setdefault("redis_backoff_seconds", settings.redis_failure_backoff_seconds)
        data.setdefault("redis_backoff_active", False)
        data.setdefault("redis_backoff_remaining_ms", 0.0)
        data.setdefault("redis_backoff_failure_events", 0)
        data.setdefault("redis_backoff_skipped_operations", 0)
        data.setdefault("limits", limits)
        return data
    return {
        "backend": str(data),
        "limits": limits,
        "fallback_max_buckets": settings.rate_limit_max_buckets,
        "fallback_prune_interval_seconds": settings.rate_limit_prune_interval_seconds,
        "fallback_pruned_buckets": 0,
        "fallback_skipped_redis_events": 0,
        "redis_socket_timeout_seconds": settings.redis_socket_timeout_seconds,
        "redis_socket_connect_timeout_seconds": settings.redis_socket_connect_timeout_seconds,
        "redis_backoff_seconds": settings.redis_failure_backoff_seconds,
        "redis_backoff_active": False,
        "redis_backoff_remaining_ms": 0.0,
        "redis_backoff_failure_events": 0,
        "redis_backoff_skipped_operations": 0,
    }


def rate_limit_config_summary(settings: Settings) -> dict[str, int]:
    return {
        "search_per_minute": settings.search_rate_limit_per_minute,
        "mall_search_per_minute": settings.mall_search_rate_limit_per_minute,
        "click_per_minute": settings.click_rate_limit_per_minute,
        "mall_click_per_minute": settings.mall_click_rate_limit_per_minute,
        "image_per_minute": settings.image_rate_limit_per_minute,
        "mall_image_per_minute": settings.mall_image_rate_limit_per_minute,
    }


def search_cache_summary(search_cache: Any | None, settings: Settings) -> dict[str, Any]:
    if search_cache is None:
        return normalize_search_cache_status(
            {},
            settings,
            backend="none",
            redis_enabled=bool(settings.redis_url),
        )
    status = getattr(search_cache, "status", None)
    if not callable(status):
        return normalize_search_cache_status(
            {},
            settings,
            backend=search_cache.__class__.__name__,
            redis_enabled=bool(settings.redis_url),
        )
    try:
        data = status()
    except Exception as exc:
        return normalize_search_cache_status(
            {
                "error_count": 1,
                "last_error": str(exc),
                "last_error_operation": "status",
            },
            settings,
            backend=search_cache.__class__.__name__,
            redis_enabled=bool(settings.redis_url),
        )
    return normalize_search_cache_status(data, settings)


def search_singleflight_summary(search_singleflight: Any | None, settings: Settings) -> dict[str, Any]:
    if search_singleflight is None:
        return normalize_search_singleflight_status(
            {
                "enabled": settings.cache_ttl_seconds > 0,
                "runtime_available": False,
            },
            settings,
        )
    status = getattr(search_singleflight, "singleflight_status", None)
    if not callable(status):
        return normalize_search_singleflight_status(
            {
                "enabled": settings.cache_ttl_seconds > 0,
                "runtime_available": False,
            },
            settings,
        )
    try:
        data = status()
    except Exception as exc:
        return normalize_search_singleflight_status(
            {
                "enabled": settings.cache_ttl_seconds > 0,
                "runtime_available": False,
                "last_error": str(exc),
            },
            settings,
        )
    return normalize_search_singleflight_status(data, settings)


def image_validation_singleflight_summary(
    image_validation_singleflight: Any | None,
    settings: Settings,
) -> dict[str, Any]:
    if image_validation_singleflight is None:
        return normalize_image_validation_singleflight_status(
            {
                "enabled": True,
                "runtime_available": False,
            },
            settings,
        )
    status = getattr(image_validation_singleflight, "image_validation_status", None)
    if not callable(status):
        return normalize_image_validation_singleflight_status(
            {
                "enabled": True,
                "runtime_available": False,
            },
            settings,
        )
    try:
        data = status()
    except Exception as exc:
        return normalize_image_validation_singleflight_status(
            {
                "enabled": True,
                "runtime_available": False,
                "last_error": str(exc),
            },
            settings,
        )
    return normalize_image_validation_singleflight_status(data, settings)


def search_execution_queue_summary(search_execution_gate: Any | None, settings: Settings) -> dict[str, Any]:
    if search_execution_gate is None:
        return normalize_search_execution_queue_status(
            {
                "enabled": False,
                "runtime_available": False,
            },
            settings,
        )
    status = getattr(search_execution_gate, "status", None)
    if not callable(status):
        return normalize_search_execution_queue_status({"enabled": False, "runtime_available": False}, settings)
    try:
        data = status()
    except Exception as exc:
        return normalize_search_execution_queue_status({"enabled": False, "runtime_available": False, "last_error": str(exc)}, settings)
    return normalize_search_execution_queue_status(data, settings)


def image_search_queue_summary(image_search_gate: Any | None, settings: Settings) -> dict[str, Any]:
    if image_search_gate is None:
        return normalize_image_search_queue_status(
            {
                "enabled": False,
                "runtime_available": False,
            },
            settings,
        )
    status = getattr(image_search_gate, "status", None)
    if not callable(status):
        return normalize_image_search_queue_status({"enabled": False, "runtime_available": False}, settings)
    try:
        data = status()
    except Exception as exc:
        return normalize_image_search_queue_status({"enabled": False, "runtime_available": False, "last_error": str(exc)}, settings)
    return normalize_image_search_queue_status(data, settings)


def api_threadpool_summary(status: dict[str, Any] | None, settings: Settings) -> dict[str, Any]:
    required_tokens = required_api_threadpool_tokens(
        settings.search_max_concurrency,
        settings.image_search_max_concurrency,
    )
    summary = dict(status) if isinstance(status, dict) else {}
    configured_tokens = int(settings.api_threadpool_tokens)
    runtime_tokens = summary.get("total_tokens")
    if not is_number(runtime_tokens):
        runtime_tokens = configured_tokens
    ok = configured_tokens >= required_tokens and int(runtime_tokens) >= required_tokens
    if summary and summary.get("ok") is not True:
        ok = False
    return {
        "ok": ok,
        "configured": bool(summary.get("configured")) if summary else None,
        "configured_tokens": configured_tokens,
        "requested_tokens": summary.get("requested_tokens", configured_tokens),
        "runtime_tokens": int(runtime_tokens),
        "previous_tokens": summary.get("previous_tokens"),
        "required_tokens": required_tokens,
        "overhead_tokens": required_tokens - max(0, int(settings.search_max_concurrency)) - max(0, int(settings.image_search_max_concurrency)),
        "search_max_concurrency": settings.search_max_concurrency,
        "image_search_max_concurrency": settings.image_search_max_concurrency,
        "last_error": summary.get("last_error"),
    }


def normalize_search_singleflight_status(data: Any, settings: Settings) -> dict[str, Any]:
    summary = dict(data) if isinstance(data, dict) else {}
    summary.setdefault("enabled", settings.cache_ttl_seconds > 0)
    summary.setdefault("runtime_available", True)
    summary.setdefault("wait_timeout_seconds", settings.cache_miss_wait_seconds)
    if not is_number(summary.get("wait_timeout_seconds")):
        summary["wait_timeout_seconds"] = settings.cache_miss_wait_seconds
    for key in ["in_flight", "wait_events", "wait_timeouts"]:
        if not is_number(summary.get(key)):
            summary[key] = 0
    for key in ["total_wait_ms", "avg_wait_ms", "max_wait_ms"]:
        if not is_number(summary.get(key)):
            summary[key] = 0.0
    summary.setdefault("last_error", None)
    return summary


def normalize_image_validation_singleflight_status(data: Any, settings: Settings) -> dict[str, Any]:
    summary = dict(data) if isinstance(data, dict) else {}
    summary.setdefault("enabled", True)
    summary.setdefault("runtime_available", True)
    summary.setdefault("wait_timeout_seconds", settings.image_validation_wait_seconds)
    summary.setdefault("cache_enabled", settings.image_validation_cache_ttl_seconds > 0)
    summary.setdefault("cache_ttl_seconds", settings.image_validation_cache_ttl_seconds)
    summary.setdefault("cache_max_entries", settings.image_validation_cache_max_entries)
    if not is_number(summary.get("wait_timeout_seconds")):
        summary["wait_timeout_seconds"] = settings.image_validation_wait_seconds
    if not is_number(summary.get("cache_ttl_seconds")):
        summary["cache_ttl_seconds"] = settings.image_validation_cache_ttl_seconds
    for key in [
        "in_flight",
        "wait_events",
        "wait_timeouts",
        "cache_max_entries",
        "cache_entry_count",
        "cache_hits",
        "cache_misses",
        "cache_evictions",
    ]:
        if not is_number(summary.get(key)):
            summary[key] = 0
    for key in ["total_wait_ms", "avg_wait_ms", "max_wait_ms"]:
        if not is_number(summary.get(key)):
            summary[key] = 0.0
    summary.setdefault("last_error", None)
    return summary


def normalize_search_execution_queue_status(data: Any, settings: Settings) -> dict[str, Any]:
    summary = dict(data) if isinstance(data, dict) else {}
    summary.setdefault("enabled", settings.search_max_concurrency > 0)
    summary.setdefault("runtime_available", True)
    summary.setdefault("max_concurrency", settings.search_max_concurrency)
    summary.setdefault("queue_timeout_seconds", settings.search_queue_timeout_seconds)
    if not is_number(summary.get("max_concurrency")):
        summary["max_concurrency"] = settings.search_max_concurrency
    if not is_number(summary.get("queue_timeout_seconds")):
        summary["queue_timeout_seconds"] = settings.search_queue_timeout_seconds
    for key in ["in_flight", "acquired_events", "queue_full_events", "wait_events"]:
        if not is_number(summary.get(key)):
            summary[key] = 0
    for key in ["total_wait_ms", "avg_wait_ms", "max_wait_ms", "last_wait_ms"]:
        if not is_number(summary.get(key)):
            summary[key] = 0.0
    if not is_number(summary.get("available_slots")):
        summary["available_slots"] = (
            max(int(summary["max_concurrency"]) - int(summary["in_flight"]), 0)
            if summary["enabled"]
            else None
        )
    summary.setdefault("last_error", None)
    return summary


def normalize_image_search_queue_status(data: Any, settings: Settings) -> dict[str, Any]:
    summary = dict(data) if isinstance(data, dict) else {}
    summary.setdefault("enabled", settings.image_search_max_concurrency > 0)
    summary.setdefault("runtime_available", True)
    summary.setdefault("max_concurrency", settings.image_search_max_concurrency)
    summary.setdefault("queue_timeout_seconds", settings.image_search_queue_timeout_seconds)
    if not is_number(summary.get("max_concurrency")):
        summary["max_concurrency"] = settings.image_search_max_concurrency
    if not is_number(summary.get("queue_timeout_seconds")):
        summary["queue_timeout_seconds"] = settings.image_search_queue_timeout_seconds
    for key in ["in_flight", "acquired_events", "queue_full_events", "wait_events"]:
        if not is_number(summary.get(key)):
            summary[key] = 0
    for key in ["total_wait_ms", "avg_wait_ms", "max_wait_ms", "last_wait_ms"]:
        if not is_number(summary.get(key)):
            summary[key] = 0.0
    if not is_number(summary.get("available_slots")):
        summary["available_slots"] = (
            max(int(summary["max_concurrency"]) - int(summary["in_flight"]), 0)
            if summary["enabled"]
            else None
        )
    summary.setdefault("last_error", None)
    return summary


def normalize_search_cache_status(
    data: Any,
    settings: Settings,
    backend: str | None = None,
    redis_enabled: bool | None = None,
) -> dict[str, Any]:
    summary = dict(data) if isinstance(data, dict) else {"backend": str(data)}
    summary.setdefault("backend", backend or "unknown")
    summary.setdefault("redis_enabled", bool(settings.redis_url) if redis_enabled is None else redis_enabled)
    summary.setdefault("ttl_seconds", settings.cache_ttl_seconds)
    error_keys = ["get_errors", "set_errors", "decode_errors", "delete_errors", "clear_errors", "lock_errors", "lock_release_errors"]
    for key in error_keys:
        if not is_number(summary.get(key)):
            summary[key] = 0
    if not is_number(summary.get("error_count")):
        summary["error_count"] = sum(int(summary[key]) for key in error_keys)
    for key in ["lock_claims", "lock_contention_events", "lock_wait_events", "lock_wait_timeouts"]:
        if not is_number(summary.get(key)):
            summary[key] = 0
    for key in ["lock_total_wait_ms", "lock_avg_wait_ms", "lock_max_wait_ms"]:
        if not is_number(summary.get(key)):
            summary[key] = 0.0
    if not is_number(summary.get("evictions")):
        summary["evictions"] = 0
    summary.setdefault("redis_socket_timeout_seconds", settings.redis_socket_timeout_seconds)
    summary.setdefault("redis_socket_connect_timeout_seconds", settings.redis_socket_connect_timeout_seconds)
    summary.setdefault("redis_backoff_seconds", settings.redis_failure_backoff_seconds)
    summary.setdefault("redis_backoff_active", False)
    for key in ["redis_backoff_failure_events", "redis_backoff_skipped_operations"]:
        if not is_number(summary.get(key)):
            summary[key] = 0
    if not is_number(summary.get("redis_backoff_remaining_ms")):
        summary["redis_backoff_remaining_ms"] = 0.0
    if summary.get("backend") == "memory" and not is_number(summary.get("max_entries")):
        summary["max_entries"] = settings.cache_max_entries
    summary.setdefault("last_error", None)
    return summary


def summarize_sync_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    run_events = [entry for entry in entries if not entry.get("type") and entry.get("mode")]
    failed_runs = [entry for entry in run_events if is_number(entry.get("failed")) and float(entry["failed"]) > 0]
    product_failures = [entry for entry in entries if entry.get("type") == "sync_product_failed"]
    batch_failures = [entry for entry in entries if entry.get("type") == "sync_batch_failed"]
    lock_busy_failures = [entry for entry in batch_failures if entry.get("action") == "acquire_sync_lock"]
    product_deletes = [
        entry
        for entry in entries
        if entry.get("type") == "sync_product_event" and entry.get("action") == "delete_from_index"
    ]
    image_failures = [entry for entry in entries if entry.get("type") == "image_probe_failed"]
    image_warnings = [entry for entry in entries if entry.get("type") == "image_quality_warning"]
    cache_invalidations = [entry for entry in entries if entry.get("type") == "search_cache_cleared"]
    cache_invalidation_failures = [entry for entry in entries if entry.get("type") == "search_cache_clear_failed"]
    failed_actions = Counter(str(entry.get("action") or "unknown") for entry in product_failures)
    batch_failed_actions = Counter(str(entry.get("action") or "unknown") for entry in batch_failures)
    return {
        "sample_size": len(entries),
        "run_events": len(run_events),
        "failed_run_events": len(failed_runs),
        "product_failed_events": len(product_failures),
        "batch_failed_events": len(batch_failures),
        "sync_lock_busy_events": len(lock_busy_failures),
        "product_delete_events": len(product_deletes),
        "cache_invalidation_events": len(cache_invalidations),
        "cache_invalidation_failed_events": len(cache_invalidation_failures),
        "image_probe_failed_events": len(image_failures),
        "image_quality_warning_events": len(image_warnings),
        "failed_action_counts": dict(sorted(failed_actions.items())),
        "batch_failed_action_counts": dict(sorted(batch_failed_actions.items())),
        "recent_failed_product_ids": [
            str(entry.get("product_id")) for entry in product_failures[-20:] if entry.get("product_id")
        ],
    }


def file_summary(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "bytes": path.stat().st_size if exists else 0,
    }


def disk_summary(path: Path) -> dict[str, Any]:
    target = path if path.exists() else first_existing_parent(path)
    total, used, free = shutil.disk_usage(target)
    return {
        "path": str(target),
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_percent": round((used / total) * 100, 1) if total else None,
    }


def process_summary() -> dict[str, Any]:
    data = {
        "pid": os.getpid(),
        "instance_id": api_instance_id(),
        "uptime_seconds": round(time.time() - PROCESS_STARTED_AT, 1),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "psutil_available": False,
        "cpu_percent": None,
        "memory_rss_bytes": None,
        "memory_vms_bytes": None,
        "thread_count": None,
    }
    try:
        import psutil
    except ImportError:
        return data
    process = psutil.Process(os.getpid())
    memory = process.memory_info()
    data.update(
        {
            "psutil_available": True,
            "cpu_percent": process.cpu_percent(interval=None),
            "memory_rss_bytes": memory.rss,
            "memory_vms_bytes": memory.vms,
            "thread_count": process.num_threads(),
        }
    )
    return data


def system_summary() -> dict[str, Any]:
    data = {
        "hostname": platform.node(),
        "python_executable": sys.executable,
        "cpu_count": os.cpu_count(),
        "psutil_available": False,
        "system_cpu_percent": None,
        "memory_total_bytes": None,
        "memory_available_bytes": None,
        "memory_used_percent": None,
    }
    try:
        import psutil
    except ImportError:
        return data
    memory = psutil.virtual_memory()
    data.update(
        {
            "psutil_available": True,
            "system_cpu_percent": psutil.cpu_percent(interval=None),
            "memory_total_bytes": memory.total,
            "memory_available_bytes": memory.available,
            "memory_used_percent": memory.percent,
        }
    )
    return data


def first_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((percent / 100) * (len(ordered) - 1))))
    return round(ordered[index], 1)


def is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True
