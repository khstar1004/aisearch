from __future__ import annotations

import json
import ipaddress
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .identifiers import normalize_mall_id
from .sql_safety import validate_readonly_query
from .url_safety import is_link_or_unspecified_host, is_local_or_link_host, safe_absolute_http_url


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PRODUCT_URL_TEMPLATE_FIELD = "{product_id}"
ORIGIN_EXAMPLE = "https://shop.example.com"
DEFAULT_ADMIN_API_KEY = "dev-admin-key"
DEFAULT_GEMINI_MODEL = "gemini-embedding-2"
DEFAULT_GEMINI_EMBEDDING_DIMENSIONS = 1536
DEFAULT_QWEN_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
DEFAULT_QWEN_EMBEDDING_DIMENSIONS = 2048
PLACEHOLDER_ADMIN_API_KEYS = {DEFAULT_ADMIN_API_KEY, "replace-with-admin-key", "change-me", "changeme"}
PLACEHOLDER_CONFIG_VALUES = {"...", "change-me", "changeme", "dummy", "sample"}
PLACEHOLDER_CONFIG_PREFIXES = ("replace-with", "dummy", "sample")
PLACEHOLDER_PUBLIC_API_KEY_VALUES = {"...", "change-me", "changeme", "dummy", "sample"}
PLACEHOLDER_PUBLIC_API_KEY_PREFIXES = ("replace-with", "dummy", "sample")
BOOLEAN_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
BOOLEAN_FALSE_VALUES = {"0", "false", "no", "n", "off"}
REDIS_URL_SCHEMES = {"redis", "rediss"}
MSSQL_CONNECTION_ENCRYPT_VALUES = {"yes", "true", "mandatory", "strict", "1"}
MSSQL_CONNECTION_TRUST_CERT_FALSE_VALUES = {"no", "false", "0"}
MSSQL_CONNECTION_READONLY_VALUES = {"readonly", "read only"}
PUBLIC_API_KEY_MIN_LENGTH = 24
PUBLIC_API_KEY_MIN_DISTINCT_CHARS = 10
PRODUCTION_ENVIRONMENTS = {"prod", "production"}
SUPPORTED_SEARCH_ENGINES = {"local", "marqo", "typesense", "qdrant"}
RESERVED_SEARCH_ENGINES = {"typesense", "qdrant"}
DEPLOYABLE_SEARCH_ENGINES = SUPPORTED_SEARCH_ENGINES - RESERVED_SEARCH_ENGINES
PRODUCTION_SEARCH_ENGINES = {"marqo"}
SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_PRODUCTION_SYNC_INTERVAL_SECONDS = 3600
MAX_OPERATIONAL_SEARCH_OFFSET = 500
API_THREADPOOL_OVERHEAD_TOKENS = 8
DEFAULT_API_THREADPOOL_TOKENS = 96
PRODUCTION_CONFIG_FILE_ENV_NAMES = (
    "HAEORUM_MALL_CONFIG_PATH",
    "HAEORUM_CORS_ORIGINS_FILE",
    "HAEORUM_QUERY_SYNONYM_PATH",
    "HAEORUM_GEMINI_QUERY_EMBEDDING_CACHE",
    "HAEORUM_QWEN_QUERY_EMBEDDING_CACHE",
)
PRODUCTION_SAMPLE_CONFIG_FILENAMES = {
    "HAEORUM_MALL_CONFIG_PATH": {"sample_malls.json"},
}


def required_api_threadpool_tokens(search_max_concurrency: int, image_search_max_concurrency: int) -> int:
    return max(
        1,
        max(0, int(search_max_concurrency))
        + max(0, int(image_search_max_concurrency))
        + API_THREADPOOL_OVERHEAD_TOKENS,
    )


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    text = str(value).strip()
    if not text:
        return default
    if is_placeholder_config_value(text):
        raise ValueError(f"{name} must be explicitly set to an integer value")
    try:
        return int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    text = str(value).strip()
    if not text:
        return default
    if is_placeholder_config_value(text):
        raise ValueError(f"{name} must be explicitly set to a numeric value")
    try:
        return float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc


def _first_env(*names: str, default: str | None = None) -> str | None:
    _name, value = _first_named_env(tuple(names))
    return value if _name is not None else default


def _int_env_alias(names: tuple[str, ...], default: int) -> int:
    value_name, value = _first_named_env(names)
    if value_name is None:
        return default
    text = str(value).strip()
    if is_placeholder_config_value(text):
        raise ValueError(f"{value_name} must be explicitly set to an integer value")
    try:
        return int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{value_name} must be an integer") from exc


def _float_env_alias(names: tuple[str, ...], default: float) -> float:
    value_name, value = _first_named_env(names)
    if value_name is None:
        return default
    text = str(value).strip()
    if is_placeholder_config_value(text):
        raise ValueError(f"{value_name} must be explicitly set to a numeric value")
    try:
        return float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{value_name} must be a number") from exc


