from __future__ import annotations

import argparse
import json
import math
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import (  # noqa: E402
    BOOLEAN_FALSE_VALUES,
    BOOLEAN_TRUE_VALUES,
    DEPLOYABLE_SEARCH_ENGINES,
    MAX_OPERATIONAL_SEARCH_OFFSET,
    MAX_PRODUCTION_SYNC_INTERVAL_SECONDS,
    PLACEHOLDER_ADMIN_API_KEYS,
    PRODUCTION_ENVIRONMENTS,
    PRODUCTION_SEARCH_ENGINES,
    RESERVED_SEARCH_ENGINES,
    Settings,
    SUPPORTED_SEARCH_ENGINES,
    check_sync_alert_webhook_url,
    is_placeholder_public_api_key,
    is_weak_public_api_key,
    load_mall_configs,
    load_settings,
    origin_uses_safe_public_url,
    parse_cors_origins,
    parse_trusted_proxy_ips,
    product_url_template_uses_safe_public_url,
    product_url_template_uses_https,
    read_string_list_file,
    required_api_threadpool_tokens,
    validate_marqo_url_value,
    validate_mssql_connection_string_value,
    validate_product_url_template_value,
    validate_redis_url_value,
)

try:  # noqa: E402
    from scripts.collect_operational_evidence import is_missing_config_value, load_env_file
    from scripts.env_file_security import SERVICE_ENV_FILE_MAX_MODE, check_secret_file_permissions
except ModuleNotFoundError:  # pragma: no cover - direct script execution from scripts/
    from collect_operational_evidence import is_missing_config_value, load_env_file
    from env_file_security import SERVICE_ENV_FILE_MAX_MODE, check_secret_file_permissions


DEFAULT_REQUIRED_VARIABLES = [
    "HAEORUM_ENV",
    "HAEORUM_SEARCH_ENGINE",
    "MARQO_URL",
    "HAEORUM_MARQO_MODEL",
    "HAEORUM_INDEX_NAME",
    "HAEORUM_ADMIN_API_KEY",
    "HAEORUM_MALL_CONFIG_PATH",
]
REQUIRED_MIXED_TRAFFIC_ACTIVE_USERS = 850
REQUIRED_MIXED_TRAFFIC_IMAGE_PERCENT = 30
REQUIRED_MIXED_TRAFFIC_SEARCH_REQUESTS = REQUIRED_MIXED_TRAFFIC_ACTIVE_USERS
REQUIRED_MIXED_TRAFFIC_IMAGE_REQUESTS = (
    REQUIRED_MIXED_TRAFFIC_ACTIVE_USERS * REQUIRED_MIXED_TRAFFIC_IMAGE_PERCENT + 99
) // 100
REQUIRED_TEXT_LOAD_CONCURRENCY = 100
REQUIRED_IMAGE_LOAD_CONCURRENCY = 30
REQUIRED_TEXT_LOAD_P95_MS = 3000
REQUIRED_IMAGE_LOAD_P95_MS = 5000
MARQO_POOL_ENV_NAMES = ("VESPA_POOL_SIZE", "MARQO_INFERENCE_POOL_SIZE")
MARQO_CONCURRENCY_ENV_NAMES = ("MARQO_API_WORKERS", "MARQO_MAX_CONCURRENT_SEARCH")
GEMINI_AUTH_MODES = {"auto", "api_key", "adc"}
DEFAULT_GEMINI_EMBEDDING_DIMENSIONS = 1536
DEFAULT_GEMINI_EMBEDDING_TIMEOUT_SECONDS = 30.0
DEFAULT_GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 10.0
DEFAULT_GEMINI_MAX_IMAGE_BYTES = 5 * 1024 * 1024
DEFAULT_GEMINI_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_GEMINI_PROVIDER_RETRY_COUNT = 2
DEFAULT_GEMINI_PROVIDER_RETRY_DELAY_SECONDS = 0.5
DEFAULT_GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS = 5.0
DEFAULT_GEMINI_PROXY_MAX_INPUTS_PER_REQUEST = 128
DEFAULT_GEMINI_PROXY_RATE_LIMIT_RPM = 3000
DEFAULT_GEMINI_PROXY_RATE_LIMIT_BURST = 600
DEFAULT_GEMINI_PROXY_MAX_CONCURRENT_CALLS = 50
DEFAULT_GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS = 15.0
PATH_CHECK_NAMES = {
    "HAEORUM_MALL_CONFIG_PATH": "mall_config_path",
    "HAEORUM_QUERY_SYNONYM_PATH": "query_synonym_path",
    "HAEORUM_CORS_ORIGINS_FILE": "cors_origins_file",
}
INT_MINIMUMS = {
    "HAEORUM_DEFAULT_LIMIT": 1,
    "HAEORUM_MAX_LIMIT": 1,
    "HAEORUM_MAX_OFFSET": 0,
    "HAEORUM_MAX_IMAGE_MB": 1,
    "HAEORUM_MAX_IMAGE_DIMENSION": 1,
    "HAEORUM_QUERY_IMAGE_MAX_DIMENSION": 1,
    "HAEORUM_MIN_IMAGE_DIMENSION": 1,
    "HAEORUM_IMAGE_VALIDATION_CACHE_MAX_ENTRIES": 1,
    "HAEORUM_CACHE_TTL_SECONDS": 0,
    "HAEORUM_CACHE_MAX_ENTRIES": 1,
    "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE": 0,
    "HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE": 0,
    "HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE": 0,
    "HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE": 0,
    "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE": 0,
    "HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE": 0,
    "HAEORUM_RATE_LIMIT_MAX_BUCKETS": 1,
    "HAEORUM_MARQO_SEARCH_RETRY_COUNT": 0,
    "HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS": 0,
    "HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD": 0,
    "HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS": 1,
    "HAEORUM_MARQO_ADD_DOCUMENTS_BATCH_SIZE": 1,
    "HAEORUM_MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES": 0,
    "HAEORUM_MARQO_DELETE_DOCUMENTS_BATCH_SIZE": 1,
    "HAEORUM_GEMINI_MIXED_QUERY_PARALLELISM": 0,
    "HAEORUM_GEMINI_QUERY_IMAGE_BATCH_SIZE": 1,
    "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES": 0,
    "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES": 0,
    "HAEORUM_QWEN_MIXED_QUERY_PARALLELISM": 0,
    "HAEORUM_QWEN_QUERY_IMAGE_BATCH_SIZE": 1,
    "HAEORUM_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES": 0,
    "HAEORUM_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES": 0,
    "HAEORUM_SEARCH_MAX_CONCURRENCY": 0,
    "HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY": 0,
    "HAEORUM_API_THREADPOOL_TOKENS": 1,
    "HAEORUM_SYNC_INTERVAL_SECONDS": 1,
    "HAEORUM_MSSQL_SYNC_FETCH_SIZE": 1,
    "HAEORUM_SYNC_LOCK_STALE_SECONDS": 0,
    "HAEORUM_PRODUCT_IMAGE_PROBE_TIMEOUT_SECONDS": 1,
    "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT": 0,
    "HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT": 1,
    "HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS": 1,
    "VESPA_POOL_SIZE": 1,
    "VESPA_SEARCH_TIMEOUT_MS": 1,
    "MARQO_INFERENCE_POOL_SIZE": 1,
    "MARQO_API_WORKERS": 1,
    "MARQO_API_KEEPALIVE_TIMEOUT": 1,
    "MARQO_API_GZIP_MINIMUM_SIZE": 0,
    "MARQO_MAX_CONCURRENT_SEARCH": 1,
    "GEMINI_EMBEDDING_DIMENSIONS": 1,
    "GEMINI_MAX_IMAGE_BYTES": 1,
    "GEMINI_MAX_RESPONSE_BYTES": 1,
    "GEMINI_PROVIDER_RETRY_COUNT": 0,
    "GEMINI_PROXY_MAX_INPUTS_PER_REQUEST": 1,
    "GEMINI_PROXY_RATE_LIMIT_RPM": 1,
    "GEMINI_PROXY_RATE_LIMIT_BURST": 1,
    "GEMINI_PROXY_MAX_CONCURRENT_CALLS": 1,
}
INT_MAXIMUMS = {
    "HAEORUM_MAX_OFFSET": MAX_OPERATIONAL_SEARCH_OFFSET,
}
FLOAT_MINIMUMS = {
    "HAEORUM_MIXED_TEXT_WEIGHT": 0.0,
    "HAEORUM_MIXED_IMAGE_WEIGHT": 0.0,
    "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS": 0.001,
    "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS": 0.0,
    "HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS": 0.0,
    "HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS": 0.0,
    "HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS": 0.0,
    "HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS": 0.0,
    "HAEORUM_ADMIN_METRICS_HEALTH_CACHE_SECONDS": 0.0,
    "HAEORUM_IMAGE_VALIDATION_CACHE_TTL_SECONDS": 0.0,
    "HAEORUM_GEMINI_QUERY_IMAGE_BATCH_WAIT_MS": 0.0,
    "HAEORUM_QWEN_QUERY_IMAGE_BATCH_WAIT_MS": 0.0,
    "HAEORUM_RATE_LIMIT_PRUNE_INTERVAL_SECONDS": 0.0,
    "HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS": 0.001,
    "HAEORUM_QWEN_QUERY_TIMEOUT_SECONDS": 0.001,
    "HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS": 0.0,
    "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS": 0.0,
    "HAEORUM_CACHE_MISS_LOCK_SECONDS": 0.0,
    "HAEORUM_CACHE_MISS_WAIT_SECONDS": 0.0,
    "HAEORUM_CACHE_MISS_POLL_SECONDS": 0.001,
    "HAEORUM_REDIS_SOCKET_TIMEOUT_SECONDS": 0.001,
    "HAEORUM_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS": 0.001,
    "HAEORUM_REDIS_FAILURE_BACKOFF_SECONDS": 0.0,
    "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_DELAY_SECONDS": 0.0,
    "GEMINI_EMBEDDING_TIMEOUT_SECONDS": 0.001,
    "GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS": 0.001,
    "GEMINI_PROVIDER_RETRY_DELAY_SECONDS": 0.0,
    "GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS": 0.001,
    "GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS": 0.001,
}
DEFAULT_NUMERIC_VALUES: dict[str, int | float] = {
    "HAEORUM_DEFAULT_LIMIT": 20,
    "HAEORUM_MAX_LIMIT": 50,
    "HAEORUM_MAX_OFFSET": Settings.max_offset,
    "HAEORUM_MAX_IMAGE_DIMENSION": 1600,
    "HAEORUM_QUERY_IMAGE_MAX_DIMENSION": Settings.query_image_max_dimension,
    "HAEORUM_MIN_IMAGE_DIMENSION": 16,
    "HAEORUM_IMAGE_VALIDATION_CACHE_TTL_SECONDS": Settings.image_validation_cache_ttl_seconds,
    "HAEORUM_IMAGE_VALIDATION_CACHE_MAX_ENTRIES": Settings.image_validation_cache_max_entries,
    "HAEORUM_MIXED_TEXT_WEIGHT": 0.4,
    "HAEORUM_MIXED_IMAGE_WEIGHT": 0.6,
    "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS": Settings.marqo_search_timeout_seconds,
    "HAEORUM_MARQO_SEARCH_RETRY_COUNT": Settings.marqo_search_retry_count,
    "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS": Settings.marqo_search_retry_delay_seconds,
    "HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS": Settings.backend_retry_after_max_seconds,
    "HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS": Settings.backend_http_max_idle_seconds,
    "HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS": Settings.backend_http_max_active_requests,
    "HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS": (
        Settings.backend_http_connection_acquire_timeout_seconds
    ),
    "HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD": Settings.backend_circuit_failure_threshold,
    "HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS": Settings.backend_circuit_cooldown_seconds,
    "HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS": Settings.backend_circuit_half_open_max_calls,
    "HAEORUM_ADMIN_METRICS_HEALTH_CACHE_SECONDS": Settings.admin_metrics_health_cache_seconds,
    "HAEORUM_MARQO_ADD_DOCUMENTS_BATCH_SIZE": Settings.marqo_add_documents_batch_size,
    "HAEORUM_MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES": Settings.marqo_add_documents_max_request_bytes,
    "HAEORUM_MARQO_DELETE_DOCUMENTS_BATCH_SIZE": Settings.marqo_delete_documents_batch_size,
    "HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS": Settings.qwen_query_timeout_seconds,
    "HAEORUM_GEMINI_MIXED_QUERY_PARALLELISM": Settings.qwen_mixed_query_parallelism,
    "HAEORUM_GEMINI_QUERY_IMAGE_BATCH_SIZE": Settings.qwen_query_image_batch_size,
    "HAEORUM_GEMINI_QUERY_IMAGE_BATCH_WAIT_MS": Settings.qwen_query_image_batch_wait_ms,
    "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES": Settings.qwen_query_runtime_text_cache_entries,
    "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES": Settings.qwen_query_runtime_image_cache_entries,
    "HAEORUM_QWEN_QUERY_TIMEOUT_SECONDS": Settings.qwen_query_timeout_seconds,
    "HAEORUM_QWEN_MIXED_QUERY_PARALLELISM": Settings.qwen_mixed_query_parallelism,
    "HAEORUM_QWEN_QUERY_IMAGE_BATCH_SIZE": Settings.qwen_query_image_batch_size,
    "HAEORUM_QWEN_QUERY_IMAGE_BATCH_WAIT_MS": Settings.qwen_query_image_batch_wait_ms,
    "HAEORUM_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES": Settings.qwen_query_runtime_text_cache_entries,
    "HAEORUM_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES": Settings.qwen_query_runtime_image_cache_entries,
    "HAEORUM_CACHE_MAX_ENTRIES": Settings.cache_max_entries,
    "HAEORUM_RATE_LIMIT_MAX_BUCKETS": Settings.rate_limit_max_buckets,
    "HAEORUM_RATE_LIMIT_PRUNE_INTERVAL_SECONDS": Settings.rate_limit_prune_interval_seconds,
    "HAEORUM_SEARCH_MAX_CONCURRENCY": Settings.search_max_concurrency,
    "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS": Settings.search_queue_timeout_seconds,
    "HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY": Settings.image_search_max_concurrency,
    "HAEORUM_API_THREADPOOL_TOKENS": Settings.api_threadpool_tokens,
    "HAEORUM_CACHE_MISS_LOCK_SECONDS": Settings.cache_miss_lock_seconds,
    "HAEORUM_CACHE_MISS_WAIT_SECONDS": Settings.cache_miss_wait_seconds,
    "HAEORUM_CACHE_MISS_POLL_SECONDS": Settings.cache_miss_poll_seconds,
    "HAEORUM_REDIS_SOCKET_TIMEOUT_SECONDS": Settings.redis_socket_timeout_seconds,
    "HAEORUM_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS": Settings.redis_socket_connect_timeout_seconds,
    "HAEORUM_REDIS_FAILURE_BACKOFF_SECONDS": Settings.redis_failure_backoff_seconds,
    "HAEORUM_SYNC_INTERVAL_SECONDS": MAX_PRODUCTION_SYNC_INTERVAL_SECONDS,
    "HAEORUM_MSSQL_SYNC_FETCH_SIZE": Settings.mssql_sync_fetch_size,
    "VESPA_POOL_SIZE": Settings.search_max_concurrency * 2,
    "VESPA_SEARCH_TIMEOUT_MS": REQUIRED_IMAGE_LOAD_P95_MS,
    "MARQO_INFERENCE_POOL_SIZE": Settings.search_max_concurrency * 2,
    "MARQO_API_WORKERS": 2,
    "MARQO_API_KEEPALIVE_TIMEOUT": 75,
    "MARQO_API_GZIP_MINIMUM_SIZE": 1024,
    "MARQO_MAX_CONCURRENT_SEARCH": REQUIRED_TEXT_LOAD_CONCURRENCY,
    "GEMINI_EMBEDDING_DIMENSIONS": DEFAULT_GEMINI_EMBEDDING_DIMENSIONS,
    "GEMINI_EMBEDDING_TIMEOUT_SECONDS": DEFAULT_GEMINI_EMBEDDING_TIMEOUT_SECONDS,
    "GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS": DEFAULT_GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS,
    "GEMINI_MAX_IMAGE_BYTES": DEFAULT_GEMINI_MAX_IMAGE_BYTES,
    "GEMINI_MAX_RESPONSE_BYTES": DEFAULT_GEMINI_MAX_RESPONSE_BYTES,
    "GEMINI_PROVIDER_RETRY_COUNT": DEFAULT_GEMINI_PROVIDER_RETRY_COUNT,
    "GEMINI_PROVIDER_RETRY_DELAY_SECONDS": DEFAULT_GEMINI_PROVIDER_RETRY_DELAY_SECONDS,
    "GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS": DEFAULT_GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS,
    "GEMINI_PROXY_MAX_INPUTS_PER_REQUEST": DEFAULT_GEMINI_PROXY_MAX_INPUTS_PER_REQUEST,
    "GEMINI_PROXY_RATE_LIMIT_RPM": DEFAULT_GEMINI_PROXY_RATE_LIMIT_RPM,
    "GEMINI_PROXY_RATE_LIMIT_BURST": DEFAULT_GEMINI_PROXY_RATE_LIMIT_BURST,
    "GEMINI_PROXY_MAX_CONCURRENT_CALLS": DEFAULT_GEMINI_PROXY_MAX_CONCURRENT_CALLS,
    "GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS": DEFAULT_GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS,
}
BOOLEAN_SETTING_NAMES = (
    "HAEORUM_FILTER_BY_MALL_ID",
    "HAEORUM_MSSQL_ALLOW_TRUST_SERVER_CERTIFICATE",
    "HAEORUM_VALIDATE_PRODUCT_IMAGES",
)


