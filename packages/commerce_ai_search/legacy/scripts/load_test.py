from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import http.client
import io
import json
import math
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_mall_configs
from app.image_validation import validate_image_bytes
from app.instance import API_INSTANCE_HEADER
from app.url_safety import (
    normalize_http_base_url,
    normalize_http_origin,
    normalize_public_http_base_url,
    normalize_public_http_origin,
    open_public_http_request,
    product_url_contains_product_id,
    redact_url_for_report,
    safe_absolute_http_url,
)
from scripts.mall_config_check import product_url_template_prefix


MODES = ("text", "image", "mixed")
JSON_DUMP_SEPARATORS = (",", ":")
ADMIN_LOG_TAIL_LIMIT = 1000
SERVER_METRIC_DELTA_UNDERCOUNT_PROBLEMS = {
    "search_events": "server_metrics.delta.search_events_below_successful_responses",
    "image_search_events": "server_metrics.delta.image_search_events_below_successful_responses",
}
SERVER_METRIC_RUN_LOG_UNDERCOUNT_PROBLEMS = {
    "search_events": "server_metrics.run_log.search_events_below_successful_responses",
    "image_search_events": "server_metrics.run_log.image_search_events_below_successful_responses",
}
SERVER_METRIC_ZERO_DELTA_PROBLEMS = {
    "rate_limited_events": "server_metrics.delta.rate_limited_events_nonzero",
    "rate_limit_fallback_events": "server_metrics.delta.rate_limit_fallback_events_nonzero",
    "rate_limit_redis_backoff_failure_events": "server_metrics.delta.rate_limit_redis_backoff_failure_events_nonzero",
    "rate_limit_redis_backoff_skipped_operations": "server_metrics.delta.rate_limit_redis_backoff_skipped_operations_nonzero",
    "cache_error_count": "server_metrics.delta.cache_error_count_nonzero",
    "cache_clear_errors": "server_metrics.delta.cache_clear_errors_nonzero",
    "cache_redis_backoff_failure_events": "server_metrics.delta.cache_redis_backoff_failure_events_nonzero",
    "cache_redis_backoff_skipped_operations": "server_metrics.delta.cache_redis_backoff_skipped_operations_nonzero",
    "cache_lock_errors": "server_metrics.delta.cache_lock_errors_nonzero",
    "cache_lock_release_errors": "server_metrics.delta.cache_lock_release_errors_nonzero",
    "cache_lock_wait_timeouts": "server_metrics.delta.cache_lock_wait_timeouts_nonzero",
    "singleflight_wait_timeouts": "server_metrics.delta.singleflight_wait_timeouts_nonzero",
    "image_validation_wait_timeouts": "server_metrics.delta.image_validation_wait_timeouts_nonzero",
    "qwen_query_vector_wait_timeouts": "server_metrics.delta.qwen_query_vector_wait_timeouts_nonzero",
    "backend_marqo_error_responses": "server_metrics.delta.backend_marqo_error_responses_nonzero",
    "backend_marqo_connection_close_responses": "server_metrics.delta.backend_marqo_connection_close_responses_nonzero",
    "backend_marqo_retry_after_responses": "server_metrics.delta.backend_marqo_retry_after_responses_nonzero",
    "backend_marqo_connection_acquire_wait_timeouts": (
        "server_metrics.delta.backend_marqo_connection_acquire_wait_timeouts_nonzero"
    ),
    "backend_marqo_circuit_open_events": "server_metrics.delta.backend_marqo_circuit_open_events_nonzero",
    "backend_marqo_circuit_short_circuits": "server_metrics.delta.backend_marqo_circuit_short_circuits_nonzero",
    "backend_qwen_error_responses": "server_metrics.delta.backend_qwen_error_responses_nonzero",
    "backend_qwen_connection_close_responses": "server_metrics.delta.backend_qwen_connection_close_responses_nonzero",
    "backend_qwen_retry_after_responses": "server_metrics.delta.backend_qwen_retry_after_responses_nonzero",
    "backend_qwen_connection_acquire_wait_timeouts": (
        "server_metrics.delta.backend_qwen_connection_acquire_wait_timeouts_nonzero"
    ),
    "backend_qwen_circuit_open_events": "server_metrics.delta.backend_qwen_circuit_open_events_nonzero",
    "backend_qwen_circuit_short_circuits": "server_metrics.delta.backend_qwen_circuit_short_circuits_nonzero",
    "search_queue_full_events": "server_metrics.delta.search_queue_full_events_nonzero",
    "image_queue_full_events": "server_metrics.delta.image_queue_full_events_nonzero",
    "result_mall_id_mismatch_events": "server_metrics.delta.result_mall_id_mismatch_events_nonzero",
    "result_mall_id_mismatch_count": "server_metrics.delta.result_mall_id_mismatch_count_nonzero",
    "search_log_write_errors": "server_metrics.delta.search_log_write_errors_nonzero",
    "error_log_write_errors": "server_metrics.delta.error_log_write_errors_nonzero",
}
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
DEFAULT_P99_TO_P95_RATIO = 1.6
DEFAULT_SERVER_WAIT_AVG_TO_P95_RATIO = 0.2
DEFAULT_SERVER_WAIT_MIN_AVG_MS = 250.0
DEFAULT_REQUEST_TIMEOUT_TO_P99_RATIO = 2.0
MAX_REQUEST_TIMEOUT_TO_P99_RATIO = 3.0
DEFAULT_REQUEST_TIMEOUT_MIN_SECONDS = 10.0
DEFAULT_MIN_RPS_TO_P95_CAPACITY_RATIO = 0.25
DEFAULT_MIN_RPS_FLOOR = 1.0
DEFAULT_MAX_PROCESS_RSS_GROWTH_MB = 512.0
MIN_API_INSTANCE_TRAFFIC_SHARE = 0.05
BYTES_PER_MIB = 1024 * 1024
SETTLED_SERVER_IN_FLIGHT_FIELDS = [
    "singleflight_in_flight",
    "image_validation_in_flight",
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
    ("image_validation", "image_validation_wait_events", "image_validation_total_wait_ms"),
    ("qwen_query_vector", "qwen_query_vector_wait_events", "qwen_query_vector_total_wait_ms"),
    (
        "backend_marqo_connection_acquire",
        "backend_marqo_connection_acquire_wait_events",
        "backend_marqo_total_connection_acquire_wait_ms",
    ),
    (
        "backend_qwen_connection_acquire",
        "backend_qwen_connection_acquire_wait_events",
        "backend_qwen_total_connection_acquire_wait_ms",
    ),
    ("search_queue", "search_queue_wait_events", "search_queue_total_wait_ms"),
    ("image_queue", "image_queue_wait_events", "image_queue_total_wait_ms"),
]
API_THREADPOOL_SERVER_METRIC_FIELDS = [
    "api_threadpool_ok",
    "api_threadpool_configured_tokens",
    "api_threadpool_runtime_tokens",
    "api_threadpool_required_tokens",
]
AGGREGATED_SERVER_METRIC_SUM_FIELDS = {
    "backend_marqo_request_attempts",
    "backend_marqo_connections_opened",
    "backend_marqo_connection_reuses",
    "backend_marqo_idle_reconnects",
    "backend_marqo_stale_reconnects",
    "backend_marqo_error_responses",
    "backend_marqo_connection_close_responses",
    "backend_marqo_gzip_responses",
    "backend_marqo_retry_after_responses",
    "backend_marqo_connection_acquire_wait_events",
    "backend_marqo_connection_acquire_wait_timeouts",
    "backend_marqo_total_connection_acquire_wait_ms",
    "backend_marqo_total_elapsed_ms",
    "backend_marqo_total_request_body_bytes",
    "backend_marqo_total_response_body_bytes",
    "backend_marqo_total_decoded_response_body_bytes",
    "backend_marqo_circuit_open_events",
    "backend_marqo_circuit_short_circuits",
    "backend_marqo_circuit_recovery_events",
    "backend_qwen_request_attempts",
    "backend_qwen_connections_opened",
    "backend_qwen_connection_reuses",
    "backend_qwen_idle_reconnects",
    "backend_qwen_stale_reconnects",
    "backend_qwen_error_responses",
    "backend_qwen_connection_close_responses",
    "backend_qwen_gzip_responses",
    "backend_qwen_retry_after_responses",
    "backend_qwen_connection_acquire_wait_events",
    "backend_qwen_connection_acquire_wait_timeouts",
    "backend_qwen_total_connection_acquire_wait_ms",
    "backend_qwen_total_elapsed_ms",
    "backend_qwen_total_request_body_bytes",
    "backend_qwen_total_response_body_bytes",
    "backend_qwen_total_decoded_response_body_bytes",
    "backend_qwen_circuit_open_events",
    "backend_qwen_circuit_short_circuits",
    "backend_qwen_circuit_recovery_events",
    "cache_evictions",
    "cache_redis_backoff_failure_events",
    "cache_redis_backoff_skipped_operations",
    "cache_error_count",
    "cache_get_errors",
    "cache_set_errors",
    "cache_decode_errors",
    "cache_delete_errors",
    "cache_clear_errors",
    "cache_lock_claims",
    "cache_lock_contention_events",
    "cache_lock_errors",
    "cache_lock_release_errors",
    "cache_lock_wait_events",
    "cache_lock_wait_timeouts",
    "cache_lock_total_wait_ms",
    "error_log_write_errors",
    "error_log_write_events",
    "error_log_write_total_ms",
    "error_log_output_opens",
    "error_log_output_reuses",
    "error_log_output_closes",
    "error_log_idle_closes",
    "image_queue_acquired_events",
    "image_queue_available_slots",
    "image_queue_full_events",
    "image_queue_in_flight",
    "image_queue_total_wait_ms",
    "image_queue_wait_events",
    "image_search_events",
    "image_validation_in_flight",
    "image_validation_cache_entry_count",
    "image_validation_cache_evictions",
    "image_validation_cache_hits",
    "image_validation_cache_max_entries",
    "image_validation_cache_misses",
    "image_validation_total_wait_ms",
    "image_validation_wait_events",
    "image_validation_wait_timeouts",
    "process_cpu_percent",
    "process_memory_rss_bytes",
    "process_thread_count",
    "qwen_query_vector_in_flight",
    "qwen_query_vector_runtime_entries",
    "qwen_query_vector_runtime_image_entries",
    "qwen_query_vector_runtime_text_entries",
    "qwen_query_vector_total_wait_ms",
    "qwen_query_vector_wait_events",
    "qwen_query_vector_wait_timeouts",
    "rate_limit_fallback_bucket_count",
    "rate_limit_fallback_events",
    "rate_limit_fallback_skipped_redis_events",
    "rate_limit_fallback_pruned_buckets",
    "rate_limit_redis_backoff_failure_events",
    "rate_limit_redis_backoff_skipped_operations",
    "rate_limited_events",
    "result_mall_id_mismatch_count",
    "result_mall_id_mismatch_events",
    "search_events",
    "engine_search_attempts",
    "engine_adaptive_refetches",
    "engine_adaptive_refetch_searches",
    "engine_underfilled_after_max_candidates_events",
    "search_log_write_errors",
    "search_log_write_events",
    "search_log_write_total_ms",
    "search_log_output_opens",
    "search_log_output_reuses",
    "search_log_output_closes",
    "search_log_idle_closes",
    "search_queue_acquired_events",
    "search_queue_available_slots",
    "search_queue_full_events",
    "search_queue_in_flight",
    "search_queue_total_wait_ms",
    "search_queue_wait_events",
    "singleflight_in_flight",
    "singleflight_total_wait_ms",
    "singleflight_wait_events",
    "singleflight_wait_timeouts",
}
AGGREGATED_SERVER_METRIC_MAX_FIELDS = {
    "backend_marqo_max_decoded_response_body_bytes",
    "backend_marqo_max_elapsed_ms",
    "backend_marqo_max_active_requests_observed",
    "backend_marqo_max_connection_acquire_wait_ms",
    "backend_marqo_max_request_body_bytes",
    "backend_marqo_max_response_body_bytes",
    "backend_marqo_max_retry_after_seconds",
    "backend_qwen_max_decoded_response_body_bytes",
    "backend_qwen_max_elapsed_ms",
    "backend_qwen_max_active_requests_observed",
    "backend_qwen_max_connection_acquire_wait_ms",
    "backend_qwen_max_request_body_bytes",
    "backend_qwen_max_response_body_bytes",
    "backend_qwen_max_retry_after_seconds",
    "cache_lock_max_wait_ms",
    "cache_redis_backoff_active",
    "cache_redis_backoff_remaining_ms",
    "disk_used_percent",
    "engine_max_search_attempts",
    "engine_max_final_candidate_limit",
    "image_queue_max_wait_ms",
    "error_log_write_max_ms",
    "error_log_write_last_ms",
    "image_validation_max_wait_ms",
    "qwen_query_vector_max_wait_ms",
    "search_log_write_max_ms",
    "search_log_write_last_ms",
    "search_queue_max_wait_ms",
    "singleflight_max_wait_ms",
    "system_cpu_percent",
    "system_memory_used_percent",
    "rate_limit_redis_backoff_active",
    "rate_limit_redis_backoff_remaining_ms",
}
AGGREGATED_SERVER_METRIC_MIN_FIELDS = {
    "disk_free_bytes",
}
AGGREGATED_SERVER_METRIC_RECOMPUTED_AVG_FIELDS = {
    "backend_marqo_avg_elapsed_ms",
    "backend_marqo_avg_connection_acquire_wait_ms",
    "backend_qwen_avg_elapsed_ms",
    "backend_qwen_avg_connection_acquire_wait_ms",
    "engine_average_search_attempts",
    "cache_lock_avg_wait_ms",
    "error_log_write_avg_ms",
    "image_queue_avg_wait_ms",
    "image_validation_avg_wait_ms",
    "qwen_query_vector_avg_wait_ms",
    "search_log_write_avg_ms",
    "search_queue_avg_wait_ms",
    "singleflight_avg_wait_ms",
}
AGGREGATED_SERVER_METRIC_IGNORE_INCONSISTENCY_FIELDS = {
    "engine_health_cache_age_ms",
    "engine_health_cache_hit",
    "generated_at",
    "process_instance_id",
    "search_log_buffer_open",
    "error_log_buffer_open",
}
DEFAULT_TRAFFIC_MIX = {"text": 70.0, "image": 10.0, "mixed": 20.0}
REQUIRED_RESULT_FIELDS = {
    "product_id",
    "name",
    "category",
    "price",
    "image_url",
    "product_url",
    "score",
    "score_percent",
    "mall_id",
    "source_scores",
}
REQUIRED_META_FIELDS = {
    "query_type",
    "elapsed_ms",
    "engine",
    "limit",
    "offset",
    "has_more",
    "next_offset",
    "mall_id",
    "text_weight",
    "image_weight",
    "low_confidence",
    "notice",
}
ALLOW_LOCAL_TARGET = False
ALLOW_PRIVATE_ADMIN_METRICS_TARGETS = False
QUERIES = [
    "검은 우산",
    "스텐 텀블러",
    "점착 메모지",
    "탁상 달력",
    "크리스탈 상패",
    "고급 볼펜",
    "친환경 가방",
]


@dataclass(frozen=True)
class LoadRequestIdentity:
    mall_id: str
    api_key: str
    origin: str
    expected_product_url_prefix: str


@dataclass(frozen=True)
class LoadRequestSpec:
    payload: dict[str, Any]
    headers: dict[str, str]
    expected_product_url_prefix: str
    identity: LoadRequestIdentity


@dataclass(frozen=True)
class ImageLoadInput:
    data_url: str
    file: str
    size_bytes: int
    width: int
    height: int
    sha256: str


def make_png_data_url() -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(12, 120, 110)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def normalize_image_file_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_values = list(value)
    else:
        raw_values = [value]
    paths: list[str] = []
    for raw_value in raw_values:
        for item in str(raw_value or "").split(","):
            text = item.strip()
            if text:
                paths.append(text)
    return paths


def image_file_list_values(value: Any) -> list[str]:
    paths: list[str] = []
    for file_list_path in normalize_image_file_values(value):
        source = Path(file_list_path)
        for line in source.read_text(encoding="utf-8-sig").splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                paths.append(text)
    return paths


def image_file_paths_for_args(args: argparse.Namespace) -> list[str]:
    paths: list[str] = []
    for value in [
        getattr(args, "image_file", ""),
        getattr(args, "image_files", ""),
        getattr(args, "additional_image_file", []),
        getattr(args, "additional_image_files", []),
    ]:
        paths.extend(normalize_image_file_values(value))
    paths.extend(image_file_list_values(getattr(args, "image_file_list", "")))
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = str(Path(path))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def image_load_input_for_file(path_text: str, max_bytes: int) -> ImageLoadInput:
    image = validate_image_bytes(Path(path_text).read_bytes(), max_bytes=max_bytes)
    return ImageLoadInput(
        data_url=image.data_url,
        file=path_text,
        size_bytes=image.size_bytes,
        width=image.width,
        height=image.height,
        sha256=image.sha256,
    )


def image_input_record(input_item: ImageLoadInput) -> dict[str, Any]:
    return {
        "file": input_item.file,
        "size_bytes": input_item.size_bytes,
        "width": input_item.width,
        "height": input_item.height,
        "sha256": input_item.sha256,
    }


def image_data_urls_for_args(args: argparse.Namespace) -> tuple[list[str], dict[str, Any]]:
    image_paths = image_file_paths_for_args(args)
    mode = str(getattr(args, "mode", "") or "")
    scenario = str(getattr(args, "scenario", "single") or "single")
    requires_image = bool(image_paths) or mode in {"image", "mixed"} or scenario != "single"
    if not requires_image:
        return [], {"source": None}
    max_bytes = int(getattr(args, "image_max_mb", 10) or 10) * 1024 * 1024
    if image_paths:
        inputs = [image_load_input_for_file(path, max_bytes) for path in image_paths]
        if len(inputs) == 1:
            image = inputs[0]
            return [image.data_url], {
                "source": "file",
                "file": image.file,
                "size_bytes": image.size_bytes,
                "width": image.width,
                "height": image.height,
                "sha256": image.sha256,
            }
        first = inputs[0]
        sha256_values = [item.sha256 for item in inputs]
        return [item.data_url for item in inputs], {
            "source": "files",
            "file": first.file,
            "files": [item.file for item in inputs],
            "file_count": len(inputs),
            "unique_sha256_count": len(set(sha256_values)),
            "size_bytes": first.size_bytes,
            "total_size_bytes": sum(item.size_bytes for item in inputs),
            "width": first.width,
            "height": first.height,
            "sha256": first.sha256,
            "sha256_values": sha256_values[:50],
            "sha256_overflow": max(len(sha256_values) - 50, 0),
            "images": [image_input_record(item) for item in inputs[:50]],
            "image_overflow": max(len(inputs) - 50, 0),
        }
    return [make_png_data_url()], {"source": "generated"}