def _first_named_env(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return name, str(value).strip()
    return None, None


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    if is_placeholder_config_value(normalized):
        raise ValueError(f"{name} must be explicitly set to true or false")
    if normalized in BOOLEAN_TRUE_VALUES:
        return True
    if normalized in BOOLEAN_FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class MallConfig:
    mall_id: str
    api_key: str | None = None
    product_url_template: str | None = None
    enabled: bool = True
    allowed_origins: tuple[str, ...] = ()
    excluded_product_ids: tuple[str, ...] = ()
    excluded_categories: tuple[str, ...] = ()
    hide_prices: bool = False
    price_multiplier: float = 1.0
    price_adjustment: float = 0.0
    price_round_to: int = 1


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    engine_backend: str = "marqo"
    marqo_url: str = "http://localhost:8882"
    marqo_model: str = "Marqo/marqo-ecommerce-embeddings-L"
    marqo_search_timeout_seconds: float = 15.0
    marqo_search_retry_count: int = 1
    marqo_search_retry_delay_seconds: float = 0.1
    backend_retry_after_max_seconds: float = 2.0
    backend_http_max_idle_seconds: float = 55.0
    backend_http_max_active_requests: int = DEFAULT_API_THREADPOOL_TOKENS
    backend_http_connection_acquire_timeout_seconds: float = 1.0
    backend_circuit_failure_threshold: int = 5
    backend_circuit_cooldown_seconds: float = 5.0
    backend_circuit_half_open_max_calls: int = 1
    admin_metrics_health_cache_seconds: float = 2.0
    marqo_add_documents_batch_size: int = 128
    marqo_add_documents_max_request_bytes: int = 8 * 1024 * 1024
    marqo_delete_documents_batch_size: int = 512
    embedding_backend: str = "gemini"
    qwen_embedding_url: str = "http://localhost:8098"
    qwen_embedding_dimensions: int = DEFAULT_GEMINI_EMBEDDING_DIMENSIONS
    qwen_model: str = DEFAULT_GEMINI_MODEL
    qwen_embedding_proxy_api_key: str | None = None
    qwen_query_timeout_seconds: float = 15.0
    qwen_mixed_query_parallelism: int = 8
    qwen_query_embedding_cache_path: Path | None = None
    qwen_query_runtime_text_cache_entries: int = 2048
    qwen_query_runtime_image_cache_entries: int = 512
    index_name: str = "haeorum-products"
    admin_api_key: str = DEFAULT_ADMIN_API_KEY
    default_limit: int = 20
    max_limit: int = 50
    max_offset: int = 200
    max_image_mb: int = 5
    max_image_dimension: int = 1600
    min_image_dimension: int = 16
    image_validation_cache_ttl_seconds: float = 30.0
    image_validation_cache_max_entries: int = 32
    mixed_text_weight: float = 0.4
    mixed_image_weight: float = 0.6
    text_auxiliary_weight: float = 0.12
    text_auxiliary_candidate_multiplier: float = 1.0
    text_auxiliary_search_parallelism: int = 8
    query_synonym_path: Path | None = None
    query_synonyms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    low_score_threshold: float = 0.4
    category_suggestion_limit: int = 15
    cache_ttl_seconds: int = 30
    cache_max_entries: int = 10000
    cache_miss_lock_seconds: float = 35.0
    cache_miss_wait_seconds: float = 5.0
    cache_miss_poll_seconds: float = 0.05
    search_rate_limit_per_minute: int = 300
    mall_search_rate_limit_per_minute: int = 1500
    click_rate_limit_per_minute: int = 600
    mall_click_rate_limit_per_minute: int = 3000
    image_rate_limit_per_minute: int = 20
    mall_image_rate_limit_per_minute: int = 120
    rate_limit_max_buckets: int = 10000
    rate_limit_prune_interval_seconds: float = 1.0
    search_max_concurrency: int = 64
    search_queue_timeout_seconds: float = 2.0
    image_search_max_concurrency: int = 8
    image_search_queue_timeout_seconds: float = 2.0
    api_threadpool_tokens: int = DEFAULT_API_THREADPOOL_TOKENS
    api_gzip_minimum_size: int = 1024
    redis_url: str | None = None
    redis_key_prefix: str = "haeorum-ai-search"
    redis_socket_timeout_seconds: float = 0.5
    redis_socket_connect_timeout_seconds: float = 0.5
    redis_failure_backoff_seconds: float = 2.0
    trusted_proxy_ips: tuple[str, ...] = ("127.0.0.1", "::1")
    cors_origins: tuple[str, ...] = ("*",)
    product_csv_path: Path = ROOT / "sample_products.csv"
    mssql_connection_string: str | None = None
    mssql_query: str = (
        "SELECT product_id, product_name, price, price_min, price_max, category_name, "
        "print_methods, materials, colors, min_order_qty, delivery_days, product_group_id, main_image_url, "
        "product_url, status, updated_at, is_deleted, display_yn, mall_id "
        "FROM dbo.v_ai_search_products"
    )
    mssql_product_id_column: str = "product_id"
    mssql_updated_at_column: str = "updated_at"
    mssql_sync_fetch_size: int = 1000
    product_url_template: str = "https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}"
    filter_by_mall_id: bool = False
    mall_config_path: Path | None = None
    malls: dict[str, MallConfig] = field(default_factory=dict)
    search_log_path: Path = ROOT / "logs" / "search.jsonl"
    error_log_path: Path = ROOT / "logs" / "error.jsonl"
    sync_log_path: Path = ROOT / "logs" / "sync.jsonl"
    log_keep_open_seconds: float = 0.0
    sync_interval_seconds: int = MAX_PRODUCTION_SYNC_INTERVAL_SECONDS
    sync_lock_stale_seconds: int = 21600
    validate_product_images: bool = False
    product_image_probe_timeout_seconds: int = 10
    product_image_probe_retry_count: int = 1
    product_image_probe_retry_delay_seconds: float = 0.25
    product_image_download_thread_count: int = 3
    sync_alert_webhook_url: str | None = None
    sync_alert_timeout_seconds: int = 5


def load_settings() -> Settings:
    environment = os.environ.get("HAEORUM_ENV", "development").strip().lower() or "development"
    if environment in PRODUCTION_ENVIRONMENTS:
        validate_production_config_file_environment_variables()
    mall_config_path = optional_path_env("HAEORUM_MALL_CONFIG_PATH")
    query_synonym_path = optional_path_env("HAEORUM_QUERY_SYNONYM_PATH")
    raw_embedding_backend = os.environ.get("HAEORUM_EMBEDDING_BACKEND", Settings.embedding_backend).strip().lower()
    default_embedding_dimensions = (
        DEFAULT_GEMINI_EMBEDDING_DIMENSIONS
        if raw_embedding_backend == "gemini"
        else DEFAULT_QWEN_EMBEDDING_DIMENSIONS
    )
    default_embedding_model = DEFAULT_GEMINI_MODEL if raw_embedding_backend == "gemini" else DEFAULT_QWEN_MODEL
    embedding_url_env_name, embedding_url_value = _first_named_env(
        ("HAEORUM_GEMINI_EMBEDDING_URL", "HAEORUM_QWEN_EMBEDDING_URL")
    )
    embedding_url_field_name = embedding_url_env_name or (
        "HAEORUM_GEMINI_EMBEDDING_URL" if raw_embedding_backend == "gemini" else "HAEORUM_QWEN_EMBEDDING_URL"
    )
    settings = Settings(
        environment=environment,
        engine_backend=os.environ.get("HAEORUM_SEARCH_ENGINE", "marqo").lower(),
        marqo_url=validate_marqo_url_value(os.environ.get("MARQO_URL", "http://localhost:8882")),
        marqo_model=os.environ.get("HAEORUM_MARQO_MODEL", Settings.marqo_model),
        marqo_search_timeout_seconds=_float_env(
            "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS",
            Settings.marqo_search_timeout_seconds,
        ),
        marqo_search_retry_count=_int_env(
            "HAEORUM_MARQO_SEARCH_RETRY_COUNT",
            Settings.marqo_search_retry_count,
        ),
        marqo_search_retry_delay_seconds=_float_env(
            "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS",
            Settings.marqo_search_retry_delay_seconds,
        ),
        backend_retry_after_max_seconds=_float_env(
            "HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS",
            Settings.backend_retry_after_max_seconds,
        ),
        backend_http_max_idle_seconds=_float_env(
            "HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS",
            Settings.backend_http_max_idle_seconds,
        ),
        backend_http_max_active_requests=_int_env(
            "HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS",
            Settings.backend_http_max_active_requests,
        ),
        backend_http_connection_acquire_timeout_seconds=_float_env(
            "HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS",
            Settings.backend_http_connection_acquire_timeout_seconds,
        ),
        backend_circuit_failure_threshold=_int_env(
            "HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD",
            Settings.backend_circuit_failure_threshold,
        ),
        backend_circuit_cooldown_seconds=_float_env(
            "HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS",
            Settings.backend_circuit_cooldown_seconds,
        ),
        backend_circuit_half_open_max_calls=_int_env(
            "HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS",
            Settings.backend_circuit_half_open_max_calls,
        ),
        admin_metrics_health_cache_seconds=_float_env(
            "HAEORUM_ADMIN_METRICS_HEALTH_CACHE_SECONDS",
            Settings.admin_metrics_health_cache_seconds,
        ),
        marqo_add_documents_batch_size=_int_env(
            "HAEORUM_MARQO_ADD_DOCUMENTS_BATCH_SIZE",
            Settings.marqo_add_documents_batch_size,
        ),
        marqo_add_documents_max_request_bytes=_int_env(
            "HAEORUM_MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES",
            Settings.marqo_add_documents_max_request_bytes,
        ),
        marqo_delete_documents_batch_size=_int_env(
            "HAEORUM_MARQO_DELETE_DOCUMENTS_BATCH_SIZE",
            Settings.marqo_delete_documents_batch_size,
        ),
        embedding_backend=raw_embedding_backend,
        qwen_embedding_url=validate_marqo_url_value(
            embedding_url_value if embedding_url_env_name is not None else Settings.qwen_embedding_url,
            embedding_url_field_name,
        ),
        qwen_embedding_dimensions=_int_env_alias(
            ("HAEORUM_GEMINI_EMBEDDING_DIMENSIONS", "HAEORUM_QWEN_EMBEDDING_DIMENSIONS"),
            default_embedding_dimensions,
        ),
        qwen_model=_first_env("HAEORUM_GEMINI_MODEL", "HAEORUM_QWEN_MODEL", default=default_embedding_model),
        qwen_embedding_proxy_api_key=_first_env(
            "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY",
            "HAEORUM_QWEN_EMBEDDING_PROXY_API_KEY",
            "HAEORUM_EMBEDDING_PROXY_API_KEY",
            "GEMINI_PROXY_API_KEY",
            default=None,
        ),
        qwen_query_timeout_seconds=_float_env_alias(
            ("HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS", "HAEORUM_QWEN_QUERY_TIMEOUT_SECONDS"),
            Settings.qwen_query_timeout_seconds,
        ),
        qwen_mixed_query_parallelism=_int_env_alias(
            ("HAEORUM_GEMINI_MIXED_QUERY_PARALLELISM", "HAEORUM_QWEN_MIXED_QUERY_PARALLELISM"),
            Settings.qwen_mixed_query_parallelism,
        ),
        qwen_query_embedding_cache_path=optional_path_env_alias(
            ("HAEORUM_GEMINI_QUERY_EMBEDDING_CACHE", "HAEORUM_QWEN_QUERY_EMBEDDING_CACHE")
        ),
        qwen_query_runtime_text_cache_entries=_int_env_alias(
            (
                "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
                "HAEORUM_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
            ),
            Settings.qwen_query_runtime_text_cache_entries,
        ),
        qwen_query_runtime_image_cache_entries=_int_env_alias(
            (
                "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
                "HAEORUM_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
            ),
            Settings.qwen_query_runtime_image_cache_entries,
        ),
        index_name=os.environ.get("HAEORUM_INDEX_NAME", "haeorum-products"),
        admin_api_key=os.environ.get("HAEORUM_ADMIN_API_KEY", DEFAULT_ADMIN_API_KEY),
        default_limit=_int_env("HAEORUM_DEFAULT_LIMIT", 20),
        max_limit=_int_env("HAEORUM_MAX_LIMIT", 50),
        max_offset=_int_env("HAEORUM_MAX_OFFSET", 200),
        max_image_mb=_int_env("HAEORUM_MAX_IMAGE_MB", 5),
        max_image_dimension=_int_env("HAEORUM_MAX_IMAGE_DIMENSION", 1600),
        min_image_dimension=_int_env("HAEORUM_MIN_IMAGE_DIMENSION", 16),
        image_validation_cache_ttl_seconds=_float_env(
            "HAEORUM_IMAGE_VALIDATION_CACHE_TTL_SECONDS",
            Settings.image_validation_cache_ttl_seconds,
        ),
        image_validation_cache_max_entries=_int_env(
            "HAEORUM_IMAGE_VALIDATION_CACHE_MAX_ENTRIES",
            Settings.image_validation_cache_max_entries,
        ),
        mixed_text_weight=_float_env("HAEORUM_MIXED_TEXT_WEIGHT", 0.4),
        mixed_image_weight=_float_env("HAEORUM_MIXED_IMAGE_WEIGHT", 0.6),
        text_auxiliary_weight=_float_env("HAEORUM_TEXT_AUXILIARY_WEIGHT", Settings.text_auxiliary_weight),
        text_auxiliary_candidate_multiplier=_float_env(
            "HAEORUM_TEXT_AUXILIARY_CANDIDATE_MULTIPLIER",
            Settings.text_auxiliary_candidate_multiplier,
        ),
        text_auxiliary_search_parallelism=_int_env(
            "HAEORUM_TEXT_AUXILIARY_SEARCH_PARALLELISM",
            Settings.text_auxiliary_search_parallelism,
        ),
        query_synonym_path=query_synonym_path,
        query_synonyms=load_query_synonyms(query_synonym_path),
        low_score_threshold=_float_env("HAEORUM_LOW_SCORE_THRESHOLD", 0.4),
        category_suggestion_limit=_int_env("HAEORUM_CATEGORY_SUGGESTION_LIMIT", 15),
        cache_ttl_seconds=_int_env("HAEORUM_CACHE_TTL_SECONDS", 30),
        cache_max_entries=_int_env("HAEORUM_CACHE_MAX_ENTRIES", Settings.cache_max_entries),
        cache_miss_lock_seconds=_float_env(
            "HAEORUM_CACHE_MISS_LOCK_SECONDS",
            Settings.cache_miss_lock_seconds,
        ),
        cache_miss_wait_seconds=_float_env(
            "HAEORUM_CACHE_MISS_WAIT_SECONDS",
            Settings.cache_miss_wait_seconds,
        ),
        cache_miss_poll_seconds=_float_env(
            "HAEORUM_CACHE_MISS_POLL_SECONDS",
            Settings.cache_miss_poll_seconds,
        ),
        search_rate_limit_per_minute=_int_env("HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE", 300),
        mall_search_rate_limit_per_minute=_int_env("HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE", 1500),
        click_rate_limit_per_minute=_int_env("HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE", 600),
        mall_click_rate_limit_per_minute=_int_env("HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE", 3000),
        image_rate_limit_per_minute=_int_env("HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE", 20),
        mall_image_rate_limit_per_minute=_int_env("HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE", 120),
        rate_limit_max_buckets=_int_env("HAEORUM_RATE_LIMIT_MAX_BUCKETS", Settings.rate_limit_max_buckets),
        rate_limit_prune_interval_seconds=_float_env(
            "HAEORUM_RATE_LIMIT_PRUNE_INTERVAL_SECONDS",
            Settings.rate_limit_prune_interval_seconds,
        ),
        search_max_concurrency=_int_env("HAEORUM_SEARCH_MAX_CONCURRENCY", Settings.search_max_concurrency),
        search_queue_timeout_seconds=_float_env(
            "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS",
            Settings.search_queue_timeout_seconds,
        ),
        image_search_max_concurrency=_int_env("HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY", 8),
        image_search_queue_timeout_seconds=_float_env("HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS", 2.0),
        api_threadpool_tokens=_int_env("HAEORUM_API_THREADPOOL_TOKENS", Settings.api_threadpool_tokens),
        api_gzip_minimum_size=_int_env("HAEORUM_API_GZIP_MINIMUM_SIZE", Settings.api_gzip_minimum_size),
        redis_url=optional_redis_url_env("HAEORUM_REDIS_URL"),
        redis_key_prefix=os.environ.get("HAEORUM_REDIS_KEY_PREFIX", "haeorum-ai-search"),
        redis_socket_timeout_seconds=_float_env(
            "HAEORUM_REDIS_SOCKET_TIMEOUT_SECONDS",
            Settings.redis_socket_timeout_seconds,
        ),
        redis_socket_connect_timeout_seconds=_float_env(
            "HAEORUM_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS",
            Settings.redis_socket_connect_timeout_seconds,
        ),
        redis_failure_backoff_seconds=_float_env(
            "HAEORUM_REDIS_FAILURE_BACKOFF_SECONDS",
            Settings.redis_failure_backoff_seconds,
        ),
        trusted_proxy_ips=parse_trusted_proxy_ips(os.environ.get("HAEORUM_TRUSTED_PROXY_IPS")),
        cors_origins=parse_cors_origins(cors_origins_config_value()),
        product_csv_path=Path(os.environ.get("HAEORUM_PRODUCT_CSV", str(ROOT / "sample_products.csv"))),
        mssql_connection_string=(
            os.environ.get("HAEORUM_MSSQL_READONLY_CONNECTION_STRING")
            or os.environ.get("HAEORUM_MSSQL_CONNECTION_STRING")
            or None
        ),
        mssql_query=os.environ.get(
            "HAEORUM_MSSQL_QUERY",
            Settings.mssql_query,
        ),
        mssql_product_id_column=validate_sql_identifier_value(
            os.environ.get("HAEORUM_MSSQL_PRODUCT_ID_COLUMN") or Settings.mssql_product_id_column,
            "HAEORUM_MSSQL_PRODUCT_ID_COLUMN",
        ),
        mssql_updated_at_column=validate_sql_identifier_value(
            os.environ.get("HAEORUM_MSSQL_UPDATED_AT_COLUMN") or Settings.mssql_updated_at_column,
            "HAEORUM_MSSQL_UPDATED_AT_COLUMN",
        ),
        mssql_sync_fetch_size=_int_env("HAEORUM_MSSQL_SYNC_FETCH_SIZE", Settings.mssql_sync_fetch_size),
        product_url_template=validate_product_url_template_value(
            os.environ.get("HAEORUM_PRODUCT_URL_TEMPLATE") or Settings.product_url_template,
            mall_id="www",
        ),
        filter_by_mall_id=_bool_env("HAEORUM_FILTER_BY_MALL_ID", False),
        mall_config_path=mall_config_path,
        malls=load_mall_configs(mall_config_path),
        search_log_path=Path(os.environ.get("HAEORUM_SEARCH_LOG_PATH", str(ROOT / "logs" / "search.jsonl"))),
        error_log_path=Path(os.environ.get("HAEORUM_ERROR_LOG_PATH", str(ROOT / "logs" / "error.jsonl"))),
        sync_log_path=Path(os.environ.get("HAEORUM_SYNC_LOG_PATH", str(ROOT / "logs" / "sync.jsonl"))),
        log_keep_open_seconds=_float_env("HAEORUM_LOG_KEEP_OPEN_SECONDS", Settings.log_keep_open_seconds),
        sync_interval_seconds=_int_env("HAEORUM_SYNC_INTERVAL_SECONDS", MAX_PRODUCTION_SYNC_INTERVAL_SECONDS),
        sync_lock_stale_seconds=_int_env("HAEORUM_SYNC_LOCK_STALE_SECONDS", 21600),
        validate_product_images=_bool_env("HAEORUM_VALIDATE_PRODUCT_IMAGES", False),
        product_image_probe_timeout_seconds=_int_env("HAEORUM_PRODUCT_IMAGE_PROBE_TIMEOUT_SECONDS", 10),
        product_image_probe_retry_count=_int_env("HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT", 1),
        product_image_probe_retry_delay_seconds=_float_env("HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_DELAY_SECONDS", 0.25),
        product_image_download_thread_count=_int_env("HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT", 3),
        sync_alert_webhook_url=os.environ.get("HAEORUM_SYNC_ALERT_WEBHOOK_URL") or None,
        sync_alert_timeout_seconds=_int_env("HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS", 5),
    )
    if settings.environment in PRODUCTION_ENVIRONMENTS:
        validate_production_environment_variables(settings)
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    if settings.engine_backend not in SUPPORTED_SEARCH_ENGINES:
        supported = ", ".join(sorted(SUPPORTED_SEARCH_ENGINES))
        raise ValueError(f"HAEORUM_SEARCH_ENGINE must be one of: {supported}")
    if settings.embedding_backend not in {"native", "qwen", "gemini"}:
        raise ValueError("HAEORUM_EMBEDDING_BACKEND must be one of: native, qwen, gemini")
    validate_marqo_url_value(settings.marqo_url)
    validate_marqo_url_value(settings.qwen_embedding_url)
    if settings.embedding_backend in {"qwen", "gemini"}:
        model_field = active_embedding_env_name(settings, "HAEORUM_GEMINI_MODEL", "HAEORUM_QWEN_MODEL")
        dimensions_field = active_embedding_env_name(
            settings,
            "HAEORUM_GEMINI_EMBEDDING_DIMENSIONS",
            "HAEORUM_QWEN_EMBEDDING_DIMENSIONS",
        )
        if not str(settings.qwen_model or "").strip():
            raise ValueError(f"{model_field} is required when HAEORUM_EMBEDDING_BACKEND={settings.embedding_backend}")
        if is_placeholder_config_value(settings.qwen_model):
            raise ValueError(
                f"{model_field} must not be a placeholder when "
                f"HAEORUM_EMBEDDING_BACKEND={settings.embedding_backend}"
            )
        proxy_key = str(settings.qwen_embedding_proxy_api_key or "").strip()
        if proxy_key and is_placeholder_config_value(proxy_key):
            raise ValueError(
                active_embedding_env_name(
                    settings,
                    "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY",
                    "HAEORUM_QWEN_EMBEDDING_PROXY_API_KEY",
                )
                + " must not be a placeholder"
            )
        require_at_least(dimensions_field, settings.qwen_embedding_dimensions, 1)
    validate_numeric_settings(settings)
    validate_readonly_query(settings.mssql_query)
    validate_sql_identifier_value(settings.mssql_product_id_column, "HAEORUM_MSSQL_PRODUCT_ID_COLUMN")
    validate_sql_identifier_value(settings.mssql_updated_at_column, "HAEORUM_MSSQL_UPDATED_AT_COLUMN")
    validate_product_url_template_value(settings.product_url_template, mall_id="www")
    validate_query_synonyms(settings.query_synonyms)
    validate_trusted_proxy_ips(settings.trusted_proxy_ips)
    if settings.redis_url:
        validate_redis_url_value(settings.redis_url)
    if settings.environment in PRODUCTION_ENVIRONMENTS:
        validate_production_settings(settings)


def active_embedding_env_name(settings: Settings, gemini_name: str, qwen_name: str) -> str:
    if os.environ.get(gemini_name) is not None:
        return gemini_name
    if os.environ.get(qwen_name) is not None or settings.embedding_backend == "qwen":
        return qwen_name
    return gemini_name


def validate_numeric_settings(settings: Settings) -> None:
    require_at_least("HAEORUM_DEFAULT_LIMIT", settings.default_limit, 1)
    require_at_least("HAEORUM_MAX_LIMIT", settings.max_limit, 1)
    if settings.max_limit < settings.default_limit:
        raise ValueError("HAEORUM_MAX_LIMIT must be at least HAEORUM_DEFAULT_LIMIT")
    require_at_least("HAEORUM_MAX_OFFSET", settings.max_offset, 0)
    require_at_most("HAEORUM_MAX_OFFSET", settings.max_offset, MAX_OPERATIONAL_SEARCH_OFFSET)
    require_at_least("HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS", settings.marqo_search_timeout_seconds, 0.001)
    require_at_least("HAEORUM_MARQO_SEARCH_RETRY_COUNT", settings.marqo_search_retry_count, 0)
    require_at_least("HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS", settings.marqo_search_retry_delay_seconds, 0.0)
    require_at_least("HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS", settings.backend_retry_after_max_seconds, 0.0)
    require_at_least("HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS", settings.backend_http_max_idle_seconds, 0.0)
    require_at_least("HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS", settings.backend_http_max_active_requests, 0)
    require_at_least(
        "HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS",
        settings.backend_http_connection_acquire_timeout_seconds,
        0.0,
    )
    require_at_least("HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD", settings.backend_circuit_failure_threshold, 0)
    require_at_least("HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS", settings.backend_circuit_cooldown_seconds, 0.0)
    require_at_least("HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS", settings.backend_circuit_half_open_max_calls, 1)
    require_at_least("HAEORUM_ADMIN_METRICS_HEALTH_CACHE_SECONDS", settings.admin_metrics_health_cache_seconds, 0.0)
    require_at_least("HAEORUM_MARQO_ADD_DOCUMENTS_BATCH_SIZE", settings.marqo_add_documents_batch_size, 1)
    require_at_least(
        "HAEORUM_MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES",
        settings.marqo_add_documents_max_request_bytes,
        0,
    )
    require_at_least("HAEORUM_MARQO_DELETE_DOCUMENTS_BATCH_SIZE", settings.marqo_delete_documents_batch_size, 1)
    require_at_least(
        active_embedding_env_name(settings, "HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS", "HAEORUM_QWEN_QUERY_TIMEOUT_SECONDS"),
        settings.qwen_query_timeout_seconds,
        0.001,
    )
    require_at_least(
        active_embedding_env_name(
            settings,
            "HAEORUM_GEMINI_MIXED_QUERY_PARALLELISM",
            "HAEORUM_QWEN_MIXED_QUERY_PARALLELISM",
        ),
        settings.qwen_mixed_query_parallelism,
        0,
    )
    require_at_least(
        active_embedding_env_name(
            settings,
            "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
            "HAEORUM_QWEN_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
        ),
        settings.qwen_query_runtime_text_cache_entries,
        0,
    )
    require_at_least(
        active_embedding_env_name(
            settings,
            "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
            "HAEORUM_QWEN_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
        ),
        settings.qwen_query_runtime_image_cache_entries,
        0,
    )
    require_at_least("HAEORUM_MAX_IMAGE_MB", settings.max_image_mb, 1)
    require_at_least("HAEORUM_MAX_IMAGE_DIMENSION", settings.max_image_dimension, 1)
    require_at_least("HAEORUM_MIN_IMAGE_DIMENSION", settings.min_image_dimension, 1)
    if settings.max_image_dimension < settings.min_image_dimension:
        raise ValueError("HAEORUM_MAX_IMAGE_DIMENSION must be at least HAEORUM_MIN_IMAGE_DIMENSION")
    require_at_least("HAEORUM_IMAGE_VALIDATION_CACHE_TTL_SECONDS", settings.image_validation_cache_ttl_seconds, 0.0)
    require_at_least("HAEORUM_IMAGE_VALIDATION_CACHE_MAX_ENTRIES", settings.image_validation_cache_max_entries, 1)
    require_at_least("HAEORUM_MIXED_TEXT_WEIGHT", settings.mixed_text_weight, 0.0)
    require_at_least("HAEORUM_MIXED_IMAGE_WEIGHT", settings.mixed_image_weight, 0.0)
    if settings.mixed_text_weight + settings.mixed_image_weight <= 0:
        raise ValueError("HAEORUM_MIXED_TEXT_WEIGHT and HAEORUM_MIXED_IMAGE_WEIGHT must not both be zero")
    require_at_least("HAEORUM_TEXT_AUXILIARY_WEIGHT", settings.text_auxiliary_weight, 0.0)
    require_at_least(
        "HAEORUM_TEXT_AUXILIARY_CANDIDATE_MULTIPLIER",
        settings.text_auxiliary_candidate_multiplier,
        0.1,
    )
    require_at_least("HAEORUM_TEXT_AUXILIARY_SEARCH_PARALLELISM", settings.text_auxiliary_search_parallelism, 0)
    require_between("HAEORUM_LOW_SCORE_THRESHOLD", settings.low_score_threshold, 0.0, 1.0)
    require_between("HAEORUM_CATEGORY_SUGGESTION_LIMIT", settings.category_suggestion_limit, 1, 15)
    require_at_least("HAEORUM_CACHE_TTL_SECONDS", settings.cache_ttl_seconds, 0)
    require_at_least("HAEORUM_CACHE_MAX_ENTRIES", settings.cache_max_entries, 1)
    require_at_least("HAEORUM_CACHE_MISS_LOCK_SECONDS", settings.cache_miss_lock_seconds, 0.0)
    require_at_least("HAEORUM_CACHE_MISS_WAIT_SECONDS", settings.cache_miss_wait_seconds, 0.0)
    require_at_least("HAEORUM_CACHE_MISS_POLL_SECONDS", settings.cache_miss_poll_seconds, 0.001)
    require_at_least("HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE", settings.search_rate_limit_per_minute, 0)
    require_at_least("HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE", settings.mall_search_rate_limit_per_minute, 0)
    require_at_least("HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE", settings.click_rate_limit_per_minute, 0)
    require_at_least("HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE", settings.mall_click_rate_limit_per_minute, 0)
    require_at_least("HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE", settings.image_rate_limit_per_minute, 0)
    require_at_least("HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE", settings.mall_image_rate_limit_per_minute, 0)
    require_at_least("HAEORUM_RATE_LIMIT_MAX_BUCKETS", settings.rate_limit_max_buckets, 1)
    require_at_least("HAEORUM_RATE_LIMIT_PRUNE_INTERVAL_SECONDS", settings.rate_limit_prune_interval_seconds, 0.0)
    require_at_least("HAEORUM_SEARCH_MAX_CONCURRENCY", settings.search_max_concurrency, 0)
    require_at_least("HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS", settings.search_queue_timeout_seconds, 0.0)
    require_at_least("HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY", settings.image_search_max_concurrency, 0)
    require_at_least("HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS", settings.image_search_queue_timeout_seconds, 0.0)
    require_at_least("HAEORUM_API_THREADPOOL_TOKENS", settings.api_threadpool_tokens, 1)
    require_at_least("HAEORUM_API_GZIP_MINIMUM_SIZE", settings.api_gzip_minimum_size, 0)
    require_at_least("HAEORUM_REDIS_SOCKET_TIMEOUT_SECONDS", settings.redis_socket_timeout_seconds, 0.001)
    require_at_least(
        "HAEORUM_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS",
        settings.redis_socket_connect_timeout_seconds,
        0.001,
    )
    require_at_least("HAEORUM_REDIS_FAILURE_BACKOFF_SECONDS", settings.redis_failure_backoff_seconds, 0.0)
    require_at_least("HAEORUM_LOG_KEEP_OPEN_SECONDS", settings.log_keep_open_seconds, 0.0)
    require_at_least("HAEORUM_SYNC_INTERVAL_SECONDS", settings.sync_interval_seconds, 1)
    require_at_least("HAEORUM_MSSQL_SYNC_FETCH_SIZE", settings.mssql_sync_fetch_size, 1)
    require_at_least("HAEORUM_SYNC_LOCK_STALE_SECONDS", settings.sync_lock_stale_seconds, 0)
    require_at_least("HAEORUM_PRODUCT_IMAGE_PROBE_TIMEOUT_SECONDS", settings.product_image_probe_timeout_seconds, 1)
    require_at_least("HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT", settings.product_image_probe_retry_count, 0)
    require_at_least(
        "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_DELAY_SECONDS",
        settings.product_image_probe_retry_delay_seconds,
        0.0,
    )
    require_at_least("HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT", settings.product_image_download_thread_count, 1)
    require_at_least("HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS", settings.sync_alert_timeout_seconds, 1)


def require_at_least(name: str, value: int | float, minimum: int | float) -> None:
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}")