def env_value(values: Mapping[str, str], name: str) -> str:
    return str(values.get(name, "") or "").strip()


def embedding_provider(values: Mapping[str, str]) -> str:
    return (env_value(values, "HAEORUM_EMBEDDING_BACKEND") or Settings.embedding_backend).strip().lower()


def provider_label(provider: str) -> str:
    return "Gemini" if provider == "gemini" else "Qwen" if provider == "qwen" else str(provider or "embedding")


def embedding_env_names(provider: str) -> dict[str, tuple[str, ...]]:
    if provider == "gemini":
        return {
            "url": ("HAEORUM_GEMINI_EMBEDDING_URL", "HAEORUM_QWEN_EMBEDDING_URL"),
            "model": ("HAEORUM_GEMINI_MODEL", "HAEORUM_QWEN_MODEL"),
            "dimensions": ("HAEORUM_GEMINI_EMBEDDING_DIMENSIONS", "HAEORUM_QWEN_EMBEDDING_DIMENSIONS"),
            "timeout": ("HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS", "HAEORUM_QWEN_QUERY_TIMEOUT_SECONDS"),
            "parallelism": ("HAEORUM_GEMINI_MIXED_QUERY_PARALLELISM", "HAEORUM_QWEN_MIXED_QUERY_PARALLELISM"),
            "text_cache": (
                "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
                "HAEORUM_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
            ),
            "image_cache": (
                "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
                "HAEORUM_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
            ),
        }
    return {
        "url": ("HAEORUM_QWEN_EMBEDDING_URL",),
        "model": ("HAEORUM_QWEN_MODEL",),
        "dimensions": ("HAEORUM_QWEN_EMBEDDING_DIMENSIONS",),
        "timeout": ("HAEORUM_QWEN_QUERY_TIMEOUT_SECONDS",),
        "parallelism": ("HAEORUM_QWEN_MIXED_QUERY_PARALLELISM",),
        "text_cache": ("HAEORUM_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",),
        "image_cache": ("HAEORUM_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",),
    }


def first_configured_value(values: Mapping[str, str], names: tuple[str, ...]) -> tuple[str, str]:
    for name in names:
        raw = env_value(values, name)
        if raw:
            return name, raw
    return names[0], ""


def parse_int_setting_alias(values: Mapping[str, str], names: tuple[str, ...]) -> tuple[str, int | None]:
    selected, raw = first_configured_value(values, names)
    if raw == "":
        default = DEFAULT_NUMERIC_VALUES.get(selected)
        return selected, int(default) if default is not None else None
    return selected, int(raw)


def check(name: str, ok: bool, message: str, **details: Any) -> dict[str, Any]:
    item = {"name": name, "ok": bool(ok), "message": message}
    item.update({key: value for key, value in details.items() if value is not None})
    return item


def read_env_values(env_file: str | Path | None) -> dict[str, str]:
    if env_file:
        return load_env_file(env_file)
    return {name: value for name, value in os.environ.items()}


def missing_variable_names(values: Mapping[str, str], names: list[str]) -> list[str]:
    return [name for name in names if is_missing_config_value(env_value(values, name))]


def parse_int_setting(values: Mapping[str, str], name: str) -> int | None:
    raw = env_value(values, name)
    if raw == "":
        default = DEFAULT_NUMERIC_VALUES.get(name)
        return int(default) if default is not None else None
    return int(raw)


def parse_float_setting(values: Mapping[str, str], name: str) -> float | None:
    raw = env_value(values, name)
    if raw == "":
        default = DEFAULT_NUMERIC_VALUES.get(name)
        return float(default) if default is not None else None
    return float(raw)


