from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.url_safety import is_non_public_host


REQUIRED_TRAFFIC_MODES = {"text", "image", "mixed"}
MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE = 50
MIN_MIXED_TRAFFIC_MALL_RESPONSE_DISTRIBUTION_RATIO = 0.5
MAX_MALL_IDENTITY_DISTRIBUTION_DETAIL_IDS = 50
MIN_LOAD_IMAGE_INPUTS = 3
DEFAULT_SERVER_WAIT_AVG_TO_P95_RATIO = 0.2
DEFAULT_SERVER_WAIT_MIN_AVG_MS = 250.0
DEFAULT_REQUEST_TIMEOUT_MIN_SECONDS = 10.0
MAX_REQUEST_TIMEOUT_TO_P99_RATIO = 3.0
DEFAULT_MIN_RPS_TO_P95_CAPACITY_RATIO = 0.25
DEFAULT_MIN_RPS_FLOOR = 1.0
DEFAULT_MAX_PROCESS_RSS_GROWTH_MB = 512.0
BYTES_PER_MIB = 1024 * 1024
SETTLED_SERVER_IN_FLIGHT_FIELDS = [
    "singleflight_in_flight",
    "search_queue_in_flight",
    "image_queue_in_flight",
    "qwen_query_vector_in_flight",
]
SERVER_RESOURCE_PERCENT_LIMITS = {
    "system_memory_used_percent": 85.0,
    "disk_used_percent": 85.0,
}
SERVER_WAIT_AVG_DELTA_FIELDS = [
    ("cache_lock", "cache_lock_wait_events", "cache_lock_total_wait_ms"),
    ("singleflight", "singleflight_wait_events", "singleflight_total_wait_ms"),
    ("qwen_query_vector", "qwen_query_vector_wait_events", "qwen_query_vector_total_wait_ms"),
    ("search_queue", "search_queue_wait_events", "search_queue_total_wait_ms"),
    ("image_queue", "image_queue_wait_events", "image_queue_total_wait_ms"),
]
REQUIRED_SERVER_METRIC_DELTA_PROBLEMS = [
    "server_metrics.delta.engine_search_attempts",
    "server_metrics.delta.engine_adaptive_refetches",
    "server_metrics.delta.engine_adaptive_refetch_searches",
    "server_metrics.delta.result_mall_id_mismatch_events",
    "server_metrics.delta.result_mall_id_mismatch_count",
    "server_metrics.delta.backend_marqo_request_attempts",
    "server_metrics.delta.backend_marqo_connections_opened",
    "server_metrics.delta.backend_marqo_connection_reuses",
    "server_metrics.delta.backend_marqo_idle_reconnects",
    "server_metrics.delta.backend_marqo_stale_reconnects",
    "server_metrics.delta.backend_marqo_error_responses",
    "server_metrics.delta.backend_marqo_connection_close_responses",
    "server_metrics.delta.backend_marqo_gzip_responses",
    "server_metrics.delta.backend_marqo_total_elapsed_ms",
    "server_metrics.delta.backend_marqo_total_request_body_bytes",
    "server_metrics.delta.backend_marqo_total_response_body_bytes",
    "server_metrics.delta.backend_marqo_total_decoded_response_body_bytes",
    "server_metrics.delta.rate_limited_events",
    "server_metrics.delta.rate_limit_fallback_events",
    "server_metrics.delta.cache_error_count",
    "server_metrics.delta.cache_clear_errors",
    "server_metrics.delta.cache_lock_claims",
    "server_metrics.delta.cache_lock_contention_events",
    "server_metrics.delta.cache_lock_errors",
    "server_metrics.delta.cache_lock_release_errors",
    "server_metrics.delta.cache_lock_wait_events",
    "server_metrics.delta.cache_lock_wait_timeouts",
    "server_metrics.delta.cache_lock_total_wait_ms",
    "server_metrics.delta.singleflight_wait_events",
    "server_metrics.delta.singleflight_wait_timeouts",
    "server_metrics.delta.singleflight_total_wait_ms",
    "server_metrics.delta.search_queue_full_events",
    "server_metrics.delta.search_queue_wait_events",
    "server_metrics.delta.search_queue_total_wait_ms",
    "server_metrics.delta.image_queue_full_events",
    "server_metrics.delta.image_queue_wait_events",
    "server_metrics.delta.image_queue_total_wait_ms",
    "server_metrics.delta.search_log_write_errors",
    "server_metrics.delta.error_log_write_errors",
]
SEARCH_ENGINE_SERVER_METRIC_FIELDS = [
    "engine_search_attempts",
    "engine_adaptive_refetches",
    "engine_adaptive_refetch_searches",
    "engine_underfilled_after_max_candidates_events",
    "engine_average_search_attempts",
    "engine_max_search_attempts",
    "engine_average_final_candidate_limit",
    "engine_max_final_candidate_limit",
]
ZERO_DELTA_SERVER_METRIC_FIELDS = [
    "rate_limited_events",
    "rate_limit_fallback_events",
    "cache_error_count",
    "cache_clear_errors",
    "cache_lock_errors",
    "cache_lock_release_errors",
    "cache_lock_wait_timeouts",
    "singleflight_wait_timeouts",
    "backend_marqo_error_responses",
    "backend_marqo_connection_close_responses",
    "search_queue_full_events",
    "image_queue_full_events",
    "engine_underfilled_after_max_candidates_events",
    "result_mall_id_mismatch_events",
    "result_mall_id_mismatch_count",
    "search_log_write_errors",
    "error_log_write_errors",
]
BACKEND_CIRCUIT_ZERO_DELTA_SERVER_METRIC_FIELDS = [
    "backend_marqo_circuit_open_events",
    "backend_marqo_circuit_short_circuits",
    "backend_qwen_circuit_open_events",
    "backend_qwen_circuit_short_circuits",
]
BACKEND_MARQO_SERVER_METRIC_FIELDS = [
    "backend_marqo_request_attempts",
    "backend_marqo_connections_opened",
    "backend_marqo_connection_reuses",
    "backend_marqo_idle_reconnects",
    "backend_marqo_stale_reconnects",
    "backend_marqo_error_responses",
    "backend_marqo_connection_close_responses",
    "backend_marqo_gzip_responses",
    "backend_marqo_total_elapsed_ms",
    "backend_marqo_avg_elapsed_ms",
    "backend_marqo_max_elapsed_ms",
    "backend_marqo_last_elapsed_ms",
    "backend_marqo_total_request_body_bytes",
    "backend_marqo_max_request_body_bytes",
    "backend_marqo_last_request_body_bytes",
    "backend_marqo_total_response_body_bytes",
    "backend_marqo_max_response_body_bytes",
    "backend_marqo_last_response_body_bytes",
    "backend_marqo_total_decoded_response_body_bytes",
    "backend_marqo_max_decoded_response_body_bytes",
    "backend_marqo_last_decoded_response_body_bytes",
]
BACKEND_QWEN_SERVER_METRIC_FIELDS = [
    "backend_qwen_request_attempts",
    "backend_qwen_connections_opened",
    "backend_qwen_connection_reuses",
    "backend_qwen_idle_reconnects",
    "backend_qwen_stale_reconnects",
    "backend_qwen_error_responses",
    "backend_qwen_connection_close_responses",
    "backend_qwen_gzip_responses",
    "backend_qwen_total_elapsed_ms",
    "backend_qwen_avg_elapsed_ms",
    "backend_qwen_max_elapsed_ms",
    "backend_qwen_last_elapsed_ms",
    "backend_qwen_total_request_body_bytes",
    "backend_qwen_max_request_body_bytes",
    "backend_qwen_last_request_body_bytes",
    "backend_qwen_total_response_body_bytes",
    "backend_qwen_max_response_body_bytes",
    "backend_qwen_last_response_body_bytes",
    "backend_qwen_total_decoded_response_body_bytes",
    "backend_qwen_max_decoded_response_body_bytes",
    "backend_qwen_last_decoded_response_body_bytes",
]
BACKEND_QWEN_SERVER_METRIC_DELTA_FIELDS = [
    "backend_qwen_request_attempts",
    "backend_qwen_connections_opened",
    "backend_qwen_connection_reuses",
    "backend_qwen_idle_reconnects",
    "backend_qwen_stale_reconnects",
    "backend_qwen_error_responses",
    "backend_qwen_connection_close_responses",
    "backend_qwen_gzip_responses",
    "backend_qwen_total_elapsed_ms",
    "backend_qwen_total_request_body_bytes",
    "backend_qwen_total_response_body_bytes",
    "backend_qwen_total_decoded_response_body_bytes",
]
BACKEND_QWEN_ZERO_DELTA_SERVER_METRIC_FIELDS = [
    "backend_qwen_error_responses",
    "backend_qwen_connection_close_responses",
]
OPTIONAL_ZERO_DELTA_SERVER_METRIC_FIELDS = [
    "backend_marqo_retry_after_responses",
    "backend_qwen_retry_after_responses",
    "rate_limit_redis_backoff_failure_events",
    "rate_limit_redis_backoff_skipped_operations",
    "cache_redis_backoff_failure_events",
    "cache_redis_backoff_skipped_operations",
]
BACKEND_MARQO_GZIP_ZERO_PROBLEM = "server_metrics.delta.backend_marqo_gzip_responses_zero"
BACKEND_MARQO_RESPONSE_BODY_NOT_BELOW_DECODED_PROBLEM = (
    "server_metrics.delta.backend_marqo_response_body_bytes_not_below_decoded"
)
BACKEND_MARQO_REQUEST_ATTEMPTS_ZERO_PROBLEM = "server_metrics.delta.backend_marqo_request_attempts_zero"
BACKEND_QWEN_REQUEST_ATTEMPTS_ZERO_PROBLEM = "server_metrics.delta.backend_qwen_request_attempts_zero"
BACKEND_MARQO_REQUEST_ATTEMPTS_BELOW_UNIQUE_PROBLEM = (
    "server_metrics.delta.backend_marqo_request_attempts_below_unique_requests"
)
BACKEND_QWEN_REQUEST_ATTEMPTS_BELOW_UNIQUE_IMAGE_PROBLEM = (
    "server_metrics.delta.backend_qwen_request_attempts_below_unique_image_inputs"
)
REQUIRED_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES = 100
REQUIRED_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES = 30
QWEN_QUERY_VECTOR_SERVER_METRIC_FIELDS = [
    "qwen_query_vector_runtime_entries",
    "qwen_query_vector_runtime_text_entries",
    "qwen_query_vector_runtime_image_entries",
    "qwen_query_vector_runtime_max_entries",
    "qwen_query_vector_runtime_text_max_entries",
    "qwen_query_vector_runtime_image_max_entries",
    "qwen_query_vector_in_flight",
    "qwen_query_vector_wait_timeout_seconds",
    "qwen_query_vector_wait_events",
    "qwen_query_vector_wait_timeouts",
    "qwen_query_vector_total_wait_ms",
    "qwen_query_vector_avg_wait_ms",
    "qwen_query_vector_max_wait_ms",
]
QWEN_QUERY_VECTOR_DELTA_SERVER_METRIC_FIELDS = [
    "qwen_query_vector_wait_events",
    "qwen_query_vector_wait_timeouts",
    "qwen_query_vector_total_wait_ms",
]
QWEN_QUERY_VECTOR_ZERO_DELTA_SERVER_METRIC_FIELDS = [
    "qwen_query_vector_wait_timeouts",
]
API_THREADPOOL_SERVER_METRIC_FIELDS = [
    "api_threadpool_ok",
    "api_threadpool_configured_tokens",
    "api_threadpool_runtime_tokens",
    "api_threadpool_required_tokens",
]
COMPARISON_TARGET_PROBLEMS = {
    "base_url": "comparison.base_url",
    "mall_id": "comparison.mall_id",
    "origin": "comparison.origin",
    "image_input": "comparison.image_input",
    "request_profile": "comparison.request_profile",
}
RUNTIME_IDENTITY_FIELDS = [
    "engine_backend",
    "engine_index",
    "marqo_model",
    "embedding_backend",
    "rate_limit_backend",
    "rate_limit_redis_enabled",
    "cache_backend",
    "cache_redis_enabled",
    "cache_ttl_seconds",
    "singleflight_enabled",
    "singleflight_wait_timeout_seconds",
    "search_queue_enabled",
    "search_queue_max_concurrency",
    "search_queue_timeout_seconds",
    "image_queue_enabled",
    "image_queue_max_concurrency",
    "image_queue_timeout_seconds",
    "api_threadpool_ok",
    "api_threadpool_configured_tokens",
    "api_threadpool_runtime_tokens",
    "api_threadpool_required_tokens",
]
QWEN_RUNTIME_IDENTITY_FIELDS = [
    "qwen_model",
    "qwen_embedding_dimensions",
    "qwen_query_vector_runtime_max_entries",
    "qwen_query_vector_runtime_text_max_entries",
    "qwen_query_vector_runtime_image_max_entries",
    "qwen_query_vector_mixed_parallelism",
]
OPERATIONAL_TARGET_URL_PROBLEM_NAMES = {
    ("base_url", "non_local"): "base_url_non_local",
    ("origin", "https"): "origin_https",
}


def external_embedding_backend(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"qwen", "gemini"}


