from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import commerce_ai_search.main as app_module
from commerce_ai_search.cache import MemorySearchCache
from commerce_ai_search.concurrency import ImageSearchGate, SearchExecutionGate
from commerce_ai_search.config import MallConfig, Settings
from commerce_ai_search.engine import LocalSearchEngine
from commerce_ai_search.models import ProductDocument, SyncStatus
from commerce_ai_search.rate_limit import RateLimitBucketStore
from commerce_ai_search.search_service import AISearchService, SearchLogger


class FakeSyncService:
    def __init__(self, logger: SearchLogger):
        self.logger = logger

    def current_status(self) -> SyncStatus:
        return SyncStatus(engine="local", index="local-products")


@pytest.fixture()
def configured_app(monkeypatch, tmp_path):
    settings = Settings(
        engine_backend="local",
        search_rate_limit_per_minute=1,
        mall_search_rate_limit_per_minute=100,
        image_rate_limit_per_minute=100,
        mall_image_rate_limit_per_minute=100,
        click_rate_limit_per_minute=100,
        mall_click_rate_limit_per_minute=100,
        rate_limit_max_buckets=16,
        cache_ttl_seconds=30,
        low_score_threshold=0.05,
        max_image_mb=1,
        search_max_concurrency=8,
        image_search_max_concurrency=2,
        api_threadpool_tokens=18,
        search_log_path=tmp_path / "search.jsonl",
        error_log_path=tmp_path / "error.jsonl",
        sync_log_path=tmp_path / "sync.jsonl",
        product_url_template="https://{mall_id}.example.com/product/{product_id}",
    )
    engine = LocalSearchEngine(
        [
            ProductDocument.model_validate(
                {
                    "product_id": "TB001",
                    "product_name": "스텐 텀블러",
                    "category_name": "텀블러",
                    "main_image_url": "https://cdn.example.com/TB001.jpg",
                    "status": "active",
                    "display_yn": "Y",
                    "keywords": ["보온병", "판촉"],
                }
            )
        ]
    )
    logger = SearchLogger(settings.search_log_path)
    error_logger = SearchLogger(settings.error_log_path)
    cache = MemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries)
    service = AISearchService(engine, settings, logger=logger, cache=cache)
    sync_logger = SearchLogger(settings.sync_log_path)
    sync_service = FakeSyncService(sync_logger)
    monkeypatch.setattr(app_module, "settings", settings)
    monkeypatch.setattr(app_module, "engine", engine)
    monkeypatch.setattr(app_module, "search_service", service)
    monkeypatch.setattr(app_module, "sync_service", sync_service)
    monkeypatch.setattr(app_module, "shared_rate_limiter", None)
    monkeypatch.setattr(app_module, "rate_limit_bucket_store", RateLimitBucketStore(settings.rate_limit_max_buckets))
    monkeypatch.setattr(app_module, "search_execution_gate", SearchExecutionGate(8, 0.1))
    monkeypatch.setattr(app_module, "image_search_gate", ImageSearchGate(2, 0.1))
    monkeypatch.setattr(
        app_module,
        "api_threadpool_status",
        {"ok": True, "configured": True, "requested_tokens": 18, "previous_tokens": 40, "total_tokens": 18},
    )
    monkeypatch.setattr(app_module, "error_logger", error_logger)
    client = TestClient(app_module.app, raise_server_exceptions=False)
    try:
        yield client, settings
    finally:
        logger.close()
        error_logger.close()
        sync_logger.close()