def numeric_check(values: Mapping[str, str]) -> dict[str, Any]:
    problems: list[str] = []
    parsed_ints: dict[str, int] = {}
    parsed_floats: dict[str, float] = {}
    for name, minimum in INT_MINIMUMS.items():
        raw = env_value(values, name)
        if raw == "" and name not in DEFAULT_NUMERIC_VALUES:
            continue
        try:
            parsed = parse_int_setting(values, name)
        except ValueError:
            problems.append(f"{name} must be an integer")
            continue
        if parsed is None:
            continue
        parsed_ints[name] = parsed
        if parsed < minimum:
            problems.append(f"{name} must be at least {minimum}")
        maximum = INT_MAXIMUMS.get(name)
        if maximum is not None and parsed > maximum:
            problems.append(f"{name} must be at most {maximum}")

    for name, minimum in FLOAT_MINIMUMS.items():
        raw = env_value(values, name)
        if raw == "" and name not in DEFAULT_NUMERIC_VALUES:
            continue
        try:
            parsed = parse_float_setting(values, name)
        except ValueError:
            problems.append(f"{name} must be a number")
            continue
        if parsed is None:
            continue
        parsed_floats[name] = parsed
        if parsed < minimum:
            problems.append(f"{name} must be at least {minimum:g}")

    try:
        category_limit = parse_int_setting(values, "HAEORUM_CATEGORY_SUGGESTION_LIMIT")
    except ValueError:
        problems.append("HAEORUM_CATEGORY_SUGGESTION_LIMIT must be an integer")
        category_limit = None
    if category_limit is not None and not 1 <= category_limit <= 15:
        problems.append("HAEORUM_CATEGORY_SUGGESTION_LIMIT must be between 1 and 15")

    low_score_raw = env_value(values, "HAEORUM_LOW_SCORE_THRESHOLD")
    if low_score_raw:
        try:
            low_score = float(low_score_raw)
        except ValueError:
            problems.append("HAEORUM_LOW_SCORE_THRESHOLD must be a number")
        else:
            if not 0.0 <= low_score <= 1.0:
                problems.append("HAEORUM_LOW_SCORE_THRESHOLD must be between 0 and 1")

    default_limit = parsed_ints.get("HAEORUM_DEFAULT_LIMIT", int(DEFAULT_NUMERIC_VALUES["HAEORUM_DEFAULT_LIMIT"]))
    max_limit = parsed_ints.get("HAEORUM_MAX_LIMIT", int(DEFAULT_NUMERIC_VALUES["HAEORUM_MAX_LIMIT"]))
    if max_limit < default_limit:
        problems.append("HAEORUM_MAX_LIMIT must be at least HAEORUM_DEFAULT_LIMIT")

    max_dimension = parsed_ints.get(
        "HAEORUM_MAX_IMAGE_DIMENSION",
        int(DEFAULT_NUMERIC_VALUES["HAEORUM_MAX_IMAGE_DIMENSION"]),
    )
    min_dimension = parsed_ints.get(
        "HAEORUM_MIN_IMAGE_DIMENSION",
        int(DEFAULT_NUMERIC_VALUES["HAEORUM_MIN_IMAGE_DIMENSION"]),
    )
    if max_dimension < min_dimension:
        problems.append("HAEORUM_MAX_IMAGE_DIMENSION must be at least HAEORUM_MIN_IMAGE_DIMENSION")

    text_weight = parsed_floats.get(
        "HAEORUM_MIXED_TEXT_WEIGHT",
        float(DEFAULT_NUMERIC_VALUES["HAEORUM_MIXED_TEXT_WEIGHT"]),
    )
    image_weight = parsed_floats.get(
        "HAEORUM_MIXED_IMAGE_WEIGHT",
        float(DEFAULT_NUMERIC_VALUES["HAEORUM_MIXED_IMAGE_WEIGHT"]),
    )
    if text_weight + image_weight <= 0:
        problems.append("HAEORUM_MIXED_TEXT_WEIGHT and HAEORUM_MIXED_IMAGE_WEIGHT must not both be zero")

    return check(
        "numeric_settings",
        not problems,
        "numeric settings are valid" if not problems else "numeric settings contain invalid values",
        problems=problems,
    )


def boolean_settings_check(values: Mapping[str, str]) -> dict[str, Any]:
    problems: list[str] = []
    parsed: dict[str, bool] = {}
    configured: list[str] = []
    for name in BOOLEAN_SETTING_NAMES:
        raw = env_value(values, name)
        if not raw:
            continue
        configured.append(name)
        normalized = raw.lower()
        if is_missing_config_value(raw):
            problems.append(f"{name} must be true or false, not a placeholder")
            continue
        if normalized in BOOLEAN_TRUE_VALUES:
            parsed[name] = True
            continue
        if normalized in BOOLEAN_FALSE_VALUES:
            parsed[name] = False
            continue
        problems.append(f"{name} must be a boolean")
    return check(
        "boolean_settings",
        not problems,
        "boolean settings are valid" if not problems else "boolean settings contain invalid values",
        configured=configured,
        parsed=parsed,
        problems=problems,
    )


def path_check(
    values: Mapping[str, str],
    name: str,
    required: bool,
    require_paths: bool,
) -> dict[str, Any]:
    check_name = PATH_CHECK_NAMES.get(name, name.lower().replace("haeorum_", ""))
    raw = env_value(values, name)
    missing = is_missing_config_value(raw)
    if missing:
        return check(
            check_name,
            not required,
            f"{name} is required" if required else f"{name} is not configured",
            path=None,
        )
    path = Path(raw)
    exists = path.exists()
    ok = exists if require_paths else True
    message = f"{name} exists" if exists else f"{name} does not exist"
    return check(
        check_name,
        ok,
        message if require_paths else f"{name} configured",
        path=str(path),
        exists=exists,
        required=required,
    )


def data_source_check(values: Mapping[str, str], require_paths: bool) -> dict[str, Any]:
    readonly_mssql = env_value(values, "HAEORUM_MSSQL_READONLY_CONNECTION_STRING")
    legacy_mssql = env_value(values, "HAEORUM_MSSQL_CONNECTION_STRING")
    mssql = readonly_mssql or legacy_mssql
    product_csv = env_value(values, "HAEORUM_PRODUCT_CSV")
    mssql_configured = not is_missing_config_value(mssql)
    mssql_env_var = "HAEORUM_MSSQL_READONLY_CONNECTION_STRING" if not is_missing_config_value(readonly_mssql) else "HAEORUM_MSSQL_CONNECTION_STRING"
    csv_configured = not is_missing_config_value(product_csv)
    csv_exists = Path(product_csv).exists() if csv_configured else False
    csv_ok = csv_configured and (csv_exists or not require_paths)
    ok = mssql_configured or csv_ok
    problems = []
    if not mssql_configured and not csv_configured:
        problems.append("HAEORUM_MSSQL_READONLY_CONNECTION_STRING, HAEORUM_MSSQL_CONNECTION_STRING, or HAEORUM_PRODUCT_CSV is required")
    if csv_configured and require_paths and not csv_exists:
        problems.append("HAEORUM_PRODUCT_CSV does not exist")
    return check(
        "data_source",
        ok,
        "MSSQL or CSV source is configured" if ok else "product source is not ready",
        mssql_configured=mssql_configured,
        mssql_env_var=mssql_env_var if mssql_configured else None,
        csv_configured=csv_configured,
        csv_exists=csv_exists if csv_configured else None,
        problems=problems,
    )


def mssql_connection_string_check(values: Mapping[str, str], require_production: bool) -> dict[str, Any]:
    readonly_mssql = env_value(values, "HAEORUM_MSSQL_READONLY_CONNECTION_STRING")
    legacy_mssql = env_value(values, "HAEORUM_MSSQL_CONNECTION_STRING")
    allow_trust_server_certificate = (
        env_value(values, "HAEORUM_MSSQL_ALLOW_TRUST_SERVER_CERTIFICATE").lower() in BOOLEAN_TRUE_VALUES
    )
    selected_name = ""
    selected_value = ""
    if not is_missing_config_value(readonly_mssql):
        selected_name = "HAEORUM_MSSQL_READONLY_CONNECTION_STRING"
        selected_value = readonly_mssql
    elif not is_missing_config_value(legacy_mssql):
        selected_name = "HAEORUM_MSSQL_CONNECTION_STRING"
        selected_value = legacy_mssql
    elif readonly_mssql or legacy_mssql:
        selected_name = "HAEORUM_MSSQL_READONLY_CONNECTION_STRING" if readonly_mssql else "HAEORUM_MSSQL_CONNECTION_STRING"
        selected_value = readonly_mssql or legacy_mssql

    if not selected_value:
        return check(
            "mssql_connection_string",
            True,
            "MSSQL connection string is not configured",
            configured=False,
            required=require_production,
        )
    if is_missing_config_value(selected_value):
        return check(
            "mssql_connection_string",
            False,
            f"{selected_name} must not be a placeholder",
            configured=True,
            env_var=selected_name,
            problems=[f"{selected_name} must not be a placeholder"],
        )
    try:
        parsed = validate_mssql_connection_string_value(
            selected_value,
            selected_name,
            allow_trust_server_certificate=allow_trust_server_certificate,
        )
    except ValueError as exc:
        return check(
            "mssql_connection_string",
            False,
            str(exc),
            configured=True,
            env_var=selected_name,
            problems=[str(exc)],
        )
    trust_server_certificate = parsed.get("trustservercertificate")
    temporary_exception = (
        allow_trust_server_certificate
        and str(trust_server_certificate or "").strip().lower() not in {"no", "false", "0"}
    )
    return check(
        "mssql_connection_string",
        True,
        "MSSQL connection string uses temporary TrustServerCertificate exception"
        if temporary_exception
        else "MSSQL connection string is hardened for read-only TLS access",
        configured=True,
        env_var=selected_name,
        has_server=any(name in parsed for name in ["server", "address", "addr", "networkaddress", "datasource"]),
        has_database=any(name in parsed for name in ["database", "initialcatalog"]),
        encrypt=parsed.get("encrypt"),
        trust_server_certificate=trust_server_certificate,
        application_intent=parsed.get("applicationintent"),
        allow_trust_server_certificate=allow_trust_server_certificate,
        temporary_exception=temporary_exception,
    )


def effective_cors_origin_value(values: Mapping[str, str], require_paths: bool) -> tuple[str, dict[str, Any] | None]:
    direct = env_value(values, "HAEORUM_CORS_ORIGINS")
    if direct:
        return direct, None
    raw_path = env_value(values, "HAEORUM_CORS_ORIGINS_FILE")
    if is_missing_config_value(raw_path):
        return "", None
    path = Path(raw_path)
    if require_paths and not path.exists():
        return "", check(
            "cors_origins",
            False,
            "HAEORUM_CORS_ORIGINS_FILE does not exist",
            path=str(path),
            source="file",
        )
    if not path.exists():
        return "", check(
            "cors_origins",
            True,
            "CORS origin file path is configured but was not inspected because path checks are skipped",
            path=str(path),
            source="file",
            inspected=False,
        )
    try:
        return read_string_list_file(path, "HAEORUM_CORS_ORIGINS_FILE"), None
    except Exception as exc:
        return "", check(
            "cors_origins",
            False,
            str(exc),
            path=str(path),
            source="file",
        )


def cors_check(values: Mapping[str, str], require_production: bool, require_paths: bool) -> dict[str, Any]:
    raw, early = effective_cors_origin_value(values, require_paths=require_paths)
    if early is not None:
        return early
    if is_missing_config_value(raw):
        return check("cors_origins", False, "HAEORUM_CORS_ORIGINS or HAEORUM_CORS_ORIGINS_FILE is required")
    try:
        origins = parse_cors_origins(raw)
    except ValueError as exc:
        return check("cors_origins", False, str(exc))
    non_https = [origin for origin in origins if not origin.startswith("https://")]
    unsafe_public = [origin for origin in origins if origin != "*" and not origin_uses_safe_public_url(origin)]
    has_wildcard = "*" in origins
    ok = bool(origins) and not has_wildcard and (not require_production or (not non_https and not unsafe_public))
    problems = []
    if has_wildcard:
        problems.append("wildcard CORS is not allowed")
    if require_production and non_https:
        problems.append("production CORS origins must use https")
    if require_production and unsafe_public:
        problems.append("production CORS origins must use safe public origins")
    return check(
        "cors_origins",
        ok,
        "CORS origins are restricted" if ok else "CORS origins are not production safe",
        source="env" if env_value(values, "HAEORUM_CORS_ORIGINS") else "file",
        origin_count=len(origins),
        non_https_count=len(non_https),
        unsafe_public_origins=unsafe_public,
        has_wildcard=has_wildcard,
        problems=problems,
    )