def operational_target_url_problem_name(field: str, problem: str) -> str:
    return OPERATIONAL_TARGET_URL_PROBLEM_NAMES.get((field, problem), f"{field}_{problem}")


def load_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("load report JSON root must be an object")
    return data


def summarize_load_report(report: dict[str, Any]) -> dict[str, Any]:
    latency = report.get("latency_ms") or {}
    mode_counts = report.get("mode_counts") or {}
    image_source_ok, image_source_problems = check_mixed_traffic_image_source(report)
    server_metrics = report.get("server_metrics") or {}
    server_metrics_before = (server_metrics.get("before") or {}).get("snapshot") or {}
    server_metrics_after = (server_metrics.get("after") or {}).get("snapshot") or {}
    admin_source_coverage = (
        server_metrics.get("admin_metrics_source_coverage")
        if isinstance(server_metrics.get("admin_metrics_source_coverage"), dict)
        else {}
    )
    response_contract = report.get("response_contract") or {}
    client_transport = report.get("client_transport") or {}
    client_search_requests = client_transport.get("search_requests") if isinstance(client_transport, dict) else {}
    response_engine = summarize_response_engine(response_contract)
    response_shape = summarize_response_contract_shape(response_contract)
    response_mall_identity = summarize_response_mall_identity(response_contract, report.get("mall_id"))
    mall_identity = summarize_mall_identity(report)
    query_type_counts = response_contract.get("query_type_counts") or {}
    query_type_coverage = summarize_query_type_coverage(response_contract, mode_counts, report.get("thresholds") or {})
    query_type_latency = summarize_query_type_latency(report)
    api_instance_coverage = report.get("api_instance_coverage") if isinstance(report.get("api_instance_coverage"), dict) else {}
    target_validation = report.get("target_validation") or {}
    target_validation_matches = (
        target_validation.get("base_url") == report.get("base_url")
        and (target_validation.get("origin") or None) == (report.get("origin") or None)
    )
    summary = {
        "ok": report.get("ok"),
        "base_url": report.get("base_url"),
        "mall_id": report.get("mall_id"),
        "origin": report.get("origin"),
        "target_validation_ok": target_validation.get("ok") is True and target_validation_matches,
        "target_validation_matches": target_validation_matches,
        "target_validation_error": target_validation.get("error"),
        "api_server_count": int(report.get("api_server_count", 0) or 0),
        "scenario": report.get("scenario"),
        "active_users": int(report.get("active_users", 0) or 0),
        "requests": int(report.get("requests", 0) or 0),
        "concurrency": int(report.get("concurrency", 0) or 0),
        "mode": report.get("mode"),
        "mode_counts": {mode: int(mode_counts.get(mode, 0) or 0) for mode in sorted(REQUIRED_TRAFFIC_MODES)},
        "error_rate": report.get("error_rate"),
        "requests_per_second": report.get("requests_per_second"),
        "p95_ms": latency.get("p95"),
        "p99_ms": latency.get("p99"),
        "thresholds": report.get("thresholds") or {},
        "request_profile": report.get("request_profile") or {},
        "api_instance_coverage": api_instance_coverage,
        "api_instance_coverage_ok": api_instance_coverage.get("ok"),
        "api_instance_coverage_problems": api_instance_coverage.get("problems") or [],
        "api_instance_counts": api_instance_coverage.get("api_instance_counts") or {},
        "distinct_api_instance_count": parse_int_value(api_instance_coverage.get("distinct_api_instance_count"), 0),
        "required_distinct_api_instances": parse_int_value(
            api_instance_coverage.get("required_distinct_api_instances"),
            0,
        ),
        "api_instance_missing_header_count": parse_int_value(api_instance_coverage.get("missing_header_count"), 0),
        "image_source_ok": image_source_ok,
        "image_source_problems": image_source_problems,
        "image_input": report.get("image_input") or {},
        "response_contract_ok": (
            response_contract.get("ok") is True
            and response_engine["ok"]
            and response_shape["ok"]
            and response_mall_identity["ok"]
            and query_type_coverage["ok"]
        ),
        "response_engine_ok": response_engine["ok"],
        "response_engine": response_engine,
        "response_shape_ok": response_shape["ok"],
        "response_shape_problems": response_shape["problems"],
        "response_shape": response_shape,
        "response_mall_identity_ok": response_mall_identity["ok"],
        "response_mall_identity_problems": response_mall_identity["problems"],
        "response_mall_identity": response_mall_identity,
        "mall_identity_ok": mall_identity["ok"],
        "mall_identity_problems": mall_identity["problems"],
        "mall_identity": mall_identity,
        "successful_responses": int(
            response_contract.get("valid_successful_responses") or response_contract.get("successful_responses") or 0
        ),
        "image_successful_responses": int(query_type_counts.get("image", 0) or 0)
        + int(query_type_counts.get("text_image", 0) or 0),
        "response_contract_query_type_counts": query_type_counts,
        "response_contract_expected_query_type_counts": response_contract.get("expected_query_type_counts") or {},
        "response_contract_unexpected_query_type_count": parse_int_value(response_contract.get("unexpected_query_type_count"), 0),
        "query_type_coverage_ok": query_type_coverage["ok"],
        "query_type_coverage_problems": query_type_coverage["problems"],
        "query_type_coverage": query_type_coverage,
        "query_type_latency_ok": query_type_latency["ok"],
        "query_type_latency_problems": query_type_latency["problems"],
        "query_type_latency": query_type_latency,
        "expected_query_type_latency_ms": query_type_latency["expected_query_type_latency_ms"],
        "response_query_type_latency_ms": query_type_latency["response_query_type_latency_ms"],
        "server_metrics_ok": server_metrics.get("ok"),
        "admin_metrics_source_coverage": admin_source_coverage,
        "admin_metrics_source_coverage_ok": admin_source_coverage.get("ok"),
        "admin_metrics_source_coverage_problems": admin_source_coverage.get("problems") or [],
        "admin_metrics_successful_source_count": parse_int_value(
            admin_source_coverage.get("successful_source_count"),
            0,
        ),
        "admin_metrics_distinct_instance_count": parse_int_value(
            admin_source_coverage.get("distinct_instance_count"),
            0,
        ),
        "admin_metrics_instance_ids": admin_source_coverage.get("instance_ids") or [],
        "engine_backend": server_metrics_after.get("engine_backend"),
        "engine_index": server_metrics_after.get("engine_index"),
        "marqo_model": server_metrics_after.get("marqo_model"),
        "embedding_backend": server_metrics_after.get("embedding_backend"),
        "qwen_model": server_metrics_after.get("qwen_model"),
        "qwen_embedding_dimensions": server_metrics_after.get("qwen_embedding_dimensions"),
        "qwen_query_vector_runtime_entries": server_metrics_after.get("qwen_query_vector_runtime_entries"),
        "qwen_query_vector_runtime_text_entries": server_metrics_after.get("qwen_query_vector_runtime_text_entries"),
        "qwen_query_vector_runtime_image_entries": server_metrics_after.get("qwen_query_vector_runtime_image_entries"),
        "qwen_query_vector_runtime_max_entries": server_metrics_after.get("qwen_query_vector_runtime_max_entries"),
        "qwen_query_vector_runtime_text_max_entries": server_metrics_after.get(
            "qwen_query_vector_runtime_text_max_entries"
        ),
        "qwen_query_vector_runtime_image_max_entries": server_metrics_after.get(
            "qwen_query_vector_runtime_image_max_entries"
        ),
        "qwen_query_vector_mixed_parallelism": server_metrics_after.get("qwen_query_vector_mixed_parallelism"),
        "qwen_query_vector_in_flight": server_metrics_after.get("qwen_query_vector_in_flight"),
        "qwen_query_vector_wait_timeout_seconds": server_metrics_after.get("qwen_query_vector_wait_timeout_seconds"),
        "qwen_query_vector_wait_events": server_metrics_after.get("qwen_query_vector_wait_events"),
        "qwen_query_vector_wait_timeouts": server_metrics_after.get("qwen_query_vector_wait_timeouts"),
        "qwen_query_vector_total_wait_ms": server_metrics_after.get("qwen_query_vector_total_wait_ms"),
        "qwen_query_vector_avg_wait_ms": server_metrics_after.get("qwen_query_vector_avg_wait_ms"),
        "qwen_query_vector_max_wait_ms": server_metrics_after.get("qwen_query_vector_max_wait_ms"),
        "engine_search_attempts": server_metrics_after.get("engine_search_attempts"),
        "engine_adaptive_refetches": server_metrics_after.get("engine_adaptive_refetches"),
        "engine_adaptive_refetch_searches": server_metrics_after.get("engine_adaptive_refetch_searches"),
        "engine_underfilled_after_max_candidates_events": server_metrics_after.get(
            "engine_underfilled_after_max_candidates_events"
        ),
        "engine_average_search_attempts": server_metrics_after.get("engine_average_search_attempts"),
        "engine_max_search_attempts": server_metrics_after.get("engine_max_search_attempts"),
        "engine_average_final_candidate_limit": server_metrics_after.get("engine_average_final_candidate_limit"),
        "engine_max_final_candidate_limit": server_metrics_after.get("engine_max_final_candidate_limit"),
        "backend_marqo_request_attempts": server_metrics_after.get("backend_marqo_request_attempts"),
        "backend_marqo_connections_opened": server_metrics_after.get("backend_marqo_connections_opened"),
        "backend_marqo_connection_reuses": server_metrics_after.get("backend_marqo_connection_reuses"),
        "backend_marqo_idle_reconnects": server_metrics_after.get("backend_marqo_idle_reconnects"),
        "backend_marqo_stale_reconnects": server_metrics_after.get("backend_marqo_stale_reconnects"),
        "backend_marqo_error_responses": server_metrics_after.get("backend_marqo_error_responses"),
        "backend_marqo_connection_close_responses": server_metrics_after.get("backend_marqo_connection_close_responses"),
        "backend_marqo_gzip_responses": server_metrics_after.get("backend_marqo_gzip_responses"),
        "backend_marqo_retry_after_responses": server_metrics_after.get("backend_marqo_retry_after_responses"),
        "backend_marqo_max_retry_after_seconds": server_metrics_after.get("backend_marqo_max_retry_after_seconds"),
        "backend_marqo_last_retry_after_seconds": server_metrics_after.get("backend_marqo_last_retry_after_seconds"),
        "backend_marqo_total_elapsed_ms": server_metrics_after.get("backend_marqo_total_elapsed_ms"),
        "backend_marqo_avg_elapsed_ms": server_metrics_after.get("backend_marqo_avg_elapsed_ms"),
        "backend_marqo_max_elapsed_ms": server_metrics_after.get("backend_marqo_max_elapsed_ms"),
        "backend_marqo_last_elapsed_ms": server_metrics_after.get("backend_marqo_last_elapsed_ms"),
        "backend_marqo_total_request_body_bytes": server_metrics_after.get("backend_marqo_total_request_body_bytes"),
        "backend_marqo_max_request_body_bytes": server_metrics_after.get("backend_marqo_max_request_body_bytes"),
        "backend_marqo_last_request_body_bytes": server_metrics_after.get("backend_marqo_last_request_body_bytes"),
        "backend_marqo_total_response_body_bytes": server_metrics_after.get("backend_marqo_total_response_body_bytes"),
        "backend_marqo_max_response_body_bytes": server_metrics_after.get("backend_marqo_max_response_body_bytes"),
        "backend_marqo_last_response_body_bytes": server_metrics_after.get("backend_marqo_last_response_body_bytes"),
        "backend_marqo_total_decoded_response_body_bytes": server_metrics_after.get(
            "backend_marqo_total_decoded_response_body_bytes"
        ),
        "backend_marqo_max_decoded_response_body_bytes": server_metrics_after.get(
            "backend_marqo_max_decoded_response_body_bytes"
        ),
        "backend_marqo_last_decoded_response_body_bytes": server_metrics_after.get(
            "backend_marqo_last_decoded_response_body_bytes"
        ),
        "backend_marqo_circuit_open_events": server_metrics_after.get("backend_marqo_circuit_open_events"),
        "backend_marqo_circuit_short_circuits": server_metrics_after.get("backend_marqo_circuit_short_circuits"),
        "backend_marqo_circuit_recovery_events": server_metrics_after.get("backend_marqo_circuit_recovery_events"),
        "backend_qwen_request_attempts": server_metrics_after.get("backend_qwen_request_attempts"),
        "backend_qwen_connections_opened": server_metrics_after.get("backend_qwen_connections_opened"),
        "backend_qwen_connection_reuses": server_metrics_after.get("backend_qwen_connection_reuses"),
        "backend_qwen_idle_reconnects": server_metrics_after.get("backend_qwen_idle_reconnects"),
        "backend_qwen_stale_reconnects": server_metrics_after.get("backend_qwen_stale_reconnects"),
        "backend_qwen_error_responses": server_metrics_after.get("backend_qwen_error_responses"),
        "backend_qwen_connection_close_responses": server_metrics_after.get("backend_qwen_connection_close_responses"),
        "backend_qwen_gzip_responses": server_metrics_after.get("backend_qwen_gzip_responses"),
        "backend_qwen_retry_after_responses": server_metrics_after.get("backend_qwen_retry_after_responses"),
        "backend_qwen_max_retry_after_seconds": server_metrics_after.get("backend_qwen_max_retry_after_seconds"),
        "backend_qwen_last_retry_after_seconds": server_metrics_after.get("backend_qwen_last_retry_after_seconds"),
        "backend_qwen_total_elapsed_ms": server_metrics_after.get("backend_qwen_total_elapsed_ms"),
        "backend_qwen_avg_elapsed_ms": server_metrics_after.get("backend_qwen_avg_elapsed_ms"),
        "backend_qwen_max_elapsed_ms": server_metrics_after.get("backend_qwen_max_elapsed_ms"),
        "backend_qwen_last_elapsed_ms": server_metrics_after.get("backend_qwen_last_elapsed_ms"),
        "backend_qwen_total_request_body_bytes": server_metrics_after.get("backend_qwen_total_request_body_bytes"),
        "backend_qwen_max_request_body_bytes": server_metrics_after.get("backend_qwen_max_request_body_bytes"),
        "backend_qwen_last_request_body_bytes": server_metrics_after.get("backend_qwen_last_request_body_bytes"),
        "backend_qwen_total_response_body_bytes": server_metrics_after.get("backend_qwen_total_response_body_bytes"),
        "backend_qwen_max_response_body_bytes": server_metrics_after.get("backend_qwen_max_response_body_bytes"),
        "backend_qwen_last_response_body_bytes": server_metrics_after.get("backend_qwen_last_response_body_bytes"),
        "backend_qwen_total_decoded_response_body_bytes": server_metrics_after.get(
            "backend_qwen_total_decoded_response_body_bytes"
        ),
        "backend_qwen_max_decoded_response_body_bytes": server_metrics_after.get(
            "backend_qwen_max_decoded_response_body_bytes"
        ),
        "backend_qwen_last_decoded_response_body_bytes": server_metrics_after.get(
            "backend_qwen_last_decoded_response_body_bytes"
        ),
        "backend_qwen_circuit_open_events": server_metrics_after.get("backend_qwen_circuit_open_events"),
        "backend_qwen_circuit_short_circuits": server_metrics_after.get("backend_qwen_circuit_short_circuits"),
        "backend_qwen_circuit_recovery_events": server_metrics_after.get("backend_qwen_circuit_recovery_events"),
        "result_mall_id_mismatch_events": server_metrics_after.get("result_mall_id_mismatch_events"),
        "result_mall_id_mismatch_count": server_metrics_after.get("result_mall_id_mismatch_count"),
        "rate_limit_backend": server_metrics_after.get("rate_limit_backend"),
        "rate_limit_redis_enabled": server_metrics_after.get("rate_limit_redis_enabled"),
        "cache_backend": server_metrics_after.get("cache_backend"),
        "cache_redis_enabled": server_metrics_after.get("cache_redis_enabled"),
        "cache_ttl_seconds": server_metrics_after.get("cache_ttl_seconds"),
        "cache_lock_claims": server_metrics_after.get("cache_lock_claims"),
        "cache_lock_contention_events": server_metrics_after.get("cache_lock_contention_events"),
        "cache_lock_errors": server_metrics_after.get("cache_lock_errors"),
        "cache_lock_release_errors": server_metrics_after.get("cache_lock_release_errors"),
        "cache_lock_wait_events": server_metrics_after.get("cache_lock_wait_events"),
        "cache_lock_wait_timeouts": server_metrics_after.get("cache_lock_wait_timeouts"),
        "cache_lock_total_wait_ms": server_metrics_after.get("cache_lock_total_wait_ms"),
        "cache_lock_avg_wait_ms": server_metrics_after.get("cache_lock_avg_wait_ms"),
        "cache_lock_max_wait_ms": server_metrics_after.get("cache_lock_max_wait_ms"),
        "server_metrics_delta": server_metrics.get("delta") or {},
        "server_metrics_coverage_ok": server_metrics.get("coverage_ok"),
        "server_metrics_run_log_coverage": server_metrics.get("run_log_coverage") or {},
        "client_transport": {
            "connection_reuse": client_transport.get("connection_reuse") if isinstance(client_transport, dict) else None,
            "search_requests": client_search_requests if isinstance(client_search_requests, dict) else {},
        },
        "singleflight_enabled": server_metrics_after.get("singleflight_enabled"),
        "singleflight_in_flight": server_metrics_after.get("singleflight_in_flight"),
        "singleflight_wait_timeout_seconds": server_metrics_after.get("singleflight_wait_timeout_seconds"),
        "singleflight_wait_events": server_metrics_after.get("singleflight_wait_events"),
        "singleflight_wait_timeouts": server_metrics_after.get("singleflight_wait_timeouts"),
        "singleflight_total_wait_ms": server_metrics_after.get("singleflight_total_wait_ms"),
        "singleflight_avg_wait_ms": server_metrics_after.get("singleflight_avg_wait_ms"),
        "singleflight_max_wait_ms": server_metrics_after.get("singleflight_max_wait_ms"),
        "search_queue_enabled": server_metrics_after.get("search_queue_enabled"),
        "search_queue_max_concurrency": server_metrics_after.get("search_queue_max_concurrency"),
        "search_queue_timeout_seconds": server_metrics_after.get("search_queue_timeout_seconds"),
        "search_queue_in_flight": server_metrics_after.get("search_queue_in_flight"),
        "search_queue_full_events": server_metrics_after.get("search_queue_full_events"),
        "search_queue_wait_events": server_metrics_after.get("search_queue_wait_events"),
        "search_queue_total_wait_ms": server_metrics_after.get("search_queue_total_wait_ms"),
        "search_queue_avg_wait_ms": server_metrics_after.get("search_queue_avg_wait_ms"),
        "search_queue_max_wait_ms": server_metrics_after.get("search_queue_max_wait_ms"),
        "image_queue_enabled": server_metrics_after.get("image_queue_enabled"),
        "image_queue_max_concurrency": server_metrics_after.get("image_queue_max_concurrency"),
        "image_queue_timeout_seconds": server_metrics_after.get("image_queue_timeout_seconds"),
        "image_queue_in_flight": server_metrics_after.get("image_queue_in_flight"),
        "image_queue_full_events": server_metrics_after.get("image_queue_full_events"),
        "image_queue_wait_events": server_metrics_after.get("image_queue_wait_events"),
        "image_queue_total_wait_ms": server_metrics_after.get("image_queue_total_wait_ms"),
        "image_queue_avg_wait_ms": server_metrics_after.get("image_queue_avg_wait_ms"),
        "image_queue_max_wait_ms": server_metrics_after.get("image_queue_max_wait_ms"),
        "api_threadpool_ok": server_metrics_after.get("api_threadpool_ok"),
        "api_threadpool_configured_tokens": server_metrics_after.get("api_threadpool_configured_tokens"),
        "api_threadpool_runtime_tokens": server_metrics_after.get("api_threadpool_runtime_tokens"),
        "api_threadpool_required_tokens": server_metrics_after.get("api_threadpool_required_tokens"),
        "process_memory_rss_bytes_before": server_metrics_before.get("process_memory_rss_bytes"),
        "process_memory_rss_bytes": server_metrics_after.get("process_memory_rss_bytes"),
        "process_memory_rss_growth_mb": process_rss_growth_mb(server_metrics_before, server_metrics_after),
        "system_memory_used_percent": server_metrics_after.get("system_memory_used_percent"),
        "disk_used_percent": server_metrics_after.get("disk_used_percent"),
        "search_log_write_errors": server_metrics_after.get("search_log_write_errors"),
        "error_log_write_errors": server_metrics_after.get("error_log_write_errors"),
    }
    summary["server_metrics_missing"] = check_scale_server_metrics(summary)
    summary["client_transport_missing"] = check_client_transport(summary)
    return summary