def image_data_url_for_args(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    image_data_urls, image_input = image_data_urls_for_args(args)
    return (image_data_urls[0] if image_data_urls else ""), image_input


def image_data_url_for_request(image_data_urls: list[str], index: int) -> str:
    if not image_data_urls:
        return ""
    return image_data_urls[index % len(image_data_urls)]


def build_payload(
    index: int,
    mode: str,
    mall_id: str,
    limit: int,
    image_base64: str,
    unique_query_suffix: str = "",
) -> dict[str, Any]:
    query = QUERIES[index % len(QUERIES)]
    suffix = str(unique_query_suffix or "").strip()
    if suffix:
        query = f"{query} {suffix}-{index + 1:05d}"
    payload: dict[str, Any] = {"mall_id": mall_id, "limit": limit}
    if mode in {"text", "mixed"}:
        payload["q"] = query
    if mode in {"image", "mixed"}:
        payload["image_base64"] = image_base64
    return payload


def expected_query_type(payload: dict[str, Any]) -> str:
    has_text = bool(str(payload.get("q") or "").strip())
    has_image = bool(str(payload.get("image_base64") or "").strip())
    if has_text and has_image:
        return "text_image"
    if has_image:
        return "image"
    return "text"


def stable_digest(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_signature_payload(payload: dict[str, Any]) -> dict[str, Any]:
    image_digest = stable_digest(payload.get("image_base64"))
    return {
        "mall_id": str(payload.get("mall_id") or "").strip(),
        "q": str(payload.get("q") or "").strip(),
        "category": str(payload.get("category") or "").strip(),
        "limit": payload.get("limit"),
        "offset": payload.get("offset"),
        "has_image": bool(image_digest),
        "image_sha256": image_digest[:16] if image_digest else "",
    }


def request_signature(payload: dict[str, Any]) -> str:
    return json.dumps(request_signature_payload(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def summarize_request_profile(request_specs: list[LoadRequestSpec]) -> dict[str, Any]:
    signatures = Counter()
    by_query_type: dict[str, set[str]] = {}
    by_mall_id: dict[str, set[str]] = {}
    image_digests: set[str] = set()
    text_queries: set[str] = set()
    for spec in request_specs:
        payload = spec.payload
        signature = request_signature(payload)
        signatures[signature] += 1
        query_type = expected_query_type(payload)
        by_query_type.setdefault(query_type, set()).add(signature)
        mall_id = str(payload.get("mall_id") or "").strip() or "missing"
        by_mall_id.setdefault(mall_id, set()).add(signature)
        image_digest = stable_digest(payload.get("image_base64"))
        if image_digest:
            image_digests.add(image_digest)
        query = str(payload.get("q") or "").strip()
        if query:
            text_queries.add(query)
    repeated = sum(count - 1 for count in signatures.values() if count > 1)
    unique_signatures = len(signatures)
    unique_image_inputs = len(image_digests)
    return {
        "total_requests": len(request_specs),
        "unique_request_signatures": unique_signatures,
        "repeated_request_count": repeated,
        "unique_by_query_type": {
            query_type: len(values)
            for query_type, values in sorted(by_query_type.items())
        },
        "unique_by_mall_id_count": {
            mall_id: len(values)
            for mall_id, values in sorted(by_mall_id.items())
        },
        "distinct_mall_count": len(by_mall_id),
        "unique_text_queries": len(text_queries),
        "unique_image_inputs": unique_image_inputs,
        "min_backend_marqo_request_attempts": unique_signatures,
        "min_backend_qwen_request_attempts": unique_image_inputs,
        "min_backend_gemini_request_attempts": unique_image_inputs,
    }


def requested_mall_id(payload: dict[str, Any]) -> str:
    return str(payload.get("mall_id") or payload.get("site_id") or "").strip()


def is_contract_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def normalized_url_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    if scheme.lower() == "https":
        return 443
    if scheme.lower() == "http":
        return 80
    return None


def url_matches_prefix(url: str, prefix: str) -> bool:
    url_text = str(url or "").strip()
    prefix_text = str(prefix or "").strip().rstrip("/")
    if not url_text or not prefix_text:
        return False
    safe_url = safe_absolute_http_url(url_text)
    safe_prefix = safe_absolute_http_url(prefix_text)
    if safe_url is None or safe_prefix is None:
        return False
    from urllib.parse import urlparse

    parsed_url = urlparse(safe_url)
    parsed_prefix = urlparse(safe_prefix)
    if parsed_url.scheme.lower() != parsed_prefix.scheme.lower():
        return False
    if (parsed_url.hostname or "").lower() != (parsed_prefix.hostname or "").lower():
        return False
    if normalized_url_port(parsed_url.scheme, parsed_url.port) != normalized_url_port(parsed_prefix.scheme, parsed_prefix.port):
        return False
    prefix_path = parsed_prefix.path.rstrip("/")
    if not prefix_path:
        return True
    url_path = parsed_url.path.rstrip("/")
    return url_path == prefix_path or url_path.startswith(prefix_path + "/")


def normalize_expected_product_url_prefix(value: Any, option_name: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if safe_absolute_http_url(text) is None:
        raise ValueError(f"{option_name} must be a safe public http(s) URL prefix")
    return text


def expected_product_url_prefix_for_args(args: argparse.Namespace) -> str:
    explicit_prefix = normalize_expected_product_url_prefix(
        getattr(args, "expected_product_url_prefix", ""),
        "--expected-product-url-prefix",
    )
    if int(getattr(args, "mall_sample_size", 0) or 0) > 0:
        if explicit_prefix:
            raise ValueError("--expected-product-url-prefix cannot be combined with --mall-sample-size")
        return ""
    if explicit_prefix:
        return explicit_prefix
    mall_config_path = str(getattr(args, "mall_config", "") or "").strip()
    if not mall_config_path:
        return ""
    mall_id = str(getattr(args, "mall_id", "") or "").strip()
    if not mall_id:
        raise ValueError("--mall-id is required when --mall-config is set")
    path = Path(mall_config_path)
    if not path.exists():
        raise ValueError("--mall-config does not exist")
    malls = load_mall_configs(path)
    mall = malls.get(mall_id)
    if mall is None or not mall.enabled:
        raise ValueError("--mall-config must contain an enabled entry for --mall-id")
    prefix = product_url_template_prefix(mall.product_url_template or "", mall.mall_id) or ""
    if not prefix:
        raise ValueError("--mall-config mall product_url_template must produce a safe product URL prefix")
    return normalize_expected_product_url_prefix(prefix, "--mall-config product_url_template")


def load_mall_identities(args: argparse.Namespace) -> list[LoadRequestIdentity]:
    sample_size = max(0, int(getattr(args, "mall_sample_size", 0) or 0))
    sampling_strategy = str(getattr(args, "mall_sample_strategy", "spread") or "spread").strip().lower()
    if sampling_strategy not in {"spread", "first"}:
        raise ValueError("--mall-sample-strategy must be spread or first")
    args.mall_sample_strategy = sampling_strategy
    if sample_size <= 0:
        args.mall_sample_source_enabled_count = 0
        args.mall_sample_eligible_count = 0
        return [
            LoadRequestIdentity(
                mall_id=str(getattr(args, "mall_id", "") or "").strip(),
                api_key=str(getattr(args, "api_key", "") or ""),
                origin=str(getattr(args, "origin", "") or ""),
                expected_product_url_prefix=str(getattr(args, "expected_product_url_prefix", "") or ""),
            )
        ]

    mall_config_path = str(getattr(args, "mall_config", "") or "").strip()
    if not mall_config_path:
        raise ValueError("--mall-config is required when --mall-sample-size is greater than 0")
    path = Path(mall_config_path)
    if not path.exists():
        raise ValueError("--mall-config does not exist")

    origin_normalizer = normalize_http_origin if getattr(args, "allow_local_target", False) else normalize_public_http_origin
    fallback_api_key = str(getattr(args, "api_key", "") or "")
    fallback_origin = str(getattr(args, "origin", "") or "")
    identities: list[LoadRequestIdentity] = []
    problems: list[str] = []
    enabled_count = 0
    for mall in sorted(load_mall_configs(path).values(), key=lambda item: item.mall_id):
        if not mall.enabled:
            continue
        enabled_count += 1
        api_key = str(mall.api_key or fallback_api_key or "")
        raw_origin = next(
            (
                str(origin or "").strip()
                for origin in mall.allowed_origins
                if str(origin or "").strip() and str(origin or "").strip() != "*"
            ),
            fallback_origin,
        )
        try:
            origin = origin_normalizer(raw_origin, f"--mall-config allowed_origins for {mall.mall_id}") if raw_origin else ""
        except ValueError as exc:
            problems.append(f"{mall.mall_id}: {exc}")
            continue
        prefix = product_url_template_prefix(mall.product_url_template or "", mall.mall_id) or ""
        if prefix:
            try:
                prefix = normalize_expected_product_url_prefix(prefix, f"--mall-config product_url_template for {mall.mall_id}")
            except ValueError as exc:
                problems.append(f"{mall.mall_id}: {exc}")
                continue
        missing = []
        if not api_key:
            missing.append("api_key")
        if not origin:
            missing.append("allowed_origins")
        if not prefix:
            missing.append("product_url_template")
        if missing:
            problems.append(f"{mall.mall_id}: missing " + ", ".join(missing))
            continue
        identities.append(
            LoadRequestIdentity(
                mall_id=mall.mall_id,
                api_key=api_key,
                origin=origin,
                expected_product_url_prefix=prefix,
            )
        )
    args.mall_sample_source_enabled_count = enabled_count
    args.mall_sample_eligible_count = len(identities)
    if len(identities) < sample_size:
        suffix = "; ".join(problems[:5])
        detail = f"; first problems: {suffix}" if suffix else ""
        raise ValueError(
            f"--mall-sample-size requested {sample_size} enabled complete mall identities, "
            f"but only {len(identities)} were usable{detail}"
        )
    return select_mall_identity_sample(identities, sample_size, sampling_strategy)


def select_mall_identity_sample(
    identities: list[LoadRequestIdentity],
    sample_size: int,
    strategy: str = "spread",
) -> list[LoadRequestIdentity]:
    sample_size = max(0, int(sample_size or 0))
    if sample_size <= 0:
        return []
    if len(identities) <= sample_size:
        return list(identities)
    normalized_strategy = str(strategy or "spread").strip().lower()
    if normalized_strategy == "first":
        return list(identities[:sample_size])
    if normalized_strategy != "spread":
        raise ValueError("--mall-sample-strategy must be spread or first")
    if sample_size == 1:
        return [identities[0]]
    last_index = len(identities) - 1
    selected_indexes: list[int] = []
    seen: set[int] = set()
    for sample_index in range(sample_size):
        source_index = int(round((sample_index * last_index) / (sample_size - 1)))
        if source_index in seen:
            continue
        seen.add(source_index)
        selected_indexes.append(source_index)
    if len(selected_indexes) < sample_size:
        for source_index in range(len(identities)):
            if source_index in seen:
                continue
            seen.add(source_index)
            selected_indexes.append(source_index)
            if len(selected_indexes) >= sample_size:
                break
    return [identities[index] for index in sorted(selected_indexes[:sample_size])]


def build_request_specs(
    payloads: list[dict[str, Any]],
    identities: list[LoadRequestIdentity],
    client_ip_count: int = 0,
    client_ip_prefix: str = "198.51.100",
) -> list[LoadRequestSpec]:
    if not identities:
        raise ValueError("at least one load request identity is required")
    specs: list[LoadRequestSpec] = []
    for index, payload in enumerate(payloads):
        identity = identities[index % len(identities)]
        request_payload = dict(payload)
        request_payload["mall_id"] = identity.mall_id
        headers: dict[str, str] = {}
        if identity.api_key:
            headers["X-API-Key"] = identity.api_key
        if identity.origin:
            headers["Origin"] = identity.origin
        client_ip = spoofed_client_ip(index, client_ip_count, client_ip_prefix)
        if client_ip:
            headers["X-Forwarded-For"] = client_ip
        specs.append(
            LoadRequestSpec(
                payload=request_payload,
                headers=headers,
                expected_product_url_prefix=identity.expected_product_url_prefix,
                identity=identity,
            )
        )
    return specs


def spoofed_client_ip(index: int, client_ip_count: int, client_ip_prefix: str) -> str:
    count = max(0, int(client_ip_count or 0))
    if count <= 0:
        return ""
    prefix = str(client_ip_prefix or "198.51.100").strip().strip(".")
    octets = prefix.split(".")
    if len(octets) != 3 or any(not item.isdigit() or not 0 <= int(item) <= 255 for item in octets):
        raise ValueError("--client-ip-prefix must be the first three IPv4 octets, for example 198.51.100")
    host = (index % count) + 1
    if host > 254:
        subnet = (int(octets[2]) + ((host - 1) // 254)) % 256
        host = ((host - 1) % 254) + 1
        octets = [octets[0], octets[1], str(subnet)]
    return ".".join([*octets, str(host)])


def load_identity_summary(
    identities: list[LoadRequestIdentity],
    requested_sample_size: int,
    sampling_strategy: str = "",
    source_enabled_count: int | None = None,
    eligible_mall_count: int | None = None,
) -> dict[str, Any]:
    sampled_mall_ids = [identity.mall_id for identity in identities]
    origins = [identity.origin for identity in identities if identity.origin]
    prefixes = [identity.expected_product_url_prefix for identity in identities if identity.expected_product_url_prefix]
    requested_sample_size = max(0, int(requested_sample_size or 0))
    effective_strategy = str(sampling_strategy or ("provided" if requested_sample_size > 0 else "single")).strip()
    effective_source_count = (
        max(0, int(source_enabled_count))
        if source_enabled_count is not None
        else (len(identities) if requested_sample_size > 0 else 0)
    )
    effective_eligible_count = (
        max(0, int(eligible_mall_count))
        if eligible_mall_count is not None
        else (len(identities) if requested_sample_size > 0 else 0)
    )
    return {
        "enabled": requested_sample_size > 0,
        "sample_size_requested": requested_sample_size,
        "sampling_strategy": effective_strategy,
        "source_enabled_count": effective_source_count,
        "eligible_mall_count": effective_eligible_count,
        "sampled_count": len(identities),
        "distinct_mall_count": len(set(sampled_mall_ids)),
        "sampled_mall_ids": sampled_mall_ids[:50],
        "sampled_mall_id_overflow": max(len(sampled_mall_ids) - 50, 0),
        "per_mall_api_keys": len({identity.api_key for identity in identities if identity.api_key}) > 1,
        "per_mall_origins": len(set(origins)) > 1,
        "per_mall_product_url_prefixes": len(set(prefixes)) > 1,
    }


def validate_result_item_shape(
    item: dict[str, Any],
    section: str,
    index: int,
    expected_mall_id: str = "",
    expected_product_url_prefix: str = "",
) -> str | None:
    prefix = f"search response {section}[{index}]"
    product_id = str(item.get("product_id") or "").strip()
    for field_name in ["product_id", "name", "category", "mall_id"]:
        value = item.get(field_name)
        if not isinstance(value, str) or not value.strip():
            return f"{prefix} {field_name} must be a non-empty string"
        if field_name == "mall_id" and expected_mall_id and value.strip() != expected_mall_id:
            return f"{prefix} mall_id must match requested mall_id"
    price = item.get("price")
    if price is not None and not is_contract_number(price):
        return f"{prefix} price must be a number or null"
    if is_contract_number(price) and (not math.isfinite(float(price)) or price < 0):
        return f"{prefix} price must be finite and non-negative"
    for field_name in ["image_url", "product_url"]:
        value = item.get(field_name)
        if not isinstance(value, str) or not value.strip():
            return f"{prefix} {field_name} must be a non-empty URL string"
        if safe_absolute_http_url(value) is None:
            return f"{prefix} {field_name} must be a safe public http(s) URL"
        if field_name == "product_url" and not product_url_contains_product_id(value, product_id):
            return f"{prefix} product_url must contain product_id"
        if field_name == "product_url" and expected_product_url_prefix and not url_matches_prefix(
            value,
            expected_product_url_prefix,
        ):
            return f"{prefix} product_url must match expected product URL prefix"
    score = item.get("score")
    if not is_contract_number(score) or not math.isfinite(float(score)) or not 0 <= score <= 1:
        return f"{prefix} score must be 0..1"
    score_percent = item.get("score_percent")
    if not is_contract_number(score_percent) or not math.isfinite(float(score_percent)) or not 0 <= score_percent <= 100:
        return f"{prefix} score_percent must be 0..100"
    for source, value in item.get("source_scores", {}).items():
        if not is_contract_number(value) or not math.isfinite(float(value)):
            return f"{prefix} source_scores.{source} must be finite number"
    return None


def validate_meta_shape(meta: dict[str, Any], expected_type: str, expected_mall_id: str = "") -> str | None:
    if not is_contract_number(meta.get("elapsed_ms")) or meta.get("elapsed_ms") < 0:
        return "search response meta elapsed_ms must be non-negative"
    if not isinstance(meta.get("limit"), int) or meta.get("limit") < 1:
        return "search response meta limit must be a positive integer"
    if not isinstance(meta.get("offset"), int) or meta.get("offset") < 0:
        return "search response meta offset must be a non-negative integer"
    if not isinstance(meta.get("has_more"), bool):
        return "search response meta has_more must be boolean"
    if meta.get("next_offset") is not None and not isinstance(meta.get("next_offset"), int):
        return "search response meta next_offset must be integer or null"
    if meta.get("next_offset") is not None and meta.get("next_offset") < 0:
        return "search response meta next_offset must be non-negative or null"
    if meta.get("has_more") is True:
        if meta.get("next_offset") is None:
            return "search response meta next_offset is required when has_more is true"
        if meta.get("next_offset") <= meta.get("offset"):
            return "search response meta next_offset must be greater than offset"
    elif meta.get("has_more") is False and meta.get("next_offset") is not None:
        return "search response meta next_offset must be null when has_more is false"
    if not isinstance(meta.get("low_confidence"), bool):
        return "search response meta low_confidence must be boolean"
    if meta.get("notice") is not None and not isinstance(meta.get("notice"), str):
        return "search response meta notice must be string or null"
    mall_id = meta.get("mall_id")
    if not isinstance(mall_id, str) or not mall_id.strip():
        return "search response meta mall_id must be a non-empty string"
    if expected_mall_id and mall_id.strip() != expected_mall_id:
        return "search response meta mall_id must match requested mall_id"
    if meta.get("text_weight") is not None and not is_contract_number(meta.get("text_weight")):
        return "search response meta text_weight must be a number or null"
    if meta.get("image_weight") is not None and not is_contract_number(meta.get("image_weight")):
        return "search response meta image_weight must be a number or null"
    if is_contract_number(meta.get("text_weight")) and meta.get("text_weight") < 0:
        return "search response meta text_weight must be non-negative"
    if is_contract_number(meta.get("image_weight")) and meta.get("image_weight") < 0:
        return "search response meta image_weight must be non-negative"
    if expected_type == "text":
        if not is_contract_number(meta.get("text_weight")):
            return "search response text query must include text_weight"
        if meta.get("image_weight") is not None:
            return "search response text query image_weight must be null"
    elif expected_type == "image":
        if meta.get("text_weight") is not None:
            return "search response image query text_weight must be null"
        if not is_contract_number(meta.get("image_weight")):
            return "search response image query must include image_weight"
    elif expected_type == "text_image":
        if not is_contract_number(meta.get("text_weight")):
            return "search response mixed query must include text_weight"
        if not is_contract_number(meta.get("image_weight")):
            return "search response mixed query must include image_weight"
    return None


def validate_search_response(
    data: Any,
    payload: dict[str, Any],
    expected_product_url_prefix: str = "",
) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "search response must be a JSON object"
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return False, "search response missing meta object"
    missing_meta = sorted(REQUIRED_META_FIELDS - set(meta))
    if missing_meta:
        return False, f"search response meta missing fields: {', '.join(missing_meta)}"
    expected_type = expected_query_type(payload)
    if meta.get("query_type") != expected_type:
        return False, f"search response query_type must be {expected_type}"
    expected_mall_id = requested_mall_id(payload)
    meta_error = validate_meta_shape(meta, expected_type, expected_mall_id)
    if meta_error:
        return False, meta_error
    top = data.get("top")
    items = data.get("items")
    categories = data.get("suggested_categories")
    if not isinstance(top, list):
        return False, "search response missing top list"
    if not top:
        return False, "search response should include top results"
    if len(top) > 3:
        return False, "search response top list must contain at most 3 items"
    if not isinstance(items, list):
        return False, "search response missing items list"
    if not items:
        return False, "search response should include related items"
    if len(items) > meta.get("limit"):
        return False, "search response related items must not exceed meta.limit"
    if meta.get("has_more") is True and meta.get("next_offset") != meta.get("offset") + len(items):
        return False, "search response meta next_offset must equal offset plus related item count"
    if not isinstance(categories, list):
        return False, "search response missing suggested_categories list"
    if not categories:
        return False, "search response should include suggested_categories"
    top_product_ids = [str(item.get("product_id")) for item in top if isinstance(item, dict)]
    item_product_ids = [str(item.get("product_id")) for item in items if isinstance(item, dict)]
    if len(set(top_product_ids)) != len(top_product_ids):
        return False, "search response top product_ids must be unique"
    if len(set(item_product_ids)) != len(item_product_ids):
        return False, "search response related item product_ids must be unique"
    repeated_ids = sorted(set(top_product_ids).intersection(item_product_ids))
    if repeated_ids:
        return False, "search response related items must exclude top product_ids"
    category_values = [str(category).strip() for category in categories]
    if len(set(category_values)) != len(category_values):
        return False, "search response suggested_categories must be unique"
    for section, results in [("top", top), ("items", items)]:
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                return False, f"search response {section}[{index}] must be an object"
            missing_fields = sorted(REQUIRED_RESULT_FIELDS - set(item))
            if missing_fields:
                return False, f"search response {section}[{index}] missing fields: {', '.join(missing_fields)}"
            if not isinstance(item.get("source_scores"), dict):
                return False, f"search response {section}[{index}] source_scores must be an object"
            item_error = validate_result_item_shape(item, section, index, expected_mall_id, expected_product_url_prefix)
            if item_error:
                return False, item_error
    return True, ""


def parse_traffic_mix(value: str) -> dict[str, float]:
    if not value:
        return dict(DEFAULT_TRAFFIC_MIX)
    weights: dict[str, float] = {}
    for part in value.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise ValueError("traffic mix must use mode=weight entries")
        mode, raw_weight = [item.strip() for item in part.split("=", 1)]
        if mode not in MODES:
            raise ValueError(f"unknown traffic mode: {mode}")
        weight = float(raw_weight)
        if weight < 0:
            raise ValueError("traffic mix weights must be non-negative")
        weights[mode] = weight
    for mode in MODES:
        weights.setdefault(mode, 0.0)
    if sum(weights.values()) <= 0:
        raise ValueError("traffic mix must contain at least one positive weight")
    return weights


def mode_for_request(index: int, weights: dict[str, float]) -> str:
    total = sum(weights.values())
    cursor = (index % 100) / 100 * total
    accumulated = 0.0
    for mode in MODES:
        accumulated += weights.get(mode, 0.0)
        if cursor < accumulated:
            return mode
    return "mixed"


def build_payloads(
    args: argparse.Namespace,
    image_base64: str | list[str],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, float]]:
    request_count = planned_request_count(args)
    image_data_urls = [image_base64] if isinstance(image_base64, str) else list(image_base64)
    if args.scenario == "single":
        payloads = [
            build_payload(
                index,
                args.mode,
                args.mall_id,
                args.limit,
                image_data_url_for_request(image_data_urls, index),
                getattr(args, "unique_query_suffix", ""),
            )
            for index in range(request_count)
        ]
        return payloads, {args.mode: len(payloads)}, {mode: 100.0 if mode == args.mode else 0.0 for mode in MODES}

    weights = parse_traffic_mix(args.traffic_mix)
    payloads = []
    mode_counts = Counter()
    for index in range(request_count):
        mode = mode_for_request(index, weights)
        mode_counts[mode] += 1
        payloads.append(
            build_payload(
                index,
                mode,
                args.mall_id,
                args.limit,
                image_data_url_for_request(image_data_urls, index),
                getattr(args, "unique_query_suffix", ""),
            )
        )
    total_weight = sum(weights.values())
    mix_percent = {mode: round((weights.get(mode, 0.0) / total_weight) * 100, 2) for mode in MODES}
    return payloads, dict(mode_counts), mix_percent


def planned_request_count(args: argparse.Namespace) -> int:
    requests = max(0, int(getattr(args, "requests", 0) or 0))
    if getattr(args, "scenario", "single") != "mixed-traffic":
        return requests
    active_users = max(0, int(getattr(args, "active_users", 0) or 0))
    return max(requests, active_users)


def raw_admin_metrics_base_urls(args: argparse.Namespace) -> list[str]:
    values = getattr(args, "admin_metrics_base_urls", None)
    if values is None:
        value = getattr(args, "admin_metrics_base_url", None)
        values = [value] if value else []
    if isinstance(values, str):
        values = [values]
    urls: list[str] = []
    for value in values or []:
        for item in str(value or "").split(","):
            text = item.strip()
            if text:
                urls.append(text)
    return urls


def normalize_admin_metrics_base_urls(args: argparse.Namespace) -> list[str]:
    raw_urls = raw_admin_metrics_base_urls(args)
    if not raw_urls:
        return [str(getattr(args, "base_url", "") or "").rstrip("/")]
    allow_private = bool(
        getattr(args, "allow_private_admin_metrics_targets", False)
        or getattr(args, "allow_local_target", False)
    )
    normalizer = normalize_http_base_url if allow_private else normalize_public_http_base_url
    normalized: list[str] = []
    for index, raw_url in enumerate(raw_urls, start=1):
        normalized_url = normalizer(raw_url, f"--admin-metrics-base-url[{index}]")
        if normalized_url not in normalized:
            normalized.append(normalized_url)
    return normalized


def validate_args(args: argparse.Namespace) -> None:
    base_normalizer = normalize_http_base_url if getattr(args, "allow_local_target", False) else normalize_public_http_base_url
    origin_normalizer = normalize_http_origin if getattr(args, "allow_local_target", False) else normalize_public_http_origin
    args.base_url = base_normalizer(getattr(args, "base_url", "http://localhost:8000"), "--base-url")
    args.admin_metrics_base_urls = normalize_admin_metrics_base_urls(args)
    origin_text = str(getattr(args, "origin", "") or "").strip()
    args.origin = origin_normalizer(origin_text, "--origin") if origin_text else ""
    args.mall_sample_size = max(0, int(getattr(args, "mall_sample_size", 0) or 0))
    args.mall_sample_strategy = str(getattr(args, "mall_sample_strategy", "spread") or "spread").strip().lower()
    if args.mall_sample_strategy not in {"spread", "first"}:
        raise ValueError("--mall-sample-strategy must be spread or first")
    if args.requests < 1:
        raise ValueError("--requests must be at least 1")
    if args.concurrency < 1:
        raise ValueError("--concurrency must be at least 1")
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    if args.p95_ms <= 0:
        raise ValueError("--p95-ms must be greater than 0")
    if not is_number(getattr(args, "p99_ms", 0)) or float(getattr(args, "p99_ms", 0) or 0) <= 0:
        args.p99_ms = default_p99_ms(args.p95_ms)
    if args.p99_ms < args.p95_ms:
        raise ValueError("--p99-ms must be greater than or equal to --p95-ms")
    if (
        not is_number(getattr(args, "request_timeout_seconds", 0))
        or float(getattr(args, "request_timeout_seconds", 0) or 0) <= 0
    ):
        args.request_timeout_seconds = default_request_timeout_seconds(args.p99_ms)
    if float(args.request_timeout_seconds) * 1000 < float(args.p99_ms):
        raise ValueError("--request-timeout-seconds must be greater than or equal to --p99-ms in seconds")
    if float(args.request_timeout_seconds) > max_request_timeout_seconds(args.p99_ms):
        raise ValueError("--request-timeout-seconds must not exceed 3x --p99-ms or the 10s minimum budget")
    if (
        not is_number(getattr(args, "max_server_wait_avg_ms", 0))
        or float(getattr(args, "max_server_wait_avg_ms", 0) or 0) <= 0
    ):
        args.max_server_wait_avg_ms = default_server_wait_avg_ms(args.p95_ms)
    if not is_number(getattr(args, "min_rps", 0)) or float(getattr(args, "min_rps", 0) or 0) <= 0:
        args.min_rps = default_min_rps(args.concurrency, args.p95_ms)
    if float(args.min_rps) <= 0:
        raise ValueError("--min-rps must be greater than 0")
    if (
        not is_number(getattr(args, "max_process_rss_growth_mb", 0))
        or float(getattr(args, "max_process_rss_growth_mb", 0) or 0) <= 0
    ):
        args.max_process_rss_growth_mb = DEFAULT_MAX_PROCESS_RSS_GROWTH_MB
    if args.max_error_rate < 0 or args.max_error_rate > 100:
        raise ValueError("--max-error-rate must be between 0 and 100")
    if args.api_server_count < 1:
        raise ValueError("--api-server-count must be at least 1")
    if int(getattr(args, "image_max_mb", 10) or 10) < 1:
        raise ValueError("--image-max-mb must be at least 1")
    for image_file in image_file_paths_for_args(args):
        if not Path(image_file).exists():
            raise ValueError("--image-file/--image-files/--image-file-list path does not exist")
    if args.scenario == "mixed-traffic" and args.active_users < 1:
        raise ValueError("--active-users must be at least 1 for mixed-traffic")
    parse_traffic_mix(args.traffic_mix)
    args.expected_product_url_prefix = expected_product_url_prefix_for_args(args)
    if args.mall_sample_size > 0:
        load_mall_identities(args)


def failed_validation_report(args: argparse.Namespace, error: Exception | str) -> dict[str, Any]:
    return {
        "ok": False,
        "base_url": redact_url_for_report(getattr(args, "base_url", "")),
        "mall_id": getattr(args, "mall_id", ""),
        "origin": redact_url_for_report(getattr(args, "origin", "")) or None,
        "api_server_count": getattr(args, "api_server_count", None),
        "scenario": getattr(args, "scenario", ""),
        "active_users": getattr(args, "active_users", 0),
        "mall_sample_size": getattr(args, "mall_sample_size", 0),
        "mode": getattr(args, "mode", ""),
        "expected_product_url_prefix": getattr(args, "expected_product_url_prefix", "") or None,
        "mode_counts": {},
        "traffic_mix_percent": {},
        "requests": getattr(args, "requests", 0),
        "concurrency": getattr(args, "concurrency", 0),
        "ok_count": 0,
        "error_count": 0,
        "error_rate": 100,
        "total_ms": 0,
        "requests_per_second": 0,
        "latency_ms": {"min": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0},
        "thresholds": {
            "p95_ms": getattr(args, "p95_ms", None),
            "p99_ms": getattr(args, "p99_ms", None),
            "request_timeout_seconds": getattr(args, "request_timeout_seconds", None),
            "max_server_wait_avg_ms": getattr(args, "max_server_wait_avg_ms", None),
            "min_requests_per_second": getattr(args, "min_rps", None),
            "max_error_rate": getattr(args, "max_error_rate", None),
        },
        "image_input": {"source": None},
        "response_contract": {"ok": False, "error": str(error)},
        "server_metrics": {"requested": False, "ok": False},
        "target_validation": {
            "ok": False,
            "error": str(error),
        },
    }


def open_target_request(request: urllib.request.Request, timeout: int | float):  # type: ignore[no-untyped-def]
    if ALLOW_LOCAL_TARGET:
        return urllib.request.urlopen(request, timeout=timeout)
    return open_public_http_request(request, timeout=timeout)


def open_admin_request(request: urllib.request.Request, timeout: int | float):  # type: ignore[no-untyped-def]
    if ALLOW_LOCAL_TARGET or ALLOW_PRIVATE_ADMIN_METRICS_TARGETS:
        return urllib.request.urlopen(request, timeout=timeout)
    return open_public_http_request(request, timeout=timeout)


def fetch_runtime_health(base_url: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/health"
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    started = time.perf_counter()
    try:
        with open_target_request(request, timeout=15) as response:
            body = response.read()
            data = json.loads(body.decode("utf-8")) if body else {}
            return {
                "ok": True,
                "status": response.status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "embedding_backend": data.get("embedding_backend") if isinstance(data, dict) else None,
                "engine": data.get("engine") if isinstance(data, dict) else None,
                "data": data,
            }
    except urllib.error.HTTPError as exc:
        exc.read()
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }


def json_body_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=JSON_DUMP_SEPARATORS).encode("utf-8")


def is_gzip_encoded(content_encoding: str | None) -> bool:
    return "gzip" in str(content_encoding or "").lower()


def decode_response_body(raw_body: bytes, content_encoding: str | None) -> bytes:
    if is_gzip_encoded(content_encoding):
        return gzip.decompress(raw_body)
    return raw_body


def response_header_value(headers: dict[str, str], name: str) -> str:
    expected = name.lower()
    for key, value in headers.items():
        if str(key).lower() == expected:
            return str(value or "").strip()
    return ""


def response_headers_from_pairs(pairs: list[tuple[str, str]]) -> dict[str, str]:
    return {str(key): str(value) for key, value in pairs}


def response_headers_from_response(response: Any) -> dict[str, str]:
    getheaders = getattr(response, "getheaders", None)
    if callable(getheaders):
        return response_headers_from_pairs(getheaders())
    headers = getattr(response, "headers", None)
    if hasattr(headers, "items"):
        return {str(key): str(value) for key, value in headers.items()}
    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        value = getheader(API_INSTANCE_HEADER)
        return {API_INSTANCE_HEADER: str(value)} if value else {}
    return {}


def normalize_api_instance_id(value: Any) -> str:
    text = str(value or "").strip()
    return text[:128]


class TargetJsonHttpClient:
    def __init__(self, base_url: str):
        parsed = urllib.parse.urlparse(base_url.rstrip("/"))
        self.scheme = parsed.scheme.lower()
        self.host = parsed.hostname or ""
        self.port = parsed.port
        self.base_path = parsed.path.rstrip("/")
        self._local = threading.local()
        self._stats_lock = threading.Lock()
        self._connections_opened = 0
        self._connection_reuses = 0
        self._requests_sent = 0
        self._request_attempts = 0
        self._stale_reconnects = 0
        self._connection_close_responses = 0
        self._gzip_responses = 0
        self._total_response_body_bytes = 0
        self._max_response_body_bytes = 0
        self._last_response_body_bytes = 0
        self._total_decoded_response_body_bytes = 0
        self._max_decoded_response_body_bytes = 0
        self._last_decoded_response_body_bytes = 0

    def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout: int | float,
    ) -> tuple[int, dict[str, Any], str, dict[str, str]]:
        body = json_body_bytes(payload)
        request_headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json; charset=utf-8",
            "Connection": "keep-alive",
            **headers,
        }
        last_error: BaseException | None = None
        for attempt in range(2):
            connection = self._connection(timeout)
            try:
                with self._stats_lock:
                    self._request_attempts += 1
                connection.request("POST", self._target_path(path), body=body, headers=request_headers)
                response = connection.getresponse()
                raw_body = response.read()
                response_headers = response_headers_from_response(response)
                response_getheader = getattr(response, "getheader", None)
                content_encoding = response_getheader("Content-Encoding") if response_getheader else None
                decoded_body = decode_response_body(raw_body, content_encoding)
                raw = decoded_body.decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    data = {}
                with self._stats_lock:
                    self._requests_sent += 1
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
                    if is_gzip_encoded(content_encoding):
                        self._gzip_responses += 1
                response_connection_header = response_getheader("Connection") if response_getheader else None
                if str(response_connection_header or "").lower() == "close":
                    with self._stats_lock:
                        self._connection_close_responses += 1
                    self.close()
                return response.status, data if isinstance(data, dict) else {}, raw, response_headers
            except (http.client.HTTPException, OSError, TimeoutError) as exc:
                last_error = exc
                with self._stats_lock:
                    if attempt == 0:
                        self._stale_reconnects += 1
                self.close()
                if attempt == 0:
                    continue
                raise
        raise last_error or RuntimeError("search request failed")

    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return {
                "requests_sent": self._requests_sent,
                "request_attempts": self._request_attempts,
                "connections_opened": self._connections_opened,
                "connection_reuses": self._connection_reuses,
                "stale_reconnects": self._stale_reconnects,
                "connection_close_responses": self._connection_close_responses,
                "gzip_responses": self._gzip_responses,
                "total_response_body_bytes": self._total_response_body_bytes,
                "max_response_body_bytes": self._max_response_body_bytes,
                "last_response_body_bytes": self._last_response_body_bytes,
                "total_decoded_response_body_bytes": self._total_decoded_response_body_bytes,
                "max_decoded_response_body_bytes": self._max_decoded_response_body_bytes,
                "last_decoded_response_body_bytes": self._last_decoded_response_body_bytes,
            }

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            try:
                connection.close()
            finally:
                self._local.connection = None

    def _target_path(self, path: str) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{self.base_path}{suffix}" if self.base_path else suffix

    def _connection(self, timeout: int | float) -> http.client.HTTPConnection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._new_connection(timeout)
            self._local.connection = connection
            with self._stats_lock:
                self._connections_opened += 1
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
                    with self._stats_lock:
                        self._connections_opened += 1
        return connection

    def _new_connection(self, timeout: int | float) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout)
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    expected_product_url_prefix: str = "",
    client: TargetJsonHttpClient | None = None,
    request_timeout_seconds: int | float = 120,
) -> dict[str, Any]:
    body = json_body_bytes(payload)
    expected_type = expected_query_type(payload)
    expected_mall_id = requested_mall_id(payload)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Accept-Encoding": "gzip", "Content-Type": "application/json; charset=utf-8", **headers},
    )
    started = time.perf_counter()
    try:
        response_headers: dict[str, str] = {}
        if client is not None:
            status, data, raw, response_headers = client.post_json(
                "/api/ai-search",
                payload,
                headers,
                timeout=request_timeout_seconds,
            )
        else:
            with open_target_request(request, timeout=request_timeout_seconds) as response:
                response_headers = response_headers_from_response(response)
                response_getheader = getattr(response, "getheader", None)
                content_encoding = response_getheader("Content-Encoding") if response_getheader else None
                raw = decode_response_body(response.read(), content_encoding).decode("utf-8", errors="replace")
                status = response.status
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    return {
                        "ok": False,
                        "status": status,
                        "elapsed_ms": (time.perf_counter() - started) * 1000,
                        "expected_query_type": expected_type,
                        "expected_mall_id": expected_mall_id,
                        "response_valid": False,
                        "error": "search response is not valid JSON",
                    }
        if client is not None and not isinstance(data, dict):
            data = {}
        if client is not None and not data and raw:
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return {
                    "ok": False,
                    "status": status,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "expected_query_type": expected_type,
                    "expected_mall_id": expected_mall_id,
                    "response_valid": False,
                    "error": "search response is not valid JSON",
                }
        response_valid, response_error = validate_search_response(data, payload, expected_product_url_prefix)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        top = data.get("top") if isinstance(data.get("top"), list) else None
        items = data.get("items") if isinstance(data.get("items"), list) else None
        categories = data.get("suggested_categories") if isinstance(data.get("suggested_categories"), list) else None
        result_items = [
            item
            for item in [*(top or []), *(items or [])]
            if isinstance(item, dict)
        ]
        result_mall_ids = [str(item.get("mall_id") or "").strip() for item in result_items]
        result_product_urls = [str(item.get("product_url") or "").strip() for item in result_items]
        product_url_prefix_mismatch_count = (
            sum(1 for product_url in result_product_urls if not url_matches_prefix(product_url, expected_product_url_prefix))
            if expected_product_url_prefix
            else 0
        )
        meta_mall_id = str(meta.get("mall_id") or "").strip()
        mall_id_matches_request = (
            bool(expected_mall_id)
            and meta_mall_id == expected_mall_id
            and bool(result_mall_ids)
            and all(mall_id == expected_mall_id for mall_id in result_mall_ids)
        )
        product_url_prefix_matches = not expected_product_url_prefix or product_url_prefix_mismatch_count == 0
        result = {
            "ok": 200 <= status < 300 and response_valid,
            "status": status,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "expected_query_type": expected_type,
            "expected_mall_id": expected_mall_id,
            "expected_product_url_prefix": expected_product_url_prefix or None,
            "meta_mall_id": meta_mall_id,
            "result_mall_ids": result_mall_ids,
            "mall_id_matches_request": mall_id_matches_request,
            "product_url_prefix_mismatch_count": product_url_prefix_mismatch_count,
            "product_url_prefix_matches": product_url_prefix_matches,
            "query_type": meta.get("query_type"),
            "engine": meta.get("engine"),
            "top_count": len(top) if top is not None else None,
            "item_count": len(items) if items is not None else None,
            "category_count": len(categories) if categories is not None else None,
            "api_instance_id": normalize_api_instance_id(
                response_header_value(response_headers, API_INSTANCE_HEADER)
            ),
            "response_valid": response_valid,
        }
        if response_error:
            result["error"] = response_error
        return result
    except urllib.error.HTTPError as exc:
        exc.read()
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "expected_query_type": expected_type,
            "expected_mall_id": expected_mall_id,
            "response_valid": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "expected_query_type": expected_type,
            "expected_mall_id": expected_mall_id,
            "response_valid": False,
            "error": str(exc),
        }