def require_at_most(name: str, value: int | float, maximum: int | float) -> None:
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    if value > maximum:
        raise ValueError(f"{name} must be at most {maximum:g}")


def require_between(name: str, value: int | float, minimum: int | float, maximum: int | float) -> None:
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")


def validate_production_environment_variables(settings: Settings) -> None:
    missing_search = [
        name
        for name in [
            "HAEORUM_SEARCH_ENGINE",
            "MARQO_URL",
            "HAEORUM_MARQO_MODEL",
            "HAEORUM_INDEX_NAME",
        ]
        if not explicit_production_env_value(name)
    ]
    if missing_search:
        raise ValueError(
            "Production search settings must be explicitly set in production: " + ", ".join(missing_search)
        )
    readonly_mssql = str(os.environ.get("HAEORUM_MSSQL_READONLY_CONNECTION_STRING", "") or "").strip()
    legacy_mssql = str(os.environ.get("HAEORUM_MSSQL_CONNECTION_STRING", "") or "").strip()
    product_csv = str(os.environ.get("HAEORUM_PRODUCT_CSV", "") or "").strip()
    placeholder_sources = [
        name
        for name, value in [
            ("HAEORUM_MSSQL_READONLY_CONNECTION_STRING", readonly_mssql),
            ("HAEORUM_MSSQL_CONNECTION_STRING", legacy_mssql),
            ("HAEORUM_PRODUCT_CSV", product_csv),
        ]
        if value and is_placeholder_config_value(value)
    ]
    if placeholder_sources:
        raise ValueError(
            "Production data source must be explicitly set in production: " + ", ".join(placeholder_sources)
        )
    readonly_configured = bool(readonly_mssql) and not is_placeholder_config_value(readonly_mssql)
    legacy_configured = bool(legacy_mssql) and not is_placeholder_config_value(legacy_mssql)
    product_csv_configured = bool(product_csv) and not is_placeholder_config_value(product_csv)
    if not readonly_configured and not legacy_configured and not product_csv_configured:
        raise ValueError(
            "Production data source must be explicitly set in production: "
            "HAEORUM_MSSQL_READONLY_CONNECTION_STRING, HAEORUM_MSSQL_CONNECTION_STRING, or HAEORUM_PRODUCT_CSV"
        )
    if not readonly_configured and not legacy_configured and product_csv:
        csv_path = Path(product_csv)
        if csv_path.name == "sample_products.csv":
            raise ValueError("HAEORUM_PRODUCT_CSV must not point to sample_products.csv in production")
        if not csv_path.exists():
            raise ValueError("HAEORUM_PRODUCT_CSV does not exist in production")
    if readonly_configured:
        validate_mssql_connection_string_value(readonly_mssql, "HAEORUM_MSSQL_READONLY_CONNECTION_STRING")
    if legacy_configured:
        validate_mssql_connection_string_value(legacy_mssql, "HAEORUM_MSSQL_CONNECTION_STRING")
    backend_raw = str(os.environ.get("HAEORUM_EMBEDDING_BACKEND", "") or "").strip()
    if not backend_raw or is_placeholder_config_value(backend_raw):
        raise ValueError(
            "HAEORUM_EMBEDDING_BACKEND must be explicitly set to gemini in production, "
            "or qwen for legacy local GPU deployments"
        )
    backend_name = backend_raw.lower()
    if backend_name in {"gemini", "qwen"}:
        if backend_name == "gemini":
            required_groups = [
                ("HAEORUM_GEMINI_EMBEDDING_URL", "HAEORUM_QWEN_EMBEDDING_URL"),
                ("HAEORUM_GEMINI_EMBEDDING_DIMENSIONS", "HAEORUM_QWEN_EMBEDDING_DIMENSIONS"),
                ("HAEORUM_GEMINI_MODEL", "HAEORUM_QWEN_MODEL"),
                ("HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY", "GEMINI_PROXY_API_KEY"),
            ]
            missing = [
                primary
                for primary, fallback in required_groups
                if not explicit_production_env_value(primary) and not explicit_production_env_value(fallback)
            ]
        else:
            missing = [
                name
                for name in [
                    "HAEORUM_QWEN_EMBEDDING_URL",
                    "HAEORUM_QWEN_EMBEDDING_DIMENSIONS",
                    "HAEORUM_QWEN_MODEL",
                ]
                if not explicit_production_env_value(name)
            ]
            if not explicit_production_env_value("HAEORUM_QWEN_EMBEDDING_PROXY_API_KEY") and not explicit_production_env_value(
                "GEMINI_PROXY_API_KEY"
            ):
                missing.append("HAEORUM_QWEN_EMBEDDING_PROXY_API_KEY")
        if missing:
            raise ValueError(
                f"{backend_name.title()} embedding settings must be explicitly set in production: "
                + ", ".join(missing)
            )
    elif settings.embedding_backend in {"gemini", "qwen"}:
        raise ValueError(
            "HAEORUM_EMBEDDING_BACKEND must be explicitly set to gemini in production, "
            "or qwen for legacy local GPU deployments"
        )