def api_instance_coverage_problems(summary: dict[str, Any]) -> list[str]:
    api_server_count = parse_int_value(summary.get("api_server_count"), 0)
    if api_server_count < 2:
        return []
    coverage = summary.get("api_instance_coverage") if isinstance(summary.get("api_instance_coverage"), dict) else {}
    if not coverage:
        return ["api_instance_coverage"]
    problems = [str(problem) for problem in (coverage.get("problems") or []) if str(problem or "").strip()]
    if coverage.get("ok") is not True and not problems:
        problems.append("api_instance_coverage.ok")
    required = max(2, parse_int_value(coverage.get("required_distinct_api_instances"), api_server_count))
    distinct = parse_int_value(coverage.get("distinct_api_instance_count"), 0)
    if distinct < required and "api_instance.distinct_count" not in problems:
        problems.append("api_instance.distinct_count")
    if parse_int_value(coverage.get("missing_header_count"), 0) > 0 and "api_instance.missing_header" not in problems:
        problems.append("api_instance.missing_header")
    return sorted(set(problems))


def admin_metrics_source_coverage_problems(summary: dict[str, Any]) -> list[str]:
    api_server_count = parse_int_value(summary.get("api_server_count"), 0)
    if api_server_count < 2:
        return []
    coverage = (
        summary.get("admin_metrics_source_coverage")
        if isinstance(summary.get("admin_metrics_source_coverage"), dict)
        else {}
    )
    if not coverage:
        return ["admin_metrics_source_coverage"]
    problems = [str(problem) for problem in (coverage.get("problems") or []) if str(problem or "").strip()]
    if coverage.get("ok") is not True and not problems:
        problems.append("admin_metrics_source_coverage.ok")
    if parse_int_value(coverage.get("successful_source_count"), 0) < api_server_count:
        problems.append("admin_metrics.source_count_below_api_server_count")
    if parse_int_value(coverage.get("distinct_instance_count"), 0) < api_server_count:
        problems.append("admin_metrics.distinct_instance_count_below_api_server_count")
    return sorted(set(problems))