def admin_key_check(values: Mapping[str, str]) -> dict[str, Any]:
    admin_key = env_value(values, "HAEORUM_ADMIN_API_KEY")
    ok = (
        not is_missing_config_value(admin_key)
        and admin_key.lower() not in PLACEHOLDER_ADMIN_API_KEYS
        and len(admin_key) >= 16
    )
    return check(
        "admin_key",
        ok,
        "admin key is production strength" if ok else "HAEORUM_ADMIN_API_KEY must be changed to a strong value",
        minimum_length=16,
    )


def product_url_template_check(values: Mapping[str, str], require_production: bool) -> dict[str, Any]:
    template = env_value(values, "HAEORUM_PRODUCT_URL_TEMPLATE") or Settings.product_url_template
    try:
        validate_product_url_template_value(
            template,
            mall_id="www",
            field_name="HAEORUM_PRODUCT_URL_TEMPLATE",
        )
    except ValueError as exc:
        return check(
            "product_url_template",
            False,
            str(exc),
            configured=not is_missing_config_value(env_value(values, "HAEORUM_PRODUCT_URL_TEMPLATE")),
            https=False,
            safe_public=False,
        )
    safe_public = product_url_template_uses_safe_public_url(template, mall_id="www")
    https = product_url_template_uses_https(template, mall_id="www")
    ok = safe_public and (https if require_production else True)
    if ok:
        message = "product URL template is production safe"
    elif not safe_public:
        message = "HAEORUM_PRODUCT_URL_TEMPLATE must format to a safe public http(s) URL"
    else:
        message = "HAEORUM_PRODUCT_URL_TEMPLATE must use https in production"
    return check(
        "product_url_template",
        ok,
        message,
        configured=not is_missing_config_value(env_value(values, "HAEORUM_PRODUCT_URL_TEMPLATE")),
        https=https,
        safe_public=safe_public,
        required_https=require_production,
    )


def mall_security_check(values: Mapping[str, str], require_production: bool, require_paths: bool) -> dict[str, Any]:
    raw_path = env_value(values, "HAEORUM_MALL_CONFIG_PATH")
    if is_missing_config_value(raw_path):
        return check("mall_security", False, "HAEORUM_MALL_CONFIG_PATH is required", enabled_count=0)
    path = Path(raw_path)
    if require_paths and not path.exists():
        return check(
            "mall_security",
            False,
            "HAEORUM_MALL_CONFIG_PATH does not exist",
            path=str(path),
            enabled_count=0,
        )
    if not path.exists():
        return check(
            "mall_security",
            True,
            "mall config path is configured but was not inspected because path checks are skipped",
            path=str(path),
            inspected=False,
        )
    try:
        malls = load_mall_configs(path)
    except Exception as exc:
        return check("mall_security", False, str(exc), path=str(path), enabled_count=0)

    enabled_malls = [mall for mall in malls.values() if mall.enabled]
    try:
        cors_raw, _ = effective_cors_origin_value(values, require_paths=require_paths)
        cors_origins = set(parse_cors_origins(cors_raw))
    except ValueError:
        cors_origins = set()

    missing_api_key = sorted(mall.mall_id for mall in enabled_malls if not mall.api_key)
    placeholder_api_key = sorted(
        mall.mall_id for mall in enabled_malls if mall.api_key and is_placeholder_public_api_key(mall.api_key)
    )
    weak_api_key = sorted(
        mall.mall_id
        for mall in enabled_malls
        if mall.api_key and not is_placeholder_public_api_key(mall.api_key) and is_weak_public_api_key(mall.api_key)
    )
    missing_allowed_origins = sorted(mall.mall_id for mall in enabled_malls if not mall.allowed_origins)
    wildcard_allowed_origins = sorted(mall.mall_id for mall in enabled_malls if "*" in mall.allowed_origins)
    non_https_allowed_origins = {
        mall.mall_id: [origin for origin in mall.allowed_origins if not str(origin).lower().startswith("https://")]
        for mall in enabled_malls
    }
    non_https_allowed_origins = {
        mall_id: origins for mall_id, origins in non_https_allowed_origins.items() if origins
    }
    unsafe_allowed_origins = {
        mall.mall_id: [origin for origin in mall.allowed_origins if not origin_uses_safe_public_url(origin)]
        for mall in enabled_malls
    }
    unsafe_allowed_origins = {mall_id: origins for mall_id, origins in unsafe_allowed_origins.items() if origins}
    missing_product_url_template = sorted(mall.mall_id for mall in enabled_malls if not mall.product_url_template)
    unsafe_product_url_template = sorted(
        mall.mall_id
        for mall in enabled_malls
        if not product_url_template_uses_safe_public_url(mall.product_url_template, mall_id=mall.mall_id)
    )
    non_https_product_url_template = sorted(
        mall.mall_id
        for mall in enabled_malls
        if not product_url_template_uses_https(mall.product_url_template, mall_id=mall.mall_id)
    )
    origins_missing_from_cors = {
        mall.mall_id: sorted(
            origin for origin in mall.allowed_origins if origin != "*" and origin not in cors_origins
        )
        for mall in enabled_malls
    }
    origins_missing_from_cors = {
        mall_id: origins for mall_id, origins in origins_missing_from_cors.items() if origins
    }

    problems: list[str] = []
    if not enabled_malls:
        problems.append("enabled mall config is required")
    if missing_api_key:
        problems.append("enabled malls require api_key")
    if placeholder_api_key:
        problems.append("enabled mall api_key must be changed from sample or placeholder values")
    if require_production and weak_api_key:
        problems.append("enabled mall api_key must be strong random values in production")
    if missing_allowed_origins:
        problems.append("enabled malls require allowed_origins")
    if wildcard_allowed_origins:
        problems.append("enabled mall allowed_origins must not contain wildcard")
    if require_production and non_https_allowed_origins:
        problems.append("enabled mall allowed_origins must use https in production")
    if require_production and unsafe_allowed_origins:
        problems.append("enabled mall allowed_origins must use safe public origins in production")
    if missing_product_url_template:
        problems.append("enabled malls require product_url_template")
    if unsafe_product_url_template:
        problems.append("enabled mall product_url_template must use safe public URLs")
    if require_production and non_https_product_url_template:
        problems.append("enabled mall product_url_template must use https in production")
    if require_production and origins_missing_from_cors:
        problems.append("enabled mall allowed_origins must be included in HAEORUM_CORS_ORIGINS")

    return check(
        "mall_security",
        not problems,
        "mall config is production safe" if not problems else "mall config has production blockers",
        path=str(path),
        inspected=True,
        total_count=len(malls),
        enabled_count=len(enabled_malls),
        missing_api_key=missing_api_key,
        placeholder_api_key=placeholder_api_key,
        weak_api_key=weak_api_key,
        missing_allowed_origins=missing_allowed_origins,
        wildcard_allowed_origins=wildcard_allowed_origins,
        non_https_allowed_origins=non_https_allowed_origins,
        unsafe_allowed_origins=unsafe_allowed_origins,
        missing_product_url_template=missing_product_url_template,
        unsafe_product_url_template=unsafe_product_url_template,
        non_https_product_url_template=non_https_product_url_template,
        origins_missing_from_cors=origins_missing_from_cors,
        problems=problems,
    )


def production_env_check(values: Mapping[str, str], require_production: bool) -> dict[str, Any]:
    environment = env_value(values, "HAEORUM_ENV").lower()
    ok = environment in PRODUCTION_ENVIRONMENTS if require_production else bool(environment)
    return check(
        "production_env",
        ok,
        "environment is production" if ok else "HAEORUM_ENV must be production",
        environment=environment or None,
        required=require_production,
    )


def search_engine_check(values: Mapping[str, str], require_production: bool) -> dict[str, Any]:
    engine = env_value(values, "HAEORUM_SEARCH_ENGINE").lower()
    allowed = PRODUCTION_SEARCH_ENGINES if require_production else DEPLOYABLE_SEARCH_ENGINES
    reserved = engine in RESERVED_SEARCH_ENGINES
    supported = engine in SUPPORTED_SEARCH_ENGINES
    ok = engine in allowed
    if ok:
        message = "search engine is deployable"
    elif reserved:
        message = f"{engine} is a reserved adapter and is not deployable yet"
    elif supported:
        message = "HAEORUM_SEARCH_ENGINE is not valid for this environment"
    else:
        message = "HAEORUM_SEARCH_ENGINE is not supported"
    return check(
        "search_engine",
        ok,
        message,
        engine=engine or None,
        allowed=sorted(allowed),
        supported=sorted(SUPPORTED_SEARCH_ENGINES),
        reserved_adapter=reserved,
        deployable=ok,
    )


def marqo_url_check(values: Mapping[str, str]) -> dict[str, Any]:
    raw = env_value(values, "MARQO_URL")
    try:
        url = validate_marqo_url_value(raw)
    except ValueError as exc:
        return check("marqo_url", False, str(exc), configured=not is_missing_config_value(raw))
    return check(
        "marqo_url",
        True,
        "MARQO_URL is a valid internal Marqo endpoint",
        configured=True,
        url=url,
    )


def embedding_provider_config_check(values: Mapping[str, str], require_production: bool) -> dict[str, Any]:
    backend_raw = env_value(values, "HAEORUM_EMBEDDING_BACKEND")
    backend = backend_raw.lower()
    provider = backend if backend in {"gemini", "qwen"} else ""
    names = embedding_env_names(provider)
    url_name, embedding_url_raw = first_configured_value(values, names["url"])
    model_name, embedding_model = first_configured_value(values, names["model"])
    dimensions_name, dimensions_raw = first_configured_value(values, names["dimensions"])
    required = require_production or provider in {"gemini", "qwen"}
    check_name = f"{provider}_embedding_config" if provider else "embedding_provider_config"
    label = provider_label(provider)
    if not required:
        return check(
            check_name,
            True,
            "External embedding provider config is not required",
            required=False,
            embedding_backend=backend or None,
            embedding_url=embedding_url_raw or None,
            embedding_model=embedding_model or None,
        )

    problems: list[str] = []
    errors: list[str] = []
    embedding_url: str | None = None
    embedding_dimensions: int | None = None
    if is_missing_config_value(backend_raw) or provider not in {"gemini", "qwen"}:
        problems.append("HAEORUM_EMBEDDING_BACKEND")
    if is_missing_config_value(embedding_url_raw):
        problems.append(url_name)
    else:
        try:
            embedding_url = validate_marqo_url_value(embedding_url_raw, url_name)
        except ValueError as exc:
            problems.append(url_name)
            errors.append(str(exc))
    if is_missing_config_value(embedding_model):
        problems.append(model_name)
    if is_missing_config_value(dimensions_raw):
        problems.append(dimensions_name)
    else:
        try:
            embedding_dimensions = int(dimensions_raw)
        except ValueError:
            problems.append(dimensions_name)
            errors.append(f"{dimensions_name} must be an integer")
        else:
            if embedding_dimensions <= 0:
                problems.append(dimensions_name)
                errors.append(f"{dimensions_name} must be greater than 0")

    problems = sorted(set(problems))
    result = check(
        check_name,
        not problems,
        f"{label} embedding config is explicitly configured"
        if not problems
        else f"{label} embedding config is missing or invalid",
        required=True,
        embedding_backend=backend or None,
        provider=provider or None,
        embedding_url=embedding_url,
        embedding_model=embedding_model or None,
        embedding_dimensions=embedding_dimensions,
        url_env_var=url_name,
        model_env_var=model_name,
        dimensions_env_var=dimensions_name,
        legacy_qwen_alias_used=provider == "gemini"
        and any(
            first_configured_value(values, names[key])[0].startswith("HAEORUM_QWEN_")
            for key in ("url", "model", "dimensions")
        ),
        problems=problems,
        errors=errors,
    )
    if provider == "qwen":
        result.update(
            {
                "qwen_embedding_url": embedding_url,
                "qwen_model": embedding_model or None,
                "qwen_embedding_dimensions": embedding_dimensions,
            }
        )
    elif provider == "gemini":
        result.update(
            {
                "gemini_embedding_url": embedding_url,
                "gemini_model": embedding_model or None,
                "gemini_embedding_dimensions": embedding_dimensions,
            }
        )
    return result