def fetch_admin_metrics(base_url: str, admin_key: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + f"/admin/metrics?limit={ADMIN_LOG_TAIL_LIMIT}"
    request = urllib.request.Request(url, method="GET", headers={"X-Admin-Key": admin_key})
    started = time.perf_counter()
    try:
        with open_admin_request(request, timeout=30) as response:
            body = response.read()
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                return {"ok": False, "status": response.status, "error": "metrics response must be an object"}
            return {"ok": True, "status": response.status, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "data": data}
    except urllib.error.HTTPError as exc:
        exc.read()
        return {"ok": False, "status": exc.code, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:
        return {"ok": False, "status": "error", "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(exc)}


def fetch_admin_search_log(base_url: str, admin_key: str, limit: int = ADMIN_LOG_TAIL_LIMIT) -> dict[str, Any]:
    url = base_url.rstrip("/") + f"/admin/search-log?limit={int(limit)}"
    request = urllib.request.Request(url, method="GET", headers={"X-Admin-Key": admin_key})
    started = time.perf_counter()
    try:
        with open_admin_request(request, timeout=30) as response:
            body = response.read()
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, list):
                return {"ok": False, "status": response.status, "error": "search log response must be a list"}
            return {
                "ok": True,
                "status": response.status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "data": data,
                "limit": int(limit),
            }
    except urllib.error.HTTPError as exc:
        exc.read()
        return {"ok": False, "status": exc.code, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:
        return {"ok": False, "status": "error", "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(exc)}


def extract_metrics_snapshot(metrics: dict[str, Any]) -> dict[str, Any]:
    engine = metrics.get("engine") or {}
    engine_transport = engine.get("transport") or {}
    marqo_transport = engine_transport.get("marqo") if isinstance(engine_transport, dict) else {}
    qwen_transport = engine_transport.get("qwen") if isinstance(engine_transport, dict) else {}
    if not qwen_transport and isinstance(engine_transport, dict):
        qwen_transport = engine_transport.get("gemini") or {}
    marqo_circuit = (marqo_transport or {}).get("circuit_breaker") if isinstance(marqo_transport, dict) else {}
    qwen_circuit = (qwen_transport or {}).get("circuit_breaker") if isinstance(qwen_transport, dict) else {}
    qwen_query_cache = engine.get("qwen_query_embedding_cache") or {}
    if not qwen_query_cache:
        qwen_query_cache = engine.get("gemini_query_embedding_cache") or {}
    engine_health_cache = engine.get("health_cache") or {}
    search = metrics.get("search") or {}
    errors = metrics.get("errors") or {}
    rate_limit = metrics.get("rate_limit") or {}
    rate_limit_limits = rate_limit.get("limits") or {}
    cache = metrics.get("cache") or {}
    singleflight = metrics.get("singleflight") or {}
    image_validation = metrics.get("image_validation") or {}
    search_queue = metrics.get("search_queue") or {}
    image_queue = metrics.get("image_queue") or {}
    api_threadpool = metrics.get("api_threadpool") or {}
    process = metrics.get("process") or {}
    system = metrics.get("system") or {}
    disk = metrics.get("disk") or {}
    logs = metrics.get("logs") or {}
    search_log = logs.get("search") or {}
    error_log = logs.get("error") or {}
    return {
        "generated_at": metrics.get("generated_at"),
        "engine_ok": engine.get("ok"),
        "engine_backend": engine.get("backend"),
        "engine_index": engine.get("index"),
        "marqo_model": engine.get("model") or engine.get("marqo_model"),
        "embedding_backend": engine.get("embedding_backend"),
        "qwen_model": engine.get("qwen_model") or engine.get("gemini_model"),
        "qwen_embedding_dimensions": engine.get("qwen_embedding_dimensions")
        or engine.get("gemini_embedding_dimensions"),
        "gemini_model": engine.get("gemini_model"),
        "gemini_embedding_dimensions": engine.get("gemini_embedding_dimensions"),
        "qwen_query_vector_runtime_entries": qwen_query_cache.get("runtime_entries") if isinstance(qwen_query_cache, dict) else None,
        "qwen_query_vector_runtime_text_entries": (
            qwen_query_cache.get("runtime_text_entries") if isinstance(qwen_query_cache, dict) else None
        ),
        "qwen_query_vector_runtime_image_entries": (
            qwen_query_cache.get("runtime_image_entries") if isinstance(qwen_query_cache, dict) else None
        ),
        "qwen_query_vector_runtime_max_entries": (
            qwen_query_cache.get("runtime_max_entries") if isinstance(qwen_query_cache, dict) else None
        ),
        "qwen_query_vector_runtime_text_max_entries": (
            qwen_query_cache.get("runtime_text_max_entries") if isinstance(qwen_query_cache, dict) else None
        ),
        "qwen_query_vector_runtime_image_max_entries": (
            qwen_query_cache.get("runtime_image_max_entries") if isinstance(qwen_query_cache, dict) else None
        ),
        "qwen_query_vector_mixed_parallelism": (
            qwen_query_cache.get("mixed_parallelism") if isinstance(qwen_query_cache, dict) else None
        ),
        "qwen_query_vector_in_flight": qwen_query_cache.get("in_flight") if isinstance(qwen_query_cache, dict) else None,
        "qwen_query_vector_wait_timeout_seconds": (
            qwen_query_cache.get("wait_timeout_seconds") if isinstance(qwen_query_cache, dict) else None
        ),
        "qwen_query_vector_wait_events": qwen_query_cache.get("wait_events") if isinstance(qwen_query_cache, dict) else None,
        "qwen_query_vector_wait_timeouts": qwen_query_cache.get("wait_timeouts") if isinstance(qwen_query_cache, dict) else None,
        "qwen_query_vector_total_wait_ms": qwen_query_cache.get("total_wait_ms") if isinstance(qwen_query_cache, dict) else None,
        "qwen_query_vector_avg_wait_ms": qwen_query_cache.get("avg_wait_ms") if isinstance(qwen_query_cache, dict) else None,
        "qwen_query_vector_max_wait_ms": qwen_query_cache.get("max_wait_ms") if isinstance(qwen_query_cache, dict) else None,
        "engine_health_cache_enabled": (
            engine_health_cache.get("enabled") if isinstance(engine_health_cache, dict) else None
        ),
        "engine_health_cache_hit": (
            engine_health_cache.get("hit") if isinstance(engine_health_cache, dict) else None
        ),
        "engine_health_cache_ttl_seconds": (
            engine_health_cache.get("ttl_seconds") if isinstance(engine_health_cache, dict) else None
        ),
        "engine_health_cache_age_ms": (
            engine_health_cache.get("age_ms") if isinstance(engine_health_cache, dict) else None
        ),
        "backend_marqo_request_attempts": (marqo_transport or {}).get("request_attempts") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_max_active_requests": (
            (marqo_transport or {}).get("max_active_requests") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_active_requests": (
            (marqo_transport or {}).get("active_requests") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_max_active_requests_observed": (
            (marqo_transport or {}).get("max_active_requests_observed") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_connection_acquire_wait_events": (
            (marqo_transport or {}).get("connection_acquire_wait_events") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_connection_acquire_wait_timeouts": (
            (marqo_transport or {}).get("connection_acquire_wait_timeouts") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_total_connection_acquire_wait_ms": (
            (marqo_transport or {}).get("total_connection_acquire_wait_ms") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_avg_connection_acquire_wait_ms": (
            (marqo_transport or {}).get("avg_connection_acquire_wait_ms") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_max_connection_acquire_wait_ms": (
            (marqo_transport or {}).get("max_connection_acquire_wait_ms") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_last_connection_acquire_wait_ms": (
            (marqo_transport or {}).get("last_connection_acquire_wait_ms") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_connections_opened": (marqo_transport or {}).get("connections_opened") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_connection_reuses": (marqo_transport or {}).get("connection_reuses") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_idle_reconnects": (marqo_transport or {}).get("idle_reconnects") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_stale_reconnects": (marqo_transport or {}).get("stale_reconnects") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_error_responses": (marqo_transport or {}).get("error_responses") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_connection_close_responses": (marqo_transport or {}).get("connection_close_responses") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_gzip_responses": (marqo_transport or {}).get("gzip_responses") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_retry_after_responses": (
            (marqo_transport or {}).get("retry_after_responses") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_max_retry_after_seconds": (
            (marqo_transport or {}).get("max_retry_after_seconds") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_last_retry_after_seconds": (
            (marqo_transport or {}).get("last_retry_after_seconds") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_total_elapsed_ms": (marqo_transport or {}).get("total_elapsed_ms") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_avg_elapsed_ms": (marqo_transport or {}).get("avg_elapsed_ms") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_max_elapsed_ms": (marqo_transport or {}).get("max_elapsed_ms") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_last_elapsed_ms": (marqo_transport or {}).get("last_elapsed_ms") if isinstance(marqo_transport, dict) else None,
        "backend_marqo_total_request_body_bytes": (
            (marqo_transport or {}).get("total_request_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_max_request_body_bytes": (
            (marqo_transport or {}).get("max_request_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_last_request_body_bytes": (
            (marqo_transport or {}).get("last_request_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_total_response_body_bytes": (
            (marqo_transport or {}).get("total_response_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_max_response_body_bytes": (
            (marqo_transport or {}).get("max_response_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_last_response_body_bytes": (
            (marqo_transport or {}).get("last_response_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_total_decoded_response_body_bytes": (
            (marqo_transport or {}).get("total_decoded_response_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_max_decoded_response_body_bytes": (
            (marqo_transport or {}).get("max_decoded_response_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_last_decoded_response_body_bytes": (
            (marqo_transport or {}).get("last_decoded_response_body_bytes") if isinstance(marqo_transport, dict) else None
        ),
        "backend_marqo_circuit_open_events": (
            (marqo_circuit or {}).get("open_events") if isinstance(marqo_circuit, dict) else None
        ),
        "backend_marqo_circuit_short_circuits": (
            (marqo_circuit or {}).get("short_circuits") if isinstance(marqo_circuit, dict) else None
        ),
        "backend_marqo_circuit_recovery_events": (
            (marqo_circuit or {}).get("recovery_events") if isinstance(marqo_circuit, dict) else None
        ),
        "backend_qwen_request_attempts": (qwen_transport or {}).get("request_attempts") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_max_active_requests": (
            (qwen_transport or {}).get("max_active_requests") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_active_requests": (
            (qwen_transport or {}).get("active_requests") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_max_active_requests_observed": (
            (qwen_transport or {}).get("max_active_requests_observed") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_connection_acquire_wait_events": (
            (qwen_transport or {}).get("connection_acquire_wait_events") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_connection_acquire_wait_timeouts": (
            (qwen_transport or {}).get("connection_acquire_wait_timeouts") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_total_connection_acquire_wait_ms": (
            (qwen_transport or {}).get("total_connection_acquire_wait_ms") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_avg_connection_acquire_wait_ms": (
            (qwen_transport or {}).get("avg_connection_acquire_wait_ms") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_max_connection_acquire_wait_ms": (
            (qwen_transport or {}).get("max_connection_acquire_wait_ms") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_last_connection_acquire_wait_ms": (
            (qwen_transport or {}).get("last_connection_acquire_wait_ms") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_connections_opened": (qwen_transport or {}).get("connections_opened") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_connection_reuses": (qwen_transport or {}).get("connection_reuses") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_idle_reconnects": (qwen_transport or {}).get("idle_reconnects") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_stale_reconnects": (qwen_transport or {}).get("stale_reconnects") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_error_responses": (qwen_transport or {}).get("error_responses") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_connection_close_responses": (qwen_transport or {}).get("connection_close_responses") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_gzip_responses": (qwen_transport or {}).get("gzip_responses") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_retry_after_responses": (
            (qwen_transport or {}).get("retry_after_responses") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_max_retry_after_seconds": (
            (qwen_transport or {}).get("max_retry_after_seconds") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_last_retry_after_seconds": (
            (qwen_transport or {}).get("last_retry_after_seconds") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_total_elapsed_ms": (qwen_transport or {}).get("total_elapsed_ms") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_avg_elapsed_ms": (qwen_transport or {}).get("avg_elapsed_ms") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_max_elapsed_ms": (qwen_transport or {}).get("max_elapsed_ms") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_last_elapsed_ms": (qwen_transport or {}).get("last_elapsed_ms") if isinstance(qwen_transport, dict) else None,
        "backend_qwen_total_request_body_bytes": (
            (qwen_transport or {}).get("total_request_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_max_request_body_bytes": (
            (qwen_transport or {}).get("max_request_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_last_request_body_bytes": (
            (qwen_transport or {}).get("last_request_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_total_response_body_bytes": (
            (qwen_transport or {}).get("total_response_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_max_response_body_bytes": (
            (qwen_transport or {}).get("max_response_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_last_response_body_bytes": (
            (qwen_transport or {}).get("last_response_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_total_decoded_response_body_bytes": (
            (qwen_transport or {}).get("total_decoded_response_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_max_decoded_response_body_bytes": (
            (qwen_transport or {}).get("max_decoded_response_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_last_decoded_response_body_bytes": (
            (qwen_transport or {}).get("last_decoded_response_body_bytes") if isinstance(qwen_transport, dict) else None
        ),
        "backend_qwen_circuit_open_events": (
            (qwen_circuit or {}).get("open_events") if isinstance(qwen_circuit, dict) else None
        ),
        "backend_qwen_circuit_short_circuits": (
            (qwen_circuit or {}).get("short_circuits") if isinstance(qwen_circuit, dict) else None
        ),
        "backend_qwen_circuit_recovery_events": (
            (qwen_circuit or {}).get("recovery_events") if isinstance(qwen_circuit, dict) else None
        ),
        "search_events": search.get("search_events"),
        "image_search_events": search.get("image_search_events"),
        "result_mall_id_mismatch_events": search.get("result_mall_id_mismatch_events"),
        "result_mall_id_mismatch_count": search.get("result_mall_id_mismatch_count"),
        "engine_search_attempts": search.get("engine_search_attempts"),
        "engine_adaptive_refetches": search.get("engine_adaptive_refetches"),
        "engine_adaptive_refetch_searches": search.get("engine_adaptive_refetch_searches"),
        "engine_underfilled_after_max_candidates_events": search.get("engine_underfilled_after_max_candidates_events"),
        "engine_average_search_attempts": search.get("engine_average_search_attempts"),
        "engine_max_search_attempts": search.get("engine_max_search_attempts"),
        "engine_average_final_candidate_limit": search.get("engine_average_final_candidate_limit"),
        "engine_max_final_candidate_limit": search.get("engine_max_final_candidate_limit"),
        "engine_average_final_raw_candidate_count": search.get("engine_average_final_raw_candidate_count"),
        "engine_average_final_collapsed_candidate_count": search.get("engine_average_final_collapsed_candidate_count"),
        "average_elapsed_ms": search.get("average_elapsed_ms"),
        "p95_elapsed_ms": search.get("p95_elapsed_ms"),
        "p99_elapsed_ms": search.get("p99_elapsed_ms"),
        "rate_limited_events": errors.get("rate_limited_events"),
        "rate_limit_backend": rate_limit.get("backend"),
        "rate_limit_redis_enabled": rate_limit.get("redis_enabled"),
        "rate_limit_search_per_minute": rate_limit_limits.get("search_per_minute"),
        "rate_limit_mall_search_per_minute": rate_limit_limits.get("mall_search_per_minute"),
        "rate_limit_image_per_minute": rate_limit_limits.get("image_per_minute"),
        "rate_limit_mall_image_per_minute": rate_limit_limits.get("mall_image_per_minute"),
        "rate_limit_fallback_events": rate_limit.get("fallback_events"),
        "rate_limit_fallback_active": rate_limit.get("fallback_active"),
        "rate_limit_fallback_skipped_redis_events": rate_limit.get("fallback_skipped_redis_events"),
        "rate_limit_fallback_bucket_count": rate_limit.get("fallback_bucket_count"),
        "rate_limit_fallback_max_buckets": rate_limit.get("fallback_max_buckets"),
        "rate_limit_fallback_prune_interval_seconds": rate_limit.get("fallback_prune_interval_seconds"),
        "rate_limit_fallback_pruned_buckets": rate_limit.get("fallback_pruned_buckets"),
        "rate_limit_redis_socket_timeout_seconds": rate_limit.get("redis_socket_timeout_seconds"),
        "rate_limit_redis_socket_connect_timeout_seconds": rate_limit.get("redis_socket_connect_timeout_seconds"),
        "rate_limit_redis_backoff_active": rate_limit.get("redis_backoff_active"),
        "rate_limit_redis_backoff_seconds": rate_limit.get("redis_backoff_seconds"),
        "rate_limit_redis_backoff_remaining_ms": rate_limit.get("redis_backoff_remaining_ms"),
        "rate_limit_redis_backoff_failure_events": rate_limit.get("redis_backoff_failure_events"),
        "rate_limit_redis_backoff_skipped_operations": rate_limit.get("redis_backoff_skipped_operations"),
        "cache_backend": cache.get("backend"),
        "cache_redis_enabled": cache.get("redis_enabled"),
        "cache_ttl_seconds": cache.get("ttl_seconds"),
        "cache_max_entries": cache.get("max_entries"),
        "cache_evictions": cache.get("evictions"),
        "cache_error_count": cache.get("error_count"),
        "cache_get_errors": cache.get("get_errors"),
        "cache_set_errors": cache.get("set_errors"),
        "cache_decode_errors": cache.get("decode_errors"),
        "cache_delete_errors": cache.get("delete_errors"),
        "cache_clear_errors": cache.get("clear_errors"),
        "cache_redis_socket_timeout_seconds": cache.get("redis_socket_timeout_seconds"),
        "cache_redis_socket_connect_timeout_seconds": cache.get("redis_socket_connect_timeout_seconds"),
        "cache_redis_backoff_active": cache.get("redis_backoff_active"),
        "cache_redis_backoff_seconds": cache.get("redis_backoff_seconds"),
        "cache_redis_backoff_remaining_ms": cache.get("redis_backoff_remaining_ms"),
        "cache_redis_backoff_failure_events": cache.get("redis_backoff_failure_events"),
        "cache_redis_backoff_skipped_operations": cache.get("redis_backoff_skipped_operations"),
        "cache_lock_claims": cache.get("lock_claims"),
        "cache_lock_contention_events": cache.get("lock_contention_events"),
        "cache_lock_errors": cache.get("lock_errors"),
        "cache_lock_release_errors": cache.get("lock_release_errors"),
        "cache_lock_wait_events": cache.get("lock_wait_events"),
        "cache_lock_wait_timeouts": cache.get("lock_wait_timeouts"),
        "cache_lock_total_wait_ms": cache.get("lock_total_wait_ms"),
        "cache_lock_avg_wait_ms": cache.get("lock_avg_wait_ms"),
        "cache_lock_max_wait_ms": cache.get("lock_max_wait_ms"),
        "singleflight_enabled": singleflight.get("enabled"),
        "singleflight_in_flight": singleflight.get("in_flight"),
        "singleflight_wait_timeout_seconds": singleflight.get("wait_timeout_seconds"),
        "singleflight_wait_events": singleflight.get("wait_events"),
        "singleflight_wait_timeouts": singleflight.get("wait_timeouts"),
        "singleflight_total_wait_ms": singleflight.get("total_wait_ms"),
        "singleflight_avg_wait_ms": singleflight.get("avg_wait_ms"),
        "singleflight_max_wait_ms": singleflight.get("max_wait_ms"),
        "image_validation_enabled": image_validation.get("enabled"),
        "image_validation_in_flight": image_validation.get("in_flight"),
        "image_validation_wait_timeout_seconds": image_validation.get("wait_timeout_seconds"),
        "image_validation_wait_events": image_validation.get("wait_events"),
        "image_validation_wait_timeouts": image_validation.get("wait_timeouts"),
        "image_validation_total_wait_ms": image_validation.get("total_wait_ms"),
        "image_validation_avg_wait_ms": image_validation.get("avg_wait_ms"),
        "image_validation_max_wait_ms": image_validation.get("max_wait_ms"),
        "image_validation_cache_enabled": image_validation.get("cache_enabled"),
        "image_validation_cache_ttl_seconds": image_validation.get("cache_ttl_seconds"),
        "image_validation_cache_max_entries": image_validation.get("cache_max_entries"),
        "image_validation_cache_entry_count": image_validation.get("cache_entry_count"),
        "image_validation_cache_hits": image_validation.get("cache_hits"),
        "image_validation_cache_misses": image_validation.get("cache_misses"),
        "image_validation_cache_evictions": image_validation.get("cache_evictions"),
        "search_queue_enabled": search_queue.get("enabled"),
        "search_queue_max_concurrency": search_queue.get("max_concurrency"),
        "search_queue_timeout_seconds": search_queue.get("queue_timeout_seconds"),
        "search_queue_in_flight": search_queue.get("in_flight"),
        "search_queue_available_slots": search_queue.get("available_slots"),
        "search_queue_acquired_events": search_queue.get("acquired_events"),
        "search_queue_full_events": search_queue.get("queue_full_events"),
        "search_queue_wait_events": search_queue.get("wait_events"),
        "search_queue_total_wait_ms": search_queue.get("total_wait_ms"),
        "search_queue_avg_wait_ms": search_queue.get("avg_wait_ms"),
        "search_queue_max_wait_ms": search_queue.get("max_wait_ms"),
        "search_queue_last_wait_ms": search_queue.get("last_wait_ms"),
        "image_queue_enabled": image_queue.get("enabled"),
        "image_queue_max_concurrency": image_queue.get("max_concurrency"),
        "image_queue_timeout_seconds": image_queue.get("queue_timeout_seconds"),
        "image_queue_in_flight": image_queue.get("in_flight"),
        "image_queue_available_slots": image_queue.get("available_slots"),
        "image_queue_acquired_events": image_queue.get("acquired_events"),
        "image_queue_full_events": image_queue.get("queue_full_events"),
        "image_queue_wait_events": image_queue.get("wait_events"),
        "image_queue_total_wait_ms": image_queue.get("total_wait_ms"),
        "image_queue_avg_wait_ms": image_queue.get("avg_wait_ms"),
        "image_queue_max_wait_ms": image_queue.get("max_wait_ms"),
        "image_queue_last_wait_ms": image_queue.get("last_wait_ms"),
        "api_threadpool_ok": api_threadpool.get("ok"),
        "api_threadpool_configured_tokens": api_threadpool.get("configured_tokens"),
        "api_threadpool_runtime_tokens": api_threadpool.get("runtime_tokens"),
        "api_threadpool_required_tokens": api_threadpool.get("required_tokens"),
        "search_log_write_errors": search_log.get("write_errors"),
        "search_log_write_events": search_log.get("write_events"),
        "search_log_write_total_ms": search_log.get("write_total_ms"),
        "search_log_write_avg_ms": search_log.get("write_avg_ms"),
        "search_log_write_max_ms": search_log.get("write_max_ms"),
        "search_log_write_last_ms": search_log.get("last_write_ms"),
        "search_log_keep_open_seconds": search_log.get("keep_open_seconds"),
        "search_log_output_opens": search_log.get("output_opens"),
        "search_log_output_reuses": search_log.get("output_reuses"),
        "search_log_output_closes": search_log.get("output_closes"),
        "search_log_idle_closes": search_log.get("idle_closes"),
        "search_log_buffer_open": search_log.get("buffer_open"),
        "error_log_write_errors": error_log.get("write_errors"),
        "error_log_write_events": error_log.get("write_events"),
        "error_log_write_total_ms": error_log.get("write_total_ms"),
        "error_log_write_avg_ms": error_log.get("write_avg_ms"),
        "error_log_write_max_ms": error_log.get("write_max_ms"),
        "error_log_write_last_ms": error_log.get("last_write_ms"),
        "error_log_keep_open_seconds": error_log.get("keep_open_seconds"),
        "error_log_output_opens": error_log.get("output_opens"),
        "error_log_output_reuses": error_log.get("output_reuses"),
        "error_log_output_closes": error_log.get("output_closes"),
        "error_log_idle_closes": error_log.get("idle_closes"),
        "error_log_buffer_open": error_log.get("buffer_open"),
        "process_instance_id": process.get("instance_id"),
        "process_cpu_percent": process.get("cpu_percent"),
        "process_memory_rss_bytes": process.get("memory_rss_bytes"),
        "process_thread_count": process.get("thread_count"),
        "system_cpu_percent": system.get("system_cpu_percent"),
        "system_memory_used_percent": system.get("memory_used_percent"),
        "disk_used_percent": disk.get("used_percent"),
        "disk_free_bytes": disk.get("free_bytes"),
    }


def add_embedding_metric_aliases(metrics: dict[str, Any], *, embedding_backend: str | None = None) -> dict[str, Any]:
    backend = str(embedding_backend or metrics.get("embedding_backend") or "").strip().lower()
    if backend != "gemini":
        return metrics
    for key, value in list(metrics.items()):
        if key.startswith("backend_qwen_"):
            metrics.setdefault("backend_gemini_" + key[len("backend_qwen_") :], value)
        elif key.startswith("qwen_query_vector_"):
            metrics.setdefault("gemini_query_vector_" + key[len("qwen_query_vector_") :], value)
    if metrics.get("qwen_model") is not None:
        metrics.setdefault("gemini_model", metrics.get("qwen_model"))
    if metrics.get("qwen_embedding_dimensions") is not None:
        metrics.setdefault("gemini_embedding_dimensions", metrics.get("qwen_embedding_dimensions"))
    return metrics


def gemini_operator_metric_key(key: str) -> str:
    if key.startswith("backend_qwen_"):
        return "backend_gemini_" + key[len("backend_qwen_") :]
    if key.startswith("qwen_query_vector_"):
        return "gemini_query_vector_" + key[len("qwen_query_vector_") :]
    if key == "qwen_model":
        return "gemini_model"
    if key == "qwen_embedding_dimensions":
        return "gemini_embedding_dimensions"
    if key == "min_backend_qwen_request_attempts":
        return "min_backend_gemini_request_attempts"
    return key


def gemini_operator_metric_string(value: str) -> str:
    return (
        value.replace("backend_qwen_", "backend_gemini_")
        .replace("qwen_query_vector", "gemini_query_vector")
        .replace("Qwen", "Gemini")
        .replace("qwen", "gemini")
    )


def hide_legacy_qwen_aliases_for_gemini(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            new_key = gemini_operator_metric_key(text_key)
            if new_key != text_key and new_key in value:
                continue
            if new_key == text_key and "qwen" in text_key.lower():
                continue
            cleaned[new_key] = hide_legacy_qwen_aliases_for_gemini(item)
        return cleaned
    if isinstance(value, list):
        return [hide_legacy_qwen_aliases_for_gemini(item) for item in value]
    if isinstance(value, str):
        return gemini_operator_metric_string(value)
    return value


def operator_visible_load_report(report: dict[str, Any]) -> dict[str, Any]:
    server_metrics = report.get("server_metrics") if isinstance(report, dict) else {}
    after_snapshot = ((server_metrics or {}).get("after") or {}).get("snapshot") if isinstance(server_metrics, dict) else {}
    runtime_health = report.get("runtime_health") if isinstance(report, dict) else {}
    embedding_backend = str(
        (after_snapshot or {}).get("embedding_backend")
        or (runtime_health or {}).get("embedding_backend")
        or ((runtime_health or {}).get("data") or {}).get("embedding_backend")
        or ""
    ).strip().lower()
    if embedding_backend != "gemini":
        return report
    cleaned = hide_legacy_qwen_aliases_for_gemini(report)
    if isinstance(cleaned, dict):
        cleaned["operator_metric_namespace"] = "gemini"
        cleaned["legacy_embedding_metric_aliases_hidden"] = True
    return cleaned


def aggregate_server_metric_snapshots(snapshots: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    if not snapshots:
        return {}, ["admin_metrics.no_successful_sources"]
    if len(snapshots) == 1:
        snapshot = dict(snapshots[0])
        instance_id = str(snapshot.get("process_instance_id") or "").strip()
        snapshot["admin_metrics_source_count"] = 1
        snapshot["admin_metrics_instance_ids"] = [instance_id] if instance_id else []
        return add_embedding_metric_aliases(snapshot), []

    problems: list[str] = []
    aggregate: dict[str, Any] = {}
    all_keys = sorted({key for snapshot in snapshots for key in snapshot})
    for key in all_keys:
        values = [snapshot.get(key) for snapshot in snapshots if snapshot.get(key) not in (None, "")]
        if not values:
            continue
        numeric_values = [value for value in values if is_number(value)]
        if key in AGGREGATED_SERVER_METRIC_SUM_FIELDS and len(numeric_values) == len(values):
            aggregate[key] = sum(float(value) for value in numeric_values)
            if all(float(value).is_integer() for value in numeric_values):
                aggregate[key] = int(aggregate[key])
            continue
        if key in AGGREGATED_SERVER_METRIC_MAX_FIELDS and numeric_values:
            aggregate[key] = max(float(value) for value in numeric_values)
            if float(aggregate[key]).is_integer():
                aggregate[key] = int(aggregate[key])
            continue
        if key in AGGREGATED_SERVER_METRIC_MIN_FIELDS and numeric_values:
            aggregate[key] = min(float(value) for value in numeric_values)
            if float(aggregate[key]).is_integer():
                aggregate[key] = int(aggregate[key])
            continue
        if key in AGGREGATED_SERVER_METRIC_RECOMPUTED_AVG_FIELDS:
            continue
        first_value = values[0]
        if key not in AGGREGATED_SERVER_METRIC_IGNORE_INCONSISTENCY_FIELDS:
            if any(value != first_value for value in values[1:]):
                problems.append(f"admin_metrics.inconsistent.{key}")
        aggregate[key] = first_value

    recompute_aggregate_average(aggregate, "backend_marqo_avg_elapsed_ms", "backend_marqo_total_elapsed_ms", "backend_marqo_request_attempts")
    recompute_aggregate_average(aggregate, "backend_qwen_avg_elapsed_ms", "backend_qwen_total_elapsed_ms", "backend_qwen_request_attempts")
    recompute_aggregate_average(aggregate, "engine_average_search_attempts", "engine_search_attempts", "search_events")
    recompute_aggregate_average(aggregate, "cache_lock_avg_wait_ms", "cache_lock_total_wait_ms", "cache_lock_wait_events")
    recompute_aggregate_average(aggregate, "singleflight_avg_wait_ms", "singleflight_total_wait_ms", "singleflight_wait_events")
    recompute_aggregate_average(aggregate, "image_validation_avg_wait_ms", "image_validation_total_wait_ms", "image_validation_wait_events")
    recompute_aggregate_average(aggregate, "qwen_query_vector_avg_wait_ms", "qwen_query_vector_total_wait_ms", "qwen_query_vector_wait_events")
    recompute_aggregate_average(aggregate, "search_queue_avg_wait_ms", "search_queue_total_wait_ms", "search_queue_wait_events")
    recompute_aggregate_average(aggregate, "image_queue_avg_wait_ms", "image_queue_total_wait_ms", "image_queue_wait_events")
    recompute_aggregate_average(aggregate, "search_log_write_avg_ms", "search_log_write_total_ms", "search_log_write_events")
    recompute_aggregate_average(aggregate, "error_log_write_avg_ms", "error_log_write_total_ms", "error_log_write_events")

    instance_ids = [
        str(snapshot.get("process_instance_id") or "").strip()
        for snapshot in snapshots
        if str(snapshot.get("process_instance_id") or "").strip()
    ]
    aggregate["admin_metrics_source_count"] = len(snapshots)
    aggregate["admin_metrics_instance_ids"] = sorted(set(instance_ids))
    if len(instance_ids) != len(set(instance_ids)):
        problems.append("admin_metrics.duplicate_instance_id")
    return add_embedding_metric_aliases(aggregate), sorted(set(problems))


def recompute_aggregate_average(snapshot: dict[str, Any], target: str, total_key: str, count_key: str) -> None:
    total = snapshot.get(total_key)
    count = snapshot.get(count_key)
    if is_number(total) and is_number(count) and int(count) > 0:
        snapshot[target] = round(float(total) / int(count), 3)


def normalize_metrics_base_url_input(base_urls: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(base_urls, str):
        return [base_urls.rstrip("/")]
    return [str(base_url or "").rstrip("/") for base_url in base_urls if str(base_url or "").strip()]


def collect_server_metrics(base_urls: str | list[str] | tuple[str, ...], admin_key: str, phase: str) -> dict[str, Any]:
    if not admin_key:
        return {"ok": True, "requested": False, "phase": phase}
    normalized_base_urls = normalize_metrics_base_url_input(base_urls)
    if not normalized_base_urls:
        normalized_base_urls = [""]
    sources = []
    snapshots = []
    for base_url in normalized_base_urls:
        raw = fetch_admin_metrics(base_url, admin_key)
        source = {
            "base_url": base_url.rstrip("/"),
            "ok": raw.get("ok") is True,
            "status": raw.get("status"),
            "elapsed_ms": raw.get("elapsed_ms"),
        }
        if raw.get("error"):
            source["error"] = raw.get("error")
        if raw.get("ok") is True:
            snapshot = extract_metrics_snapshot(raw["data"])
            source["process_instance_id"] = snapshot.get("process_instance_id")
            snapshots.append(snapshot)
        sources.append(source)
    if not snapshots:
        return {
            "ok": False,
            "requested": True,
            "phase": phase,
            "source_count": len(normalized_base_urls),
            "successful_source_count": 0,
            "failed_source_count": len(sources),
            "sources": sources,
            "status": sources[0].get("status") if sources else "error",
            "error": "admin metrics request failed",
        }
    aggregate, aggregation_problems = aggregate_server_metric_snapshots(snapshots)
    return {
        "ok": all(source.get("ok") is True for source in sources) and not aggregation_problems,
        "requested": True,
        "phase": phase,
        "source_count": len(normalized_base_urls),
        "successful_source_count": len(snapshots),
        "failed_source_count": len(sources) - len(snapshots),
        "sources": sources,
        "aggregation_problems": aggregation_problems,
        "status": sources[0].get("status") if len(sources) == 1 else "multi",
        "elapsed_ms": round(sum(float(source.get("elapsed_ms") or 0.0) for source in sources), 1),
        "snapshot": aggregate,
    }


def server_metrics_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_snapshot = before.get("snapshot") or {}
    after_snapshot = after.get("snapshot") or {}
    delta = {}
    for key in [
        "search_events",
        "image_search_events",
        "engine_search_attempts",
        "engine_adaptive_refetches",
        "engine_adaptive_refetch_searches",
        "engine_underfilled_after_max_candidates_events",
        "result_mall_id_mismatch_events",
        "result_mall_id_mismatch_count",
        "backend_marqo_request_attempts",
        "backend_marqo_connections_opened",
        "backend_marqo_connection_reuses",
        "backend_marqo_idle_reconnects",
        "backend_marqo_stale_reconnects",
        "backend_marqo_error_responses",
        "backend_marqo_connection_close_responses",
        "backend_marqo_gzip_responses",
        "backend_marqo_retry_after_responses",
        "backend_marqo_total_request_body_bytes",
        "backend_marqo_total_response_body_bytes",
        "backend_marqo_total_decoded_response_body_bytes",
        "backend_marqo_circuit_open_events",
        "backend_marqo_circuit_short_circuits",
        "backend_marqo_circuit_recovery_events",
        "backend_qwen_request_attempts",
        "backend_qwen_connections_opened",
        "backend_qwen_connection_reuses",
        "backend_qwen_idle_reconnects",
        "backend_qwen_stale_reconnects",
        "backend_qwen_error_responses",
        "backend_qwen_connection_close_responses",
        "backend_qwen_gzip_responses",
        "backend_qwen_retry_after_responses",
        "backend_qwen_total_request_body_bytes",
        "backend_qwen_total_response_body_bytes",
        "backend_qwen_total_decoded_response_body_bytes",
        "backend_qwen_circuit_open_events",
        "backend_qwen_circuit_short_circuits",
        "backend_qwen_circuit_recovery_events",
        "rate_limited_events",
        "rate_limit_fallback_events",
        "rate_limit_fallback_pruned_buckets",
        "cache_evictions",
        "cache_error_count",
        "cache_clear_errors",
        "cache_lock_claims",
        "cache_lock_contention_events",
        "cache_lock_errors",
        "cache_lock_release_errors",
        "cache_lock_wait_events",
        "cache_lock_wait_timeouts",
        "singleflight_wait_events",
        "singleflight_wait_timeouts",
        "image_validation_wait_events",
        "image_validation_wait_timeouts",
        "qwen_query_vector_wait_events",
        "qwen_query_vector_wait_timeouts",
        "search_queue_acquired_events",
        "search_queue_full_events",
        "search_queue_wait_events",
        "image_queue_acquired_events",
        "image_queue_full_events",
        "image_queue_wait_events",
        "search_log_write_errors",
        "search_log_write_events",
        "search_log_output_opens",
        "search_log_output_reuses",
        "search_log_output_closes",
        "search_log_idle_closes",
        "error_log_write_errors",
        "error_log_write_events",
        "error_log_output_opens",
        "error_log_output_reuses",
        "error_log_output_closes",
        "error_log_idle_closes",
    ]:
        before_value = before_snapshot.get(key)
        after_value = after_snapshot.get(key)
        if is_number(before_value) and is_number(after_value):
            delta[key] = int(after_value) - int(before_value)
    for key in [
        "cache_lock_total_wait_ms",
        "singleflight_total_wait_ms",
        "image_validation_total_wait_ms",
        "qwen_query_vector_total_wait_ms",
        "backend_marqo_total_elapsed_ms",
        "backend_qwen_total_elapsed_ms",
        "search_queue_total_wait_ms",
        "image_queue_total_wait_ms",
        "search_log_write_total_ms",
        "error_log_write_total_ms",
    ]:
        before_value = before_snapshot.get(key)
        after_value = after_snapshot.get(key)
        if is_number(before_value) and is_number(after_value):
            delta[key] = round(float(after_value) - float(before_value), 3)
    for service in ["marqo", "qwen"]:
        attempts = delta.get(f"backend_{service}_request_attempts")
        elapsed_ms = delta.get(f"backend_{service}_total_elapsed_ms")
        if is_number(attempts) and int(attempts) > 0 and is_number(elapsed_ms):
            delta[f"backend_{service}_run_avg_elapsed_ms"] = round(float(elapsed_ms) / int(attempts), 3)
        body_bytes = delta.get(f"backend_{service}_total_request_body_bytes")
        if is_number(attempts) and int(attempts) > 0 and is_number(body_bytes):
            delta[f"backend_{service}_run_avg_request_body_bytes"] = round(float(body_bytes) / int(attempts), 3)
        response_body_bytes = delta.get(f"backend_{service}_total_response_body_bytes")
        if is_number(attempts) and int(attempts) > 0 and is_number(response_body_bytes):
            delta[f"backend_{service}_run_avg_response_body_bytes"] = round(float(response_body_bytes) / int(attempts), 3)
        decoded_body_bytes = delta.get(f"backend_{service}_total_decoded_response_body_bytes")
        if is_number(attempts) and int(attempts) > 0 and is_number(decoded_body_bytes):
            delta[f"backend_{service}_run_avg_decoded_response_body_bytes"] = round(
                float(decoded_body_bytes) / int(attempts),
                3,
            )
    for prefix, events_key, total_wait_key in SERVER_WAIT_AVG_DELTA_FIELDS:
        events = delta.get(events_key)
        total_wait_ms = delta.get(total_wait_key)
        if is_number(events) and int(events) > 0 and is_number(total_wait_ms):
            delta[f"{prefix}_run_avg_wait_ms"] = round(float(total_wait_ms) / int(events), 3)
    for prefix in ["search_log", "error_log"]:
        events = delta.get(f"{prefix}_write_events")
        total_ms = delta.get(f"{prefix}_write_total_ms")
        if is_number(events) and int(events) > 0 and is_number(total_ms):
            delta[f"{prefix}_write_run_avg_ms"] = round(float(total_ms) / int(events), 3)
    return add_embedding_metric_aliases(delta, embedding_backend=after_snapshot.get("embedding_backend"))


def server_metrics_source_coverage(
    before: dict[str, Any],
    after: dict[str, Any],
    expected_api_server_count: int = 1,
) -> dict[str, Any]:
    expected = max(1, int(expected_api_server_count or 1))
    requested = before.get("requested") is True or after.get("requested") is True
    before_ids = set((before.get("snapshot") or {}).get("admin_metrics_instance_ids") or [])
    after_ids = set((after.get("snapshot") or {}).get("admin_metrics_instance_ids") or [])
    instance_ids = sorted(before_ids | after_ids)
    before_count = int(before.get("successful_source_count") or (1 if before.get("ok") is True else 0))
    after_count = int(after.get("successful_source_count") or (1 if after.get("ok") is True else 0))
    successful_source_count = min(before_count, after_count)
    problems = []
    if requested and expected >= 2:
        if successful_source_count < expected:
            problems.append("admin_metrics.source_count_below_api_server_count")
        if len(instance_ids) < expected:
            problems.append("admin_metrics.distinct_instance_count_below_api_server_count")
        for phase, data in [("before", before), ("after", after)]:
            for problem in data.get("aggregation_problems") or []:
                problems.append(f"admin_metrics.{phase}.{problem}")
    return {
        "ok": not problems,
        "expected_api_server_count": expected,
        "successful_source_count": successful_source_count,
        "distinct_instance_count": len(instance_ids),
        "instance_ids": instance_ids,
        "problems": sorted(set(problems)),
    }


def build_server_metrics_report(
    base_url: str,
    admin_key: str,
    before: dict[str, Any],
    after: dict[str, Any],
    expected_api_server_count: int = 1,
) -> dict[str, Any]:
    requested = bool(admin_key)
    source_coverage = server_metrics_source_coverage(before, after, expected_api_server_count)
    return {
        "requested": requested,
        "ok": (
            (not requested)
            or (before.get("ok") is True and after.get("ok") is True and source_coverage["ok"])
        ),
        "base_url": base_url.rstrip("/"),
        "admin_metrics_base_urls": [
            source.get("base_url")
            for source in (after.get("sources") or before.get("sources") or [])
            if source.get("base_url")
        ],
        "admin_metrics_source_coverage": source_coverage,
        "before": before,
        "after": after,
        "delta": server_metrics_delta(before, after) if requested else {},
    }


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
    if str(embedding_backend or "").strip().lower() in {"qwen", "gemini"}:
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


def default_request_timeout_seconds(p99_ms: float | int) -> float:
    return round(
        max(DEFAULT_REQUEST_TIMEOUT_MIN_SECONDS, float(p99_ms) / 1000.0 * DEFAULT_REQUEST_TIMEOUT_TO_P99_RATIO),
        1,
    )


def max_request_timeout_seconds(p99_ms: float | int) -> float:
    return round(
        max(DEFAULT_REQUEST_TIMEOUT_MIN_SECONDS, float(p99_ms) / 1000.0 * MAX_REQUEST_TIMEOUT_TO_P99_RATIO),
        1,
    )


def default_min_rps(concurrency: float | int, p95_ms: float | int) -> float:
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
    prefixes = [
        "cache_lock",
        "singleflight",
        "image_validation",
        "backend_marqo_connection_acquire",
        "search_queue",
        "image_queue",
    ]
    if str(embedding_backend or "").strip().lower() in {"qwen", "gemini"}:
        prefixes.extend(["qwen_query_vector", "backend_qwen_connection_acquire"])
    problems: list[str] = []
    for prefix in prefixes:
        value = delta.get(f"{prefix}_run_avg_wait_ms")
        if not is_number(value):
            continue
        if float(value) > max_wait_avg_ms:
            problems.append(f"server_metrics.delta.{prefix}_run_avg_wait_ms_above_threshold")
    return problems


def annotate_backend_latency_guardrails(
    server_metrics: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    embedding_backend: str | None = None,
) -> None:
    if server_metrics.get("requested") is not True:
        return
    problems = backend_run_latency_problems(
        server_metrics.get("delta") or {},
        thresholds,
        embedding_backend=embedding_backend,
    )
    server_metrics["backend_latency_ok"] = not problems
    server_metrics["backend_latency_problems"] = problems
    if problems:
        server_metrics["ok"] = False


def backend_transport_payload_summary(
    delta: dict[str, Any],
    *,
    embedding_backend: str | None = None,
) -> dict[str, dict[str, Any]]:
    services = ["marqo"]
    embedding_service = "gemini" if str(embedding_backend or "").strip().lower() == "gemini" else "qwen"
    if str(embedding_backend or "").strip().lower() in {"qwen", "gemini"}:
        services.append(embedding_service)
    summary: dict[str, dict[str, Any]] = {}
    for service in services:
        response_bytes = delta.get(f"backend_{service}_total_response_body_bytes")
        decoded_bytes = delta.get(f"backend_{service}_total_decoded_response_body_bytes")
        service_summary: dict[str, Any] = {
            "request_attempts": delta.get(f"backend_{service}_request_attempts"),
            "gzip_responses": delta.get(f"backend_{service}_gzip_responses"),
            "total_response_body_bytes": response_bytes,
            "total_decoded_response_body_bytes": decoded_bytes,
        }
        if is_number(response_bytes) and is_number(decoded_bytes) and float(decoded_bytes) > 0:
            service_summary["compression_ratio"] = round(float(response_bytes) / float(decoded_bytes), 4)
        summary[service] = service_summary
    return summary


def backend_transport_payload_problems(
    delta: dict[str, Any],
    *,
    embedding_backend: str | None = None,
) -> list[str]:
    services = ["marqo"]
    if str(embedding_backend or "").strip().lower() in {"qwen", "gemini"}:
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
    min_marqo_request_attempts: int | None = None,
    min_qwen_request_attempts: int | None = None,
) -> list[str]:
    problems: list[str] = []
    successful = int(successful_responses or 0)
    image_successful = int(image_successful_responses or 0)
    marqo_attempts = delta.get("backend_marqo_request_attempts")
    if successful > 0 and is_number(marqo_attempts) and int(marqo_attempts) <= 0:
        problems.append(BACKEND_MARQO_REQUEST_ATTEMPTS_ZERO_PROBLEM)
    if (
        successful > 0
        and is_number(marqo_attempts)
        and int(min_marqo_request_attempts or 0) > 0
        and int(marqo_attempts) < int(min_marqo_request_attempts or 0)
    ):
        problems.append(BACKEND_MARQO_REQUEST_ATTEMPTS_BELOW_UNIQUE_PROBLEM)
    qwen_attempts = delta.get("backend_qwen_request_attempts")
    if (
        str(embedding_backend or "").strip().lower() in {"qwen", "gemini"}
        and image_successful > 0
        and is_number(qwen_attempts)
        and int(qwen_attempts) <= 0
    ):
        problems.append(BACKEND_QWEN_REQUEST_ATTEMPTS_ZERO_PROBLEM)
    if (
        str(embedding_backend or "").strip().lower() in {"qwen", "gemini"}
        and image_successful > 0
        and is_number(qwen_attempts)
        and int(min_qwen_request_attempts or 0) > 0
        and int(qwen_attempts) < int(min_qwen_request_attempts or 0)
    ):
        problems.append(BACKEND_QWEN_REQUEST_ATTEMPTS_BELOW_UNIQUE_IMAGE_PROBLEM)
    return problems


def annotate_backend_transport_payload_guardrails(
    server_metrics: dict[str, Any],
    *,
    embedding_backend: str | None = None,
) -> None:
    if server_metrics.get("requested") is not True:
        return
    delta = server_metrics.get("delta") or {}
    problems = backend_transport_payload_problems(delta, embedding_backend=embedding_backend)
    server_metrics["backend_payload"] = backend_transport_payload_summary(delta, embedding_backend=embedding_backend)
    server_metrics["backend_payload_ok"] = not problems
    server_metrics["backend_payload_problems"] = problems
    if problems:
        server_metrics["ok"] = False


def expected_server_metric_deltas(response_contract: dict[str, Any]) -> dict[str, int]:
    query_type_counts = response_contract.get("query_type_counts") or {}
    successful = int(response_contract.get("valid_successful_responses") or response_contract.get("successful_responses") or 0)
    image_successful = int(query_type_counts.get("image", 0) or 0) + int(query_type_counts.get("text_image", 0) or 0)
    return {
        "search_events": successful,
        "image_search_events": image_successful,
    }


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def count_run_search_log_entries(entries: list[Any], started_at: Any) -> dict[str, Any]:
    started = parse_iso_datetime(started_at)
    counts = {
        "started_at": started_at,
        "entries_scanned": len(entries),
        "search_events": 0,
        "image_search_events": 0,
        "invalid_timestamp_events": 0,
    }
    if started is None:
        counts["started_at_valid"] = False
        return counts
    counts["started_at_valid"] = True
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "search":
            continue
        timestamp = parse_iso_datetime(entry.get("timestamp"))
        if timestamp is None:
            counts["invalid_timestamp_events"] += 1
            continue
        if timestamp < started:
            continue
        original_timestamp = str(entry.get("timestamp") or "")
        first_timestamp = first_timestamp or original_timestamp
        last_timestamp = original_timestamp
        counts["search_events"] += 1
        if entry.get("query_type") in {"image", "text_image"}:
            counts["image_search_events"] += 1
    counts["first_matched_timestamp"] = first_timestamp
    counts["last_matched_timestamp"] = last_timestamp
    return counts


def build_run_log_coverage(entries: list[Any], started_at: Any, response_contract: dict[str, Any], limit: int = ADMIN_LOG_TAIL_LIMIT) -> dict[str, Any]:
    expected = expected_server_metric_deltas(response_contract)
    counts = count_run_search_log_entries(entries, started_at)
    missing = []
    if counts.get("started_at_valid") is not True:
        missing.append("server_metrics.run_log.started_at")
    for key, minimum in expected.items():
        if minimum <= 0:
            continue
        value = counts.get(key)
        if not is_number(value) or int(value) < minimum:
            missing.append(SERVER_METRIC_RUN_LOG_UNDERCOUNT_PROBLEMS[key])
    return {
        "requested": True,
        "ok": not missing,
        "limit": int(limit),
        "expected_minimums": expected,
        "missing": missing,
        **counts,
    }


def search_log_entry_key(entry: Any) -> str:
    if not isinstance(entry, dict):
        return json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
    return json.dumps(
        {
            "timestamp": entry.get("timestamp"),
            "type": entry.get("type"),
            "mall_id": entry.get("mall_id"),
            "query_type": entry.get("query_type"),
            "q": entry.get("q"),
            "image_hash": entry.get("image_hash"),
            "top_product_ids": entry.get("top_product_ids"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def collect_run_log_coverage(
    base_url: str | list[str] | tuple[str, ...],
    admin_key: str,
    started_at: Any,
    response_contract: dict[str, Any],
    limit: int = ADMIN_LOG_TAIL_LIMIT,
) -> dict[str, Any]:
    if not admin_key:
        return {"requested": False, "ok": True}
    base_urls = normalize_metrics_base_url_input(base_url)
    if not base_urls:
        base_urls = [""]
    raw_results = []
    entries: list[Any] = []
    seen_entries = set()
    for item_base_url in base_urls:
        raw = fetch_admin_search_log(item_base_url, admin_key, limit=limit)
        raw_results.append(
            {
                "base_url": item_base_url.rstrip("/"),
                "ok": raw.get("ok") is True,
                "status": raw.get("status"),
                "elapsed_ms": raw.get("elapsed_ms"),
                "error": raw.get("error"),
            }
        )
        if raw.get("ok") is not True:
            continue
        for entry in raw.get("data") or []:
            key = search_log_entry_key(entry)
            if key in seen_entries:
                continue
            seen_entries.add(key)
            entries.append(entry)
    if not entries and any(result.get("ok") is not True for result in raw_results):
        return {
            "requested": True,
            "ok": False,
            "limit": int(limit),
            "started_at": started_at,
            "status": raw_results[0].get("status") if raw_results else "error",
            "elapsed_ms": round(sum(float(result.get("elapsed_ms") or 0.0) for result in raw_results), 1),
            "sources": raw_results,
            "missing": ["server_metrics.run_log.request"],
            "error": "admin search log request failed",
        }
    coverage = build_run_log_coverage(entries, started_at, response_contract, limit=limit)
    if any(result.get("ok") is not True for result in raw_results):
        coverage["missing"] = sorted(set((coverage.get("missing") or []) + ["server_metrics.run_log.partial_request"]))
        coverage["ok"] = False
    coverage["status"] = raw_results[0].get("status") if len(raw_results) == 1 else "multi"
    coverage["elapsed_ms"] = round(sum(float(result.get("elapsed_ms") or 0.0) for result in raw_results), 1)
    coverage["source_count"] = len(raw_results)
    coverage["sources"] = raw_results
    coverage["deduplicated_entries"] = len(entries)
    return coverage


def annotate_server_metrics_expectations(
    server_metrics: dict[str, Any],
    response_contract: dict[str, Any],
    run_log_coverage: dict[str, Any] | None = None,
) -> None:
    if server_metrics.get("requested") is not True:
        return
    expected = expected_server_metric_deltas(response_contract)
    delta = server_metrics.get("delta") or {}
    missing = []
    for key, minimum in expected.items():
        if minimum <= 0:
            continue
        value = delta.get(key)
        if not is_number(value) or int(value) < minimum:
            missing.append(SERVER_METRIC_DELTA_UNDERCOUNT_PROBLEMS[key])
    server_metrics["expected_delta_minimums"] = expected
    server_metrics["delta_coverage_ok"] = not missing
    server_metrics["delta_coverage_missing"] = missing
    if run_log_coverage is not None:
        server_metrics["run_log_coverage"] = run_log_coverage
    run_log_ok = isinstance(run_log_coverage, dict) and run_log_coverage.get("ok") is True
    coverage_ok = not missing or run_log_ok
    server_metrics["coverage_ok"] = coverage_ok
    server_metrics["coverage_missing"] = [] if coverage_ok else list(missing)
    if missing and not run_log_ok:
        server_metrics["ok"] = False


def api_threadpool_runtime_problems(snapshot: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for field in API_THREADPOOL_SERVER_METRIC_FIELDS:
        if snapshot.get(field) in (None, ""):
            problems.append(f"server_metrics.after.{field}")
    if snapshot.get("api_threadpool_ok") is not True and "server_metrics.after.api_threadpool_ok" not in problems:
        problems.append("server_metrics.after.api_threadpool_ok")
    required = snapshot.get("api_threadpool_required_tokens")
    if not is_number(required) or float(required) <= 0:
        if "server_metrics.after.api_threadpool_required_tokens" not in problems:
            problems.append("server_metrics.after.api_threadpool_required_tokens")
        return sorted(set(problems))
    for field in ["api_threadpool_configured_tokens", "api_threadpool_runtime_tokens"]:
        value = snapshot.get(field)
        problem = f"server_metrics.after.{field}"
        if not is_number(value):
            if problem not in problems:
                problems.append(problem)
        elif float(value) < float(required):
            problems.append(f"{problem}_below_required")
    return sorted(set(problems))


def server_settled_state_problems(snapshot: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for field in SETTLED_SERVER_IN_FLIGHT_FIELDS:
        value = snapshot.get(field)
        if is_number(value) and int(value) > 0:
            problems.append(f"server_metrics.after.{field}_nonzero")
    for field, limit in SERVER_RESOURCE_PERCENT_LIMITS.items():
        value = snapshot.get(field)
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


def process_rss_growth_problems(
    before: dict[str, Any],
    after: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[str]:
    threshold_value = thresholds.get("max_process_rss_growth_mb") if isinstance(thresholds, dict) else None
    threshold_configured = isinstance(thresholds, dict) and "max_process_rss_growth_mb" in thresholds
    max_growth_mb = (
        float(threshold_value)
        if is_number(threshold_value) and float(threshold_value) > 0
        else DEFAULT_MAX_PROCESS_RSS_GROWTH_MB
    )
    if not is_number(before.get("process_memory_rss_bytes")):
        return ["server_metrics.before.process_memory_rss_bytes"] if threshold_configured else []
    if not is_number(after.get("process_memory_rss_bytes")):
        return ["server_metrics.after.process_memory_rss_bytes"] if threshold_configured else []
    growth_mb = process_rss_growth_mb(before, after)
    if growth_mb is not None and growth_mb > max_growth_mb:
        return ["server_metrics.after.process_memory_rss_growth_mb_above_threshold"]
    return []


def annotate_server_metrics_guardrails(
    server_metrics: dict[str, Any],
    request_profile: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
) -> None:
    if server_metrics.get("requested") is not True:
        return
    delta = server_metrics.get("delta") or {}
    before = (server_metrics.get("before") or {}).get("snapshot") or {}
    after = (server_metrics.get("after") or {}).get("snapshot") or {}
    effective_thresholds = thresholds or {}
    rss_growth_mb = process_rss_growth_mb(before, after)
    if rss_growth_mb is not None:
        server_metrics["process_memory_rss_growth_mb"] = rss_growth_mb
    problems: list[str] = []
    for key, problem in SERVER_METRIC_ZERO_DELTA_PROBLEMS.items():
        value = delta.get(key)
        if is_number(value) and float(value) > 0:
            problems.append(problem)
    expected = server_metrics.get("expected_delta_minimums") if isinstance(server_metrics.get("expected_delta_minimums"), dict) else {}
    problems.extend(
        backend_request_attempt_problems(
            delta,
            embedding_backend=after.get("embedding_backend"),
            successful_responses=int(expected.get("search_events") or 0),
            image_successful_responses=int(expected.get("image_search_events") or 0),
            min_marqo_request_attempts=(
                int(request_profile.get("min_backend_marqo_request_attempts") or 0)
                if isinstance(request_profile, dict)
                else None
            ),
            min_qwen_request_attempts=(
                int(request_profile.get("min_backend_qwen_request_attempts") or 0)
                if isinstance(request_profile, dict)
                else None
            ),
        )
    )
    problems.extend(api_threadpool_runtime_problems(after))
    problems.extend(server_settled_state_problems(after))
    problems.extend(process_rss_growth_problems(before, after, effective_thresholds))
    problems.extend(
        server_wait_avg_latency_problems(
            delta,
            effective_thresholds,
            embedding_backend=after.get("embedding_backend"),
        )
    )
    server_metrics["runtime_guardrails_ok"] = not problems
    server_metrics["runtime_guardrails_problems"] = problems
    annotate_backend_transport_payload_guardrails(
        server_metrics,
        embedding_backend=after.get("embedding_backend"),
    )
    if problems:
        server_metrics["ok"] = False


def summarize_response_contract(results: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [item for item in results if is_success_status(item.get("status"))]
    valid_successful = [item for item in successful if item.get("response_valid") is True]
    invalid_successful = [item for item in successful if item.get("response_valid") is not True]
    expected_counts = Counter(str(item.get("expected_query_type") or "unknown") for item in results)
    query_type_counts = Counter(str(item.get("query_type") or "missing") for item in valid_successful)
    engine_counts = Counter(str(item.get("engine") or "missing") for item in valid_successful)
    expected_mall_id_counts = Counter(str(item.get("expected_mall_id") or "missing") for item in successful)
    expected_product_url_prefix_counts = Counter(
        str(item.get("expected_product_url_prefix") or "missing") for item in successful
    )
    product_url_prefix_required = any(str(item.get("expected_product_url_prefix") or "").strip() for item in successful)
    meta_mall_id_counts = Counter(str(item.get("meta_mall_id") or "missing") for item in successful)
    result_mall_id_counts = Counter(
        str(mall_id or "missing")
        for item in successful
        for mall_id in (item.get("result_mall_ids") if isinstance(item.get("result_mall_ids"), list) else [])
    )
    unexpected_query_type_count = sum(
        1
        for item in valid_successful
        if str(item.get("query_type") or "") != str(item.get("expected_query_type") or "")
    )
    mall_id_mismatch_count = sum(1 for item in successful if item.get("mall_id_matches_request") is not True)
    product_url_prefix_mismatch_count = sum(
        int(item.get("product_url_prefix_mismatch_count") or 0)
        for item in successful
        if is_number(item.get("product_url_prefix_mismatch_count") or 0)
    )
    non_marqo_engine_responses = sum(
        count for engine, count in engine_counts.items() if engine.strip().lower() != "marqo"
    )
    return {
        "ok": bool(successful)
        and not invalid_successful
        and unexpected_query_type_count == 0
        and non_marqo_engine_responses == 0
        and mall_id_mismatch_count == 0
        and product_url_prefix_mismatch_count == 0,
        "total_requests": len(results),
        "successful_responses": len(successful),
        "valid_successful_responses": len(valid_successful),
        "invalid_successful_responses": len(invalid_successful),
        "unexpected_query_type_count": unexpected_query_type_count,
        "expected_query_type_counts": dict(sorted(expected_counts.items())),
        "query_type_counts": dict(sorted(query_type_counts.items())),
        "engine_counts": dict(sorted(engine_counts.items())),
        "non_marqo_engine_responses": non_marqo_engine_responses,
        "expected_mall_id_counts": dict(sorted(expected_mall_id_counts.items())),
        "expected_product_url_prefix_counts": dict(sorted(expected_product_url_prefix_counts.items())),
        "product_url_prefix_required": product_url_prefix_required,
        "meta_mall_id_counts": dict(sorted(meta_mall_id_counts.items())),
        "result_mall_id_counts": dict(sorted(result_mall_id_counts.items())),
        "mall_id_mismatch_count": mall_id_mismatch_count,
        "product_url_prefix_mismatch_count": product_url_prefix_mismatch_count,
        "min_top_count": min_int_field(valid_successful, "top_count"),
        "min_item_count": min_int_field(valid_successful, "item_count"),
        "min_category_count": min_int_field(valid_successful, "category_count"),
        "invalid_samples": [
            {
                "status": item.get("status"),
                "expected_query_type": item.get("expected_query_type"),
                "query_type": item.get("query_type"),
                "engine": item.get("engine"),
                "error": item.get("error"),
            }
            for item in invalid_successful[:5]
        ],
    }


def api_instance_coverage_report(results: list[dict[str, Any]], expected_api_server_count: int) -> dict[str, Any]:
    expected_instances = max(1, int(expected_api_server_count or 1))
    successful = [item for item in results if is_success_status(item.get("status"))]
    counts = Counter(
        normalize_api_instance_id(item.get("api_instance_id"))
        for item in successful
        if normalize_api_instance_id(item.get("api_instance_id"))
    )
    missing_header_count = len(successful) - sum(counts.values())
    required_instances = min(expected_instances, len(successful)) if successful else expected_instances
    min_instance_responses = (
        max(1, math.floor(len(successful) * MIN_API_INSTANCE_TRAFFIC_SHARE))
        if expected_instances >= 2 and successful
        else 0
    )
    under_minimum = sorted(
        instance_id
        for instance_id, count in counts.items()
        if min_instance_responses > 0 and count < min_instance_responses
    )
    problems: list[str] = []
    if expected_instances >= 2:
        if missing_header_count > 0:
            problems.append("api_instance.missing_header")
        if len(counts) < required_instances:
            problems.append("api_instance.distinct_count")
        if under_minimum:
            problems.append("api_instance.minimum_instance_share")
    return {
        "ok": not problems,
        "expected_api_server_count": expected_instances,
        "required_distinct_api_instances": required_instances,
        "distinct_api_instance_count": len(counts),
        "successful_responses": len(successful),
        "missing_header_count": missing_header_count,
        "minimum_instance_responses": min_instance_responses,
        "api_instance_counts": dict(sorted(counts.items())),
        "under_minimum_api_instances": under_minimum,
        "problems": sorted(set(problems)),
    }


def min_int_field(items: list[dict[str, Any]], field: str) -> int | None:
    values = [int(item[field]) for item in items if is_number(item.get(field))]
    return min(values) if values else None


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def latency_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    return {
        "count": len(values),
        "min": round(min(values), 1),
        "avg": round(statistics.mean(values), 1),
        "p50": round(percentile(values, 50), 1),
        "p95": round(percentile(values, 95), 1),
        "p99": round(percentile(values, 99), 1),
        "max": round(max(values), 1),
    }


def latency_breakdown(results: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for item in results:
        key = str(item.get(field) or "missing")
        grouped.setdefault(key, []).append(float(item.get("elapsed_ms") or 0.0))
    return {key: latency_summary(values) for key, values in sorted(grouped.items())}


def expected_query_type_counts_from_mode_counts(mode_counts: dict[str, Any]) -> dict[str, int]:
    if not isinstance(mode_counts, dict):
        return {}
    mapping = {"text": "text", "image": "image", "mixed": "text_image"}
    counts: dict[str, int] = {}
    for mode, query_type in mapping.items():
        if not is_number(mode_counts.get(mode)):
            continue
        count = int(float(mode_counts.get(mode) or 0))
        if count > 0:
            counts[query_type] = counts.get(query_type, 0) + count
    return counts


def query_type_latency_report(
    *,
    mode_counts: dict[str, Any],
    thresholds: dict[str, Any],
    expected_latency: dict[str, dict[str, Any]],
    response_latency: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    planned_query_type_counts = expected_query_type_counts_from_mode_counts(mode_counts)
    total_planned = sum(planned_query_type_counts.values())
    p95_limit = thresholds.get("p95_ms") if isinstance(thresholds, dict) else None
    p99_limit = thresholds.get("p99_ms") if isinstance(thresholds, dict) else None
    max_error_rate = max(0.0, float(thresholds.get("max_error_rate") or 0.0)) if isinstance(thresholds, dict) and is_number(thresholds.get("max_error_rate")) else 0.0
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
    if not is_number(p99_limit) or float(p99_limit) <= 0:
        problems.append("thresholds.p99_ms")
    for query_type, expected_count in planned_query_type_counts.items():
        expected_summary = expected_latency.get(query_type) if isinstance(expected_latency, dict) else None
        if not isinstance(expected_summary, dict):
            problems.append(f"expected_query_type_latency_ms.{query_type}")
        elif int(float(expected_summary.get("count") or 0)) < expected_count:
            problems.append(f"expected_query_type_latency_ms.{query_type}.count")

        response_summary = response_latency.get(query_type) if isinstance(response_latency, dict) else None
        if not isinstance(response_summary, dict):
            problems.append(f"response_query_type_latency_ms.{query_type}")
            continue
        if int(float(response_summary.get("count") or 0)) < minimum_response_counts.get(query_type, 1):
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
        "expected_query_type_latency_ms": expected_latency if isinstance(expected_latency, dict) else {},
        "response_query_type_latency_ms": response_latency if isinstance(response_latency, dict) else {},
    }


def default_p99_ms(p95_ms: float | int) -> float:
    return round(max(float(p95_ms), float(p95_ms) * DEFAULT_P99_TO_P95_RATIO), 1)


def run(args: argparse.Namespace) -> dict[str, Any]:
    global ALLOW_LOCAL_TARGET, ALLOW_PRIVATE_ADMIN_METRICS_TARGETS
    ALLOW_LOCAL_TARGET = bool(getattr(args, "allow_local_target", False))
    ALLOW_PRIVATE_ADMIN_METRICS_TARGETS = bool(getattr(args, "allow_private_admin_metrics_targets", False))
    validate_args(args)
    base_url = args.base_url.rstrip("/")
    origin = str(getattr(args, "origin", "") or "")
    url = base_url + "/api/ai-search"
    image_base64_values, image_input = image_data_urls_for_args(args)
    expected_product_url_prefix = str(getattr(args, "expected_product_url_prefix", "") or "")
    payloads, mode_counts, traffic_mix_percent = build_payloads(args, image_base64_values)
    identities = load_mall_identities(args)
    request_specs = build_request_specs(
        payloads,
        identities,
        client_ip_count=getattr(args, "client_ip_count", 0),
        client_ip_prefix=getattr(args, "client_ip_prefix", "198.51.100"),
    )
    request_profile = summarize_request_profile(request_specs)
    requested_mall_sample_size = int(getattr(args, "mall_sample_size", 0) or 0)
    runtime_health = fetch_runtime_health(base_url)
    metrics_before = collect_server_metrics(args.admin_metrics_base_urls, args.admin_key, phase="before")
    search_client = TargetJsonHttpClient(base_url)
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                post_json,
                url,
                spec.payload,
                spec.headers,
                spec.expected_product_url_prefix,
                search_client,
                args.request_timeout_seconds,
            )
            for spec in request_specs
        ]
        for future in as_completed(futures):
            results.append(future.result())
    total_ms = (time.perf_counter() - started) * 1000
    latencies = [float(item["elapsed_ms"]) for item in results]
    ok_count = sum(1 for item in results if item["ok"])
    error_count = len(results) - ok_count
    error_rate = (error_count / len(results)) * 100 if results else 0
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    metrics_after = collect_server_metrics(args.admin_metrics_base_urls, args.admin_key, phase="after")
    server_metrics = build_server_metrics_report(
        args.base_url,
        args.admin_key,
        metrics_before,
        metrics_after,
        expected_api_server_count=args.api_server_count,
    )
    response_contract = summarize_response_contract(results)
    api_instance_coverage = api_instance_coverage_report(results, args.api_server_count)
    metrics_started_at = (metrics_before.get("snapshot") or {}).get("generated_at")
    run_log_coverage = collect_run_log_coverage(
        args.admin_metrics_base_urls,
        args.admin_key,
        metrics_started_at,
        response_contract,
    )
    expected_query_type_latency = latency_breakdown(results, "expected_query_type")
    response_query_type_latency = latency_breakdown(
        [item for item in results if is_success_status(item.get("status"))],
        "query_type",
    )
    thresholds = {
        "p95_ms": args.p95_ms,
        "p99_ms": args.p99_ms,
        "request_timeout_seconds": args.request_timeout_seconds,
        "max_server_wait_avg_ms": args.max_server_wait_avg_ms,
        "min_requests_per_second": args.min_rps,
        "max_process_rss_growth_mb": args.max_process_rss_growth_mb,
        "max_error_rate": args.max_error_rate,
    }
    metrics_after_snapshot = (server_metrics.get("after") or {}).get("snapshot") or {}
    annotate_server_metrics_expectations(server_metrics, response_contract, run_log_coverage)
    annotate_server_metrics_guardrails(server_metrics, request_profile, thresholds)
    annotate_backend_latency_guardrails(
        server_metrics,
        thresholds,
        embedding_backend=metrics_after_snapshot.get("embedding_backend"),
    )
    query_type_latency = query_type_latency_report(
        mode_counts=mode_counts,
        thresholds=thresholds,
        expected_latency=expected_query_type_latency,
        response_latency=response_query_type_latency,
    )
    report = {
        "ok": (
            error_rate <= args.max_error_rate
            and (round((len(results) / total_ms) * 1000, 2) if total_ms else 0) >= args.min_rps
            and p95 <= args.p95_ms
            and p99 <= args.p99_ms
            and query_type_latency["ok"]
            and api_instance_coverage["ok"]
            and server_metrics["ok"]
            and response_contract["ok"]
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "mall_id": args.mall_id,
        "origin": origin or None,
        "expected_product_url_prefix": expected_product_url_prefix or None,
        "api_server_count": args.api_server_count,
        "scenario": args.scenario,
        "active_users": args.active_users,
        "mall_identity": load_identity_summary(
            identities,
            requested_mall_sample_size,
            sampling_strategy=getattr(args, "mall_sample_strategy", ""),
            source_enabled_count=getattr(args, "mall_sample_source_enabled_count", None),
            eligible_mall_count=getattr(args, "mall_sample_eligible_count", None),
        ),
        "mode": args.mode,
        "mode_counts": mode_counts,
        "request_profile": request_profile,
        "traffic_mix_percent": traffic_mix_percent,
        "requests": len(results),
        "concurrency": args.concurrency,
        "ok_count": ok_count,
        "error_count": error_count,
        "error_rate": round(error_rate, 2),
        "total_ms": round(total_ms, 1),
        "requests_per_second": round((len(results) / total_ms) * 1000, 2) if total_ms else 0,
        "latency_ms": {
            "min": round(min(latencies), 1) if latencies else 0,
            "avg": round(statistics.mean(latencies), 1) if latencies else 0,
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(p95, 1),
            "p99": round(p99, 1),
            "max": round(max(latencies), 1) if latencies else 0,
        },
        "client_transport": {
            "search_requests": search_client.stats(),
            "connection_reuse": "thread_local_keep_alive",
        },
        "expected_query_type_latency_ms": expected_query_type_latency,
        "response_query_type_latency_ms": response_query_type_latency,
        "query_type_latency": query_type_latency,
        "api_instance_coverage": api_instance_coverage,
        "thresholds": thresholds,
        "runtime_health": runtime_health,
        "image_input": image_input,
        "response_contract": response_contract,
        "server_metrics": server_metrics,
        "target_validation": {
            "ok": True,
            "base_url": base_url,
            "origin": origin or None,
        },
    }
    if error_count:
        report["statuses"] = sorted({str(item["status"]) for item in results})
        report["error_samples"] = [
            {"status": item.get("status"), "error": item.get("error")}
            for item in results
            if not item.get("ok")
        ][:5]
    return operator_visible_load_report(report)


def to_markdown(report: dict[str, Any]) -> str:
    latency = report["latency_ms"]
    thresholds = report["thresholds"]
    server_metrics = report.get("server_metrics") or {}
    after_snapshot = ((server_metrics.get("after") or {}).get("snapshot") or {}) if isinstance(server_metrics, dict) else {}
    runtime_health = report.get("runtime_health") or {}
    runtime_health_data = (runtime_health.get("data") or {}) if isinstance(runtime_health, dict) else {}
    embedding_backend = str(
        after_snapshot.get("embedding_backend")
        or runtime_health.get("embedding_backend")
        or runtime_health_data.get("embedding_backend")
        or ""
    ).lower()
    embedding_label = "Gemini" if embedding_backend == "gemini" else "Qwen" if embedding_backend == "qwen" else "Embedding provider"
    embedding_metric = "gemini" if embedding_backend == "gemini" else "qwen"
    backend_metric_prefix = f"backend_{embedding_metric}"
    query_vector_metric_prefix = f"{embedding_metric}_query_vector"
    lines = [
        "# Haeorum AI Search Load Test Report",
        "",
        f"- OK: `{report['ok']}`",
        f"- Base URL: `{report['base_url']}`",
        f"- API Server Count: `{report.get('api_server_count')}`",
        f"- Scenario: `{report['scenario']}`",
        f"- Active Users: `{report['active_users']}`",
        f"- Image source: `{(report.get('image_input') or {}).get('source')}`",
        f"- Image file: `{(report.get('image_input') or {}).get('file')}`",
        f"- Image file count: `{(report.get('image_input') or {}).get('file_count', 1 if (report.get('image_input') or {}).get('source') == 'file' else 0)}`",
        f"- Unique image files: `{(report.get('image_input') or {}).get('unique_sha256_count', 1 if (report.get('image_input') or {}).get('source') == 'file' else 0)}`",
        f"- Requests: `{report['requests']}`",
        f"- Concurrency: `{report['concurrency']}`",
        f"- Error Rate: `{report['error_rate']}%`",
        f"- RPS: `{report['requests_per_second']}`",
    ]
    client_transport = report.get("client_transport") or {}
    search_transport = client_transport.get("search_requests") if isinstance(client_transport, dict) else {}
    if isinstance(search_transport, dict):
        lines.extend(
            [
                f"- Client transport: `{client_transport.get('connection_reuse')}`",
                f"- Client search connections opened: `{search_transport.get('connections_opened')}`",
                f"- Client search connection reuses: `{search_transport.get('connection_reuses')}`",
                f"- Client search request attempts: `{search_transport.get('request_attempts')}`",
                f"- Client search requests sent: `{search_transport.get('requests_sent')}`",
                f"- Client search stale reconnects: `{search_transport.get('stale_reconnects')}`",
                f"- Client search connection-close responses: `{search_transport.get('connection_close_responses')}`",
                f"- Client search gzip responses: `{search_transport.get('gzip_responses')}`",
                f"- Client search response wire bytes: `{search_transport.get('total_response_body_bytes')}`",
                f"- Client search response decoded bytes: `{search_transport.get('total_decoded_response_body_bytes')}`",
            ]
        )
    lines.extend(["", "## Traffic Mix", "", "| Mode | Count | Target % |", "| --- | ---: | ---: |"])
    for mode in MODES:
        lines.append(f"| {mode} | {report['mode_counts'].get(mode, 0)} | {report['traffic_mix_percent'].get(mode, 0)} |")
    request_profile = report.get("request_profile") or {}
    if request_profile:
        lines.extend(
            [
                "",
                "## Request Profile",
                "",
                f"- Unique request signatures: `{request_profile.get('unique_request_signatures')}`",
                f"- Repeated requests: `{request_profile.get('repeated_request_count')}`",
                f"- Unique text queries: `{request_profile.get('unique_text_queries')}`",
                f"- Unique image inputs: `{request_profile.get('unique_image_inputs')}`",
                f"- Minimum Marqo backend attempts: `{request_profile.get('min_backend_marqo_request_attempts')}`",
                f"- Minimum {embedding_label} embedding backend attempts: `{request_profile.get(f'min_backend_{embedding_metric}_request_attempts')}`",
                f"- Unique by query type: `{json.dumps(request_profile.get('unique_by_query_type') or {}, ensure_ascii=False)}`",
            ]
        )
    mall_identity = report.get("mall_identity") or {}
    if isinstance(mall_identity, dict):
        lines.extend(
            [
                "",
                "## Mall Identity Coverage",
                "",
                f"- Multi-mall sampling enabled: `{mall_identity.get('enabled')}`",
                f"- Requested sample size: `{mall_identity.get('sample_size_requested')}`",
                f"- Sampled count: `{mall_identity.get('sampled_count')}`",
                f"- Distinct mall count: `{mall_identity.get('distinct_mall_count')}`",
                f"- Per-mall API keys: `{mall_identity.get('per_mall_api_keys')}`",
                f"- Per-mall Origins: `{mall_identity.get('per_mall_origins')}`",
                f"- Per-mall product URL prefixes: `{mall_identity.get('per_mall_product_url_prefixes')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Latency",
            "",
            "| Metric | ms |",
            "| --- | ---: |",
            f"| min | {latency['min']} |",
            f"| avg | {latency['avg']} |",
            f"| p50 | {latency['p50']} |",
            f"| p95 | {latency['p95']} |",
            f"| p99 | {latency['p99']} |",
            f"| max | {latency['max']} |",
            "",
            "## Thresholds",
            "",
            f"- p95_ms: `{thresholds['p95_ms']}`",
            f"- p99_ms: `{thresholds.get('p99_ms')}`",
            f"- request_timeout_seconds: `{thresholds.get('request_timeout_seconds')}`",
            f"- max_server_wait_avg_ms: `{thresholds.get('max_server_wait_avg_ms')}`",
            f"- min_requests_per_second: `{thresholds.get('min_requests_per_second')}`",
            f"- max_error_rate: `{thresholds['max_error_rate']}`",
        ]
    )
    query_type_latency = report.get("query_type_latency") or {}
    if query_type_latency:
        lines.extend(
            [
                "",
                f"- Query type latency OK: `{query_type_latency.get('ok')}`",
                f"- Query type latency problems: `{', '.join(query_type_latency.get('problems') or [])}`",
            ]
        )
    expected_latency = report.get("expected_query_type_latency_ms") or {}
    response_latency = report.get("response_query_type_latency_ms") or {}
    if expected_latency:
        lines.extend(["", "## Expected Query Type Latency", "", "| Query Type | Count | p95 ms | p99 ms | max ms |", "| --- | ---: | ---: | ---: | ---: |"])
        for query_type, summary in expected_latency.items():
            lines.append(
                f"| {query_type} | {summary.get('count')} | {summary.get('p95')} | "
                f"{summary.get('p99')} | {summary.get('max')} |"
            )
    if response_latency:
        lines.extend(["", "## Response Query Type Latency", "", "| Query Type | Count | p95 ms | p99 ms | max ms |", "| --- | ---: | ---: | ---: | ---: |"])
        for query_type, summary in response_latency.items():
            lines.append(
                f"| {query_type} | {summary.get('count')} | {summary.get('p95')} | "
                f"{summary.get('p99')} | {summary.get('max')} |"
            )
    if report.get("statuses"):
        lines.extend(["", f"- Error statuses: `{', '.join(report['statuses'])}`"])
    response_contract = report.get("response_contract") or {}
    if response_contract:
        lines.extend(
            [
                "",
                "## Response Contract",
                "",
                f"- Contract OK: `{response_contract.get('ok')}`",
                f"- Successful responses: `{response_contract.get('successful_responses')}`",
                f"- Valid successful responses: `{response_contract.get('valid_successful_responses')}`",
                f"- Invalid successful responses: `{response_contract.get('invalid_successful_responses')}`",
                f"- Query type counts: `{json.dumps(response_contract.get('query_type_counts') or {}, ensure_ascii=False)}`",
                f"- Engine counts: `{json.dumps(response_contract.get('engine_counts') or {}, ensure_ascii=False)}`",
                f"- Non-Marqo engine responses: `{response_contract.get('non_marqo_engine_responses')}`",
                f"- Expected mall_id counts: `{json.dumps(response_contract.get('expected_mall_id_counts') or {}, ensure_ascii=False)}`",
                f"- Meta mall_id counts: `{json.dumps(response_contract.get('meta_mall_id_counts') or {}, ensure_ascii=False)}`",
                f"- Result mall_id counts: `{json.dumps(response_contract.get('result_mall_id_counts') or {}, ensure_ascii=False)}`",
                f"- Mall_id mismatch responses: `{response_contract.get('mall_id_mismatch_count')}`",
                f"- Expected product URL prefixes: `{json.dumps(response_contract.get('expected_product_url_prefix_counts') or {}, ensure_ascii=False)}`",
                f"- Product URL prefix mismatches: `{response_contract.get('product_url_prefix_mismatch_count')}`",
                f"- Minimum top count: `{response_contract.get('min_top_count')}`",
                f"- Minimum related item count: `{response_contract.get('min_item_count')}`",
                f"- Minimum category count: `{response_contract.get('min_category_count')}`",
            ]
        )
    api_instance_coverage = report.get("api_instance_coverage") or {}
    if api_instance_coverage:
        lines.extend(
            [
                "",
                "## API Instance Coverage",
                "",
                f"- OK: `{api_instance_coverage.get('ok')}`",
                f"- Expected API server count: `{api_instance_coverage.get('expected_api_server_count')}`",
                f"- Distinct API instance count: `{api_instance_coverage.get('distinct_api_instance_count')}`",
                f"- Missing instance headers: `{api_instance_coverage.get('missing_header_count')}`",
                f"- Minimum instance responses: `{api_instance_coverage.get('minimum_instance_responses')}`",
                f"- API instance counts: `{json.dumps(api_instance_coverage.get('api_instance_counts') or {}, ensure_ascii=False)}`",
                f"- Problems: `{json.dumps(api_instance_coverage.get('problems') or [], ensure_ascii=False)}`",
            ]
        )
    if server_metrics.get("requested"):
        after = (server_metrics.get("after") or {}).get("snapshot") or {}
        delta = server_metrics.get("delta") or {}
        run_log_coverage = server_metrics.get("run_log_coverage") or {}
        embedding_backend = str(after.get("embedding_backend") or "").lower()
        embedding_label = "Gemini" if embedding_backend == "gemini" else "Qwen" if embedding_backend == "qwen" else "Embedding provider"
        embedding_metric = "gemini" if embedding_backend == "gemini" else "qwen"
        backend_metric_prefix = f"backend_{embedding_metric}"
        query_vector_metric_prefix = f"{embedding_metric}_query_vector"
        embedding_model_value = after.get("gemini_model") or after.get("qwen_model")
        embedding_dimensions_value = after.get("gemini_embedding_dimensions") or after.get("qwen_embedding_dimensions")
        compatibility_note = ""
        lines.extend(
            [
                "",
                "## Server Metrics",
                "",
                f"- Metrics OK: `{server_metrics.get('ok')}`",
                f"- Admin metrics source coverage OK: `{(server_metrics.get('admin_metrics_source_coverage') or {}).get('ok')}`",
                f"- Admin metrics source count: `{(server_metrics.get('admin_metrics_source_coverage') or {}).get('successful_source_count')}`",
                f"- Admin metrics instance IDs: `{json.dumps((server_metrics.get('admin_metrics_source_coverage') or {}).get('instance_ids') or [], ensure_ascii=False)}`",
                f"- Admin metrics source problems: `{json.dumps((server_metrics.get('admin_metrics_source_coverage') or {}).get('problems') or [], ensure_ascii=False)}`",
                f"- Engine OK: `{after.get('engine_ok')}`",
                f"- Engine backend: `{after.get('engine_backend')}`",
                f"- Engine index: `{after.get('engine_index')}`",
                f"- Marqo model: `{after.get('marqo_model')}`",
                f"- Embedding backend: `{after.get('embedding_backend')}`",
                f"- {embedding_label} model: `{embedding_model_value}`",
                f"- {embedding_label} embedding dimensions: `{embedding_dimensions_value}`",
                f"- {embedding_label} query vector runtime entries{compatibility_note}: `{after.get(f'{query_vector_metric_prefix}_runtime_entries')}`",
                f"- {embedding_label} query vector text/image entries{compatibility_note}: `{after.get(f'{query_vector_metric_prefix}_runtime_text_entries')}` / `{after.get(f'{query_vector_metric_prefix}_runtime_image_entries')}`",
                f"- {embedding_label} query vector text/image max entries{compatibility_note}: `{after.get(f'{query_vector_metric_prefix}_runtime_text_max_entries')}` / `{after.get(f'{query_vector_metric_prefix}_runtime_image_max_entries')}`",
                f"- {embedding_label} query vector wait timeout seconds{compatibility_note}: `{after.get(f'{query_vector_metric_prefix}_wait_timeout_seconds')}`",
                f"- {embedding_label} query vector wait events/timeouts{compatibility_note}: `{after.get(f'{query_vector_metric_prefix}_wait_events')}` / `{after.get(f'{query_vector_metric_prefix}_wait_timeouts')}`",
                f"- {embedding_label} query vector max wait ms{compatibility_note}: `{after.get(f'{query_vector_metric_prefix}_max_wait_ms')}`",
                f"- Engine health cache enabled/hit: `{after.get('engine_health_cache_enabled')}` / `{after.get('engine_health_cache_hit')}`",
                f"- Engine health cache TTL/age: `{after.get('engine_health_cache_ttl_seconds')}` / `{after.get('engine_health_cache_age_ms')}`",
                f"- Engine search attempts/adaptive refetches: `{after.get('engine_search_attempts')}` / `{after.get('engine_adaptive_refetches')}`",
                f"- Engine adaptive refetch searches/underfilled events: `{after.get('engine_adaptive_refetch_searches')}` / `{after.get('engine_underfilled_after_max_candidates_events')}`",
                f"- Engine avg/max search attempts: `{after.get('engine_average_search_attempts')}` / `{after.get('engine_max_search_attempts')}`",
                f"- Engine avg/max final candidate limit: `{after.get('engine_average_final_candidate_limit')}` / `{after.get('engine_max_final_candidate_limit')}`",
                f"- Backend Marqo request attempts: `{after.get('backend_marqo_request_attempts')}`",
                f"- Backend Marqo connections opened: `{after.get('backend_marqo_connections_opened')}`",
                f"- Backend Marqo connection reuses: `{after.get('backend_marqo_connection_reuses')}`",
                f"- Backend Marqo idle reconnects: `{after.get('backend_marqo_idle_reconnects')}`",
                f"- Backend Marqo stale reconnects: `{after.get('backend_marqo_stale_reconnects')}`",
                f"- Backend Marqo error responses: `{after.get('backend_marqo_error_responses')}`",
                f"- Backend Marqo connection-close responses: `{after.get('backend_marqo_connection_close_responses')}`",
                f"- Backend Marqo gzip responses: `{after.get('backend_marqo_gzip_responses')}`",
                f"- Backend Marqo Retry-After responses/max seconds: `{after.get('backend_marqo_retry_after_responses')}` / `{after.get('backend_marqo_max_retry_after_seconds')}`",
                f"- Backend Marqo circuit open/short/recovery events: `{after.get('backend_marqo_circuit_open_events')}` / `{after.get('backend_marqo_circuit_short_circuits')}` / `{after.get('backend_marqo_circuit_recovery_events')}`",
                f"- Backend Marqo avg/max elapsed ms: `{after.get('backend_marqo_avg_elapsed_ms')}` / `{after.get('backend_marqo_max_elapsed_ms')}`",
                f"- Backend Marqo max request body bytes: `{after.get('backend_marqo_max_request_body_bytes')}`",
                f"- Backend Marqo max response body bytes: `{after.get('backend_marqo_max_response_body_bytes')}`",
                f"- Backend Marqo max decoded response body bytes: `{after.get('backend_marqo_max_decoded_response_body_bytes')}`",
                f"- Backend {embedding_label} request attempts{compatibility_note}: `{after.get(f'{backend_metric_prefix}_request_attempts')}`",
                f"- Backend {embedding_label} connections opened{compatibility_note}: `{after.get(f'{backend_metric_prefix}_connections_opened')}`",
                f"- Backend {embedding_label} connection reuses{compatibility_note}: `{after.get(f'{backend_metric_prefix}_connection_reuses')}`",
                f"- Backend {embedding_label} idle reconnects{compatibility_note}: `{after.get(f'{backend_metric_prefix}_idle_reconnects')}`",
                f"- Backend {embedding_label} stale reconnects{compatibility_note}: `{after.get(f'{backend_metric_prefix}_stale_reconnects')}`",
                f"- Backend {embedding_label} error responses{compatibility_note}: `{after.get(f'{backend_metric_prefix}_error_responses')}`",
                f"- Backend {embedding_label} connection-close responses{compatibility_note}: `{after.get(f'{backend_metric_prefix}_connection_close_responses')}`",
                f"- Backend {embedding_label} gzip responses{compatibility_note}: `{after.get(f'{backend_metric_prefix}_gzip_responses')}`",
                f"- Backend {embedding_label} Retry-After responses/max seconds{compatibility_note}: `{after.get(f'{backend_metric_prefix}_retry_after_responses')}` / `{after.get(f'{backend_metric_prefix}_max_retry_after_seconds')}`",
                f"- Backend {embedding_label} circuit open/short/recovery events{compatibility_note}: `{after.get(f'{backend_metric_prefix}_circuit_open_events')}` / `{after.get(f'{backend_metric_prefix}_circuit_short_circuits')}` / `{after.get(f'{backend_metric_prefix}_circuit_recovery_events')}`",
                f"- Backend {embedding_label} avg/max elapsed ms{compatibility_note}: `{after.get(f'{backend_metric_prefix}_avg_elapsed_ms')}` / `{after.get(f'{backend_metric_prefix}_max_elapsed_ms')}`",
                f"- Backend {embedding_label} max request body bytes{compatibility_note}: `{after.get(f'{backend_metric_prefix}_max_request_body_bytes')}`",
                f"- Backend {embedding_label} max response body bytes{compatibility_note}: `{after.get(f'{backend_metric_prefix}_max_response_body_bytes')}`",
                f"- Backend {embedding_label} max decoded response body bytes{compatibility_note}: `{after.get(f'{backend_metric_prefix}_max_decoded_response_body_bytes')}`",
                f"- Process CPU %: `{after.get('process_cpu_percent')}`",
                f"- Process RSS bytes: `{after.get('process_memory_rss_bytes')}`",
                f"- Process RSS growth MB: `{server_metrics.get('process_memory_rss_growth_mb')}`",
                f"- System CPU %: `{after.get('system_cpu_percent')}`",
                f"- System memory used %: `{after.get('system_memory_used_percent')}`",
                f"- Disk used %: `{after.get('disk_used_percent')}`",
                f"- Rate limit backend: `{after.get('rate_limit_backend')}`",
                f"- Rate limit Redis enabled: `{after.get('rate_limit_redis_enabled')}`",
                f"- Rate limit search/mall per minute: `{after.get('rate_limit_search_per_minute')}` / `{after.get('rate_limit_mall_search_per_minute')}`",
                f"- Rate limit image/mall image per minute: `{after.get('rate_limit_image_per_minute')}` / `{after.get('rate_limit_mall_image_per_minute')}`",
                f"- Rate limit fallback events: `{after.get('rate_limit_fallback_events')}`",
                f"- Rate limit fallback buckets/max/pruned: `{after.get('rate_limit_fallback_bucket_count')}` / `{after.get('rate_limit_fallback_max_buckets')}` / `{after.get('rate_limit_fallback_pruned_buckets')}`",
                f"- Cache backend: `{after.get('cache_backend')}`",
                f"- Cache Redis enabled: `{after.get('cache_redis_enabled')}`",
                f"- Cache max entries: `{after.get('cache_max_entries')}`",
                f"- Cache evictions: `{after.get('cache_evictions')}`",
                f"- Cache error count: `{after.get('cache_error_count')}`",
                f"- Cache clear errors: `{after.get('cache_clear_errors')}`",
                f"- Cache miss lock claims: `{after.get('cache_lock_claims')}`",
                f"- Cache miss lock contention events: `{after.get('cache_lock_contention_events')}`",
                f"- Cache miss lock wait events: `{after.get('cache_lock_wait_events')}`",
                f"- Cache miss lock wait timeouts: `{after.get('cache_lock_wait_timeouts')}`",
                f"- Cache miss lock max wait ms: `{after.get('cache_lock_max_wait_ms')}`",
                f"- Singleflight wait events: `{after.get('singleflight_wait_events')}`",
                f"- Singleflight wait timeouts: `{after.get('singleflight_wait_timeouts')}`",
                f"- Singleflight max wait ms: `{after.get('singleflight_max_wait_ms')}`",
                f"- Image validation wait events: `{after.get('image_validation_wait_events')}`",
                f"- Image validation wait timeouts: `{after.get('image_validation_wait_timeouts')}`",
                f"- Image validation max wait ms: `{after.get('image_validation_max_wait_ms')}`",
                f"- Search queue enabled: `{after.get('search_queue_enabled')}`",
                f"- Search queue max concurrency: `{after.get('search_queue_max_concurrency')}`",
                f"- Search queue full events: `{after.get('search_queue_full_events')}`",
                f"- Search queue wait events: `{after.get('search_queue_wait_events')}`",
                f"- Search queue avg wait ms: `{after.get('search_queue_avg_wait_ms')}`",
                f"- Search queue max wait ms: `{after.get('search_queue_max_wait_ms')}`",
                f"- Image queue enabled: `{after.get('image_queue_enabled')}`",
                f"- Image queue max concurrency: `{after.get('image_queue_max_concurrency')}`",
                f"- Image queue timeout seconds: `{after.get('image_queue_timeout_seconds')}`",
                f"- Image queue full events: `{after.get('image_queue_full_events')}`",
                f"- Image queue wait events: `{after.get('image_queue_wait_events')}`",
                f"- Image queue avg wait ms: `{after.get('image_queue_avg_wait_ms')}`",
                f"- Image queue max wait ms: `{after.get('image_queue_max_wait_ms')}`",
                f"- API threadpool OK: `{after.get('api_threadpool_ok')}`",
                f"- API threadpool configured/runtime/required tokens: `{after.get('api_threadpool_configured_tokens')}` / `{after.get('api_threadpool_runtime_tokens')}` / `{after.get('api_threadpool_required_tokens')}`",
                f"- Search log write errors: `{after.get('search_log_write_errors')}`",
                f"- Error log write errors: `{after.get('error_log_write_errors')}`",
                f"- Search event delta: `{delta.get('search_events')}`",
                f"- Image search event delta: `{delta.get('image_search_events')}`",
                f"- Engine search attempt delta: `{delta.get('engine_search_attempts')}`",
                f"- Engine adaptive refetch delta: `{delta.get('engine_adaptive_refetches')}`",
                f"- Engine adaptive refetch search delta: `{delta.get('engine_adaptive_refetch_searches')}`",
                f"- Engine underfilled-after-max-candidates delta: `{delta.get('engine_underfilled_after_max_candidates_events')}`",
                f"- Result mall mismatch event delta: `{delta.get('result_mall_id_mismatch_events')}`",
                f"- Result mall mismatch count delta: `{delta.get('result_mall_id_mismatch_count')}`",
                f"- Backend Marqo request attempt delta: `{delta.get('backend_marqo_request_attempts')}`",
                f"- Backend Marqo idle reconnect delta: `{delta.get('backend_marqo_idle_reconnects')}`",
                f"- Backend Marqo stale reconnect delta: `{delta.get('backend_marqo_stale_reconnects')}`",
                f"- Backend Marqo error response delta: `{delta.get('backend_marqo_error_responses')}`",
                f"- Backend Marqo connection-close response delta: `{delta.get('backend_marqo_connection_close_responses')}`",
                f"- Backend Marqo gzip response delta: `{delta.get('backend_marqo_gzip_responses')}`",
                f"- Backend Marqo Retry-After response delta: `{delta.get('backend_marqo_retry_after_responses')}`",
                f"- Backend Marqo circuit open/short delta: `{delta.get('backend_marqo_circuit_open_events')}` / `{delta.get('backend_marqo_circuit_short_circuits')}`",
                f"- Backend Marqo run avg elapsed ms: `{delta.get('backend_marqo_run_avg_elapsed_ms')}`",
                f"- Backend Marqo run avg response body bytes: `{delta.get('backend_marqo_run_avg_response_body_bytes')}`",
                f"- Backend Marqo run avg decoded response body bytes: `{delta.get('backend_marqo_run_avg_decoded_response_body_bytes')}`",
                f"- Backend Marqo compression ratio: `{((server_metrics.get('backend_payload') or {}).get('marqo') or {}).get('compression_ratio')}`",
                f"- Backend {embedding_label} compression ratio{compatibility_note}: `{((server_metrics.get('backend_payload') or {}).get(embedding_metric) or {}).get('compression_ratio')}`",
                f"- Backend {embedding_label} request attempt delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_request_attempts')}`",
                f"- Backend {embedding_label} idle reconnect delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_idle_reconnects')}`",
                f"- Backend {embedding_label} stale reconnect delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_stale_reconnects')}`",
                f"- Backend {embedding_label} error response delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_error_responses')}`",
                f"- Backend {embedding_label} connection-close response delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_connection_close_responses')}`",
                f"- Backend {embedding_label} gzip response delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_gzip_responses')}`",
                f"- Backend {embedding_label} Retry-After response delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_retry_after_responses')}`",
                f"- Backend {embedding_label} circuit open/short delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_circuit_open_events')}` / `{delta.get(f'{backend_metric_prefix}_circuit_short_circuits')}`",
                f"- Backend {embedding_label} run avg elapsed ms{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_run_avg_elapsed_ms')}`",
                f"- Backend {embedding_label} run avg response body bytes{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_run_avg_response_body_bytes')}`",
                f"- Backend {embedding_label} run avg decoded response body bytes{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_run_avg_decoded_response_body_bytes')}`",
                f"- Backend Marqo elapsed ms delta: `{delta.get('backend_marqo_total_elapsed_ms')}`",
                f"- Backend {embedding_label} elapsed ms delta{compatibility_note}: `{delta.get(f'{backend_metric_prefix}_total_elapsed_ms')}`",
                f"- Rate limited event delta: `{delta.get('rate_limited_events')}`",
                f"- Rate limit fallback delta: `{delta.get('rate_limit_fallback_events')}`",
                f"- Rate limit fallback pruned bucket delta: `{delta.get('rate_limit_fallback_pruned_buckets')}`",
                f"- Cache eviction delta: `{delta.get('cache_evictions')}`",
                f"- Cache error delta: `{delta.get('cache_error_count')}`",
                f"- Cache clear error delta: `{delta.get('cache_clear_errors')}`",
                f"- Cache miss lock claim delta: `{delta.get('cache_lock_claims')}`",
                f"- Cache miss lock contention delta: `{delta.get('cache_lock_contention_events')}`",
                f"- Cache miss lock wait event delta: `{delta.get('cache_lock_wait_events')}`",
                f"- Cache miss lock wait timeout delta: `{delta.get('cache_lock_wait_timeouts')}`",
                f"- Cache miss lock total wait ms delta: `{delta.get('cache_lock_total_wait_ms')}`",
                f"- Singleflight wait event delta: `{delta.get('singleflight_wait_events')}`",
                f"- Singleflight timeout delta: `{delta.get('singleflight_wait_timeouts')}`",
                f"- Singleflight total wait ms delta: `{delta.get('singleflight_total_wait_ms')}`",
                f"- Image validation wait event delta: `{delta.get('image_validation_wait_events')}`",
                f"- Image validation timeout delta: `{delta.get('image_validation_wait_timeouts')}`",
                f"- Image validation total wait ms delta: `{delta.get('image_validation_total_wait_ms')}`",
                f"- {embedding_label} query vector wait event delta{compatibility_note}: `{delta.get(f'{query_vector_metric_prefix}_wait_events')}`",
                f"- {embedding_label} query vector wait timeout delta{compatibility_note}: `{delta.get(f'{query_vector_metric_prefix}_wait_timeouts')}`",
                f"- {embedding_label} query vector total wait ms delta{compatibility_note}: `{delta.get(f'{query_vector_metric_prefix}_total_wait_ms')}`",
                f"- Search queue acquired delta: `{delta.get('search_queue_acquired_events')}`",
                f"- Search queue full delta: `{delta.get('search_queue_full_events')}`",
                f"- Search queue wait event delta: `{delta.get('search_queue_wait_events')}`",
                f"- Search queue total wait ms delta: `{delta.get('search_queue_total_wait_ms')}`",
                f"- Image queue full delta: `{delta.get('image_queue_full_events')}`",
                f"- Image queue wait event delta: `{delta.get('image_queue_wait_events')}`",
                f"- Image queue total wait ms delta: `{delta.get('image_queue_total_wait_ms')}`",
                f"- Search log write error delta: `{delta.get('search_log_write_errors')}`",
                f"- Error log write error delta: `{delta.get('error_log_write_errors')}`",
                f"- Server metric delta coverage OK: `{server_metrics.get('delta_coverage_ok')}`",
                f"- Server metric coverage OK: `{server_metrics.get('coverage_ok')}`",
                f"- Server runtime guardrails OK: `{server_metrics.get('runtime_guardrails_ok')}`",
                f"- Server runtime guardrail problems: `{', '.join(server_metrics.get('runtime_guardrails_problems') or [])}`",
                f"- Server backend latency OK: `{server_metrics.get('backend_latency_ok')}`",
                f"- Server backend latency problems: `{', '.join(server_metrics.get('backend_latency_problems') or [])}`",
                f"- Server backend payload OK: `{server_metrics.get('backend_payload_ok')}`",
                f"- Server backend payload problems: `{', '.join(server_metrics.get('backend_payload_problems') or [])}`",
                f"- Expected search event delta: `{(server_metrics.get('expected_delta_minimums') or {}).get('search_events')}`",
                f"- Expected image search event delta: `{(server_metrics.get('expected_delta_minimums') or {}).get('image_search_events')}`",
                f"- Delta coverage missing: `{', '.join(server_metrics.get('delta_coverage_missing') or [])}`",
                f"- Run log coverage OK: `{run_log_coverage.get('ok')}`",
                f"- Run log entries scanned: `{run_log_coverage.get('entries_scanned')}`",
                f"- Run log search events: `{run_log_coverage.get('search_events')}`",
                f"- Run log image search events: `{run_log_coverage.get('image_search_events')}`",
                f"- Run log coverage missing: `{', '.join(run_log_coverage.get('missing') or [])}`",
            ]
        )
    return "\n".join(lines) + "\n"


def is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def is_success_status(value: Any) -> bool:
    try:
        status = int(value)
    except (TypeError, ValueError):
        return False
    return 200 <= status < 300


def main() -> int:
    parser = argparse.ArgumentParser(description="Simple HTTP load smoke test for Haeorum AI Search.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--mall-id", default="shop001")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--admin-key", default="", help="When set, collect /admin/metrics before and after the load run.")
    parser.add_argument("--origin", default="", help="Optional Origin header for mall allowed_origins checks.")
    parser.add_argument("--mall-config", default="", help="Optional mall config used to validate result product URL prefixes.")
    parser.add_argument(
        "--mall-sample-size",
        type=int,
        default=0,
        help=(
            "When greater than 0, cycle load requests across this many enabled mall_config entries, "
            "using each mall's API key, Origin, and product URL template."
        ),
    )
    parser.add_argument(
        "--mall-sample-strategy",
        choices=["spread", "first"],
        default="spread",
        help=(
            "How --mall-sample-size selects malls from mall_config. "
            "spread samples evenly across the enabled catalog; first keeps the old first-N behavior for debugging."
        ),
    )
    parser.add_argument(
        "--expected-product-url-prefix",
        default="",
        help="Optional explicit product URL prefix that all returned product_url values must match.",
    )
    parser.add_argument("--api-server-count", type=int, default=1, help="Number of API server instances behind the tested URL.")
    parser.add_argument(
        "--admin-metrics-base-url",
        action="append",
        dest="admin_metrics_base_urls",
        default=[],
        help=(
            "Optional per-instance API base URL for /admin/metrics and /admin/search-log collection. "
            "Pass once per API server when --api-server-count is greater than 1."
        ),
    )
    parser.add_argument(
        "--allow-private-admin-metrics-targets",
        action="store_true",
        help=(
            "Allow private/internal admin metrics URLs while keeping the public search --base-url validation. "
            "Use only from the controlled deployment host or VPC."
        ),
    )
    parser.add_argument("--scenario", choices=["single", "mixed-traffic"], default="single")
    parser.add_argument("--active-users", type=int, default=850)
    parser.add_argument("--traffic-mix", default="text=70,image=10,mixed=20")
    parser.add_argument("--mode", choices=["text", "image", "mixed"], default="text")
    parser.add_argument(
        "--unique-query-suffix",
        default="",
        help=(
            "Append a deterministic per-request suffix to text queries. Use this for admin-metrics evidence "
            "when you need to prove the Marqo/Gemini backend path instead of measuring cache-only latency."
        ),
    )
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--client-ip-count",
        type=int,
        default=0,
        help=(
            "Local/proxy load-test helper: when greater than 0, cycle X-Forwarded-For across this many "
            "synthetic client IPs. The API only honors it from configured trusted proxies."
        ),
    )
    parser.add_argument(
        "--client-ip-prefix",
        default="198.51.100",
        help="First three IPv4 octets for --client-ip-count synthetic clients.",
    )
    parser.add_argument("--image-file", default="", help="Reference image file used for image, mixed, and mixed-traffic load requests.")
    parser.add_argument(
        "--image-files",
        default="",
        help=(
            "Comma-separated additional reference image files. Image and mixed requests cycle through all "
            "provided --image-file/--image-files/--additional-image-file inputs."
        ),
    )
    parser.add_argument(
        "--additional-image-file",
        action="append",
        default=[],
        help=(
            "Additional reference image file. Repeat this option to exercise the image/Gemini path with "
            "multiple distinct inputs during load tests."
        ),
    )
    parser.add_argument(
        "--image-file-list",
        default="",
        help="Path to a UTF-8 text file containing one reference image file path per line.",
    )
    parser.add_argument("--image-max-mb", type=int, default=10, help="Maximum accepted --image-file size in MB before embedding it in JSON payloads.")
    parser.add_argument("--p95-ms", type=float, default=3000)
    parser.add_argument(
        "--p99-ms",
        type=float,
        default=0,
        help="Maximum accepted p99 latency. Defaults to 1.6x --p95-ms when omitted.",
    )
    parser.add_argument(
        "--max-server-wait-avg-ms",
        type=float,
        default=0,
        help="Maximum run average wait time for cache/singleflight/image validation/Gemini/search/image queue metrics. Defaults to max(250ms, 20%% of --p95-ms).",
    )
    parser.add_argument(
        "--min-rps",
        type=float,
        default=0,
        help="Minimum accepted aggregate search requests per second. Defaults to 25%% of concurrency divided by --p95-ms, floored at 1 RPS.",
    )
    parser.add_argument(
        "--max-process-rss-growth-mb",
        type=float,
        default=0,
        help="Maximum accepted API process RSS growth during the run. Defaults to 512 MiB.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=0,
        help="Per-search client request timeout. Defaults to max(10s, 2x --p99-ms) and is capped at max(10s, 3x --p99-ms).",
    )
    parser.add_argument("--max-error-rate", type=float, default=1.0)
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument(
        "--allow-local-target",
        action="store_true",
        help="Allow localhost/private targets for local development only. Operational evidence must omit this flag.",
    )
    args = parser.parse_args()

    try:
        validate_args(args)
    except ValueError as exc:
        report = failed_validation_report(args, exc)
        text = json.dumps(report, ensure_ascii=False, indent=2)
        print(text)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        if args.markdown_output:
            Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
        return 2

    report = run(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