def validate_production_config_file_environment_variables() -> None:
    missing = [
        name
        for name in ["HAEORUM_MALL_CONFIG_PATH"]
        if not explicit_production_env_value(name)
    ]
    invalid = [
        name
        for name in PRODUCTION_CONFIG_FILE_ENV_NAMES
        if str(os.environ.get(name, "") or "").strip()
        and not explicit_production_env_value(name)
    ]
    if missing or invalid:
        names = sorted(set(missing + invalid))
        raise ValueError("Production config file settings must be explicitly set in production: " + ", ".join(names))

    sample_paths = [
        name
        for name, sample_filenames in PRODUCTION_SAMPLE_CONFIG_FILENAMES.items()
        if Path(str(os.environ.get(name, "") or "").strip()).name in sample_filenames
    ]
    if sample_paths:
        raise ValueError(
            "Production config file settings must not point to sample files: " + ", ".join(sample_paths)
        )


def validate_production_settings(settings: Settings) -> None:
    if settings.engine_backend not in PRODUCTION_SEARCH_ENGINES:
        supported = ", ".join(sorted(PRODUCTION_SEARCH_ENGINES))
        raise ValueError(f"HAEORUM_SEARCH_ENGINE must be one of: {supported} in production")
    if settings.embedding_backend not in {"gemini", "qwen"}:
        raise ValueError(
            "HAEORUM_EMBEDDING_BACKEND must be gemini in production, or qwen for legacy local GPU deployments"
        )
    if not str(settings.qwen_embedding_proxy_api_key or "").strip():
        key_name = (
            "HAEORUM_QWEN_EMBEDDING_PROXY_API_KEY"
            if settings.embedding_backend == "qwen"
            else "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY"
        )
        raise ValueError(f"{key_name} is required in production")
    if settings.search_max_concurrency <= 0:
        raise ValueError("HAEORUM_SEARCH_MAX_CONCURRENCY must be greater than 0 in production")
    if settings.image_search_max_concurrency <= 0:
        raise ValueError("HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY must be greater than 0 in production")
    required_threadpool_tokens = required_api_threadpool_tokens(
        settings.search_max_concurrency,
        settings.image_search_max_concurrency,
    )
    if settings.api_threadpool_tokens < required_threadpool_tokens:
        raise ValueError(
            f"HAEORUM_API_THREADPOOL_TOKENS must be at least {required_threadpool_tokens} "
            "for configured production search concurrency"
        )
    admin_key = str(settings.admin_api_key or "")
    if admin_key.lower() in PLACEHOLDER_ADMIN_API_KEYS or len(admin_key) < 16:
        raise ValueError("HAEORUM_ADMIN_API_KEY must be changed to a strong value in production")
    if "*" in settings.cors_origins:
        raise ValueError("HAEORUM_CORS_ORIGINS must not contain * in production")
    non_https_cors_origins = [
        origin for origin in settings.cors_origins if origin != "*" and not str(origin).lower().startswith("https://")
    ]
    if non_https_cors_origins:
        raise ValueError(
            "HAEORUM_CORS_ORIGINS must use https origins in production: "
            + ", ".join(sorted(non_https_cors_origins))
        )
    unsafe_cors_origins = [
        origin for origin in settings.cors_origins if origin != "*" and not origin_uses_safe_public_url(origin)
    ]
    if unsafe_cors_origins:
        raise ValueError(
            "HAEORUM_CORS_ORIGINS must use safe public origins in production: "
            + ", ".join(sorted(unsafe_cors_origins))
        )
    if not settings.malls:
        raise ValueError("HAEORUM_MALL_CONFIG_PATH with enabled malls is required in production")
    if not product_url_template_uses_safe_public_url(settings.product_url_template, mall_id="www"):
        raise ValueError("HAEORUM_PRODUCT_URL_TEMPLATE must format to a safe public http(s) URL")
    if not product_url_template_uses_https(settings.product_url_template, mall_id="www"):
        raise ValueError("HAEORUM_PRODUCT_URL_TEMPLATE must use https in production")
    insecure_malls = [
        mall.mall_id
        for mall in settings.malls.values()
        if mall.enabled and (not mall.api_key or not mall.allowed_origins or not mall.product_url_template)
    ]
    if insecure_malls:
        raise ValueError(
            "enabled production malls require api_key, allowed_origins, and product_url_template: "
            + ", ".join(sorted(insecure_malls))
        )
    placeholder_key_malls = [
        mall.mall_id
        for mall in settings.malls.values()
        if mall.enabled and is_placeholder_public_api_key(mall.api_key)
    ]
    if placeholder_key_malls:
        raise ValueError(
            "enabled production malls must not use sample or placeholder api_key: "
            + ", ".join(sorted(placeholder_key_malls))
        )
    unsafe_product_url_malls = [
        mall.mall_id
        for mall in settings.malls.values()
        if mall.enabled and not product_url_template_uses_safe_public_url(mall.product_url_template, mall_id=mall.mall_id)
    ]
    if unsafe_product_url_malls:
        raise ValueError(
            "enabled production malls must use safe public product_url_template: "
            + ", ".join(sorted(unsafe_product_url_malls))
        )
    http_product_url_malls = [
        mall.mall_id
        for mall in settings.malls.values()
        if mall.enabled and not product_url_template_uses_https(mall.product_url_template, mall_id=mall.mall_id)
    ]
    if http_product_url_malls:
        raise ValueError(
            "enabled production malls must use https product_url_template: "
            + ", ".join(sorted(http_product_url_malls))
        )
    wildcard_origin_malls = [
        mall.mall_id for mall in settings.malls.values() if mall.enabled and "*" in mall.allowed_origins
    ]
    if wildcard_origin_malls:
        raise ValueError(
            "enabled production malls must not use wildcard allowed_origins: "
            + ", ".join(sorted(wildcard_origin_malls))
        )
    non_https_origin_malls = {
        mall.mall_id: [origin for origin in mall.allowed_origins if not str(origin).lower().startswith("https://")]
        for mall in settings.malls.values()
        if mall.enabled
    }
    non_https_origin_malls = {mall_id: origins for mall_id, origins in non_https_origin_malls.items() if origins}
    if non_https_origin_malls:
        details = ", ".join(
            f"{mall_id}({', '.join(origins)})" for mall_id, origins in sorted(non_https_origin_malls.items())
        )
        raise ValueError("enabled production mall allowed_origins must use https: " + details)
    unsafe_origin_malls = {
        mall.mall_id: [origin for origin in mall.allowed_origins if not origin_uses_safe_public_url(origin)]
        for mall in settings.malls.values()
        if mall.enabled
    }
    unsafe_origin_malls = {mall_id: origins for mall_id, origins in unsafe_origin_malls.items() if origins}
    if unsafe_origin_malls:
        details = ", ".join(
            f"{mall_id}({', '.join(origins)})" for mall_id, origins in sorted(unsafe_origin_malls.items())
        )
        raise ValueError("enabled production mall allowed_origins must use safe public origins: " + details)
    missing_cors_origins = mall_origins_missing_from_cors(settings)
    if missing_cors_origins:
        details = ", ".join(
            f"{mall_id}({', '.join(origins)})" for mall_id, origins in sorted(missing_cors_origins.items())
        )
        raise ValueError(
            "enabled production mall allowed_origins must be included in HAEORUM_CORS_ORIGINS: " + details
        )
    weak_key_malls = [
        mall.mall_id
        for mall in settings.malls.values()
        if mall.enabled and is_weak_public_api_key(mall.api_key)
    ]
    if weak_key_malls:
        raise ValueError(
            "enabled production malls must use strong random api_key values: "
            + ", ".join(sorted(weak_key_malls))
        )
    if settings.sync_interval_seconds > MAX_PRODUCTION_SYNC_INTERVAL_SECONDS:
        raise ValueError(
            f"HAEORUM_SYNC_INTERVAL_SECONDS must be at most "
            f"{MAX_PRODUCTION_SYNC_INTERVAL_SECONDS} in production"
        )
    webhook_check = check_sync_alert_webhook_url(settings.sync_alert_webhook_url)
    if webhook_check["configured"] and not webhook_check["ok"]:
        raise ValueError(str(webhook_check["message"]))