def check_client_transport(summary: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    client_transport = summary.get("client_transport") if isinstance(summary.get("client_transport"), dict) else {}
    search_requests = client_transport.get("search_requests") if isinstance(client_transport, dict) else {}
    if client_transport.get("connection_reuse") != "thread_local_keep_alive":
        problems.append("client_transport.connection_reuse")
    if not isinstance(search_requests, dict):
        return sorted(set([*problems, "client_transport.search_requests"]))
    for key in [
        "requests_sent",
        "request_attempts",
        "connections_opened",
        "connection_reuses",
        "stale_reconnects",
        "connection_close_responses",
    ]:
        if not is_number(search_requests.get(key)):
            problems.append(f"client_transport.search_requests.{key}")
    payload_keys = [
        "gzip_responses",
        "total_response_body_bytes",
        "max_response_body_bytes",
        "last_response_body_bytes",
        "total_decoded_response_body_bytes",
        "max_decoded_response_body_bytes",
        "last_decoded_response_body_bytes",
    ]
    for key in payload_keys:
        if not is_number(search_requests.get(key)):
            problems.append(f"client_transport.search_requests.{key}")
        elif parse_int_value(search_requests.get(key), -1) < 0:
            problems.append(f"client_transport.search_requests.{key}_negative")
    requests = parse_int_value(summary.get("requests"), 0)
    concurrency = parse_int_value(summary.get("concurrency"), 0)
    requests_sent = parse_int_value(search_requests.get("requests_sent"), -1)
    request_attempts = parse_int_value(search_requests.get("request_attempts"), -1)
    connections_opened = parse_int_value(search_requests.get("connections_opened"), -1)
    connection_reuses = parse_int_value(search_requests.get("connection_reuses"), -1)
    stale_reconnects = parse_int_value(search_requests.get("stale_reconnects"), -1)
    connection_close_responses = parse_int_value(search_requests.get("connection_close_responses"), -1)
    gzip_responses = parse_int_value(search_requests.get("gzip_responses"), -1)
    total_response_body_bytes = parse_int_value(search_requests.get("total_response_body_bytes"), -1)
    total_decoded_response_body_bytes = parse_int_value(
        search_requests.get("total_decoded_response_body_bytes"),
        -1,
    )
    if requests > 0 and requests_sent < requests:
        problems.append("client_transport.search_requests.requests_sent_below_requests")
    if requests_sent >= 0 and request_attempts < requests_sent:
        problems.append("client_transport.search_requests.request_attempts_below_sent")
    if requests > 0 and gzip_responses <= 0:
        problems.append("client_transport.search_requests.gzip_responses_zero")
    if requests_sent >= 0 and gzip_responses > requests_sent:
        problems.append("client_transport.search_requests.gzip_responses_exceeds_requests_sent")
    if requests > 0 and total_response_body_bytes <= 0:
        problems.append("client_transport.search_requests.total_response_body_bytes_zero")
    if requests > 0 and total_decoded_response_body_bytes <= 0:
        problems.append("client_transport.search_requests.total_decoded_response_body_bytes_zero")
    if (
        requests > 0
        and total_response_body_bytes >= 0
        and total_decoded_response_body_bytes >= 0
        and total_response_body_bytes >= total_decoded_response_body_bytes
    ):
        problems.append("client_transport.search_requests.response_body_bytes_not_below_decoded")
    if requests > 0 and connections_opened <= 0:
        problems.append("client_transport.search_requests.connections_opened")
    if requests > max(concurrency, 0) and connection_reuses <= 0:
        problems.append("client_transport.search_requests.connection_reuses")
    if stale_reconnects > 0:
        problems.append("client_transport.search_requests.stale_reconnects_nonzero")
    if connection_close_responses > 0:
        problems.append("client_transport.search_requests.connection_close_responses_nonzero")
    return sorted(set(problems))


def request_profile_problems(summary: dict[str, Any]) -> list[str]:
    profile = summary.get("request_profile") if isinstance(summary.get("request_profile"), dict) else {}
    problems: list[str] = []
    if not profile:
        return ["request_profile"]
    requests = parse_int_value(summary.get("requests"), 0)
    total_requests = parse_int_value(profile.get("total_requests"), -1)
    unique_request_signatures = parse_int_value(profile.get("unique_request_signatures"), -1)
    repeated_request_count = parse_int_value(profile.get("repeated_request_count"), -1)
    if requests > 0 and total_requests != requests:
        problems.append("request_profile.total_requests")
    if total_requests < 0:
        problems.append("request_profile.total_requests")
    if unique_request_signatures <= 0:
        problems.append("request_profile.unique_request_signatures")
    if repeated_request_count < 0:
        problems.append("request_profile.repeated_request_count")
    if total_requests >= 0 and unique_request_signatures >= 0:
        if unique_request_signatures > total_requests:
            problems.append("request_profile.unique_request_signatures")
        if repeated_request_count >= 0 and unique_request_signatures + repeated_request_count != total_requests:
            problems.append("request_profile.repeated_request_count")
    if parse_int_value(profile.get("min_backend_marqo_request_attempts"), 0) <= 0:
        problems.append("request_profile.min_backend_marqo_request_attempts")
    unique_by_query_type = (
        profile.get("unique_by_query_type")
        if isinstance(profile.get("unique_by_query_type"), dict)
        else {}
    )
    for query_type, planned_count in expected_query_type_counts_from_mode_counts(summary.get("mode_counts") or {}).items():
        if planned_count > 0 and parse_int_value(unique_by_query_type.get(query_type), 0) <= 0:
            problems.append(f"request_profile.unique_by_query_type.{query_type}")
    image_successful = parse_int_value(summary.get("image_successful_responses"), 0)
    unique_image_inputs = parse_int_value(profile.get("unique_image_inputs"), 0)
    if image_successful > 0 and unique_image_inputs <= 0:
        problems.append("request_profile.unique_image_inputs")
    if image_successful > 0 and unique_image_inputs < MIN_LOAD_IMAGE_INPUTS:
        problems.append("request_profile.unique_image_inputs")
    if external_embedding_backend(summary.get("embedding_backend")) and parse_int_value(
        summary.get("image_successful_responses"),
        0,
    ) > 0 and parse_int_value(profile.get("min_backend_qwen_request_attempts"), 0) <= 0:
        problems.append("request_profile.min_backend_qwen_request_attempts")
    if summary.get("scenario") == "mixed-traffic":
        distinct_mall_count = parse_int_value(profile.get("distinct_mall_count"), 0)
        if distinct_mall_count < MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE:
            problems.append("request_profile.distinct_mall_count")
        mall_identity = summary.get("mall_identity") if isinstance(summary.get("mall_identity"), dict) else {}
        sampled_mall_ids = [
            str(mall_id)
            for mall_id in (mall_identity.get("sampled_mall_ids") or [])
            if str(mall_id or "").strip()
        ]
        unique_by_mall = (
            profile.get("unique_by_mall_id_count")
            if isinstance(profile.get("unique_by_mall_id_count"), dict)
            else {}
        )
        if sampled_mall_ids and not unique_by_mall:
            problems.append("request_profile.unique_by_mall_id_count")
        for mall_id in sampled_mall_ids:
            if unique_by_mall and parse_int_value(unique_by_mall.get(mall_id), 0) <= 0:
                problems.append(f"request_profile.unique_by_mall_id_count.{mall_id}")
    return sorted(set(problems))


def backend_transport_payload_problems(
    delta: dict[str, Any],
    *,
    embedding_backend: str | None = None,
) -> list[str]:
    services = ["marqo"]
    if external_embedding_backend(embedding_backend):
        services.append("qwen")
    problems: list[str] = []
    for service in services:
        attempts = delta.get(f"backend_{service}_request_attempts")
        gzip_responses = delta.get(f"backend_{service}_gzip_responses")
        response_bytes = delta.get(f"backend_{service}_total_response_body_bytes")
        decoded_bytes = delta.get(f"backend_{service}_total_decoded_response_body_bytes")
        prefix = f"server_metrics.delta.backend_{service}"
        if not is_number(attempts) or int(attempts) <= 0:
            continue
        if service == "marqo" and (not is_number(gzip_responses) or int(gzip_responses) <= 0):
            problems.append(BACKEND_MARQO_GZIP_ZERO_PROBLEM)
        if is_number(gzip_responses) and int(gzip_responses) > int(attempts):
            problems.append(f"{prefix}_gzip_responses_exceeds_attempts")
        if (
            service == "marqo"
            and is_number(gzip_responses)
            and int(gzip_responses) > 0
            and is_number(response_bytes)
            and is_number(decoded_bytes)
            and float(decoded_bytes) > 0
            and float(response_bytes) >= float(decoded_bytes)
        ):
            problems.append(BACKEND_MARQO_RESPONSE_BODY_NOT_BELOW_DECODED_PROBLEM)
        elif (
            is_number(response_bytes)
            and is_number(decoded_bytes)
            and float(decoded_bytes) > 0
            and float(response_bytes) > float(decoded_bytes)
        ):
            problems.append(f"{prefix}_response_body_bytes_exceed_decoded")
    return sorted(set(problems))


def backend_request_attempt_problems(
    delta: dict[str, Any],
    *,
    embedding_backend: str | None = None,
    successful_responses: int | None = None,
    image_successful_responses: int | None = None,
    request_profile: dict[str, Any] | None = None,
) -> list[str]:
    problems: list[str] = []
    successful = int(successful_responses or 0)
    image_successful = int(image_successful_responses or 0)
    marqo_attempts = delta.get("backend_marqo_request_attempts")
    if successful > 0 and is_number(marqo_attempts) and int(marqo_attempts) <= 0:
        problems.append(BACKEND_MARQO_REQUEST_ATTEMPTS_ZERO_PROBLEM)
    min_marqo_attempts = (
        parse_int_value(request_profile.get("min_backend_marqo_request_attempts"), 0)
        if isinstance(request_profile, dict)
        else 0
    )
    if successful > 0 and is_number(marqo_attempts) and min_marqo_attempts > 0 and int(marqo_attempts) < min_marqo_attempts:
        problems.append(BACKEND_MARQO_REQUEST_ATTEMPTS_BELOW_UNIQUE_PROBLEM)
    qwen_attempts = delta.get("backend_qwen_request_attempts")
    if (
        external_embedding_backend(embedding_backend)
        and image_successful > 0
        and is_number(qwen_attempts)
        and int(qwen_attempts) <= 0
    ):
        problems.append(BACKEND_QWEN_REQUEST_ATTEMPTS_ZERO_PROBLEM)
    min_qwen_attempts = (
        parse_int_value(request_profile.get("min_backend_qwen_request_attempts"), 0)
        if isinstance(request_profile, dict)
        else 0
    )
    if (
        external_embedding_backend(embedding_backend)
        and image_successful > 0
        and is_number(qwen_attempts)
        and min_qwen_attempts > 0
        and int(qwen_attempts) < min_qwen_attempts
    ):
        problems.append(BACKEND_QWEN_REQUEST_ATTEMPTS_BELOW_UNIQUE_IMAGE_PROBLEM)
    return problems


def summarize_response_engine(response_contract: dict[str, Any]) -> dict[str, Any]:
    successful = int(response_contract.get("valid_successful_responses") or response_contract.get("successful_responses") or 0)
    raw_counts = response_contract.get("engine_counts") or {}
    engine_counts = raw_counts if isinstance(raw_counts, dict) else {}
    marqo_responses = sum(
        int(count or 0)
        for engine, count in engine_counts.items()
        if str(engine or "").strip().lower() == "marqo" and is_number(count)
    )
    if "non_marqo_engine_responses" in response_contract:
        raw_non_marqo = response_contract.get("non_marqo_engine_responses")
        non_marqo = int(raw_non_marqo) if is_number(raw_non_marqo) else successful
    else:
        non_marqo = sum(
            int(count or 0)
            for engine, count in engine_counts.items()
            if str(engine or "").strip().lower() != "marqo" and is_number(count)
        )
        if successful > 0 and not engine_counts:
            non_marqo = successful
    return {
        "ok": bool(engine_counts) and successful > 0 and marqo_responses >= successful and non_marqo == 0,
        "engine_counts": engine_counts,
        "valid_successful_responses": successful,
        "marqo_responses": marqo_responses,
        "non_marqo_engine_responses": non_marqo,
    }


def summarize_response_contract_shape(response_contract: dict[str, Any]) -> dict[str, Any]:
    successful = parse_int_value(response_contract.get("successful_responses"), 0)
    valid_successful = parse_int_value(response_contract.get("valid_successful_responses"), 0)
    invalid_successful = parse_int_value(response_contract.get("invalid_successful_responses"), 0)
    unexpected_query_type_count = parse_int_value(response_contract.get("unexpected_query_type_count"), 0)
    product_url_prefix_mismatch_count = parse_int_value(response_contract.get("product_url_prefix_mismatch_count"), 0)
    expected_product_url_prefix_counts = (
        response_contract.get("expected_product_url_prefix_counts")
        if isinstance(response_contract.get("expected_product_url_prefix_counts"), dict)
        else {}
    )
    product_url_prefix_required = response_contract.get("product_url_prefix_required") is True
    min_top_count = response_contract.get("min_top_count")
    min_item_count = response_contract.get("min_item_count")
    min_category_count = response_contract.get("min_category_count")

    problems: list[str] = []
    if successful <= 0:
        problems.append("response_contract.successful_responses")
    if valid_successful <= 0:
        problems.append("response_contract.valid_successful_responses")
    if invalid_successful != 0:
        problems.append("response_contract.invalid_successful_responses")
    if unexpected_query_type_count != 0:
        problems.append("response_contract.unexpected_query_type_count")
    if product_url_prefix_mismatch_count != 0:
        problems.append("response_contract.product_url_prefix_mismatch_count")
    if product_url_prefix_required:
        observed_prefix_counts = [
            count
            for prefix, count in expected_product_url_prefix_counts.items()
            if str(prefix or "").strip() and str(prefix or "").strip() != "missing"
        ]
        if (
            not observed_prefix_counts
            or parse_int_value(expected_product_url_prefix_counts.get("missing"), 0) > 0
            or all(parse_int_value(count, 0) <= 0 for count in observed_prefix_counts)
        ):
            problems.append("response_contract.expected_product_url_prefix_counts")
    for field, value in [
        ("min_top_count", min_top_count),
        ("min_item_count", min_item_count),
        ("min_category_count", min_category_count),
    ]:
        if not is_number(value) or not math.isfinite(float(value)) or float(value) <= 0:
            problems.append(f"response_contract.{field}")

    return {
        "ok": not problems,
        "problems": sorted(set(problems)),
        "successful_responses": successful,
        "valid_successful_responses": valid_successful,
        "invalid_successful_responses": invalid_successful,
        "unexpected_query_type_count": unexpected_query_type_count,
        "expected_product_url_prefix_counts": expected_product_url_prefix_counts,
        "product_url_prefix_required": product_url_prefix_required,
        "product_url_prefix_mismatch_count": product_url_prefix_mismatch_count,
        "min_top_count": parse_int_value(min_top_count, 0),
        "min_item_count": parse_int_value(min_item_count, 0),
        "min_category_count": parse_int_value(min_category_count, 0),
    }


def summarize_response_mall_identity(response_contract: dict[str, Any], expected_mall_id: Any) -> dict[str, Any]:
    expected = str(expected_mall_id or "").strip()
    successful = parse_int_value(response_contract.get("successful_responses"), 0)
    expected_counts = response_contract.get("expected_mall_id_counts") if isinstance(response_contract.get("expected_mall_id_counts"), dict) else {}
    meta_counts = response_contract.get("meta_mall_id_counts") if isinstance(response_contract.get("meta_mall_id_counts"), dict) else {}
    result_counts = response_contract.get("result_mall_id_counts") if isinstance(response_contract.get("result_mall_id_counts"), dict) else {}
    mismatch_count = parse_int_value(response_contract.get("mall_id_mismatch_count"), successful)
    expected_id_counts = {
        str(mall_id): parse_int_value(count, 0)
        for mall_id, count in expected_counts.items()
        if str(mall_id or "").strip() and str(mall_id) != "missing" and parse_int_value(count, 0) > 0
    }
    expected_ids = set(expected_id_counts)
    multi_expected = len(expected_ids) > 1

    problems = []
    if not expected and not expected_ids:
        problems.append("mall_id")
    if successful <= 0:
        problems.append("response_contract.successful_responses")
    if not expected_counts:
        problems.append("response_contract.expected_mall_id_counts")
    elif parse_int_value(expected_counts.get("missing"), 0) > 0:
        problems.append("response_contract.expected_mall_id_counts")
    elif expected_ids and sum(expected_id_counts.values()) < successful:
        problems.append("response_contract.expected_mall_id_counts")
    elif expected and not multi_expected and parse_int_value(expected_counts.get(expected), 0) < successful:
        problems.append("response_contract.expected_mall_id_counts")
    if not meta_counts:
        problems.append("response_contract.meta_mall_id_counts")
    elif expected_ids:
        for mall_id, expected_count in expected_id_counts.items():
            if parse_int_value(meta_counts.get(mall_id), 0) < expected_count:
                problems.append("response_contract.meta_mall_id_counts")
                break
    elif expected and parse_int_value(meta_counts.get(expected), 0) < successful:
        problems.append("response_contract.meta_mall_id_counts")
    allowed_meta_ids = expected_ids or ({expected} if expected else set())
    unexpected_meta_ids = sorted(
        str(mall_id)
        for mall_id, count in meta_counts.items()
        if str(mall_id) not in allowed_meta_ids and parse_int_value(count, 0) > 0
    )
    if unexpected_meta_ids:
        problems.append("response_contract.meta_mall_id_counts.unexpected")
    if not result_counts:
        problems.append("response_contract.result_mall_id_counts")
    unexpected_result_ids = sorted(
        str(mall_id)
        for mall_id, count in result_counts.items()
        if str(mall_id) not in allowed_meta_ids and parse_int_value(count, 0) > 0
    )
    if unexpected_result_ids:
        problems.append("response_contract.result_mall_id_counts.unexpected")
    if mismatch_count != 0:
        problems.append("response_contract.mall_id_mismatch_count")

    return {
        "ok": not problems,
        "problems": sorted(set(problems)),
        "expected_mall_id": expected or None,
        "successful_responses": successful,
        "expected_mall_id_counts": expected_counts,
        "expected_mall_ids": sorted(expected_ids),
        "meta_mall_id_counts": meta_counts,
        "result_mall_id_counts": result_counts,
        "mall_id_mismatch_count": mismatch_count,
        "unexpected_meta_mall_ids": unexpected_meta_ids,
        "unexpected_result_mall_ids": unexpected_result_ids,
    }


def summarize_mall_identity(report: dict[str, Any]) -> dict[str, Any]:
    raw_identity = report.get("mall_identity")
    identity = raw_identity if isinstance(raw_identity, dict) else {}
    response_contract = report.get("response_contract") if isinstance(report.get("response_contract"), dict) else {}
    expected_mall_id_counts = (
        response_contract.get("expected_mall_id_counts")
        if isinstance(response_contract.get("expected_mall_id_counts"), dict)
        else {}
    )
    sampled_mall_ids = identity.get("sampled_mall_ids") if isinstance(identity.get("sampled_mall_ids"), list) else []
    missing_response_malls = sorted(
        str(mall_id)
        for mall_id in sampled_mall_ids
        if str(mall_id or "").strip() and parse_int_value(expected_mall_id_counts.get(str(mall_id)), 0) <= 0
    )
    successful_responses = parse_int_value(
        response_contract.get("valid_successful_responses"),
        parse_int_value(response_contract.get("successful_responses"), 0),
    )
    distribution_details = summarize_sampled_mall_response_distribution(
        sampled_mall_ids,
        expected_mall_id_counts,
        successful_responses,
    )
    details = {
        "enabled": identity.get("enabled") is True,
        "sample_size_requested": parse_int_value(identity.get("sample_size_requested"), 0),
        "sampling_strategy": str(identity.get("sampling_strategy") or "").strip(),
        "source_enabled_count": parse_int_value(identity.get("source_enabled_count"), 0),
        "eligible_mall_count": parse_int_value(identity.get("eligible_mall_count"), 0),
        "sampled_count": parse_int_value(identity.get("sampled_count"), 0),
        "distinct_mall_count": parse_int_value(identity.get("distinct_mall_count"), 0),
        "sampled_mall_ids": sampled_mall_ids,
        "sampled_mall_id_overflow": parse_int_value(identity.get("sampled_mall_id_overflow"), 0),
        "per_mall_api_keys": identity.get("per_mall_api_keys") is True,
        "per_mall_origins": identity.get("per_mall_origins") is True,
        "per_mall_product_url_prefixes": identity.get("per_mall_product_url_prefixes") is True,
        "sampled_mall_ids_missing_response_counts": missing_response_malls,
        **distribution_details,
    }
    problems: list[str] = []
    if report.get("scenario") == "mixed-traffic":
        if not details["enabled"]:
            problems.append("mall_identity.enabled")
        if details["sample_size_requested"] < MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE:
            problems.append("mall_identity.sample_size_requested")
        if (
            details["source_enabled_count"] > details["sampled_count"]
            and details["sampling_strategy"] != "spread"
        ):
            problems.append("mall_identity.sampling_strategy")
        if details["source_enabled_count"] > 0 and details["source_enabled_count"] < MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE:
            problems.append("mall_identity.source_enabled_count")
        if details["eligible_mall_count"] > 0 and details["eligible_mall_count"] < MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE:
            problems.append("mall_identity.eligible_mall_count")
        if details["sampled_count"] < MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE:
            problems.append("mall_identity.sampled_count")
        if details["distinct_mall_count"] < MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE:
            problems.append("mall_identity.distinct_mall_count")
        if details["sample_size_requested"] > 0 and details["sampled_count"] < details["sample_size_requested"]:
            problems.append("mall_identity.sampled_count_below_requested")
        for field in ["per_mall_api_keys", "per_mall_origins", "per_mall_product_url_prefixes"]:
            if details[field] is not True:
                problems.append(f"mall_identity.{field}")
        if missing_response_malls:
            problems.append("mall_identity.sampled_mall_ids_missing_response_counts")
        if distribution_details["underrepresented_mall_id_count"] > 0:
            problems.append("mall_identity.sampled_mall_ids_underrepresented")
    details["ok"] = not problems
    details["problems"] = sorted(set(problems))
    return details


def summarize_sampled_mall_response_distribution(
    sampled_mall_ids: list[Any],
    expected_mall_id_counts: dict[str, Any],
    successful_responses: int,
) -> dict[str, Any]:
    normalized_sampled_ids = list(
        dict.fromkeys(str(mall_id).strip() for mall_id in sampled_mall_ids if str(mall_id or "").strip())
    )
    sampled_expected_counts = {
        mall_id: parse_int_value(expected_mall_id_counts.get(mall_id), 0)
        for mall_id in normalized_sampled_ids
    }
    observed_total = sum(sampled_expected_counts.values())
    distribution_total = max(parse_int_value(successful_responses, 0), observed_total)
    minimum_count = 0
    if normalized_sampled_ids and distribution_total > 0:
        average_per_mall = distribution_total / len(normalized_sampled_ids)
        minimum_count = max(
            1,
            math.floor(average_per_mall * MIN_MIXED_TRAFFIC_MALL_RESPONSE_DISTRIBUTION_RATIO),
        )
    underrepresented_malls = sorted(
        mall_id
        for mall_id, count in sampled_expected_counts.items()
        if minimum_count > 0 and count < minimum_count
    )
    return {
        "sampled_expected_mall_id_count_total": observed_total,
        "minimum_expected_mall_response_count": minimum_count,
        "mall_response_distribution_ratio": MIN_MIXED_TRAFFIC_MALL_RESPONSE_DISTRIBUTION_RATIO,
        "underrepresented_mall_id_count": len(underrepresented_malls),
        "underrepresented_mall_ids": underrepresented_malls[:MAX_MALL_IDENTITY_DISTRIBUTION_DETAIL_IDS],
        "underrepresented_mall_id_overflow": max(
            0,
            len(underrepresented_malls) - MAX_MALL_IDENTITY_DISTRIBUTION_DETAIL_IDS,
        ),
    }


def check_mixed_traffic_image_source(report: dict[str, Any]) -> tuple[bool, list[str]]:
    if report.get("scenario") != "mixed-traffic":
        return True, []
    image_input = report.get("image_input") or {}
    problems = []
    if image_input.get("source") != "files":
        problems.append("image_input.source")
    if not str(image_input.get("file") or "").strip():
        problems.append("image_input.file")
    if not is_positive_number(image_input.get("size_bytes")):
        problems.append("image_input.size_bytes")
    if not is_number(image_input.get("width")) or int(image_input.get("width") or 0) < 16:
        problems.append("image_input.width")
    if not is_number(image_input.get("height")) or int(image_input.get("height") or 0) < 16:
        problems.append("image_input.height")
    if not str(image_input.get("sha256") or "").strip():
        problems.append("image_input.sha256")
    files = image_input.get("files") if isinstance(image_input.get("files"), list) else []
    file_count = parse_int_value(image_input.get("file_count"), 0)
    unique_sha256_count = parse_int_value(image_input.get("unique_sha256_count"), 0)
    if file_count < MIN_LOAD_IMAGE_INPUTS:
        problems.append("image_input.file_count")
    if len(files) != file_count:
        problems.append("image_input.files")
    if unique_sha256_count < MIN_LOAD_IMAGE_INPUTS:
        problems.append("image_input.unique_sha256_count")
    return not problems, problems


def build_report(
    single_report: dict[str, Any],
    multi_report: dict[str, Any],
    max_multi_p95_regression_percent: float = 25.0,
    max_multi_p99_regression_percent: float = 25.0,
    min_multi_rps_ratio: float = 0.8,
) -> dict[str, Any]:
    single = summarize_load_report(single_report)
    multi = summarize_load_report(multi_report)
    comparable = comparable_workload(single, multi)
    runtime_identity_report = compare_runtime_identity(single, multi)
    p95_change_percent = percent_change(single.get("p95_ms"), multi.get("p95_ms"))
    p99_change_percent = percent_change(single.get("p99_ms"), multi.get("p99_ms"))
    rps_ratio = ratio(multi.get("requests_per_second"), single.get("requests_per_second"))
    problems = []
    if single.get("ok") is not True:
        problems.append("single.ok")
    if multi.get("ok") is not True:
        problems.append("multi.ok")
    if single["api_server_count"] != 1:
        problems.append("single.api_server_count")
    if multi["api_server_count"] < 2:
        problems.append("multi.api_server_count")
    if not comparable:
        problems.append("workload_comparable")
    for key in ["base_url", "mall_id", "origin"]:
        if single.get(key) != multi.get(key):
            problems.append(COMPARISON_TARGET_PROBLEMS[key])
    request_profile_comparable = request_profile_workload(single) == request_profile_workload(multi)
    if image_workload_identity(single) != image_workload_identity(multi):
        problems.append(COMPARISON_TARGET_PROBLEMS["image_input"])
    if not request_profile_comparable:
        problems.append(COMPARISON_TARGET_PROBLEMS["request_profile"])
    for problem in runtime_identity_report["problems"]:
        if problem not in problems:
            problems.append(problem)
    for prefix, summary in [("single", single), ("multi", multi)]:
        if not str(summary.get("mall_id") or "").strip():
            problems.append(f"{prefix}.mall_id")
        if summary.get("target_validation_ok") is not True:
            problems.append(f"{prefix}.target_validation")
        for url_problem in operational_target_url_problems("base_url", summary.get("base_url")):
            problems.append(f"{prefix}.{url_problem}")
        for url_problem in operational_target_url_problems("origin", summary.get("origin"), origin_only=True):
            problems.append(f"{prefix}.{url_problem}")
        if summary["scenario"] != "mixed-traffic":
            problems.append(f"{prefix}.scenario")
        if summary["active_users"] < 850:
            problems.append(f"{prefix}.active_users")
        if summary["requests"] < 850:
            problems.append(f"{prefix}.requests")
        if summary["concurrency"] < 100:
            problems.append(f"{prefix}.concurrency")
        missing_modes = sorted(mode for mode, count in summary["mode_counts"].items() if count <= 0)
        if missing_modes:
            problems.append(f"{prefix}.mode_counts")
        if summary.get("image_source_ok") is not True:
            problems.append(f"{prefix}.image_input")
        if summary.get("mall_identity_ok") is not True:
            problems.append(f"{prefix}.mall_identity")
        if summary.get("response_contract_ok") is not True:
            problems.append(f"{prefix}.response_contract")
        if summary.get("response_engine_ok") is not True:
            problems.append(f"{prefix}.response_engine")
        if summary.get("response_shape_ok") is not True:
            problems.append(f"{prefix}.response_shape")
        if summary.get("query_type_coverage_ok") is not True:
            problems.append(f"{prefix}.query_type_coverage")
        if summary.get("query_type_latency_ok") is not True:
            problems.append(f"{prefix}.query_type_latency")
        for api_instance_problem in api_instance_coverage_problems(summary):
            problem = f"{prefix}.{api_instance_problem}"
            if problem not in problems:
                problems.append(problem)
        for missing in summary.get("server_metrics_missing") or []:
            problem = f"{prefix}.{missing}"
            if problem not in problems:
                problems.append(problem)
        for missing in summary.get("client_transport_missing") or []:
            problem = f"{prefix}.{missing}"
            if problem not in problems:
                problems.append(problem)
    if p95_change_percent is None:
        problems.append("comparison.p95_change_percent")
    elif p95_change_percent > max_multi_p95_regression_percent:
        problems.append("comparison.multi_p95_regression")
    if p99_change_percent is None:
        problems.append("comparison.p99_change_percent")
    elif p99_change_percent > max_multi_p99_regression_percent:
        problems.append("comparison.multi_p99_regression")
    if rps_ratio is None:
        problems.append("comparison.rps_ratio")
    elif rps_ratio < min_multi_rps_ratio:
        problems.append("comparison.multi_rps_ratio")
    comparison = {
        "comparable": comparable,
        "p95_change_percent": round(p95_change_percent, 2) if p95_change_percent is not None else None,
        "p99_change_percent": round(p99_change_percent, 2) if p99_change_percent is not None else None,
        "rps_ratio": round(rps_ratio, 4) if rps_ratio is not None else None,
        "max_multi_p95_regression_percent": max_multi_p95_regression_percent,
        "max_multi_p99_regression_percent": max_multi_p99_regression_percent,
        "min_multi_rps_ratio": min_multi_rps_ratio,
        "runtime_identity_ok": runtime_identity_report["ok"],
        "runtime_identity": runtime_identity_report,
        "request_profile_comparable": request_profile_comparable,
    }
    return {
        "ok": not problems,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "single": single,
        "multi": multi,
        "comparison": comparison,
        "problems": problems,
    }


def comparable_workload(single: dict[str, Any], multi: dict[str, Any]) -> bool:
    keys = ["base_url", "mall_id", "origin", "scenario", "active_users", "requests", "concurrency"]
    if any(single.get(key) != multi.get(key) for key in keys):
        return False
    return (
        single.get("mode_counts") == multi.get("mode_counts")
        and image_workload_identity(single) == image_workload_identity(multi)
        and mall_identity_workload(single) == mall_identity_workload(multi)
        and request_profile_workload(single) == request_profile_workload(multi)
    )


def request_profile_workload(summary: dict[str, Any]) -> tuple[Any, ...]:
    profile = summary.get("request_profile") if isinstance(summary.get("request_profile"), dict) else {}
    unique_by_query_type = (
        profile.get("unique_by_query_type")
        if isinstance(profile.get("unique_by_query_type"), dict)
        else {}
    )
    unique_by_mall_id_count = (
        profile.get("unique_by_mall_id_count")
        if isinstance(profile.get("unique_by_mall_id_count"), dict)
        else {}
    )
    return (
        parse_int_value(profile.get("total_requests"), -1),
        parse_int_value(profile.get("unique_request_signatures"), -1),
        parse_int_value(profile.get("repeated_request_count"), -1),
        tuple(sorted((str(key), parse_int_value(value, 0)) for key, value in unique_by_query_type.items())),
        tuple(sorted((str(key), parse_int_value(value, 0)) for key, value in unique_by_mall_id_count.items())),
        parse_int_value(profile.get("distinct_mall_count"), -1),
        parse_int_value(profile.get("unique_text_queries"), -1),
        parse_int_value(profile.get("unique_image_inputs"), -1),
        parse_int_value(profile.get("min_backend_marqo_request_attempts"), -1),
        parse_int_value(profile.get("min_backend_qwen_request_attempts"), -1),
    )


def mall_identity_workload(summary: dict[str, Any]) -> tuple[Any, ...]:
    identity = summary.get("mall_identity") if isinstance(summary.get("mall_identity"), dict) else {}
    return (
        identity.get("enabled"),
        identity.get("sample_size_requested"),
        identity.get("sampled_count"),
        identity.get("distinct_mall_count"),
        tuple(identity.get("sampled_mall_ids") or []),
        identity.get("sampled_mall_id_overflow"),
        tuple(identity.get("sampled_mall_ids_missing_response_counts") or []),
        identity.get("per_mall_api_keys"),
        identity.get("per_mall_origins"),
        identity.get("per_mall_product_url_prefixes"),
    )


def compare_runtime_identity(single: dict[str, Any], multi: dict[str, Any]) -> dict[str, Any]:
    fields = runtime_identity_fields(single, multi)
    single_identity = runtime_identity(single, fields)
    multi_identity = runtime_identity(multi, fields)
    problems = [
        f"comparison.{field}"
        for field in fields
        if single_identity.get(field) != multi_identity.get(field)
    ]
    return {
        "ok": not problems,
        "fields": fields,
        "single": single_identity,
        "multi": multi_identity,
        "problems": problems,
    }


def runtime_identity_fields(single: dict[str, Any], multi: dict[str, Any]) -> list[str]:
    fields = list(RUNTIME_IDENTITY_FIELDS)
    embedding_backends = {
        str(summary.get("embedding_backend") or "").strip().lower()
        for summary in [single, multi]
    }
    qwen_configured = bool({"qwen", "gemini"} & embedding_backends) or any(
        str(summary.get(field) or "").strip()
        for summary in [single, multi]
        for field in QWEN_RUNTIME_IDENTITY_FIELDS
    )
    if qwen_configured:
        fields.extend(QWEN_RUNTIME_IDENTITY_FIELDS)
    return fields


def runtime_identity(summary: dict[str, Any], fields: list[str] | None = None) -> dict[str, Any]:
    selected_fields = fields or runtime_identity_fields(summary, summary)
    return {field: normalize_runtime_identity_value(field, summary.get(field)) for field in selected_fields}


def qwen_query_vector_server_metric_problems(summary: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in QWEN_QUERY_VECTOR_SERVER_METRIC_FIELDS:
        if not is_number(summary.get(field)):
            missing.append(field)
    for field in [
        "qwen_query_vector_runtime_max_entries",
        "qwen_query_vector_runtime_text_max_entries",
        "qwen_query_vector_runtime_image_max_entries",
        "qwen_query_vector_wait_timeout_seconds",
    ]:
        if not is_positive_number(summary.get(field)) and field not in missing:
            missing.append(field)
    text_capacity = summary.get("qwen_query_vector_runtime_text_max_entries")
    image_capacity = summary.get("qwen_query_vector_runtime_image_max_entries")
    total_capacity = summary.get("qwen_query_vector_runtime_max_entries")
    if is_number(text_capacity) and float(text_capacity) < REQUIRED_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES:
        missing.append("qwen_query_vector_runtime_text_max_entries_below_required")
    if is_number(image_capacity) and float(image_capacity) < REQUIRED_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES:
        missing.append("qwen_query_vector_runtime_image_max_entries_below_required")
    if (
        is_number(total_capacity)
        and is_number(text_capacity)
        and is_number(image_capacity)
        and float(total_capacity) < float(text_capacity) + float(image_capacity)
    ):
        missing.append("qwen_query_vector_runtime_max_entries_below_text_image_sum")
    return sorted(set(missing))


def normalize_runtime_identity_value(field: str, value: Any) -> Any:
    if value in (None, ""):
        return None
    if field in {"engine_backend", "embedding_backend", "rate_limit_backend", "cache_backend"}:
        return str(value).strip().lower()
    if field in {
        "cache_ttl_seconds",
        "singleflight_wait_timeout_seconds",
        "search_queue_max_concurrency",
        "search_queue_timeout_seconds",
        "image_queue_max_concurrency",
        "image_queue_timeout_seconds",
        "api_threadpool_configured_tokens",
        "api_threadpool_runtime_tokens",
        "api_threadpool_required_tokens",
        "qwen_embedding_dimensions",
        "qwen_query_vector_runtime_max_entries",
        "qwen_query_vector_runtime_text_max_entries",
        "qwen_query_vector_runtime_image_max_entries",
        "qwen_query_vector_mixed_parallelism",
    }:
        return float(value) if is_number(value) else value
    return value


def api_threadpool_server_metric_problems(summary: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in API_THREADPOOL_SERVER_METRIC_FIELDS:
        if summary.get(field) in (None, ""):
            missing.append(f"server_metrics.after.{field}")
    if summary.get("api_threadpool_ok") is not True:
        missing.append("server_metrics.after.api_threadpool_ok")
    required = summary.get("api_threadpool_required_tokens")
    if not is_positive_number(required):
        missing.append("server_metrics.after.api_threadpool_required_tokens")
        return sorted(set(missing))
    for field in ["api_threadpool_configured_tokens", "api_threadpool_runtime_tokens"]:
        value = summary.get(field)
        problem = f"server_metrics.after.{field}"
        if not is_number(value):
            missing.append(problem)
        elif float(value) < float(required):
            missing.append(f"{problem}_below_required")
    return sorted(set(missing))


def server_settled_state_problems(summary: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for field in SETTLED_SERVER_IN_FLIGHT_FIELDS:
        value = summary.get(field)
        if is_number(value) and int(value) > 0:
            problems.append(f"server_metrics.after.{field}_nonzero")
    for field, limit in SERVER_RESOURCE_PERCENT_LIMITS.items():
        value = summary.get(field)
        if is_number(value) and float(value) >= limit:
            problems.append(f"server_metrics.after.{field}_high")
    return sorted(set(problems))


def process_rss_growth_mb(before: dict[str, Any], after: dict[str, Any]) -> float | None:
    before_value = before.get("process_memory_rss_bytes")
    after_value = after.get("process_memory_rss_bytes")
    if not is_number(before_value) or not is_number(after_value):
        return None
    growth_bytes = max(0.0, float(after_value) - float(before_value))
    return round(growth_bytes / BYTES_PER_MIB, 3)


def process_rss_growth_problems(summary: dict[str, Any]) -> list[str]:
    thresholds = summary.get("thresholds") or {}
    threshold_value = thresholds.get("max_process_rss_growth_mb") if isinstance(thresholds, dict) else None
    threshold_configured = isinstance(thresholds, dict) and "max_process_rss_growth_mb" in thresholds
    max_growth_mb = (
        float(threshold_value)
        if is_number(threshold_value) and float(threshold_value) > 0
        else DEFAULT_MAX_PROCESS_RSS_GROWTH_MB
    )
    if not is_number(summary.get("process_memory_rss_bytes_before")):
        return ["server_metrics.before.process_memory_rss_bytes"] if threshold_configured else []
    if not is_number(summary.get("process_memory_rss_bytes")):
        return ["server_metrics.after.process_memory_rss_bytes"] if threshold_configured else []
    growth_mb = summary.get("process_memory_rss_growth_mb")
    if not is_number(growth_mb):
        return ["server_metrics.after.process_memory_rss_growth_mb"]
    if float(growth_mb) > max_growth_mb:
        return ["server_metrics.after.process_memory_rss_growth_mb_above_threshold"]
    return []


def summarize_query_type_coverage(
    response_contract: dict[str, Any],
    mode_counts: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    planned_query_type_counts = expected_query_type_counts_from_mode_counts(mode_counts)
    raw_query_type_counts = response_contract.get("query_type_counts")
    query_type_counts = raw_query_type_counts if isinstance(raw_query_type_counts, dict) else {}
    observed_query_type_counts = {
        str(query_type): parse_int_value(count, 0)
        for query_type, count in query_type_counts.items()
    }
    raw_expected_query_type_counts = response_contract.get("expected_query_type_counts")
    expected_query_type_counts = (
        raw_expected_query_type_counts
        if isinstance(raw_expected_query_type_counts, dict)
        else {}
    )
    normalized_expected_query_type_counts = {
        str(query_type): parse_int_value(count, 0)
        for query_type, count in expected_query_type_counts.items()
    }
    total_planned = sum(planned_query_type_counts.values())
    max_error_rate = max(0.0, parse_float_value(thresholds.get("max_error_rate"), 0.0))
    allowed_shortfall = math.ceil(total_planned * (max_error_rate / 100.0)) if total_planned > 0 else 0
    minimum_observed_query_type_counts = {
        query_type: max(1, expected_count - allowed_shortfall)
        for query_type, expected_count in planned_query_type_counts.items()
        if expected_count > 0
    }
    problems = []
    if not planned_query_type_counts:
        problems.append("mode_counts")
    if not query_type_counts:
        problems.append("response_contract.query_type_counts")
    for query_type, expected_count in minimum_observed_query_type_counts.items():
        if observed_query_type_counts.get(query_type, 0) < expected_count:
            problems.append(f"response_contract.query_type_counts.{query_type}")
    unexpected_observed_query_types = sorted(
        query_type
        for query_type, count in observed_query_type_counts.items()
        if count > 0 and query_type not in planned_query_type_counts
    )
    if unexpected_observed_query_types:
        problems.append("response_contract.query_type_counts.unexpected")
    if expected_query_type_counts and normalized_expected_query_type_counts != planned_query_type_counts:
        problems.append("response_contract.expected_query_type_counts")
    if parse_int_value(response_contract.get("unexpected_query_type_count"), 0) != 0:
        problems.append("response_contract.unexpected_query_type_count")
    return {
        "ok": not problems,
        "problems": sorted(set(problems)),
        "planned_query_type_counts": planned_query_type_counts,
        "observed_query_type_counts": observed_query_type_counts,
        "expected_query_type_counts": normalized_expected_query_type_counts,
        "minimum_observed_query_type_counts": minimum_observed_query_type_counts,
        "allowed_shortfall": allowed_shortfall,
        "unexpected_observed_query_types": unexpected_observed_query_types,
    }


def summarize_query_type_latency(report: dict[str, Any]) -> dict[str, Any]:
    mode_counts = report.get("mode_counts") or {}
    thresholds = report.get("thresholds") or {}
    planned_query_type_counts = expected_query_type_counts_from_mode_counts(mode_counts)
    expected_latency = normalized_query_type_latency(report.get("expected_query_type_latency_ms"))
    response_latency = normalized_query_type_latency(report.get("response_query_type_latency_ms"))
    total_planned = sum(planned_query_type_counts.values())
    p95_limit = thresholds.get("p95_ms") if isinstance(thresholds, dict) else None
    p99_limit = thresholds.get("p99_ms") if isinstance(thresholds, dict) else None
    max_error_rate = max(0.0, parse_float_value(thresholds.get("max_error_rate"), 0.0)) if isinstance(thresholds, dict) else 0.0
    allowed_shortfall = math.ceil(total_planned * (max_error_rate / 100.0)) if total_planned > 0 else 0
    minimum_response_counts = {
        query_type: max(1, expected_count - allowed_shortfall)
        for query_type, expected_count in planned_query_type_counts.items()
        if expected_count > 0
    }
    problems: list[str] = []
    if not planned_query_type_counts:
        problems.append("mode_counts")
    if not is_number(p95_limit) or float(p95_limit) <= 0:
        problems.append("thresholds.p95_ms")
    if is_number(p99_limit) and float(p99_limit) <= 0:
        problems.append("thresholds.p99_ms")
    for query_type, expected_count in planned_query_type_counts.items():
        expected_summary = expected_latency.get(query_type)
        if not isinstance(expected_summary, dict):
            problems.append(f"expected_query_type_latency_ms.{query_type}")
        elif parse_int_value(expected_summary.get("count"), 0) < expected_count:
            problems.append(f"expected_query_type_latency_ms.{query_type}.count")

        response_summary = response_latency.get(query_type)
        if not isinstance(response_summary, dict):
            problems.append(f"response_query_type_latency_ms.{query_type}")
            continue
        if parse_int_value(response_summary.get("count"), 0) < minimum_response_counts.get(query_type, 1):
            problems.append(f"response_query_type_latency_ms.{query_type}.count")
        p95 = response_summary.get("p95")
        if not is_number(p95):
            problems.append(f"response_query_type_latency_ms.{query_type}.p95")
        elif is_number(p95_limit) and float(p95) > float(p95_limit):
            problems.append(f"response_query_type_latency_ms.{query_type}.p95")
        p99 = response_summary.get("p99")
        if not is_number(p99):
            problems.append(f"response_query_type_latency_ms.{query_type}.p99")
        elif is_number(p99_limit) and float(p99) > float(p99_limit):
            problems.append(f"response_query_type_latency_ms.{query_type}.p99")
        if not is_number(response_summary.get("max")):
            problems.append(f"response_query_type_latency_ms.{query_type}.max")
    return {
        "ok": not problems,
        "problems": sorted(set(problems)),
        "planned_query_type_counts": planned_query_type_counts,
        "minimum_response_counts": minimum_response_counts,
        "allowed_shortfall": allowed_shortfall,
        "threshold_p95_ms": float(p95_limit) if is_number(p95_limit) else None,
        "threshold_p99_ms": float(p99_limit) if is_number(p99_limit) else None,
        "expected_query_type_latency_ms": expected_latency,
        "response_query_type_latency_ms": response_latency,
    }


def normalized_query_type_latency(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(query_type): summary
        for query_type, summary in value.items()
        if isinstance(summary, dict)
    }


def expected_query_type_counts_from_mode_counts(mode_counts: dict[str, Any]) -> dict[str, int]:
    if not isinstance(mode_counts, dict):
        return {}
    mapping = {"text": "text", "image": "image", "mixed": "text_image"}
    counts: dict[str, int] = {}
    for mode, query_type in mapping.items():
        count = parse_int_value(mode_counts.get(mode), 0)
        if count > 0:
            counts[query_type] = counts.get(query_type, 0) + count
    return counts


def check_scale_server_metrics(summary: dict[str, Any]) -> list[str]:
    missing = []
    for problem in request_timeout_threshold_problems(summary.get("thresholds") or {}):
        if problem not in missing:
            missing.append(problem)
    for problem in throughput_threshold_problems(summary):
        if problem not in missing:
            missing.append(problem)
    if str(summary.get("engine_backend") or "").strip().lower() != "marqo":
        missing.append("engine_backend")
    if not str(summary.get("engine_index") or "").strip():
        missing.append("engine_index")
    if not str(summary.get("marqo_model") or "").strip():
        missing.append("marqo_model")
    embedding_backend = str(summary.get("embedding_backend") or "").strip().lower()
    if embedding_backend not in {"native", "qwen", "gemini"}:
        missing.append("embedding_backend")
    if external_embedding_backend(embedding_backend):
        if not str(summary.get("qwen_model") or "").strip():
            missing.append("qwen_model")
        if not is_positive_number(summary.get("qwen_embedding_dimensions")):
            missing.append("qwen_embedding_dimensions")
        for key in BACKEND_QWEN_SERVER_METRIC_FIELDS:
            if not is_number(summary.get(key)):
                missing.append(key)
        missing.extend(qwen_query_vector_server_metric_problems(summary))
    for key in BACKEND_MARQO_SERVER_METRIC_FIELDS:
        if not is_number(summary.get(key)):
            missing.append(key)
    for key in SEARCH_ENGINE_SERVER_METRIC_FIELDS:
        if not is_number(summary.get(key)):
            missing.append(key)
    for key in ["result_mall_id_mismatch_events", "result_mall_id_mismatch_count"]:
        if not is_number(summary.get(key)):
            missing.append(key)
    if summary.get("server_metrics_ok") is not True:
        missing.append("server_metrics")
    for problem in admin_metrics_source_coverage_problems(summary):
        missing.append(problem)
    if str(summary.get("rate_limit_backend") or "").strip().lower() != "redis":
        missing.append("rate_limit_backend")
    if summary.get("rate_limit_redis_enabled") is not True:
        missing.append("rate_limit_redis_enabled")
    if str(summary.get("cache_backend") or "").strip().lower() != "redis":
        missing.append("cache_backend")
    if summary.get("cache_redis_enabled") is not True:
        missing.append("cache_redis_enabled")
    if not is_positive_number(summary.get("cache_ttl_seconds")):
        missing.append("cache_ttl_seconds")
    if summary.get("singleflight_enabled") is not True and "singleflight_enabled" not in missing:
        missing.append("singleflight_enabled")
    for key in [
        "cache_lock_claims",
        "cache_lock_contention_events",
        "cache_lock_errors",
        "cache_lock_release_errors",
        "cache_lock_wait_events",
        "cache_lock_wait_timeouts",
        "cache_lock_total_wait_ms",
        "cache_lock_avg_wait_ms",
        "cache_lock_max_wait_ms",
        "singleflight_in_flight",
        "singleflight_wait_events",
        "singleflight_wait_timeouts",
        "singleflight_total_wait_ms",
        "singleflight_avg_wait_ms",
        "singleflight_max_wait_ms",
    ]:
        if not is_number(summary.get(key)) and key not in missing:
            missing.append(key)
    if not is_positive_number(summary.get("singleflight_wait_timeout_seconds")) and "singleflight_wait_timeout_seconds" not in missing:
        missing.append("singleflight_wait_timeout_seconds")
    for key in [
        "search_queue_enabled",
        "search_queue_max_concurrency",
        "search_queue_timeout_seconds",
        "search_queue_full_events",
        "search_queue_wait_events",
        "search_queue_total_wait_ms",
        "search_queue_avg_wait_ms",
        "search_queue_max_wait_ms",
        "image_queue_enabled",
        "image_queue_max_concurrency",
        "image_queue_timeout_seconds",
        "image_queue_full_events",
        "image_queue_wait_events",
        "image_queue_total_wait_ms",
        "image_queue_avg_wait_ms",
        "image_queue_max_wait_ms",
        "search_log_write_errors",
        "error_log_write_errors",
    ]:
        if summary.get(key) in (None, ""):
            missing.append(key)
    if summary.get("search_queue_enabled") is not True and "search_queue_enabled" not in missing:
        missing.append("search_queue_enabled")
    if not is_positive_number(summary.get("search_queue_max_concurrency")) and "search_queue_max_concurrency" not in missing:
        missing.append("search_queue_max_concurrency")
    if summary.get("image_queue_enabled") is not True and "image_queue_enabled" not in missing:
        missing.append("image_queue_enabled")
    if not is_positive_number(summary.get("image_queue_max_concurrency")) and "image_queue_max_concurrency" not in missing:
        missing.append("image_queue_max_concurrency")
    for problem in api_threadpool_server_metric_problems(summary):
        if problem not in missing:
            missing.append(problem)
    for problem in server_settled_state_problems(summary):
        if problem not in missing:
            missing.append(problem)
    for problem in process_rss_growth_problems(summary):
        if problem not in missing:
            missing.append(problem)
    delta = summary.get("server_metrics_delta") or {}
    run_log_coverage = summary.get("server_metrics_run_log_coverage") or {}
    successful = int(summary.get("successful_responses", 0) or 0)
    image_successful = int(summary.get("image_successful_responses", 0) or 0)
    for problem in request_profile_problems(summary):
        if problem not in missing:
            missing.append(problem)
    search_delta = delta.get("search_events")
    if metric_coverage_meets(delta, run_log_coverage, "search_events", successful):
        pass
    elif not is_number(search_delta) or int(search_delta) <= 0:
        missing.append("server_metrics.delta.search_events")
    elif int(search_delta) < successful:
        missing.append("server_metrics.delta.search_events_below_successful_responses")
    image_delta = delta.get("image_search_events")
    if metric_coverage_meets(delta, run_log_coverage, "image_search_events", image_successful):
        pass
    elif not is_number(image_delta) or int(image_delta) <= 0:
        missing.append("server_metrics.delta.image_search_events")
    elif int(image_delta) < image_successful:
        missing.append("server_metrics.delta.image_search_events_below_successful_responses")
    for problem in REQUIRED_SERVER_METRIC_DELTA_PROBLEMS:
        key = problem.rsplit(".", 1)[-1]
        if not is_number(delta.get(key)):
            missing.append(problem)
    if external_embedding_backend(embedding_backend):
        for key in BACKEND_QWEN_SERVER_METRIC_DELTA_FIELDS:
            if not is_number(delta.get(key)):
                missing.append(f"server_metrics.delta.{key}")
        for key in QWEN_QUERY_VECTOR_DELTA_SERVER_METRIC_FIELDS:
            if not is_number(delta.get(key)):
                missing.append(f"server_metrics.delta.{key}")
        for key in BACKEND_QWEN_ZERO_DELTA_SERVER_METRIC_FIELDS:
            value = delta.get(key)
            problem = f"server_metrics.delta.{key}"
            if is_number(value) and float(value) > 0:
                missing.append(f"{problem}_nonzero")
        for key in QWEN_QUERY_VECTOR_ZERO_DELTA_SERVER_METRIC_FIELDS:
            value = delta.get(key)
            problem = f"server_metrics.delta.{key}"
            if is_number(value) and float(value) > 0:
                missing.append(f"{problem}_nonzero")
    for problem in backend_run_latency_problems(
        delta,
        summary.get("thresholds") or {},
        embedding_backend=embedding_backend,
    ):
        if problem not in missing:
            missing.append(problem)
    for problem in server_wait_avg_latency_problems(
        delta,
        summary.get("thresholds") or {},
        embedding_backend=embedding_backend,
    ):
        if problem not in missing:
            missing.append(problem)
    for problem in backend_transport_payload_problems(delta, embedding_backend=embedding_backend):
        if problem not in missing:
            missing.append(problem)
    for problem in backend_request_attempt_problems(
        delta,
        embedding_backend=embedding_backend,
        successful_responses=successful,
        image_successful_responses=image_successful,
        request_profile=summary.get("request_profile") if isinstance(summary.get("request_profile"), dict) else {},
    ):
        if problem not in missing:
            missing.append(problem)
    for key in ZERO_DELTA_SERVER_METRIC_FIELDS:
        value = delta.get(key)
        problem = f"server_metrics.delta.{key}"
        if not is_number(value) and problem not in missing:
            missing.append(problem)
        elif is_number(value) and float(value) > 0:
            missing.append(f"{problem}_nonzero")
    for key in BACKEND_CIRCUIT_ZERO_DELTA_SERVER_METRIC_FIELDS:
        value = delta.get(key)
        if is_number(value) and float(value) > 0:
            missing.append(f"server_metrics.delta.{key}_nonzero")
    for key in OPTIONAL_ZERO_DELTA_SERVER_METRIC_FIELDS:
        value = delta.get(key)
        if is_number(value) and float(value) > 0:
            missing.append(f"server_metrics.delta.{key}_nonzero")
    return missing


def backend_run_latency_problems(
    delta: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    embedding_backend: str | None = None,
) -> list[str]:
    p95_limit = thresholds.get("p95_ms") if isinstance(thresholds, dict) else None
    if not is_number(p95_limit) or float(p95_limit) <= 0:
        return []
    services = ["marqo"]
    if external_embedding_backend(embedding_backend):
        services.append("qwen")
    problems: list[str] = []
    for service in services:
        attempts = delta.get(f"backend_{service}_request_attempts")
        elapsed_ms = delta.get(f"backend_{service}_total_elapsed_ms")
        if not is_number(attempts) or int(attempts) <= 0 or not is_number(elapsed_ms):
            continue
        avg_elapsed_ms = delta.get(f"backend_{service}_run_avg_elapsed_ms")
        if not is_number(avg_elapsed_ms):
            avg_elapsed_ms = round(float(elapsed_ms) / int(attempts), 3)
        if float(avg_elapsed_ms) > float(p95_limit):
            problems.append(f"server_metrics.delta.backend_{service}_run_avg_elapsed_ms_above_p95")
    return problems


def default_server_wait_avg_ms(p95_ms: float | int) -> float:
    return round(max(DEFAULT_SERVER_WAIT_MIN_AVG_MS, float(p95_ms) * DEFAULT_SERVER_WAIT_AVG_TO_P95_RATIO), 1)


def request_timeout_threshold_problems(thresholds: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not isinstance(thresholds, dict):
        return ["thresholds.request_timeout_seconds"]
    timeout_seconds = thresholds.get("request_timeout_seconds")
    p99_limit = thresholds.get("p99_ms")
    if not is_number(timeout_seconds) or float(timeout_seconds) <= 0:
        return ["thresholds.request_timeout_seconds"]
    if not is_number(p99_limit) or float(p99_limit) <= 0:
        return problems
    timeout = float(timeout_seconds)
    p99_seconds = float(p99_limit) / 1000.0
    if timeout < p99_seconds:
        problems.append("thresholds.request_timeout_seconds_below_p99")
    if timeout > max_request_timeout_seconds(float(p99_limit)):
        problems.append("thresholds.request_timeout_seconds_above_budget")
    return problems


def max_request_timeout_seconds(p99_ms: float | int) -> float:
    return round(
        max(DEFAULT_REQUEST_TIMEOUT_MIN_SECONDS, float(p99_ms) / 1000.0 * MAX_REQUEST_TIMEOUT_TO_P99_RATIO),
        1,
    )


def throughput_threshold_problems(summary: dict[str, Any]) -> list[str]:
    thresholds = summary.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        return ["thresholds.min_requests_per_second"]
    threshold = thresholds.get("min_requests_per_second")
    if not is_number(threshold) or float(threshold) <= 0:
        threshold = default_min_rps(summary.get("concurrency"), thresholds.get("p95_ms"))
    if not is_number(threshold) or float(threshold) <= 0:
        return ["thresholds.min_requests_per_second"]
    rps = summary.get("requests_per_second")
    if not is_number(rps):
        return ["requests_per_second"]
    if float(rps) < float(threshold):
        return ["requests_per_second_below_threshold"]
    return []


def default_min_rps(concurrency: Any, p95_ms: Any) -> float | None:
    if not is_number(concurrency) or not is_number(p95_ms) or float(p95_ms) <= 0:
        return None
    p95_seconds = max(float(p95_ms) / 1000.0, 0.001)
    return round(max(DEFAULT_MIN_RPS_FLOOR, float(concurrency) / p95_seconds * DEFAULT_MIN_RPS_TO_P95_CAPACITY_RATIO), 2)


def server_wait_avg_latency_problems(
    delta: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    embedding_backend: str | None = None,
) -> list[str]:
    p95_limit = thresholds.get("p95_ms") if isinstance(thresholds, dict) else None
    if not is_number(p95_limit) or float(p95_limit) <= 0:
        return []
    threshold_value = thresholds.get("max_server_wait_avg_ms") if isinstance(thresholds, dict) else None
    if not is_number(threshold_value) or float(threshold_value) <= 0:
        threshold_value = default_server_wait_avg_ms(float(p95_limit))
    max_wait_avg_ms = float(threshold_value)
    prefixes = ["cache_lock", "singleflight", "search_queue", "image_queue"]
    if external_embedding_backend(embedding_backend):
        prefixes.append("qwen_query_vector")
    problems: list[str] = []
    for prefix in prefixes:
        value = delta.get(f"{prefix}_run_avg_wait_ms")
        if not is_number(value):
            field = next((item for item in SERVER_WAIT_AVG_DELTA_FIELDS if item[0] == prefix), None)
            if field is not None:
                _, events_key, total_wait_key = field
                events = delta.get(events_key)
                total_wait_ms = delta.get(total_wait_key)
                if is_number(events) and int(events) > 0 and is_number(total_wait_ms):
                    value = round(float(total_wait_ms) / int(events), 3)
        if is_number(value) and float(value) > max_wait_avg_ms:
            problems.append(f"server_metrics.delta.{prefix}_run_avg_wait_ms_above_threshold")
    return problems


def metric_coverage_meets(
    delta: dict[str, Any],
    run_log_coverage: dict[str, Any],
    key: str,
    minimum: int,
) -> bool:
    if minimum <= 0:
        return True
    delta_value = delta.get(key)
    if is_number(delta_value) and int(delta_value) >= minimum:
        return True
    run_log_value = run_log_coverage.get(key)
    return run_log_coverage.get("ok") is True and is_number(run_log_value) and int(run_log_value) >= minimum


def image_workload_identity(summary: dict[str, Any]) -> tuple[Any, ...]:
    image_input = summary.get("image_input") or {}
    return (
        image_input.get("source"),
        image_input.get("file"),
        tuple(image_input.get("files") or []),
        image_input.get("sha256"),
        tuple(image_input.get("sha256_values") or []),
        image_input.get("file_count"),
        image_input.get("unique_sha256_count"),
        image_input.get("size_bytes"),
        image_input.get("total_size_bytes"),
        image_input.get("width"),
        image_input.get("height"),
    )


def is_local_operational_host(hostname: str | None) -> bool:
    return is_non_public_host(hostname)


def operational_target_url_problems(field: str, value: Any, origin_only: bool = False) -> list[str]:
    text = str(value or "").strip()
    problems: list[str] = []
    if not text:
        return [field]
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        return [operational_target_url_problem_name(field, "format")]
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError:
        return [operational_target_url_problem_name(field, "port")]
    if parsed.scheme.lower() != "https":
        problems.append(operational_target_url_problem_name(field, "https"))
    if not parsed.netloc or not parsed.hostname:
        problems.append(operational_target_url_problem_name(field, "host"))
    if parsed.username is not None or parsed.password is not None:
        problems.append(operational_target_url_problem_name(field, "credentials"))
    if parsed.hostname and is_local_operational_host(parsed.hostname):
        problems.append(operational_target_url_problem_name(field, "non_local"))
    if parsed.params or parsed.query or parsed.fragment:
        problems.append(operational_target_url_problem_name(field, "clean_url"))
    if origin_only and parsed.path not in {"", "/"}:
        problems.append(operational_target_url_problem_name(field, "origin_only"))
    return sorted(set(problems))


def percent_change(before: Any, after: Any) -> float | None:
    if not is_positive_number(before) or not is_number(after):
        return None
    return ((float(after) - float(before)) / float(before)) * 100


def ratio(numerator: Any, denominator: Any) -> float | None:
    if not is_number(numerator) or not is_positive_number(denominator):
        return None
    return float(numerator) / float(denominator)


def is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def parse_int_value(value: Any, default: int = 0) -> int:
    if not is_number(value):
        return default
    return int(float(value))


def parse_float_value(value: Any, default: float = 0.0) -> float:
    if not is_number(value):
        return default
    return float(value)


def is_positive_number(value: Any) -> bool:
    return is_number(value) and float(value) > 0


def to_markdown(report: dict[str, Any]) -> str:
    single = report["single"]
    multi = report["multi"]
    comparison = report["comparison"]
    return "\n".join(
        [
            "# Haeorum AI Search API Scale Comparison",
            "",
            f"- OK: `{report['ok']}`",
            f"- Single API servers: `{single.get('api_server_count')}`",
            f"- Multi API servers: `{multi.get('api_server_count')}`",
            f"- Base URL: `{multi.get('base_url')}`",
            f"- Mall ID: `{multi.get('mall_id')}`",
            f"- Origin: `{multi.get('origin')}`",
            f"- Single target validation OK: `{single.get('target_validation_ok')}`",
            f"- Multi target validation OK: `{multi.get('target_validation_ok')}`",
            f"- Scenario: `{multi.get('scenario')}`",
            f"- Active users: `{multi.get('active_users')}`",
            f"- Requests: `{multi.get('requests')}`",
            f"- Concurrency: `{multi.get('concurrency')}`",
            f"- Single image source: `{(single.get('image_input') or {}).get('source')}`",
            f"- Multi image source: `{(multi.get('image_input') or {}).get('source')}`",
            f"- Comparable workload: `{comparison.get('comparable')}`",
            f"- Runtime identity OK: `{comparison.get('runtime_identity_ok')}`",
            f"- Single runtime identity: `{json.dumps(((comparison.get('runtime_identity') or {}).get('single') or {}), ensure_ascii=False)}`",
            f"- Multi runtime identity: `{json.dumps(((comparison.get('runtime_identity') or {}).get('multi') or {}), ensure_ascii=False)}`",
            f"- Runtime identity problems: `{', '.join(((comparison.get('runtime_identity') or {}).get('problems') or []))}`",
            f"- Single p95 ms: `{single.get('p95_ms')}`",
            f"- Multi p95 ms: `{multi.get('p95_ms')}`",
            f"- Multi p95 change %: `{comparison.get('p95_change_percent')}`",
            f"- Single p99 ms: `{single.get('p99_ms')}`",
            f"- Multi p99 ms: `{multi.get('p99_ms')}`",
            f"- Multi p99 change %: `{comparison.get('p99_change_percent')}`",
            f"- Single RPS: `{single.get('requests_per_second')}`",
            f"- Multi RPS: `{multi.get('requests_per_second')}`",
            f"- Multi RPS ratio: `{comparison.get('rps_ratio')}`",
            f"- Single client transport: `{json.dumps(single.get('client_transport') or {}, ensure_ascii=False)}`",
            f"- Multi client transport: `{json.dumps(multi.get('client_transport') or {}, ensure_ascii=False)}`",
            f"- Single client transport missing: `{', '.join(single.get('client_transport_missing') or [])}`",
            f"- Multi client transport missing: `{', '.join(multi.get('client_transport_missing') or [])}`",
            f"- Single API instance coverage: `{json.dumps(single.get('api_instance_coverage') or {}, ensure_ascii=False)}`",
            f"- Multi API instance coverage: `{json.dumps(multi.get('api_instance_coverage') or {}, ensure_ascii=False)}`",
            f"- Single admin metrics source coverage: `{json.dumps(single.get('admin_metrics_source_coverage') or {}, ensure_ascii=False)}`",
            f"- Multi admin metrics source coverage: `{json.dumps(multi.get('admin_metrics_source_coverage') or {}, ensure_ascii=False)}`",
            f"- Single server metrics missing: `{', '.join(single.get('server_metrics_missing') or [])}`",
            f"- Multi server metrics missing: `{', '.join(multi.get('server_metrics_missing') or [])}`",
            f"- Single response engine OK: `{single.get('response_engine_ok')}`",
            f"- Multi response engine OK: `{multi.get('response_engine_ok')}`",
            f"- Single response engine counts: `{json.dumps((single.get('response_engine') or {}).get('engine_counts') or {}, ensure_ascii=False)}`",
            f"- Multi response engine counts: `{json.dumps((multi.get('response_engine') or {}).get('engine_counts') or {}, ensure_ascii=False)}`",
            f"- Single response shape OK: `{single.get('response_shape_ok')}`",
            f"- Multi response shape OK: `{multi.get('response_shape_ok')}`",
            f"- Single response shape problems: `{', '.join(single.get('response_shape_problems') or [])}`",
            f"- Multi response shape problems: `{', '.join(multi.get('response_shape_problems') or [])}`",
            f"- Single query type coverage OK: `{single.get('query_type_coverage_ok')}`",
            f"- Multi query type coverage OK: `{multi.get('query_type_coverage_ok')}`",
            f"- Single query type coverage problems: `{', '.join(single.get('query_type_coverage_problems') or [])}`",
            f"- Multi query type coverage problems: `{', '.join(multi.get('query_type_coverage_problems') or [])}`",
            f"- Single query type latency OK: `{single.get('query_type_latency_ok')}`",
            f"- Multi query type latency OK: `{multi.get('query_type_latency_ok')}`",
            f"- Single query type latency problems: `{', '.join(single.get('query_type_latency_problems') or [])}`",
            f"- Multi query type latency problems: `{', '.join(multi.get('query_type_latency_problems') or [])}`",
            f"- Single server metric coverage OK: `{single.get('server_metrics_coverage_ok')}`",
            f"- Multi server metric coverage OK: `{multi.get('server_metrics_coverage_ok')}`",
            f"- Problems: `{', '.join(report.get('problems') or [])}`",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare one-API-server and multi-API-server load test reports.")
    parser.add_argument("--single-report", required=True)
    parser.add_argument("--multi-report", required=True)
    parser.add_argument("--max-multi-p95-regression-percent", type=float, default=25.0)
    parser.add_argument("--max-multi-p99-regression-percent", type=float, default=25.0)
    parser.add_argument("--min-multi-rps-ratio", type=float, default=0.8)
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    args = parser.parse_args()

    report = build_report(
        load_json(args.single_report),
        load_json(args.multi_report),
        max_multi_p95_regression_percent=args.max_multi_p95_regression_percent,
        max_multi_p99_regression_percent=args.max_multi_p99_regression_percent,
        min_multi_rps_ratio=args.min_multi_rps_ratio,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
