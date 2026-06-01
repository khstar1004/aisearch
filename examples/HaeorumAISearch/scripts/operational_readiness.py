from __future__ import annotations

import argparse
import ipaddress
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse


EvidenceCheck = Callable[[dict[str, Any]], tuple[bool, str, dict[str, Any]]]
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.url_safety import is_non_public_host

DEFAULT_EVIDENCE_FILENAMES = {
    "api_smoke_report": "api-smoke.json",
    "mssql_export_report": "mssql-export.json",
    "poc_dataset_report": "poc-dataset.json",
    "mssql_view_report": "mssql-view.json",
    "image_url_report": "image-url-check.json",
    "quality_report": "quality-report.json",
    "csv_index_report": "csv-index.json",
    "mall_config_build_report": "mall-config-build.json",
    "mall_config_report": "mall-config-check.json",
    "marqo_resource_report": "marqo-resource.json",
    "server_preflight_report": "server-preflight.json",
    "env_check_report": "env-check.json",
    "load_text_report": "load-text.json",
    "load_image_report": "load-image.json",
    "load_mixed_report": "load-mixed.json",
    "load_mixed_traffic_report": "load-mixed-traffic.json",
    "api_scale_report": "api-scale.json",
    "representative_sites_report": "representative-sites.json",
    "security_report": "security.json",
}
DERIVED_OUTPUT_PLACEHOLDERS = {
    "<quality_report.md>": "quality-report.md",
    "<csv_index.md>": "csv-index.md",
    "<env_check.md>": "env-check.md",
    "<load_text.md>": "load-text.md",
    "<load_image.md>": "load-image.md",
    "<load_mixed.md>": "load-mixed.md",
    "<load_mixed_traffic.md>": "load-mixed-traffic.md",
    "<api_scale.md>": "api-scale.md",
    "<representative_sites.md>": "representative-sites.md",
    "<security.md>": "security.md",
}
QUALITY_CASE_MIN_TYPE_COUNTS = {"text": 2, "image": 1, "text_image": 1}
QUALITY_CASE_MIN_RESULTS = 3
QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES = 1
QUALITY_CASE_MIN_TEXT_VARIANT_CASES = 1
BLOCKING_IMAGE_WARNING_TYPES = {"placeholder_or_sample_image"}
SIMULATION_EVIDENCE_MARKER = "SIMULATED_ONLY_NOT_OPERATIONAL_EVIDENCE"
DEFAULT_MAX_EVIDENCE_AGE_DAYS = 14
EVIDENCE_FUTURE_SKEW_SECONDS = 300
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
    "image_validation_in_flight",
    "search_queue_in_flight",
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
    ("search_queue", "search_queue_wait_events", "search_queue_total_wait_ms"),
    ("image_queue", "image_queue_wait_events", "image_queue_total_wait_ms"),
]
API_SCALE_REQUIRED_SERVER_METRIC_DELTAS = [
    "server_metrics.delta.search_events",
    "server_metrics.delta.image_search_events",
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
    "server_metrics.delta.image_validation_wait_events",
    "server_metrics.delta.image_validation_wait_timeouts",
    "server_metrics.delta.image_validation_total_wait_ms",
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
SEARCH_ENGINE_REQUIRED_DELTA_SERVER_METRIC_FIELDS = [
    "engine_search_attempts",
    "engine_adaptive_refetches",
    "engine_adaptive_refetch_searches",
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
    "image_validation_wait_timeouts",
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
BACKEND_QWEN_SERVER_METRIC_DELTAS = [
    f"server_metrics.delta.{field}"
    for field in [
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


def external_embedding_backend(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"qwen", "gemini"}
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
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b((?:password|passwd|pwd|api[_-]?key|admin[_-]?key|x[_-]?api[_-]?key|"
    r"x[_-]?admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"client[_-]?secret|connection[_-]?string|secret|token)\s*[:=]\s*)([^;\s,&]+)",
    re.IGNORECASE,
)
URL_CREDENTIAL_PATTERN = re.compile(r"([a-z][a-z0-9+.-]*://)([^:/\s]+):([^@\s]+)@", re.IGNORECASE)
MSSQL_FAILURE_DETAIL_KEYS = (
    "connection_string_configured",
    "query_configured",
    "query_fingerprint",
    "error_type",
    "sample_size",
    "product_id_column",
    "updated_at_column",
    "mall_config_path",
)
CHECK_REPORT_ARGS = {
    "api_smoke": "api_smoke_report",
    "mssql_export": "mssql_export_report",
    "poc_dataset": "poc_dataset_report",
    "mssql_view": "mssql_view_report",
    "image_urls": "image_url_report",
    "quality_report": "quality_report",
    "csv_poc_index": "csv_index_report",
    "mall_config_build": "mall_config_build_report",
    "mall_config": "mall_config_report",
    "marqo_resource": "marqo_resource_report",
    "server_preflight": "server_preflight_report",
    "env_preflight": "env_check_report",
    "load_text_100_concurrent": "load_text_report",
    "load_image_30_concurrent": "load_image_report",
    "load_mixed_30_concurrent": "load_mixed_report",
    "load_mixed_traffic_850_active_users": "load_mixed_traffic_report",
    "api_scale_comparison": "api_scale_report",
    "representative_mall_sites": "representative_sites_report",
    "security": "security_report",
}
COMMAND_HINTS = {
    "api_smoke": (
        "python scripts/api_smoke_test.py --base-url https://ai-search.haeorumgift.com "
        "--mall-id <mall_id> --api-key <public_api_key> --origin <mall_origin> "
        "--admin-key <admin_key> --mall-config <mall_config.json> --expect-click-rate-limit "
        "--click-rate-limit-probe-count <configured_click_rate_limit_plus_1> --output {path}"
    ),
    "mssql_export": (
        "python scripts/mssql_export_csv.py --connection-string <readonly_connection_string> "
        "--query <readonly_select_query> --output-csv <products_csv> --mall-config <mall_config.json> "
        "--fetch-size 1000 --report-output {path}"
    ),
    "poc_dataset": (
        "python scripts/poc_dataset_builder.py --csv <products_csv> --output-csv <poc_products_csv> "
        "--report-output {path} --target-size 300 --min-products 300 --min-per-category 10"
    ),
    "mssql_view": (
        "python scripts/mssql_view_check.py --connection-string <readonly_connection_string> "
        "--query <readonly_select_query> --output {path}"
    ),
    "image_urls": (
        "python scripts/image_url_check.py --csv <products_csv> --limit 100 --concurrency 5 "
        "--min-dimension 16 --require-https --output {path}"
    ),
    "quality_report": (
        "python scripts/quality_report.py --csv <poc_products_csv> --strict "
        "--engine marqo --index-name <index_name> --marqo-url <marqo_url> "
        "--cases <quality_cases_file> --mall-config <mall_config.json> --min-products 300 "
        "--max-text-ms 3000 --max-image-ms 5000 --max-mixed-ms 5000 "
        "--json-output {path} --markdown-output <quality_report.md>"
    ),
    "csv_poc_index": (
        "python scripts/csv_index.py --csv <poc_products_csv> --engine marqo "
        "--index-name <index_name> --marqo-url <marqo_url> --mode reindex "
        "--validate-images --output {path} --markdown-output <csv_index.md>"
    ),
    "mall_config_build": (
        "python scripts/mall_config_builder.py --csv <malls_csv> --output <mall_config.json> "
        "--report-output {path} --min-count 1700 --sort-by-mall-id"
    ),
    "mall_config": "python scripts/mall_config_check.py --config <mall_config.json> --min-count 1700 --output {path}",
    "marqo_resource": (
        "python scripts/marqo_resource_check.py --marqo-url <marqo_url> "
        "--index <index_name> --container marqo-api --storage-container vespa "
        "--storage-path /opt/vespa/var --expected-model <marqo_model> --embedding-backend gemini "
        "--gemini-model gemini-embedding-2 --gemini-embedding-url <gemini_embedding_url> "
        "--gemini-embedding-dimensions 1536 "
        "--max-cpu-percent 90 --max-memory-percent 85 --max-storage-percent 85 "
        "--min-storage-available-gb 10 --output {path}"
    ),
    "server_preflight": (
        "python scripts/server_preflight_check.py --role api --require-docker "
        "--require-compose --require-pyodbc --expected-odbc-driver \"ODBC Driver 18 for SQL Server\" --output {path}"
    ),
    "env_preflight": (
        "python scripts/env_check.py --env-file /etc/haeorum-ai-search/haeorum-ai-search.env "
        "--role api --api-server-count <api_server_count> --output {path} --markdown-output <env_check.md>"
    ),
    "load_text_100_concurrent": (
        "python scripts/load_test.py --base-url https://ai-search.haeorumgift.com --mall-id <mall_id> "
        "--api-key <public_api_key> --origin <mall_origin> --admin-key <admin_key> --mall-config <mall_config.json> "
        "--mode text --requests 100 --concurrency 100 --p95-ms 3000 --p99-ms 4800 "
        "--request-timeout-seconds 10 --max-server-wait-avg-ms 600 --min-rps 8.3 --output {path} "
        "--markdown-output <load_text.md>"
    ),
    "load_image_30_concurrent": (
        "python scripts/load_test.py --base-url https://ai-search.haeorumgift.com --mall-id <mall_id> "
        "--api-key <public_api_key> --origin <mall_origin> --admin-key <admin_key> --mall-config <mall_config.json> "
        "--mode image --image-file <reference_image_file> "
        "--additional-image-file <reference_image_file_2> --additional-image-file <reference_image_file_3> "
        "--requests 30 --concurrency 30 "
        "--p95-ms 5000 --p99-ms 8000 --request-timeout-seconds 16 --max-server-wait-avg-ms 1000 --min-rps 1.5 --output {path} "
        "--markdown-output <load_image.md>"
    ),
    "load_mixed_30_concurrent": (
        "python scripts/load_test.py --base-url https://ai-search.haeorumgift.com --mall-id <mall_id> "
        "--api-key <public_api_key> --origin <mall_origin> --admin-key <admin_key> --mall-config <mall_config.json> "
        "--mode mixed --image-file <reference_image_file> "
        "--additional-image-file <reference_image_file_2> --additional-image-file <reference_image_file_3> "
        "--requests 30 --concurrency 30 "
        "--p95-ms 5000 --p99-ms 8000 --request-timeout-seconds 16 --max-server-wait-avg-ms 1000 --min-rps 1.5 --output {path} "
        "--markdown-output <load_mixed.md>"
    ),
    "load_mixed_traffic_850_active_users": (
        "python scripts/load_test.py --base-url https://ai-search.haeorumgift.com --mall-id <mall_id> "
        "--api-key <public_api_key> --origin <mall_origin> --admin-key <admin_key> --mall-config <mall_config.json> "
        "--scenario mixed-traffic --active-users 850 --requests 850 --concurrency 100 "
        "--mall-sample-size 50 --mall-sample-strategy spread --p95-ms 5000 --p99-ms 8000 --request-timeout-seconds 16 --max-server-wait-avg-ms 1000 --min-rps 5.0 "
        "--image-file <reference_image_file> "
        "--additional-image-file <reference_image_file_2> --additional-image-file <reference_image_file_3> "
        "--output {path} --markdown-output <load_mixed_traffic.md>"
    ),
    "api_scale_comparison": (
        "python scripts/load_compare.py --single-report <load_mixed_traffic_1_api.json> "
        "--multi-report <load_mixed_traffic_2_api.json> --max-multi-p99-regression-percent 25 "
        "--output {path} --markdown-output <api_scale.md>"
    ),
    "representative_mall_sites": (
        "python scripts/representative_site_check.py --sites <representative_sites.config.json> "
        "--api-base-url https://ai-search.haeorumgift.com --image-file <reference_image_file> "
        "--output {path} --markdown-output <representative_sites.md>"
    ),
    "security": (
        "python scripts/security_check.py --base-url https://ai-search.haeorumgift.com "
        "--mssql-ip-restricted --nginx-config /etc/nginx/sites-enabled/haeorum-ai-search.conf "
        "--systemd-service /etc/systemd/system/haeorum-ai-search.service "
        "--sync-systemd-service /etc/systemd/system/haeorum-ai-sync.service "
        "--reindex-systemd-service /etc/systemd/system/haeorum-ai-reindex.service "
        "--reindex-systemd-timer /etc/systemd/system/haeorum-ai-reindex.timer "
        "--logrotate-config /etc/logrotate.d/haeorum-ai-search --sync-alerting-configured "
        "--output {path} --markdown-output <security.md>"
    ),
}
REQUIRED_REPRESENTATIVE_SITE_CHECKS = {
    "site_config",
    "desktop_page",
    "mobile_page",
    "widget_init",
    "widget_script_asset",
    "result_image_csp",
    "text_search",
    "image_search",
    "mixed_search",
    "text_category_refetch",
    "text_product_url_rule",
    "text_all_product_url_rules",
    "text_detail_url",
    "text_click_log",
    "image_product_url_rule",
    "image_all_product_url_rules",
    "image_detail_url",
    "image_click_log",
    "mixed_product_url_rule",
    "mixed_all_product_url_rules",
    "mixed_detail_url",
    "mixed_click_log",
}
REPRESENTATIVE_SEARCH_CHECKS = {"text_search", "image_search", "mixed_search"}
REPRESENTATIVE_PRODUCT_URL_RULE_CHECKS = {
    "text_product_url_rule",
    "image_product_url_rule",
    "mixed_product_url_rule",
}
REQUIRED_API_SMOKE_RESPONSE_CONTRACT_CHECKS = {
    "text_search",
    "site_id_search",
    "image_search",
    "multipart_image_search",
    "site_id_multipart_image_search",
    "mixed_search",
}
REQUIRED_API_SMOKE_CLICK_PRODUCT_URL_CHECKS = {
    "click_log",
    "site_id_click_log",
}


def load_json(path: str) -> dict[str, Any] | None:
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(read_json_text(target))
    except UnicodeError as exc:
        return {"ok": False, "parse_error": f"invalid JSON encoding: {exc}"}
    except json.JSONDecodeError:
        return {"ok": False, "parse_error": "invalid JSON"}
    return data if isinstance(data, dict) else {"ok": False, "parse_error": "JSON root must be an object"}


def read_json_text(path: Path) -> str:
    last_error: UnicodeError | None = None
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return path.read_text(encoding="utf-8")


def evidence_item(
    name: str,
    path: str,
    checker: EvidenceCheck,
    command_hint: str = "",
    max_evidence_age_days: int = DEFAULT_MAX_EVIDENCE_AGE_DAYS,
) -> dict[str, Any]:
    data = load_json(path)
    if data is None:
        return {
            "name": name,
            "status": "missing",
            "ok": False,
            "path": path or None,
            "message": "evidence report is missing",
            "command_hint": command_hint,
        }
    if is_simulated_evidence(data):
        details = {
            "simulation_only": data.get("simulation_only"),
            "simulation_marker": data.get("simulation_marker"),
        }
        item = {
            "name": name,
            "status": "failed",
            "ok": False,
            "path": path,
            "message": "simulated evidence is not accepted for operational readiness",
            "details": details,
        }
        if command_hint:
            item["command_hint"] = command_hint
        return item
    freshness = evidence_freshness(data, max_evidence_age_days)
    ok, message, details = checker(data)
    details["evidence_freshness"] = freshness
    if not freshness["ok"]:
        ok = False
        message = (
            "evidence report generated_at is missing, invalid, future-dated, or stale"
            if message == "report ok=true"
            else f"{message}; evidence report generated_at is missing, invalid, future-dated, or stale"
        )
    item = {
        "name": name,
        "status": "passed" if ok else "failed",
        "ok": ok,
        "path": path,
        "message": message,
        "details": details,
    }
    if not ok and command_hint:
        item["command_hint"] = command_hint
    return item


def is_simulated_evidence(data: dict[str, Any]) -> bool:
    marker = str(data.get("simulation_marker") or "").strip()
    return data.get("simulation_only") is True or marker == SIMULATION_EVIDENCE_MARKER


def evidence_freshness(
    data: dict[str, Any],
    max_age_days: int = DEFAULT_MAX_EVIDENCE_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    max_age_days = int(max_age_days or 0)
    generated_at = str(data.get("generated_at") or "").strip()
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    details: dict[str, Any] = {
        "ok": True,
        "generated_at": generated_at or None,
        "checked_at": checked_at.isoformat(),
        "max_age_days": max_age_days,
        "future_skew_seconds": EVIDENCE_FUTURE_SKEW_SECONDS,
        "age_seconds": None,
        "problems": [],
    }
    if max_age_days <= 0:
        return details
    if not generated_at:
        details["ok"] = False
        details["problems"] = ["generated_at"]
        return details
    try:
        parsed = parse_evidence_datetime(generated_at)
    except ValueError:
        details["ok"] = False
        details["problems"] = ["generated_at_format"]
        return details
    age = checked_at - parsed
    details["age_seconds"] = round(age.total_seconds(), 1)
    problems: list[str] = []
    if parsed - checked_at > timedelta(seconds=EVIDENCE_FUTURE_SKEW_SECONDS):
        problems.append("generated_at_future")
    if age > timedelta(days=max_age_days):
        problems.append("generated_at_stale")
    details["ok"] = not problems
    details["problems"] = problems
    return details


def parse_evidence_datetime(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("invalid generated_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fingerprint_problems(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["missing"]
    problems = []
    digest = str(value.get("digest") or "").strip().lower()
    if value.get("algorithm") != "sha256":
        problems.append("algorithm")
    if value.get("exists") is not True:
        problems.append("exists")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        problems.append("digest")
    if parse_int_value(value.get("size_bytes"), 0) <= 0:
        problems.append("size_bytes")
    return problems


def query_fingerprint_problems(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["missing"]
    problems = []
    digest = str(value.get("digest") or "").strip().lower()
    if value.get("algorithm") != "sha256":
        problems.append("algorithm")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        problems.append("digest")
    if parse_int_value(value.get("normalized_length"), 0) <= 0:
        problems.append("normalized_length")
    return problems


def case_image_fingerprint_problems(value: Any, expected_count: int) -> list[str]:
    if expected_count <= 0:
        return []
    if not isinstance(value, list):
        return ["missing"]
    problems: list[str] = []
    if len(value) < expected_count:
        problems.append("count")
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    seen_digests: set[str] = set()
    for index, entry in enumerate(value):
        prefix = str(index)
        if not isinstance(entry, dict):
            problems.append(prefix + ".entry")
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            problems.append(prefix + ".name")
        elif name in seen_names:
            problems.append(prefix + ".name_duplicate")
        if name:
            seen_names.add(name)
        if str(entry.get("query_type") or "") not in {"image", "text_image"}:
            problems.append(prefix + ".query_type")
        if entry.get("image_source") != "case_file":
            problems.append(prefix + ".image_source")
        fingerprint = entry.get("fingerprint")
        fingerprint_fields = fingerprint_problems(fingerprint)
        if isinstance(fingerprint, dict) and not str(fingerprint.get("path") or "").strip():
            fingerprint_fields.append("path")
        problems.extend(prefix + ".fingerprint." + problem for problem in sorted(set(fingerprint_fields)))
        if fingerprint_fields or not isinstance(fingerprint, dict):
            continue
        path = str(fingerprint.get("path") or "").strip()
        digest = str(fingerprint.get("digest") or "").strip().lower()
        if path in seen_paths:
            problems.append(prefix + ".fingerprint.path_duplicate")
        elif path:
            seen_paths.add(path)
        if digest in seen_digests:
            problems.append(prefix + ".fingerprint.digest_duplicate")
        elif digest:
            seen_digests.add(digest)
    return sorted(set(problems))


def fingerprint_digest(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("digest") or "").strip().lower()


def evidence_path_identity(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while "//" in text and not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.IGNORECASE):
        text = text.replace("//", "/")
    return text.rstrip("/")


def fingerprint_path_problems(value: Any, expected_path: Any) -> list[str]:
    expected = evidence_path_identity(expected_path)
    if not expected:
        return ["expected_path"]
    if not isinstance(value, dict):
        return ["missing"]
    actual = evidence_path_identity(value.get("path"))
    if not actual:
        return ["path"]
    if actual != expected:
        return ["path_mismatch"]
    return []


def resolve_report_path(args: argparse.Namespace, arg_name: str) -> str:
    explicit = str(getattr(args, arg_name, "") or "").strip()
    if explicit:
        return normalize_report_path(explicit)
    evidence_dir = str(getattr(args, "evidence_dir", "") or "").strip()
    if not evidence_dir:
        return ""
    return normalize_report_path(Path(evidence_dir) / DEFAULT_EVIDENCE_FILENAMES[arg_name])


def normalize_report_path(value: str | Path) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else path.resolve())


def command_hint(name: str, path: str) -> str:
    filename = DEFAULT_EVIDENCE_FILENAMES.get(CHECK_REPORT_ARGS.get(name, ""), name + ".json")
    fallback_path = f"<evidence_dir>/{filename}"
    return COMMAND_HINTS.get(name, "").format(path=quote_command_value(path or fallback_path))


def command_hint_for_item(
    item: dict[str, Any],
    evidence_output_dir: str | Path | None = None,
    shell: str = "powershell",
) -> str:
    output_dir = str(evidence_output_dir or "").strip()
    if output_dir:
        name = str(item.get("name") or "")
        arg_name = CHECK_REPORT_ARGS.get(name, "")
        filename = DEFAULT_EVIDENCE_FILENAMES.get(arg_name, name + ".json")
        command = command_hint(name, join_evidence_output_path(output_dir, filename, shell=shell))
        return replace_derived_output_placeholders(command, output_dir, shell=shell)
    if shell == "bash":
        name = str(item.get("name") or "")
        path = normalize_bash_path(str(item.get("path") or ""))
        if name and path:
            return command_hint(name, path)
    return str(item.get("command_hint") or "").strip()


def join_evidence_output_path(evidence_dir: str | Path, filename: str, shell: str = "powershell") -> str:
    if shell == "bash":
        text = normalize_bash_path(str(evidence_dir).rstrip("/\\"))
        return f"{text}/{filename}" if text else filename
    text = str(evidence_dir).rstrip("/\\")
    if not text:
        return filename
    if text.startswith("/") and not Path(text).drive:
        return str(PurePosixPath(text) / filename)
    return str(Path(text) / filename)


def replace_derived_output_placeholders(command: str, evidence_dir: str | Path, shell: str = "powershell") -> str:
    result = command
    for placeholder, filename in DERIVED_OUTPUT_PLACEHOLDERS.items():
        if placeholder in result:
            path = join_evidence_output_path(evidence_dir, filename, shell=shell)
            result = result.replace(placeholder, quote_command_value(path))
    return result


def quote_command_value(value: str) -> str:
    return '"' + str(value).replace('"', '`"') + '"'


def check_report_ok(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    ok = data.get("ok") is True
    return ok, "report ok=true" if ok else "report ok is not true", compact_details(data)


def is_local_operational_host(hostname: str | None) -> bool:
    return is_non_public_host(hostname)


OPERATIONAL_URL_PROBLEM_NAMES = {
    ("base_url", "https"): "base_url_https",
    ("base_url", "non_local"): "base_url_non_local",
    ("origin", "https"): "origin_https",
    ("origin", "non_local"): "origin_non_local",
}
API_SCALE_TARGET_PROBLEMS = {
    "base_url": "comparison.base_url",
    "mall_id": "comparison.mall_id",
    "origin": "comparison.origin",
}
API_SCALE_RUNTIME_IDENTITY_FIELDS = [
    "engine_backend",
    "engine_index",
    "marqo_model",
    "embedding_backend",
    "rate_limit_backend",
    "rate_limit_redis_enabled",
    "cache_backend",
    "cache_redis_enabled",
    "cache_ttl_seconds",
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
API_SCALE_QWEN_RUNTIME_IDENTITY_FIELDS = [
    "qwen_model",
    "qwen_embedding_dimensions",
    "qwen_query_vector_runtime_max_entries",
    "qwen_query_vector_runtime_text_max_entries",
    "qwen_query_vector_runtime_image_max_entries",
    "qwen_query_vector_mixed_parallelism",
]


def operational_url_problem_name(field: str, problem: str) -> str:
    return OPERATIONAL_URL_PROBLEM_NAMES.get((field, problem), f"{field}_{problem}")


def operational_https_url_problems(field: str, value: Any, origin_only: bool = False) -> list[str]:
    text = str(value or "").strip()
    problems: list[str] = []
    if not text:
        return [field]
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        problems.append(operational_url_problem_name(field, "format"))
        return problems
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError:
        problems.append(operational_url_problem_name(field, "port"))
        return problems
    if parsed.scheme.lower() != "https":
        problems.append(operational_url_problem_name(field, "https"))
    if not parsed.netloc or not parsed.hostname:
        problems.append(operational_url_problem_name(field, "host"))
    if parsed.username is not None or parsed.password is not None:
        problems.append(operational_url_problem_name(field, "credentials"))
    if parsed.hostname and is_local_operational_host(parsed.hostname):
        problems.append(operational_url_problem_name(field, "non_local"))
    if parsed.params or parsed.query or parsed.fragment:
        problems.append(operational_url_problem_name(field, "clean_url"))
    if origin_only and parsed.path not in {"", "/"}:
        problems.append(operational_url_problem_name(field, "origin_only"))
    return sorted(set(problems))


def marqo_url_evidence_problems(field: str, value: Any) -> list[str]:
    text = str(value or "").strip()
    problems: list[str] = []
    if not text:
        return [field]
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        return [f"{field}.format"]
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError:
        return [f"{field}.port"]
    if parsed.scheme.lower() not in {"http", "https"}:
        problems.append(f"{field}.scheme")
    if not parsed.netloc or not parsed.hostname:
        problems.append(f"{field}.host")
    if parsed.username is not None or parsed.password is not None:
        problems.append(f"{field}.credentials")
    if parsed.hostname:
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and (address.is_unspecified or address.is_link_local):
            problems.append(f"{field}.host")
    if parsed.params or parsed.query or parsed.fragment:
        problems.append(f"{field}.clean_url")
    if parsed.path not in {"", "/"}:
        problems.append(f"{field}.path")
    return sorted(set(problems))


def check_api_smoke_response_mall_identity(
    check: dict[str, Any],
    report_mall_id: Any,
) -> list[str]:
    name = str(check.get("name") or "unknown")
    expected = str(check.get("expected_mall_id") or "").strip()
    report_mall = str(report_mall_id or "").strip()
    meta_mall = str(check.get("meta_mall_id") or "").strip()
    result_mall_ids = check.get("result_mall_ids")
    result_values = [
        str(value or "").strip()
        for value in result_mall_ids
        if str(value or "").strip()
    ] if isinstance(result_mall_ids, list) else []

    problems = []
    if not report_mall:
        problems.append(f"{name}.report_mall_id")
    if not expected:
        problems.append(f"{name}.expected_mall_id")
    elif report_mall and expected != report_mall:
        problems.append(f"{name}.expected_mall_id")
    if not meta_mall:
        problems.append(f"{name}.meta_mall_id")
    elif expected and meta_mall != expected:
        problems.append(f"{name}.meta_mall_id")
    if not isinstance(result_mall_ids, list) or not result_values:
        problems.append(f"{name}.result_mall_ids")
    elif expected and any(value != expected for value in result_values):
        problems.append(f"{name}.result_mall_ids")
    return problems


def check_api_smoke_response_product_url_prefix(check: dict[str, Any]) -> list[str]:
    name = str(check.get("name") or "unknown")
    expected_prefix = str(check.get("expected_product_url_prefix") or "").strip()
    result_product_urls = check.get("result_product_urls")
    result_values = [
        str(value or "").strip()
        for value in result_product_urls
        if str(value or "").strip()
    ] if isinstance(result_product_urls, list) else []
    mismatch_count = parse_int_value(check.get("product_url_prefix_mismatch_count"), -1)

    problems = []
    if not expected_prefix:
        problems.append(f"{name}.expected_product_url_prefix")
    if not isinstance(result_product_urls, list) or not result_values:
        problems.append(f"{name}.result_product_urls")
    if mismatch_count != 0:
        problems.append(f"{name}.product_url_prefix_mismatch_count")
    return problems


def check_api_smoke_click_product_url_prefix(check: dict[str, Any]) -> list[str]:
    name = str(check.get("name") or "unknown")
    expected_prefix = str(check.get("expected_product_url_prefix") or "").strip()
    click_product_url = str(check.get("click_product_url") or "").strip()
    problems = []
    if not expected_prefix:
        problems.append(f"{name}.expected_product_url_prefix")
    if not click_product_url:
        problems.append(f"{name}.click_product_url")
    if check.get("click_product_url_prefix_matches") is not True:
        problems.append(f"{name}.click_product_url_prefix_matches")
    if check.get("click_product_url_contains_product_id") is not True:
        problems.append(f"{name}.click_product_url_contains_product_id")
    return problems


def check_api_smoke(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    required_checks = {
        "health",
        "cors_preflight",
        "invalid_cors_preflight_rejected",
        "click_log_cors_preflight",
        "invalid_click_log_cors_preflight_rejected",
        "text_search",
        "site_id_search",
        "conflicting_site_id_rejected",
        "image_search",
        "oversized_json_image_rejected",
        "small_json_image_rejected",
        "multipart_image_search",
        "site_id_multipart_image_search",
        "conflicting_multipart_site_id_rejected",
        "unsupported_multipart_field_rejected",
        "invalid_multipart_image_rejected",
        "damaged_multipart_image_rejected",
        "small_multipart_image_rejected",
        "oversized_multipart_image_rejected",
        "malformed_multipart_rejected",
        "mixed_search",
        "openapi_click_rate_limit_documented",
        "invalid_api_key_rejected",
        "query_api_key_rejected",
        "query_api_key_alias_rejected",
        "query_admin_key_alias_rejected",
        "empty_query_api_key_rejected",
        "body_api_key_rejected",
        "body_api_key_alias_rejected",
        "body_admin_key_alias_rejected",
        "multipart_body_api_key_rejected",
        "multipart_body_api_key_alias_rejected",
        "multipart_body_admin_key_alias_rejected",
        "invalid_origin_rejected",
        "invalid_search_payload_rejected",
        "unsupported_json_field_rejected",
        "invalid_domain_filter_rejected",
        "malformed_search_json_rejected",
        "click_log",
        "site_id_click_log",
        "conflicting_click_site_id_rejected",
        "invalid_click_api_key_rejected",
        "query_click_api_key_rejected",
        "query_click_api_key_alias_rejected",
        "query_click_admin_key_alias_rejected",
        "empty_query_click_api_key_rejected",
        "body_click_api_key_rejected",
        "body_click_api_key_alias_rejected",
        "body_click_admin_key_alias_rejected",
        "invalid_click_origin_rejected",
        "invalid_click_payload_rejected",
        "unsupported_click_field_rejected",
        "unsafe_click_product_url_rejected",
        "foreign_click_product_url_rejected",
        "click_product_url_template_prefix_mismatch_rejected",
        "click_product_url_product_id_mismatch_rejected",
        "malformed_click_json_rejected",
        "click_log_rate_limited",
        "sync_status",
        "search_log",
        "sync_log",
        "error_log",
        "sensitive_log_redaction",
        "search_insights",
        "metrics",
        "prometheus_metrics",
        "invalid_admin_key_rejected",
        "admin_query_key_alias_rejected",
        "admin_mutation_endpoints_protected",
    }
    checks = data.get("checks", [])
    by_name = {str(check.get("name")): check for check in checks if isinstance(check, dict)}
    missing = sorted(required_checks - set(by_name))
    failed = sorted(name for name, check in by_name.items() if name in required_checks and check.get("ok") is not True)
    missing_response_contract = sorted(
        name
        for name in REQUIRED_API_SMOKE_RESPONSE_CONTRACT_CHECKS
        if name in by_name and by_name[name].get("response_contract_ok") is not True
    )
    non_marqo_response_engine = sorted(
        name
        for name in REQUIRED_API_SMOKE_RESPONSE_CONTRACT_CHECKS
        if name in by_name
        and by_name[name].get("response_contract_ok") is True
        and str(by_name[name].get("engine") or "").strip().lower() != "marqo"
    )
    response_mall_identity_problems = sorted(
        problem
        for name in REQUIRED_API_SMOKE_RESPONSE_CONTRACT_CHECKS
        if name in by_name and by_name[name].get("response_contract_ok") is True
        for problem in check_api_smoke_response_mall_identity(by_name[name], data.get("mall_id"))
    )
    response_product_url_prefix_problems = sorted(
        problem
        for name in REQUIRED_API_SMOKE_RESPONSE_CONTRACT_CHECKS
        if name in by_name and by_name[name].get("response_contract_ok") is True
        for problem in check_api_smoke_response_product_url_prefix(by_name[name])
    )
    click_product_url_prefix_problems = sorted(
        problem
        for name in REQUIRED_API_SMOKE_CLICK_PRODUCT_URL_CHECKS
        if name in by_name and by_name[name].get("ok") is True
        for problem in check_api_smoke_click_product_url_prefix(by_name[name])
    )
    metrics_check = by_name.get("metrics") or {}
    metrics_engine_ok = (
        metrics_check.get("ok") is True
        and metrics_check.get("engine_ok") is True
        and str(metrics_check.get("engine_backend") or "").strip().lower() == "marqo"
    )
    metrics_search_queue_ok = (
        metrics_check.get("ok") is True
        and metrics_check.get("search_queue_enabled") is True
        and is_number(metrics_check.get("search_queue_max_concurrency"))
        and int(metrics_check.get("search_queue_max_concurrency") or 0) > 0
    )
    metrics_image_queue_ok = (
        metrics_check.get("ok") is True
        and metrics_check.get("image_queue_enabled") is True
        and is_number(metrics_check.get("image_queue_max_concurrency"))
        and int(metrics_check.get("image_queue_max_concurrency") or 0) > 0
    )
    sync_status_check = by_name.get("sync_status") or {}
    sync_status_index = str(sync_status_check.get("sync_status_index") or "").strip()
    sync_status_engine_ok = (
        sync_status_check.get("ok") is True
        and sync_status_check.get("sync_status_engine_ok") is True
        and str(sync_status_check.get("sync_status_engine") or "").strip().lower() == "marqo"
    )
    sync_status_index_ok = (
        sync_status_check.get("ok") is True
        and sync_status_check.get("sync_status_index_ok") is True
        and bool(sync_status_index)
    )
    url_problems = (
        operational_https_url_problems("base_url", data.get("base_url"))
        + operational_https_url_problems("origin", data.get("origin"), origin_only=True)
    )
    target_validation = data.get("target_validation") or {}
    target_validation_matches = (
        target_validation.get("base_url") == data.get("base_url")
        and (target_validation.get("origin") or None) == (data.get("origin") or None)
    )
    target_validation_ok = target_validation.get("ok") is True and target_validation_matches
    ok = (
        data.get("ok") is True
        and not missing
        and not failed
        and not missing_response_contract
        and not non_marqo_response_engine
        and not response_mall_identity_problems
        and not response_product_url_prefix_problems
        and not click_product_url_prefix_problems
        and metrics_engine_ok
        and metrics_search_queue_ok
        and metrics_image_queue_ok
        and sync_status_engine_ok
        and sync_status_index_ok
        and not url_problems
        and target_validation_ok
    )
    return (
        ok,
        "API smoke checks passed" if ok else "API smoke report is incomplete or failed",
        {
            "base_url": data.get("base_url"),
            "mall_id": data.get("mall_id"),
            "origin": data.get("origin"),
            "missing_checks": missing,
            "failed_checks": failed,
            "missing_response_contract_evidence": missing_response_contract,
            "non_marqo_response_engine": non_marqo_response_engine,
            "response_mall_identity_problems": response_mall_identity_problems,
            "response_product_url_prefix_problems": response_product_url_prefix_problems,
            "click_product_url_prefix_problems": click_product_url_prefix_problems,
            "metrics_engine_ok": metrics_engine_ok,
            "metrics_engine_backend": metrics_check.get("engine_backend"),
            "metrics_search_queue_ok": metrics_search_queue_ok,
            "metrics_search_queue_max_concurrency": metrics_check.get("search_queue_max_concurrency"),
            "metrics_image_queue_ok": metrics_image_queue_ok,
            "metrics_image_queue_max_concurrency": metrics_check.get("image_queue_max_concurrency"),
            "sync_status_engine_ok": sync_status_engine_ok,
            "sync_status_engine": sync_status_check.get("sync_status_engine"),
            "sync_status_index_ok": sync_status_index_ok,
            "sync_status_index": sync_status_index or None,
            "url_problems": url_problems,
            "target_validation_ok": target_validation_ok,
            "target_validation_matches": target_validation_matches,
            "target_validation_error": target_validation.get("error"),
        },
    )


def check_mssql_view(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    columns = data.get("column_report") or {}
    sample = data.get("sample_report") or {}
    sample_quality = data.get("sample_quality_report") or {}
    permissions = data.get("permission_report") or {}
    query_configured = data.get("query_configured") is True
    query_fingerprint = data.get("query_fingerprint")
    query_fingerprint_problem_list = query_fingerprint_problems(query_fingerprint)
    sample_size = parse_int_value(data.get("sample_size"), 0)
    parsed_rows = parse_int_value(sample.get("parsed_rows"), 0)
    sample_rows = parse_int_value(sample.get("sample_rows"), parsed_rows)
    minimum_sample_rows = 20
    problems = []
    if not query_configured:
        problems.append("query_configured")
    if query_fingerprint_problem_list:
        problems.append("query_fingerprint")
    if sample_size < minimum_sample_rows:
        problems.append("sample_size")
    if parsed_rows < minimum_sample_rows:
        problems.append("parsed_rows")
    if sample_rows < minimum_sample_rows:
        problems.append("sample_rows")
    ok = (
        data.get("ok") is True
        and columns.get("ok") is True
        and parsed_rows >= minimum_sample_rows
        and sample_quality.get("ok") is True
        and permissions.get("checked") is True
        and permissions.get("ok") is True
        and not problems
    )
    details = {
        "query_configured": query_configured,
        "query_fingerprint": query_fingerprint,
        "query_fingerprint_problems": query_fingerprint_problem_list,
        "sample_size": sample_size,
        "sample_rows": sample_rows,
        "minimum_sample_rows": minimum_sample_rows,
        "column_ok": columns.get("ok"),
        "missing_required_columns": columns.get("missing_required_columns"),
        "available_columns": columns.get("columns"),
        "noncanonical_required_aliases": columns.get("noncanonical_required_aliases"),
        "ambiguous_required_aliases": columns.get("ambiguous_required_aliases"),
        "suggested_select_aliases": columns.get("suggested_select_aliases"),
        "parsed_rows": parsed_rows,
        "active_rows": sample.get("active_rows"),
        "active_missing_product_url_rows": sample.get("active_missing_product_url_rows"),
        "active_missing_mall_id_rows": sample.get("active_missing_mall_id_rows"),
        "active_unsafe_image_url_rows": sample.get("active_unsafe_image_url_rows"),
        "active_non_https_image_url_rows": sample.get("active_non_https_image_url_rows"),
        "active_unsafe_product_url_rows": sample.get("active_unsafe_product_url_rows"),
        "active_product_url_product_id_mismatch_rows": sample.get("active_product_url_product_id_mismatch_rows"),
        "active_product_url_product_id_mismatch_product_ids": (
            sample.get("active_product_url_product_id_mismatch_product_ids") or []
        ),
        "future_updated_at_rows": sample.get("future_updated_at_rows"),
        "future_updated_at_product_ids": sample.get("future_updated_at_product_ids") or [],
        "updated_at_reference_now": sample.get("updated_at_reference_now"),
        "updated_at_max_future_skew_seconds": sample.get("updated_at_max_future_skew_seconds"),
        "parse_errors": sample.get("parse_errors"),
        "sample_quality_ok": sample_quality.get("ok"),
        "sample_quality_problems": sample_quality.get("problems"),
        "permission_checked": permissions.get("checked") is True,
        "permission_ok": permissions.get("ok"),
        "dangerous_roles": permissions.get("dangerous_roles"),
        "dangerous_permissions": permissions.get("dangerous_permissions"),
        "problems": problems,
    }
    details.update(mssql_failure_details(data))
    return (
        ok,
        "MSSQL View sample parsed with read-only permissions"
        if ok
        else "MSSQL View report failed, is missing required columns, sampled too few rows, or account is not read-only",
        details,
    )


def check_mssql_export(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    rows_read = int(data.get("rows_read", 0) or 0)
    exported_products = int(data.get("exported_products", 0) or 0)
    active_products = int(data.get("active_products", 0) or 0)
    inactive_products = parse_int_value(data.get("inactive_products"), -1)
    source_deletion_signal_count = parse_int_value(data.get("source_deletion_signal_count"), inactive_products)
    source_deletion_signal_ok = data.get("source_deletion_signal_ok")
    parse_errors = data.get("parse_errors") or []
    duplicate_product_ids = data.get("duplicate_product_ids") or []
    output_csv = data.get("output_csv")
    output_csv_fingerprint = data.get("output_csv_fingerprint") or {}
    output_csv_fingerprint_path_problems = fingerprint_path_problems(output_csv_fingerprint, output_csv)
    query_configured = data.get("query_configured") is True
    query_fingerprint = data.get("query_fingerprint")
    query_fingerprint_problem_list = query_fingerprint_problems(query_fingerprint)
    limit = parse_int_value(data.get("limit"), -1)
    since_configured = data.get("since_configured")
    fetch_size = parse_int_value(data.get("fetch_size"), 0)
    fetch_batches = parse_int_value(data.get("fetch_batches"), -1)
    max_fetch_batch_rows = parse_int_value(data.get("max_fetch_batch_rows"), -1)
    batched_fetch = data.get("batched_fetch")
    streaming_parse = data.get("streaming_parse")
    streamed_product_csv = data.get("streamed_product_csv")
    retained_product_rows = parse_int_value(data.get("retained_product_rows"), -1)
    csv_rows_written = parse_int_value(data.get("csv_rows_written"), -1)
    minimum_active_products = 300
    missing_updated_at_count = parse_int_value(data.get("missing_updated_at_count"), -1)
    invalid_updated_at_count = parse_int_value(data.get("invalid_updated_at_count"), -1)
    future_updated_at_count = parse_int_value(data.get("future_updated_at_count"), -1)
    active_missing_category_count = parse_int_value(data.get("active_missing_category_count"), -1)
    active_missing_image_url_count = parse_int_value(data.get("active_missing_image_url_count"), -1)
    active_missing_product_url_count = parse_int_value(data.get("active_missing_product_url_count"), -1)
    active_missing_mall_id_count = parse_int_value(data.get("active_missing_mall_id_count"), -1)
    active_negative_price_count = parse_int_value(data.get("active_negative_price_count"), -1)
    active_unsafe_image_url_count = parse_int_value(data.get("active_unsafe_image_url_count"), -1)
    active_non_https_image_url_count = parse_int_value(data.get("active_non_https_image_url_count"), -1)
    active_unsafe_product_url_count = parse_int_value(data.get("active_unsafe_product_url_count"), -1)
    active_product_url_product_id_mismatch_count = parse_int_value(
        data.get("active_product_url_product_id_mismatch_count"),
        -1,
    )
    mall_config_alignment = data.get("mall_config_alignment") if isinstance(data.get("mall_config_alignment"), dict) else {}
    mall_config_alignment_checked = mall_config_alignment.get("checked") is True
    mall_config_alignment_ok = mall_config_alignment.get("ok") is True
    mall_config_active_products_checked = parse_int_value(mall_config_alignment.get("active_products_checked"), -1)
    active_unknown_mall_id_count = parse_int_value(mall_config_alignment.get("active_unknown_mall_id_count"), -1)
    active_product_url_mismatch_count = parse_int_value(mall_config_alignment.get("active_product_url_mismatch_count"), -1)
    domain_filter_coverage = (
        data.get("domain_filter_coverage")
        if isinstance(data.get("domain_filter_coverage"), dict)
        else {}
    )
    column_report = data.get("column_report") if isinstance(data.get("column_report"), dict) else {}
    column_report_ok = column_report.get("ok") if column_report else None
    domain_filter_coverage_ok = domain_filter_coverage.get("ok") is True
    domain_filter_coverage_problems = [
        str(problem)
        for problem in domain_filter_coverage.get("problems", [])
        if str(problem).strip()
    ]
    problems = []
    if data.get("ok") is not True:
        problems.append("ok")
    if not query_configured:
        problems.append("query_configured")
    if query_fingerprint_problem_list:
        problems.append("query_fingerprint")
    if limit != 0:
        problems.append("limit")
    if since_configured is not False:
        problems.append("since_configured")
    if batched_fetch is not True:
        problems.append("batched_fetch")
    if streaming_parse is not True:
        problems.append("streaming_parse")
    if streamed_product_csv is not True:
        problems.append("streamed_product_csv")
    if retained_product_rows != 0:
        problems.append("retained_product_rows")
    if fetch_size <= 0:
        problems.append("fetch_size")
    if rows_read > 0 and fetch_batches <= 0:
        problems.append("fetch_batches")
    if rows_read > 0 and max_fetch_batch_rows <= 0:
        problems.append("max_fetch_batch_rows")
    if fetch_size > 0 and max_fetch_batch_rows > fetch_size:
        problems.append("max_fetch_batch_rows_exceeds_fetch_size")
    if rows_read <= 0:
        problems.append("rows_read")
    if exported_products <= 0:
        problems.append("exported_products")
    if active_products < minimum_active_products:
        problems.append("active_products")
    if rows_read > 0 and exported_products > rows_read:
        problems.append("exported_products_exceed_rows_read")
    if csv_rows_written != exported_products:
        problems.append("csv_rows_written")
    if active_products > exported_products:
        problems.append("active_products_exceed_exported_products")
    if inactive_products < 0:
        problems.append("inactive_products")
    elif inactive_products <= 0:
        problems.append("inactive_products")
    elif active_products + inactive_products != exported_products:
        problems.append("active_inactive_exported_products_mismatch")
    if source_deletion_signal_count < 0:
        problems.append("source_deletion_signal_count")
    elif inactive_products >= 0 and source_deletion_signal_count != inactive_products:
        problems.append("source_deletion_signal_count")
    if source_deletion_signal_ok is False:
        problems.append("source_deletion_signal_ok")
    if parse_errors:
        problems.append("parse_errors")
    if duplicate_product_ids:
        problems.append("duplicate_product_ids")
    if missing_updated_at_count < 0:
        problems.append("missing_updated_at_count")
    elif missing_updated_at_count != 0:
        problems.append("missing_updated_at_count")
    if invalid_updated_at_count < 0:
        problems.append("invalid_updated_at_count")
    elif invalid_updated_at_count != 0:
        problems.append("invalid_updated_at_count")
    if future_updated_at_count < 0:
        problems.append("future_updated_at_count")
    elif future_updated_at_count != 0:
        problems.append("future_updated_at_count")
    if active_missing_category_count < 0:
        problems.append("active_missing_category_count")
    elif active_missing_category_count != 0:
        problems.append("active_missing_category_count")
    if active_missing_image_url_count < 0:
        problems.append("active_missing_image_url_count")
    elif active_missing_image_url_count != 0:
        problems.append("active_missing_image_url_count")
    if active_missing_product_url_count < 0:
        problems.append("active_missing_product_url_count")
    elif active_missing_product_url_count != 0:
        problems.append("active_missing_product_url_count")
    if active_missing_mall_id_count < 0:
        problems.append("active_missing_mall_id_count")
    elif active_missing_mall_id_count != 0:
        problems.append("active_missing_mall_id_count")
    if active_negative_price_count < 0:
        problems.append("active_negative_price_count")
    elif active_negative_price_count != 0:
        problems.append("active_negative_price_count")
    if active_unsafe_image_url_count < 0:
        problems.append("active_unsafe_image_url_count")
    elif active_unsafe_image_url_count != 0:
        problems.append("active_unsafe_image_url_count")
    if active_non_https_image_url_count < 0:
        problems.append("active_non_https_image_url_count")
    elif active_non_https_image_url_count != 0:
        problems.append("active_non_https_image_url_count")
    if active_unsafe_product_url_count < 0:
        problems.append("active_unsafe_product_url_count")
    elif active_unsafe_product_url_count != 0:
        problems.append("active_unsafe_product_url_count")
    if active_product_url_product_id_mismatch_count < 0:
        problems.append("active_product_url_product_id_mismatch_count")
    elif active_product_url_product_id_mismatch_count != 0:
        problems.append("active_product_url_product_id_mismatch_count")
    if not mall_config_alignment_checked:
        problems.append("mall_config_alignment")
    elif not mall_config_alignment_ok:
        problems.append("mall_config_alignment")
    if mall_config_alignment_checked and active_unknown_mall_id_count != 0:
        problems.append("active_unknown_mall_id_count")
    if mall_config_alignment_checked and active_product_url_mismatch_count != 0:
        problems.append("active_product_url_mismatch_count")
    if mall_config_alignment_checked:
        if mall_config_active_products_checked < 0:
            problems.append("mall_config_alignment.active_products_checked")
        elif mall_config_active_products_checked != active_products:
            problems.append("mall_config_alignment.active_products_checked")
    if not domain_filter_coverage_ok:
        problems.append("domain_filter_coverage")
        problems.extend(f"domain_filter_coverage.{problem}" for problem in domain_filter_coverage_problems)
    if column_report and column_report_ok is not True:
        problems.append("column_report")
    if is_builtin_sample_csv_value(output_csv):
        problems.append("builtin_sample")
    if fingerprint_problems(output_csv_fingerprint):
        problems.append("output_csv_fingerprint")
    if output_csv_fingerprint_path_problems:
        problems.extend(f"output_csv_fingerprint.{problem}" for problem in output_csv_fingerprint_path_problems)
    details = {
        "query_configured": query_configured,
        "query_fingerprint": query_fingerprint,
        "query_fingerprint_problems": query_fingerprint_problem_list,
        "limit": limit,
        "since_configured": since_configured,
        "batched_fetch": batched_fetch,
        "streaming_parse": streaming_parse,
        "streamed_product_csv": streamed_product_csv,
        "retained_product_rows": retained_product_rows,
        "csv_rows_written": csv_rows_written,
        "fetch_size": fetch_size,
        "fetch_batches": fetch_batches,
        "max_fetch_batch_rows": max_fetch_batch_rows,
        "output_csv": output_csv,
        "output_csv_fingerprint": output_csv_fingerprint,
        "output_csv_fingerprint_path_problems": output_csv_fingerprint_path_problems,
        "rows_read": rows_read,
        "exported_products": exported_products,
        "active_products": active_products,
        "minimum_active_products": minimum_active_products,
        "inactive_products": inactive_products,
        "source_deletion_signal_ok": source_deletion_signal_ok,
        "source_deletion_signal_count": source_deletion_signal_count,
        "source_deletion_signal_product_ids": data.get("source_deletion_signal_product_ids") or [],
        "duplicate_product_ids": duplicate_product_ids,
        "missing_updated_at_count": missing_updated_at_count,
        "missing_updated_at_product_ids": data.get("missing_updated_at_product_ids") or [],
        "invalid_updated_at_count": invalid_updated_at_count,
        "invalid_updated_at_product_ids": data.get("invalid_updated_at_product_ids") or [],
        "future_updated_at_count": future_updated_at_count,
        "future_updated_at_product_ids": data.get("future_updated_at_product_ids") or [],
        "updated_at_min": data.get("updated_at_min"),
        "updated_at_max": data.get("updated_at_max"),
        "updated_at_reference_now": data.get("updated_at_reference_now"),
        "updated_at_max_future_skew_seconds": data.get("updated_at_max_future_skew_seconds"),
        "active_missing_category_count": active_missing_category_count,
        "active_missing_category_product_ids": data.get("active_missing_category_product_ids") or [],
        "active_missing_image_url_count": active_missing_image_url_count,
        "active_missing_image_url_product_ids": data.get("active_missing_image_url_product_ids") or [],
        "active_missing_product_url_count": active_missing_product_url_count,
        "active_missing_product_url_product_ids": data.get("active_missing_product_url_product_ids") or [],
        "active_missing_mall_id_count": active_missing_mall_id_count,
        "active_missing_mall_id_product_ids": data.get("active_missing_mall_id_product_ids") or [],
        "active_negative_price_count": active_negative_price_count,
        "active_negative_price_product_ids": data.get("active_negative_price_product_ids") or [],
        "active_unsafe_image_url_count": active_unsafe_image_url_count,
        "active_unsafe_image_url_product_ids": data.get("active_unsafe_image_url_product_ids") or [],
        "active_non_https_image_url_count": active_non_https_image_url_count,
        "active_non_https_image_url_product_ids": data.get("active_non_https_image_url_product_ids") or [],
        "active_unsafe_product_url_count": active_unsafe_product_url_count,
        "active_unsafe_product_url_product_ids": data.get("active_unsafe_product_url_product_ids") or [],
        "active_product_url_product_id_mismatch_count": active_product_url_product_id_mismatch_count,
        "active_product_url_product_id_mismatch_product_ids": (
            data.get("active_product_url_product_id_mismatch_product_ids") or []
        ),
        "mall_config_alignment": mall_config_alignment,
        "mall_config_alignment_checked": mall_config_alignment_checked,
        "mall_config_alignment_ok": mall_config_alignment_ok,
        "mall_config_active_products_checked": mall_config_active_products_checked,
        "active_unknown_mall_id_count": active_unknown_mall_id_count,
        "active_unknown_mall_ids": mall_config_alignment.get("active_unknown_mall_ids") or [],
        "active_product_url_mismatch_count": active_product_url_mismatch_count,
        "active_product_url_mismatches": mall_config_alignment.get("active_product_url_mismatches") or [],
        "domain_filter_coverage": domain_filter_coverage,
        "domain_filter_coverage_ok": domain_filter_coverage_ok,
        "domain_filter_coverage_problems": domain_filter_coverage_problems,
        "source_columns": data.get("source_columns") or column_report.get("columns"),
        "column_report_ok": column_report_ok,
        "missing_required_columns": column_report.get("missing_required_columns"),
        "noncanonical_required_aliases": column_report.get("noncanonical_required_aliases"),
        "parse_error_count": len(parse_errors),
        "problems": problems,
    }
    details.update(mssql_failure_details(data))
    return (
        not problems,
        "MSSQL export produced a normalized full product CSV"
        if not problems
        else "MSSQL export evidence is missing rows, too small, partial, sample-based, lacks deletion signals, or has parse errors",
        details,
    )


def mssql_failure_details(data: dict[str, Any]) -> dict[str, Any]:
    details = {key: data.get(key) for key in MSSQL_FAILURE_DETAIL_KEYS if key in data}
    if "error" in data:
        details["error"] = sanitize_evidence_error(data.get("error"), [data.get("connection_string")])
    return details


def sanitize_evidence_error(error: Any, sensitive_values: list[Any] | None = None) -> str:
    text = str(error or "")
    for value in sorted({str(item) for item in sensitive_values or [] if len(str(item)) >= 4}, key=len, reverse=True):
        text = text.replace(value, "***")
    text = SECRET_ASSIGNMENT_PATTERN.sub(lambda match: match.group(1) + "***", text)
    text = URL_CREDENTIAL_PATTERN.sub(lambda match: match.group(1) + "[redacted]@", text)
    return text


def check_poc_dataset(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    selected_products = int(data.get("selected_products", 0) or 0)
    minimum_products = int(data.get("minimum_products", 300) or 300)
    minimum_per_recommended_category = parse_int_value(data.get("minimum_per_recommended_category"), 10)
    missing_images = int(data.get("selected_missing_image_url_count", 0) or 0)
    selected_unsafe_image_urls = parse_int_value(data.get("selected_unsafe_image_url_count"), -1)
    selected_non_https_image_urls = parse_int_value(data.get("selected_non_https_image_url_count"), -1)
    missing_product_urls = parse_int_value(data.get("selected_missing_product_url_count"), -1)
    missing_mall_ids = parse_int_value(data.get("selected_missing_mall_id_count"), -1)
    selected_unsafe_product_urls = parse_int_value(data.get("selected_unsafe_product_url_count"), -1)
    selected_product_url_product_id_mismatches = parse_int_value(
        data.get("selected_product_url_product_id_mismatch_count"),
        -1,
    )
    duplicate_product_ids = data.get("duplicate_product_ids") or []
    missing_recommended = data.get("missing_recommended_categories") or []
    thin_recommended = data.get("thin_recommended_categories") or []
    recommended_categories = [str(category).strip() for category in data.get("recommended_categories") or [] if str(category).strip()]
    active_categories = parse_category_counts(data.get("active_categories"))
    selected_categories = parse_category_counts(data.get("selected_categories"))
    selected_missing_recommended = data.get("selected_missing_recommended_categories") or []
    selected_thin_recommended = data.get("selected_thin_recommended_categories") or []
    if recommended_categories and active_categories and selected_categories:
        computed_missing, computed_thin = selected_category_gaps(
            active_categories,
            selected_categories,
            recommended_categories,
            minimum_per_recommended_category,
        )
        selected_missing_recommended = sorted(
            {str(category).strip() for category in selected_missing_recommended if str(category).strip()}
            | set(computed_missing)
        )
        selected_thin_recommended = merge_selected_thin_category_gaps(selected_thin_recommended, computed_thin)
    local_only = data.get("local_only") is True or data.get("not_operational_readiness") is True
    source = data.get("source") or {}
    source_csv_fingerprint = data.get("source_csv_fingerprint") or {}
    output_csv_fingerprint = data.get("output_csv_fingerprint") or {}
    source_csv_fingerprint_path_problems = fingerprint_path_problems(source_csv_fingerprint, data.get("source_csv"))
    output_csv_fingerprint_path_problems = fingerprint_path_problems(output_csv_fingerprint, data.get("output_csv"))
    builtin_sample = (
        source.get("csv_is_builtin_sample") is True
        or source.get("dataset_is_builtin_sample_derived") is True
    )
    problems = []
    if data.get("ok") is not True:
        problems.append("ok")
    if selected_products < minimum_products:
        problems.append("selected_products")
    if missing_images != 0:
        problems.append("selected_missing_image_url_count")
    if selected_unsafe_image_urls < 0:
        problems.append("selected_unsafe_image_url_count")
    elif selected_unsafe_image_urls != 0:
        problems.append("selected_unsafe_image_url_count")
    if selected_non_https_image_urls < 0:
        problems.append("selected_non_https_image_url_count")
    elif selected_non_https_image_urls != 0:
        problems.append("selected_non_https_image_url_count")
    if missing_product_urls < 0:
        problems.append("selected_missing_product_url_count")
    elif missing_product_urls != 0:
        problems.append("selected_missing_product_url_count")
    if missing_mall_ids < 0:
        problems.append("selected_missing_mall_id_count")
    elif missing_mall_ids != 0:
        problems.append("selected_missing_mall_id_count")
    if selected_unsafe_product_urls < 0:
        problems.append("selected_unsafe_product_url_count")
    elif selected_unsafe_product_urls != 0:
        problems.append("selected_unsafe_product_url_count")
    if selected_product_url_product_id_mismatches < 0:
        problems.append("selected_product_url_product_id_mismatch_count")
    elif selected_product_url_product_id_mismatches != 0:
        problems.append("selected_product_url_product_id_mismatch_count")
    if duplicate_product_ids:
        problems.append("duplicate_product_ids")
    if missing_recommended:
        problems.append("missing_recommended_categories")
    if thin_recommended:
        problems.append("thin_recommended_categories")
    if not active_categories:
        problems.append("active_categories")
    if not selected_categories:
        problems.append("selected_categories")
    if selected_missing_recommended:
        problems.append("selected_missing_recommended_categories")
    if selected_thin_recommended:
        problems.append("selected_thin_recommended_categories")
    if local_only:
        problems.append("local_only")
    if builtin_sample:
        problems.append("builtin_sample")
    if fingerprint_problems(source_csv_fingerprint):
        problems.append("source_csv_fingerprint")
    if source_csv_fingerprint_path_problems:
        problems.extend(f"source_csv_fingerprint.{problem}" for problem in source_csv_fingerprint_path_problems)
    if fingerprint_problems(output_csv_fingerprint):
        problems.append("output_csv_fingerprint")
    if output_csv_fingerprint_path_problems:
        problems.extend(f"output_csv_fingerprint.{problem}" for problem in output_csv_fingerprint_path_problems)
    return (
        not problems,
        "PoC dataset contains a balanced non-sample product subset"
        if not problems
        else "PoC dataset evidence is too small, unbalanced, field-incomplete, or sample-derived",
        {
            "selected_products": selected_products,
            "minimum_products": minimum_products,
            "target_size": data.get("target_size"),
            "source_csv": data.get("source_csv"),
            "source_csv_fingerprint": source_csv_fingerprint,
            "source_csv_fingerprint_path_problems": source_csv_fingerprint_path_problems,
            "output_csv": data.get("output_csv"),
            "output_csv_fingerprint": output_csv_fingerprint,
            "output_csv_fingerprint_path_problems": output_csv_fingerprint_path_problems,
            "category_count": data.get("category_count"),
            "minimum_per_recommended_category": minimum_per_recommended_category,
            "recommended_categories": recommended_categories,
            "active_categories": active_categories,
            "selected_categories": selected_categories,
            "selected_missing_image_url_count": missing_images,
            "selected_missing_image_url_product_ids": data.get("selected_missing_image_url_product_ids") or [],
            "selected_unsafe_image_url_count": selected_unsafe_image_urls,
            "selected_unsafe_image_url_product_ids": data.get("selected_unsafe_image_url_product_ids") or [],
            "selected_non_https_image_url_count": selected_non_https_image_urls,
            "selected_non_https_image_url_product_ids": data.get("selected_non_https_image_url_product_ids") or [],
            "selected_missing_product_url_count": missing_product_urls,
            "selected_missing_product_url_product_ids": data.get("selected_missing_product_url_product_ids") or [],
            "selected_missing_mall_id_count": missing_mall_ids,
            "selected_missing_mall_id_product_ids": data.get("selected_missing_mall_id_product_ids") or [],
            "selected_unsafe_product_url_count": selected_unsafe_product_urls,
            "selected_unsafe_product_url_product_ids": data.get("selected_unsafe_product_url_product_ids") or [],
            "selected_product_url_product_id_mismatch_count": selected_product_url_product_id_mismatches,
            "selected_product_url_product_id_mismatch_product_ids": (
                data.get("selected_product_url_product_id_mismatch_product_ids") or []
            ),
            "duplicate_product_ids": duplicate_product_ids,
            "missing_recommended_categories": missing_recommended,
            "thin_recommended_categories": thin_recommended,
            "selected_missing_recommended_categories": selected_missing_recommended,
            "selected_thin_recommended_categories": selected_thin_recommended,
            "local_only": local_only,
            "builtin_sample": builtin_sample,
            "problems": problems,
        },
    )


def parse_category_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts = {}
    for category, count in value.items():
        category_text = str(category or "").strip()
        if not category_text:
            continue
        counts[category_text] = parse_int_value(count, 0)
    return counts


def selected_category_gaps(
    active_categories: dict[str, int],
    selected_categories: dict[str, int],
    recommended_categories: list[str],
    minimum_per_category: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    missing = []
    thin = []
    for category in recommended_categories:
        active_count = int(active_categories.get(category, 0) or 0)
        if active_count <= 0:
            continue
        selected_count = int(selected_categories.get(category, 0) or 0)
        required = min(active_count, max(1, minimum_per_category))
        if selected_count <= 0:
            missing.append(category)
        elif selected_count < required:
            thin.append(
                {
                    "category": category,
                    "selected_products": selected_count,
                    "active_products": active_count,
                    "minimum": required,
                }
            )
    return missing, thin


def merge_selected_thin_category_gaps(existing: Any, computed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_category = {}
    existing_items = existing if isinstance(existing, list) else []
    for item in existing_items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        if category:
            by_category[category] = item
    for item in computed:
        by_category[str(item.get("category"))] = item
    return [by_category[category] for category in sorted(by_category)]


def check_image_urls(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    checked = int(data.get("checked", 0) or 0)
    failed = int(data.get("failed", 0) or 0)
    concurrency = int(data.get("concurrency", 0) or 0)
    min_dimension = int(data.get("min_dimension", 0) or 0)
    active_products = parse_int_value(data.get("active_products"), -1)
    missing_active_image_url_count = parse_int_value(data.get("missing_active_image_url_count"), -1)
    require_https = data.get("require_https") is True
    non_https_active_image_url_count = parse_int_value(data.get("non_https_active_image_url_count"), -1)
    warning_type_counts = data.get("warning_type_counts") if isinstance(data.get("warning_type_counts"), dict) else {}
    raw_blocking_warning_type_counts = (
        data.get("blocking_warning_type_counts")
        if isinstance(data.get("blocking_warning_type_counts"), dict)
        else {}
    )
    blocking_warning_type_counts = {
        str(warning_type): int(count or 0)
        for warning_type, count in raw_blocking_warning_type_counts.items()
        if str(warning_type) in BLOCKING_IMAGE_WARNING_TYPES
    }
    if not blocking_warning_type_counts:
        blocking_warning_type_counts = {
            str(warning_type): int(count or 0)
            for warning_type, count in warning_type_counts.items()
            if str(warning_type) in BLOCKING_IMAGE_WARNING_TYPES
        }
    blocking_warning_count = parse_int_value(
        data.get("blocking_warning_count"),
        sum(blocking_warning_type_counts.values()),
    )
    source = data.get("source") or {}
    csv_fingerprint = data.get("csv_fingerprint") or {}
    csv_fingerprint_path_problems = fingerprint_path_problems(csv_fingerprint, data.get("csv"))
    required_checked = 100
    max_concurrency = 5
    problems = []
    if active_products < checked:
        problems.append("active_products")
    if missing_active_image_url_count < 0:
        problems.append("missing_active_image_url_count")
    elif missing_active_image_url_count != 0:
        problems.append("missing_active_image_url_count")
    if not require_https:
        problems.append("require_https")
    if non_https_active_image_url_count < 0:
        problems.append("non_https_active_image_url_count")
    elif non_https_active_image_url_count != 0:
        problems.append("non_https_active_image_url_count")
    if blocking_warning_count != 0:
        problems.append("blocking_image_warnings")
    source_problems = []
    if not isinstance(source, dict) or not source:
        source_problems.append("source")
    if source.get("csv_is_builtin_sample") is True or is_builtin_sample_csv_value(data.get("csv")):
        source_problems.append("builtin_sample_csv")
    if source.get("dataset_is_builtin_sample_derived") is True:
        source_problems.append("builtin_sample_dataset")
    if fingerprint_problems(csv_fingerprint):
        source_problems.append("csv_fingerprint")
    if csv_fingerprint_path_problems:
        source_problems.extend(f"csv_fingerprint.{problem}" for problem in csv_fingerprint_path_problems)
    ok = (
        data.get("ok") is True
        and checked >= required_checked
        and failed == 0
        and 1 <= concurrency <= max_concurrency
        and min_dimension >= 16
        and not problems
        and not source_problems
    )
    return (
        ok,
        "image URL sample passed"
        if ok
        else "image URL report failed, checked too few images, used sample data, or used unsafe concurrency",
        {
            "csv": data.get("csv"),
            "csv_fingerprint": csv_fingerprint,
            "csv_fingerprint_path_problems": csv_fingerprint_path_problems,
            "active_products": active_products,
            "missing_active_image_url_count": missing_active_image_url_count,
            "missing_active_image_url_product_ids": data.get("missing_active_image_url_product_ids") or [],
            "require_https": require_https,
            "non_https_active_image_url_count": non_https_active_image_url_count,
            "non_https_active_image_url_product_ids": data.get("non_https_active_image_url_product_ids") or [],
            "checked": checked,
            "required_checked": required_checked,
            "failed": failed,
            "warning_count": data.get("warning_count"),
            "failure_category_counts": data.get("failure_category_counts") or {},
            "warning_type_counts": warning_type_counts,
            "blocking_warning_count": blocking_warning_count,
            "blocking_warning_type_counts": blocking_warning_type_counts,
            "blocking_warnings": data.get("blocking_warnings") or [],
            "attempts": data.get("attempts") or {},
            "concurrency": concurrency,
            "max_concurrency": max_concurrency,
            "timeout_seconds": data.get("timeout_seconds"),
            "retry_count": data.get("retry_count"),
            "max_mb": data.get("max_mb"),
            "min_dimension": min_dimension,
            "problems": problems,
            "source": source,
            "source_problems": source_problems,
        },
    )


def is_builtin_sample_csv_value(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    return bool(text) and (text.endswith("/sample_products.csv") or text == "sample_products.csv")


def check_quality(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    dataset = data.get("dataset") or {}
    response_time = data.get("response_time") or {}
    source = data.get("source") or {}
    csv_fingerprint = data.get("csv_fingerprint") or {}
    case_source_fingerprint = data.get("case_source_fingerprint") or {}
    engine = source.get("engine") or data.get("engine")
    custom_cases = data.get("custom_cases") is True and data.get("case_source") not in {None, "", "builtin"}
    skipped_case_checks = int(data.get("skipped_case_checks", 0) or 0)
    image_cases_with_supplied_source = int(data.get("image_cases_with_supplied_source", 0) or 0)
    mixed_cases_with_supplied_source = int(data.get("mixed_cases_with_supplied_source", 0) or 0)
    image_cases_with_file_source = int(data.get("image_cases_with_file_source", 0) or 0)
    mixed_cases_with_file_source = int(data.get("mixed_cases_with_file_source", 0) or 0)
    case_image_fingerprints = data.get("case_image_fingerprints")
    expected_case_image_fingerprints = image_cases_with_file_source + mixed_cases_with_file_source
    case_contract = quality_case_contract(data)
    case_result_evidence = summarize_quality_case_result_evidence(data)
    result_contract_evidence = summarize_quality_result_contract_evidence(data)
    csv_fingerprint_path_problems = fingerprint_path_problems(csv_fingerprint, data.get("csv"))
    local_only = (
        data.get("local_only") is True
        or data.get("not_operational_readiness") is True
        or engine != "marqo"
        or source.get("dataset_is_builtin_sample_derived") is True
    )
    builtin_sample = (
        source.get("csv_is_builtin_sample") is True
        or source.get("dataset_is_builtin_sample_derived") is True
        or is_builtin_sample_csv_value(data.get("csv"))
    )
    csv_fingerprint_problems = fingerprint_problems(csv_fingerprint)
    case_source_fingerprint_problems: list[str] = []
    if custom_cases:
        case_source_fingerprint_problems = fingerprint_problems(case_source_fingerprint)
        if isinstance(case_source_fingerprint, dict) and not str(case_source_fingerprint.get("path") or "").strip():
            case_source_fingerprint_problems.append("path")
        case_source_fingerprint_problems.extend(fingerprint_path_problems(case_source_fingerprint, data.get("case_source")))
    case_image_fingerprint_problems_list = case_image_fingerprint_problems(
        case_image_fingerprints,
        expected_case_image_fingerprints,
    )
    ok = (
        data.get("ok") is True
        and data.get("quality_ok") is True
        and data.get("response_time_ok") is True
        and data.get("dataset_ready") is True
        and not local_only
        and not builtin_sample
        and custom_cases
        and not case_source_fingerprint_problems
        and skipped_case_checks == 0
        and image_cases_with_supplied_source >= 1
        and mixed_cases_with_supplied_source >= 1
        and image_cases_with_file_source >= 1
        and mixed_cases_with_file_source >= 1
        and not case_image_fingerprint_problems_list
        and case_contract.get("ok") is True
        and case_result_evidence.get("ok") is True
        and result_contract_evidence.get("ok") is True
        and not csv_fingerprint_problems
        and not csv_fingerprint_path_problems
    )
    problems = []
    if data.get("ok") is not True:
        problems.append("ok")
    if data.get("quality_ok") is not True:
        problems.append("quality_ok")
    if data.get("response_time_ok") is not True:
        problems.append("response_time_ok")
    if data.get("dataset_ready") is not True:
        problems.append("dataset_ready")
    if local_only:
        problems.append("local_only")
    if builtin_sample:
        problems.append("builtin_sample")
    if not custom_cases:
        problems.append("custom_cases")
    if case_source_fingerprint_problems:
        problems.append("case_source_fingerprint")
    if skipped_case_checks != 0:
        problems.append("skipped_case_checks")
    if image_cases_with_supplied_source < 1:
        problems.append("image_cases_with_supplied_source")
    if mixed_cases_with_supplied_source < 1:
        problems.append("mixed_cases_with_supplied_source")
    if image_cases_with_file_source < 1:
        problems.append("image_cases_with_file_source")
    if mixed_cases_with_file_source < 1:
        problems.append("mixed_cases_with_file_source")
    if case_image_fingerprint_problems_list:
        problems.append("case_image_fingerprints")
    if case_contract.get("ok") is not True:
        problems.append("case_contract")
    if case_result_evidence.get("ok") is not True:
        problems.extend(
            f"case_result_evidence.{problem}"
            for problem in case_result_evidence.get("problems") or ["ok"]
        )
    if result_contract_evidence.get("ok") is not True:
        problems.extend(
            f"result_contract_evidence.{problem}"
            for problem in result_contract_evidence.get("problems") or ["ok"]
        )
    if csv_fingerprint_problems:
        problems.append("csv_fingerprint")
    if csv_fingerprint_path_problems:
        problems.extend(f"csv_fingerprint.{problem}" for problem in csv_fingerprint_path_problems)
    return (
        ok,
        "PoC quality, response time, and minimum dataset size passed"
        if ok
        else "PoC quality report is not production-ready, lacks production quality cases, or uses bundled local sample data",
        {
            "quality_ok": data.get("quality_ok"),
            "response_time_ok": data.get("response_time_ok"),
            "response_time": response_time,
            "dataset_ready": data.get("dataset_ready"),
            "active_products": dataset.get("active_products"),
            "minimum_poc_products": dataset.get("minimum_poc_products"),
            "csv": data.get("csv"),
            "csv_fingerprint": csv_fingerprint,
            "csv_fingerprint_problems": csv_fingerprint_problems,
            "csv_fingerprint_path_problems": csv_fingerprint_path_problems,
            "engine": engine,
            "case_source": data.get("case_source"),
            "case_source_fingerprint": case_source_fingerprint,
            "case_source_fingerprint_problems": case_source_fingerprint_problems,
            "custom_cases": custom_cases,
            "skipped_case_checks": skipped_case_checks,
            "image_source_counts": data.get("image_source_counts") or {},
            "image_cases_with_supplied_source": image_cases_with_supplied_source,
            "mixed_cases_with_supplied_source": mixed_cases_with_supplied_source,
            "image_cases_with_file_source": image_cases_with_file_source,
            "mixed_cases_with_file_source": mixed_cases_with_file_source,
            "case_image_fingerprints": case_image_fingerprints or [],
            "case_image_fingerprint_problems": case_image_fingerprint_problems_list,
            "case_contract": case_contract,
            "case_result_evidence": case_result_evidence,
            "result_contract_evidence": result_contract_evidence,
            "local_only": local_only,
            "builtin_sample": builtin_sample,
            "problems": sorted(set(problems)),
        },
    )


def summarize_quality_case_result_evidence(data: dict[str, Any]) -> dict[str, Any]:
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return {
            "ok": False,
            "case_count": 0,
            "expected_category_case_count": 0,
            "problems": ["cases"],
            "failed_expected_category_cases": [],
        }

    problems: list[str] = []
    failed_expected_category_cases: list[str] = []
    expected_category_case_count = 0
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            problems.append(f"case_{index}.object")
            continue
        case_name = str(case.get("name") or f"case_{index}")
        checks = case.get("checks") if isinstance(case.get("checks"), list) else []
        expected_category_checks = [
            check
            for check in checks
            if isinstance(check, dict) and str(check.get("name") or "") == "expected_category"
        ]
        if not expected_category_checks:
            continue
        expected_category_case_count += 1
        for check in expected_category_checks:
            expected = str(check.get("expected") or "").strip()
            if check.get("ok") is not True:
                problems.append(f"{case_name}.expected_category.ok")
                failed_expected_category_cases.append(case_name)
            if not expected:
                problems.append(f"{case_name}.expected_category.expected")

            if check.get("top_category_ok") is not True:
                problems.append(f"{case_name}.expected_category.top_category_ok")
            if check.get("suggested_category_ok") is not True:
                problems.append(f"{case_name}.expected_category.suggested_category_ok")
            actual = check.get("actual")
            if not isinstance(actual, dict):
                problems.append(f"{case_name}.expected_category.actual")
                continue
            top_categories = quality_category_values(actual.get("top"))
            suggested_categories = quality_category_values(actual.get("suggested"))
            if expected and expected not in top_categories:
                problems.append(f"{case_name}.expected_category.actual.top")
            if expected and expected not in suggested_categories:
                problems.append(f"{case_name}.expected_category.actual.suggested")

    if expected_category_case_count <= 0:
        problems.append("expected_category_cases")

    return {
        "ok": not problems,
        "case_count": len(cases),
        "expected_category_case_count": expected_category_case_count,
        "problems": sorted(set(problems)),
        "failed_expected_category_cases": sorted(set(failed_expected_category_cases)),
    }


def quality_category_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def summarize_quality_result_contract_evidence(data: dict[str, Any]) -> dict[str, Any]:
    cases = data.get("cases")
    report_contract = data.get("result_contract")
    if not isinstance(cases, list) or not cases:
        return {
            "ok": False,
            "case_count": 0,
            "checked": 0,
            "problems": ["cases"],
            "failed_cases": [],
        }

    problems: list[str] = []
    failed_cases: list[str] = []
    totals = {
        "checked": 0,
        "product_url_missing_count": 0,
        "product_url_unsafe_count": 0,
        "product_url_product_id_mismatch_count": 0,
        "product_url_prefix_mismatch_count": 0,
        "mall_id_missing_count": 0,
        "mall_id_mismatch_count": 0,
    }
    expected_report_mall_id = str(data.get("mall_id") or "").strip()

    if not isinstance(report_contract, dict):
        problems.append("result_contract")
    else:
        if report_contract.get("ok") is not True:
            problems.append("result_contract.ok")
        if parse_int_value(report_contract.get("case_count"), -1) != len(cases):
            problems.append("result_contract.case_count")
        if parse_int_value(report_contract.get("cases_with_expected_product_url_prefix"), -1) != len(cases):
            problems.append("result_contract.cases_with_expected_product_url_prefix")
        if report_contract.get("missing_expected_product_url_prefix_cases"):
            problems.append("result_contract.missing_expected_product_url_prefix_cases")
        for key in totals:
            value = parse_int_value(report_contract.get(key), -1)
            if value < 0:
                problems.append(f"result_contract.{key}")
            elif key != "checked" and value != 0:
                problems.append(f"result_contract.{key}")

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            problems.append(f"case_{index}.object")
            continue
        case_name = str(case.get("name") or f"case_{index}")
        contract = case.get("result_contract")
        if not isinstance(contract, dict):
            problems.append(f"{case_name}.result_contract")
            failed_cases.append(case_name)
            continue
        if contract.get("ok") is not True:
            problems.append(f"{case_name}.result_contract.ok")
            failed_cases.append(case_name)

        expected_mall_id = str(contract.get("expected_mall_id") or "").strip()
        if expected_report_mall_id and expected_mall_id and expected_mall_id != expected_report_mall_id:
            problems.append(f"{case_name}.result_contract.expected_mall_id")
        if not str(contract.get("expected_product_url_prefix") or "").strip():
            problems.append(f"{case_name}.result_contract.expected_product_url_prefix")

        checked = parse_int_value(contract.get("checked"), -1)
        if checked < 0:
            problems.append(f"{case_name}.result_contract.checked")
            checked = 0
        totals["checked"] += checked

        expected_min_results = max(
            [
                parse_int_value(check.get("expected"), 0)
                for check in case.get("checks") or []
                if isinstance(check, dict) and check.get("name") == "expected_min_results"
            ]
            or [0]
        )
        if expected_min_results > 0 and checked < expected_min_results:
            problems.append(f"{case_name}.result_contract.checked")

        observed_items = contract.get("observed_items")
        if checked > 0 and not isinstance(observed_items, list):
            problems.append(f"{case_name}.result_contract.observed_items")
            observed_items = []
        elif not isinstance(observed_items, list):
            observed_items = []
        if checked > 0 and len(observed_items) != checked:
            problems.append(f"{case_name}.result_contract.observed_items")

        for key in [
            "product_url_missing_count",
            "product_url_unsafe_count",
            "product_url_product_id_mismatch_count",
            "product_url_prefix_mismatch_count",
            "mall_id_missing_count",
            "mall_id_mismatch_count",
        ]:
            count = parse_int_value(contract.get(key), -1)
            if count < 0:
                problems.append(f"{case_name}.result_contract.{key}")
                count = 0
            elif count != 0:
                problems.append(f"{case_name}.result_contract.{key}")
                failed_cases.append(case_name)
            totals[key] += count

        for item_index, item in enumerate(observed_items):
            if not isinstance(item, dict):
                problems.append(f"{case_name}.result_contract.observed_items.{item_index}")
                continue
            item_prefix = f"{case_name}.result_contract.observed_items.{item_index}"
            if str(item.get("section") or "") not in {"top", "items"}:
                problems.append(f"{item_prefix}.section")
            if not str(item.get("product_id") or "").strip():
                problems.append(f"{item_prefix}.product_id")
            if not str(item.get("product_url") or "").strip():
                problems.append(f"{item_prefix}.product_url")
            if not str(item.get("mall_id") or "").strip():
                problems.append(f"{item_prefix}.mall_id")
            if item.get("product_url_safe") is not True:
                problems.append(f"{item_prefix}.product_url_safe")
            if item.get("product_url_contains_product_id") is not True:
                problems.append(f"{item_prefix}.product_url_contains_product_id")
            if item.get("product_url_matches_expected_prefix") is not True:
                problems.append(f"{item_prefix}.product_url_matches_expected_prefix")
            if item.get("mall_id_matches_request") is not True:
                problems.append(f"{item_prefix}.mall_id_matches_request")

    if totals["checked"] <= 0:
        problems.append("checked")

    if isinstance(report_contract, dict):
        for key, actual in totals.items():
            reported = parse_int_value(report_contract.get(key), -1)
            if reported >= 0 and reported != actual:
                problems.append(f"result_contract.{key}")

    return {
        "ok": not problems,
        "case_count": len(cases),
        "failed_cases": sorted(set(failed_cases)),
        **totals,
        "problems": sorted(set(problems)),
    }


def quality_case_contract(data: dict[str, Any]) -> dict[str, Any]:
    contract = data.get("case_contract")
    cases = data.get("cases") if isinstance(data.get("cases"), list) else []
    if isinstance(contract, dict):
        return normalize_quality_case_contract(contract, cases)
    return summarize_quality_case_contract(cases, require_cases=True)


def normalize_quality_case_contract(contract: dict[str, Any], cases: list[Any]) -> dict[str, Any]:
    normalized = dict(contract)
    computed = summarize_quality_case_contract(cases, require_cases=False)
    for key in [
        "min_type_counts",
        "min_expected_results",
        "query_type_counts",
        "missing_type_counts",
        "missing_expectation_cases",
        "low_min_result_cases",
        "low_confidence_case_count",
        "missing_low_confidence_case",
        "text_variant_case_count",
        "missing_text_variant_case",
        "duplicate_case_names",
    ]:
        normalized.setdefault(key, computed.get(key))

    low_confidence_case_count = parse_int_value(normalized.get("low_confidence_case_count"), 0)
    text_variant_case_count = parse_int_value(normalized.get("text_variant_case_count"), 0)
    missing_type_counts = normalized.get("missing_type_counts") if isinstance(normalized.get("missing_type_counts"), dict) else {}
    missing_expectation_cases = [
        str(name)
        for name in normalized.get("missing_expectation_cases") or []
        if str(name).strip()
    ]
    low_min_result_cases = [
        str(name)
        for name in normalized.get("low_min_result_cases") or []
        if str(name).strip()
    ]
    duplicate_case_names = [
        str(name)
        for name in normalized.get("duplicate_case_names") or []
        if str(name).strip()
    ]
    raw_missing_low_confidence_case = normalized.get("missing_low_confidence_case")
    raw_missing_text_variant_case = normalized.get("missing_text_variant_case")
    missing_low_confidence_case = (
        raw_missing_low_confidence_case is True
        or str(raw_missing_low_confidence_case).strip().lower() == "true"
        or low_confidence_case_count < QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES
    )
    missing_text_variant_case = (
        raw_missing_text_variant_case is True
        or str(raw_missing_text_variant_case).strip().lower() == "true"
        or text_variant_case_count < QUALITY_CASE_MIN_TEXT_VARIANT_CASES
    )

    normalized["min_low_confidence_cases"] = QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES
    normalized["low_confidence_case_count"] = low_confidence_case_count
    normalized["missing_low_confidence_case"] = missing_low_confidence_case
    normalized["min_text_variant_cases"] = QUALITY_CASE_MIN_TEXT_VARIANT_CASES
    normalized["text_variant_case_count"] = text_variant_case_count
    normalized["missing_text_variant_case"] = missing_text_variant_case
    normalized["missing_type_counts"] = missing_type_counts
    normalized["missing_expectation_cases"] = missing_expectation_cases
    normalized["low_min_result_cases"] = low_min_result_cases
    normalized["duplicate_case_names"] = duplicate_case_names
    normalized["ok"] = (
        normalized.get("ok") is True
        and not missing_type_counts
        and not missing_expectation_cases
        and not low_min_result_cases
        and not missing_low_confidence_case
        and not missing_text_variant_case
        and not duplicate_case_names
    )
    return normalized


def summarize_quality_case_contract(cases: list[Any], require_cases: bool) -> dict[str, Any]:
    type_counts = {query_type: 0 for query_type in QUALITY_CASE_MIN_TYPE_COUNTS}
    missing_expectation_cases = []
    low_min_result_cases = []
    seen_names: set[str] = set()
    duplicate_case_names: set[str] = set()
    low_confidence_case_count = 0
    text_variant_case_count = 0
    for case in cases:
        if not isinstance(case, dict):
            continue
        name = str(case.get("name") or "")
        if name:
            if name in seen_names:
                duplicate_case_names.add(name)
            seen_names.add(name)
        query_type = str(case.get("query_type") or "")
        if query_type in type_counts:
            type_counts[query_type] += 1
        if query_type == "text" and case_has_text_variant_marker(case):
            text_variant_case_count += 1
        checks = case.get("checks") if isinstance(case.get("checks"), list) else []
        check_names = {str(check.get("name") or "") for check in checks if isinstance(check, dict)}
        low_confidence_case = case_expects_low_confidence(checks)
        if low_confidence_case:
            low_confidence_case_count += 1
        if not ({"expected_category", "expected_top_product_id"} & check_names) and not low_confidence_case:
            missing_expectation_cases.append(name)
        if low_confidence_case:
            continue
        min_result_expectations = [
            parse_int_value(check.get("expected"), 0)
            for check in checks
            if isinstance(check, dict) and check.get("name") == "expected_min_results"
        ]
        if max(min_result_expectations or [0]) < QUALITY_CASE_MIN_RESULTS:
            low_min_result_cases.append(name)
    missing_type_counts = {
        query_type: {"expected": expected, "actual": type_counts.get(query_type, 0)}
        for query_type, expected in QUALITY_CASE_MIN_TYPE_COUNTS.items()
        if type_counts.get(query_type, 0) < expected
    }
    missing_low_confidence_case = low_confidence_case_count < QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES
    missing_text_variant_case = text_variant_case_count < QUALITY_CASE_MIN_TEXT_VARIANT_CASES
    return {
        "ok": (bool(cases) or not require_cases)
        and not missing_type_counts
        and not missing_expectation_cases
        and not low_min_result_cases
        and not duplicate_case_names
        and not missing_low_confidence_case
        and not missing_text_variant_case,
        "min_type_counts": dict(QUALITY_CASE_MIN_TYPE_COUNTS),
        "min_expected_results": QUALITY_CASE_MIN_RESULTS,
        "min_low_confidence_cases": QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES,
        "min_text_variant_cases": QUALITY_CASE_MIN_TEXT_VARIANT_CASES,
        "query_type_counts": type_counts,
        "low_confidence_case_count": low_confidence_case_count,
        "missing_low_confidence_case": missing_low_confidence_case,
        "text_variant_case_count": text_variant_case_count,
        "missing_text_variant_case": missing_text_variant_case,
        "missing_type_counts": missing_type_counts,
        "missing_expectation_cases": missing_expectation_cases,
        "low_min_result_cases": low_min_result_cases,
        "duplicate_case_names": sorted(duplicate_case_names),
    }


def case_has_text_variant_marker(case: dict[str, Any]) -> bool:
    name = str(case.get("name") or "").lower()
    if any(token in name for token in ["typo", "synonym", "variant"]):
        return True
    return "typo_or_synonym" in case_tags(case)


def case_tags(case: dict[str, Any]) -> list[str]:
    raw_tags = case.get("tags")
    if isinstance(raw_tags, str):
        values = raw_tags.replace(";", ",").replace("|", ",").split(",")
    elif isinstance(raw_tags, list):
        values = raw_tags
    else:
        values = []
    return [str(value).strip().lower() for value in values if str(value).strip()]


def case_expects_low_confidence(checks: list[Any]) -> bool:
    for check in checks:
        if not isinstance(check, dict) or check.get("name") != "expected_low_confidence":
            continue
        expected = check.get("expected")
        return expected is True or str(expected).strip().lower() == "true"
    return False


def parse_int_value(value: Any, default: int = 0) -> int:
    if isinstance(value, str) and value.strip().lower() in {"unlimited", "infinity", "inf"}:
        return 10**18
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def check_csv_index(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    summary = data.get("summary") or {}
    source = data.get("source") or {}
    csv_fingerprint = data.get("csv_fingerprint") or {}
    csv_fingerprint_path_problems = fingerprint_path_problems(csv_fingerprint, data.get("csv"))
    active_products = int(summary.get("active_products", 0) or 0)
    indexed = int(data.get("indexed", 0) or 0)
    failed = int(data.get("failed", 0) or 0)
    expected_index_document_count = parse_int_value(data.get("expected_index_document_count"), active_products)
    post_index_document_count = parse_int_value(data.get("post_index_document_count"), -1)
    post_index_document_count_ok = data.get("post_index_document_count_ok")
    missing_active_image_count = int(summary.get("missing_active_image_count", 0) or 0)
    active_unsafe_image_url_count = parse_int_value(summary.get("active_unsafe_image_url_count"), -1)
    active_non_https_image_url_count = parse_int_value(summary.get("active_non_https_image_url_count"), -1)
    missing_active_product_url_count = parse_int_value(summary.get("missing_active_product_url_count"), -1)
    missing_active_mall_id_count = parse_int_value(summary.get("missing_active_mall_id_count"), -1)
    active_unsafe_product_url_count = parse_int_value(summary.get("active_unsafe_product_url_count"), -1)
    active_product_url_product_id_mismatch_count = parse_int_value(
        summary.get("active_product_url_product_id_mismatch_count"),
        -1,
    )
    duplicate_product_ids = summary.get("duplicate_product_ids") or []
    minimum_poc_products = 300
    problems = []
    if data.get("ok") is not True:
        problems.append("ok")
    if data.get("dry_run") is not False:
        problems.append("dry_run")
    if data.get("mode") != "reindex":
        problems.append("mode")
    engine = str(data.get("engine") or "").strip().lower()
    engine_ok = engine == "marqo"
    if not engine_ok:
        problems.append("engine")
    marqo_url = str(data.get("marqo_url") or "").strip()
    marqo_model = str(data.get("marqo_model") or "").strip()
    validate_images = data.get("validate_images")
    marqo_url_problems = marqo_url_evidence_problems("marqo_url", marqo_url) if engine_ok else []
    if engine_ok and marqo_url_problems:
        problems.append("marqo_url")
    if engine_ok and not marqo_model:
        problems.append("marqo_model")
    if data.get("persistent_index") is not True:
        problems.append("persistent_index")
    if engine_ok and data.get("dry_run") is False and data.get("mode") == "reindex" and validate_images is not True:
        problems.append("validate_images")
    local_only = (
        data.get("local_only") is True
        or data.get("not_operational_readiness") is True
        or source.get("dataset_is_builtin_sample_derived") is True
    )
    builtin_sample = (
        source.get("csv_is_builtin_sample") is True
        or source.get("dataset_is_builtin_sample_derived") is True
        or is_builtin_sample_csv_value(data.get("csv"))
    )
    if local_only:
        problems.append("local_only")
    if builtin_sample:
        problems.append("builtin_sample")
    if active_products < minimum_poc_products:
        problems.append("active_products")
    if indexed != active_products:
        problems.append("indexed")
    if engine_ok and data.get("dry_run") is False and data.get("mode") == "reindex":
        if expected_index_document_count != active_products:
            problems.append("expected_index_document_count")
        if post_index_document_count < 0:
            problems.append("post_index_document_count")
        elif post_index_document_count != expected_index_document_count:
            problems.append("post_index_document_count")
        if post_index_document_count_ok is not True:
            problems.append("post_index_document_count_ok")
    if failed != 0:
        problems.append("failed")
    if missing_active_image_count != 0:
        problems.append("missing_active_image_count")
    if active_unsafe_image_url_count < 0:
        problems.append("active_unsafe_image_url_count")
    elif active_unsafe_image_url_count != 0:
        problems.append("active_unsafe_image_url_count")
    if active_non_https_image_url_count < 0:
        problems.append("active_non_https_image_url_count")
    elif active_non_https_image_url_count != 0:
        problems.append("active_non_https_image_url_count")
    if missing_active_product_url_count < 0:
        problems.append("missing_active_product_url_count")
    elif missing_active_product_url_count != 0:
        problems.append("missing_active_product_url_count")
    if missing_active_mall_id_count < 0:
        problems.append("missing_active_mall_id_count")
    elif missing_active_mall_id_count != 0:
        problems.append("missing_active_mall_id_count")
    if active_unsafe_product_url_count < 0:
        problems.append("active_unsafe_product_url_count")
    elif active_unsafe_product_url_count != 0:
        problems.append("active_unsafe_product_url_count")
    if active_product_url_product_id_mismatch_count < 0:
        problems.append("active_product_url_product_id_mismatch_count")
    elif active_product_url_product_id_mismatch_count != 0:
        problems.append("active_product_url_product_id_mismatch_count")
    if duplicate_product_ids:
        problems.append("duplicate_product_ids")
    if fingerprint_problems(csv_fingerprint):
        problems.append("csv_fingerprint")
    if csv_fingerprint_path_problems:
        problems.extend(f"csv_fingerprint.{problem}" for problem in csv_fingerprint_path_problems)
    ok = not problems
    return (
        ok,
        "PoC CSV indexed into a persistent search index" if ok else "PoC CSV index evidence is not production-ready",
        {
            "csv": data.get("csv"),
            "csv_fingerprint": csv_fingerprint,
            "csv_fingerprint_path_problems": csv_fingerprint_path_problems,
            "engine": data.get("engine"),
            "engine_ok": engine_ok,
            "index": data.get("index"),
            "marqo_url": marqo_url or None,
            "marqo_url_problems": marqo_url_problems,
            "marqo_model": marqo_model or None,
            "mode": data.get("mode"),
            "dry_run": data.get("dry_run"),
            "persistent_index": data.get("persistent_index"),
            "validate_images": validate_images,
            "local_only": local_only,
            "builtin_sample": builtin_sample,
            "active_products": active_products,
            "minimum_poc_products": minimum_poc_products,
            "indexed": indexed,
            "deleted": data.get("deleted"),
            "failed": failed,
            "expected_index_document_count": expected_index_document_count,
            "post_index_document_count": post_index_document_count,
            "post_index_document_count_ok": post_index_document_count_ok,
            "post_index_document_count_error": data.get("post_index_document_count_error"),
            "missing_active_image_count": missing_active_image_count,
            "active_unsafe_image_url_count": active_unsafe_image_url_count,
            "active_unsafe_image_url_product_ids": summary.get("active_unsafe_image_url_product_ids") or [],
            "active_non_https_image_url_count": active_non_https_image_url_count,
            "active_non_https_image_url_product_ids": summary.get("active_non_https_image_url_product_ids") or [],
            "missing_active_product_url_count": missing_active_product_url_count,
            "missing_active_product_url_product_ids": summary.get("missing_active_product_url_product_ids") or [],
            "missing_active_mall_id_count": missing_active_mall_id_count,
            "missing_active_mall_id_product_ids": summary.get("missing_active_mall_id_product_ids") or [],
            "active_unsafe_product_url_count": active_unsafe_product_url_count,
            "active_unsafe_product_url_product_ids": summary.get("active_unsafe_product_url_product_ids") or [],
            "active_product_url_product_id_mismatch_count": active_product_url_product_id_mismatch_count,
            "active_product_url_product_id_mismatch_product_ids": (
                summary.get("active_product_url_product_id_mismatch_product_ids") or []
            ),
            "duplicate_product_ids": duplicate_product_ids,
            "problems": problems,
        },
    )


def check_mall_config(expected_malls: int) -> EvidenceCheck:
    def _check(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        mall_count = int(data.get("mall_count", 0) or 0)
        enabled_count = int(data.get("enabled_count", 0) or 0)
        enabled_mall_ids = data.get("enabled_mall_ids") if isinstance(data.get("enabled_mall_ids"), list) else []
        enabled_mall_id_values = [str(mall_id).strip() for mall_id in enabled_mall_ids if str(mall_id).strip()]
        enabled_mall_origins = data.get("enabled_mall_origins") if isinstance(data.get("enabled_mall_origins"), dict) else {}
        enabled_mall_origin_ids = sorted(
            mall_id
            for mall_id, origins in enabled_mall_origins.items()
            if str(mall_id).strip() and isinstance(origins, list) and origins
        )
        enabled_product_url_prefixes = (
            data.get("enabled_mall_product_url_prefixes")
            if isinstance(data.get("enabled_mall_product_url_prefixes"), dict)
            else {}
        )
        enabled_product_url_prefix_ids = sorted(
            mall_id
            for mall_id, prefix in enabled_product_url_prefixes.items()
            if str(mall_id).strip() and str(prefix or "").strip()
        )
        enabled_api_key_hashes = (
            data.get("enabled_mall_api_key_hashes")
            if isinstance(data.get("enabled_mall_api_key_hashes"), dict)
            else {}
        )
        enabled_api_key_hash_ids = sorted(
            mall_id
            for mall_id, key_hash in enabled_api_key_hashes.items()
            if str(mall_id).strip() and str(key_hash or "").strip()
        )
        report_problems = data.get("problems")
        duplicate_allowed_origins = (
            data.get("duplicate_allowed_origins")
            if isinstance(data.get("duplicate_allowed_origins"), dict)
            else {}
        )
        duplicate_api_keys = data.get("duplicate_api_keys") if isinstance(data.get("duplicate_api_keys"), list) else []
        duplicate_product_url_prefixes = (
            data.get("duplicate_product_url_prefixes")
            if isinstance(data.get("duplicate_product_url_prefixes"), dict)
            else {}
        )
        weak_api_key_mall_ids = (
            [str(mall_id).strip() for mall_id in data.get("weak_api_key_mall_ids") if str(mall_id).strip()]
            if isinstance(data.get("weak_api_key_mall_ids"), list)
            else None
        )
        api_key_strength = data.get("api_key_strength") if isinstance(data.get("api_key_strength"), dict) else {}
        readiness_problems = []
        if data.get("ok") is not True:
            readiness_problems.append("ok")
        if mall_count < expected_malls:
            readiness_problems.append("mall_count")
        if enabled_count < expected_malls:
            readiness_problems.append("enabled_count")
        if not isinstance(data.get("enabled_mall_ids"), list):
            readiness_problems.append("enabled_mall_ids")
        elif len(enabled_mall_id_values) < expected_malls:
            readiness_problems.append("enabled_mall_ids.count")
        elif len(set(enabled_mall_id_values)) != len(enabled_mall_id_values):
            readiness_problems.append("enabled_mall_ids.duplicates")
        if not isinstance(data.get("enabled_mall_origins"), dict):
            readiness_problems.append("enabled_mall_origins")
        elif len(enabled_mall_origin_ids) < expected_malls:
            readiness_problems.append("enabled_mall_origins.count")
        if not isinstance(data.get("enabled_mall_product_url_prefixes"), dict):
            readiness_problems.append("enabled_mall_product_url_prefixes")
        elif len(enabled_product_url_prefix_ids) < expected_malls:
            readiness_problems.append("enabled_mall_product_url_prefixes.count")
        if not isinstance(data.get("enabled_mall_api_key_hashes"), dict):
            readiness_problems.append("enabled_mall_api_key_hashes")
        elif len(enabled_api_key_hash_ids) < expected_malls:
            readiness_problems.append("enabled_mall_api_key_hashes.count")
        if duplicate_api_keys:
            readiness_problems.append("duplicate_api_keys")
        if duplicate_allowed_origins:
            readiness_problems.append("duplicate_allowed_origins")
        if duplicate_product_url_prefixes:
            readiness_problems.append("duplicate_product_url_prefixes")
        if weak_api_key_mall_ids is None:
            readiness_problems.append("weak_api_key_mall_ids")
        elif weak_api_key_mall_ids:
            readiness_problems.append("weak_api_key_mall_ids")
        if not api_key_strength or api_key_strength.get("required") is not True:
            readiness_problems.append("api_key_strength")
        if "problems" not in data:
            readiness_problems.append("problems_missing")
            problem_entries = []
        elif not isinstance(report_problems, list):
            readiness_problems.append("problems_format")
            problem_entries = []
        else:
            problem_entries = report_problems
            if problem_entries:
                readiness_problems.append("problems")
        problem_fields = sorted(
            {
                str(problem.get("field") or "unknown")
                for problem in problem_entries
                if isinstance(problem, dict)
            }
        )
        problem_samples = [problem for problem in problem_entries[:5] if isinstance(problem, dict)]
        ok = not readiness_problems
        return (
            ok,
            "mall config passed expected rollout count and security policy"
            if ok
            else "mall config is invalid, incomplete, or below expected rollout count",
            {
                "mall_count": mall_count,
                "enabled_count": enabled_count,
                "expected_malls": expected_malls,
                "enabled_mall_ids_count": len(enabled_mall_id_values),
                "enabled_mall_id_samples": enabled_mall_id_values[:5],
                "enabled_mall_origins_count": len(enabled_mall_origin_ids),
                "enabled_mall_origin_samples": {
                    mall_id: enabled_mall_origins.get(mall_id)
                    for mall_id in enabled_mall_origin_ids[:3]
                },
                "enabled_mall_product_url_prefixes_count": len(enabled_product_url_prefix_ids),
                "enabled_mall_product_url_prefix_samples": {
                    mall_id: enabled_product_url_prefixes.get(mall_id)
                    for mall_id in enabled_product_url_prefix_ids[:3]
                },
                "enabled_mall_api_key_hashes_count": len(enabled_api_key_hash_ids),
                "duplicate_api_keys": duplicate_api_keys[:5],
                "duplicate_allowed_origins": {
                    origin: mall_ids
                    for origin, mall_ids in list(duplicate_allowed_origins.items())[:5]
                },
                "duplicate_product_url_prefixes": {
                    prefix: mall_ids
                    for prefix, mall_ids in list(duplicate_product_url_prefixes.items())[:5]
                },
                "api_key_strength": api_key_strength,
                "weak_api_key_mall_ids": (weak_api_key_mall_ids or [])[:20],
                "weak_api_key_count": len(weak_api_key_mall_ids or []),
                "problem_count": len(problem_entries),
                "problem_fields": problem_fields,
                "problem_samples": problem_samples,
                "problems": readiness_problems,
            },
        )

    return _check


def check_mall_config_build(expected_malls: int) -> EvidenceCheck:
    def _check(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        mall_count = parse_int_value(data.get("mall_count"), -1)
        enabled_count = parse_int_value(data.get("enabled_count"), -1)
        generated_api_key_count = parse_int_value(data.get("generated_api_key_count"), -1)
        duplicate_mall_ids = data.get("duplicate_mall_ids") if isinstance(data.get("duplicate_mall_ids"), list) else []
        duplicate_api_keys = data.get("duplicate_api_keys") if isinstance(data.get("duplicate_api_keys"), list) else []
        duplicate_allowed_origins = (
            data.get("duplicate_allowed_origins")
            if isinstance(data.get("duplicate_allowed_origins"), dict)
            else {}
        )
        duplicate_product_url_prefixes = (
            data.get("duplicate_product_url_prefixes")
            if isinstance(data.get("duplicate_product_url_prefixes"), dict)
            else {}
        )
        report_problems = data.get("problems")
        if isinstance(report_problems, list):
            problem_entries = report_problems
        else:
            problem_entries = []
        validation = data.get("validation") if isinstance(data.get("validation"), dict) else {}
        validation_hashes = (
            validation.get("enabled_mall_api_key_hashes")
            if isinstance(validation.get("enabled_mall_api_key_hashes"), dict)
            else {}
        )
        validation_problems = validation.get("problems") if isinstance(validation.get("problems"), list) else []
        validation_enabled_count = parse_int_value(validation.get("enabled_count"), -1)
        readiness_problems: list[str] = []
        if data.get("ok") is not True:
            readiness_problems.append("ok")
        if not str(data.get("input") or "").strip():
            readiness_problems.append("input")
        if not str(data.get("output") or "").strip():
            readiness_problems.append("output")
        if "config" in data:
            readiness_problems.append("raw_config_embedded")
        if mall_count < expected_malls:
            readiness_problems.append("mall_count")
        if enabled_count < expected_malls:
            readiness_problems.append("enabled_count")
        if generated_api_key_count != 0:
            readiness_problems.append("generated_api_key_count")
        if duplicate_mall_ids:
            readiness_problems.append("duplicate_mall_ids")
        if duplicate_api_keys:
            readiness_problems.append("duplicate_api_keys")
        if duplicate_allowed_origins:
            readiness_problems.append("duplicate_allowed_origins")
        if duplicate_product_url_prefixes:
            readiness_problems.append("duplicate_product_url_prefixes")
        if "problems" not in data:
            readiness_problems.append("problems_missing")
        elif not isinstance(report_problems, list):
            readiness_problems.append("problems_format")
        elif problem_entries:
            readiness_problems.append("problems")
        if validation.get("ok") is not True:
            readiness_problems.append("validation.ok")
        if validation_enabled_count < expected_malls:
            readiness_problems.append("validation.enabled_count")
        if len(validation_hashes) < expected_malls:
            readiness_problems.append("validation.enabled_mall_api_key_hashes.count")
        if validation_problems:
            readiness_problems.append("validation.problems")
        ok = not readiness_problems
        return (
            ok,
            "mall config builder report proves the rollout config was generated from an export without fallback keys"
            if ok
            else "mall config builder report is missing, unsafe, or does not validate the rollout config",
            {
                "input": data.get("input"),
                "output": data.get("output"),
                "mall_count": mall_count,
                "enabled_count": enabled_count,
                "expected_malls": expected_malls,
                "generated_api_key_count": generated_api_key_count,
                "duplicate_mall_ids": duplicate_mall_ids[:5],
                "duplicate_api_keys": duplicate_api_keys[:5],
                "duplicate_allowed_origins_count": len(duplicate_allowed_origins),
                "duplicate_product_url_prefixes_count": len(duplicate_product_url_prefixes),
                "problem_count": len(problem_entries),
                "validation_path": validation.get("path"),
                "validation_enabled_count": validation_enabled_count,
                "validation_enabled_api_key_hash_count": len(validation_hashes),
                "validation_problem_count": len(validation_problems),
                "raw_config_embedded": "config" in data,
                "problems": readiness_problems,
            },
        )

    return _check


def check_marqo_resource(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    health = data.get("health") or {}
    index_stats = data.get("index_stats") or {}
    index_stats_data = index_stats.get("data") if isinstance(index_stats.get("data"), dict) else {}
    index_settings = data.get("index_settings") or {}
    index_settings_contract = data.get("index_settings_contract") or {}
    embedding_health = data.get("embedding_health") or data.get("gemini_health") or data.get("qwen_health") or {}
    embedding_probe = data.get("embedding_probe") or data.get("gemini_embedding_probe") or data.get("qwen_embedding_probe") or {}
    image_embedding_probe = (
        data.get("image_embedding_probe")
        or data.get("gemini_image_embedding_probe")
        or data.get("qwen_image_embedding_probe")
        or {}
    )
    embedding_contract = (
        data.get("embedding_contract")
        or data.get("gemini_embedding_contract")
        or data.get("qwen_embedding_contract")
        or {}
    )
    docker_stats = data.get("docker_stats") or {}
    resource_thresholds = data.get("resource_thresholds") or {}
    storage_usage = data.get("storage_usage") or {}
    storage_thresholds = data.get("storage_thresholds") or {}
    embedding_backend = str(
        index_settings_contract.get("embedding_backend") or data.get("embedding_backend") or ""
    ).strip().lower()
    embedding_required = external_embedding_backend(embedding_backend)
    embedding_provider = "gemini" if embedding_backend == "gemini" else "qwen" if embedding_backend == "qwen" else "embedding"
    embedding_health_data = embedding_health.get("data") if isinstance(embedding_health.get("data"), dict) else {}
    minimum_index_documents = 300
    index_documents = parse_int_value(
        index_stats_data.get("numberOfDocuments", index_stats_data.get("number_of_documents")),
        default=-1,
    )
    required = {
        "health": health.get("ok") is True,
        "index_stats": index_stats.get("ok") is True,
        "index_settings": index_settings.get("ok") is True,
        "index_settings_contract": (
            index_settings_contract.get("ok") is True
            and not (index_settings_contract.get("problems") or [])
        ),
        "embedding_url": (not embedding_required) or bool(str(data.get("embedding_url") or data.get(f"{embedding_provider}_embedding_url") or "").strip()),
        "embedding_health": (
            (not embedding_required)
            or (embedding_health.get("ok") is True and embedding_health.get("skipped") is not True)
        ),
        "embedding_ready": (not embedding_required) or embedding_health_data.get("ready") is True,
        "embedding_probe": (
            (not embedding_required)
            or (embedding_probe.get("ok") is True and embedding_probe.get("skipped") is not True)
        ),
        "image_embedding_probe": (
            (not embedding_required)
            or (
                image_embedding_probe.get("ok") is True
                and image_embedding_probe.get("skipped") is not True
            )
        ),
        "embedding_contract": (
            (not embedding_required)
            or (
                embedding_contract.get("ok") is True
                and embedding_contract.get("skipped") is not True
                and not (embedding_contract.get("problems") or [])
            )
        ),
        "index": bool(str(data.get("index") or "").strip()),
        "index_documents": index_documents >= minimum_index_documents,
        "docker_stats": docker_stats.get("ok") is True and docker_stats.get("skipped") is not True,
        "cpu_percent": is_number(docker_stats.get("cpu_percent")),
        "memory_usage_bytes": is_number(docker_stats.get("memory_usage_bytes")),
        "memory_limit_bytes": is_number(docker_stats.get("memory_limit_bytes")),
        "memory_percent": is_number(docker_stats.get("memory_percent")),
        "resource_thresholds": (
            resource_thresholds.get("ok") is True
            and resource_thresholds.get("skipped") is not True
            and not (resource_thresholds.get("problems") or [])
        ),
        "storage_usage": storage_usage.get("ok") is True and storage_usage.get("skipped") is not True,
        "storage_used_percent": is_number(storage_usage.get("used_percent")),
        "storage_available_bytes": is_number(storage_usage.get("available_bytes")),
        "storage_thresholds": (
            storage_thresholds.get("ok") is True
            and storage_thresholds.get("skipped") is not True
            and not (storage_thresholds.get("problems") or [])
        ),
    }
    missing_or_false = sorted(name for name, ok in required.items() if not ok)
    if embedding_provider in {"gemini", "qwen"}:
        provider_aliases = {
            "embedding_url": f"{embedding_provider}_embedding_url",
            "embedding_health": f"{embedding_provider}_health",
            "embedding_ready": f"{embedding_provider}_ready",
            "embedding_probe": f"{embedding_provider}_embedding_probe",
            "image_embedding_probe": f"{embedding_provider}_image_embedding_probe",
            "embedding_contract": f"{embedding_provider}_embedding_contract",
        }
        missing_or_false = sorted(provider_aliases.get(name, name) for name in missing_or_false)
    ok = data.get("ok") is True and not missing_or_false
    return (
        ok,
        "Marqo health and resource evidence passed" if ok else "Marqo health/resource evidence is incomplete",
        {
            "marqo_url": data.get("marqo_url"),
            "index": data.get("index"),
            "container": data.get("container"),
            "storage_container": data.get("storage_container"),
            "storage_path": data.get("storage_path"),
            "embedding_backend": embedding_backend or None,
            "embedding_provider": embedding_provider,
            "embedding_provider_required": embedding_required,
            "qwen_required": embedding_required,
            "embedding_url": data.get("embedding_url") or data.get(f"{embedding_provider}_embedding_url"),
            "qwen_embedding_url": data.get("qwen_embedding_url"),
            "gemini_embedding_url": data.get("gemini_embedding_url"),
            "missing_or_false": missing_or_false,
            "index_settings_contract": index_settings_contract,
            "embedding_health": embedding_health,
            "embedding_probe": embedding_probe,
            "image_embedding_probe": image_embedding_probe,
            "embedding_contract": embedding_contract,
            "qwen_health": data.get("qwen_health") or embedding_health,
            "qwen_embedding_probe": data.get("qwen_embedding_probe") or embedding_probe,
            "qwen_image_embedding_probe": data.get("qwen_image_embedding_probe") or image_embedding_probe,
            "qwen_embedding_contract": data.get("qwen_embedding_contract") or embedding_contract,
            "gemini_health": data.get("gemini_health") or (embedding_health if embedding_provider == "gemini" else None),
            "gemini_embedding_probe": data.get("gemini_embedding_probe") or (embedding_probe if embedding_provider == "gemini" else None),
            "gemini_image_embedding_probe": data.get("gemini_image_embedding_probe") or (
                image_embedding_probe if embedding_provider == "gemini" else None
            ),
            "gemini_embedding_contract": data.get("gemini_embedding_contract") or (
                embedding_contract if embedding_provider == "gemini" else None
            ),
            "index_documents": index_documents if index_documents >= 0 else None,
            "minimum_index_documents": minimum_index_documents,
            "cpu_percent": docker_stats.get("cpu_percent"),
            "memory_usage_bytes": docker_stats.get("memory_usage_bytes"),
            "memory_limit_bytes": docker_stats.get("memory_limit_bytes"),
            "memory_percent": docker_stats.get("memory_percent"),
            "resource_thresholds": resource_thresholds,
            "storage_usage": storage_usage,
            "storage_thresholds": storage_thresholds,
            "storage_min_available_bytes": storage_thresholds.get("min_available_bytes"),
        },
    )


def check_server_preflight(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    checks = data.get("checks", [])
    by_name = {str(check.get("name")): check for check in checks if isinstance(check, dict)}
    required_checks = {
        "linux_host",
        "supported_linux_release",
        "python_version",
        "python_modules",
        "odbc_driver",
        "host_resources",
        "docker",
        "docker_compose",
    }
    missing = sorted(required_checks - set(by_name))
    failed = sorted(
        name
        for name, check in by_name.items()
        if name in required_checks and check.get("ok") is not True
    )
    docker_required = (by_name.get("docker") or {}).get("required") is True
    compose_required = (by_name.get("docker_compose") or {}).get("required") is True
    required_modules = set((by_name.get("python_modules") or {}).get("required") or [])
    problems = []
    if not docker_required:
        problems.append("docker.required")
    if not compose_required:
        problems.append("docker_compose.required")
    if "pyodbc" not in required_modules:
        problems.append("python_modules.pyodbc")
    supported_linux = by_name.get("supported_linux_release") or {}
    if supported_linux.get("required") is not True:
        problems.append("supported_linux_release.required")
    odbc_driver = by_name.get("odbc_driver") or {}
    if odbc_driver.get("required") is not True:
        problems.append("odbc_driver.required")
    if odbc_driver.get("expected_driver") != "ODBC Driver 18 for SQL Server":
        problems.append("odbc_driver.expected_driver")
    resources = by_name.get("host_resources") or {}
    resource_requirements = resources.get("requirements") if isinstance(resources.get("requirements"), dict) else {}
    minimum_resource_requirements = {
        "min_cpu": 4,
        "min_memory_gb": 8,
        "min_disk_free_gb": 20,
        "min_open_files": 65535,
    }
    if not resource_requirements:
        problems.append("host_resources.requirements")
    for key, minimum in minimum_resource_requirements.items():
        if parse_float_value(resource_requirements.get(key), 0.0) < float(minimum):
            problems.append(f"host_resources.requirements.{key}")
    cpu_count = parse_int_value(resources.get("cpu_count"))
    memory_total_gb = parse_float_value(resources.get("memory_total_gb"))
    disk_free_gb = parse_float_value(resources.get("disk_free_gb"))
    open_file_limit_soft = parse_int_value(resources.get("open_file_limit_soft"))
    open_file_limit_hard = parse_int_value(resources.get("open_file_limit_hard"))
    min_cpu = parse_int_value(
        resource_requirements.get("min_cpu"),
        int(minimum_resource_requirements["min_cpu"]),
    )
    min_memory_gb = parse_float_value(
        resource_requirements.get("min_memory_gb"),
        float(minimum_resource_requirements["min_memory_gb"]),
    )
    min_disk_free_gb = parse_float_value(
        resource_requirements.get("min_disk_free_gb"),
        float(minimum_resource_requirements["min_disk_free_gb"]),
    )
    min_open_files = parse_int_value(
        resource_requirements.get("min_open_files"),
        int(minimum_resource_requirements["min_open_files"]),
    )
    resource_problems = list(resources.get("problems") or [])
    if resource_problems:
        problems.append("host_resources.problems")
    if cpu_count < min_cpu:
        problems.append("host_resources.cpu_count")
    if memory_total_gb < min_memory_gb:
        problems.append("host_resources.memory_total_gb")
    if disk_free_gb < min_disk_free_gb:
        problems.append("host_resources.disk_free_gb")
    if open_file_limit_soft < min_open_files:
        problems.append("host_resources.open_file_limit_soft")
    if open_file_limit_hard and open_file_limit_hard < min_open_files:
        problems.append("host_resources.open_file_limit_hard")
    ok = data.get("ok") is True and not missing and not failed and not problems
    return (
        ok,
        "deployment server preflight passed" if ok else "deployment server preflight is incomplete or failed",
        {
            "role": data.get("role"),
            "missing_checks": missing,
            "failed_checks": failed,
            "problems": problems,
            "linux_release": {
                "id": supported_linux.get("id"),
                "version_id": supported_linux.get("version_id"),
                "baseline": supported_linux.get("baseline"),
                "minimum_version": supported_linux.get("minimum_version"),
            },
            "cpu_count": cpu_count,
            "memory_total_gb": memory_total_gb,
            "disk_free_gb": disk_free_gb,
            "open_file_limit_soft": open_file_limit_soft,
            "open_file_limit_hard": open_file_limit_hard,
            "resource_requirements": resource_requirements,
            "resource_problems": resource_problems,
        },
    )


def check_env_preflight(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    checks = data.get("checks", [])
    by_name = {str(check.get("name")): check for check in checks if isinstance(check, dict)}
    embedding_config_check = (
        "gemini_embedding_config" if "gemini_embedding_config" in by_name else "qwen_embedding_config"
    )
    required_checks = {
        "env_loaded",
        "env_file_permissions",
        "api_server_count",
        "required_variables",
        "production_env",
        "search_engine",
        "marqo_url",
        embedding_config_check,
        "admin_key",
        "cors_origins",
        "product_url_template",
        "data_source",
        "mall_config_path",
        "mall_security",
        "redis_required_for_scale",
        "cache_ttl_required_for_scale",
        "trusted_proxy_ips",
        "sync_interval_hourly",
        "sync_alert_webhook",
        "numeric_settings",
        "settings_load",
    }
    missing = sorted(required_checks - set(by_name))
    failed = sorted(
        name
        for name, check in by_name.items()
        if name in required_checks and check.get("ok") is not True
    )
    problems = []
    if data.get("require_production") is not True:
        problems.append("require_production")
    if data.get("require_paths") is not True:
        problems.append("require_paths")
    if int(data.get("api_server_count", 0) or 0) < 1:
        problems.append("api_server_count")
    ok = data.get("ok") is True and not missing and not failed and not problems
    return (
        ok,
        "deployment env preflight passed" if ok else "deployment env preflight is incomplete",
        {
            "env_file": data.get("env_file"),
            "role": data.get("role"),
            "api_server_count": data.get("api_server_count"),
            "missing_checks": missing,
            "failed_checks": failed,
            "problems": problems,
        },
    )


def check_load(
    mode: str,
    min_concurrency: int = 0,
    min_active_users: int = 0,
    min_requests: int = 0,
) -> EvidenceCheck:
    def _check(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        requests = int(data.get("requests", 0) or 0)
        concurrency = int(data.get("concurrency", 0) or 0)
        active_users = int(data.get("active_users", 0) or 0)
        mode_counts = data.get("mode_counts") or {}
        threshold_ok, threshold_problems = check_load_thresholds(data)
        server_ok, server_missing = check_load_server_metrics(data, mode)
        client_transport_ok, client_transport_problems = check_load_client_transport(data)
        api_instance_ok, api_instance_problems, api_instance_details = check_load_api_instance_coverage(data)
        image_source_ok, image_source_problems = check_load_image_source(data, mode)
        mall_identity_ok, mall_identity_problems, mall_identity_details = check_load_mall_identity_coverage(data, mode)
        response_contract = data.get("response_contract") or {}
        client_transport = data.get("client_transport") or {}
        response_engine_ok, response_engine_details = check_load_response_engine(response_contract)
        response_shape_ok, response_shape_problems, response_shape_details = check_load_response_contract_shape(
            response_contract
        )
        response_mall_ok, response_mall_problems, response_mall_details = check_load_response_mall_identity(
            response_contract,
            data.get("mall_id"),
        )
        response_contract_ok = (
            response_contract.get("ok") is True
            and response_engine_ok
            and response_shape_ok
            and response_mall_ok
        )
        query_type_ok, query_type_problems, query_type_details = check_load_query_type_coverage(
            response_contract,
            mode_counts,
            data.get("thresholds") or {},
        )
        query_type_latency_ok, query_type_latency_problems, query_type_latency_details = check_load_query_type_latency(data)
        target_validation = data.get("target_validation") or {}
        target_validation_matches = (
            target_validation.get("base_url") == data.get("base_url")
            and (target_validation.get("origin") or None) == (data.get("origin") or None)
        )
        target_validation_ok = target_validation.get("ok") is True and target_validation_matches
        url_problems = (
            operational_https_url_problems("base_url", data.get("base_url"))
            + operational_https_url_problems("origin", data.get("origin"), origin_only=True)
        )
        ok = (
            data.get("ok") is True
            and requests >= min_requests
            and concurrency >= min_concurrency
            and active_users >= min_active_users
            and threshold_ok
            and client_transport_ok
            and api_instance_ok
            and image_source_ok
            and mall_identity_ok
            and response_contract_ok
            and query_type_ok
            and query_type_latency_ok
            and target_validation_ok
            and not url_problems
        )
        if mode != "mixed-traffic":
            ok = ok and data.get("mode") == mode and int(mode_counts.get(mode, 0) or 0) >= min_requests
        else:
            required_mode_counts = {
                traffic_mode: int(mode_counts.get(traffic_mode, 0) or 0) for traffic_mode in ["text", "image", "mixed"]
            }
            ok = ok and data.get("scenario") == "mixed-traffic" and all(count > 0 for count in required_mode_counts.values())
        ok = ok and server_ok
        return (
            ok,
            f"{mode} load report passed" if ok else f"{mode} load report failed or used too small a scenario",
            {
                "scenario": data.get("scenario"),
                "mode": data.get("mode"),
                "requests": requests,
                "active_users": active_users,
                "concurrency": concurrency,
                "mode_counts": mode_counts,
                "error_rate": data.get("error_rate"),
                "p95_ms": (data.get("latency_ms") or {}).get("p95"),
                "p99_ms": (data.get("latency_ms") or {}).get("p99"),
                "required_requests": min_requests,
                "required_concurrency": min_concurrency,
                "required_active_users": min_active_users,
                "thresholds_ok": threshold_ok,
                "threshold_problems": threshold_problems,
                "image_source_ok": image_source_ok,
                "image_source_problems": image_source_problems,
                "image_input": data.get("image_input") or {},
                "mall_identity_ok": mall_identity_ok,
                "mall_identity_problems": mall_identity_problems,
                "mall_identity": mall_identity_details,
                "response_contract_ok": response_contract_ok,
                "response_engine_ok": response_engine_ok,
                "response_engine": response_engine_details,
                "response_shape_ok": response_shape_ok,
                "response_shape_problems": response_shape_problems,
                "response_shape": response_shape_details,
                "response_mall_identity_ok": response_mall_ok,
                "response_mall_identity_problems": response_mall_problems,
                "response_mall_identity": response_mall_details,
                "query_type_coverage_ok": query_type_ok,
                "query_type_coverage_problems": query_type_problems,
                "query_type_coverage": query_type_details,
                "query_type_latency_ok": query_type_latency_ok,
                "query_type_latency_problems": query_type_latency_problems,
                "query_type_latency": query_type_latency_details,
                "response_contract": response_contract,
                "target_validation_ok": target_validation_ok,
                "target_validation_matches": target_validation_matches,
                "target_validation_error": target_validation.get("error"),
                "url_problems": url_problems,
                "server_metrics_ok": server_ok,
                "server_metrics_missing": server_missing,
                "client_transport_ok": client_transport_ok,
                "client_transport_problems": client_transport_problems,
                "api_instance_coverage_ok": api_instance_ok,
                "api_instance_coverage_problems": api_instance_problems,
                "api_instance_coverage": api_instance_details,
                "server_metrics_delta": (data.get("server_metrics") or {}).get("delta") or {},
                "engine_backend": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("engine_backend"),
                "engine_index": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("engine_index"),
                "marqo_model": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("marqo_model"),
                "embedding_backend": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("embedding_backend"),
                "qwen_model": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("qwen_model"),
                "qwen_embedding_dimensions": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("qwen_embedding_dimensions"),
                "rate_limit_backend": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("rate_limit_backend"),
                "rate_limit_redis_enabled": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("rate_limit_redis_enabled"),
                "cache_backend": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("cache_backend"),
                "cache_redis_enabled": ((data.get("server_metrics") or {}).get("after") or {})
                .get("snapshot", {})
                .get("cache_redis_enabled"),
                "server_metrics_coverage_ok": (data.get("server_metrics") or {}).get("coverage_ok"),
                "server_metrics_run_log_coverage": (data.get("server_metrics") or {}).get("run_log_coverage") or {},
                "client_transport": client_transport if isinstance(client_transport, dict) else {},
            },
        )

    return _check


def check_load_mall_identity_coverage(
    data: dict[str, Any],
    mode: str,
    min_sample: int = MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE,
) -> tuple[bool, list[str], dict[str, Any]]:
    raw_identity = data.get("mall_identity")
    identity = raw_identity if isinstance(raw_identity, dict) else {}
    response_contract = data.get("response_contract") if isinstance(data.get("response_contract"), dict) else {}
    expected_mall_id_counts = (
        response_contract.get("expected_mall_id_counts")
        if isinstance(response_contract.get("expected_mall_id_counts"), dict)
        else {}
    )
    if not expected_mall_id_counts:
        response_mall_identity = (
            data.get("response_mall_identity") if isinstance(data.get("response_mall_identity"), dict) else {}
        )
        expected_mall_id_counts = (
            response_mall_identity.get("expected_mall_id_counts")
            if isinstance(response_mall_identity.get("expected_mall_id_counts"), dict)
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
    if mode == "mixed-traffic":
        if not details["enabled"]:
            problems.append("mall_identity.enabled")
        if details["sample_size_requested"] < min_sample:
            problems.append("mall_identity.sample_size_requested")
        if (
            details["source_enabled_count"] > details["sampled_count"]
            and details["sampling_strategy"] != "spread"
        ):
            problems.append("mall_identity.sampling_strategy")
        if details["source_enabled_count"] > 0 and details["source_enabled_count"] < min_sample:
            problems.append("mall_identity.source_enabled_count")
        if details["eligible_mall_count"] > 0 and details["eligible_mall_count"] < min_sample:
            problems.append("mall_identity.eligible_mall_count")
        if details["sampled_count"] < min_sample:
            problems.append("mall_identity.sampled_count")
        if details["distinct_mall_count"] < min_sample:
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
    return not problems, sorted(set(problems)), details


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


def check_load_query_type_coverage(
    response_contract: dict[str, Any],
    mode_counts: dict[str, Any],
    thresholds: dict[str, Any],
) -> tuple[bool, list[str], dict[str, Any]]:
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
    if expected_query_type_counts:
        if normalized_expected_query_type_counts != planned_query_type_counts:
            problems.append("response_contract.expected_query_type_counts")
    if parse_int_value(response_contract.get("unexpected_query_type_count"), 0) != 0:
        problems.append("response_contract.unexpected_query_type_count")
    return not problems, sorted(set(problems)), {
        "planned_query_type_counts": planned_query_type_counts,
        "observed_query_type_counts": observed_query_type_counts,
        "expected_query_type_counts": normalized_expected_query_type_counts,
        "minimum_observed_query_type_counts": minimum_observed_query_type_counts,
        "allowed_shortfall": allowed_shortfall,
        "unexpected_observed_query_types": unexpected_observed_query_types,
    }


def check_load_query_type_latency(data: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    mode_counts = data.get("mode_counts") or {}
    thresholds = data.get("thresholds") or {}
    planned_query_type_counts = expected_query_type_counts_from_mode_counts(mode_counts)
    expected_latency = normalized_query_type_latency(data.get("expected_query_type_latency_ms"))
    response_latency = normalized_query_type_latency(data.get("response_query_type_latency_ms"))
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
    details = {
        "planned_query_type_counts": planned_query_type_counts,
        "minimum_response_counts": minimum_response_counts,
        "allowed_shortfall": allowed_shortfall,
        "threshold_p95_ms": float(p95_limit) if is_number(p95_limit) else None,
        "threshold_p99_ms": float(p99_limit) if is_number(p99_limit) else None,
        "expected_query_type_latency_ms": expected_latency,
        "response_query_type_latency_ms": response_latency,
    }
    return not problems, sorted(set(problems)), details


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


def check_load_client_transport(data: dict[str, Any]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    client_transport = data.get("client_transport") if isinstance(data.get("client_transport"), dict) else {}
    search_requests = client_transport.get("search_requests") if isinstance(client_transport, dict) else {}
    if client_transport.get("connection_reuse") != "thread_local_keep_alive":
        problems.append("client_transport.connection_reuse")
    if not isinstance(search_requests, dict):
        return False, sorted(set([*problems, "client_transport.search_requests"]))
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
    requests = parse_int_value(data.get("requests"), 0)
    concurrency = parse_int_value(data.get("concurrency"), 0)
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
    problems = sorted(set(problems))
    return not problems, problems


def check_load_api_instance_coverage(data: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    api_server_count = parse_int_value(data.get("api_server_count"), 1)
    coverage = data.get("api_instance_coverage") if isinstance(data.get("api_instance_coverage"), dict) else {}
    if api_server_count < 2:
        return True, [], coverage
    if not coverage:
        return False, ["api_instance_coverage"], {}
    problems = [str(problem) for problem in (coverage.get("problems") or []) if str(problem or "").strip()]
    if coverage.get("ok") is not True and not problems:
        problems.append("api_instance_coverage.ok")
    required = max(2, parse_int_value(coverage.get("required_distinct_api_instances"), api_server_count))
    distinct = parse_int_value(coverage.get("distinct_api_instance_count"), 0)
    if distinct < required and "api_instance.distinct_count" not in problems:
        problems.append("api_instance.distinct_count")
    if parse_int_value(coverage.get("missing_header_count"), 0) > 0 and "api_instance.missing_header" not in problems:
        problems.append("api_instance.missing_header")
    problems = sorted(set(problems))
    return not problems, problems, coverage


def check_load_response_engine(response_contract: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    valid_successful = parse_int_value(response_contract.get("valid_successful_responses"), 0)
    raw_counts = response_contract.get("engine_counts") or {}
    engine_counts = raw_counts if isinstance(raw_counts, dict) else {}
    marqo_count = sum(
        parse_int_value(count, 0)
        for engine, count in engine_counts.items()
        if str(engine or "").strip().lower() == "marqo"
    )
    if "non_marqo_engine_responses" in response_contract:
        non_marqo = parse_int_value(response_contract.get("non_marqo_engine_responses"), valid_successful)
    else:
        non_marqo = sum(
            parse_int_value(count, 0)
            for engine, count in engine_counts.items()
            if str(engine or "").strip().lower() != "marqo"
        )
        if not engine_counts and valid_successful > 0:
            non_marqo = valid_successful
    ok = bool(engine_counts) and valid_successful > 0 and marqo_count >= valid_successful and non_marqo == 0
    return ok, {
        "engine_counts": engine_counts,
        "valid_successful_responses": valid_successful,
        "marqo_responses": marqo_count,
        "non_marqo_engine_responses": non_marqo,
    }


def check_load_response_contract_shape(response_contract: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
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

    details = {
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
    return not problems, sorted(set(problems)), details


def check_load_response_mall_identity(
    response_contract: dict[str, Any],
    expected_mall_id: Any,
) -> tuple[bool, list[str], dict[str, Any]]:
    expected = str(expected_mall_id or "").strip()
    successful = parse_int_value(response_contract.get("successful_responses"), 0)
    expected_counts = (
        response_contract.get("expected_mall_id_counts")
        if isinstance(response_contract.get("expected_mall_id_counts"), dict)
        else {}
    )
    meta_counts = (
        response_contract.get("meta_mall_id_counts")
        if isinstance(response_contract.get("meta_mall_id_counts"), dict)
        else {}
    )
    result_counts = (
        response_contract.get("result_mall_id_counts")
        if isinstance(response_contract.get("result_mall_id_counts"), dict)
        else {}
    )
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

    details = {
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
    return not problems, sorted(set(problems)), details


def check_load_image_source(
    data: dict[str, Any],
    mode: str,
    *,
    min_image_inputs: int = MIN_LOAD_IMAGE_INPUTS,
) -> tuple[bool, list[str]]:
    if mode == "text":
        return True, []
    image_input = data.get("image_input") or {}
    problems = []
    source = image_input.get("source")
    min_image_inputs = max(1, int(min_image_inputs or 1))
    if source not in {"file", "files"} or (min_image_inputs > 1 and source != "files"):
        problems.append("image_input.source")
    if not str(image_input.get("file") or "").strip():
        problems.append("image_input.file")
    if not is_number(image_input.get("size_bytes")) or int(image_input.get("size_bytes") or 0) <= 0:
        problems.append("image_input.size_bytes")
    if not is_number(image_input.get("width")) or int(image_input.get("width") or 0) < 16:
        problems.append("image_input.width")
    if not is_number(image_input.get("height")) or int(image_input.get("height") or 0) < 16:
        problems.append("image_input.height")
    if not str(image_input.get("sha256") or "").strip():
        problems.append("image_input.sha256")
    if source == "files":
        files = image_input.get("files") if isinstance(image_input.get("files"), list) else []
        file_count = parse_int_value(image_input.get("file_count"), 0)
        unique_sha256_count = parse_int_value(image_input.get("unique_sha256_count"), 0)
        if file_count < min_image_inputs:
            problems.append("image_input.file_count")
        if len(files) != file_count:
            problems.append("image_input.files")
        if unique_sha256_count < min_image_inputs:
            problems.append("image_input.unique_sha256_count")
        images = image_input.get("images") if isinstance(image_input.get("images"), list) else []
        if images and len(images) < min(file_count, 50):
            problems.append("image_input.images")
        for index, image in enumerate(images[:50]):
            if not isinstance(image, dict):
                problems.append(f"image_input.images[{index}]")
                continue
            if not str(image.get("file") or "").strip():
                problems.append(f"image_input.images[{index}].file")
            if not is_number(image.get("size_bytes")) or int(image.get("size_bytes") or 0) <= 0:
                problems.append(f"image_input.images[{index}].size_bytes")
            if not is_number(image.get("width")) or int(image.get("width") or 0) < 16:
                problems.append(f"image_input.images[{index}].width")
            if not is_number(image.get("height")) or int(image.get("height") or 0) < 16:
                problems.append(f"image_input.images[{index}].height")
            if not str(image.get("sha256") or "").strip():
                problems.append(f"image_input.images[{index}].sha256")
    return not problems, problems


def check_load_thresholds(data: dict[str, Any]) -> tuple[bool, list[str]]:
    problems = []
    thresholds = data.get("thresholds") or {}
    latency = data.get("latency_ms") or {}
    error_rate = data.get("error_rate")
    max_error_rate = thresholds.get("max_error_rate")
    p95 = latency.get("p95")
    p95_limit = thresholds.get("p95_ms")
    p99 = latency.get("p99")
    p99_limit = thresholds.get("p99_ms")
    if not is_number(error_rate) or not is_number(max_error_rate):
        problems.append("error_rate/max_error_rate")
    elif float(error_rate) > float(max_error_rate):
        problems.append("error_rate")
    if not is_number(p95) or not is_number(p95_limit):
        problems.append("latency_ms.p95/thresholds.p95_ms")
    elif float(p95) > float(p95_limit):
        problems.append("latency_ms.p95")
    if not is_number(p99):
        problems.append("latency_ms.p99")
    elif is_number(p99_limit) and float(p99) > float(p99_limit):
        problems.append("latency_ms.p99")
    problems.extend(request_timeout_threshold_problems(thresholds))
    problems.extend(throughput_threshold_problems(data))
    return not problems, problems


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


def throughput_threshold_problems(data: dict[str, Any]) -> list[str]:
    thresholds = data.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        return ["thresholds.min_requests_per_second"]
    threshold = thresholds.get("min_requests_per_second")
    if not is_number(threshold) or float(threshold) <= 0:
        threshold = default_min_rps(data.get("concurrency"), thresholds.get("p95_ms"))
    if not is_number(threshold) or float(threshold) <= 0:
        return ["thresholds.min_requests_per_second"]
    rps = data.get("requests_per_second")
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


def qwen_query_vector_server_metric_problems(snapshot: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in QWEN_QUERY_VECTOR_SERVER_METRIC_FIELDS:
        if not is_number(snapshot.get(field)):
            missing.append(field)
    for field in [
        "qwen_query_vector_runtime_max_entries",
        "qwen_query_vector_runtime_text_max_entries",
        "qwen_query_vector_runtime_image_max_entries",
        "qwen_query_vector_wait_timeout_seconds",
    ]:
        if not is_positive_number(snapshot.get(field)) and field not in missing:
            missing.append(field)
    text_capacity = snapshot.get("qwen_query_vector_runtime_text_max_entries")
    image_capacity = snapshot.get("qwen_query_vector_runtime_image_max_entries")
    total_capacity = snapshot.get("qwen_query_vector_runtime_max_entries")
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


def api_threadpool_server_metric_problems(snapshot: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in API_THREADPOOL_SERVER_METRIC_FIELDS:
        if snapshot.get(field) in (None, ""):
            missing.append(f"server_metrics.after.{field}")
    if snapshot.get("api_threadpool_ok") is not True:
        missing.append("server_metrics.after.api_threadpool_ok")
    required = snapshot.get("api_threadpool_required_tokens")
    if not is_positive_number(required):
        missing.append("server_metrics.after.api_threadpool_required_tokens")
        return sorted(set(missing))
    for field in ["api_threadpool_configured_tokens", "api_threadpool_runtime_tokens"]:
        value = snapshot.get(field)
        problem = f"server_metrics.after.{field}"
        if not is_number(value):
            missing.append(problem)
        elif float(value) < float(required):
            missing.append(f"{problem}_below_required")
    return sorted(set(missing))


def server_settled_state_problems(
    snapshot: dict[str, Any],
    *,
    include_image_queue: bool = False,
) -> list[str]:
    problems: list[str] = []
    fields = list(SETTLED_SERVER_IN_FLIGHT_FIELDS)
    if include_image_queue:
        fields.append("image_queue_in_flight")
    for field in fields:
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


def request_profile_problems(
    request_profile: dict[str, Any] | None,
    *,
    embedding_backend: str | None = None,
    image_successful_responses: int | None = None,
    expected_requests: int | None = None,
    mode_counts: dict[str, Any] | None = None,
    scenario: str | None = None,
    min_unique_image_inputs: int = 0,
    min_distinct_mall_count: int = 0,
    sampled_mall_ids: list[str] | None = None,
) -> list[str]:
    profile = request_profile if isinstance(request_profile, dict) else {}
    problems: list[str] = []
    if not profile:
        return ["request_profile"]
    expected_total = int(expected_requests or 0)
    total_requests = parse_int_value(profile.get("total_requests"), -1)
    unique_request_signatures = parse_int_value(profile.get("unique_request_signatures"), -1)
    repeated_request_count = parse_int_value(profile.get("repeated_request_count"), -1)
    if expected_total > 0 and total_requests != expected_total:
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
    for query_type, planned_count in expected_query_type_counts_from_mode_counts(mode_counts or {}).items():
        if planned_count > 0 and parse_int_value(unique_by_query_type.get(query_type), 0) <= 0:
            problems.append(f"request_profile.unique_by_query_type.{query_type}")
    image_successful = int(image_successful_responses or 0)
    unique_image_inputs = parse_int_value(profile.get("unique_image_inputs"), 0)
    if image_successful > 0 and unique_image_inputs <= 0:
        problems.append("request_profile.unique_image_inputs")
    if image_successful > 0 and min_unique_image_inputs > 0 and unique_image_inputs < int(min_unique_image_inputs):
        problems.append("request_profile.unique_image_inputs")
    if (
        external_embedding_backend(embedding_backend)
        and image_successful > 0
        and parse_int_value(profile.get("min_backend_qwen_request_attempts"), 0) <= 0
    ):
        problems.append("request_profile.min_backend_qwen_request_attempts")
    if scenario == "mixed-traffic" or min_distinct_mall_count > 0:
        distinct_mall_count = parse_int_value(profile.get("distinct_mall_count"), 0)
        if distinct_mall_count < max(0, int(min_distinct_mall_count)):
            problems.append("request_profile.distinct_mall_count")
        unique_by_mall = (
            profile.get("unique_by_mall_id_count")
            if isinstance(profile.get("unique_by_mall_id_count"), dict)
            else {}
        )
        requested_mall_ids = [mall_id for mall_id in (sampled_mall_ids or []) if str(mall_id or "").strip()]
        if requested_mall_ids and not unique_by_mall:
            problems.append("request_profile.unique_by_mall_id_count")
        for mall_id in requested_mall_ids:
            if unique_by_mall and parse_int_value(unique_by_mall.get(str(mall_id)), 0) <= 0:
                problems.append(f"request_profile.unique_by_mall_id_count.{mall_id}")
    return sorted(set(problems))


def check_load_server_metrics(data: dict[str, Any], mode: str) -> tuple[bool, list[str]]:
    server_metrics = data.get("server_metrics") or {}
    initial_missing = []
    if server_metrics.get("requested") is not True:
        initial_missing.append("server_metrics.requested")
    if server_metrics.get("ok") is not True:
        initial_missing.append("server_metrics.ok")
    api_server_count = parse_int_value(data.get("api_server_count"), 0)
    source_coverage = (
        server_metrics.get("admin_metrics_source_coverage")
        if isinstance(server_metrics.get("admin_metrics_source_coverage"), dict)
        else {}
    )
    if api_server_count >= 2:
        if not source_coverage:
            initial_missing.append("server_metrics.admin_metrics_source_coverage")
        else:
            if source_coverage.get("ok") is not True:
                initial_missing.extend(
                    f"server_metrics.{problem}"
                    for problem in (source_coverage.get("problems") or [])
                    if str(problem or "").strip()
                )
            if parse_int_value(source_coverage.get("successful_source_count"), 0) < api_server_count:
                initial_missing.append("server_metrics.admin_metrics.source_count_below_api_server_count")
            if parse_int_value(source_coverage.get("distinct_instance_count"), 0) < api_server_count:
                initial_missing.append("server_metrics.admin_metrics.distinct_instance_count_below_api_server_count")
    before = (server_metrics.get("before") or {}).get("snapshot") or {}
    after = (server_metrics.get("after") or {}).get("snapshot") or {}
    delta = server_metrics.get("delta") or {}
    run_log_coverage = server_metrics.get("run_log_coverage") or {}
    response_contract = data.get("response_contract") or {}
    query_type_counts = response_contract.get("query_type_counts") or {}
    successful = int(response_contract.get("valid_successful_responses") or response_contract.get("successful_responses") or 0)
    image_successful = int(query_type_counts.get("image", 0) or 0) + int(query_type_counts.get("text_image", 0) or 0)
    required = [
        "engine_ok",
        "engine_backend",
        "engine_index",
        "marqo_model",
        "embedding_backend",
        "rate_limit_backend",
        "rate_limit_redis_enabled",
        "rate_limit_fallback_events",
        "cache_backend",
        "cache_redis_enabled",
        "cache_ttl_seconds",
        "cache_error_count",
        "cache_clear_errors",
        "cache_lock_claims",
        "cache_lock_contention_events",
        "cache_lock_errors",
        "cache_lock_release_errors",
        "cache_lock_wait_events",
        "cache_lock_wait_timeouts",
        "cache_lock_total_wait_ms",
        "cache_lock_avg_wait_ms",
        "cache_lock_max_wait_ms",
        "singleflight_enabled",
        "singleflight_in_flight",
        "singleflight_wait_timeout_seconds",
        "singleflight_wait_events",
        "singleflight_wait_timeouts",
        "singleflight_total_wait_ms",
        "singleflight_avg_wait_ms",
        "singleflight_max_wait_ms",
        "image_validation_enabled",
        "image_validation_in_flight",
        "image_validation_wait_timeout_seconds",
        "image_validation_wait_events",
        "image_validation_wait_timeouts",
        "image_validation_total_wait_ms",
        "image_validation_avg_wait_ms",
        "image_validation_max_wait_ms",
        "result_mall_id_mismatch_events",
        "result_mall_id_mismatch_count",
        "search_log_write_errors",
        "error_log_write_errors",
        *SEARCH_ENGINE_SERVER_METRIC_FIELDS,
        *BACKEND_MARQO_SERVER_METRIC_FIELDS,
        "search_queue_enabled",
        "search_queue_max_concurrency",
        "search_queue_timeout_seconds",
        "search_queue_in_flight",
        "search_queue_full_events",
        "search_queue_wait_events",
        "search_queue_total_wait_ms",
        "search_queue_avg_wait_ms",
        "search_queue_max_wait_ms",
        "process_memory_rss_bytes",
        "system_memory_used_percent",
        "disk_used_percent",
    ]
    missing = [*initial_missing, *[key for key in required if after.get(key) in (None, "")]]
    if after.get("engine_ok") is not True and "engine_ok" not in missing:
        missing.append("engine_ok")
    if str(after.get("engine_backend") or "").strip().lower() != "marqo" and "engine_backend" not in missing:
        missing.append("engine_backend")
    if not str(after.get("engine_index") or "").strip() and "engine_index" not in missing:
        missing.append("engine_index")
    if not str(after.get("marqo_model") or "").strip() and "marqo_model" not in missing:
        missing.append("marqo_model")
    embedding_backend = str(after.get("embedding_backend") or "").strip().lower()
    if embedding_backend not in {"native", "qwen", "gemini"} and "embedding_backend" not in missing:
        missing.append("embedding_backend")
    mall_identity = data.get("mall_identity") if isinstance(data.get("mall_identity"), dict) else {}
    sampled_mall_ids = [
        str(mall_id)
        for mall_id in (mall_identity.get("sampled_mall_ids") or [])
        if str(mall_id or "").strip()
    ]
    for problem in request_profile_problems(
        data.get("request_profile") if isinstance(data.get("request_profile"), dict) else {},
        embedding_backend=embedding_backend,
        image_successful_responses=image_successful,
        expected_requests=parse_int_value(data.get("requests"), 0),
        mode_counts=data.get("mode_counts") if isinstance(data.get("mode_counts"), dict) else {},
        scenario=str(data.get("scenario") or ""),
        min_unique_image_inputs=MIN_LOAD_IMAGE_INPUTS if image_successful > 0 else 0,
        min_distinct_mall_count=(
            MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE if data.get("scenario") == "mixed-traffic" else 0
        ),
        sampled_mall_ids=sampled_mall_ids,
    ):
        if problem not in missing:
            missing.append(problem)
    if external_embedding_backend(embedding_backend):
        if not str(after.get("qwen_model") or "").strip():
            missing.append("qwen_model")
        if not is_positive_number(after.get("qwen_embedding_dimensions")):
            missing.append("qwen_embedding_dimensions")
        missing.extend(key for key in BACKEND_QWEN_SERVER_METRIC_FIELDS if after.get(key) in (None, ""))
        missing.extend(qwen_query_vector_server_metric_problems(after))
    for key in BACKEND_MARQO_SERVER_METRIC_FIELDS:
        if not is_number(after.get(key)) and key not in missing:
            missing.append(key)
    for key in ["result_mall_id_mismatch_events", "result_mall_id_mismatch_count"]:
        if not is_number(after.get(key)) and key not in missing:
            missing.append(key)
    for key in ["search_log_write_errors", "error_log_write_errors"]:
        if not is_number(after.get(key)) and key not in missing:
            missing.append(key)
    for key in SEARCH_ENGINE_SERVER_METRIC_FIELDS:
        if not is_number(after.get(key)) and key not in missing:
            missing.append(key)
    if external_embedding_backend(embedding_backend):
        for key in BACKEND_QWEN_SERVER_METRIC_FIELDS:
            if not is_number(after.get(key)) and key not in missing:
                missing.append(key)
    if str(after.get("rate_limit_backend") or "").strip().lower() != "redis" and "rate_limit_backend" not in missing:
        missing.append("rate_limit_backend")
    if after.get("rate_limit_redis_enabled") is not True and "rate_limit_redis_enabled" not in missing:
        missing.append("rate_limit_redis_enabled")
    if str(after.get("cache_backend") or "").strip().lower() != "redis" and "cache_backend" not in missing:
        missing.append("cache_backend")
    if after.get("cache_redis_enabled") is not True and "cache_redis_enabled" not in missing:
        missing.append("cache_redis_enabled")
    if (
        not is_number(after.get("cache_ttl_seconds"))
        or float(after.get("cache_ttl_seconds") or 0) <= 0
    ) and "cache_ttl_seconds" not in missing:
        missing.append("cache_ttl_seconds")
    if after.get("singleflight_enabled") is not True and "singleflight_enabled" not in missing:
        missing.append("singleflight_enabled")
    if after.get("image_validation_enabled") is not True and "image_validation_enabled" not in missing:
        missing.append("image_validation_enabled")
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
        "image_validation_in_flight",
        "image_validation_wait_events",
        "image_validation_wait_timeouts",
        "image_validation_total_wait_ms",
        "image_validation_avg_wait_ms",
        "image_validation_max_wait_ms",
    ]:
        if not is_number(after.get(key)) and key not in missing:
            missing.append(key)
    if not is_number(after.get("singleflight_wait_timeout_seconds")) or float(after.get("singleflight_wait_timeout_seconds") or 0) <= 0:
        if "singleflight_wait_timeout_seconds" not in missing:
            missing.append("singleflight_wait_timeout_seconds")
    if (
        not is_number(after.get("image_validation_wait_timeout_seconds"))
        or float(after.get("image_validation_wait_timeout_seconds") or 0) <= 0
    ):
        if "image_validation_wait_timeout_seconds" not in missing:
            missing.append("image_validation_wait_timeout_seconds")
    if after.get("search_queue_enabled") is not True and "search_queue_enabled" not in missing:
        missing.append("search_queue_enabled")
    if not is_number(after.get("search_queue_max_concurrency")) or int(after.get("search_queue_max_concurrency") or 0) <= 0:
        if "search_queue_max_concurrency" not in missing:
            missing.append("search_queue_max_concurrency")
    if not is_number(after.get("search_queue_timeout_seconds")):
        if "search_queue_timeout_seconds" not in missing:
            missing.append("search_queue_timeout_seconds")
    for key in [
        "search_queue_wait_events",
        "search_queue_total_wait_ms",
        "search_queue_avg_wait_ms",
        "search_queue_max_wait_ms",
    ]:
        if not is_number(after.get(key)) and key not in missing:
            missing.append(key)
    for problem in api_threadpool_server_metric_problems(after):
        if problem not in missing:
            missing.append(problem)
    for problem in server_settled_state_problems(
        after,
        include_image_queue=mode in {"image", "mixed", "mixed-traffic"},
    ):
        if problem not in missing:
            missing.append(problem)
    for problem in process_rss_growth_problems(
        before,
        after,
        data.get("thresholds") or {},
    ):
        if problem not in missing:
            missing.append(problem)
    search_delta = delta.get("search_events")
    if metric_coverage_meets(delta, run_log_coverage, "search_events", successful):
        pass
    elif not is_number(search_delta) or int(search_delta) <= 0:
        missing.append("server_metrics.delta.search_events")
    elif successful > 0 and int(search_delta) < successful:
        missing.append("server_metrics.delta.search_events_below_successful_responses")
    if mode in {"image", "mixed", "mixed-traffic"}:
        image_queue_required = [
            "image_queue_enabled",
            "image_queue_max_concurrency",
            "image_queue_timeout_seconds",
            "image_queue_in_flight",
            "image_queue_full_events",
            "image_queue_wait_events",
            "image_queue_total_wait_ms",
            "image_queue_avg_wait_ms",
            "image_queue_max_wait_ms",
        ]
        missing.extend(key for key in image_queue_required if after.get(key) in (None, ""))
        if after.get("image_queue_enabled") is not True and "image_queue_enabled" not in missing:
            missing.append("image_queue_enabled")
        if not is_number(after.get("image_queue_max_concurrency")) or int(after.get("image_queue_max_concurrency") or 0) <= 0:
            if "image_queue_max_concurrency" not in missing:
                missing.append("image_queue_max_concurrency")
        if not is_number(after.get("image_queue_timeout_seconds")):
            if "image_queue_timeout_seconds" not in missing:
                missing.append("image_queue_timeout_seconds")
        for key in [
            "image_queue_wait_events",
            "image_queue_total_wait_ms",
            "image_queue_avg_wait_ms",
            "image_queue_max_wait_ms",
        ]:
            if not is_number(after.get(key)) and key not in missing:
                missing.append(key)
        image_delta = delta.get("image_search_events")
        if metric_coverage_meets(delta, run_log_coverage, "image_search_events", image_successful):
            pass
        elif not is_number(image_delta) or int(image_delta) <= 0:
            missing.append("server_metrics.delta.image_search_events")
        elif image_successful > 0 and int(image_delta) < image_successful:
            missing.append("server_metrics.delta.image_search_events_below_successful_responses")
        if not is_number(delta.get("image_queue_full_events")):
            missing.append("server_metrics.delta.image_queue_full_events")
        if not is_number(delta.get("image_queue_wait_events")):
            missing.append("server_metrics.delta.image_queue_wait_events")
        if not is_number(delta.get("image_queue_total_wait_ms")):
            missing.append("server_metrics.delta.image_queue_total_wait_ms")
    if not is_number(delta.get("rate_limited_events")):
        missing.append("server_metrics.delta.rate_limited_events")
    if not is_number(delta.get("rate_limit_fallback_events")):
        missing.append("server_metrics.delta.rate_limit_fallback_events")
    if not is_number(delta.get("cache_error_count")):
        missing.append("server_metrics.delta.cache_error_count")
    if not is_number(delta.get("cache_clear_errors")):
        missing.append("server_metrics.delta.cache_clear_errors")
    for key in SEARCH_ENGINE_REQUIRED_DELTA_SERVER_METRIC_FIELDS:
        if not is_number(delta.get(key)):
            missing.append(f"server_metrics.delta.{key}")
    for key in [
        "cache_lock_claims",
        "cache_lock_contention_events",
        "cache_lock_errors",
        "cache_lock_release_errors",
        "cache_lock_wait_events",
        "cache_lock_wait_timeouts",
        "cache_lock_total_wait_ms",
        "singleflight_wait_events",
        "singleflight_wait_timeouts",
        "singleflight_total_wait_ms",
        "image_validation_wait_events",
        "image_validation_wait_timeouts",
        "image_validation_total_wait_ms",
    ]:
        if not is_number(delta.get(key)):
            missing.append(f"server_metrics.delta.{key}")
    for field in API_SCALE_REQUIRED_SERVER_METRIC_DELTAS:
        if field.startswith("server_metrics.delta.backend_marqo_"):
            key = field.rsplit(".", 1)[-1]
            if not is_number(delta.get(key)):
                missing.append(field)
    if external_embedding_backend(embedding_backend):
        for field in BACKEND_QWEN_SERVER_METRIC_DELTAS:
            key = field.rsplit(".", 1)[-1]
            if not is_number(delta.get(key)):
                missing.append(field)
        for key in QWEN_QUERY_VECTOR_DELTA_SERVER_METRIC_FIELDS:
            if not is_number(delta.get(key)):
                missing.append(f"server_metrics.delta.{key}")
        for key in BACKEND_QWEN_ZERO_DELTA_SERVER_METRIC_FIELDS:
            value = delta.get(key)
            missing_name = f"server_metrics.delta.{key}"
            if is_number(value) and float(value) > 0:
                missing.append(f"{missing_name}_nonzero")
        for key in QWEN_QUERY_VECTOR_ZERO_DELTA_SERVER_METRIC_FIELDS:
            value = delta.get(key)
            missing_name = f"server_metrics.delta.{key}"
            if is_number(value) and float(value) > 0:
                missing.append(f"{missing_name}_nonzero")
    for problem in backend_run_latency_problems(
        delta,
        data.get("thresholds") or {},
        embedding_backend=embedding_backend,
    ):
        if problem not in missing:
            missing.append(problem)
    for problem in server_wait_avg_latency_problems(
        delta,
        data.get("thresholds") or {},
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
        request_profile=data.get("request_profile") if isinstance(data.get("request_profile"), dict) else {},
    ):
        if problem not in missing:
            missing.append(problem)
    if not is_number(delta.get("search_queue_full_events")):
        missing.append("server_metrics.delta.search_queue_full_events")
    if not is_number(delta.get("search_queue_wait_events")):
        missing.append("server_metrics.delta.search_queue_wait_events")
    if not is_number(delta.get("search_queue_total_wait_ms")):
        missing.append("server_metrics.delta.search_queue_total_wait_ms")
    for key in ZERO_DELTA_SERVER_METRIC_FIELDS:
        value = delta.get(key)
        missing_name = f"server_metrics.delta.{key}"
        if not is_number(value) and missing_name not in missing:
            missing.append(missing_name)
        elif is_number(value) and float(value) > 0:
            missing.append(f"{missing_name}_nonzero")
    for key in BACKEND_CIRCUIT_ZERO_DELTA_SERVER_METRIC_FIELDS:
        value = delta.get(key)
        if is_number(value) and float(value) > 0:
            missing.append(f"server_metrics.delta.{key}_nonzero")
    for key in OPTIONAL_ZERO_DELTA_SERVER_METRIC_FIELDS:
        value = delta.get(key)
        if is_number(value) and float(value) > 0:
            missing.append(f"server_metrics.delta.{key}_nonzero")
    return not missing, missing


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
    prefixes = ["cache_lock", "singleflight", "image_validation", "search_queue", "image_queue"]
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


def check_api_scale(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    single = data.get("single") or {}
    multi = data.get("multi") or {}
    comparison = data.get("comparison") or {}
    problems = list(data.get("problems") or [])
    if data.get("ok") is not True and "ok" not in problems:
        problems.append("ok")
    if int(single.get("api_server_count", 0) or 0) != 1 and "single.api_server_count" not in problems:
        problems.append("single.api_server_count")
    if int(multi.get("api_server_count", 0) or 0) < 2 and "multi.api_server_count" not in problems:
        problems.append("multi.api_server_count")
    if comparison.get("comparable") is not True and "workload_comparable" not in problems:
        problems.append("workload_comparable")
    for key in ["base_url", "mall_id", "origin"]:
        problem = API_SCALE_TARGET_PROBLEMS[key]
        if single.get(key) != multi.get(key) and problem not in problems:
            problems.append(problem)
    runtime_identity = compare_api_scale_runtime_identity(single, multi)
    for problem in runtime_identity["problems"]:
        if problem not in problems:
            problems.append(problem)
    query_type_coverage: dict[str, tuple[bool, list[str], dict[str, Any]]] = {}
    query_type_latency: dict[str, tuple[bool, list[str], dict[str, Any]]] = {}
    response_shapes: dict[str, tuple[bool, list[str], dict[str, Any]]] = {}
    response_mall_identities: dict[str, tuple[bool, list[str], dict[str, Any]]] = {}
    mall_identities: dict[str, tuple[bool, list[str], dict[str, Any]]] = {}
    api_instance_coverages: dict[str, tuple[bool, list[str], dict[str, Any]]] = {}
    for prefix, summary in [("single", single), ("multi", multi)]:
        response_engine_ok, _response_engine_details = check_api_scale_response_engine(summary)
        response_shapes[prefix] = check_api_scale_response_shape(summary)
        response_shape_ok, _response_shape_problems, _response_shape_details = response_shapes[prefix]
        response_mall_identities[prefix] = check_api_scale_response_mall_identity(summary)
        response_mall_ok, _response_mall_problems, _response_mall_details = response_mall_identities[prefix]
        mall_identities[prefix] = check_load_mall_identity_coverage(summary, "mixed-traffic")
        mall_identity_ok, _mall_identity_problems, _mall_identity_details = mall_identities[prefix]
        query_type_coverage[prefix] = check_api_scale_query_type_coverage(summary)
        query_type_ok, _query_type_problems, _query_type_details = query_type_coverage[prefix]
        query_type_latency[prefix] = check_api_scale_query_type_latency(summary)
        query_type_latency_ok, _query_type_latency_problems, _query_type_latency_details = query_type_latency[prefix]
        if not str(summary.get("mall_id") or "").strip() and f"{prefix}.mall_id" not in problems:
            problems.append(f"{prefix}.mall_id")
        for url_problem in (
            operational_https_url_problems("base_url", summary.get("base_url"))
            + operational_https_url_problems("origin", summary.get("origin"), origin_only=True)
        ):
            problem_name = f"{prefix}.{url_problem}"
            if problem_name not in problems:
                problems.append(problem_name)
        if summary.get("scenario") != "mixed-traffic" and f"{prefix}.scenario" not in problems:
            problems.append(f"{prefix}.scenario")
        if int(summary.get("active_users", 0) or 0) < 850 and f"{prefix}.active_users" not in problems:
            problems.append(f"{prefix}.active_users")
        if int(summary.get("requests", 0) or 0) < 850 and f"{prefix}.requests" not in problems:
            problems.append(f"{prefix}.requests")
        if int(summary.get("concurrency", 0) or 0) < 100 and f"{prefix}.concurrency" not in problems:
            problems.append(f"{prefix}.concurrency")
        if summary.get("server_metrics_ok") is not True and f"{prefix}.server_metrics" not in problems:
            problems.append(f"{prefix}.server_metrics")
        for missing in check_api_scale_server_metrics(summary):
            problem_name = f"{prefix}.{missing}"
            if problem_name not in problems:
                problems.append(problem_name)
        client_transport_ok, client_transport_problems = check_load_client_transport(summary)
        if not client_transport_ok:
            for client_problem in client_transport_problems:
                problem_name = f"{prefix}.{client_problem}"
                if problem_name not in problems:
                    problems.append(problem_name)
        api_instance_coverages[prefix] = check_load_api_instance_coverage(summary)
        api_instance_ok, api_instance_problems, _api_instance_details = api_instance_coverages[prefix]
        if not api_instance_ok:
            for api_instance_problem in api_instance_problems:
                problem_name = f"{prefix}.{api_instance_problem}"
                if problem_name not in problems:
                    problems.append(problem_name)
        mode_counts = summary.get("mode_counts") or {}
        if any(int(mode_counts.get(mode, 0) or 0) <= 0 for mode in ["text", "image", "mixed"]):
            if f"{prefix}.mode_counts" not in problems:
                problems.append(f"{prefix}.mode_counts")
        if summary.get("image_source_ok") is not True and f"{prefix}.image_input" not in problems:
            problems.append(f"{prefix}.image_input")
        if not mall_identity_ok and f"{prefix}.mall_identity" not in problems:
            problems.append(f"{prefix}.mall_identity")
        if summary.get("response_contract_ok") is not True and f"{prefix}.response_contract" not in problems:
            problems.append(f"{prefix}.response_contract")
        if not response_engine_ok and f"{prefix}.response_engine" not in problems:
            problems.append(f"{prefix}.response_engine")
        if not response_shape_ok and f"{prefix}.response_shape" not in problems:
            problems.append(f"{prefix}.response_shape")
        if not response_mall_ok and f"{prefix}.response_mall_identity" not in problems:
            problems.append(f"{prefix}.response_mall_identity")
        if not query_type_ok and f"{prefix}.query_type_coverage" not in problems:
            problems.append(f"{prefix}.query_type_coverage")
        if not query_type_latency_ok and f"{prefix}.query_type_latency" not in problems:
            problems.append(f"{prefix}.query_type_latency")
    max_p95_regression = comparison.get("max_multi_p95_regression_percent")
    p95_change = comparison.get("p95_change_percent")
    if not is_number(p95_change) or not is_number(max_p95_regression):
        if "comparison.p95_change_percent" not in problems:
            problems.append("comparison.p95_change_percent")
    elif float(p95_change) > float(max_p95_regression):
        if "comparison.multi_p95_regression" not in problems:
            problems.append("comparison.multi_p95_regression")
    max_p99_regression = comparison.get("max_multi_p99_regression_percent")
    p99_change = comparison.get("p99_change_percent")
    if is_number(max_p99_regression) or is_number(p99_change):
        if not is_number(p99_change) or not is_number(max_p99_regression):
            if "comparison.p99_change_percent" not in problems:
                problems.append("comparison.p99_change_percent")
        elif float(p99_change) > float(max_p99_regression):
            if "comparison.multi_p99_regression" not in problems:
                problems.append("comparison.multi_p99_regression")
    min_rps_ratio = comparison.get("min_multi_rps_ratio")
    rps_ratio = comparison.get("rps_ratio")
    if not is_number(rps_ratio) or not is_number(min_rps_ratio):
        if "comparison.rps_ratio" not in problems:
            problems.append("comparison.rps_ratio")
    elif float(rps_ratio) < float(min_rps_ratio):
        if "comparison.multi_rps_ratio" not in problems:
            problems.append("comparison.multi_rps_ratio")
    ok = not problems
    return (
        ok,
        "API 1-server/2-server scale comparison passed" if ok else "API scale comparison evidence is incomplete or failed",
        {
            "single_api_server_count": single.get("api_server_count"),
            "multi_api_server_count": multi.get("api_server_count"),
            "base_url": multi.get("base_url"),
            "mall_id": multi.get("mall_id"),
            "origin": multi.get("origin"),
            "scenario": multi.get("scenario"),
            "active_users": multi.get("active_users"),
            "requests": multi.get("requests"),
            "concurrency": multi.get("concurrency"),
            "single_p95_ms": single.get("p95_ms"),
            "multi_p95_ms": multi.get("p95_ms"),
            "single_p99_ms": single.get("p99_ms"),
            "multi_p99_ms": multi.get("p99_ms"),
            "single_image_source_ok": single.get("image_source_ok"),
            "multi_image_source_ok": multi.get("image_source_ok"),
            "single_image_source_problems": single.get("image_source_problems") or [],
            "multi_image_source_problems": multi.get("image_source_problems") or [],
            "single_mall_identity_ok": mall_identities.get("single", (False, [], {}))[0],
            "multi_mall_identity_ok": mall_identities.get("multi", (False, [], {}))[0],
            "single_mall_identity_problems": mall_identities.get("single", (False, [], {}))[1],
            "multi_mall_identity_problems": mall_identities.get("multi", (False, [], {}))[1],
            "single_mall_identity": mall_identities.get("single", (False, [], {}))[2],
            "multi_mall_identity": mall_identities.get("multi", (False, [], {}))[2],
            "single_response_engine": check_api_scale_response_engine(single)[1],
            "multi_response_engine": check_api_scale_response_engine(multi)[1],
            "single_response_shape_ok": response_shapes.get("single", (False, [], {}))[0],
            "multi_response_shape_ok": response_shapes.get("multi", (False, [], {}))[0],
            "single_response_shape_problems": response_shapes.get("single", (False, [], {}))[1],
            "multi_response_shape_problems": response_shapes.get("multi", (False, [], {}))[1],
            "single_response_shape": response_shapes.get("single", (False, [], {}))[2],
            "multi_response_shape": response_shapes.get("multi", (False, [], {}))[2],
            "single_response_mall_identity_ok": response_mall_identities.get("single", (False, [], {}))[0],
            "multi_response_mall_identity_ok": response_mall_identities.get("multi", (False, [], {}))[0],
            "single_response_mall_identity_problems": response_mall_identities.get("single", (False, [], {}))[1],
            "multi_response_mall_identity_problems": response_mall_identities.get("multi", (False, [], {}))[1],
            "single_response_mall_identity": response_mall_identities.get("single", (False, [], {}))[2],
            "multi_response_mall_identity": response_mall_identities.get("multi", (False, [], {}))[2],
            "single_query_type_coverage_ok": query_type_coverage.get("single", (False, [], {}))[0],
            "multi_query_type_coverage_ok": query_type_coverage.get("multi", (False, [], {}))[0],
            "single_query_type_coverage_problems": query_type_coverage.get("single", (False, [], {}))[1],
            "multi_query_type_coverage_problems": query_type_coverage.get("multi", (False, [], {}))[1],
            "single_query_type_coverage": query_type_coverage.get("single", (False, [], {}))[2],
            "multi_query_type_coverage": query_type_coverage.get("multi", (False, [], {}))[2],
            "single_query_type_latency_ok": query_type_latency.get("single", (False, [], {}))[0],
            "multi_query_type_latency_ok": query_type_latency.get("multi", (False, [], {}))[0],
            "single_query_type_latency_problems": query_type_latency.get("single", (False, [], {}))[1],
            "multi_query_type_latency_problems": query_type_latency.get("multi", (False, [], {}))[1],
            "single_query_type_latency": query_type_latency.get("single", (False, [], {}))[2],
            "multi_query_type_latency": query_type_latency.get("multi", (False, [], {}))[2],
            "single_server_metrics_missing": check_api_scale_server_metrics(single),
            "multi_server_metrics_missing": check_api_scale_server_metrics(multi),
            "single_client_transport_problems": check_load_client_transport(single)[1],
            "multi_client_transport_problems": check_load_client_transport(multi)[1],
            "single_api_instance_coverage_ok": api_instance_coverages.get("single", (False, [], {}))[0],
            "multi_api_instance_coverage_ok": api_instance_coverages.get("multi", (False, [], {}))[0],
            "single_api_instance_coverage_problems": api_instance_coverages.get("single", (False, [], {}))[1],
            "multi_api_instance_coverage_problems": api_instance_coverages.get("multi", (False, [], {}))[1],
            "single_api_instance_coverage": api_instance_coverages.get("single", (False, [], {}))[2],
            "multi_api_instance_coverage": api_instance_coverages.get("multi", (False, [], {}))[2],
            "single_engine_backend": single.get("engine_backend"),
            "multi_engine_backend": multi.get("engine_backend"),
            "runtime_identity_ok": runtime_identity["ok"],
            "runtime_identity": runtime_identity,
            "p95_change_percent": comparison.get("p95_change_percent"),
            "p99_change_percent": comparison.get("p99_change_percent"),
            "rps_ratio": comparison.get("rps_ratio"),
            "problems": problems,
        },
    )


def compare_api_scale_runtime_identity(single: dict[str, Any], multi: dict[str, Any]) -> dict[str, Any]:
    fields = api_scale_runtime_identity_fields(single, multi)
    single_identity = api_scale_runtime_identity(single, fields)
    multi_identity = api_scale_runtime_identity(multi, fields)
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


def api_scale_runtime_identity_fields(single: dict[str, Any], multi: dict[str, Any]) -> list[str]:
    fields = list(API_SCALE_RUNTIME_IDENTITY_FIELDS)
    embedding_backends = {
        str(summary.get("embedding_backend") or "").strip().lower()
        for summary in [single, multi]
    }
    qwen_configured = bool({"qwen", "gemini"} & embedding_backends) or any(
        str(summary.get(field) or "").strip()
        for summary in [single, multi]
        for field in API_SCALE_QWEN_RUNTIME_IDENTITY_FIELDS
    )
    if qwen_configured:
        fields.extend(API_SCALE_QWEN_RUNTIME_IDENTITY_FIELDS)
    return fields


def api_scale_runtime_identity(summary: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: normalize_api_scale_runtime_identity_value(field, summary.get(field)) for field in fields}


def normalize_api_scale_runtime_identity_value(field: str, value: Any) -> Any:
    if value in (None, ""):
        return None
    if field in {"engine_backend", "embedding_backend", "rate_limit_backend", "cache_backend"}:
        return str(value).strip().lower()
    if field in {
        "cache_ttl_seconds",
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


def check_api_scale_query_type_coverage(summary: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    coverage = summary.get("query_type_coverage") if isinstance(summary.get("query_type_coverage"), dict) else {}
    response_contract = {
        "query_type_counts": summary.get("response_contract_query_type_counts") or {},
        "expected_query_type_counts": summary.get("response_contract_expected_query_type_counts")
        or coverage.get("expected_query_type_counts")
        or {},
        "unexpected_query_type_count": summary.get("response_contract_unexpected_query_type_count", 0),
    }
    return check_load_query_type_coverage(
        response_contract,
        summary.get("mode_counts") or {},
        summary.get("thresholds") or {},
    )


def check_api_scale_query_type_latency(summary: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    latency = summary.get("query_type_latency") if isinstance(summary.get("query_type_latency"), dict) else {}
    return check_load_query_type_latency(
        {
            "mode_counts": summary.get("mode_counts") or {},
            "thresholds": summary.get("thresholds") or {},
            "expected_query_type_latency_ms": summary.get("expected_query_type_latency_ms")
            or latency.get("expected_query_type_latency_ms")
            or {},
            "response_query_type_latency_ms": summary.get("response_query_type_latency_ms")
            or latency.get("response_query_type_latency_ms")
            or {},
        }
    )


def check_api_scale_response_shape(summary: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    raw_details = summary.get("response_shape") if isinstance(summary.get("response_shape"), dict) else {}
    response_contract = {
        "successful_responses": raw_details.get("successful_responses", summary.get("successful_responses")),
        "valid_successful_responses": raw_details.get(
            "valid_successful_responses",
            summary.get("successful_responses"),
        ),
        "invalid_successful_responses": raw_details.get("invalid_successful_responses", 0),
        "unexpected_query_type_count": summary.get(
            "response_contract_unexpected_query_type_count",
            raw_details.get("unexpected_query_type_count", 0),
        ),
        "expected_product_url_prefix_counts": raw_details.get(
            "expected_product_url_prefix_counts",
            summary.get("expected_product_url_prefix_counts"),
        ),
        "product_url_prefix_required": raw_details.get(
            "product_url_prefix_required",
            summary.get("product_url_prefix_required"),
        ),
        "product_url_prefix_mismatch_count": raw_details.get(
            "product_url_prefix_mismatch_count",
            summary.get("product_url_prefix_mismatch_count", 0),
        ),
        "min_top_count": raw_details.get("min_top_count"),
        "min_item_count": raw_details.get("min_item_count"),
        "min_category_count": raw_details.get("min_category_count"),
    }
    ok, problems, details = check_load_response_contract_shape(response_contract)
    if details.get("product_url_prefix_required") is not True:
        problems.append("response_contract.product_url_prefix_required")
    return ok and not problems, sorted(set(problems)), details


def check_api_scale_response_mall_identity(summary: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    raw_details = summary.get("response_mall_identity") if isinstance(summary.get("response_mall_identity"), dict) else {}
    response_contract = {
        "successful_responses": raw_details.get("successful_responses", summary.get("successful_responses")),
        "expected_mall_id_counts": raw_details.get("expected_mall_id_counts"),
        "meta_mall_id_counts": raw_details.get("meta_mall_id_counts"),
        "result_mall_id_counts": raw_details.get("result_mall_id_counts"),
        "mall_id_mismatch_count": raw_details.get("mall_id_mismatch_count"),
    }
    return check_load_response_mall_identity(response_contract, summary.get("mall_id"))


def check_api_scale_response_engine(summary: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    raw_details = summary.get("response_engine") if isinstance(summary.get("response_engine"), dict) else {}
    engine_counts = raw_details.get("engine_counts") if isinstance(raw_details.get("engine_counts"), dict) else {}
    successful = parse_int_value(
        raw_details.get("valid_successful_responses"),
        parse_int_value(summary.get("successful_responses"), 0),
    )
    marqo_responses = parse_int_value(raw_details.get("marqo_responses"), 0)
    if marqo_responses == 0 and engine_counts:
        marqo_responses = sum(
            parse_int_value(count, 0)
            for engine, count in engine_counts.items()
            if str(engine or "").strip().lower() == "marqo"
        )
    if "non_marqo_engine_responses" in raw_details:
        non_marqo = parse_int_value(raw_details.get("non_marqo_engine_responses"), successful)
    else:
        non_marqo = sum(
            parse_int_value(count, 0)
            for engine, count in engine_counts.items()
            if str(engine or "").strip().lower() != "marqo"
        )
        if successful > 0 and not engine_counts:
            non_marqo = successful
    ok = (
        summary.get("response_engine_ok") is True
        and bool(engine_counts)
        and successful > 0
        and marqo_responses >= successful
        and non_marqo == 0
    )
    return ok, {
        "ok": ok,
        "engine_counts": engine_counts,
        "valid_successful_responses": successful,
        "marqo_responses": marqo_responses,
        "non_marqo_engine_responses": non_marqo,
    }


def check_api_scale_server_metrics(summary: dict[str, Any]) -> list[str]:
    missing = list(summary.get("server_metrics_missing") or [])
    delta = summary.get("server_metrics_delta") or {}
    run_log_coverage = summary.get("server_metrics_run_log_coverage") or {}
    successful = int(summary.get("successful_responses", 0) or 0)
    image_successful = int(summary.get("image_successful_responses", 0) or 0)
    api_server_count = parse_int_value(summary.get("api_server_count"), 0)
    source_coverage = (
        summary.get("admin_metrics_source_coverage")
        if isinstance(summary.get("admin_metrics_source_coverage"), dict)
        else {}
    )
    if api_server_count >= 2:
        if not source_coverage:
            missing.append("admin_metrics_source_coverage")
        else:
            if source_coverage.get("ok") is not True:
                missing.extend(
                    str(problem)
                    for problem in (source_coverage.get("problems") or [])
                    if str(problem or "").strip()
                )
            if parse_int_value(source_coverage.get("successful_source_count"), 0) < api_server_count:
                missing.append("admin_metrics.source_count_below_api_server_count")
            if parse_int_value(source_coverage.get("distinct_instance_count"), 0) < api_server_count:
                missing.append("admin_metrics.distinct_instance_count_below_api_server_count")
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
    for problem in request_profile_problems(
        summary.get("request_profile") if isinstance(summary.get("request_profile"), dict) else {},
        embedding_backend=embedding_backend,
        image_successful_responses=image_successful,
        expected_requests=parse_int_value(summary.get("requests"), 0),
        mode_counts=summary.get("mode_counts") if isinstance(summary.get("mode_counts"), dict) else {},
        scenario=str(summary.get("scenario") or ""),
        min_unique_image_inputs=MIN_LOAD_IMAGE_INPUTS if image_successful > 0 else 0,
        min_distinct_mall_count=(
            MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE if summary.get("scenario") == "mixed-traffic" else 0
        ),
        sampled_mall_ids=[
            str(mall_id)
            for mall_id in (
                (summary.get("mall_identity") if isinstance(summary.get("mall_identity"), dict) else {}).get(
                    "sampled_mall_ids"
                )
                or []
            )
            if str(mall_id or "").strip()
        ],
    ):
        if problem not in missing:
            missing.append(problem)
    if external_embedding_backend(embedding_backend):
        if not str(summary.get("qwen_model") or "").strip():
            missing.append("qwen_model")
        if not is_positive_number(summary.get("qwen_embedding_dimensions")):
            missing.append("qwen_embedding_dimensions")
        for field in BACKEND_QWEN_SERVER_METRIC_FIELDS:
            if not is_number(summary.get(field)):
                missing.append(field)
        missing.extend(qwen_query_vector_server_metric_problems(summary))
    for field in BACKEND_MARQO_SERVER_METRIC_FIELDS:
        if not is_number(summary.get(field)):
            missing.append(field)
    for field in SEARCH_ENGINE_SERVER_METRIC_FIELDS:
        if not is_number(summary.get(field)):
            missing.append(field)
    for field in ["result_mall_id_mismatch_events", "result_mall_id_mismatch_count"]:
        if not is_number(summary.get(field)):
            missing.append(field)
    for problem in api_threadpool_server_metric_problems(summary):
        if problem not in missing:
            missing.append(problem)
    for field in API_SCALE_REQUIRED_SERVER_METRIC_DELTAS:
        key = field.rsplit(".", 1)[-1]
        value = delta.get(key)
        if field.endswith(".search_events"):
            if metric_coverage_meets(delta, run_log_coverage, key, successful):
                continue
            if not is_number(value) or int(value) <= 0:
                missing.append(field)
            elif successful > 0 and int(value) < successful:
                missing.append("server_metrics.delta.search_events_below_successful_responses")
        elif field.endswith(".image_search_events"):
            if metric_coverage_meets(delta, run_log_coverage, key, image_successful):
                continue
            if not is_number(value) or int(value) <= 0:
                missing.append(field)
            elif image_successful > 0 and int(value) < image_successful:
                missing.append("server_metrics.delta.image_search_events_below_successful_responses")
        elif not is_number(value):
            missing.append(field)
    if external_embedding_backend(embedding_backend):
        for field in BACKEND_QWEN_SERVER_METRIC_DELTAS:
            key = field.rsplit(".", 1)[-1]
            if not is_number(delta.get(key)):
                missing.append(field)
        for key in QWEN_QUERY_VECTOR_DELTA_SERVER_METRIC_FIELDS:
            if not is_number(delta.get(key)):
                missing.append(f"server_metrics.delta.{key}")
        for key in BACKEND_QWEN_ZERO_DELTA_SERVER_METRIC_FIELDS:
            value = delta.get(key)
            missing_name = f"server_metrics.delta.{key}"
            if is_number(value) and float(value) > 0:
                missing.append(f"{missing_name}_nonzero")
        for key in QWEN_QUERY_VECTOR_ZERO_DELTA_SERVER_METRIC_FIELDS:
            value = delta.get(key)
            missing_name = f"server_metrics.delta.{key}"
            if is_number(value) and float(value) > 0:
                missing.append(f"{missing_name}_nonzero")
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
        missing_name = f"server_metrics.delta.{key}"
        if not is_number(value):
            missing.append(missing_name)
        elif float(value) > 0:
            missing.append(f"{missing_name}_nonzero")
    for key in BACKEND_CIRCUIT_ZERO_DELTA_SERVER_METRIC_FIELDS:
        value = delta.get(key)
        if is_number(value) and float(value) > 0:
            missing.append(f"server_metrics.delta.{key}_nonzero")
    for key in OPTIONAL_ZERO_DELTA_SERVER_METRIC_FIELDS:
        value = delta.get(key)
        if is_number(value) and float(value) > 0:
            missing.append(f"server_metrics.delta.{key}_nonzero")
    return sorted(set(missing))


def check_widget(required_sites: int) -> EvidenceCheck:
    def _check(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        sites = data.get("checked_sites", [])
        widget_config = data.get("widget_config") if isinstance(data.get("widget_config"), dict) else {}
        local_only = data.get("local_only") is True or data.get("not_operational_readiness") is True
        required_config_fields = [
            "conflictingMallSiteIdRejected",
            "unsafeMallIdRejected",
            "unsafeApiBaseUrlRejected",
            "scriptSrcApiBaseUrlFallback",
            "scriptDataAttributeAutoInit",
            "unsafeProductUrlsNeutralized",
            "deferredInitUntilDomReady",
            "repeatedInitReplacesWidget",
            "cssSpecialIdSelectorFallback",
            "complexCssSpecialIdSelectorFallback",
            "ambiguousExplicitSelectorRejected",
            "dynamicAutoAttachAfterDomMutation",
        ]
        config_ok = all(widget_config.get(field) is True for field in required_config_fields)
        sites_with_required_fields = [
            site
            for site in sites
            if isinstance(site, dict)
            and site.get("responsiveLayout") is True
            and site.get("dragDropUpload") is True
            and site.get("oversizedImageRejected") is True
            and site.get("smallImageRejected") is True
            and site.get("damagedImageRejected") is True
            and site.get("validImagePreview") is True
            and site.get("imageRemoveClearsPayload") is True
            and site.get("keyboardCloseRestoresFocus") is True
            and site.get("keyboardTrapCyclesFocus") is True
            and site.get("loadingState") is True
            and site.get("modalCloseControls") is True
            and site.get("rateLimitErrorClearsStaleResults") is True
            and site.get("resultFieldsRendered") is True
            and site.get("modalCopyComplete") is True
            and site.get("supportedImageFormats") is True
            and site.get("resultSectionHeadings") is True
            and site.get("imageAndDetailClickLogging") is True
            and site.get("cameraIconRendered") is True
            and bool(str(site.get("triggerTitle") or "").strip())
            and bool(str(site.get("triggerAriaLabel") or "").strip())
            and bool(str(site.get("prefilledQuery") or "").strip())
            and bool(str(site.get("refreshedQuery") or "").strip())
            and str(site.get("prefilledQuery") or "").strip() != str(site.get("refreshedQuery") or "").strip()
        ]
        ok = (
            data.get("ok") is True
            and not local_only
            and config_ok
            and isinstance(sites, list)
            and len(sites_with_required_fields) >= required_sites
        )
        problems = []
        if local_only:
            problems.append("local_only")
        return (
            ok,
            "representative widget configurations passed" if ok else "widget representative configuration check failed",
            {
                "local_only": local_only,
                "checked_sites": len(sites) if isinstance(sites, list) else 0,
                "sites_with_required_fields": len(sites_with_required_fields),
                "required_sites": required_sites,
                "required_widget_config_fields": required_config_fields,
                "missing_widget_config_fields": [
                    field for field in required_config_fields if widget_config.get(field) is not True
                ],
                "required_fields": [
                    "responsiveLayout",
                    "dragDropUpload",
                    "oversizedImageRejected",
                    "smallImageRejected",
                    "damagedImageRejected",
                    "validImagePreview",
                    "imageRemoveClearsPayload",
                    "keyboardCloseRestoresFocus",
                    "keyboardTrapCyclesFocus",
                    "loadingState",
                    "modalCloseControls",
                    "rateLimitErrorClearsStaleResults",
                    "resultFieldsRendered",
                    "modalCopyComplete",
                    "supportedImageFormats",
                    "resultSectionHeadings",
                    "imageAndDetailClickLogging",
                    "cameraIconRendered",
                    "triggerTitle",
                    "triggerAriaLabel",
                    "prefilledQuery",
                    "refreshedQuery",
                ],
                "problems": problems,
            },
        )

    return _check


def check_representative_sites(required_sites: int) -> EvidenceCheck:
    def _check(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        sites = data.get("sites", [])
        site_count = parse_int_value(data.get("site_count"), len(sites) if isinstance(sites, list) else 0)
        image_source_ok, image_source_problems = check_load_image_source(data, "image", min_image_inputs=1)
        ok_sites = []
        incomplete_sites = []
        site_identity_problems: list[str] = []
        site_url_problems: list[dict[str, Any]] = []
        non_marqo_search_checks: list[dict[str, Any]] = []
        for site in sites:
            if not isinstance(site, dict):
                continue
            checks = site.get("checks", [])
            by_name = {str(check.get("name")): check for check in checks if isinstance(check, dict)}
            missing = sorted(REQUIRED_REPRESENTATIVE_SITE_CHECKS - set(by_name))
            failed = sorted(
                name
                for name, check in by_name.items()
                if name in REQUIRED_REPRESENTATIVE_SITE_CHECKS and check.get("ok") is not True
            )
            local_non_marqo = sorted(
                name
                for name in REPRESENTATIVE_SEARCH_CHECKS
                if name in by_name
                and by_name[name].get("ok") is True
                and str(by_name[name].get("engine") or "").strip().lower() != "marqo"
            )
            missing_product_url_evidence = sorted(
                name
                for name in REPRESENTATIVE_PRODUCT_URL_RULE_CHECKS
                if name in by_name
                and by_name[name].get("ok") is True
                and not str(by_name[name].get("url") or "").strip()
            )
            if local_non_marqo:
                failed = sorted(set(failed + local_non_marqo))
                non_marqo_search_checks.append(
                    {
                        "mall_id": site.get("mall_id"),
                        "url": site.get("url"),
                        "checks": local_non_marqo,
                    }
                )
            if missing_product_url_evidence:
                failed = sorted(set(failed + missing_product_url_evidence))
            if site.get("ok") is True and not missing and not failed:
                ok_sites.append(site)
            else:
                incomplete_sites.append(
                    {
                        "mall_id": site.get("mall_id"),
                        "url": site.get("url"),
                        "origin": site.get("origin"),
                        "missing_checks": missing,
                        "failed_checks": failed,
                        "widget_missing_selectors": (by_name.get("widget_init") or {}).get("missing_selectors") or [],
                        "widget_selector_found": (by_name.get("widget_init") or {}).get("selector_found") or {},
                    }
                )
        mall_ids = [str(site.get("mall_id") or "").strip() for site in ok_sites]
        urls = [str(site.get("url") or "").strip() for site in ok_sites]
        origins = [str(site.get("origin") or "").strip() for site in ok_sites]
        distinct_mall_ids = {value for value in mall_ids if value}
        distinct_urls = {value for value in urls if value}
        distinct_origins = {value for value in origins if value}
        if site_count < required_sites:
            site_identity_problems.append("site_count")
        if len(distinct_mall_ids) < required_sites:
            site_identity_problems.append("distinct_mall_ids")
        if len(distinct_urls) < required_sites:
            site_identity_problems.append("distinct_urls")
        if len(distinct_origins) < required_sites:
            site_identity_problems.append("distinct_origins")
        for index, site in enumerate(ok_sites):
            checks = (
                ("url", site.get("url"), False),
                ("origin", site.get("origin"), True),
                ("api_base_url", site.get("api_base_url") or data.get("api_base_url"), False),
            )
            for field, value, origin_only in checks:
                problems = operational_https_url_problems(field, value, origin_only=origin_only)
                if problems:
                    site_url_problems.append(
                        {
                            "index": index,
                            "mall_id": site.get("mall_id"),
                            "field": field,
                            "problems": problems,
                        }
                    )
        if site_url_problems:
            site_identity_problems.append("site_url_problems")
        if non_marqo_search_checks:
            site_identity_problems.append("non_marqo_search_checks")
        ok = (
            data.get("ok") is True
            and site_count >= required_sites
            and len(ok_sites) >= required_sites
            and image_source_ok
            and not site_identity_problems
        )
        return (
            ok,
            "representative mall site checks passed" if ok else "representative mall site evidence is missing or failed",
            {
                "site_count": site_count,
                "ok_sites": len(ok_sites),
                "required_sites": required_sites,
                "distinct_mall_ids": len(distinct_mall_ids),
                "distinct_urls": len(distinct_urls),
                "distinct_origins": len(distinct_origins),
                "site_identity_problems": sorted(set(site_identity_problems)),
                "site_url_problems": site_url_problems,
                "non_marqo_search_checks": non_marqo_search_checks,
                "image_source_ok": image_source_ok,
                "image_source_problems": image_source_problems,
                "image_input": data.get("image_input") or {},
                "incomplete_sites": incomplete_sites,
            },
        )

    return _check


def check_security(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    required = [
        "https",
        "public_base_url",
        "cors_restricted",
        "cors_origins_https",
        "cors_origins_safe_public",
        "cors_covers_allowed_origins",
        "allowed_origins",
        "allowed_origins_safe_public",
        "product_url_templates_https",
        "product_url_templates_safe_public",
        "admin_key",
        "mall_api_key",
        "mall_api_key_strength",
        "mssql_ip_restricted",
        "production_env",
        "production_search_engine",
        "sync_interval_hourly",
        "sync_failure_alerting",
        "nginx_client_max_body_size",
        "nginx_upstream_resilience",
        "nginx_forwarded_for_safety",
        "systemd_restart_policy",
        "systemd_sync_worker",
        "systemd_reindex_service",
        "systemd_reindex_timer",
        "logrotate_config",
        "service_env_file_permissions",
    ]
    missing_or_false = [key for key in required if data.get(key) is not True]
    evidence_problems: list[str] = []
    failed_checks = data.get("failed_checks")
    if not isinstance(failed_checks, list):
        evidence_problems.append("failed_checks")
        failed_checks = []
    elif failed_checks:
        evidence_problems.extend(f"failed_checks.{name}" for name in failed_checks)

    for url_problem in operational_https_url_problems("base_url", data.get("base_url")):
        evidence_problems.append(url_problem)
    public_base_url = data.get("public_base_url_report") if isinstance(data.get("public_base_url_report"), dict) else {}
    if public_base_url.get("ok") is not True:
        evidence_problems.append("public_base_url_report")
    if public_base_url.get("url") != data.get("base_url"):
        evidence_problems.append("public_base_url_report.url")
    if public_base_url.get("problems") not in ([], None):
        evidence_problems.append("public_base_url_report.problems")

    if data.get("environment") != "production":
        evidence_problems.append("environment")
    if data.get("engine_backend") != "marqo":
        evidence_problems.append("engine_backend")
    if parse_int_value(data.get("sync_interval_seconds")) <= 0 or parse_int_value(data.get("sync_interval_seconds")) > 3600:
        evidence_problems.append("sync_interval_seconds")

    if not isinstance(data.get("cors_origins"), list) or not data.get("cors_origins") or "*" in data.get("cors_origins"):
        evidence_problems.append("cors_origins")
    if parse_int_value(data.get("enabled_mall_count")) <= 0:
        evidence_problems.append("enabled_mall_count")
    expected_empty_collections = [
        "non_https_cors_origins",
        "unsafe_cors_origins",
        "malls_without_allowed_origins",
        "malls_with_wildcard_allowed_origins",
        "malls_with_non_https_allowed_origins",
        "malls_with_unsafe_allowed_origins",
        "malls_missing_cors_origins",
        "malls_without_api_key",
        "malls_with_placeholder_api_keys",
        "malls_with_weak_api_keys",
        "malls_with_non_https_product_url_templates",
        "malls_with_unsafe_product_url_templates",
    ]
    for key in expected_empty_collections:
        value = data.get(key)
        if value not in ([], {}, None):
            evidence_problems.append(key)

    sync_webhook = data.get("sync_alert_webhook") if isinstance(data.get("sync_alert_webhook"), dict) else {}
    sync_webhook_ok = data.get("sync_alert_webhook_configured") is True and data.get("sync_alert_webhook_valid") is True and sync_webhook.get("ok") is True
    external_alerting_ok = data.get("external_sync_alerting_confirmed") is True
    if not (sync_webhook_ok or external_alerting_ok):
        evidence_problems.append("sync_failure_alerting_details")

    supporting_reports = {
        "nginx": "nginx_client_max_body_size",
        "nginx_upstream": "nginx_upstream_resilience",
        "nginx_forwarded_for": "nginx_forwarded_for_safety",
        "systemd": "systemd_restart_policy",
        "sync_systemd": "systemd_sync_worker",
        "reindex_systemd": "systemd_reindex_service",
        "reindex_timer": "systemd_reindex_timer",
        "logrotate": "logrotate_config",
        "service_env_file_permissions_report": "service_env_file_permissions",
    }
    for report_key, check_name in supporting_reports.items():
        report = data.get(report_key) if isinstance(data.get(report_key), dict) else {}
        if report.get("ok") is not True:
            evidence_problems.append(f"{report_key}.ok")
        if not str(report.get("path") or "").strip():
            evidence_problems.append(f"{report_key}.path")
        if check_name in missing_or_false:
            evidence_problems.append(f"{report_key}.required_check")

    ok = data.get("ok") is True and not missing_or_false and not evidence_problems
    return (
        ok,
        "security evidence passed" if ok else "security evidence is incomplete",
        {
            "missing_or_false": missing_or_false,
            "failed_checks": failed_checks,
            "evidence_problems": sorted(set(evidence_problems)),
        },
    )


def compact_details(data: dict[str, Any]) -> dict[str, Any]:
    return {key: data.get(key) for key in ["ok", "base_url", "mall_id", "scenario", "mode"] if key in data}


def is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def is_positive_number(value: Any) -> bool:
    return is_number(value) and float(value) > 0


def data_lineage_item(paths: dict[str, str]) -> dict[str, Any] | None:
    required_reports = {
        "mssql_view": "mssql_view_report",
        "mssql_export": "mssql_export_report",
        "poc_dataset": "poc_dataset_report",
        "image_urls": "image_url_report",
        "quality_report": "quality_report",
        "csv_poc_index": "csv_index_report",
    }
    loaded: dict[str, dict[str, Any]] = {}
    for name, arg_name in required_reports.items():
        data = load_json(paths[arg_name])
        if data is None:
            return None
        loaded[name] = data
    marqo_resource = load_json(paths["marqo_resource_report"])
    if marqo_resource is not None:
        loaded["marqo_resource"] = marqo_resource
    api_smoke = load_json(paths["api_smoke_report"])
    if api_smoke is not None:
        loaded["api_smoke"] = api_smoke
    mall_config = load_json(paths["mall_config_report"])
    if mall_config is not None:
        loaded["mall_config"] = mall_config
    mall_config_build = load_json(paths["mall_config_build_report"])
    if mall_config_build is not None:
        loaded["mall_config_build"] = mall_config_build
    for name, arg_name in {
        "load_text": "load_text_report",
        "load_image": "load_image_report",
        "load_mixed": "load_mixed_report",
        "load_mixed_traffic": "load_mixed_traffic_report",
        "api_scale": "api_scale_report",
        "representative_sites": "representative_sites_report",
        "security": "security_report",
    }.items():
        data = load_json(paths[arg_name])
        if data is not None:
            loaded[name] = data
    ok, message, details = check_data_lineage(loaded)
    item = {
        "name": "data_lineage",
        "status": "passed" if ok else "failed",
        "ok": ok,
        "path": None,
        "message": message,
        "details": details,
    }
    if not ok:
        item["command_hint"] = (
            "Rerun mssql_export_csv.py, poc_dataset_builder.py, image_url_check.py, "
            "csv_index.py, quality_report.py, API smoke, load, API scale, representative site, "
            "and security evidence with the same products_csv, poc_products_csv, and API target."
        )
    return item


def normalized_evidence_value(value: Any, *, trim_trailing_slash: bool = False) -> str | None:
    text = str(value or "").strip()
    if trim_trailing_slash:
        text = text.rstrip("/")
    return text or None


def add_api_target_values(
    label: str,
    data: dict[str, Any],
    api_base_urls: dict[str, str | None],
    api_origins: dict[str, str | None],
    api_mall_ids: dict[str, str | None],
    *,
    base_field: str = "base_url",
    include_origin: bool = True,
    include_mall_id: bool = True,
) -> None:
    api_base_urls[label] = normalized_evidence_value(data.get(base_field), trim_trailing_slash=True)
    if include_origin:
        api_origins[label] = normalized_evidence_value(data.get("origin"), trim_trailing_slash=True)
    if include_mall_id:
        api_mall_ids[label] = normalized_evidence_value(data.get("mall_id"))


def collect_api_target_values(
    reports: dict[str, dict[str, Any]],
) -> tuple[dict[str, str | None], dict[str, str | None], dict[str, str | None]]:
    api_base_urls: dict[str, str | None] = {}
    api_origins: dict[str, str | None] = {}
    api_mall_ids: dict[str, str | None] = {}

    for name in ["api_smoke", "load_text", "load_image", "load_mixed", "load_mixed_traffic"]:
        if name in reports:
            add_api_target_values(name, reports[name], api_base_urls, api_origins, api_mall_ids)

    api_scale = reports.get("api_scale")
    if isinstance(api_scale, dict):
        for summary_name in ["single", "multi"]:
            summary = api_scale.get(summary_name)
            if isinstance(summary, dict):
                add_api_target_values(
                    f"api_scale.{summary_name}",
                    summary,
                    api_base_urls,
                    api_origins,
                    api_mall_ids,
                )

    representative_sites = reports.get("representative_sites")
    if isinstance(representative_sites, dict):
        sites = representative_sites.get("sites")
        if isinstance(sites, list):
            for index, site in enumerate(sites, start=1):
                if isinstance(site, dict):
                    add_api_target_values(
                        f"representative_sites.{index}",
                        site,
                        api_base_urls,
                        api_origins,
                        api_mall_ids,
                        base_field="api_base_url",
                        include_origin=False,
                        include_mall_id=False,
                    )

    security = reports.get("security")
    if isinstance(security, dict):
        add_api_target_values(
            "security",
            security,
            api_base_urls,
            api_origins,
            api_mall_ids,
            include_origin=False,
            include_mall_id=False,
        )

    return api_base_urls, api_origins, api_mall_ids


def add_qwen_runtime_values(
    label: str,
    source: dict[str, Any],
    qwen_models: dict[str, Any],
    qwen_dimensions: dict[str, Any],
    *,
    model_field: str = "qwen_model",
    dimension_field: str = "qwen_embedding_dimensions",
    qwen_required: bool = False,
) -> None:
    model = source.get(model_field)
    dimensions = source.get(dimension_field)
    has_qwen_value = bool(str(model or "").strip()) or bool(str(dimensions or "").strip())
    if not (qwen_required or has_qwen_value):
        return
    qwen_models[label] = model
    qwen_dimensions[label] = dimensions


def qwen_dimension_identity(value: Any) -> str | None:
    if not is_number(value):
        return None
    number = float(value)
    if number <= 0:
        return None
    return str(int(number)) if number.is_integer() else str(number)


def collect_qwen_runtime_values(
    reports: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    qwen_models: dict[str, Any] = {}
    qwen_dimensions: dict[str, Any] = {}

    marqo_resource = reports.get("marqo_resource")
    if isinstance(marqo_resource, dict):
        settings_contract = marqo_resource.get("index_settings_contract")
        if isinstance(settings_contract, dict) and external_embedding_backend(settings_contract.get("embedding_backend")):
            add_qwen_runtime_values(
                "marqo_resource.index_settings_contract",
                {
                    "qwen_model": settings_contract.get("qwen_model") or settings_contract.get("gemini_model"),
                    "qwen_embedding_dimensions": settings_contract.get(
                        "actual_qwen_embedding_dimensions",
                        settings_contract.get(
                            "actual_gemini_embedding_dimensions",
                            settings_contract.get(
                                "expected_qwen_embedding_dimensions",
                                settings_contract.get("expected_gemini_embedding_dimensions"),
                            ),
                        ),
                    ),
                },
                qwen_models,
                qwen_dimensions,
                qwen_required=True,
            )
        qwen_contract = (
            marqo_resource.get("embedding_contract")
            or marqo_resource.get("gemini_embedding_contract")
            or marqo_resource.get("qwen_embedding_contract")
        )
        if isinstance(qwen_contract, dict) and qwen_contract.get("skipped") is not True:
            contract_provider = str(
                (settings_contract.get("embedding_backend") if isinstance(settings_contract, dict) else "")
                or marqo_resource.get("embedding_backend")
                or "qwen"
            )
            for suffix, model_field, dimension_field in [
                ("expected", "expected_model", "expected_dimensions"),
                ("health", "health_model", "health_dimensions"),
                ("text_probe", "probe_model", "probe_dimensions"),
                ("image_probe", "image_probe_model", "image_probe_dimensions"),
            ]:
                add_qwen_runtime_values(
                    f"marqo_resource.{contract_provider}_embedding_contract.{suffix}",
                    {
                        "qwen_model": qwen_contract.get(model_field),
                        "qwen_embedding_dimensions": qwen_contract.get(dimension_field),
                    },
                    qwen_models,
                    qwen_dimensions,
                    qwen_required=True,
                )

    for name in ["load_text", "load_image", "load_mixed", "load_mixed_traffic"]:
        report = reports.get(name)
        if not isinstance(report, dict):
            continue
        snapshot = ((report.get("server_metrics") or {}).get("after") or {}).get("snapshot") or {}
        if not isinstance(snapshot, dict):
            continue
        add_qwen_runtime_values(
            f"{name}.server_metrics",
            snapshot,
            qwen_models,
            qwen_dimensions,
            qwen_required=external_embedding_backend(snapshot.get("embedding_backend")),
        )

    api_scale = reports.get("api_scale")
    if isinstance(api_scale, dict):
        for summary_name in ["single", "multi"]:
            summary = api_scale.get(summary_name)
            if not isinstance(summary, dict):
                continue
            add_qwen_runtime_values(
                f"api_scale.{summary_name}",
                summary,
                qwen_models,
                qwen_dimensions,
                qwen_required=external_embedding_backend(summary.get("embedding_backend")),
            )

    return qwen_models, qwen_dimensions


def collect_required_cors_origins(
    reports: dict[str, dict[str, Any]],
    api_origins: dict[str, str | None],
) -> dict[str, str | None]:
    origins = dict(api_origins)
    representative_sites = reports.get("representative_sites")
    if isinstance(representative_sites, dict):
        sites = representative_sites.get("sites")
        if isinstance(sites, list):
            for index, site in enumerate(sites, start=1):
                if isinstance(site, dict):
                    origins[f"representative_sites.{index}"] = normalized_evidence_value(
                        site.get("origin"),
                        trim_trailing_slash=True,
                    )
    return origins


def collect_security_cors_origins(reports: dict[str, dict[str, Any]]) -> list[str]:
    security = reports.get("security")
    if not isinstance(security, dict) or not isinstance(security.get("cors_origins"), list):
        return []
    origins = [
        normalized_evidence_value(origin, trim_trailing_slash=True)
        for origin in security.get("cors_origins", [])
    ]
    return sorted({origin for origin in origins if origin})


def collect_enabled_mall_ids(reports: dict[str, dict[str, Any]]) -> list[str]:
    mall_config = reports.get("mall_config")
    if not isinstance(mall_config, dict) or not isinstance(mall_config.get("enabled_mall_ids"), list):
        return []
    ids = [normalized_evidence_value(mall_id) for mall_id in mall_config.get("enabled_mall_ids", [])]
    return sorted({mall_id for mall_id in ids if mall_id})


def collect_enabled_mall_origins(reports: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    mall_config = reports.get("mall_config")
    if not isinstance(mall_config, dict) or not isinstance(mall_config.get("enabled_mall_origins"), dict):
        return {}
    origins_by_mall: dict[str, list[str]] = {}
    for mall_id, origins in mall_config.get("enabled_mall_origins", {}).items():
        normalized_mall_id = normalized_evidence_value(mall_id)
        if not normalized_mall_id or not isinstance(origins, list):
            continue
        normalized_origins = [
            normalized_evidence_value(origin, trim_trailing_slash=True)
            for origin in origins
        ]
        origins_by_mall[normalized_mall_id] = sorted({origin for origin in normalized_origins if origin})
    return origins_by_mall


def collect_enabled_mall_product_url_prefixes(reports: dict[str, dict[str, Any]]) -> dict[str, str]:
    mall_config = reports.get("mall_config")
    if not isinstance(mall_config, dict) or not isinstance(mall_config.get("enabled_mall_product_url_prefixes"), dict):
        return {}
    prefixes_by_mall: dict[str, str] = {}
    for mall_id, prefix in mall_config.get("enabled_mall_product_url_prefixes", {}).items():
        normalized_mall_id = normalized_evidence_value(mall_id)
        normalized_prefix = normalized_evidence_value(prefix, trim_trailing_slash=True)
        if normalized_mall_id and normalized_prefix:
            prefixes_by_mall[normalized_mall_id] = normalized_prefix
    return dict(sorted(prefixes_by_mall.items()))


def collect_enabled_mall_api_key_hashes(reports: dict[str, dict[str, Any]]) -> dict[str, str]:
    mall_config = reports.get("mall_config")
    if not isinstance(mall_config, dict) or not isinstance(mall_config.get("enabled_mall_api_key_hashes"), dict):
        return {}
    hashes_by_mall: dict[str, str] = {}
    for mall_id, key_hash in mall_config.get("enabled_mall_api_key_hashes", {}).items():
        normalized_mall_id = normalized_evidence_value(mall_id)
        normalized_hash = normalized_evidence_value(key_hash)
        if normalized_mall_id and normalized_hash:
            hashes_by_mall[normalized_mall_id] = normalized_hash
    return dict(sorted(hashes_by_mall.items()))


def collect_representative_api_key_hashes(reports: dict[str, dict[str, Any]]) -> dict[str, str]:
    representative_sites = reports.get("representative_sites")
    api_key_hashes: dict[str, str] = {}
    if not isinstance(representative_sites, dict):
        return api_key_hashes
    sites = representative_sites.get("sites")
    if not isinstance(sites, list):
        return api_key_hashes
    for index, site in enumerate(sites, start=1):
        if isinstance(site, dict):
            api_key_hashes[f"representative_sites.{index}"] = normalized_evidence_value(site.get("api_key_hash"))
    return api_key_hashes


def collect_representative_product_urls(reports: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    representative_sites = reports.get("representative_sites")
    product_urls: list[dict[str, str]] = []
    if not isinstance(representative_sites, dict):
        return product_urls
    sites = representative_sites.get("sites")
    if not isinstance(sites, list):
        return product_urls
    for index, site in enumerate(sites, start=1):
        if not isinstance(site, dict):
            continue
        mall_id = normalized_evidence_value(site.get("mall_id"))
        checks = site.get("checks")
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, dict) or check.get("name") not in REPRESENTATIVE_PRODUCT_URL_RULE_CHECKS:
                continue
            product_url = normalized_evidence_value(check.get("url"))
            if mall_id and product_url:
                product_urls.append(
                    {
                        "site": f"representative_sites.{index}",
                        "check": str(check.get("name")),
                        "mall_id": mall_id,
                        "url": product_url,
                    }
                )
    return product_urls


def normalized_url_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    if scheme.lower() == "https":
        return 443
    if scheme.lower() == "http":
        return 80
    return None


def evidence_url_matches_prefix(url: str, prefix: str) -> bool:
    url_text = str(url or "").strip()
    prefix_text = str(prefix or "").strip().rstrip("/")
    if not url_text or not prefix_text:
        return False
    parsed_url = urlparse(url_text)
    parsed_prefix = urlparse(prefix_text)
    try:
        url_port = parsed_url.port
        prefix_port = parsed_prefix.port
    except ValueError:
        return False
    if parsed_url.scheme.lower() != parsed_prefix.scheme.lower():
        return False
    if (parsed_url.hostname or "").lower() != (parsed_prefix.hostname or "").lower():
        return False
    if normalized_url_port(parsed_url.scheme, url_port) != normalized_url_port(parsed_prefix.scheme, prefix_port):
        return False
    prefix_path = parsed_prefix.path.rstrip("/")
    if not prefix_path:
        return True
    url_path = parsed_url.path.rstrip("/")
    return url_path == prefix_path or url_path.startswith(prefix_path + "/")


def collect_representative_mall_ids(reports: dict[str, dict[str, Any]]) -> dict[str, str | None]:
    representative_sites = reports.get("representative_sites")
    mall_ids: dict[str, str | None] = {}
    if isinstance(representative_sites, dict):
        sites = representative_sites.get("sites")
        if isinstance(sites, list):
            for index, site in enumerate(sites, start=1):
                if isinstance(site, dict):
                    mall_ids[f"representative_sites.{index}"] = normalized_evidence_value(site.get("mall_id"))
    return mall_ids


def check_data_lineage(reports: dict[str, dict[str, Any]]) -> tuple[bool, str, dict[str, Any]]:
    non_operational_evidence = summarize_non_operational_evidence(reports)
    quality_source = reports["quality_report"].get("source")
    if not isinstance(quality_source, dict):
        quality_source = {}
    mssql_view_report = reports.get("mssql_view") if isinstance(reports.get("mssql_view"), dict) else {}
    full_csv_fingerprints = {
        "mssql_export.output_csv": reports["mssql_export"].get("output_csv_fingerprint"),
        "poc_dataset.source_csv": reports["poc_dataset"].get("source_csv_fingerprint"),
        "image_urls.csv": reports["image_urls"].get("csv_fingerprint"),
    }
    mssql_query_fingerprints = {
        "mssql_view.query": mssql_view_report.get("query_fingerprint"),
        "mssql_export.query": reports["mssql_export"].get("query_fingerprint"),
    }
    poc_csv_fingerprints = {
        "poc_dataset.output_csv": reports["poc_dataset"].get("output_csv_fingerprint"),
        "quality_report.csv": reports["quality_report"].get("csv_fingerprint"),
        "csv_poc_index.csv": reports["csv_poc_index"].get("csv_fingerprint"),
    }
    marqo_indexes = {
        "quality_report.source.index_name": quality_source.get("index_name"),
        "csv_poc_index.index": reports["csv_poc_index"].get("index"),
    }
    marqo_urls = {
        "quality_report.source.marqo_url": quality_source.get("marqo_url"),
        "csv_poc_index.marqo_url": reports["csv_poc_index"].get("marqo_url"),
    }
    marqo_models = {
        "quality_report.source.marqo_model": quality_source.get("marqo_model"),
        "csv_poc_index.marqo_model": reports["csv_poc_index"].get("marqo_model"),
    }
    csv_summary = reports["csv_poc_index"].get("summary")
    if not isinstance(csv_summary, dict):
        csv_summary = {}
    quality_dataset = reports["quality_report"].get("dataset")
    if not isinstance(quality_dataset, dict):
        quality_dataset = {}
    mssql_active_products = parse_int_value(reports["mssql_export"].get("active_products"), -1)
    poc_selected_products = parse_int_value(reports["poc_dataset"].get("selected_products"), -1)
    quality_active_products = parse_int_value(quality_dataset.get("active_products"), -1)
    csv_total_products = parse_int_value(csv_summary.get("total_products"), -1)
    csv_active_products = parse_int_value(csv_summary.get("active_products"), -1)
    csv_inactive_products = parse_int_value(csv_summary.get("inactive_products"), -1)
    csv_indexed_products = parse_int_value(reports["csv_poc_index"].get("indexed"), 0)
    mssql_total_products = parse_int_value(reports["mssql_export"].get("exported_products"), -1)
    mssql_inactive_products = parse_int_value(reports["mssql_export"].get("inactive_products"), -1)
    poc_source_total_products = parse_int_value(reports["poc_dataset"].get("total_products"), -1)
    poc_source_active_products = parse_int_value(reports["poc_dataset"].get("active_products"), -1)
    poc_source_inactive_products = parse_int_value(reports["poc_dataset"].get("inactive_products"), -1)
    image_url_active_products = parse_int_value(reports["image_urls"].get("active_products"), -1)
    required_marqo_documents = max(0, csv_active_products, csv_indexed_products)
    marqo_index_documents: int | None = None
    if "marqo_resource" in reports:
        marqo_indexes["marqo_resource.index"] = reports["marqo_resource"].get("index")
        marqo_urls["marqo_resource.marqo_url"] = reports["marqo_resource"].get("marqo_url")
        marqo_index_documents = marqo_resource_index_documents(reports["marqo_resource"])
    if "api_smoke" in reports:
        api_checks = reports["api_smoke"].get("checks") if isinstance(reports["api_smoke"].get("checks"), list) else []
        sync_status = next(
            (check for check in api_checks if isinstance(check, dict) and check.get("name") == "sync_status"),
            {},
        )
        marqo_indexes["api_smoke.sync_status.index"] = sync_status.get("sync_status_index")
    api_base_urls, api_origins, api_mall_ids = collect_api_target_values(reports)
    qwen_models, qwen_dimensions = collect_qwen_runtime_values(reports)
    required_cors_origins = collect_required_cors_origins(reports, api_origins)
    security_cors_origins = collect_security_cors_origins(reports)
    enabled_mall_ids = collect_enabled_mall_ids(reports)
    enabled_mall_origins = collect_enabled_mall_origins(reports)
    enabled_mall_product_url_prefixes = collect_enabled_mall_product_url_prefixes(reports)
    enabled_mall_api_key_hashes = collect_enabled_mall_api_key_hashes(reports)
    mall_config_build = reports.get("mall_config_build") if isinstance(reports.get("mall_config_build"), dict) else {}
    mall_config_build_validation = (
        mall_config_build.get("validation")
        if isinstance(mall_config_build.get("validation"), dict)
        else {}
    )
    mall_config_build_output = normalized_evidence_value(mall_config_build.get("output"), trim_trailing_slash=True)
    mall_config_report_path = (
        normalized_evidence_value(reports.get("mall_config", {}).get("path"), trim_trailing_slash=True)
        if isinstance(reports.get("mall_config"), dict)
        else None
    )
    build_validation_enabled_ids = (
        {
            normalized_evidence_value(mall_id)
            for mall_id in mall_config_build_validation.get("enabled_mall_ids", [])
            if normalized_evidence_value(mall_id)
        }
        if isinstance(mall_config_build_validation.get("enabled_mall_ids"), list)
        else set()
    )
    build_validation_origins = (
        collect_enabled_mall_origins({"mall_config": mall_config_build_validation})
        if mall_config_build_validation
        else {}
    )
    build_validation_product_url_prefixes = (
        collect_enabled_mall_product_url_prefixes({"mall_config": mall_config_build_validation})
        if mall_config_build_validation
        else {}
    )
    build_validation_api_key_hashes = (
        collect_enabled_mall_api_key_hashes({"mall_config": mall_config_build_validation})
        if mall_config_build_validation
        else {}
    )
    representative_mall_ids = collect_representative_mall_ids(reports)
    representative_api_key_hashes = collect_representative_api_key_hashes(reports)
    representative_product_urls = collect_representative_product_urls(reports)
    problems = []
    if non_operational_evidence:
        problems.append("non_operational_evidence")
    for name, fingerprint in mssql_query_fingerprints.items():
        problems.extend(f"{name}.{problem}" for problem in query_fingerprint_problems(fingerprint))
    for name, fingerprint in {**full_csv_fingerprints, **poc_csv_fingerprints}.items():
        problems.extend(f"{name}.{problem}" for problem in fingerprint_problems(fingerprint))
    for name, value in marqo_indexes.items():
        if not str(value or "").strip():
            problems.append(f"{name}.missing")
    for name, value in marqo_urls.items():
        if not str(value or "").strip():
            problems.append(f"{name}.missing")
    for name, value in marqo_models.items():
        if not str(value or "").strip():
            problems.append(f"{name}.missing")
    for name, value in qwen_models.items():
        if not str(value or "").strip():
            problems.append(f"{name}.missing")
    for name, value in qwen_dimensions.items():
        if qwen_dimension_identity(value) is None:
            problems.append(f"{name}.missing")
    for name, value in api_base_urls.items():
        if not value:
            problems.append(f"api_base_url.{name}.missing")
    for name, value in api_origins.items():
        if not value:
            problems.append(f"api_origin.{name}.missing")
    for name, value in api_mall_ids.items():
        if not value:
            problems.append(f"api_mall_id.{name}.missing")

    mssql_query_digests = {
        fingerprint_digest(value)
        for value in mssql_query_fingerprints.values()
        if fingerprint_digest(value)
    }
    full_digests = {fingerprint_digest(value) for value in full_csv_fingerprints.values() if fingerprint_digest(value)}
    poc_digests = {fingerprint_digest(value) for value in poc_csv_fingerprints.values() if fingerprint_digest(value)}
    index_names = {str(value or "").strip() for value in marqo_indexes.values() if str(value or "").strip()}
    marqo_url_values = {str(value or "").rstrip("/") for value in marqo_urls.values() if str(value or "").strip()}
    marqo_model_values = {str(value or "").strip() for value in marqo_models.values() if str(value or "").strip()}
    qwen_model_values = {str(value or "").strip() for value in qwen_models.values() if str(value or "").strip()}
    qwen_dimension_values = {
        dimension
        for dimension in (qwen_dimension_identity(value) for value in qwen_dimensions.values())
        if dimension is not None
    }
    api_base_url_values = {value for value in api_base_urls.values() if value}
    api_origin_values = {value for value in api_origins.values() if value}
    api_mall_id_values = {value for value in api_mall_ids.values() if value}
    if len(mssql_query_digests) > 1:
        problems.append("mssql_query_fingerprint_mismatch")
    if len(full_digests) > 1:
        problems.append("full_products_csv_digest_mismatch")
    if len(poc_digests) > 1:
        problems.append("poc_products_csv_digest_mismatch")
    if len(index_names) > 1:
        problems.append("marqo_index_mismatch")
    if len(marqo_url_values) > 1:
        problems.append("marqo_url_mismatch")
    if len(marqo_model_values) > 1:
        problems.append("marqo_model_mismatch")
    if len(qwen_model_values) > 1:
        problems.append("qwen_model_mismatch")
    if len(qwen_dimension_values) > 1:
        problems.append("qwen_embedding_dimensions_mismatch")
    if len(api_base_url_values) > 1:
        problems.append("api_base_url_mismatch")
    if len(api_origin_values) > 1:
        problems.append("api_origin_mismatch")
    if len(api_mall_id_values) > 1:
        problems.append("api_mall_id_mismatch")
    full_total_count_values = {
        "mssql_export.exported_products": mssql_total_products,
        "poc_dataset.total_products": poc_source_total_products,
    }
    full_active_count_values = {
        "mssql_export.active_products": mssql_active_products,
        "poc_dataset.active_products": poc_source_active_products,
        "image_urls.active_products": image_url_active_products,
    }
    full_inactive_count_values = {
        "mssql_export.inactive_products": mssql_inactive_products,
        "poc_dataset.inactive_products": poc_source_inactive_products,
    }
    full_count_groups = {
        "full_total_product_count": full_total_count_values,
        "full_active_product_count": full_active_count_values,
        "full_inactive_product_count": full_inactive_count_values,
    }
    full_count_missing_fields: dict[str, list[str]] = {}
    for problem_prefix, values in full_count_groups.items():
        missing_fields = sorted(name for name, value in values.items() if value < 0)
        if missing_fields:
            problems.append(f"{problem_prefix}_missing")
            full_count_missing_fields[problem_prefix] = missing_fields
            continue
        if len(set(values.values())) > 1:
            problems.append(f"{problem_prefix}_mismatch")
    poc_count_values = {
        "poc_dataset.selected_products": poc_selected_products,
        "quality_report.dataset.active_products": quality_active_products,
        "csv_poc_index.summary.total_products": csv_total_products,
        "csv_poc_index.summary.active_products": csv_active_products,
        "csv_poc_index.indexed": csv_indexed_products,
    }
    missing_poc_count_fields = sorted(name for name, value in poc_count_values.items() if value < 0)
    if missing_poc_count_fields:
        problems.append("poc_product_count_missing")
    comparable_poc_counts = {name: value for name, value in poc_count_values.items() if value >= 0}
    if len(set(comparable_poc_counts.values())) > 1:
        problems.append("poc_product_count_mismatch")
    if csv_inactive_products < 0:
        problems.append("csv_poc_index.summary.inactive_products.missing")
    elif csv_inactive_products != 0:
        problems.append("csv_poc_index.summary.inactive_products")
    if "mall_config" in reports and "mall_config_build" in reports:
        if mall_config_build_output and mall_config_report_path and mall_config_build_output != mall_config_report_path:
            problems.append("mall_config_build_output_mismatch")
        if build_validation_enabled_ids and set(enabled_mall_ids) and build_validation_enabled_ids != set(enabled_mall_ids):
            problems.append("mall_config_build_enabled_mall_ids_mismatch")
        if build_validation_origins and enabled_mall_origins and build_validation_origins != enabled_mall_origins:
            problems.append("mall_config_build_origins_mismatch")
        if (
            build_validation_product_url_prefixes
            and enabled_mall_product_url_prefixes
            and build_validation_product_url_prefixes != enabled_mall_product_url_prefixes
        ):
            problems.append("mall_config_build_product_url_prefixes_mismatch")
        if build_validation_api_key_hashes and enabled_mall_api_key_hashes and build_validation_api_key_hashes != enabled_mall_api_key_hashes:
            problems.append("mall_config_build_api_key_hashes_mismatch")
    if mssql_active_products >= 0 and poc_selected_products >= 0 and mssql_active_products < poc_selected_products:
        problems.append("mssql_export_active_products_below_poc_dataset")
    required_cors_origin_values = {value for value in required_cors_origins.values() if value}
    security_cors_missing_origins = sorted(required_cors_origin_values - set(security_cors_origins))
    if "security" in reports and security_cors_missing_origins:
        problems.append("security_cors_missing_api_origins")
    enabled_mall_id_set = set(enabled_mall_ids)
    representative_malls_not_enabled = sorted(
        {
            mall_id
            for mall_id in representative_mall_ids.values()
            if mall_id and enabled_mall_id_set and mall_id not in enabled_mall_id_set
        }
    )
    if "mall_config" in reports and "representative_sites" in reports and representative_malls_not_enabled:
        problems.append("representative_mall_id_not_enabled")
    api_malls_not_enabled = [
        {"evidence": label, "mall_id": mall_id}
        for label, mall_id in sorted(api_mall_ids.items())
        if mall_id and enabled_mall_id_set and mall_id not in enabled_mall_id_set
    ]
    if "mall_config" in reports and api_malls_not_enabled:
        problems.append("api_mall_id_not_enabled")
    api_origin_mismatches = []
    for label, mall_id in sorted(api_mall_ids.items()):
        origin = api_origins.get(label)
        if not mall_id or not origin or mall_id not in enabled_mall_origins:
            continue
        allowed_origins = enabled_mall_origins.get(mall_id) or []
        if origin not in allowed_origins:
            api_origin_mismatches.append(
                {
                    "evidence": label,
                    "mall_id": mall_id,
                    "origin": origin,
                    "allowed_origins": allowed_origins,
                }
            )
    if "mall_config" in reports and api_origin_mismatches:
        problems.append("api_origin_not_allowed_for_mall")
    representative_origin_mismatches = []
    for label, mall_id in representative_mall_ids.items():
        origin = required_cors_origins.get(label)
        if not mall_id or not origin or mall_id not in enabled_mall_origins:
            continue
        allowed_origins = enabled_mall_origins.get(mall_id) or []
        if origin not in allowed_origins:
            representative_origin_mismatches.append(
                {
                    "site": label,
                    "mall_id": mall_id,
                    "origin": origin,
                    "allowed_origins": allowed_origins,
                }
            )
    if "mall_config" in reports and "representative_sites" in reports and representative_origin_mismatches:
        problems.append("representative_origin_not_allowed_for_mall")
    representative_api_key_hash_missing = []
    representative_api_key_mismatches = []
    if "mall_config" in reports and "representative_sites" in reports and enabled_mall_api_key_hashes:
        for label, mall_id in sorted(representative_mall_ids.items()):
            if not mall_id or mall_id not in enabled_mall_api_key_hashes:
                continue
            expected_hash = enabled_mall_api_key_hashes.get(mall_id) or ""
            actual_hash = representative_api_key_hashes.get(label) or ""
            if not actual_hash:
                representative_api_key_hash_missing.append({"site": label, "mall_id": mall_id})
            elif actual_hash != expected_hash:
                representative_api_key_mismatches.append({"site": label, "mall_id": mall_id})
    if representative_api_key_hash_missing:
        problems.append("representative_api_key_hash_missing")
    if representative_api_key_mismatches:
        problems.append("representative_api_key_not_matching_mall_config")
    representative_product_url_mismatches = []
    for item in representative_product_urls:
        mall_id = item.get("mall_id")
        prefix = enabled_mall_product_url_prefixes.get(mall_id or "")
        if not mall_id or not prefix:
            continue
        if not evidence_url_matches_prefix(item.get("url", ""), prefix):
            representative_product_url_mismatches.append(
                {
                    "site": item.get("site"),
                    "check": item.get("check"),
                    "mall_id": mall_id,
                    "url": item.get("url"),
                    "expected_prefix": prefix,
                }
            )
    if "mall_config" in reports and "representative_sites" in reports and representative_product_url_mismatches:
        problems.append("representative_product_url_not_matching_mall_template")
    if marqo_index_documents is not None:
        if marqo_index_documents < required_marqo_documents:
            problems.append("marqo_resource_documents_below_csv_index")
        elif marqo_index_documents > required_marqo_documents:
            problems.append("marqo_resource_documents_exceed_csv_index")

    details = {
        "mssql_query": summarize_query_fingerprint_group(mssql_query_fingerprints),
        "full_products_csv": summarize_fingerprint_group(full_csv_fingerprints),
        "poc_products_csv": summarize_fingerprint_group(poc_csv_fingerprints),
        "marqo_index": summarize_value_group(marqo_indexes),
            "marqo_url": summarize_value_group(marqo_urls),
            "marqo_model": summarize_value_group(marqo_models),
            "qwen_model": summarize_value_group(qwen_models),
            "qwen_embedding_dimensions": summarize_value_group(qwen_dimensions),
            "api_base_url": summarize_value_group(api_base_urls),
        "api_origin": summarize_value_group(api_origins),
        "api_mall_id": summarize_value_group(api_mall_ids),
        "security_cors": {
            "configured_origins": security_cors_origins,
            "required_origins": summarize_value_group(required_cors_origins),
            "missing_origins": security_cors_missing_origins,
        },
        "mall_identity": {
            "enabled_mall_ids_count": len(enabled_mall_ids),
            "enabled_mall_id_samples": enabled_mall_ids[:5],
            "enabled_mall_origins_count": len(enabled_mall_origins),
            "enabled_mall_origin_samples": {
                mall_id: enabled_mall_origins.get(mall_id)
                for mall_id in sorted(enabled_mall_origins)[:3]
            },
            "enabled_mall_product_url_prefixes_count": len(enabled_mall_product_url_prefixes),
            "enabled_mall_product_url_prefix_samples": {
                mall_id: enabled_mall_product_url_prefixes.get(mall_id)
                for mall_id in sorted(enabled_mall_product_url_prefixes)[:3]
            },
            "enabled_mall_api_key_hashes_count": len(enabled_mall_api_key_hashes),
            "mall_config_build_output": mall_config_build_output,
            "mall_config_report_path": mall_config_report_path,
            "mall_config_build_validation_enabled_mall_ids_count": len(build_validation_enabled_ids),
            "mall_config_build_validation_origins_count": len(build_validation_origins),
            "mall_config_build_validation_product_url_prefixes_count": len(build_validation_product_url_prefixes),
            "mall_config_build_validation_api_key_hashes_count": len(build_validation_api_key_hashes),
            "representative_mall_ids": summarize_value_group(representative_mall_ids),
            "representative_api_key_hashes_count": len(
                [value for value in representative_api_key_hashes.values() if value]
            ),
            "api_malls_not_enabled": api_malls_not_enabled,
            "api_origin_mismatches": api_origin_mismatches,
            "representative_malls_not_enabled": representative_malls_not_enabled,
            "representative_origin_mismatches": representative_origin_mismatches,
            "representative_api_key_hash_missing": representative_api_key_hash_missing,
            "representative_api_key_mismatches": representative_api_key_mismatches,
            "representative_product_url_mismatches": representative_product_url_mismatches,
        },
        "marqo_document_counts": {
            "csv_poc_index.active_products": csv_active_products,
            "csv_poc_index.indexed": csv_indexed_products,
            "marqo_resource.index_documents": marqo_index_documents,
            "required_minimum": required_marqo_documents,
            "expected_exact": required_marqo_documents,
        },
        "full_product_counts": {
            "total": full_total_count_values,
            "active": full_active_count_values,
            "inactive": full_inactive_count_values,
            "missing_fields": full_count_missing_fields,
        },
        "poc_product_counts": {
            "mssql_export.active_products": mssql_active_products,
            "poc_dataset.selected_products": poc_selected_products,
            "quality_report.dataset.active_products": quality_active_products,
            "csv_poc_index.summary.total_products": csv_total_products,
            "csv_poc_index.summary.active_products": csv_active_products,
            "csv_poc_index.summary.inactive_products": csv_inactive_products,
            "csv_poc_index.indexed": csv_indexed_products,
            "missing_fields": missing_poc_count_fields,
        },
        "non_operational_evidence": non_operational_evidence,
        "problems": problems,
    }
    ok = not problems
    return (
        ok,
        "MSSQL view/export, image URL probe, PoC dataset, index, quality, Marqo resource, API smoke, load, API scale, site, and security reports use matching query/CSV fingerprints, Marqo/API target details, mall API key fingerprints, and mall URL rules"
        if ok
        else "MSSQL/CSV/index/API evidence lineage is missing query or CSV fingerprints/Marqo/API target details, uses local/simulated reports, or points at mismatched product datasets/Marqo/API targets/mall API keys/mall URL rules",
        details,
    )


def summarize_non_operational_evidence(reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for name, report in sorted(reports.items()):
        reasons: list[str] = []
        if is_simulated_evidence(report):
            reasons.append("simulation_only")
        if report.get("local_only") is True:
            reasons.append("local_only")
        if report.get("not_operational_readiness") is True:
            reasons.append("not_operational_readiness")
        if reasons:
            findings.append({"name": name, "reasons": sorted(set(reasons))})
    return findings


def summarize_fingerprint_group(fingerprints: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "path": fingerprint.get("path") if isinstance(fingerprint, dict) else None,
            "digest": fingerprint_digest(fingerprint),
            "size_bytes": fingerprint.get("size_bytes") if isinstance(fingerprint, dict) else None,
            "exists": fingerprint.get("exists") if isinstance(fingerprint, dict) else None,
        }
        for name, fingerprint in fingerprints.items()
    }


def summarize_query_fingerprint_group(fingerprints: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "algorithm": fingerprint.get("algorithm") if isinstance(fingerprint, dict) else None,
            "digest": fingerprint_digest(fingerprint),
            "normalized_length": fingerprint.get("normalized_length") if isinstance(fingerprint, dict) else None,
        }
        for name, fingerprint in fingerprints.items()
    }


def summarize_value_group(values: dict[str, Any]) -> dict[str, str | None]:
    return {name: str(value).strip() if str(value or "").strip() else None for name, value in values.items()}


def marqo_resource_index_documents(data: dict[str, Any]) -> int | None:
    index_stats = data.get("index_stats") if isinstance(data.get("index_stats"), dict) else {}
    index_stats_data = index_stats.get("data") if isinstance(index_stats.get("data"), dict) else {}
    value = parse_int_value(index_stats_data.get("numberOfDocuments", index_stats_data.get("number_of_documents")), -1)
    return value if value >= 0 else None


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    required_sites = max(1, args.required_sites)
    expected_malls = max(1, args.expected_malls)
    max_evidence_age_days = max(
        0,
        int(getattr(args, "max_evidence_age_days", DEFAULT_MAX_EVIDENCE_AGE_DAYS) or 0),
    )
    paths = {arg_name: resolve_report_path(args, arg_name) for arg_name in DEFAULT_EVIDENCE_FILENAMES}
    def readiness_item(name: str, path: str, checker: EvidenceCheck) -> dict[str, Any]:
        return evidence_item(
            name,
            path,
            checker,
            command_hint(name, path),
            max_evidence_age_days=max_evidence_age_days,
        )

    checks = [
        readiness_item("api_smoke", paths["api_smoke_report"], check_api_smoke),
        readiness_item("mssql_export", paths["mssql_export_report"], check_mssql_export),
        readiness_item("poc_dataset", paths["poc_dataset_report"], check_poc_dataset),
        readiness_item("mssql_view", paths["mssql_view_report"], check_mssql_view),
        readiness_item("image_urls", paths["image_url_report"], check_image_urls),
        readiness_item("quality_report", paths["quality_report"], check_quality),
        readiness_item("csv_poc_index", paths["csv_index_report"], check_csv_index),
        readiness_item("mall_config_build", paths["mall_config_build_report"], check_mall_config_build(expected_malls)),
        readiness_item("mall_config", paths["mall_config_report"], check_mall_config(expected_malls)),
        readiness_item("marqo_resource", paths["marqo_resource_report"], check_marqo_resource),
        readiness_item("server_preflight", paths["server_preflight_report"], check_server_preflight),
        readiness_item("env_preflight", paths["env_check_report"], check_env_preflight),
        readiness_item("load_text_100_concurrent", paths["load_text_report"], check_load("text", min_concurrency=100, min_requests=100)),
        readiness_item("load_image_30_concurrent", paths["load_image_report"], check_load("image", min_concurrency=30, min_requests=30)),
        readiness_item("load_mixed_30_concurrent", paths["load_mixed_report"], check_load("mixed", min_concurrency=30, min_requests=30)),
        readiness_item(
            "load_mixed_traffic_850_active_users",
            paths["load_mixed_traffic_report"],
            check_load("mixed-traffic", min_concurrency=100, min_active_users=850, min_requests=850),
        ),
        readiness_item("api_scale_comparison", paths["api_scale_report"], check_api_scale),
        readiness_item("representative_mall_sites", paths["representative_sites_report"], check_representative_sites(required_sites)),
        readiness_item("security", paths["security_report"], check_security),
    ]
    lineage = data_lineage_item(paths)
    if lineage is not None:
        checks.insert(7, lineage)
    status_counts = {
        "passed": sum(1 for item in checks if item["status"] == "passed"),
        "failed": sum(1 for item in checks if item["status"] == "failed"),
        "missing": sum(1 for item in checks if item["status"] == "missing"),
    }
    return {
        "ok": all(item["ok"] for item in checks),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expected_malls": expected_malls,
        "required_sites": required_sites,
        "max_evidence_age_days": max_evidence_age_days,
        "status_counts": status_counts,
        "checks": checks,
    }


def sanitize_report_for_deployment(
    value: Any,
    deployment_project_root: str | Path = "",
    deployment_evidence_dir: str | Path = "",
) -> Any:
    if not str(deployment_project_root or "").strip() and not str(deployment_evidence_dir or "").strip():
        return value
    if isinstance(value, dict):
        return {
            key: sanitize_report_for_deployment(
                item,
                deployment_project_root=deployment_project_root,
                deployment_evidence_dir=deployment_evidence_dir,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            sanitize_report_for_deployment(
                item,
                deployment_project_root=deployment_project_root,
                deployment_evidence_dir=deployment_evidence_dir,
            )
            for item in value
        ]
    if isinstance(value, str):
        return sanitize_report_string(value, deployment_project_root, deployment_evidence_dir)
    return value


def sanitize_report_string(
    value: str,
    deployment_project_root: str | Path = "",
    deployment_evidence_dir: str | Path = "",
) -> str:
    text = normalize_python_invocation(value)
    if str(deployment_evidence_dir or "").strip():
        text = rewrite_evidence_paths(text, deployment_evidence_dir)
        text = replace_derived_output_placeholders(text, deployment_evidence_dir, shell="bash")
    if str(deployment_project_root or "").strip():
        text = rewrite_project_paths(text, deployment_project_root)
    return text


def normalize_python_invocation(text: str) -> str:
    return re.sub(r"[A-Za-z]:\\[^\r\n\"']*?python(?:\.exe)?(?=\s+scripts[\\/])", "python", text)


def rewrite_evidence_paths(text: str, deployment_evidence_dir: str | Path) -> str:
    evidence_filenames = sorted(
        {*DEFAULT_EVIDENCE_FILENAMES.values(), *DERIVED_OUTPUT_PLACEHOLDERS.values()},
        key=len,
        reverse=True,
    )
    filename_pattern = "|".join(re.escape(filename) for filename in evidence_filenames)
    patterns = [
        r"[A-Za-z]:[\\/][^\r\n\"'` ]*?[\\/]HaeorumAISearch[\\/](?:logs|reports(?:[\\/]evidence)?)[\\/](?P<filename>[^\r\n\"'` ]+)",
        r"(?<![/\\])examples[\\/]HaeorumAISearch[\\/](?:logs|reports(?:[\\/]evidence)?)[\\/](?P<filename>[^\r\n\"'` ]+)",
        rf"[A-Za-z]:[\\/][^\r\n\"'` ]*?[\\/](?P<filename>{filename_pattern})",
    ]
    result = text
    for pattern in patterns:
        result = re.sub(
            pattern,
            lambda match: join_evidence_output_path(deployment_evidence_dir, match.group("filename"), shell="bash"),
            result,
        )
    return result


def rewrite_project_paths(text: str, deployment_project_root: str | Path) -> str:
    target_root = normalize_bash_path(str(deployment_project_root)).rstrip("/")
    if not target_root:
        return text
    result = text
    for candidate in [str(ROOT), ROOT.as_posix()]:
        if candidate:
            result = result.replace(candidate, target_root)
    for candidate in [r"examples\HaeorumAISearch", "examples/HaeorumAISearch"]:
        result = replace_relative_project_path(result, candidate, target_root)
    result = result.replace(target_root + "\\", target_root + "/")
    return re.sub(
        re.escape(target_root) + r"(?P<tail>[^\s\"'`]*)",
        lambda match: target_root + match.group("tail").replace("\\", "/"),
        result,
    )


def replace_relative_project_path(text: str, candidate: str, target_root: str) -> str:
    pattern = r"(?<![/\\])" + re.escape(candidate)
    return re.sub(pattern, target_root, text)


def to_markdown(
    report: dict[str, Any],
    deployment_project_root: str | Path = "",
    deployment_evidence_dir: str | Path = "",
) -> str:
    lines = [
        "# Haeorum AI Search Operational Readiness",
        "",
        f"- OK: `{report['ok']}`",
        f"- Expected malls: `{report['expected_malls']}`",
        f"- Required representative sites: `{report['required_sites']}`",
        f"- Max evidence age days: `{report.get('max_evidence_age_days')}`",
    ]
    if str(deployment_project_root or "").strip():
        lines.append(f"- Run from: `{normalize_bash_path(str(deployment_project_root))}`")
    if str(deployment_evidence_dir or "").strip():
        lines.append(f"- Evidence output dir: `{normalize_bash_path(str(deployment_evidence_dir))}`")
    lines.extend(
        [
            "",
            "| Check | Status | Message | Next Step |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in report["checks"]:
        next_step = ""
        if item.get("status") != "passed":
            next_step = markdown_next_step(item, deployment_evidence_dir)
        lines.append(
            "| {name} | {status} | {message} | {next_step} |".format(
                name=escape_markdown_cell(item["name"]),
                status=escape_markdown_cell(item["status"]),
                message=escape_markdown_cell(item["message"]),
                next_step=escape_markdown_cell(next_step),
            )
        )
    return "\n".join(lines) + "\n"


def markdown_next_step(item: dict[str, Any], deployment_evidence_dir: str | Path = "") -> str:
    if str(deployment_evidence_dir or "").strip():
        return command_hint_for_item(item, evidence_output_dir=deployment_evidence_dir, shell="bash")
    return str(item.get("command_hint") or "")


def escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def to_missing_commands_script(
    report: dict[str, Any],
    project_root: str | Path = ROOT,
    shell: str = "powershell",
    evidence_output_dir: str | Path | None = None,
) -> str:
    if shell == "bash":
        return to_missing_commands_bash(report, project_root, evidence_output_dir=evidence_output_dir)
    return to_missing_commands_powershell(report, project_root, evidence_output_dir=evidence_output_dir)


def to_missing_commands_powershell(
    report: dict[str, Any],
    project_root: str | Path = ROOT,
    evidence_output_dir: str | Path | None = None,
) -> str:
    evidence_dir = str(evidence_output_dir or "").strip()
    lines = [
        "# Haeorum AI Search missing operational evidence commands",
        "# Replace placeholder values wrapped in <...> before running in production.",
        "$ErrorActionPreference = 'Stop'",
        f"$ProjectRoot = {quote_powershell_string(normalize_script_project_root(project_root, shell='powershell'))}",
    ]
    if evidence_dir:
        lines.append(f"$EvidenceDir = {quote_powershell_string(evidence_dir)}")
    lines.extend(
        [
            "$PlaceholderOpen = '<'",
            "$PlaceholderClose = '>'",
            (
                "$PlaceholderPattern = [regex]::Escape($PlaceholderOpen) + '[^' + "
                "[regex]::Escape($PlaceholderClose) + '][^' + [regex]::Escape($PlaceholderClose) + "
                "']*' + [regex]::Escape($PlaceholderClose)"
            ),
            "if ($PSCommandPath) {",
            "  $ActiveScriptText = (Get-Content -LiteralPath $PSCommandPath | Where-Object { $_ -notmatch '^\\s*#' }) -join \"`n\"",
            "  if ($ActiveScriptText -match $PlaceholderPattern) {",
            "    throw 'Replace placeholder values wrapped in angle brackets before running this script.'",
            "  }",
            "}",
        ]
    )
    if evidence_dir:
        lines.append("New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null")
    lines.extend(["Push-Location $ProjectRoot", "try {", ""])
    missing_or_failed = [item for item in report["checks"] if item.get("status") != "passed"]
    if not missing_or_failed:
        lines.append("  # No missing or failed evidence checks.")
        lines.extend(["", "} finally {", "  Pop-Location", "}"])
        return "\n".join(lines) + "\n"
    for item in missing_or_failed:
        command_lines = command_lines_for_item(item, shell="powershell", evidence_output_dir=evidence_output_dir)
        if not command_lines:
            continue
        lines.append(f"  # {item.get('name')}: {item.get('status')} - {item.get('message')}")
        lines.append(f"  Write-Host {quote_powershell_string(step_execution_label(item))}")
        lines.extend("  " + line for line in command_lines)
        lines.append("")
    lines.extend(["} finally {", "  Pop-Location", "}"])
    return "\n".join(lines).rstrip() + "\n"


def to_missing_commands_bash(
    report: dict[str, Any],
    project_root: str | Path = ROOT,
    evidence_output_dir: str | Path | None = None,
) -> str:
    evidence_dir = str(evidence_output_dir or "").strip()
    lines = [
        "#!/usr/bin/env bash",
        "# Haeorum AI Search missing operational evidence commands",
        "# Replace placeholder values wrapped in <...> before running in production.",
        "set -euo pipefail",
        f"PROJECT_ROOT={quote_shell_string(normalize_script_project_root(project_root, shell='bash'))}",
    ]
    if evidence_dir:
        lines.append(f"EVIDENCE_DIR={quote_shell_string(normalize_bash_path(evidence_dir))}")
    lines.extend(
        [
            'SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"',
            "PLACEHOLDER_OPEN='<'",
            "PLACEHOLDER_CLOSE='>'",
            'if [ -f "$SCRIPT_PATH" ] && grep -Ev \'^[[:space:]]*#\' "$SCRIPT_PATH" | grep -q "${PLACEHOLDER_OPEN}[^${PLACEHOLDER_CLOSE}][^${PLACEHOLDER_CLOSE}]*${PLACEHOLDER_CLOSE}"; then',
            "  echo 'ERROR: replace placeholder values wrapped in angle brackets before running this script.' >&2",
            "  exit 2",
            "fi",
        ]
    )
    if evidence_dir:
        lines.append('mkdir -p "$EVIDENCE_DIR"')
    lines.extend(['cd "$PROJECT_ROOT"', ""])
    missing_or_failed = [item for item in report["checks"] if item.get("status") != "passed"]
    if not missing_or_failed:
        lines.append("# No missing or failed evidence checks.")
        return "\n".join(lines) + "\n"
    for item in missing_or_failed:
        command_lines = command_lines_for_item(item, shell="bash", evidence_output_dir=evidence_output_dir)
        if not command_lines:
            continue
        lines.extend(
            [
                f"# {item.get('name')}: {item.get('status')} - {item.get('message')}",
                f"echo {quote_shell_string(step_execution_label(item))}",
                *command_lines,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def step_execution_label(item: dict[str, Any]) -> str:
    return re.sub(
        r"\s+",
        " ",
        "[haeorum-evidence] {name}: {status} - {message}".format(
            name=item.get("name") or "",
            status=item.get("status") or "",
            message=item.get("message") or "",
        ),
    ).strip()


def command_lines_for_item(
    item: dict[str, Any],
    shell: str,
    evidence_output_dir: str | Path | None = None,
) -> list[str]:
    command = command_hint_for_item(item, evidence_output_dir, shell=shell)
    return [quote_placeholder_tokens(command)] if command else []


def evidence_output_path_for_item(item: dict[str, Any], evidence_output_dir: str | Path | None = None) -> str:
    output_dir = str(evidence_output_dir or "").strip()
    name = str(item.get("name") or "")
    arg_name = CHECK_REPORT_ARGS.get(name, "")
    filename = DEFAULT_EVIDENCE_FILENAMES.get(arg_name, name + ".json")
    if output_dir:
        return join_evidence_output_path(output_dir, filename)
    path = str(item.get("path") or "").strip()
    return path or f"<evidence_dir>/{filename}"


def quote_placeholder_tokens(command: str) -> str:
    return re.sub(r"(?<![\"'])<([^>\r\n]+)>", r'"<\1>"', command)


def quote_powershell_string(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def quote_shell_string(value: Any) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def normalize_script_project_root(value: str | Path, shell: str) -> str:
    text = str(value)
    if shell == "bash":
        return normalize_bash_path(text)
    if text.startswith("/") and not Path(text).drive:
        return normalize_bash_path(text)
    path = Path(text)
    return str(path if path.is_absolute() else path.resolve())


def normalize_bash_path(value: str) -> str:
    return str(value).replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate operational evidence for Haeorum AI Search readiness.")
    parser.add_argument(
        "--evidence-dir",
        default="",
        help="Directory containing standard evidence filenames such as api-smoke.json and security.json.",
    )
    parser.add_argument("--api-smoke-report", default="")
    parser.add_argument("--mssql-export-report", default="")
    parser.add_argument("--poc-dataset-report", default="")
    parser.add_argument("--mssql-view-report", default="")
    parser.add_argument("--image-url-report", default="")
    parser.add_argument("--quality-report", default="")
    parser.add_argument("--csv-index-report", default="")
    parser.add_argument("--mall-config-build-report", default="")
    parser.add_argument("--mall-config-report", default="")
    parser.add_argument("--marqo-resource-report", default="")
    parser.add_argument("--server-preflight-report", default="")
    parser.add_argument("--env-check-report", default="")
    parser.add_argument("--load-text-report", default="")
    parser.add_argument("--load-image-report", default="")
    parser.add_argument("--load-mixed-report", default="")
    parser.add_argument("--load-mixed-traffic-report", default="")
    parser.add_argument("--api-scale-report", default="")
    parser.add_argument("--widget-dom-report", default="", help=argparse.SUPPRESS)
    parser.add_argument("--representative-sites-report", default="")
    parser.add_argument("--security-report", default="")
    parser.add_argument("--expected-malls", type=int, default=1700)
    parser.add_argument("--required-sites", type=int, default=3)
    parser.add_argument(
        "--max-evidence-age-days",
        type=int,
        default=DEFAULT_MAX_EVIDENCE_AGE_DAYS,
        help="Reject evidence reports whose generated_at is older than this many days. Use 0 only for local debugging.",
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument(
        "--missing-commands-output",
        default="",
        help="Write a PowerShell checklist containing commands for missing or failed evidence reports.",
    )
    parser.add_argument(
        "--missing-commands-shell",
        choices=["powershell", "bash"],
        default="powershell",
        help="Shell syntax for --missing-commands-output.",
    )
    parser.add_argument(
        "--missing-commands-project-root",
        default="",
        help="Project root written into the missing evidence checklist. Defaults to this example directory.",
    )
    parser.add_argument(
        "--missing-commands-evidence-dir",
        default="",
        help=(
            "Evidence output directory written into the missing evidence checklist. "
            "Defaults to the report paths used by this readiness run."
        ),
    )
    args = parser.parse_args()

    report = build_report(args)
    output_report = sanitize_report_for_deployment(
        report,
        deployment_project_root=args.missing_commands_project_root,
        deployment_evidence_dir=args.missing_commands_evidence_dir,
    )
    text = json.dumps(output_report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).write_text(
            to_markdown(
                output_report,
                deployment_project_root=args.missing_commands_project_root,
                deployment_evidence_dir=args.missing_commands_evidence_dir,
            ),
            encoding="utf-8",
        )
    if args.missing_commands_output:
        checklist_root = args.missing_commands_project_root or ROOT
        Path(args.missing_commands_output).write_text(
            to_missing_commands_script(
                output_report,
                project_root=checklist_root,
                shell=args.missing_commands_shell,
                evidence_output_dir=args.missing_commands_evidence_dir,
            ),
            encoding="utf-8",
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