def explicit_production_env_value(name: str) -> str:
    value = str(os.environ.get(name, "") or "").strip()
    if not value or is_placeholder_config_value(value):
        return ""
    return value


def optional_redis_url_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    return validate_redis_url_value(text, name)


def is_placeholder_config_value(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    if normalized in PLACEHOLDER_CONFIG_VALUES:
        return True
    if normalized.startswith(PLACEHOLDER_CONFIG_PREFIXES):
        return True
    return normalized.startswith("<") and normalized.endswith(">")


def check_sync_alert_webhook_url(webhook_url: str | None) -> dict[str, object]:
    text = str(webhook_url or "").strip()
    if not text:
        return {
            "ok": True,
            "configured": False,
            "scheme": None,
            "host": None,
            "message": "sync alert webhook is not configured",
        }
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        return {
            "ok": False,
            "configured": True,
            "scheme": None,
            "host": None,
            "message": "HAEORUM_SYNC_ALERT_WEBHOOK_URL must not contain whitespace, control characters, or backslashes",
        }
    parsed = urlparse(text)
    try:
        port = parsed.port
    except ValueError:
        return {
            "ok": False,
            "configured": True,
            "scheme": parsed.scheme.lower() or None,
            "host": parsed.hostname,
            "message": "HAEORUM_SYNC_ALERT_WEBHOOK_URL has an invalid port",
        }
    problems: list[str] = []
    if parsed.scheme.lower() != "https":
        problems.append("scheme must be https")
    if not parsed.netloc or not parsed.hostname:
        problems.append("host is required")
    elif is_local_or_link_host(parsed.hostname):
        problems.append("host must not be localhost, loopback, link-local, or unspecified")
    if parsed.username or parsed.password:
        problems.append("credentials must not be embedded in the URL")
    if parsed.fragment:
        problems.append("fragment is not allowed")
    ok = not problems
    return {
        "ok": ok,
        "configured": True,
        "scheme": parsed.scheme.lower() or None,
        "host": parsed.hostname,
        "port": port,
        "has_path": bool(parsed.path and parsed.path != "/"),
        "has_query": bool(parsed.query),
        "message": "sync alert webhook URL is valid"
        if ok
        else "HAEORUM_SYNC_ALERT_WEBHOOK_URL is invalid: " + "; ".join(problems),
    }


def validate_marqo_url_value(value: object, field_name: str = "MARQO_URL") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        raise ValueError(
            f"{field_name} must be an absolute HTTP(S) URL without whitespace, control characters, or backslashes"
        )
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} has an invalid port") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{field_name} must be an absolute HTTP(S) URL without credentials, query strings, or fragments"
        )
    if is_link_or_unspecified_host(parsed.hostname):
        raise ValueError(f"{field_name} must not use link-local or unspecified hosts")
    return text.rstrip("/")