@pytest.fixture()
def restricted_mall_app(monkeypatch, tmp_path):
    settings = Settings(
        engine_backend="local",
        search_rate_limit_per_minute=100,
        mall_search_rate_limit_per_minute=100,
        image_rate_limit_per_minute=100,
        mall_image_rate_limit_per_minute=100,
        click_rate_limit_per_minute=100,
        mall_click_rate_limit_per_minute=100,
        cache_ttl_seconds=30,
        low_score_threshold=0.05,
        max_image_mb=1,
        search_max_concurrency=8,
        image_search_max_concurrency=2,
        api_threadpool_tokens=18,
        search_log_path=tmp_path / "restricted-search.jsonl",
        error_log_path=tmp_path / "restricted-error.jsonl",
        sync_log_path=tmp_path / "restricted-sync.jsonl",
        product_url_template="https://{mall_id}.example.com/product/{product_id}",
        malls={
            "shop001": MallConfig(
                mall_id="shop001",
                api_key="shop001-secret",
                allowed_origins=("https://shop.example.com",),
                product_url_template="https://shop001.example.com/product/{product_id}",
            )
        },
    )
    engine = LocalSearchEngine(
        [
            ProductDocument.model_validate(
                {
                    "product_id": "TB001",
                    "product_name": "스텐 텀블러",
                    "category_name": "텀블러",
                    "main_image_url": "https://cdn.example.com/TB001.jpg",
                    "product_url": "https://www.jclgift.com/product_w/product_view.asp?p_idx=TB001",
                    "mall_id": "shop001",
                    "status": "active",
                    "display_yn": "Y",
                    "keywords": ["보온병", "판촉"],
                }
            )
        ]
    )
    logger = SearchLogger(settings.search_log_path)
    error_logger = SearchLogger(settings.error_log_path)
    cache = MemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries)
    service = AISearchService(engine, settings, logger=logger, cache=cache)
    sync_logger = SearchLogger(settings.sync_log_path)
    sync_service = FakeSyncService(sync_logger)
    monkeypatch.setattr(app_module, "settings", settings)
    monkeypatch.setattr(app_module, "engine", engine)
    monkeypatch.setattr(app_module, "search_service", service)
    monkeypatch.setattr(app_module, "sync_service", sync_service)
    monkeypatch.setattr(app_module, "shared_rate_limiter", None)
    monkeypatch.setattr(app_module, "rate_limit_bucket_store", RateLimitBucketStore(settings.rate_limit_max_buckets))
    monkeypatch.setattr(app_module, "search_execution_gate", SearchExecutionGate(8, 0.1))
    monkeypatch.setattr(app_module, "image_search_gate", ImageSearchGate(2, 0.1))
    monkeypatch.setattr(
        app_module,
        "api_threadpool_status",
        {"ok": True, "configured": True, "requested_tokens": 18, "previous_tokens": 40, "total_tokens": 18},
    )
    monkeypatch.setattr(app_module, "error_logger", error_logger)
    client = TestClient(app_module.app, raise_server_exceptions=False)
    try:
        yield client, settings
    finally:
        logger.close()
        error_logger.close()
        sync_logger.close()


def test_health_uses_configured_local_engine(configured_app):
    client, _settings = configured_app

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["backend"] == "local"
    assert data["ready"] is True
    assert data["products"] == 1


def test_ai_search_returns_results_and_logs_search(configured_app):
    client, settings = configured_app

    response = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "스텐텀블러", "limit": 5})

    assert response.status_code == 200
    data = response.json()
    assert data["top"][0]["product_id"] == "TB001"
    assert data["top"][0]["product_url"] == "https://shop001.example.com/product/TB001"
    assert data["meta"]["query_type"] == "text"
    assert data["suggested_categories"][:1] == ["텀블러"]
    assert Path(settings.search_log_path).exists()


def test_ai_search_rejects_api_key_in_query_or_body(configured_app):
    client, _settings = configured_app

    query_response = client.post("/api/ai-search?api_key=secret", json={"mall_id": "shop001", "q": "텀블러"})
    body_response = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "텀블러", "api_key": "secret"})

    assert query_response.status_code == 400
    assert query_response.json()["detail"] == "api_key query parameter is not supported; use X-API-Key header"
    assert body_response.status_code == 400
    assert body_response.json()["detail"] == "API key request body field is not supported; use X-API-Key header"


def test_ai_search_rejects_weight_sum_overflow(configured_app):
    client, _settings = configured_app

    response = client.post(
        "/api/ai-search",
        json={"q": "스텐텀블러", "text_weight": 1e308, "image_weight": 1e308},
    )

    assert response.status_code == 400
    assert "text_weight and image_weight sum must be finite" in response.json()["detail"]


def test_restricted_mall_search_requires_valid_public_api_key(restricted_mall_app):
    client, _settings = restricted_mall_app

    missing_key = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "텀블러"})
    wrong_key = client.post(
        "/api/ai-search",
        json={"mall_id": "shop001", "q": "텀블러"},
        headers={"X-API-Key": "wrong"},
    )

    assert missing_key.status_code == 401
    assert missing_key.json()["detail"] == "invalid API key"
    assert wrong_key.status_code == 401
    assert wrong_key.json()["detail"] == "invalid API key"