def gemini_proxy_provider_config_check(
    values: Mapping[str, str],
    role: str,
    require_production: bool,
) -> dict[str, Any]:
    provider = embedding_provider(values)
    required = role in {"api", "combined"} and provider == "gemini"
    auth_mode = (env_value(values, "GEMINI_AUTH_MODE") or "auto").lower()
    api_key = (
        env_value(values, "GEMINI_API_KEY")
        or env_value(values, "GOOGLE_API_KEY")
        or env_value(values, "GOOGLE_GENERATIVE_AI_API_KEY")
    )
    quota_project = (
        env_value(values, "GEMINI_QUOTA_PROJECT")
        or env_value(values, "GEMINI_GOOGLE_CLOUD_PROJECT")
        or env_value(values, "GOOGLE_CLOUD_PROJECT")
        or env_value(values, "GCLOUD_PROJECT")
    )
    proxy_api_key = (
        env_value(values, "GEMINI_PROXY_API_KEY")
        or env_value(values, "GEMINI_PROXY_SHARED_SECRET")
        or env_value(values, "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY")
        or env_value(values, "HAEORUM_EMBEDDING_PROXY_API_KEY")
    )
    limit_names = (
        "GEMINI_EMBEDDING_DIMENSIONS",
        "GEMINI_MAX_IMAGE_BYTES",
        "GEMINI_MAX_RESPONSE_BYTES",
        "GEMINI_PROVIDER_RETRY_COUNT",
        "GEMINI_PROXY_MAX_INPUTS_PER_REQUEST",
        "GEMINI_PROXY_RATE_LIMIT_RPM",
        "GEMINI_PROXY_RATE_LIMIT_BURST",
        "GEMINI_PROXY_MAX_CONCURRENT_CALLS",
    )
    float_limit_names = (
        "GEMINI_EMBEDDING_TIMEOUT_SECONDS",
        "GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS",
        "GEMINI_PROVIDER_RETRY_DELAY_SECONDS",
        "GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS",
        "GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS",
    )
    problems: list[str] = []
    parsed_ints: dict[str, int] = {}
    parsed_floats: dict[str, float] = {}
    if not required:
        return check(
            "gemini_proxy_provider_config",
            True,
            "Gemini proxy provider config is not required for this role/embedding backend",
            required=False,
            role=role,
            embedding_backend=provider,
            auth_mode=auth_mode,
            api_key_configured=not is_missing_config_value(api_key),
            proxy_api_key_configured=not is_missing_config_value(proxy_api_key),
            quota_project_configured=not is_missing_config_value(quota_project),
        )

    if auth_mode not in GEMINI_AUTH_MODES:
        problems.append("GEMINI_AUTH_MODE must be one of: auto, api_key, adc")
    elif require_production and auth_mode == "auto":
        problems.append("GEMINI_AUTH_MODE must be explicitly set to api_key or adc in production")
    elif auth_mode == "api_key" and is_missing_config_value(api_key):
        problems.append("GEMINI_API_KEY is required when GEMINI_AUTH_MODE=api_key")
    elif auth_mode == "adc" and is_missing_config_value(quota_project):
        problems.append("GEMINI_QUOTA_PROJECT or GOOGLE_CLOUD_PROJECT is required when GEMINI_AUTH_MODE=adc")
    if require_production and is_missing_config_value(proxy_api_key):
        problems.append("GEMINI_PROXY_API_KEY is required in production")

    for name in limit_names:
        try:
            value = parse_int_setting(values, name)
        except ValueError:
            problems.append(f"{name} must be an integer")
            continue
        if value is None:
            continue
        parsed_ints[name] = value
        minimum = INT_MINIMUMS[name]
        if value < minimum:
            problems.append(f"{name} must be at least {minimum}")

    for name in float_limit_names:
        try:
            value = parse_float_setting(values, name)
        except ValueError:
            problems.append(f"{name} must be a number")
            continue
        if value is None:
            continue
        parsed_floats[name] = value
        minimum = FLOAT_MINIMUMS[name]
        if value < minimum:
            problems.append(f"{name} must be at least {minimum:g}")

    return check(
        "gemini_proxy_provider_config",
        not problems,
        "Gemini proxy provider config is explicit and bounded"
        if not problems
        else "Gemini proxy provider config is missing or invalid",
        required=True,
        role=role,
        embedding_backend=provider,
        auth_mode=auth_mode,
        api_key_configured=not is_missing_config_value(api_key),
        proxy_api_key_configured=not is_missing_config_value(proxy_api_key),
        quota_project_configured=not is_missing_config_value(quota_project),
        embedding_dimensions=parsed_ints.get("GEMINI_EMBEDDING_DIMENSIONS"),
        max_image_bytes=parsed_ints.get("GEMINI_MAX_IMAGE_BYTES"),
        max_response_bytes=parsed_ints.get("GEMINI_MAX_RESPONSE_BYTES"),
        provider_retry_count=parsed_ints.get("GEMINI_PROVIDER_RETRY_COUNT"),
        provider_retry_delay_seconds=parsed_floats.get("GEMINI_PROVIDER_RETRY_DELAY_SECONDS"),
        provider_retry_max_delay_seconds=parsed_floats.get("GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS"),
        max_inputs_per_request=parsed_ints.get("GEMINI_PROXY_MAX_INPUTS_PER_REQUEST"),
        rate_limit_rpm=parsed_ints.get("GEMINI_PROXY_RATE_LIMIT_RPM"),
        rate_limit_burst=parsed_ints.get("GEMINI_PROXY_RATE_LIMIT_BURST"),
        max_concurrent_calls=parsed_ints.get("GEMINI_PROXY_MAX_CONCURRENT_CALLS"),
        queue_timeout_seconds=parsed_floats.get("GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS"),
        problems=problems,
    )


def embedding_query_runtime_cache_capacity_check(
    values: Mapping[str, str],
    role: str,
    require_production: bool,
) -> dict[str, Any]:
    engine = (env_value(values, "HAEORUM_SEARCH_ENGINE") or Settings.engine_backend).strip().lower()
    provider = embedding_provider(values)
    names = embedding_env_names(provider)
    required = role in {"api", "combined"} and engine == "marqo" and provider in {"gemini", "qwen"}
    check_name = f"{provider}_query_runtime_cache_capacity" if provider in {"gemini", "qwen"} else "query_runtime_cache_capacity"
    label = provider_label(provider)
    problems: list[str] = []
    parsed: dict[str, tuple[str, int | None]] = {}
    for key in ["parallelism", "text_cache", "image_cache"]:
        try:
            name, value = parse_int_setting_alias(values, names[key])
        except ValueError:
            name = names[key][0]
            parsed[key] = (name, None)
            problems.append(f"{name} must be an integer")
            continue
        if value is None:
            value = int(DEFAULT_NUMERIC_VALUES[name])
        parsed[key] = (name, value)

    parallelism_name, mixed_parallelism = parsed.get("parallelism", (names["parallelism"][0], None))
    text_cache_name, text_entries = parsed.get("text_cache", (names["text_cache"][0], None))
    image_cache_name, image_entries = parsed.get("image_cache", (names["image_cache"][0], None))
    if require_production and required:
        if mixed_parallelism is not None and mixed_parallelism < 2:
            problems.append(f"{parallelism_name} must be at least 2 for {label} mixed search")
        if text_entries is not None and text_entries < REQUIRED_TEXT_LOAD_CONCURRENCY:
            problems.append(
                f"{text_cache_name} must be at least "
                f"{REQUIRED_TEXT_LOAD_CONCURRENCY} for required text load concurrency"
            )
        if image_entries is not None and image_entries < REQUIRED_IMAGE_LOAD_CONCURRENCY:
            problems.append(
                f"{image_cache_name} must be at least "
                f"{REQUIRED_IMAGE_LOAD_CONCURRENCY} for required image load concurrency"
            )

    return check(
        check_name,
        not problems,
        f"{label} query runtime caches are sized for required load concurrency"
        if not problems and required
        else "query runtime cache sizing is not required for this role/search engine/embedding backend"
        if not problems
        else f"{label} query runtime cache sizing can churn under required load concurrency",
        required=required,
        engine=engine,
        embedding_backend=provider,
        provider=provider if provider in {"gemini", "qwen"} else None,
        role=role,
        mixed_query_parallelism=mixed_parallelism,
        text_cache_entries=text_entries,
        image_cache_entries=image_entries,
        mixed_query_parallelism_env_var=parallelism_name,
        text_cache_entries_env_var=text_cache_name,
        image_cache_entries_env_var=image_cache_name,
        required_text_load_concurrency=REQUIRED_TEXT_LOAD_CONCURRENCY,
        required_image_load_concurrency=REQUIRED_IMAGE_LOAD_CONCURRENCY,
        separate_text_image_quotas=True,
        problems=problems,
    )


def redis_scale_check(values: Mapping[str, str], role: str, api_server_count: int) -> dict[str, Any]:
    required = role in {"api", "combined"} and api_server_count > 1
    redis_url = env_value(values, "HAEORUM_REDIS_URL")
    ok = (not required) or not is_missing_config_value(redis_url)
    return check(
        "redis_required_for_scale",
        ok,
        "Redis is configured for multi-API cache and rate limit sharing"
        if ok and required
        else "Redis is not required for this role/count"
        if ok
        else "HAEORUM_REDIS_URL is required when API server count is greater than 1",
        required=required,
        api_server_count=api_server_count,
        role=role,
    )


def redis_url_check(values: Mapping[str, str]) -> dict[str, Any]:
    raw = env_value(values, "HAEORUM_REDIS_URL")
    if not raw:
        return check(
            "redis_url",
            True,
            "HAEORUM_REDIS_URL is not configured",
            configured=False,
        )
    if is_missing_config_value(raw):
        return check(
            "redis_url",
            False,
            "HAEORUM_REDIS_URL must not be a placeholder",
            configured=True,
            problems=["HAEORUM_REDIS_URL must not be a placeholder"],
        )
    try:
        normalized = validate_redis_url_value(raw)
    except ValueError as exc:
        return check(
            "redis_url",
            False,
            str(exc),
            configured=True,
            problems=[str(exc)],
        )
    parsed = urlparse(normalized)
    return check(
        "redis_url",
        True,
        "HAEORUM_REDIS_URL is valid",
        configured=True,
        scheme=parsed.scheme.lower(),
        host=parsed.hostname,
        port=parsed.port,
        database=parsed.path.removeprefix("/") if parsed.path and parsed.path != "/" else None,
        tls=parsed.scheme.lower() == "rediss",
        has_credentials=bool(parsed.username or parsed.password),
        has_query=bool(parsed.query),
    )