def validate_redis_url_value(value: object, field_name: str = "HAEORUM_REDIS_URL") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if is_placeholder_config_value(text):
        raise ValueError(f"{field_name} must not be a placeholder")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        raise ValueError(
            f"{field_name} must be a Redis URL without whitespace, control characters, or backslashes"
        )
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} has an invalid port") from exc
    if parsed.scheme.lower() not in REDIS_URL_SCHEMES or not parsed.netloc or not parsed.hostname:
        raise ValueError(f"{field_name} must be an absolute redis:// or rediss:// URL")
    if parsed.params or parsed.fragment:
        raise ValueError(f"{field_name} must not include URL params or fragments")
    if is_link_or_unspecified_host(parsed.hostname):
        raise ValueError(f"{field_name} must not use link-local or unspecified hosts")
    if parsed.path and parsed.path != "/":
        db_number = parsed.path.removeprefix("/")
        if "/" in db_number or not db_number.isdigit():
            raise ValueError(f"{field_name} path must be /<database-number>")
    return text.rstrip("/")


def validate_mssql_connection_string_value(
    value: object,
    field_name: str = "HAEORUM_MSSQL_READONLY_CONNECTION_STRING",
) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if is_placeholder_config_value(text):
        raise ValueError(f"{field_name} must not be a placeholder")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"{field_name} must not contain control characters")
    parts = parse_mssql_connection_string_value(text, field_name)
    required_groups = {
        "server": {"server", "address", "addr", "networkaddress", "datasource"},
        "database": {"database", "initialcatalog"},
        "encrypt": {"encrypt"},
        "trustservercertificate": {"trustservercertificate"},
        "applicationintent": {"applicationintent"},
    }
    missing = [
        group_name
        for group_name, aliases in required_groups.items()
        if all(alias not in parts for alias in aliases)
    ]
    if missing:
        raise ValueError(f"{field_name} must include: " + ", ".join(missing))
    encrypt = parts["encrypt"].strip().lower()
    if encrypt not in MSSQL_CONNECTION_ENCRYPT_VALUES:
        raise ValueError(f"{field_name} must set Encrypt=yes, Encrypt=mandatory, or Encrypt=strict")
    trust_server_certificate = parts["trustservercertificate"].strip().lower()
    if trust_server_certificate not in MSSQL_CONNECTION_TRUST_CERT_FALSE_VALUES:
        raise ValueError(f"{field_name} must set TrustServerCertificate=no")
    application_intent = " ".join(parts["applicationintent"].strip().lower().split())
    if application_intent not in MSSQL_CONNECTION_READONLY_VALUES:
        raise ValueError(f"{field_name} must set ApplicationIntent=ReadOnly")
    return parts