def test_restricted_mall_search_requires_allowed_origin_and_mall_id(restricted_mall_app):
    client, _settings = restricted_mall_app
    headers = {"X-API-Key": "shop001-secret"}

    missing_origin = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "텀블러"}, headers=headers)
    wrong_origin = client.post(
        "/api/ai-search",
        json={"mall_id": "shop001", "q": "텀블러"},
        headers={**headers, "Origin": "https://evil.example.com"},
    )
    missing_mall = client.post(
        "/api/ai-search",
        json={"q": "텀블러"},
        headers={**headers, "Origin": "https://shop.example.com"},
    )
    wrong_mall = client.post(
        "/api/ai-search",
        json={"mall_id": "shop002", "q": "텀블러"},
        headers={**headers, "Origin": "https://shop.example.com"},
    )

    assert missing_origin.status_code == 403
    assert missing_origin.json()["detail"] == "origin is required"
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["detail"] == "origin is not allowed"
    assert missing_mall.status_code == 403
    assert missing_mall.json()["detail"] == "mall_id is required"
    assert wrong_mall.status_code == 403
    assert wrong_mall.json()["detail"] == "mall_id is not allowed"


def test_restricted_mall_search_accepts_valid_key_origin_and_mall(restricted_mall_app):
    client, _settings = restricted_mall_app

    response = client.post(
        "/api/ai-search",
        json={"mall_id": "shop001", "q": "텀블러"},
        headers={"X-API-Key": "shop001-secret", "Origin": "https://shop.example.com"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["top"][0]["product_id"] == "TB001"
    assert data["top"][0]["product_url"] == "https://shop001.example.com/product/TB001"


def test_restricted_mall_click_log_requires_valid_key_origin_and_mall(restricted_mall_app):
    client, _settings = restricted_mall_app
    payload = {
        "mall_id": "shop001",
        "product_id": "TB001",
        "product_url": "https://shop001.example.com/product/TB001",
        "position": 1,
    }

    missing_key = client.post("/api/click-log", json=payload)
    missing_origin = client.post("/api/click-log", json=payload, headers={"X-API-Key": "shop001-secret"})
    wrong_origin = client.post(
        "/api/click-log",
        json=payload,
        headers={"X-API-Key": "shop001-secret", "Origin": "https://evil.example.com"},
    )
    wrong_mall = client.post(
        "/api/click-log",
        json={**payload, "mall_id": "shop002", "product_url": "https://shop002.example.com/product/TB001"},
        headers={"X-API-Key": "shop001-secret", "Origin": "https://shop.example.com"},
    )
    allowed = client.post(
        "/api/click-log",
        json=payload,
        headers={"X-API-Key": "shop001-secret", "Origin": "https://shop.example.com"},
    )

    assert missing_key.status_code == 401
    assert missing_key.json()["detail"] == "invalid API key"
    assert missing_origin.status_code == 403
    assert missing_origin.json()["detail"] == "origin is required"
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["detail"] == "origin is not allowed"
    assert wrong_mall.status_code == 403
    assert wrong_mall.json()["detail"] == "mall_id is not allowed"
    assert allowed.status_code == 200
    assert allowed.json() == {"ok": True}


def test_search_rate_limit_returns_429_after_configured_client_limit(configured_app):
    client, _settings = configured_app

    first = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "텀블러"})
    second = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "우산"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"] == "search rate limit exceeded for client"