def cache_ttl_scale_check(values: Mapping[str, str], role: str, api_server_count: int) -> dict[str, Any]:
    required = role in {"api", "combined"} and api_server_count > 1
    raw = env_value(values, "HAEORUM_CACHE_TTL_SECONDS")
    try:
        ttl_seconds = int(raw) if raw else Settings.cache_ttl_seconds
    except ValueError:
        return check(
            "cache_ttl_required_for_scale",
            False,
            "HAEORUM_CACHE_TTL_SECONDS must be an integer",
            required=required,
            api_server_count=api_server_count,
            role=role,
            ttl_seconds=None,
        )
    ok = (not required) or ttl_seconds > 0
    return check(
        "cache_ttl_required_for_scale",
        ok,
        "search cache TTL is enabled for multi-API cache sharing"
        if ok and required
        else "search cache TTL is not required for this role/count"
        if ok
        else "HAEORUM_CACHE_TTL_SECONDS must be greater than 0 when API server count is greater than 1",
        required=required,
        api_server_count=api_server_count,
        role=role,
        ttl_seconds=ttl_seconds,
    )


def backend_retry_budget_seconds(
    timeout_seconds: float,
    retry_count: int,
    retry_delay_seconds: float,
    retry_after_max_seconds: float = 0.0,
) -> float:
    attempts = max(0, int(retry_count)) + 1
    delay = max(0.0, float(retry_delay_seconds))
    retry_after_max = max(0.0, float(retry_after_max_seconds))
    retry_delay_total = sum(max(delay * (2 ** attempt), retry_after_max) for attempt in range(max(attempts - 1, 0)))
    return max(0.001, (max(0.001, float(timeout_seconds)) * attempts) + retry_delay_total)


def cache_miss_coordination_check(
    values: Mapping[str, str],
    role: str,
    api_server_count: int,
    require_production: bool,
) -> dict[str, Any]:
    engine = (env_value(values, "HAEORUM_SEARCH_ENGINE") or Settings.engine_backend).strip().lower()
    api_role = role in {"api", "combined"}
    redis_configured = not is_missing_config_value(env_value(values, "HAEORUM_REDIS_URL"))
    redis_coordination_required = api_role and engine == "marqo" and (api_server_count > 1 or redis_configured)
    problems: list[str] = []

    parsed_floats: dict[str, float | None] = {}
    for name in [
        "HAEORUM_CACHE_MISS_LOCK_SECONDS",
        "HAEORUM_CACHE_MISS_WAIT_SECONDS",
        "HAEORUM_CACHE_MISS_POLL_SECONDS",
        "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS",
        "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS",
        "HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS",
    ]:
        try:
            parsed_value = parse_float_setting(values, name)
        except ValueError:
            parsed_floats[name] = None
            problems.append(f"{name} must be a number")
            continue
        if parsed_value is not None and not math.isfinite(parsed_value):
            parsed_floats[name] = None
            problems.append(f"{name} must be finite")
            continue
        parsed_floats[name] = parsed_value

    try:
        retry_count = parse_int_setting(values, "HAEORUM_MARQO_SEARCH_RETRY_COUNT")
    except ValueError:
        retry_count = None
        problems.append("HAEORUM_MARQO_SEARCH_RETRY_COUNT must be an integer")

    lock_seconds = parsed_floats.get("HAEORUM_CACHE_MISS_LOCK_SECONDS")
    if lock_seconds is None:
        lock_seconds = Settings.cache_miss_lock_seconds
    wait_seconds = parsed_floats.get("HAEORUM_CACHE_MISS_WAIT_SECONDS")
    if wait_seconds is None:
        wait_seconds = Settings.cache_miss_wait_seconds
    poll_seconds = parsed_floats.get("HAEORUM_CACHE_MISS_POLL_SECONDS")
    if poll_seconds is None:
        poll_seconds = Settings.cache_miss_poll_seconds
    timeout_seconds = parsed_floats.get("HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS")
    if timeout_seconds is None:
        timeout_seconds = Settings.marqo_search_timeout_seconds
    retry_delay_seconds = parsed_floats.get("HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS")
    if retry_delay_seconds is None:
        retry_delay_seconds = Settings.marqo_search_retry_delay_seconds
    retry_after_max_seconds = parsed_floats.get("HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS")
    if retry_after_max_seconds is None:
        retry_after_max_seconds = Settings.backend_retry_after_max_seconds
    if retry_count is None:
        retry_count = Settings.marqo_search_retry_count

    backend_budget = backend_retry_budget_seconds(
        timeout_seconds,
        retry_count,
        retry_delay_seconds,
        retry_after_max_seconds,
    )
    required_lock_seconds = max(backend_budget + 1.0, wait_seconds + poll_seconds)

    if require_production and redis_coordination_required:
        if wait_seconds <= 0:
            problems.append("HAEORUM_CACHE_MISS_WAIT_SECONDS must be greater than 0 for duplicate search coalescing")
        if poll_seconds <= 0:
            problems.append("HAEORUM_CACHE_MISS_POLL_SECONDS must be greater than 0 for duplicate search coalescing")
        if poll_seconds > wait_seconds:
            problems.append("HAEORUM_CACHE_MISS_POLL_SECONDS must not exceed HAEORUM_CACHE_MISS_WAIT_SECONDS")
        if lock_seconds < required_lock_seconds:
            problems.append(
                f"HAEORUM_CACHE_MISS_LOCK_SECONDS must be at least {required_lock_seconds:g} "
                "so Redis miss locks outlive Marqo timeout and retry budget"
            )

    return check(
        "cache_miss_coordination",
        not problems,
        "Redis cache miss lock timing covers Marqo timeout and retry budget"
        if not problems and redis_coordination_required
        else "cache miss lock coordination is not required for this role/count/search engine"
        if not problems
        else "Redis cache miss lock timing can allow duplicate Marqo searches under slow backend responses",
        required=redis_coordination_required,
        engine=engine,
        role=role,
        api_server_count=api_server_count,
        redis_configured=redis_configured,
        cache_miss_lock_seconds=lock_seconds,
        cache_miss_wait_seconds=wait_seconds,
        cache_miss_poll_seconds=poll_seconds,
        marqo_search_timeout_seconds=timeout_seconds,
        marqo_search_retry_count=retry_count,
        marqo_search_retry_delay_seconds=retry_delay_seconds,
        backend_retry_after_max_seconds=retry_after_max_seconds,
        backend_retry_budget_seconds=round(backend_budget, 3),
        required_lock_seconds=math.ceil(required_lock_seconds * 1000) / 1000,
        problems=problems,
    )


def backend_circuit_breaker_check(values: Mapping[str, str], role: str, require_production: bool) -> dict[str, Any]:
    engine = (env_value(values, "HAEORUM_SEARCH_ENGINE") or Settings.engine_backend).strip().lower()
    required = engine == "marqo" and role in {"api", "combined"}
    problems: list[str] = []
    if not required:
        return check(
            "backend_circuit_breaker",
            True,
            "backend circuit breaker is not required for this role/search engine",
            required=False,
            engine=engine,
            role=role,
        )

    parsed_ints: dict[str, int | None] = {}
    for name in ["HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD", "HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS"]:
        raw = env_value(values, name)
        if require_production and is_missing_config_value(raw):
            parsed_ints[name] = None
            problems.append(f"{name} must be explicitly set for Marqo production traffic")
            continue
        try:
            parsed_ints[name] = parse_int_setting(values, name)
        except ValueError:
            parsed_ints[name] = None
            problems.append(f"{name} must be an integer")
        if parsed_ints[name] is None and name in DEFAULT_NUMERIC_VALUES:
            parsed_ints[name] = int(DEFAULT_NUMERIC_VALUES[name])

    raw_cooldown = env_value(values, "HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS")
    if require_production and is_missing_config_value(raw_cooldown):
        cooldown_seconds = None
        problems.append("HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS must be explicitly set for Marqo production traffic")
    else:
        try:
            cooldown_seconds = parse_float_setting(values, "HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS")
        except ValueError:
            cooldown_seconds = None
            problems.append("HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS must be a number")
        if cooldown_seconds is None:
            cooldown_seconds = float(DEFAULT_NUMERIC_VALUES["HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS"])

    failure_threshold = parsed_ints.get("HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD")
    half_open_max_calls = parsed_ints.get("HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS")
    if failure_threshold is not None and failure_threshold <= 0:
        problems.append("HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD must be greater than 0 for Marqo production traffic")
    if cooldown_seconds is not None and cooldown_seconds <= 0:
        problems.append("HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS must be greater than 0 for Marqo production traffic")
    if half_open_max_calls is not None and half_open_max_calls < 1:
        problems.append("HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS must be at least 1")
    if (
        failure_threshold is not None
        and failure_threshold > 0
        and half_open_max_calls is not None
        and half_open_max_calls > max(1, min(failure_threshold, 3))
    ):
        problems.append(
            "HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS should stay small so recovery probes do not stampede Marqo"
        )

    return check(
        "backend_circuit_breaker",
        not problems,
        "backend circuit breaker fails fast during repeated Marqo/Qwen transient failures"
        if not problems
        else "backend circuit breaker can be disabled or too loose during backend outage",
        required=True,
        engine=engine,
        role=role,
        failure_threshold=failure_threshold,
        cooldown_seconds=cooldown_seconds,
        half_open_max_calls=half_open_max_calls,
        problems=problems,
    )


def api_threadpool_capacity_check(values: Mapping[str, str], role: str, require_production: bool) -> dict[str, Any]:
    required = role in {"api", "combined"}
    problems: list[str] = []
    parsed: dict[str, int | None] = {}
    for name in [
        "HAEORUM_SEARCH_MAX_CONCURRENCY",
        "HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY",
        "HAEORUM_API_THREADPOOL_TOKENS",
    ]:
        raw = env_value(values, name)
        if name == "HAEORUM_API_THREADPOOL_TOKENS" and require_production and required and is_missing_config_value(raw):
            parsed[name] = None
            problems.append("HAEORUM_API_THREADPOOL_TOKENS must be explicitly set for API production traffic")
            continue
        try:
            value = parse_int_setting(values, name)
        except ValueError:
            parsed[name] = None
            problems.append(f"{name} must be an integer")
            continue
        if value is None:
            value = int(DEFAULT_NUMERIC_VALUES[name])
        parsed[name] = value

    search_max_concurrency = int(parsed.get("HAEORUM_SEARCH_MAX_CONCURRENCY") or 0)
    image_search_max_concurrency = int(parsed.get("HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY") or 0)
    configured_tokens = parsed.get("HAEORUM_API_THREADPOOL_TOKENS")
    required_tokens = required_api_threadpool_tokens(search_max_concurrency, image_search_max_concurrency)
    if require_production and required:
        if search_max_concurrency <= 0:
            problems.append("HAEORUM_SEARCH_MAX_CONCURRENCY must be greater than 0 for API production traffic")
        if image_search_max_concurrency <= 0:
            problems.append("HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY must be greater than 0 for API production traffic")
    if required and configured_tokens is not None and configured_tokens < required_tokens:
        problems.append(
            f"HAEORUM_API_THREADPOOL_TOKENS must be at least {required_tokens} "
            "for configured search and image concurrency"
        )

    return check(
        "api_threadpool_capacity",
        not problems,
        "API threadpool tokens cover configured search and image concurrency"
        if not problems
        else "API threadpool tokens can bottleneck configured search and image concurrency",
        required=required,
        role=role,
        search_max_concurrency=search_max_concurrency,
        image_search_max_concurrency=image_search_max_concurrency,
        configured_tokens=configured_tokens,
        required_tokens=required_tokens,
        overhead_tokens=required_tokens - search_max_concurrency - image_search_max_concurrency,
        problems=problems,
    )