def parse_mssql_connection_string_value(value: str, field_name: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for segment in split_mssql_connection_string(value):
        if not segment:
            continue
        if "=" not in segment:
            raise ValueError(f"{field_name} contains a segment without '='")
        raw_key, raw_value = segment.split("=", 1)
        key = normalize_mssql_connection_key(raw_key)
        parsed_value = raw_value.strip()
        if not key:
            raise ValueError(f"{field_name} contains an empty key")
        if not parsed_value:
            raise ValueError(f"{field_name} contains an empty value for {raw_key.strip()}")
        if key in parts:
            raise ValueError(f"{field_name} contains duplicate key: {raw_key.strip()}")
        parts[key] = parsed_value.strip("{}") if parsed_value.startswith("{") and parsed_value.endswith("}") else parsed_value
    if not parts:
        raise ValueError(f"{field_name} is required")
    return parts


def split_mssql_connection_string(value: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    brace_depth = 0
    for char in value:
        if char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth > 0:
            brace_depth -= 1
        if char == ";" and brace_depth == 0:
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            continue
        current.append(char)
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def normalize_mssql_connection_key(value: object) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def is_placeholder_public_api_key(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    if normalized in PLACEHOLDER_PUBLIC_API_KEY_VALUES:
        return True
    if normalized.startswith(PLACEHOLDER_PUBLIC_API_KEY_PREFIXES):
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    return "dev-key" in normalized or normalized.endswith("-dev")


def public_api_key_strength_problems(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text or is_placeholder_public_api_key(text):
        return []
    problems: list[str] = []
    if len(text) < PUBLIC_API_KEY_MIN_LENGTH:
        problems.append(f"length<{PUBLIC_API_KEY_MIN_LENGTH}")
    alnum = re.sub(r"[^A-Za-z0-9]", "", text).lower()
    if len(set(alnum)) < PUBLIC_API_KEY_MIN_DISTINCT_CHARS:
        problems.append(f"distinct_chars<{PUBLIC_API_KEY_MIN_DISTINCT_CHARS}")
    return problems


def is_weak_public_api_key(value: str | None) -> bool:
    return bool(public_api_key_strength_problems(value))


def mall_origins_missing_from_cors(settings: Settings) -> dict[str, list[str]]:
    cors_origins = set(settings.cors_origins)
    if "*" in cors_origins:
        return {}
    missing: dict[str, list[str]] = {}
    for mall in settings.malls.values():
        if not mall.enabled:
            continue
        missing_origins = sorted(
            origin for origin in mall.allowed_origins if origin != "*" and origin not in cors_origins
        )
        if missing_origins:
            missing[mall.mall_id] = missing_origins
    return missing


def optional_path_env(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value)


def optional_path_env_alias(names: tuple[str, ...]) -> Path | None:
    _name, value = _first_named_env(names)
    if not value:
        return None
    return Path(value)


def load_mall_configs(path: Path | None) -> dict[str, MallConfig]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_malls = data.get("malls", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    if not isinstance(raw_malls, list):
        raise ValueError("mall config must be a list or an object with a malls list")
    malls: dict[str, MallConfig] = {}
    for raw in raw_malls:
        if not isinstance(raw, dict):
            raise ValueError("mall config entries must be objects")
        try:
            mall_id = normalize_mall_id(raw.get("mall_id") or raw.get("mallId"), required=True)
        except ValueError as exc:
            message = str(exc).replace("mall_id is required", "mall config requires mall_id")
            raise ValueError(message) from exc
        if mall_id in malls:
            raise ValueError(f"duplicate mall_id: {mall_id}")
        malls[mall_id] = MallConfig(
            mall_id=mall_id,
            api_key=raw.get("api_key") or raw.get("apiKey"),
            product_url_template=validate_product_url_template_value(
                raw.get("product_url_template") or raw.get("productUrlTemplate"),
                mall_id=mall_id,
            ),
            enabled=parse_bool_value(alias_value(raw, "enabled", "enabled"), "enabled", default=True),
            allowed_origins=parse_allowed_origins(alias_value(raw, "allowed_origins", "allowedOrigins")),
            excluded_product_ids=tuple(
                parse_string_list(alias_value(raw, "excluded_product_ids", "excludedProductIds"), "excluded_product_ids")
            ),
            excluded_categories=tuple(
                parse_string_list(alias_value(raw, "excluded_categories", "excludedCategories"), "excluded_categories")
            ),
            hide_prices=parse_bool_value(alias_value(raw, "hide_prices", "hidePrices"), "hide_prices", default=False),
            price_multiplier=parse_float_value(
                alias_value(raw, "price_multiplier", "priceMultiplier"),
                "price_multiplier",
                default=1.0,
                minimum=0.0,
                allow_equal_minimum=False,
            ),
            price_adjustment=parse_float_value(
                alias_value(raw, "price_adjustment", "priceAdjustment"),
                "price_adjustment",
                default=0.0,
            ),
            price_round_to=parse_int_value(
                alias_value(raw, "price_round_to", "priceRoundTo"),
                "price_round_to",
                default=1,
                minimum=1,
            ),
        )
    return malls


def alias_value(raw: dict[str, object], primary: str, alias: str) -> object:
    if primary in raw:
        return raw[primary]
    return raw.get(alias)


def parse_string_list(value: object, field_name: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.replace("|", ",").replace(";", ",").split(",") if item.strip()]
    raise ValueError(f"{field_name} must be a list or delimited string")


def load_query_synonyms(path: Path | None) -> dict[str, tuple[str, ...]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("query synonym config must be a JSON object")
    raw_synonyms = data.get("synonyms", data)
    if not isinstance(raw_synonyms, dict):
        raise ValueError("query synonym config must be an object or contain a synonyms object")

    groups: list[list[str]] = []
    for raw_key, raw_values in raw_synonyms.items():
        key = normalize_query_synonym_term(raw_key)
        values = [normalize_query_synonym_term(value) for value in parse_string_list(raw_values, "query_synonyms")]
        group = [term for term in [key, *values] if term]
        if len(group) > 1:
            groups.append(group)

    synonyms: dict[str, list[str]] = {}
    for group in groups:
        for term in group:
            related = synonyms.setdefault(term, [])
            for candidate in group:
                if candidate != term and candidate not in related:
                    related.append(candidate)
    return {term: tuple(values) for term, values in sorted(synonyms.items())}


def normalize_query_synonym_term(value: object) -> str:
    return " ".join(str(value or "").strip().lower().replace("/", " ").replace("-", " ").split())


def validate_query_synonyms(synonyms: dict[str, tuple[str, ...]]) -> None:
    for term, values in synonyms.items():
        if not normalize_query_synonym_term(term):
            raise ValueError("query synonym terms must not be blank")
        if not isinstance(values, tuple):
            raise ValueError("query synonym values must be tuples")
        if not values:
            raise ValueError(f"query synonym {term} must have at least one related term")


def parse_trusted_proxy_ips(value: str | None) -> tuple[str, ...]:
    if value in (None, ""):
        return Settings.trusted_proxy_ips
    proxies = tuple(item.strip() for item in value.replace(";", ",").split(",") if item.strip())
    validate_trusted_proxy_ips(proxies)
    return proxies


def validate_trusted_proxy_ips(values: tuple[str, ...]) -> None:
    for value in values:
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError("HAEORUM_TRUSTED_PROXY_IPS must contain IP addresses or CIDR ranges") from exc


def parse_allowed_origins(value: object) -> tuple[str, ...]:
    origins = parse_string_list(value, "allowed_origins")
    return normalize_origin_values(origins, allow_wildcard=True, field_name="allowed_origins")


def parse_cors_origins(value: object) -> tuple[str, ...]:
    origins = parse_string_list("*" if value in (None, "") else value, "HAEORUM_CORS_ORIGINS")
    return normalize_origin_values(origins, allow_wildcard=True, field_name="HAEORUM_CORS_ORIGINS")


def cors_origins_config_value() -> str:
    direct = os.environ.get("HAEORUM_CORS_ORIGINS")
    if direct not in (None, ""):
        return direct
    path = os.environ.get("HAEORUM_CORS_ORIGINS_FILE")
    if path:
        return read_string_list_file(Path(path), "HAEORUM_CORS_ORIGINS_FILE")
    return "*"


def read_string_list_file(path: Path, field_name: str) -> str:
    values = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            raise ValueError(f"{field_name} line {line_number} must contain one value, not KEY=VALUE")
        values.extend(item.strip() for item in line.split(",") if item.strip())
    if not values:
        raise ValueError(f"{field_name} must contain at least one value")
    return ",".join(values)


def normalize_origin_values(
    values: list[str],
    allow_wildcard: bool,
    field_name: str,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        origin = normalize_origin_value(value, allow_wildcard=allow_wildcard, field_name=field_name)
        if origin == "*" and len(values) > 1:
            raise ValueError(f"{field_name} wildcard must not be combined with explicit origins")
        if origin not in seen:
            seen.add(origin)
            normalized.append(origin)
    return tuple(normalized)


def normalize_origin_value(value: str, allow_wildcard: bool = False, field_name: str = "allowed_origins") -> str:
    normalized = str(value or "").strip()
    if allow_wildcard and normalized == "*":
        return "*"
    parsed = urlparse(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} values must be origins such as {ORIGIN_EXAMPLE}") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(f"{field_name} values must be origins such as {ORIGIN_EXAMPLE}")
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def origin_uses_safe_public_url(value: object, allow_wildcard: bool = False, field_name: str = "origin") -> bool:
    try:
        origin = normalize_origin_value(str(value or ""), allow_wildcard=allow_wildcard, field_name=field_name)
    except ValueError:
        return False
    if origin == "*":
        return False
    return safe_absolute_http_url(origin) is not None


def validate_sql_identifier_value(value: object, field_name: str) -> str:
    identifier = str(value or "").strip()
    if not SQL_IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(f"{field_name} must be a simple SQL column identifier")
    return identifier


def validate_product_url_template_value(
    value: object,
    mall_id: str,
    field_name: str = "product_url_template",
) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    template = value.strip()
    if not template:
        return None
    if REQUIRED_PRODUCT_URL_TEMPLATE_FIELD not in template:
        raise ValueError(f"{field_name} must contain {REQUIRED_PRODUCT_URL_TEMPLATE_FIELD}")
    template_mall_id = normalize_mall_id(mall_id, required=False) or "shop001"
    try:
        formatted = template.format(product_id="P001", mall_id=template_mall_id)
    except Exception as exc:
        raise ValueError(f"{field_name} is not formattable: {exc}") from exc
    parsed = urlparse(formatted)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} must format to an http(s) URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{field_name} must format to an http(s) URL")
    if safe_absolute_http_url(formatted) is None:
        raise ValueError(f"{field_name} must format to a safe public http(s) URL")
    return template


def product_url_template_uses_safe_public_url(value: object, mall_id: str) -> bool:
    try:
        template = validate_product_url_template_value(value, mall_id=mall_id)
    except ValueError:
        return False
    if not template:
        return False
    template_mall_id = normalize_mall_id(mall_id, required=False) or "shop001"
    try:
        formatted = template.format(product_id="P001", mall_id=template_mall_id)
    except Exception:
        return False
    return safe_absolute_http_url(formatted) is not None


def product_url_template_uses_https(value: object, mall_id: str) -> bool:
    template = validate_product_url_template_value(value, mall_id=mall_id)
    if not template:
        return False
    template_mall_id = normalize_mall_id(mall_id, required=False) or "shop001"
    try:
        formatted = template.format(product_id="P001", mall_id=template_mall_id)
    except Exception:
        return False
    return urlparse(formatted).scheme.lower() == "https"


def parse_bool_value(value: object, field_name: str, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in BOOLEAN_TRUE_VALUES:
            return True
        if normalized in BOOLEAN_FALSE_VALUES:
            return False
    raise ValueError(f"{field_name} must be a boolean")


def parse_float_value(
    value: object,
    field_name: str,
    default: float,
    minimum: float | None = None,
    allow_equal_minimum: bool = True,
) -> float:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite")
    if minimum is not None:
        if allow_equal_minimum and parsed < minimum:
            raise ValueError(f"{field_name} must be at least {minimum:g}")
        if not allow_equal_minimum and parsed <= minimum:
            raise ValueError(f"{field_name} must be greater than {minimum:g}")
    return parsed


def parse_int_value(value: object, field_name: str, default: int, minimum: int | None = None) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")
    return parsed