def test_search_rate_limit_uses_forwarded_ip_from_trusted_proxy(configured_app, monkeypatch):
    client, settings = configured_app
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(settings, trusted_proxy_ips=("testclient", "127.0.0.1")),
    )

    first = client.post(
        "/api/ai-search",
        json={"mall_id": "shop001", "q": "텀블러"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    same_forwarded_ip = client.post(
        "/api/ai-search",
        json={"mall_id": "shop001", "q": "우산"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    different_forwarded_ip = client.post(
        "/api/ai-search",
        json={"mall_id": "shop001", "q": "우산"},
        headers={"X-Forwarded-For": "203.0.113.11"},
    )

    assert first.status_code == 200
    assert same_forwarded_ip.status_code == 429
    assert same_forwarded_ip.json()["detail"] == "search rate limit exceeded for client"
    assert different_forwarded_ip.status_code == 200


def test_ai_search_internal_error_returns_500_and_logs_sanitized_detail(configured_app, monkeypatch):
    client, _settings = configured_app

    def fail_search(*_args, **_kwargs):
        raise RuntimeError("sensitive backend failure")

    monkeypatch.setattr(app_module.search_service, "search", fail_search)

    response = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "텀블러"})

    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    entries = app_module.error_logger.tail(10)
    assert entries[-1]["status_code"] == 500
    assert entries[-1]["detail"] == "internal server error"
    assert entries[-1]["error_type"] == "RuntimeError"


def test_ai_search_oversized_json_body_returns_413(configured_app):
    client, _settings = configured_app
    oversized = b"{" + (b" " * (2_500_000))

    response = client.post("/api/ai-search", content=oversized, headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert response.json()["detail"].startswith("JSON body exceeds")


def test_ai_search_oversized_multipart_body_returns_413(configured_app):
    client, _settings = configured_app

    response = client.post(
        "/api/ai-search",
        data={"mall_id": "shop001"},
        files={"image": ("bad.png", b"not-an-image", "image/png")},
        headers={"content-length": "3000000"},
    )

    assert response.status_code == 413
    assert response.json()["detail"].startswith("multipart body exceeds")


def test_ai_search_multipart_rejects_unsupported_image_bytes(configured_app):
    client, _settings = configured_app

    response = client.post(
        "/api/ai-search",
        data={"mall_id": "shop001"},
        files={"image": ("bad.png", b"not-an-image", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "only JPG, PNG, and WEBP images are supported"


def test_click_log_rejects_product_url_outside_mall_template(configured_app):
    client, _settings = configured_app

    response = client.post(
        "/api/click-log",
        json={
            "mall_id": "shop001",
            "product_id": "TB001",
            "product_url": "https://evil.example.com/product/TB001",
            "position": 1,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "product_url is not allowed for mall"


def test_admin_routes_require_admin_key(configured_app):
    client, _settings = configured_app

    response = client.get("/admin/search-log")

    assert response.status_code == 401
    assert response.json()["detail"] == "admin authentication required"


def test_admin_metrics_exposes_operational_guard_status(configured_app):
    client, settings = configured_app

    response = client.get("/admin/metrics", headers={"X-Admin-Key": settings.admin_api_key})

    assert response.status_code == 200
    data = response.json()
    assert data["engine"]["backend"] == "local"
    assert data["rate_limit"]["backend"] == "memory"
    assert data["rate_limit"]["limits"]["search_per_minute"] == settings.search_rate_limit_per_minute
    assert data["cache"]["backend"] == "memory"
    assert data["singleflight"]["enabled"] is True
    assert data["search_queue"]["max_concurrency"] == 8
    assert data["image_queue"]["max_concurrency"] == 2
    assert data["api_threadpool"]["ok"] is True
    assert data["api_threadpool"]["required_tokens"] == 18


def test_admin_metrics_prometheus_exposes_key_operational_series(configured_app):
    client, settings = configured_app

    response = client.get("/admin/metrics.prom", headers={"X-Admin-Key": settings.admin_api_key})

    assert response.status_code == 200
    assert "haeorum_api_threadpool_required_tokens 18" in response.text
    assert "haeorum_rate_limit_search_per_minute 1" in response.text
    assert "haeorum_search_queue_max_concurrency 8" in response.text
    assert "haeorum_image_search_queue_max_concurrency 2" in response.text
    assert "haeorum_image_validation_error_cache_hits 0" in response.text


def test_admin_prewarm_query_cache_handles_unsupported_engine(configured_app):
    client, settings = configured_app

    response = client.post(
        "/admin/prewarm-query-cache",
        headers={"X-Admin-Key": settings.admin_api_key},
        json={"queries": ["스텐텀블러"], "batch_size": 8},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["supported"] is False
    assert data["engine"] == "local"
    assert data["query_count"] == 1
    assert data["skipped"] == 1


def test_admin_prewarm_query_cache_normalizes_queries_and_calls_engine(configured_app, monkeypatch):
    client, settings = configured_app
    captured = {}

    def fake_prewarm_query_vectors(queries, *, batch_size: int):
        captured["queries"] = queries
        captured["batch_size"] = batch_size
        return {
            "ok": True,
            "supported": True,
            "engine": "fake",
            "requested": len(queries),
            "prepared": len(queries),
            "cached": 0,
            "computed": len(queries),
            "deduplicated": 0,
            "skipped": 0,
            "batch_size": batch_size,
        }

    monkeypatch.setattr(app_module.search_service.engine, "prewarm_text_query_vectors", fake_prewarm_query_vectors)

    response = client.post(
        "/admin/prewarm-query-cache",
        headers={"X-Admin-Key": settings.admin_api_key},
        json={"queries": ["  스텐텀블러  ", "스텐텀블러", "검정우산"], "batch_size": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["supported"] is True
    assert data["computed"] == 2
    assert data["query_count"] == 2
    assert captured["batch_size"] == 2
    assert len(captured["queries"]) == 2
    assert all(query.limit == 1 for query in captured["queries"])
    assert all(query.query_synonyms == settings.query_synonyms for query in captured["queries"])
    assert any(query.q for query in captured["queries"])


def test_widget_js_is_served_from_resources(configured_app):
    client, _settings = configured_app

    response = client.get("/widget.js")

    assert response.status_code == 200
    assert "window.HaeorumAISearch" in response.text
    assert 'role="button" aria-label="상품 이미지 업로드"' in response.text