def marqo_pool_capacity_check(values: Mapping[str, str], role: str, api_server_count: int, require_production: bool) -> dict[str, Any]:
    engine = (env_value(values, "HAEORUM_SEARCH_ENGINE") or Settings.engine_backend).strip().lower()
    required = engine == "marqo" and role in {"api", "combined"}
    pool_values: dict[str, int | None] = {}
    problems: list[str] = []
    if not required:
        return check(
            "marqo_pool_capacity",
            True,
            "Marqo pool capacity is not required for this role/search engine",
            required=False,
            engine=engine,
            role=role,
        )

    try:
        search_max_concurrency = parse_int_setting(values, "HAEORUM_SEARCH_MAX_CONCURRENCY")
    except ValueError:
        search_max_concurrency = None
        problems.append("HAEORUM_SEARCH_MAX_CONCURRENCY must be an integer")
    if search_max_concurrency is None:
        search_max_concurrency = Settings.search_max_concurrency
    if require_production and search_max_concurrency <= 0:
        problems.append("HAEORUM_SEARCH_MAX_CONCURRENCY must be greater than 0 for Marqo production traffic")

    per_api_slots = max(int(search_max_concurrency or 0), 1)
    total_api_slots = per_api_slots * max(int(api_server_count or 1), 1)
    required_pool_size = total_api_slots
    target_load_concurrency = min(REQUIRED_TEXT_LOAD_CONCURRENCY, total_api_slots)
    concurrency_values: dict[str, int | None] = {}
    backend_http_max_active_requests = None
    try:
        backend_http_max_active_requests = parse_int_setting(values, "HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS")
    except ValueError:
        problems.append("HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS must be an integer")
    if backend_http_max_active_requests is None:
        backend_http_max_active_requests = int(DEFAULT_NUMERIC_VALUES["HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS"])
    if 0 < backend_http_max_active_requests < per_api_slots:
        problems.append(
            "HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS must be 0 or at least "
            f"{per_api_slots} for one API server's configured search concurrency"
        )

    for name in MARQO_POOL_ENV_NAMES:
        raw = env_value(values, name)
        if require_production and is_missing_config_value(raw):
            pool_values[name] = None
            problems.append(f"{name} must be explicitly set for Marqo production traffic")
            continue
        try:
            pool_value = parse_int_setting(values, name)
        except ValueError:
            pool_values[name] = None
            problems.append(f"{name} must be an integer")
            continue
        if pool_value is None:
            pool_value = int(DEFAULT_NUMERIC_VALUES[name])
        pool_values[name] = pool_value
        if pool_value < required_pool_size:
            problems.append(f"{name} must be at least {required_pool_size} for current API search concurrency")

    for name in MARQO_CONCURRENCY_ENV_NAMES:
        raw = env_value(values, name)
        if require_production and is_missing_config_value(raw):
            concurrency_values[name] = None
            problems.append(f"{name} must be explicitly set for Marqo production traffic")
            continue
        try:
            concurrency_value = parse_int_setting(values, name)
        except ValueError:
            concurrency_values[name] = None
            problems.append(f"{name} must be an integer")
            continue
        if concurrency_value is None:
            concurrency_value = int(DEFAULT_NUMERIC_VALUES[name])
        concurrency_values[name] = concurrency_value

    marqo_api_workers = concurrency_values.get("MARQO_API_WORKERS")
    if marqo_api_workers is not None and api_server_count > 1 and marqo_api_workers < 2:
        problems.append("MARQO_API_WORKERS must be at least 2 when one Marqo API backs multiple API servers")
    marqo_max_concurrent_search = concurrency_values.get("MARQO_MAX_CONCURRENT_SEARCH")
    required_marqo_search_concurrency = max(target_load_concurrency, 1)
    if marqo_max_concurrent_search is not None and marqo_max_concurrent_search < required_marqo_search_concurrency:
        problems.append(
            f"MARQO_MAX_CONCURRENT_SEARCH must be at least {required_marqo_search_concurrency} "
            "for required text load concurrency"
        )

    return check(
        "marqo_pool_capacity",
        not problems,
        "Marqo/Vespa pools cover configured API search concurrency"
        if not problems
        else "Marqo/Vespa pool sizing can bottleneck configured API search concurrency",
        required=True,
        engine=engine,
        role=role,
        api_server_count=api_server_count,
        search_max_concurrency=search_max_concurrency,
        total_api_search_slots=total_api_slots,
        required_pool_size=required_pool_size,
        backend_http_max_active_requests=backend_http_max_active_requests,
        required_text_load_concurrency=REQUIRED_TEXT_LOAD_CONCURRENCY,
        target_load_concurrency=target_load_concurrency,
        required_marqo_search_concurrency=required_marqo_search_concurrency,
        pools=pool_values,
        concurrency=concurrency_values,
        problems=problems,
    )


def marqo_timeout_budget_check(values: Mapping[str, str], role: str, require_production: bool) -> dict[str, Any]:
    engine = (env_value(values, "HAEORUM_SEARCH_ENGINE") or Settings.engine_backend).strip().lower()
    required = engine == "marqo" and role in {"api", "combined"}
    problems: list[str] = []
    if not required:
        return check(
            "marqo_timeout_budget",
            True,
            "Marqo timeout budget is not required for this role/search engine",
            required=False,
            engine=engine,
            role=role,
        )

    try:
        app_search_timeout_seconds = parse_float_setting(values, "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS")
    except ValueError:
        app_search_timeout_seconds = None
        problems.append("HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS must be a number")
    if app_search_timeout_seconds is None:
        app_search_timeout_seconds = Settings.marqo_search_timeout_seconds

    raw_vespa_timeout = env_value(values, "VESPA_SEARCH_TIMEOUT_MS")
    if require_production and is_missing_config_value(raw_vespa_timeout):
        vespa_search_timeout_ms = None
        problems.append("VESPA_SEARCH_TIMEOUT_MS must be explicitly set for Marqo production traffic")
    else:
        try:
            vespa_search_timeout_ms = parse_int_setting(values, "VESPA_SEARCH_TIMEOUT_MS")
        except ValueError:
            vespa_search_timeout_ms = None
            problems.append("VESPA_SEARCH_TIMEOUT_MS must be an integer")
        if vespa_search_timeout_ms is None:
            vespa_search_timeout_ms = int(DEFAULT_NUMERIC_VALUES["VESPA_SEARCH_TIMEOUT_MS"])

    if vespa_search_timeout_ms is not None:
        if vespa_search_timeout_ms < REQUIRED_TEXT_LOAD_P95_MS:
            problems.append(
                f"VESPA_SEARCH_TIMEOUT_MS must be at least {REQUIRED_TEXT_LOAD_P95_MS} "
                "so Vespa does not fail before the required text p95 target"
            )
        marqo_internal_http_timeout_ms = max(vespa_search_timeout_ms + 1000, 5000)
        app_search_timeout_ms = float(app_search_timeout_seconds) * 1000
        if app_search_timeout_ms <= marqo_internal_http_timeout_ms:
            problems.append(
                "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS must exceed Marqo's Vespa HTTP timeout budget"
            )
    else:
        marqo_internal_http_timeout_ms = None
        app_search_timeout_ms = float(app_search_timeout_seconds) * 1000

    try:
        backend_http_max_idle_seconds = parse_float_setting(values, "HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS")
    except ValueError:
        backend_http_max_idle_seconds = None
        problems.append("HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS must be a number")
    if backend_http_max_idle_seconds is None:
        backend_http_max_idle_seconds = float(DEFAULT_NUMERIC_VALUES["HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS"])

    raw_keepalive_timeout = env_value(values, "MARQO_API_KEEPALIVE_TIMEOUT")
    if require_production and is_missing_config_value(raw_keepalive_timeout):
        marqo_api_keepalive_timeout_seconds = None
        problems.append("MARQO_API_KEEPALIVE_TIMEOUT must be explicitly set for Marqo production traffic")
    else:
        try:
            marqo_api_keepalive_timeout_seconds = parse_int_setting(values, "MARQO_API_KEEPALIVE_TIMEOUT")
        except ValueError:
            marqo_api_keepalive_timeout_seconds = None
            problems.append("MARQO_API_KEEPALIVE_TIMEOUT must be an integer")
        if marqo_api_keepalive_timeout_seconds is None:
            marqo_api_keepalive_timeout_seconds = int(DEFAULT_NUMERIC_VALUES["MARQO_API_KEEPALIVE_TIMEOUT"])

    if (
        marqo_api_keepalive_timeout_seconds is not None
        and backend_http_max_idle_seconds >= marqo_api_keepalive_timeout_seconds
    ):
        problems.append(
            "HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS must be lower than MARQO_API_KEEPALIVE_TIMEOUT "
            "so API workers rotate idle backend connections before Uvicorn closes them"
        )

    return check(
        "marqo_timeout_budget",
        not problems,
        "Marqo/Vespa timeout budget can cover required load p95 targets"
        if not problems
        else "Marqo/Vespa timeout budget can fail before required load p95 targets",
        required=True,
        engine=engine,
        role=role,
        app_search_timeout_seconds=app_search_timeout_seconds,
        app_search_timeout_ms=round(app_search_timeout_ms, 3),
        vespa_search_timeout_ms=vespa_search_timeout_ms,
        marqo_internal_http_timeout_ms=marqo_internal_http_timeout_ms,
        backend_http_max_idle_seconds=backend_http_max_idle_seconds,
        marqo_api_keepalive_timeout_seconds=marqo_api_keepalive_timeout_seconds,
        required_text_p95_ms=REQUIRED_TEXT_LOAD_P95_MS,
        required_image_p95_ms=REQUIRED_IMAGE_LOAD_P95_MS,
        problems=problems,
    )


def marqo_transport_tuning_check(values: Mapping[str, str], role: str, require_production: bool) -> dict[str, Any]:
    engine = (env_value(values, "HAEORUM_SEARCH_ENGINE") or Settings.engine_backend).strip().lower()
    required = engine == "marqo" and role in {"api", "combined"}
    problems: list[str] = []
    if not required:
        return check(
            "marqo_transport_tuning",
            True,
            "Marqo transport tuning is not required for this role/search engine",
            required=False,
            engine=engine,
            role=role,
        )

    raw_gzip_minimum_size = env_value(values, "MARQO_API_GZIP_MINIMUM_SIZE")
    if require_production and is_missing_config_value(raw_gzip_minimum_size):
        gzip_minimum_size = None
        problems.append("MARQO_API_GZIP_MINIMUM_SIZE must be explicitly set for Marqo production traffic")
    else:
        try:
            gzip_minimum_size = parse_int_setting(values, "MARQO_API_GZIP_MINIMUM_SIZE")
        except ValueError:
            gzip_minimum_size = None
            problems.append("MARQO_API_GZIP_MINIMUM_SIZE must be an integer")
        if gzip_minimum_size is None:
            gzip_minimum_size = int(DEFAULT_NUMERIC_VALUES["MARQO_API_GZIP_MINIMUM_SIZE"])

    if gzip_minimum_size is not None:
        if gzip_minimum_size <= 0:
            problems.append("MARQO_API_GZIP_MINIMUM_SIZE must be greater than 0 so Marqo compresses JSON responses")
        if gzip_minimum_size > 4096:
            problems.append(
                "MARQO_API_GZIP_MINIMUM_SIZE must be at most 4096 so large search JSON responses are compressed"
            )

    return check(
        "marqo_transport_tuning",
        not problems,
        "Marqo API transport tuning reduces search response payload overhead"
        if not problems
        else "Marqo API transport tuning can leave large search responses uncompressed",
        required=True,
        engine=engine,
        role=role,
        gzip_minimum_size=gzip_minimum_size,
        max_gzip_minimum_size=4096,
        problems=problems,
    )


