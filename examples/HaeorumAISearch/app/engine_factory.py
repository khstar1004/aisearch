from __future__ import annotations

from .config import PRODUCTION_ENVIRONMENTS, PRODUCTION_SEARCH_ENGINES, SUPPORTED_SEARCH_ENGINES, Settings
from .engine import LocalSearchEngine, MarqoSearchEngine, QdrantSearchEngine, SearchEngine, TypesenseSearchEngine
from .sync import CsvProductSource


def create_search_engine(settings: Settings, preload_local_products: bool = True) -> SearchEngine:
    backend = ensure_search_engine_runtime_allowed(settings)
    if backend == "local":
        products = CsvProductSource(settings.product_csv_path).fetch_all() if preload_local_products else []
        return LocalSearchEngine(products)
    if backend == "marqo":
        return MarqoSearchEngine(
            settings.marqo_url,
            settings.index_name,
            settings.marqo_model,
            embedding_backend=settings.embedding_backend,
            qwen_embedding_url=settings.qwen_embedding_url,
            qwen_embedding_dimensions=settings.qwen_embedding_dimensions,
            qwen_model=settings.qwen_model,
            qwen_embedding_proxy_api_key=settings.qwen_embedding_proxy_api_key,
            qwen_query_embedding_cache_path=settings.qwen_query_embedding_cache_path,
            qwen_query_runtime_text_cache_entries=settings.qwen_query_runtime_text_cache_entries,
            qwen_query_runtime_image_cache_entries=settings.qwen_query_runtime_image_cache_entries,
            qwen_mixed_query_parallelism=settings.qwen_mixed_query_parallelism,
            image_download_thread_count=settings.product_image_download_thread_count,
            search_timeout_seconds=settings.marqo_search_timeout_seconds,
            qwen_query_timeout_seconds=settings.qwen_query_timeout_seconds,
            search_retry_count=settings.marqo_search_retry_count,
            search_retry_delay_seconds=settings.marqo_search_retry_delay_seconds,
            backend_retry_after_max_seconds=settings.backend_retry_after_max_seconds,
            backend_http_max_idle_seconds=settings.backend_http_max_idle_seconds,
            backend_http_max_active_requests=settings.backend_http_max_active_requests,
            backend_http_connection_acquire_timeout_seconds=(
                settings.backend_http_connection_acquire_timeout_seconds
            ),
            backend_circuit_failure_threshold=settings.backend_circuit_failure_threshold,
            backend_circuit_cooldown_seconds=settings.backend_circuit_cooldown_seconds,
            backend_circuit_half_open_max_calls=settings.backend_circuit_half_open_max_calls,
            admin_metrics_health_cache_seconds=settings.admin_metrics_health_cache_seconds,
            add_documents_batch_size=settings.marqo_add_documents_batch_size,
            add_documents_max_request_bytes=settings.marqo_add_documents_max_request_bytes,
            delete_documents_batch_size=settings.marqo_delete_documents_batch_size,
            text_auxiliary_weight=settings.text_auxiliary_weight,
            text_auxiliary_candidate_multiplier=settings.text_auxiliary_candidate_multiplier,
            text_auxiliary_search_parallelism=settings.text_auxiliary_search_parallelism,
        )
    if backend == "typesense":
        return TypesenseSearchEngine()
    if backend == "qdrant":
        return QdrantSearchEngine()
    raise ValueError(f"Unsupported HAEORUM_SEARCH_ENGINE: {settings.engine_backend}")


def ensure_search_engine_runtime_allowed(settings: Settings) -> str:
    backend = settings.engine_backend.lower()
    if backend not in SUPPORTED_SEARCH_ENGINES:
        supported = ", ".join(sorted(SUPPORTED_SEARCH_ENGINES))
        raise ValueError(f"HAEORUM_SEARCH_ENGINE must be one of: {supported}")
    if settings.environment in PRODUCTION_ENVIRONMENTS and backend not in PRODUCTION_SEARCH_ENGINES:
        supported = ", ".join(sorted(PRODUCTION_SEARCH_ENGINES))
        raise ValueError(f"HAEORUM_SEARCH_ENGINE must be one of: {supported} in production runtime")
    return backend