def parse_rate_limit_capacity_value(values: Mapping[str, str], name: str, default: int) -> tuple[int | None, str | None]:
    raw = env_value(values, name)
    if raw == "":
        return int(default), None
    try:
        return int(raw), None
    except ValueError:
        return None, f"{name} must be an integer"


def rate_limit_capacity_check(values: Mapping[str, str], require_production: bool) -> dict[str, Any]:
    required = bool(require_production)
    defaults = {
        "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE": Settings.search_rate_limit_per_minute,
        "HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE": Settings.mall_search_rate_limit_per_minute,
        "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE": Settings.image_rate_limit_per_minute,
        "HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE": Settings.mall_image_rate_limit_per_minute,
    }
    limits: dict[str, int | None] = {}
    problems: list[str] = []
    for name, default in defaults.items():
        parsed, error = parse_rate_limit_capacity_value(values, name, default)
        limits[name] = parsed
        if error:
            problems.append(error)

    requirements = {
        "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE": REQUIRED_MIXED_TRAFFIC_SEARCH_REQUESTS,
        "HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE": REQUIRED_MIXED_TRAFFIC_SEARCH_REQUESTS,
        "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE": REQUIRED_MIXED_TRAFFIC_IMAGE_REQUESTS,
        "HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE": REQUIRED_MIXED_TRAFFIC_IMAGE_REQUESTS,
    }
    too_low: dict[str, dict[str, int]] = {}
    if required:
        for name, minimum in requirements.items():
            value = limits.get(name)
            if value is None:
                continue
            if value > 0 and value < minimum:
                too_low[name] = {"configured": value, "minimum": minimum}
        for name, detail in too_low.items():
            problems.append(f"{name} must be 0 or at least {detail['minimum']} for required load evidence")

    return check(
        "rate_limit_capacity_for_required_load",
        not problems,
        "rate limits allow required 850-user mixed traffic evidence"
        if not problems
        else "rate limit settings would throttle required 850-user mixed traffic evidence",
        required=required,
        required_active_users=REQUIRED_MIXED_TRAFFIC_ACTIVE_USERS,
        required_search_requests_per_minute=REQUIRED_MIXED_TRAFFIC_SEARCH_REQUESTS,
        required_image_requests_per_minute=REQUIRED_MIXED_TRAFFIC_IMAGE_REQUESTS,
        limits=limits,
        too_low=too_low,
        problems=problems,
    )


def trusted_proxy_check(values: Mapping[str, str]) -> dict[str, Any]:
    raw = env_value(values, "HAEORUM_TRUSTED_PROXY_IPS")
    try:
        proxies = parse_trusted_proxy_ips(raw)
    except ValueError as exc:
        return check("trusted_proxy_ips", False, str(exc), proxies=[])
    return check(
        "trusted_proxy_ips",
        True,
        "trusted proxy IP/CIDR list is valid",
        proxies=list(proxies),
    )


def sync_interval_hourly_check(values: Mapping[str, str]) -> dict[str, Any]:
    try:
        sync_interval_seconds = parse_int_setting(values, "HAEORUM_SYNC_INTERVAL_SECONDS")
    except ValueError:
        return check(
            "sync_interval_hourly",
            False,
            "HAEORUM_SYNC_INTERVAL_SECONDS must be an integer",
            sync_interval_seconds=None,
            maximum_seconds=MAX_PRODUCTION_SYNC_INTERVAL_SECONDS,
        )
    if sync_interval_seconds is None:
        sync_interval_seconds = MAX_PRODUCTION_SYNC_INTERVAL_SECONDS
    problems: list[str] = []
    if sync_interval_seconds <= 0:
        problems.append("HAEORUM_SYNC_INTERVAL_SECONDS must be positive")
    if sync_interval_seconds > MAX_PRODUCTION_SYNC_INTERVAL_SECONDS:
        problems.append(
            f"HAEORUM_SYNC_INTERVAL_SECONDS must be at most "
            f"{MAX_PRODUCTION_SYNC_INTERVAL_SECONDS} for hourly sync"
        )
    return check(
        "sync_interval_hourly",
        not problems,
        "sync interval satisfies hourly sync requirement"
        if not problems
        else "sync interval does not satisfy hourly sync requirement",
        sync_interval_seconds=sync_interval_seconds,
        maximum_seconds=MAX_PRODUCTION_SYNC_INTERVAL_SECONDS,
        problems=problems,
    )


def sync_alert_webhook_check(values: Mapping[str, str]) -> dict[str, Any]:
    report = check_sync_alert_webhook_url(env_value(values, "HAEORUM_SYNC_ALERT_WEBHOOK_URL"))
    return check(
        "sync_alert_webhook",
        bool(report["ok"]),
        str(report["message"]),
        configured=report["configured"],
        scheme=report["scheme"],
        host=report["host"],
        has_path=report.get("has_path"),
        has_query=report.get("has_query"),
    )


@contextmanager
def patched_environ(values: Mapping[str, str]) -> Iterator[None]:
    original = dict(os.environ)
    os.environ.clear()
    os.environ.update({name: str(value) for name, value in values.items()})
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def settings_load_check(values: Mapping[str, str]) -> dict[str, Any]:
    try:
        with patched_environ(values):
            settings = load_settings()
    except Exception as exc:
        return check("settings_load", False, str(exc))
    enabled_mall_count = sum(1 for mall in settings.malls.values() if mall.enabled)
    return check(
        "settings_load",
        True,
        "application settings load successfully",
        environment=settings.environment,
        engine=settings.engine_backend,
        enabled_mall_count=enabled_mall_count,
    )


def build_report(args: argparse.Namespace, env_values: Mapping[str, str] | None = None) -> dict[str, Any]:
    env_file = str(getattr(args, "env_file", "") or "")
    role = str(getattr(args, "role", "api") or "api")
    api_server_count = int(getattr(args, "api_server_count", 1) or 1)
    require_production = not bool(getattr(args, "allow_non_production", False))
    require_paths = not bool(getattr(args, "skip_path_checks", False))
    checks: list[dict[str, Any]] = []
    try:
        values = dict(env_values) if env_values is not None else read_env_values(env_file or None)
    except Exception as exc:
        checks.append(check("env_loaded", False, str(exc), env_file=env_file or None))
        return report_from_checks(args, checks, {}, env_file, role, api_server_count, require_production, require_paths)

    checks.append(
        check(
            "env_loaded",
            True,
            "environment values loaded",
            env_file=env_file or None,
            source="env_file" if env_file else "process",
            variable_count=len(values),
        )
    )
    checks.append(
        check_secret_file_permissions(
            env_file or None,
            name="env_file_permissions",
            required=bool(env_file),
            max_mode=SERVICE_ENV_FILE_MAX_MODE,
        )
    )
    checks.append(
        check(
            "api_server_count",
            api_server_count >= 1,
            "API server count is valid" if api_server_count >= 1 else "--api-server-count must be at least 1",
            api_server_count=api_server_count,
        )
    )
    missing = missing_variable_names(values, DEFAULT_REQUIRED_VARIABLES)
    checks.append(
        check(
            "required_variables",
            not missing,
            "required variables are present" if not missing else "required variables are missing or placeholders",
            missing=missing,
        )
    )
    checks.extend(
        [
            production_env_check(values, require_production),
            search_engine_check(values, require_production),
            marqo_url_check(values),
            embedding_provider_config_check(values, require_production),
            gemini_proxy_provider_config_check(values, role, require_production),
            embedding_query_runtime_cache_capacity_check(values, role, require_production),
            admin_key_check(values),
            cors_check(values, require_production, require_paths),
            product_url_template_check(values, require_production),
            data_source_check(values, require_paths),
            mssql_connection_string_check(values, require_production),
            path_check(values, "HAEORUM_MALL_CONFIG_PATH", required=True, require_paths=require_paths),
            mall_security_check(values, require_production, require_paths),
            path_check(values, "HAEORUM_CORS_ORIGINS_FILE", required=False, require_paths=require_paths),
            path_check(values, "HAEORUM_QUERY_SYNONYM_PATH", required=False, require_paths=require_paths),
            redis_url_check(values),
            redis_scale_check(values, role, api_server_count),
            cache_ttl_scale_check(values, role, api_server_count),
            cache_miss_coordination_check(values, role, api_server_count, require_production),
            backend_circuit_breaker_check(values, role, require_production),
            api_threadpool_capacity_check(values, role, require_production),
            marqo_pool_capacity_check(values, role, api_server_count, require_production),
            marqo_timeout_budget_check(values, role, require_production),
            marqo_transport_tuning_check(values, role, require_production),
            rate_limit_capacity_check(values, require_production),
            trusted_proxy_check(values),
            sync_interval_hourly_check(values),
            sync_alert_webhook_check(values),
            boolean_settings_check(values),
            numeric_check(values),
            settings_load_check(values),
        ]
    )
    return report_from_checks(args, checks, values, env_file, role, api_server_count, require_production, require_paths)


def report_from_checks(
    args: argparse.Namespace,
    checks: list[dict[str, Any]],
    values: Mapping[str, str],
    env_file: str,
    role: str,
    api_server_count: int,
    require_production: bool,
    require_paths: bool,
) -> dict[str, Any]:
    failed = [item["name"] for item in checks if item.get("ok") is not True]
    return {
        "ok": not failed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "env_file": env_file or None,
        "role": role,
        "api_server_count": api_server_count,
        "require_production": require_production,
        "require_paths": require_paths,
        "failed_checks": failed,
        "configured_variable_count": len(values),
        "checks": checks,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Haeorum AI Search Env Check",
        "",
        f"- OK: `{report['ok']}`",
        f"- Env file: `{report.get('env_file')}`",
        f"- Role: `{report.get('role')}`",
        f"- API server count: `{report.get('api_server_count')}`",
        "",
        "| Check | Passed | Message |",
        "| --- | --- | --- |",
    ]
    for item in report.get("checks", []):
        lines.append(
            "| {name} | `{ok}` | {message} |".format(
                name=item.get("name"),
                ok=item.get("ok"),
                message=escape_markdown_cell(item.get("message")),
            )
        )
    if report.get("failed_checks"):
        lines.extend(["", f"- Failed checks: `{', '.join(report['failed_checks'])}`"])
    return "\n".join(lines) + "\n"


def escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build env preflight evidence for Haeorum AI Search deployment.")
    parser.add_argument("--env-file", default="", help="KEY=VALUE file used by systemd, Docker, or the shell.")
    parser.add_argument("--role", choices=["api", "sync", "combined"], default="api")
    parser.add_argument("--api-server-count", type=int, default=1)
    parser.add_argument(
        "--allow-non-production",
        action="store_true",
        help="Only for local dry-runs. Production env checks require HAEORUM_ENV=production by default.",
    )
    parser.add_argument(
        "--skip-path-checks",
        action="store_true",
        help="Only for reviewing an env file before config/data files exist on the target server.",
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    args = parser.parse_args()

    report = build_report(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
