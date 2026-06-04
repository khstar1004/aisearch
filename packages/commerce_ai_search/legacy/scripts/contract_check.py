from __future__ import annotations

import json
import sys
import time
import csv
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.identifiers import product_identity_key, product_identity_label
from app.models import ClickLogRequest, SearchRequest, SearchResponse
from app.openapi_contract import load_public_openapi_schema


CONTRACT_DIR = ROOT / "contracts"
REQUEST_FILES = [
    "text_request.json",
    "site_id_request.json",
    "image_request.json",
    "mixed_request.json",
]
REQUIRED_ITEM_FIELDS = {
    "product_id",
    "name",
    "category",
    "price",
    "image_url",
    "product_url",
    "score",
    "score_percent",
}
REQUIRED_OPENAPI_PATHS = {
    "/health": "get",
    "/api/ai-search": "post",
    "/api/click-log": "post",
    "/admin/sync": "post",
    "/admin/reindex": "post",
    "/admin/reindex/{product_id}": "post",
    "/admin/reindex-product": "post",
    "/admin/product/{product_id}": "delete",
    "/admin/delete-product": "post",
    "/admin/sync-status": "get",
    "/admin/search-log": "get",
    "/admin/sync-log": "get",
    "/admin/search-insights": "get",
    "/admin/error-log": "get",
    "/admin/metrics": "get",
    "/admin/metrics.prom": "get",
    "/widget.js": "get",
}
REQUIRED_OPENAPI_SCHEMAS = {
    "SearchRequest",
    "SearchResponse",
    "SearchResultItem",
    "SearchMeta",
    "ClickLogRequest",
    "AdminProductRequest",
    "SyncStatus",
    "SyncResult",
    "HealthResponse",
    "AdminMetrics",
    "SearchInsightsReport",
    "QueryInsight",
    "MixedWeightInsight",
    "ClickedProductInsight",
    "SynonymSeedCandidate",
    "QualityCaseCandidate",
    "MixedWeightRecommendation",
    "SearchRecommendation",
    "ErrorResponse",
}
REQUIRED_DEPLOYMENT_FILES = [
    "Dockerfile",
    "compose-haeorum-demo.yaml",
    "compose-haeorum-marqo.yaml",
    "compose-haeorum-gemini.yaml",
    "compose-haeorum-existing-8gb.yaml",
    "deploy/systemd/haeorum-ai-search.service",
    "deploy/systemd/haeorum-ai-sync.service",
    "deploy/systemd/haeorum-ai-reindex.service",
    "deploy/systemd/haeorum-ai-reindex.timer",
    "deploy/logrotate/haeorum-ai-search",
    "deploy/nginx/haeorum-ai-search.conf",
    "deploy/haeorum-ai-search.env.example",
    "deploy/server-db-intake.md",
    "deploy/server-db-request.ko.md",
    "deploy/production-handoff-checklist.md",
    "deploy/runtime-stack-gemini-marqo.md",
    "deploy/server82-runbook.md",
    "deploy/operational-risk-register.md",
    "deploy/go-live-failure-scenarios.md",
    "deploy/production-incident-runbook.md",
    ".env.example",
    "OPERATIONS.md",
    "INTEGRATION.md",
    "REQUIREMENTS_TRACE.md",
    "sample_query_synonyms.json",
    "contracts/representative_sites.example.json",
    "contracts/operational_evidence.env.example",
    "scripts/env_file_security.py",
    "scripts/env_check.py",
    "scripts/csv_index.py",
    "scripts/search_insights.py",
    "scripts/widget_integration_probe.py",
    "scripts/load_compare.py",
    "scripts/marqo_resource_check.py",
    "scripts/prepare_handoff.py",
    "scripts/prepare_operational_bundle.py",
    "scripts/operational_bundle_check.py",
    "scripts/pre_handoff_audit.py",
    "scripts/server_db_intake_check.py",
    "scripts/go_live_scenario_check.py",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_requests() -> list[dict[str, Any]]:
    results = []
    for filename in REQUEST_FILES:
        started = time.perf_counter()
        try:
            request = SearchRequest.model_validate(load_json(CONTRACT_DIR / filename))
            results.append(
                {
                    "name": filename,
                    "ok": True,
                    "query_type": request.query_type.value,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "name": filename,
                    "ok": False,
                    "error": str(exc),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
    return results


def check_response() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        raw = load_json(CONTRACT_DIR / "search_response.example.json")
        response = SearchResponse.model_validate(raw)
        assert len(response.top) <= 3, "top must contain at most 3 products"
        top_ids = [item.product_id for item in response.top]
        item_ids = [item.product_id for item in response.items]
        assert len(set(top_ids)) == len(top_ids), "top product_ids must be unique"
        assert len(set(item_ids)) == len(item_ids), "items product_ids must be unique"
        assert not set(top_ids).intersection(item_ids), "items must not repeat top product_ids"
        assert len(set(response.suggested_categories)) == len(response.suggested_categories), "suggested_categories must be unique"
        assert response.meta.query_type.value in {"text", "image", "text_image"}
        assert response.meta.offset >= 0, "response meta offset must be non-negative"
        assert isinstance(response.meta.has_more, bool), "response meta has_more must be boolean"
        for section in ("top", "items"):
            for item in raw[section]:
                missing = REQUIRED_ITEM_FIELDS - set(item)
                assert not missing, f"{section} item is missing fields: {sorted(missing)}"
        return {
            "name": "search_response.example.json",
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        return {
            "name": "search_response.example.json",
            "ok": False,
            "error": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def check_click_log_request() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        request = ClickLogRequest.model_validate(load_json(CONTRACT_DIR / "click_log_request.json"))
        assert request.mall_id == "shop001"
        assert request.product_id == "P001"
        assert request.position == 1
        return {
            "name": "click_log_request.json",
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        return {
            "name": "click_log_request.json",
            "ok": False,
            "error": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def check_openapi() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        raw = load_public_openapi_schema(CONTRACT_DIR / "openapi.json")
        assert str(raw.get("openapi", "")).startswith("3."), "openapi must be 3.x"
        paths = raw["paths"]
        for path, method in REQUIRED_OPENAPI_PATHS.items():
            assert path in paths, f"missing path: {path}"
            assert method in paths[path], f"missing method for {path}: {method}"

        components = raw["components"]
        schemes = components["securitySchemes"]
        unsafe_credential_fields = {
            "api_key",
            "apiKey",
            "apikey",
            "api-key",
            "x-api-key",
            "admin_key",
            "admin-key",
            "x-admin-key",
        }
        for path in ["/api/ai-search", "/api/click-log"]:
            description = raw["paths"][path]["post"].get("description", "")
            for expected in ["X-API-Key", "api-key", "x-api-key", "admin_key", "x-admin-key"]:
                assert expected in description, f"OpenAPI {path} description missing unsafe credential alias {expected}"
        assert schemes["PublicApiKey"]["name"] == "X-API-Key"
        assert schemes["PublicApiKey"]["in"] == "header"
        assert "URL query string" in schemes["PublicApiKey"]["description"]
        assert "PublicApiKeyQuery" not in schemes, "OpenAPI must not advertise api_key query parameters"
        assert schemes["AdminApiKey"]["name"] == "X-Admin-Key"
        assert schemes["AdminApiKey"]["in"] == "header"
        assert "request body" in schemes["AdminApiKey"]["description"]
        responses = components["responses"]
        assert "Forbidden" in responses
        assert responses["Forbidden"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")

        schemas = components["schemas"]
        missing_schemas = REQUIRED_OPENAPI_SCHEMAS - set(schemas)
        assert not missing_schemas, f"missing schemas: {sorted(missing_schemas)}"
        assert "site_id" in schemas["SearchRequest"]["properties"], "SearchRequest schema missing site_id alias"
        assert "site_id" in schemas["ClickLogRequest"]["properties"], "ClickLogRequest schema missing site_id alias"
        assert schemas["SearchRequest"]["additionalProperties"] is False
        assert schemas["ClickLogRequest"]["additionalProperties"] is False
        assert not (unsafe_credential_fields & set(schemas["SearchRequest"]["properties"]))
        assert not (unsafe_credential_fields & set(schemas["ClickLogRequest"]["properties"]))
        result_properties = schemas["SearchResultItem"]["properties"]
        assert result_properties["product_id"]["minLength"] == 1
        assert result_properties["product_id"]["maxLength"] == 100
        assert result_properties["name"]["minLength"] == 1
        assert result_properties["category"]["maxLength"] == 100
        assert result_properties["price"]["minimum"] == 0
        assert result_properties["image_url"]["format"] == "uri"
        assert result_properties["image_url"]["maxLength"] == 1000
        assert result_properties["image_url"]["nullable"] is True
        assert result_properties["product_url"]["format"] == "uri"
        assert result_properties["product_url"]["maxLength"] == 1000
        assert result_properties["product_url"].get("nullable") is not True
        assert result_properties["mall_id"]["maxLength"] == 64
        assert result_properties["score"]["minimum"] == 0
        assert result_properties["score"]["maximum"] == 1
        assert result_properties["score_percent"]["minimum"] == 0
        assert result_properties["score_percent"]["maximum"] == 100
        multipart_schema = paths["/api/ai-search"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
        assert multipart_schema["additionalProperties"] is False
        assert not (unsafe_credential_fields & set(multipart_schema["properties"]))
        assert "site_id" in multipart_schema["properties"], "multipart search schema missing site_id alias"
        assert schemas["ClickLogRequest"]["required"] == ["product_id"]
        click_identity_any_of = [set(option.get("required", [])) for option in schemas["ClickLogRequest"].get("anyOf", [])]
        assert {"mall_id"} in click_identity_any_of and {"site_id"} in click_identity_any_of
        assert schemas["SearchRequest"]["properties"]["mall_id"]["maxLength"] == 64
        assert schemas["SearchRequest"]["properties"]["q"]["maxLength"] == 200
        assert schemas["SearchRequest"]["properties"]["category"]["maxLength"] == 100
        for field_name in ["print_method", "material", "color"]:
            assert schemas["SearchRequest"]["properties"][field_name]["maxLength"] == 100
        for field_name in ["min_price", "max_price", "quantity", "order_qty", "max_delivery_days"]:
            assert field_name in schemas["SearchRequest"]["properties"], f"SearchRequest missing {field_name}"
        assert schemas["ClickLogRequest"]["properties"]["product_id"]["maxLength"] == 100
        assert schemas["ClickLogRequest"]["properties"]["product_url"]["maxLength"] == 1000
        product_url_description = schemas["ClickLogRequest"]["properties"]["product_url"].get("description", "")
        assert "절대 HTTP(S) URL" in product_url_description
        assert "사용자 정보" in product_url_description
        assert schemas["ClickLogRequest"]["properties"]["query"]["maxLength"] == 200
        assert "events" in schemas["AdminMetrics"]["properties"]["sync"]["properties"], "AdminMetrics sync missing events summary"
        assert "alerts" in schemas["AdminMetrics"]["properties"], "AdminMetrics missing operational alerts"
        insights_get = paths["/admin/search-insights"]["get"]
        sync_log_get = paths["/admin/sync-log"]["get"]
        assert sync_log_get["summary"] == "최근 동기화/색인 로그 조회"
        assert sync_log_get["responses"]["200"]["content"]["application/json"]["schema"]["type"] == "array"
        insights_schema = insights_get["responses"]["200"]["content"]["application/json"]["schema"]
        assert insights_schema["$ref"].endswith("/SearchInsightsReport")
        insights_params = {parameter["name"] for parameter in insights_get.get("parameters", [])}
        assert {"limit", "min_searches"}.issubset(insights_params), "search insights must expose limit and min_searches"
        assert {"slow_text_ms", "slow_image_ms", "slow_mixed_ms"}.issubset(insights_params), (
            "search insights must expose slow latency thresholds"
        )
        insights_properties = schemas["SearchInsightsReport"]["properties"]
        for field in ["latency_ms", "query_type_latency_ms", "cache_latency_ms", "slow_queries", "slow_search_samples"]:
            assert field in insights_properties, f"SearchInsightsReport missing {field}"
        assert "mixed_weight_performance" in insights_properties, "SearchInsightsReport missing mixed weight performance"
        assert insights_properties["mixed_weight_performance"]["items"]["$ref"].endswith("/MixedWeightInsight")
        assert "synonym_seed_candidates" in insights_properties, "SearchInsightsReport missing synonym seed candidates"
        assert insights_properties["synonym_seed_candidates"]["items"]["$ref"].endswith("/SynonymSeedCandidate")
        assert "quality_case_candidates" in insights_properties, "SearchInsightsReport missing quality case candidates"
        assert insights_properties["quality_case_candidates"]["items"]["$ref"].endswith("/QualityCaseCandidate")
        assert "mixed_weight_recommendation" in insights_properties, "SearchInsightsReport missing mixed weight recommendation"
        assert schemas["SearchResponse"]["properties"]["top"]["maxItems"] == 3
        assert schemas["SearchResponse"]["properties"]["suggested_categories"]["maxItems"] == 15
        assert schemas["SearchResponse"]["properties"]["suggested_categories"]["uniqueItems"] is True
        search_meta = schemas["SearchMeta"]["properties"]
        assert search_meta["elapsed_ms"]["minimum"] == 0
        assert "embedding_backend" in search_meta, "SearchMeta missing embedding_backend"
        assert search_meta["limit"]["minimum"] == 1
        assert search_meta["offset"]["minimum"] == 0
        assert search_meta["next_offset"]["minimum"] == 0
        assert search_meta["text_weight"]["minimum"] == 0
        assert search_meta["image_weight"]["minimum"] == 0
        prometheus_get = paths["/admin/metrics.prom"]["get"]
        assert "text/plain" in prometheus_get["responses"]["200"]["content"], "Prometheus metrics response must be text/plain"

        search_post = paths["/api/ai-search"]["post"]
        request_content = search_post["requestBody"]["content"]
        assert "application/json" in request_content
        assert "multipart/form-data" in request_content
        json_examples = request_content["application/json"].get("examples", {})
        assert {"text", "image", "mixed"}.issubset(json_examples), "JSON search examples must cover text, image, and mixed modes"
        assert "image_base64" in json_examples["image"]["value"], "JSON image example must show image_base64"
        multipart_examples = request_content["multipart/form-data"].get("examples", {})
        assert {"image", "mixed", "site_id_alias"}.issubset(multipart_examples), "multipart search examples must cover image, mixed, and site_id alias modes"
        assert "image" in multipart_examples["mixed"]["value"], "multipart mixed example must show the image file field"
        assert "site_id" in multipart_examples["site_id_alias"]["value"], "multipart site_id alias example must show site_id"
        multipart_properties = request_content["multipart/form-data"]["schema"]["properties"]
        assert multipart_properties["q"]["maxLength"] == 200
        assert multipart_properties["category"]["maxLength"] == 100
        for field_name in ["print_method", "material", "color"]:
            assert multipart_properties[field_name]["maxLength"] == 100, f"multipart search missing {field_name}"
        for field_name in ["min_price", "max_price", "quantity", "order_qty", "max_delivery_days"]:
            assert field_name in multipart_properties, f"multipart search missing {field_name}"
        assert "HAEORUM_MIN_IMAGE_DIMENSION" in multipart_properties["image"]["description"]
        assert "HAEORUM_MIN_IMAGE_DIMENSION" in schemas["SearchRequest"]["properties"]["image_base64"]["description"]
        response_ref = search_post["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert response_ref.endswith("/SearchResponse")
        assert search_post["responses"]["403"]["$ref"].endswith("/Forbidden")
        click_post = paths["/api/click-log"]["post"]
        assert click_post["responses"]["400"]["$ref"].endswith("/BadRequest")
        assert click_post["responses"]["403"]["$ref"].endswith("/Forbidden")
        assert click_post["responses"]["429"]["$ref"].endswith("/RateLimited")
        return {
            "name": "openapi.json",
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        return {
            "name": "openapi.json",
            "ok": False,
            "error": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def check_deployment_files() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        missing = [filename for filename in REQUIRED_DEPLOYMENT_FILES if not (ROOT / filename).exists()]
        assert not missing, f"missing deployment files: {missing}"

        compose = (ROOT / "compose-haeorum-marqo.yaml").read_text(encoding="utf-8")
        for expected in [
            "marqo-api:",
            "ai-search:",
            "reindex-once:",
            "sync-worker:",
            "x-haeorum-nofile",
            "ulimits: *haeorum-nofile",
            "soft: 65535",
            "hard: 65535",
            "INSTALL_MSSQL_ODBC",
            "ACCEPT_MS_ODBC_EULA",
            "HAEORUM_ENV",
            "HAEORUM_SEARCH_ENGINE: \"marqo\"",
            "MARQO_URL: \"http://marqo-api:8882\"",
            "HAEORUM_MARQO_MODEL",
            "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS",
            "HAEORUM_MARQO_SEARCH_RETRY_COUNT",
            "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS",
            "HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS",
            "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
            "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
            "x-haeorum-logging",
            "logging: *haeorum-logging",
            "MARQO_API_WORKERS: \"${MARQO_API_WORKERS:-2}\"",
            "MARQO_API_KEEPALIVE_TIMEOUT: \"${MARQO_API_KEEPALIVE_TIMEOUT:-75}\"",
            "MARQO_API_GZIP_MINIMUM_SIZE: \"${MARQO_API_GZIP_MINIMUM_SIZE:-1024}\"",
            "MARQO_MAX_CONCURRENT_SEARCH: \"${MARQO_MAX_CONCURRENT_SEARCH:-100}\"",
            "VESPA_POOL_SIZE: \"${VESPA_POOL_SIZE:-128}\"",
            "VESPA_SEARCH_TIMEOUT_MS: \"${VESPA_SEARCH_TIMEOUT_MS:-5000}\"",
            "MARQO_INFERENCE_POOL_SIZE: \"${MARQO_INFERENCE_POOL_SIZE:-128}\"",
            "HAEORUM_INDEX_NAME",
            "HAEORUM_QUERY_SYNONYM_PATH",
            "HAEORUM_CATEGORY_SUGGESTION_LIMIT",
            "HAEORUM_MAX_OFFSET",
            "HAEORUM_MAX_IMAGE_DIMENSION",
            "HAEORUM_MIN_IMAGE_DIMENSION",
            "HAEORUM_MIXED_TEXT_WEIGHT",
            "HAEORUM_MIXED_IMAGE_WEIGHT",
            "HAEORUM_ERROR_LOG_PATH",
            "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_RATE_LIMIT_MAX_BUCKETS",
            "HAEORUM_SEARCH_MAX_CONCURRENCY",
            "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS",
            "HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY",
            "HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS",
            "HAEORUM_API_THREADPOOL_TOKENS",
            "HAEORUM_CACHE_MAX_ENTRIES",
            "HAEORUM_CACHE_MISS_LOCK_SECONDS",
            "HAEORUM_CACHE_MISS_WAIT_SECONDS",
            "HAEORUM_CACHE_MISS_POLL_SECONDS",
            "HAEORUM_REDIS_URL",
            "HAEORUM_REDIS_KEY_PREFIX",
            "HAEORUM_TRUSTED_PROXY_IPS",
            "172.16.0.0/12",
            "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT",
            "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_DELAY_SECONDS",
            "HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT",
            "HAEORUM_SYNC_ALERT_WEBHOOK_URL",
            "HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS",
            "HAEORUM_SYNC_LOCK_STALE_SECONDS",
            "healthcheck:",
            "http://127.0.0.1:8000/health",
        ]:
            assert expected in compose, f"compose-haeorum-marqo.yaml missing {expected}"

        compose_gemini = (ROOT / "compose-haeorum-gemini.yaml").read_text(encoding="utf-8")
        compose_gemini_localtest = (ROOT / "compose-haeorum-marqo-gemini-localtest.yaml").read_text(
            encoding="utf-8"
        )
        existing_8gb_compose = (ROOT / "compose-haeorum-existing-8gb.yaml").read_text(encoding="utf-8")
        assert "127.0.0.1:${HAEORUM_AI_SEARCH_PORT:-8000}:8000" in compose
        assert "127.0.0.1:${MARQO_PORT:-8882}:8882" in compose
        assert '"${HAEORUM_AI_SEARCH_PORT:-8000}:8000"' not in compose
        assert '"${MARQO_PORT:-8882}:8882"' not in compose
        assert 'GEMINI_AUTH_MODE: "${GEMINI_AUTH_MODE:-api_key}"' in compose_gemini
        assert 'GEMINI_API_KEY: "${GEMINI_API_KEY:-}"' in compose_gemini
        assert 'GEMINI_PROXY_API_KEY: "${GEMINI_PROXY_API_KEY:-}"' in compose_gemini
        assert 'HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY: "${GEMINI_PROXY_API_KEY:-}"' in compose_gemini
        assert "127.0.0.1:${GEMINI_EMBEDDING_PORT:-8098}:8098" in compose_gemini
        assert '"${GEMINI_EMBEDDING_PORT:-8098}:8098"' not in compose_gemini
        assert "GEMINI_PROXY_RATE_LIMIT_RPM" in existing_8gb_compose
        assert "HAEORUM_SEARCH_MAX_CONCURRENCY" in existing_8gb_compose
        assert "replace-with-google-cloud-project-id" not in compose_gemini
        assert "replace-with-google-cloud-account-number" not in compose_gemini
        assert "/gcp/application_default_credentials.json" not in compose_gemini
        assert 'GEMINI_AUTH_MODE: "${GEMINI_AUTH_MODE:-api_key}"' in compose_gemini_localtest
        assert 'GEMINI_API_KEY: "${GEMINI_API_KEY:?' in compose_gemini_localtest
        assert "/gcp/application_default_credentials.json" not in compose_gemini_localtest

        demo_compose = (ROOT / "compose-haeorum-demo.yaml").read_text(encoding="utf-8")
        assert "INSTALL_MSSQL_ODBC" in demo_compose
        assert "ACCEPT_MS_ODBC_EULA" in demo_compose
        assert "HAEORUM_ENV" in demo_compose
        assert "HAEORUM_QUERY_SYNONYM_PATH" in demo_compose
        assert "HAEORUM_CATEGORY_SUGGESTION_LIMIT" in demo_compose
        assert "HAEORUM_MIN_IMAGE_DIMENSION" in demo_compose
        assert "HAEORUM_MIXED_TEXT_WEIGHT" in demo_compose
        assert "HAEORUM_MIXED_IMAGE_WEIGHT" in demo_compose
        assert "HAEORUM_ERROR_LOG_PATH" in demo_compose
        assert "haeorum-logs" in demo_compose
        assert "HAEORUM_MAX_OFFSET" in demo_compose
        assert "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE" in demo_compose
        assert "HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE" in demo_compose
        assert "HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE" in demo_compose
        assert "HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE" in demo_compose
        assert "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE" in demo_compose
        assert "HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE" in demo_compose
        assert "HAEORUM_RATE_LIMIT_MAX_BUCKETS" in demo_compose
        assert "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS" in demo_compose
        assert "HAEORUM_MARQO_SEARCH_RETRY_COUNT" in demo_compose
        assert "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS" in demo_compose
        assert "HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS" in demo_compose
        assert "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES" in demo_compose
        assert "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES" in demo_compose
        assert "HAEORUM_SEARCH_MAX_CONCURRENCY" in demo_compose
        assert "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS" in demo_compose
        assert "HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY" in demo_compose
        assert "HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS" in demo_compose
        assert "HAEORUM_CACHE_MAX_ENTRIES" in demo_compose
        assert "HAEORUM_CACHE_MISS_LOCK_SECONDS" in demo_compose
        assert "HAEORUM_CACHE_MISS_WAIT_SECONDS" in demo_compose
        assert "HAEORUM_CACHE_MISS_POLL_SECONDS" in demo_compose
        assert "HAEORUM_REDIS_KEY_PREFIX" in demo_compose
        assert "HAEORUM_TRUSTED_PROXY_IPS" in demo_compose
        assert "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT" in demo_compose
        assert "HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT" in demo_compose
        assert "HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS" in demo_compose
        assert "HAEORUM_SYNC_LOCK_STALE_SECONDS" in demo_compose
        assert "healthcheck:" in demo_compose
        assert "http://127.0.0.1:8000/health" in demo_compose
        assert "127.0.0.1:${HAEORUM_AI_SEARCH_PORT:-8000}:8000" in demo_compose
        assert '"${HAEORUM_AI_SEARCH_PORT:-8000}:8000"' not in demo_compose

        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for expected in [
            "HAEORUM_ENV=production",
            "HAEORUM_FILTER_BY_MALL_ID",
            "HAEORUM_QUERY_SYNONYM_PATH",
            "HAEORUM_CATEGORY_SUGGESTION_LIMIT",
            "HAEORUM_MAX_OFFSET",
            "HAEORUM_MAX_IMAGE_DIMENSION",
            "HAEORUM_MIN_IMAGE_DIMENSION",
            "HAEORUM_MIXED_TEXT_WEIGHT",
            "HAEORUM_MIXED_IMAGE_WEIGHT",
            "HAEORUM_ERROR_LOG_PATH",
            "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_RATE_LIMIT_MAX_BUCKETS",
            "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS",
            "HAEORUM_MARQO_SEARCH_RETRY_COUNT",
            "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS",
            "MARQO_URL=http://marqo-api:8882",
            "MARQO_API_KEEPALIVE_TIMEOUT=75",
            "MARQO_API_GZIP_MINIMUM_SIZE=1024",
            "HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098",
            "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY=replace-with-internal-gemini-proxy-key",
            "HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS",
            "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
            "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
            "GEMINI_AUTH_MODE=api_key",
            "GEMINI_API_KEY=replace-with-protected-gemini-api-key",
            "GEMINI_PROXY_API_KEY=replace-with-internal-gemini-proxy-key",
            "GEMINI_MAX_RESPONSE_BYTES=33554432",
            "GEMINI_EMBEDDING_PORT=8098",
            "HAEORUM_SEARCH_MAX_CONCURRENCY",
            "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS",
            "HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY",
            "HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS",
            "HAEORUM_CACHE_MAX_ENTRIES",
            "HAEORUM_CACHE_MISS_LOCK_SECONDS",
            "HAEORUM_CACHE_MISS_WAIT_SECONDS",
            "HAEORUM_CACHE_MISS_POLL_SECONDS",
            "HAEORUM_REDIS_URL",
            "HAEORUM_REDIS_KEY_PREFIX",
            "HAEORUM_TRUSTED_PROXY_IPS",
            "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT",
            "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_DELAY_SECONDS",
            "HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT",
            "HAEORUM_SYNC_ALERT_WEBHOOK_URL",
            "HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS",
            "HAEORUM_SYNC_LOCK_STALE_SECONDS",
            "HAEORUM_MSSQL_READONLY_CONNECTION_STRING",
            "HAEORUM_MSSQL_QUERY",
            "HAEORUM_MSSQL_PRODUCT_ID_COLUMN",
            "HAEORUM_MSSQL_UPDATED_AT_COLUMN",
            "INSTALL_MSSQL_ODBC=false",
            "ACCEPT_MS_ODBC_EULA=false",
        ]:
            assert expected in env_example, f".env.example missing {expected}"

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "uvicorn" in dockerfile and "app.main:app" in dockerfile
        for expected in [
            "INSTALL_MSSQL_ODBC",
            "ACCEPT_MS_ODBC_EULA",
            "requirements-mssql.txt",
            "packages.microsoft.com",
            "msodbcsql18",
            "ACCEPT_EULA=Y",
            "unixodbc",
            "unixodbc-dev",
            "COPY scripts ./scripts",
            "COPY sample_query_synonyms.json",
        ]:
            assert expected in dockerfile, f"Dockerfile missing {expected}"

        api_service = (ROOT / "deploy" / "systemd" / "haeorum-ai-search.service").read_text(encoding="utf-8")
        sync_service = (ROOT / "deploy" / "systemd" / "haeorum-ai-sync.service").read_text(encoding="utf-8")
        reindex_service = (ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.service").read_text(encoding="utf-8")
        reindex_timer = (ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.timer").read_text(encoding="utf-8")
        nginx_config = (ROOT / "deploy" / "nginx" / "haeorum-ai-search.conf").read_text(encoding="utf-8")
        logrotate_config = (ROOT / "deploy" / "logrotate" / "haeorum-ai-search").read_text(encoding="utf-8")
        deploy_env = (ROOT / "deploy" / "haeorum-ai-search.env.example").read_text(encoding="utf-8")
        risk_register = (ROOT / "deploy" / "operational-risk-register.md").read_text(encoding="utf-8")
        go_live_scenarios = (ROOT / "deploy" / "go-live-failure-scenarios.md").read_text(encoding="utf-8")
        incident_runbook = (ROOT / "deploy" / "production-incident-runbook.md").read_text(encoding="utf-8")
        runtime_stack = (ROOT / "deploy" / "runtime-stack-gemini-marqo.md").read_text(encoding="utf-8")
        server_db_request = (ROOT / "deploy" / "server-db-request.ko.md").read_text(encoding="utf-8")
        server_db_intake = (ROOT / "deploy" / "server-db-intake.md").read_text(encoding="utf-8")
        pre_handoff_audit = (ROOT / "scripts" / "pre_handoff_audit.py").read_text(encoding="utf-8")
        server_db_intake_check = (ROOT / "scripts" / "server_db_intake_check.py").read_text(encoding="utf-8")
        compose_exposure_check = (ROOT / "scripts" / "compose_exposure_check.py").read_text(encoding="utf-8")
        go_live_scenario_check = (ROOT / "scripts" / "go_live_scenario_check.py").read_text(encoding="utf-8")
        gemini_embedding_proxy = (ROOT / "app" / "gemini_embedding_proxy.py").read_text(encoding="utf-8")
        gemini_embeddings = (ROOT / "app" / "gemini_embeddings.py").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        operations = (ROOT / "OPERATIONS.md").read_text(encoding="utf-8")
        requirements_trace = (ROOT / "REQUIREMENTS_TRACE.md").read_text(encoding="utf-8")
        config_module = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        repository_root = ROOT.parents[1]
        marqo_runner = (repository_root / "components" / "marqo" / "run_marqo.sh").read_text(encoding="utf-8")
        marqo_api = (
            repository_root / "components" / "marqo" / "src" / "marqo" / "tensor_search" / "api.py"
        ).read_text(encoding="utf-8")
        assert "uvicorn app.main:app" in api_service
        assert "Restart=always" in api_service
        assert "RestartSec=5" in api_service
        assert "NoNewPrivileges=true" in api_service
        assert "app.sync_worker --mode sync" in sync_service
        assert "app.sync_worker --mode reindex --once" in reindex_service
        assert "OnCalendar=*-*-* 03:00:00" in reindex_timer
        assert "Persistent=true" in reindex_timer
        assert "upstream haeorum_ai_search_api" in nginx_config
        assert "least_conn" in nginx_config
        assert "server 127.0.0.1:8000 max_fails=3 fail_timeout=10s" in nginx_config
        assert "proxy_pass http://haeorum_ai_search_api" in nginx_config
        assert "keepalive 64" in nginx_config
        assert "ssl_certificate" in nginx_config
        assert "client_max_body_size 6m" in nginx_config
        assert "proxy_set_header X-Real-IP $remote_addr" in nginx_config
        assert "proxy_set_header X-Forwarded-For $remote_addr" in nginx_config
        assert 'proxy_set_header Forwarded ""' in nginx_config
        assert "$proxy_add_x_forwarded_for" not in nginx_config
        assert "/var/log/haeorum-ai-search/*.jsonl" in logrotate_config
        assert "copytruncate" in logrotate_config
        assert "rotate 14" in logrotate_config
        assert "HAEORUM_ENV=production" in deploy_env
        assert "MARQO_URL=http://marqo-api:8882" in deploy_env
        assert "HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098" in deploy_env
        assert "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY=replace-with-internal-gemini-proxy-key" in deploy_env
        assert "GEMINI_AUTH_MODE=api_key" in deploy_env
        assert "GEMINI_API_KEY=replace-with-protected-gemini-api-key" in deploy_env
        assert "GEMINI_PROXY_API_KEY=replace-with-internal-gemini-proxy-key" in deploy_env
        assert "GEMINI_MAX_RESPONSE_BYTES=33554432" in deploy_env
        assert "GEMINI_EMBEDDING_PORT=8098" in deploy_env
        for forbidden in [
            "replace-with-real-google-project-id",
            "replace-with-real-google-account-number",
            "AQ.",
            "root-password-placeholder",
            "root /",
        ]:
            assert forbidden not in deploy_env, f"deploy env contains forbidden token: {forbidden}"
        assert "HAEORUM_QUERY_SYNONYM_PATH" in deploy_env
        assert "HAEORUM_CATEGORY_SUGGESTION_LIMIT" in deploy_env
        assert "HAEORUM_MIN_IMAGE_DIMENSION" in deploy_env
        assert "HAEORUM_MIXED_TEXT_WEIGHT" in deploy_env
        assert "HAEORUM_MIXED_IMAGE_WEIGHT" in deploy_env
        assert "HAEORUM_MSSQL_READONLY_CONNECTION_STRING" in deploy_env
        assert "ApplicationIntent=ReadOnly" in deploy_env
        assert "HAEORUM_MSSQL_CONNECTION_STRING" in config_module
        assert "validate_mssql_connection_string_value" in config_module
        assert "TrustServerCertificate=no" in config_module
        assert "HAEORUM_ERROR_LOG_PATH" in deploy_env
        assert "HAEORUM_MAX_OFFSET" in deploy_env
        assert "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE" in deploy_env
        assert "HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE" in deploy_env
        assert "HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE" in deploy_env
        assert "HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE" in deploy_env
        assert "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE" in deploy_env
        assert "HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE" in deploy_env
        assert "HAEORUM_RATE_LIMIT_MAX_BUCKETS" in deploy_env
        assert "HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS" in deploy_env
        assert "HAEORUM_MARQO_SEARCH_RETRY_COUNT" in deploy_env
        assert "HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS" in deploy_env
        assert "HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS" in deploy_env
        assert "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES" in deploy_env
        assert "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES" in deploy_env
        assert "HAEORUM_SEARCH_MAX_CONCURRENCY" in deploy_env
        assert "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS" in deploy_env
        assert "HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY" in deploy_env
        assert "HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS" in deploy_env
        assert "HAEORUM_API_THREADPOOL_TOKENS=96" in deploy_env
        assert "MARQO_API_WORKERS=2" in deploy_env
        assert "MARQO_API_KEEPALIVE_TIMEOUT=75" in deploy_env
        assert "MARQO_API_GZIP_MINIMUM_SIZE=1024" in deploy_env
        for expected in [
            "API resource exhaustion",
            "Gemini project quota exceeded",
            "Log disk fill",
            "Spoofed client IP",
            "DB View drift",
            "Rollback",
            "OWASP API4:2023",
            "Gemini API rate limits",
            "Docker json-file log rotation",
            "Nginx `proxy_set_header`",
        ]:
            assert expected in risk_register, f"operational-risk-register.md missing {expected}"
        for expected in [
            "Runtime Stack: Marqo + Gemini",
            "ai-search",
            "gemini-embedding",
            "marqo-api",
            "mioc",
            "vespa",
            "haeorum_gemini_query_vector",
            "GEMINI_AUTH_MODE=api_key",
        ]:
            assert expected in runtime_stack, f"runtime-stack-gemini-marqo.md missing {expected}"
        for path_name, text in [
            ("README.md", readme),
            ("OPERATIONS.md", operations),
            ("REQUIREMENTS_TRACE.md", requirements_trace),
            ("deploy/operational-risk-register.md", risk_register),
            ("deploy/go-live-failure-scenarios.md", go_live_scenarios),
            ("deploy/production-incident-runbook.md", incident_runbook),
            ("deploy/runtime-stack-gemini-marqo.md", runtime_stack),
        ]:
            assert "qwen" not in text.lower(), f"{path_name} contains legacy embedding provider text"
        for expected in [
            "Go-Live Failure Scenarios",
            "abusive_or_accidental_traffic_spike",
            "gemini_quota_429_or_cost_runaway",
            "backend_overload_retry_explosion",
            "internal_port_exposure",
            "multi_api_scale_state_split",
            "observability_alerting_gap",
            "unsafe_external_url_or_image_source",
            "deployment_restart_or_rollback_gap",
            "index_rebuild_or_sync_recovery_gap",
            "cost_budget_notification_gap",
            "production-incident-runbook.md",
            "operator_surface_gemini_only",
            "tools/go_live_scenario_check.py",
        ]:
            assert expected in go_live_scenarios, f"go-live-failure-scenarios.md missing {expected}"
        for expected in [
            "Production Incident Runbook",
            "First 10 Minutes",
            "Required Pre-Signoff Alerts",
            "Recovery Exit Criteria",
            "Gemini Quota Or Cost Spike",
            "Unsafe External URL Or Image Source",
            "Deployment Or Restart Failure",
        ]:
            assert expected in incident_runbook, f"production-incident-runbook.md missing {expected}"
        for expected in [
            '@app.get("/metrics")',
            "load_proxy_status",
            "validate_gemini_auth_settings",
            "Gemini embedding proxy is not configured",
            "status_code=503",
            "max_response_bytes",
        ]:
            assert expected in gemini_embedding_proxy, f"gemini_embedding_proxy.py missing {expected}"
        for expected in [
            "DEFAULT_GEMINI_MAX_RESPONSE_BYTES",
            "GEMINI_MAX_RESPONSE_BYTES",
            "read_gemini_response_limited",
            "Gemini embedding API response exceeds",
        ]:
            assert expected in gemini_embeddings, f"gemini_embeddings.py missing {expected}"
        for expected in [
            "기존 개발자/인프라 담당자 요청 문구",
            "SSH 접속 주소",
            "MSSQL read-only",
            "Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly",
            "gemini-embedding-2",
            "AI API 장애 시 기존 검색으로 돌아가는 fallback 방식",
            "위젯 비활성화 롤백 확인",
        ]:
            assert expected in server_db_request, f"server-db-request.ko.md missing {expected}"
        for expected in [
            "server_db_intake_check.py",
            "ready_for_env_and_server_preflight",
            "Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly",
            "Nginx forwarded header policy",
            "Docker log rotation values",
            "API/Marqo/Gemini internal bind/listen policy",
        ]:
            assert expected in server_db_intake, f"server-db-intake.md missing {expected}"
        for expected in [
            "--mall-id",
            "operator_visible_docs_gemini_only",
            "security_handoff_defaults",
            "docker_port_binding_problems",
            "proxy_set_header X-Forwarded-For $remote_addr;",
            "GEMINI_API_KEY",
            "rollback test confirmation",
            "payload={\"mall_id\": args.mall_id",
        ]:
            assert expected in pre_handoff_audit, f"pre_handoff_audit.py missing {expected}"
        for expected in [
            "ready_for_env_and_server_preflight",
            "TrustServerCertificate must not be allowed",
            "AI API/Marqo/Gemini ports must not be public inbound ports",
            "Nginx forwarded header policy must overwrite X-Forwarded-For with $remote_addr",
            "Docker log rotation values must include max-size and max-file",
            "API/Marqo/Gemini internal bind/listen policy must keep API/Marqo/Gemini ports private",
            "no_plaintext_secrets",
            "Gemini quota page checked must be yes",
            "fallback behavior must explicitly keep or restore the existing search",
        ]:
            assert expected in server_db_intake_check, f"server_db_intake_check.py missing {expected}"
        for expected in [
            "protected_ports_loopback_only",
            "embedding_proxy_loopback_only",
            "127.0.0.1",
            "0.0.0.0",
            "compose exposure check",
        ]:
            assert expected in compose_exposure_check, f"compose_exposure_check.py missing {expected}"
        for expected in [
            "SCENARIOS",
            "gemini_quota_429_or_cost_runaway",
            "check_operator_surface",
            "operator_surface_gemini_only",
            "runtime_health_gemini_marqo",
            "runtime_critical_alerts_absent",
            "SOURCE_NOTES",
        ]:
            assert expected in go_live_scenario_check, f"go_live_scenario_check.py missing {expected}"
        assert "MARQO_MAX_CONCURRENT_SEARCH=100" in deploy_env
        assert "VESPA_POOL_SIZE=128" in deploy_env
        assert "VESPA_SEARCH_TIMEOUT_MS=5000" in deploy_env
        assert "MARQO_INFERENCE_POOL_SIZE=128" in deploy_env
        assert "HAEORUM_CACHE_MAX_ENTRIES" in deploy_env
        assert "HAEORUM_CACHE_MISS_LOCK_SECONDS" in deploy_env
        assert "HAEORUM_CACHE_MISS_WAIT_SECONDS" in deploy_env
        assert "HAEORUM_CACHE_MISS_POLL_SECONDS" in deploy_env
        assert "HAEORUM_REDIS_URL" in deploy_env
        assert "HAEORUM_REDIS_KEY_PREFIX" in deploy_env
        assert "HAEORUM_TRUSTED_PROXY_IPS" in deploy_env
        assert "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT" in deploy_env
        assert "HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_DELAY_SECONDS" in deploy_env
        assert "HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT" in deploy_env
        assert "HAEORUM_SYNC_ALERT_WEBHOOK_URL" in deploy_env
        assert "HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS" in deploy_env
        assert "HAEORUM_SYNC_LOCK_STALE_SECONDS" in deploy_env
        assert "HAEORUM_MSSQL_PRODUCT_ID_COLUMN" in deploy_env
        assert "HAEORUM_MSSQL_UPDATED_AT_COLUMN" in deploy_env
        assert "MARQO_API_KEEPALIVE_TIMEOUT" in marqo_runner
        assert "MARQO_API_GZIP_MINIMUM_SIZE" in marqo_runner
        assert "exec uvicorn api:app" in marqo_runner
        assert "GZipMiddleware" in marqo_api
        assert "default_response_class=ORJSONResponse" in marqo_api

        load_test = (ROOT / "scripts" / "load_test.py").read_text(encoding="utf-8")
        for expected in [
            "choices=[\"text\", \"image\", \"mixed\"]",
            "mixed-traffic",
            "active_users",
            "planned_request_count",
            "traffic_mix",
            "markdown-output",
            "max_error_rate",
            "fetch_admin_metrics",
            "server_metrics",
            "cache_clear_errors",
            "search_log_write_errors",
            "--admin-key",
            "api_server_count",
            "--api-server-count",
            "image_base64",
            "image_data_url_for_args",
            "--image-file",
            "image_input",
            "validate_image_bytes",
            "--origin",
            "\"p99\"",
            "validate_search_response",
            "response_valid",
            "summarize_response_contract",
            "response_contract",
            "expected_delta_minimums",
            "delta_coverage_ok",
            "coverage_ok",
            "run_log_coverage",
            "fetch_admin_search_log",
            "server_metrics.run_log.search_events_below_successful_responses",
            "search_events_below_successful_responses",
            "validate_args",
            "normalize_http_base_url",
            "normalize_http_origin",
            "normalize_public_http_base_url",
            "normalize_public_http_origin",
            "open_public_http_request",
            "allow_local_target",
            "--allow-local-target",
            "target_validation",
            "failed_validation_report",
            "REQUIRED_RESULT_FIELDS",
            "REQUIRED_META_FIELDS",
            "--requests must be at least 1",
            "search response should include top results",
            "search response should include related items",
            "search response should include suggested_categories",
            "search response meta missing fields",
            "missing fields:",
            "engine_counts",
            "non_marqo_engine_responses",
            "expected_mall_id_counts",
            "meta_mall_id_counts",
            "result_mall_id_counts",
            "mall_id_mismatch_count",
            "mall_id_mismatch_count == 0",
            "qwen_query_vector_wait_timeout_seconds",
            "qwen_query_vector_runtime_text_max_entries",
            "qwen_query_vector_runtime_image_max_entries",
            "backend_marqo_total_elapsed_ms",
            "backend_qwen_total_elapsed_ms",
            "backend_marqo_total_request_body_bytes",
            "backend_qwen_total_request_body_bytes",
            "backend_transport_payload_problems",
            "backend_transport_payload_summary",
            "backend_payload_ok",
            "summarize_request_profile",
            "request_profile",
            "min_backend_marqo_request_attempts",
            "server_metrics.delta.backend_marqo_request_attempts_below_unique_requests",
            "server_metrics.delta.backend_qwen_request_attempts_below_unique_image_inputs",
            "server_metrics.delta.backend_marqo_gzip_responses_zero",
            "server_metrics.delta.backend_marqo_response_body_bytes_not_below_decoded",
            "LoadRequestIdentity",
            "load_mall_identities",
            "select_mall_identity_sample",
            "build_request_specs",
            "load_identity_summary",
            "--mall-sample-size",
            "--mall-sample-strategy",
            "sampling_strategy",
            "source_enabled_count",
            "eligible_mall_count",
            "mall_identity",
            "requested_mall_id",
            "search response meta mall_id must match requested mall_id",
            "mall_id must match requested mall_id",
        ]:
            assert expected in load_test, f"load_test.py missing {expected}"

        load_compare = (ROOT / "scripts" / "load_compare.py").read_text(encoding="utf-8")
        for expected in [
            "single-report",
            "multi-report",
            "api_server_count",
            "mixed-traffic",
            "active_users",
            "max_multi_p95_regression_percent",
            "min_multi_rps_ratio",
            "workload_comparable",
            "comparison.base_url",
            "comparison.image_input",
            "operational_target_url_problems",
            "is_non_public_host",
            "base_url_non_local",
            "origin_https",
            "mall_id",
            "origin",
            "response_contract_ok",
            "response_engine_ok",
            "non_marqo_engine_responses",
            "response_mall_identity_ok",
            "response_mall_identity_problems",
            "mall_id_mismatch_count",
            "MIN_MIXED_TRAFFIC_MALL_IDENTITY_SAMPLE",
            "MIN_MIXED_TRAFFIC_MALL_RESPONSE_DISTRIBUTION_RATIO",
            "summarize_mall_identity",
            "mall_identity_ok",
            "mall_identity_problems",
            "mall_identity_workload",
            "mall_identity.sampled_mall_ids_missing_response_counts",
            "mall_identity.sampled_mall_ids_underrepresented",
            "image_source_ok",
            "image_input.source",
            "image_input.sha256",
            "server_metrics_missing",
            "server_metrics.delta.search_events",
            "server_metrics.delta.cache_clear_errors",
            "QWEN_QUERY_VECTOR_DELTA_SERVER_METRIC_FIELDS",
            "qwen_query_vector_server_metric_problems",
            "qwen_query_vector_runtime_text_max_entries_below_required",
            "qwen_query_vector_runtime_image_max_entries_below_required",
            "backend_marqo_total_elapsed_ms",
            "backend_qwen_total_elapsed_ms",
            "backend_marqo_total_request_body_bytes",
            "backend_qwen_total_request_body_bytes",
            "backend_transport_payload_problems",
            "backend_request_attempt_problems",
            "request_profile_problems",
            "request_profile",
            "server_metrics.delta.backend_marqo_request_attempts_zero",
            "server_metrics.delta.backend_qwen_request_attempts_zero",
            "server_metrics.delta.backend_marqo_request_attempts_below_unique_requests",
            "server_metrics.delta.backend_qwen_request_attempts_below_unique_image_inputs",
            "server_metrics.delta.backend_marqo_gzip_responses_zero",
            "server_metrics.delta.backend_marqo_response_body_bytes_not_below_decoded",
            "server_metrics_run_log_coverage",
            "metric_coverage_meets",
            "search_events_below_successful_responses",
            "target_validation_ok",
            "external_embedding_backend",
            '{"native", "qwen", "gemini"}',
        ]:
            assert expected in load_compare, f"load_compare.py missing {expected}"

        cache_module = (ROOT / "app" / "cache.py").read_text(encoding="utf-8")
        for expected in ["RedisSearchCache", "MemorySearchCache", "max_entries", "evictions", "claim_miss_owner", "release_miss_owner", "search-cache-lock", "make_search_cache", "clear(self)", "scan_iter"]:
            assert expected in cache_module, f"cache.py missing {expected}"

        csv_index = (ROOT / "scripts" / "csv_index.py").read_text(encoding="utf-8")
        for expected in [
            "CsvProductSource",
            "SyncService",
            "create_search_engine",
            "DEPLOYABLE_SEARCH_ENGINES",
            "summarize_products",
            "dry-run",
            "markdown-output",
            "persistent_index",
            "empty_product_summary",
            "would_index",
            "would_delete",
            "validate_marqo_url_value",
            "validate-images",
            "active_unsafe_product_url_count",
            "active_product_url_product_id_mismatch_count",
        ]:
            assert expected in csv_index, f"csv_index.py missing {expected}"

        engine_factory = (ROOT / "app" / "engine_factory.py").read_text(encoding="utf-8")
        for expected in [
            "ensure_search_engine_runtime_allowed",
            "PRODUCTION_SEARCH_ENGINES",
            "SUPPORTED_SEARCH_ENGINES",
            "production runtime",
        ]:
            assert expected in engine_factory, f"engine_factory.py missing {expected}"

        main_module = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        for expected in [
            "enforce_search_rate_limit",
            "search rate limit exceeded for client",
            "enforce_click_rate_limit",
            "click log rate limit exceeded for client",
            "request.headers.get(\"origin\")",
            "run_in_threadpool",
            "validate_public_access_headers(request)",
            "enforce_search_client_rate_limit",
            "enforce_search_mall_rate_limit",
            "enforce_click_client_rate_limit",
            "enforce_click_mall_rate_limit",
            "await run_in_threadpool(enforce_search_client_rate_limit, request)",
            "await run_in_threadpool(enforce_search_mall_rate_limit, mall_id)",
            "await run_in_threadpool(enforce_image_rate_limit, request, mall_id)",
            "await run_in_threadpool(enforce_click_client_rate_limit, request)",
            "await run_in_threadpool(enforce_click_mall_rate_limit, click.mall_id)",
            "ParsedSearchInput",
            "read_json_object_limited",
            "read_multipart_image_data_url",
            "await run_in_threadpool(service.search, search_request, acquire_search_execution_slot)",
            "await run_in_threadpool(service.search, search_request, acquire_image_search_slot)",
            "await run_in_threadpool(enter_image_search_slot_context)",
            "await run_in_threadpool(exit_image_search_slot_context, upload_slot_context)",
            "await run_in_threadpool(service.log_click, click)",
            "click = ClickLogRequest.model_validate(payload)",
            "search_cache = make_search_cache(settings)",
            "search_cache=search_cache",
            "HTTPException(status_code=400",
            "compare_digest",
            "app.openapi",
            "load_public_openapi_schema",
            "metrics_to_prometheus",
            "/admin/metrics.prom",
            "PlainTextResponse",
        ]:
            assert expected in main_module, f"main.py missing {expected}"

        request_context = (ROOT / "app" / "request_context.py").read_text(encoding="utf-8")
        for expected in [
            "resolve_client_ip",
            "extract_forwarded_for",
            "normalized_ip_token",
            '"forwarded"',
            '"x-forwarded-for"',
            '"x-real-ip"',
        ]:
            assert expected in request_context, f"request_context.py missing {expected}"

        request_body = (ROOT / "app" / "request_body.py").read_text(encoding="utf-8")
        for expected in [
            "read_request_body_limited",
            "read_json_object_limited",
            "exceeds {max_bytes} bytes",
            "invalid JSON body",
        ]:
            assert expected in request_body, f"request_body.py missing {expected}"

        openapi_contract = (ROOT / "app" / "openapi_contract.py").read_text(encoding="utf-8")
        for expected in [
            "CONTRACT_OPENAPI_PATH",
            "multipart/form-data",
            "application/json",
            "SearchResponse",
            "validate_public_openapi_schema",
            "403 Forbidden",
            "429 RateLimited",
            "maxLength",
        ]:
            assert expected in openapi_contract, f"openapi_contract.py missing {expected}"

        security_module = (ROOT / "app" / "security.py").read_text(encoding="utf-8")
        for expected in [
            "validate_origin_access",
            "validate_public_header_access",
            "PublicHeaderAccessIndex",
            "public_header_access_index",
            "_PUBLIC_HEADER_INDEX_CACHE",
            "allowed_origins",
            "origin is not allowed",
            "UNSAFE_PUBLIC_API_KEY_FIELD_NORMALIZED",
            '"api-key"',
            '"x-api-key"',
            '"admin_key"',
            'replace("_", "").replace("-", "")',
        ]:
            assert expected in security_module, f"security.py missing {expected}"

        config_module = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        for expected in [
            "validate_production_settings",
            "HAEORUM_ADMIN_API_KEY must be changed",
            "HAEORUM_CORS_ORIGINS must not contain *",
            "HAEORUM_CORS_ORIGINS must use https origins in production",
            "HAEORUM_CORS_ORIGINS must use safe public origins in production",
            "parse_cors_origins",
            "wildcard must not be combined",
            "enabled production malls must not use wildcard allowed_origins",
            "enabled production mall allowed_origins must use https",
            "enabled production mall allowed_origins must use safe public origins",
            "mall_origins_missing_from_cors",
            "enabled production mall allowed_origins must be included in HAEORUM_CORS_ORIGINS",
            "MAX_PRODUCTION_SYNC_INTERVAL_SECONDS",
            "MAX_OPERATIONAL_SEARCH_OFFSET",
            "normalize_origin_value",
            "origin_uses_safe_public_url",
            "validate_product_url_template_value",
            "product_url_template_uses_safe_public_url",
            "product_url_template_uses_https",
            "HAEORUM_PRODUCT_URL_TEMPLATE must format to a safe public http(s) URL",
            "HAEORUM_PRODUCT_URL_TEMPLATE must use https in production",
            "enabled production malls must use safe public product_url_template",
            "enabled production malls must use https product_url_template",
            "values must be origins",
            "must format to an http(s) URL",
            "must format to a safe public http(s) URL",
            "validate_numeric_settings",
            "HAEORUM_MAX_LIMIT must be at least HAEORUM_DEFAULT_LIMIT",
            'require_at_most("HAEORUM_MAX_OFFSET"',
            "must be at most",
            "HAEORUM_MIN_IMAGE_DIMENSION",
            "HAEORUM_MAX_IMAGE_DIMENSION must be at least HAEORUM_MIN_IMAGE_DIMENSION",
            "HAEORUM_MIXED_TEXT_WEIGHT and HAEORUM_MIXED_IMAGE_WEIGHT must not both be zero",
            "HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE",
            "HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE",
            "check_sync_alert_webhook_url",
            "HAEORUM_SYNC_ALERT_WEBHOOK_URL is invalid",
            "is_link_or_unspecified_host",
            "host must not be localhost, loopback, link-local, or unspecified",
            "must not use link-local or unspecified hosts",
            "must be explicitly set to an integer value",
            "must be explicitly set to a numeric value",
            "must be explicitly set to true or false",
            "must be an integer",
            "must be a number",
            "must be a boolean",
            "must not contain whitespace, control characters, or backslashes",
            'require_between("HAEORUM_LOW_SCORE_THRESHOLD"',
            "SUPPORTED_SEARCH_ENGINES",
            "RESERVED_SEARCH_ENGINES",
            "DEPLOYABLE_SEARCH_ENGINES",
            "PRODUCTION_SEARCH_ENGINES",
            "HAEORUM_SEARCH_ENGINE must be one of",
            "validate_production_environment_variables",
            "validate_production_config_file_environment_variables",
            "Production config file settings must be explicitly set in production",
            "Production config file settings must not point to sample files",
            "is_placeholder_config_value",
            "explicit_production_env_value",
            "Production search settings must be explicitly set in production",
            "Production data source must be explicitly set in production",
            "HAEORUM_PRODUCT_CSV must not point to sample_products.csv in production",
            "HAEORUM_PRODUCT_CSV does not exist in production",
            "HAEORUM_MARQO_MODEL",
            "HAEORUM_INDEX_NAME",
            "HAEORUM_QWEN_EMBEDDING_DIMENSIONS",
            "HAEORUM_GEMINI_MODEL",
            "HAEORUM_GEMINI_EMBEDDING_DIMENSIONS",
            "model_field = active_embedding_env_name",
            "is required when HAEORUM_EMBEDDING_BACKEND",
            "must not be a placeholder when ",
            "embedding settings must be explicitly set in production",
            "qwen for legacy local GPU deployments",
            "validate_marqo_url_value",
            "must be an absolute HTTP(S) URL without credentials, query strings, or fragments",
            "validate_readonly_query",
            "HAEORUM_MSSQL_QUERY",
            "validate_sql_identifier_value",
            "HAEORUM_MSSQL_PRODUCT_ID_COLUMN",
            "HAEORUM_MSSQL_UPDATED_AT_COLUMN",
        ]:
            assert expected in config_module, f"config.py missing {expected}"

        engine_module = (ROOT / "app" / "engine.py").read_text(encoding="utf-8")
        for expected in [
            "term_matches_document",
            "edit_distance_at_most",
            "ReservedSearchEngineUnavailable",
            "RESERVED_ENGINE_PLANS",
            "reserved_adapter",
            "required_components",
            "TypesenseSearchEngine",
            "QdrantSearchEngine",
            "product_matches_query_filters",
            "print_methods",
            "min_order_qty",
            "max_delivery_days",
            "search_payload_limit",
            "Numeric filters are pushed into Marqo range filters",
            "numeric_range_filter",
            "any_numeric_range_filter",
            "marqo_numeric_value",
            "inferred_categories",
            "_category_intent_score",
            "append_inferred_categories",
            "validate_marqo_url_value",
            "safe_absolute_http_url(product.image_url)",
            "MARQO_MAX_SEARCH_CANDIDATES",
            "max_candidates",
            "min(base_limit",
            "qwen_health_contract_problems",
            "qwen_health_problems",
        ]:
            assert expected in engine_module, f"engine.py missing {expected}"

        url_safety = (ROOT / "app" / "url_safety.py").read_text(encoding="utf-8")
        for expected in [
            "safe_absolute_http_url",
            "SafePublicHTTPRedirectHandler",
            "UnsafePublicHttpTargetError",
            "validate_http_url_resolves_to_public_network",
            "open_public_http_request",
            "is_local_or_link_host",
            "normalize_http_base_url",
            "normalize_http_origin",
            "normalize_public_http_base_url",
            "normalize_public_http_origin",
            "normalize_origin_parts",
            "redact_url_for_report",
            "is_link_or_unspecified_host",
            "is_non_public_host",
            "is_non_public_ip_address",
            "ipaddress.ip_address",
            "address.is_link_local",
            "parsed.is_private",
            "parsed.is_reserved",
            "parsed.is_multicast",
            "not parsed.is_global",
            "socket.getaddrinfo",
            "resolves to a non-public address",
            "DNS resolution failed",
            'rstrip(".")',
            "parsed.username",
            "parsed.password",
            "parsed.port",
            "must not use non-public hosts",
            'char == "\\\\"',
        ]:
            assert expected in url_safety, f"url_safety.py missing {expected}"

        search_service = (ROOT / "app" / "search_service.py").read_text(encoding="utf-8")
        for expected in [
            "PreparedSearch",
            "cached_search",
            "apply_mall_visibility_policy",
            "mall_policy_overfetch",
            "resolve_product_price",
            "result_score_to_unit_interval",
            "sanitize_log_entry",
            "redacted-email",
            "redacted-secret",
            "access[_-]?token",
            "client[_-]?secret",
            "redacted-image",
            "IMAGE_DATA_URL_PATTERN",
            "image-base64",
            "mssql-connection-string",
            "MAX_LOG_TAIL_LIMIT",
            "normalize_tail_limit",
            "read_jsonl_tail",
            "read_tail_lines",
            "read_reverse_lines",
            'source.seek(0, 2)',
            "write_errors",
            "last_write_error_type",
            "_record_write_error",
            "CACHE_POLICY_VERSION",
            "collapse_product_groups",
            "cache_policy_fingerprint",
            "cache_policy_fingerprint_digest",
            "_cache_policy_token",
            "return token",
            "_policy_fingerprint_tokens.setdefault",
            "SearchCacheMissInFlight",
            "identical search is still running",
            "normalize_query_text",
            "normalized_query",
            "infer_category_intents",
            "inferred_categories",
            "image_perceptual_hash",
            '"index_name": settings.index_name',
            '"marqo_model": settings.marqo_model',
            '"engine_backend": settings.engine_backend',
            "absolute_product_url",
            "safe_absolute_http_url(product.image_url)",
            "safe_absolute_http_url(mall.product_url_template.format",
            "safe_absolute_http_url(settings.product_url_template.format",
            "safe_absolute_http_url(product_url)",
            "safe_absolute_http_url(urljoin",
            "product_url_base",
            "urljoin",
            "safe_absolute_http_url(f\"{parsed.scheme}://{parsed.netloc}/\")",
            "parsed.scheme or parsed.netloc",
            "parsed.scheme.lower() == base_parsed.scheme.lower()",
            "parsed.netloc.lower() == base_parsed.netloc.lower()",
        ]:
            assert expected in search_service, f"search_service.py missing {expected}"

        query_normalizer = (ROOT / "app" / "query_normalizer.py").read_text(encoding="utf-8")
        for expected in ["QUERY_REPLACEMENTS", "normalize_query_text", "build_search_query", "텐블러", "포스트잇"]:
            assert expected in query_normalizer, f"query_normalizer.py missing {expected}"

        category_intent = (ROOT / "app" / "category_intent.py").read_text(encoding="utf-8")
        for expected in ["CATEGORY_INTENT_RULES", "infer_category_intents", "append_inferred_categories", "수건", "타올"]:
            assert expected in category_intent, f"category_intent.py missing {expected}"

        main_module = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        assert "normalize_tail_limit(limit)" in main_module, "main.py must bound admin log/metrics limit"
        assert "/admin/search-insights" in main_module, "main.py missing search insights admin endpoint"
        assert "/admin/sync-log" in main_module, "main.py missing sync log admin endpoint"
        assert "build_search_insights_report" in main_module, "main.py must use search insights report builder"

        concurrency_module = (ROOT / "app" / "concurrency.py").read_text(encoding="utf-8")
        for expected in ["SearchExecutionGate", "SearchQueueFull", "ImageSearchGate", "ImageSearchQueueFull", "BoundedSemaphore"]:
            assert expected in concurrency_module, f"concurrency.py missing {expected}"

        metrics_module = (ROOT / "app" / "metrics.py").read_text(encoding="utf-8")
        for expected in [
            "process_summary",
            "system_summary",
            "memory_rss_bytes",
            "system_cpu_percent",
            "cached_search_events",
            "safe_engine_health",
            "error_type",
            "build_operational_alerts",
            "engine_unhealthy",
            "sync_product_failures",
            "sync_batch_failures",
            "sync_lock_contention",
            "search_cache_invalidation_failures",
            "rate_limited_requests",
            "image_search_queue_full",
            "search_queue_full",
            "disk_usage_high",
            "cache_hit_rate_percent",
            "summarize_sync_entries",
            "normalize_search_cache_status",
            "search_singleflight_summary",
            "image_search_queue_summary",
            "search_execution_queue_summary",
            "product_failed_events",
            "batch_failed_events",
            "sync_lock_busy_events",
            "cache_invalidation_failed_events",
            "batch_failed_action_counts",
            "metrics_to_prometheus",
            "haeorum_engine_up",
            "haeorum_sync_recent_lock_busy_events",
            "haeorum_sync_recent_search_cache_clear_failed_events",
            "haeorum_search_cache_clear_error_events",
            "haeorum_search_cache_lock_contention_events",
            "haeorum_search_queue_enabled",
            "haeorum_search_queue_full_events",
            "haeorum_search_cache_max_entries",
            "haeorum_search_cache_evictions",
            "haeorum_search_singleflight_wait_timeouts",
            "haeorum_image_search_queue_enabled",
            "haeorum_image_search_queue_full_events",
            "haeorum_log_write_error_events",
            "haeorum_sync_recent_batch_failed_action_events",
            "haeorum_operational_alerts",
        ]:
            assert expected in metrics_module, f"metrics.py missing {expected}"

        image_validation = (ROOT / "app" / "image_validation.py").read_text(encoding="utf-8")
        for expected in [
            "quality_warnings",
            "analyze_image_quality",
            "transparent_or_cutout_background",
            "normalize_declared_mime_type",
            "estimated_base64_decoded_size",
            "read_upload_bytes_limited",
            "DEFAULT_UPLOAD_READ_CHUNK_SIZE",
            "DEFAULT_MULTIPART_FORM_OVERHEAD_BYTES",
            "DEFAULT_JSON_BODY_OVERHEAD_BYTES",
            "DEFAULT_MIN_IMAGE_DIMENSION",
            "EXIF_ORIENTATION_TAG",
            "ImageOps.exif_transpose",
            "perceptual_hash",
            "compute_average_hash",
            "validate_min_image_dimensions",
            "validate_multipart_content_length",
            "multipart Content-Length is required",
            "validate_json_image_content_length",
            "image/jpg",
        ]:
            assert expected in image_validation, f"image_validation.py missing {expected}"
        main_module = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        assert "read_upload_bytes_limited" in main_module, "main.py must stream-limit multipart image uploads"
        assert "validate_multipart_content_length" in main_module, "main.py must reject oversized multipart bodies before form parsing"
        assert "validate_json_image_content_length" in main_module, "main.py must reject oversized JSON image bodies before parsing"
        assert "read_multipart_form" in main_module, "main.py must normalize malformed multipart parsing errors"
        assert "invalid multipart form body" in main_module, "main.py must return clear malformed multipart errors"
        assert "reject_unsupported_multipart_fields(form)" in main_module, "main.py must reject unsupported multipart fields"
        assert "unsupported_multipart_field_names" in main_module, "main.py must use the shared multipart field contract"
        assert "api_key query parameter is not supported" in main_module, "main.py must reject api_key query parameters"
        assert "public_api_key_field_names(request.query_params)" in main_module, "main.py must reject api_key query parameter presence, including empty values"
        assert "reject_body_api_key_fields(payload)" in main_module, "main.py must reject public API keys in request bodies"
        assert 'or request.query_params.get("api_key")' not in main_module, "main.py must not accept api_key from query parameters"

        image_probe = (ROOT / "app" / "image_probe.py").read_text(encoding="utf-8")
        for expected in [
            "safe_absolute_http_url(product.image_url)",
            "image URL must be an absolute safe http(s) URL",
            "SafeImageRedirectHandler",
            "UnsafeImageRedirectError",
            "UnsafeImageHostError",
            "validate_image_url_resolves_to_public_network",
            "socket.getaddrinfo",
            "non-public address",
            "redirected to a target that is not an absolute safe http(s) URL",
            "image_url_quality_warnings",
            "possible_watermark",
            "placeholder_or_sample_image",
        ]:
            assert expected in image_probe, f"image_probe.py missing {expected}"

        sync_worker = (ROOT / "app" / "sync_worker.py").read_text(encoding="utf-8")
        for expected in ["resolve_sync_since", "latest_successful_result", "last_started_at", "make_search_cache"]:
            assert expected in sync_worker, f"sync_worker.py missing {expected}"

        sync_module = (ROOT / "app" / "sync.py").read_text(encoding="utf-8")
        for expected in [
            "fetch_products",
            "SyncOperationLock",
            "SyncAlreadyRunning",
            "sync_lock_path",
            "sync_lock_stale_seconds",
            "read_sync_lock_payload",
            "read_reverse_lines(self.path)",
            "sync_lock_age_seconds",
            "sync_lock_owner_is_running",
            "acquire_sync_lock",
            "source_product_missing",
            "fetch_product",
            "write_image_warning",
            "image_quality_warning",
            "write_product_event",
            "write_cache_invalidation",
            "_clear_search_cache_if_mutated",
            "search_cache_cleared",
            "search_cache_clear_failed",
            "sync_product_failed",
            "delete_from_index",
            "upsert_to_index",
            "product_id is required",
            "product_name is required",
            "clean_readonly_query",
            "validate_readonly_query",
            "build_wrapped_mssql_query",
            "split_cte_query",
            "find_top_level_keyword",
            "parse_sync_datetime",
            "mssql_sync_datetime_param",
            "updated_at for product",
            "product CSV does not exist",
            "product_id_column",
            "updated_at_column",
        ]:
            assert expected in sync_module, f"sync.py missing {expected}"

        sql_safety_module = (ROOT / "app" / "sql_safety.py").read_text(encoding="utf-8")
        for expected in [
            "FORBIDDEN_SQL_PATTERN",
            "SQL_COMMENT_PATTERN",
            "validate_readonly_query",
            "clean_readonly_query",
            "MSSQL query must not contain comments",
            "MSSQL query must contain a single SELECT statement",
            "MSSQL query must start with SELECT or WITH",
            "MSSQL query contains a forbidden keyword",
        ]:
            assert expected in sql_safety_module, f"sql_safety.py missing {expected}"

        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        assert "psutil" in requirements, "requirements.txt missing psutil"
        requirements_mssql = (ROOT / "requirements-mssql.txt").read_text(encoding="utf-8")
        assert "pyodbc" in requirements_mssql, "requirements-mssql.txt missing pyodbc"

        api_smoke_test = (ROOT / "scripts" / "api_smoke_test.py").read_text(encoding="utf-8")
        for expected in [
            "/admin/metrics",
            "/admin/metrics.prom",
            "/admin/search-insights",
            "--origin",
            "\"Origin\"",
            "cors_preflight",
            "invalid_cors_preflight_rejected",
            "click_log_cors_preflight",
            "invalid_click_log_cors_preflight_rejected",
            "request_preflight",
            "Access-Control-Request-Method",
            "multipart_image_search",
            "site_id_search",
            "conflicting_site_id_rejected",
            "site_id_multipart_image_search",
            "conflicting_multipart_site_id_rejected",
            "unsupported_multipart_field_rejected",
            "site_id_click_log",
            "conflicting_click_site_id_rejected",
            "invalid_multipart_image_rejected",
            "damaged_multipart_image_rejected",
            "oversized_json_image_rejected",
            "small_json_image_rejected",
            "small_multipart_image_rejected",
            "make_damaged_png_bytes",
            "oversized_multipart_image_rejected",
            "malformed_multipart_rejected",
            "make_oversized_upload_bytes",
            "make_oversized_json_image_body",
            "oversized-upload-mb",
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
            "malformed_search_json_rejected",
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
            "click_product_url_template_prefix_mismatch_rejected",
            "click_product_url_product_id_mismatch_rejected",
            "https://user:pass@example.test/product/smoke-product",
            "https://token@example.test/product/smoke-product",
            "malformed_click_json_rejected",
            "click_log_rate_limited",
            "--expect-click-rate-limit",
            "click-rate-limit-probe-count",
            "invalid_admin_key_rejected",
            "admin_query_key_alias_rejected",
            "admin_mutation_endpoints_protected",
            "/admin/reindex/__smoke_product__",
            "/admin/reindex/__smoke/product__",
            "/admin/reindex-product",
            "__smoke/product?query#fragment__",
            "/admin/product/__smoke_product__",
            "/admin/product/__smoke/product__",
            "/admin/delete-product",
            "search_log",
            "sync_log",
            "error_log",
            "sensitive_log_redaction",
            "sensitive_log_redaction_ok",
            "assert_no_sensitive_log_markers",
            "search_insights",
            "alerts",
            "prometheus_metrics",
            "request_text",
            "request_raw_json",
            "haeorum_engine_up",
            "--admin-key is required",
            "request_multipart",
            "request_raw_multipart",
            "multipart/form-data",
            "filename=\"{filename}\"",
            "normalize_http_base_url",
            "normalize_http_origin",
            "normalize_public_http_base_url",
            "normalize_public_http_origin",
            "open_public_http_request",
            "allow_local_target",
            "--allow-local-target",
            "target_validation",
            "failed_validation_report",
            "validate_search_response",
            "sync_status_engine",
            "sync_status_index",
            "sync status engine must be marqo",
            "REQUIRED_RESULT_FIELDS",
            "REQUIRED_META_FIELDS",
            "top should contain 1 to 3 products",
            "response should include related items",
            "response should include suggested_categories",
            "expected_mall_id",
            "requested_mall_id",
            "meta_mall_id",
            "result_mall_ids",
            "meta mall_id must match requested mall_id",
            "mall_id must match requested mall_id",
            "inferred_categories",
            "image_perceptual_hash",
        ]:
            assert expected in api_smoke_test, f"api_smoke_test.py missing {expected}"

        operational_readiness = (ROOT / "scripts" / "operational_readiness.py").read_text(encoding="utf-8")
        for expected in [
            "api_smoke",
            "marqo_resource",
            "marqo-resource.json",
            "check_marqo_resource",
            "server_preflight",
            "server-preflight.json",
            "check_server_preflight",
            "server_preflight_check.py",
            "env_preflight",
            "env-check.json",
            "check_env_preflight",
            "env_check.py",
            "--env-check-report",
            "product_url_template",
            "mall_security",
            "gemini_embedding_config",
            "embedding_config_check",
            "redis_required_for_scale",
            "cache_ttl_required_for_scale",
            "trusted_proxy_ips",
            "sync_interval_hourly",
            "sync_alert_webhook",
            "env_file_permissions",
            "expected-odbc-driver",
            "odbc_driver",
            "cors_preflight",
            "invalid_cors_preflight_rejected",
            "click_log_cors_preflight",
            "invalid_click_log_cors_preflight_rejected",
            "multipart_image_search",
            "site_id_search",
            "conflicting_site_id_rejected",
            "site_id_multipart_image_search",
            "conflicting_multipart_site_id_rejected",
            "unsupported_multipart_field_rejected",
            "site_id_click_log",
            "conflicting_click_site_id_rejected",
            "invalid_multipart_image_rejected",
            "damaged_multipart_image_rejected",
            "oversized_json_image_rejected",
            "small_json_image_rejected",
            "small_multipart_image_rejected",
            "oversized_multipart_image_rejected",
            "malformed_multipart_rejected",
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
            "malformed_search_json_rejected",
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
            "click_product_url_template_prefix_mismatch_rejected",
            "click_product_url_product_id_mismatch_rejected",
            "malformed_click_json_rejected",
            "click_log_rate_limited",
            "configured_click_rate_limit_plus_1",
            "invalid_admin_key_rejected",
            "admin_query_key_alias_rejected",
            "admin_mutation_endpoints_protected",
            "prometheus_metrics",
            "search_log",
            "sync_log",
            "error_log",
            "sensitive_log_redaction",
            "operational_https_url_problems",
            "is_non_public_host",
            "base_url_non_local",
            "origin_https",
            "search_insights",
            "mssql_view",
            "image_urls",
            "quality_report",
            "response_time_ok",
            "custom_cases",
            "skipped_case_checks",
            "case_contract",
            "quality_case_contract",
            "expected_low_confidence",
            "missing_low_confidence_case",
            "missing_text_variant_case",
            "duplicate_case_names",
            "text_variant_case_count",
            "normalize_quality_case_contract",
            "image_cases_with_supplied_source",
            "mixed_cases_with_supplied_source",
            "csv_poc_index",
            "csv-index.json",
            "check_csv_index",
            "--csv-index-report",
            "mall_config_build",
            "mall-config-build.json",
            "check_mall_config_build",
            "--mall-config-build-report",
            "raw_config_embedded",
            "generated_api_key_count",
            "mall_config_build_api_key_hashes_mismatch",
            "persistent_index",
            "minimum_poc_products",
            "marqo_url_problems",
            "marqo_model",
            "load_mixed_traffic_850_active_users",
            "--mall-sample-size 50",
            "--mall-sample-strategy spread",
            "api_scale_comparison",
            "api-scale.json",
            "check_api_scale",
            "check_api_scale_response_mall_identity",
            "check_load_response_mall_identity",
            "evidence_freshness",
            "generated_at_stale",
            "--max-evidence-age-days",
            "response_mall_identity_problems",
            "response_contract.mall_id_mismatch_count",
            "response_contract.product_url_prefix_required",
            "expected_product_url_prefix_counts",
            "check_api_scale_server_metrics",
            "comparison.base_url",
            "--api-scale-report",
            "load_compare.py",
            "representative_mall_sites",
            "resultFieldsRendered",
            "smallImageRejected",
            "damagedImageRejected",
            "rateLimitErrorClearsStaleResults",
            "keyboardTrapCyclesFocus",
            "REQUIRED_REPRESENTATIVE_SITE_CHECKS",
            "REPRESENTATIVE_PRODUCT_URL_RULE_CHECKS",
            "missing_product_url_evidence",
            "site_config",
            "text_category_refetch",
            "text_product_url_rule",
            "image_product_url_rule",
            "mixed_click_log",
            "DEFAULT_EVIDENCE_FILENAMES",
            "--evidence-dir",
            "command_hint",
            "missing-commands-output",
            "missing-commands-shell",
            "missing-commands-project-root",
            "missing-commands-evidence-dir",
            "to_missing_commands_script",
            "to_missing_commands_bash",
            "command_lines_for_item",
            "step_execution_label",
            "[haeorum-evidence]",
            "PLACEHOLDER_OPEN='<'",
            "grep -Ev",
            'mkdir -p "$EVIDENCE_DIR"',
            "$PlaceholderOpen = '<'",
            "New-Item -ItemType Directory -Force -Path $EvidenceDir",
            "read_json_text",
            "utf-8-sig",
            "utf-16",
            "Push-Location",
            "normalize_report_path",
            "normalize_script_project_root",
            "join_evidence_output_path",
            "sanitize_report_for_deployment",
            "rewrite_evidence_paths",
            "rewrite_project_paths",
            "security",
            "public_base_url",
            "cors_origins_https",
            "cors_covers_allowed_origins",
            "product_url_templates_https",
            "product_url_templates_safe_public",
            "production_env",
            "production_search_engine",
            "sync_interval_hourly",
            "logrotate_config",
            "systemd_restart_policy",
            "nginx_upstream_resilience",
            "nginx_forwarded_for_safety",
            "check_load_thresholds",
            "check_load_server_metrics",
            "check_load_image_source",
            "check_load_mall_identity_coverage",
            "MIN_MIXED_TRAFFIC_MALL_RESPONSE_DISTRIBUTION_RATIO",
            "mall_identity_ok",
            "mall_identity_problems",
            "multi_mall_identity_ok",
            "mall_identity.sampled_mall_ids_missing_response_counts",
            "mall_identity.sampled_mall_ids_underrepresented",
            "required_requests",
            "threshold_problems",
            "image_source_ok",
            "image_input.source",
            "image_input.sha256",
            "target_validation_ok",
            "target_validation_matches",
            "sync_status_engine_ok",
            "sync_status_index_ok",
            "api_smoke.sync_status.index",
            "server_metrics.delta.search_events",
            "server_metrics.delta.image_search_events",
            "server_metrics.delta.cache_clear_errors",
            "QWEN_QUERY_VECTOR_DELTA_SERVER_METRIC_FIELDS",
            "qwen_query_vector_server_metric_problems",
            "qwen_query_vector_runtime_text_max_entries_below_required",
            "qwen_query_vector_runtime_image_max_entries_below_required",
            "backend_marqo_total_elapsed_ms",
            "backend_qwen_total_elapsed_ms",
            "backend_marqo_total_request_body_bytes",
            "backend_qwen_total_request_body_bytes",
            "backend_transport_payload_problems",
            "backend_request_attempt_problems",
            "request_profile_problems",
            "request_profile",
            "server_metrics.delta.backend_marqo_request_attempts_zero",
            "server_metrics.delta.backend_qwen_request_attempts_zero",
            "server_metrics.delta.backend_marqo_request_attempts_below_unique_requests",
            "server_metrics.delta.backend_qwen_request_attempts_below_unique_image_inputs",
            "server_metrics.delta.backend_marqo_gzip_responses_zero",
            "server_metrics.delta.backend_marqo_response_body_bytes_not_below_decoded",
            "multi_server_metrics_missing",
            "server_metrics_run_log_coverage",
            "metric_coverage_meets",
            "search_events_below_successful_responses",
            "server_metrics",
            "REQUIRED_API_SMOKE_RESPONSE_CONTRACT_CHECKS",
            "missing_response_contract_evidence",
            "mssql_export",
            "poc_dataset",
            "check_mssql_export",
            "query_fingerprint",
            "query_fingerprint_problems",
            "mssql_query_fingerprint_mismatch",
            "batched_fetch",
            "fetch_batches",
            "max_fetch_batch_rows",
            "fingerprint_path_problems",
            "path_mismatch",
            "minimum_sample_rows",
            "active_missing_category_count",
            "active_negative_price_count",
            "check_poc_dataset",
            "selected_missing_product_url_count",
            "selected_missing_mall_id_count",
            "selected_unsafe_product_url_count",
            "selected_product_url_product_id_mismatch_count",
            "active_unsafe_product_url_count",
            "active_product_url_product_id_mismatch_count",
            "marqo_index_mismatch",
            "marqo_url_mismatch",
            "marqo_model_mismatch",
            "qwen_model_mismatch",
            "qwen_embedding_dimensions_mismatch",
            "collect_qwen_runtime_values",
            "marqo_resource_documents_below_csv_index",
            "marqo_document_counts",
            "api_base_url_mismatch",
            "api_origin_mismatch",
            "api_mall_id_mismatch",
            "api_base_url",
            "api_origin",
            "api_mall_id",
            "security_cors_missing_api_origins",
            "security_cors",
            "required_origins",
            "missing_origins",
            "enabled_mall_ids",
            "enabled_mall_ids_count",
            "enabled_mall_origins",
            "enabled_mall_origins_count",
            "enabled_mall_product_url_prefixes",
            "enabled_mall_product_url_prefixes_count",
            "enabled_mall_api_key_hashes",
            "enabled_mall_api_key_hashes_count",
            "representative_mall_id_not_enabled",
            "representative_origin_not_allowed_for_mall",
            "representative_api_key_not_matching_mall_config",
            "representative_product_url_not_matching_mall_template",
            "mall_identity",
            "representative_malls_not_enabled",
            "representative_origin_mismatches",
            "representative_api_key_mismatches",
            "representative_product_url_mismatches",
            "collect_required_cors_origins",
            "collect_security_cors_origins",
            "collect_enabled_mall_ids",
            "collect_enabled_mall_origins",
            "collect_enabled_mall_product_url_prefixes",
            "collect_enabled_mall_api_key_hashes",
            "collect_representative_mall_ids",
            "collect_representative_api_key_hashes",
            "collect_representative_product_urls",
            "evidence_url_matches_prefix",
            "collect_api_target_values",
            "quality_report.source.index_name",
            "quality_report.source.marqo_url",
            "mssql_query",
            "streamed_product_csv",
            "retained_product_rows",
            "csv_rows_written",
            "case_source_fingerprint_problems",
            "case_image_fingerprints",
            "case_image_fingerprint_problems",
            "digest_duplicate",
            "path_duplicate",
            "csv_poc_index.marqo_url",
            "marqo_resource.marqo_url",
            "csv_poc_index.marqo_model",
            "marqo_resource.index",
            "custom_cases",
            "skipped_case_checks",
            "case_contract",
            "quality_case_contract",
            "QUALITY_CASE_MIN_TYPE_COUNTS",
            "QUALITY_CASE_MIN_RESULTS",
            "duplicate_case_names",
            "image_cases_with_supplied_source",
            "mixed_cases_with_supplied_source",
            "unsafeApiBaseUrlRejected",
            "unsafeMallIdRejected",
            "cssSpecialIdSelectorFallback",
            "complexCssSpecialIdSelectorFallback",
            "ambiguousExplicitSelectorRejected",
            "required_widget_config_fields",
            "--cases <quality_cases_file>",
            "markdown-output",
        ]:
            assert expected in operational_readiness, f"operational_readiness.py missing {expected}"
        command_hints_source = operational_readiness.split("COMMAND_HINTS = {", 1)[1]
        quality_hint_source = command_hints_source.split('"quality_report":', 1)[1].split('"csv_poc_index":', 1)[0]
        assert "--min-products 300" in quality_hint_source, "operational_readiness.py quality command must keep PoC minimum explicit"
        csv_index_source = operational_readiness.split("def check_csv_index", 1)[1].split("\ndef check_mall_config", 1)[0]
        assert 'engine == "marqo"' in csv_index_source, "operational_readiness.py csv index check must require Marqo"
        assert 'problems.append("engine")' in csv_index_source, "operational_readiness.py csv index check must fail non-Marqo reports"
        assert '"engine_ok": engine_ok' in csv_index_source, "operational_readiness.py csv index details must expose engine_ok"

        collect_operational_evidence = (ROOT / "scripts" / "collect_operational_evidence.py").read_text(encoding="utf-8")
        for expected in [
            "Collect Haeorum AI Search operational evidence",
            "operational_readiness",
            "api_smoke",
            "mssql_view",
            "load_mixed_traffic_850_active_users",
            "api_scale_comparison",
            "env_preflight",
            "representative_mall_sites",
            "SENSITIVE_OPTIONS",
            "redact_command",
            "SENSITIVE_ENV_NAME_PATTERN",
            "SECRET_ASSIGNMENT_PATTERN",
            "sanitize_process_output",
            "sensitive_values_for_command",
            "missing_config",
            "missing_input_files",
            "invalid_input_files",
            "input_file_validation_problems",
            "primary_mall_validation_context",
            "product_csv_file_problems",
            "POC_SOURCE_MATCH_FIELDS",
            "changed source fields from products_csv",
            "product_csv_pair_problem_messages",
            "CsvProductSource",
            "builtin_sample_dataset_profile",
            "active_products",
            "active_unsafe_product_url_count",
            "active_product_url_product_id_mismatch_count",
            "mall_config_source",
            "mall_config_source_file_problems",
            "build_mall_config_from_csv",
            "mall_config_build",
            "validate_readonly_query",
            "validate_mssql_connection_string_value",
            "TrustServerCertificate=no",
            "ApplicationIntent=ReadOnly",
            "mssql_query",
            "mall_config_file_problems",
            "quality_cases_file_problems",
            "quality_case_contract_problem_messages",
            "configured mall_id",
            "active products for configured mall_id",
            "image_path is reused",
            "image_path content duplicates",
            "representative_sites_config_problems",
            "representative_site_widget_probe_problem_messages",
            "widget_probe_source",
            "require_saved_widget_probe_sources",
            "saved PC/mobile coverage incomplete",
            "saved HTML source file is reused",
            "saved HTML source content duplicates",
            "has_explicit_probe_source",
            "data_auto_init_ready=false",
            "preview validation failed",
            "validate_preview_html_body",
            "data_auto_init_script_multiple",
            "configured api_key does not match mall_config api_key",
            "does not match configured load.image_file",
            "service_env_file_problems",
            "build_env_check_report",
            "security_input_file_problems",
            "check_nginx_client_max_body_size",
            "check_systemd_sync_worker_service",
            "check_logrotate_config",
            "load_report_file_problems",
            "load_image_file_problems",
            "validate_image_bytes",
            "summarize_load_report",
            "api_server_count",
            "response_contract.engine_counts",
            "response_contract.product_url_prefix_required",
            "product URL prefix evidence from mall_config",
            "--mall-config <mall_config.json>",
            "--mall-sample-size",
            "load.mixed_traffic.mall_sample_size",
            "non_marqo_engine_responses",
            "server_metrics.after.snapshot.engine_backend",
            "required_files",
            "Missing Inputs",
            "required input file is missing",
            "required input file is invalid",
            "capture_stdout_to",
            "evidence_file",
            "evidence_exists",
            "evidence file was not created",
            "failed_steps",
            "skipped_steps",
            "output_option_value",
            "normalize_evidence_dir",
            "load_env_file",
            "collection_environment",
            "child_process_environment",
            "COLLECTOR_ENV_FILE_MAX_MODE",
            "collector_env_file_permissions",
            "APP_ENV_PREFIX",
            "load_env_file(env_file)",
            "dict(os.environ)",
            "key.upper().startswith(APP_ENV_PREFIX)",
            "env=dict(environment)",
            "environment is None",
            "PLACEHOLDER_PATTERNS",
            "is_missing_config_value",
            "urlparse",
            "is_safe_http_url_config",
            "is_non_public_host",
            "is_local_http_host",
            "is_link_or_unspecified_http_host",
            'rstrip(".")',
            "is_link_local",
            "require_https",
            "allow_local",
            "origin_only",
            "absolute HTTPS non-local API base URL",
            "HTTPS browser origin",
            "Marqo URL",
            "replace-with",
            "env-file",
            "requirements-audit-output",
            "write_requirements_audit_report",
            "requirements-blocker-checklist-output",
            "requirements_audit_to_blocker_checklist",
            "blocker-checklist-project-root",
            "blocker-checklist-evidence-dir",
            "missing_commands",
            "env_check.env_file",
            "service_env_values",
            "sync_alert_webhook_is_configured",
            "HAEORUM_SYNC_ALERT_WEBHOOK_URL",
            "check_sync_alert_webhook_url",
            '"--env-file"',
            "ready_to_execute",
            "evidence_complete",
            "evidence_freshness",
            "evidence_shape",
            "evidence_content",
            "EVIDENCE_REQUIRED_KEYS",
            "IMAGE_FILE_EVIDENCE_STEPS",
            "wrong-shape",
            "content-invalid",
            "generated_at_stale",
            "--max-existing-evidence-age-days",
            "simulation_only",
            "simulation_marker",
            "dry_run",
            "produced evidence is invalid, wrong-shape, content-invalid, dry-run, stale, simulated, local-only, or ok is not true",
            "execution_runbook",
            "deployment_commands",
            "DEPLOYMENT_CONFIG_PATH",
            "DEPLOYMENT_ENV_PATH",
            "sanitize_collection_report_for_deployment",
            "rewrite_collection_evidence_dir",
            "Execution Runbook",
            "evidence-collection.json",
            "requirements-blockers.md",
            "blocker-checklist-output",
            "config_resolution_hint",
            "file_resolution_hint",
            "file_validation_context",
            "Resolution",
            "dry run only; command was not executed",
            "dry-run",
            "stop-on-failure",
            "quality.cases_file",
            "quality_cases_file",
            "--cases",
            "load.image_file",
            "input_preparation.mssql_export.fetch_size",
            "--fetch-size",
            "readiness_check_content_problems",
            "check_mssql_export",
            "check_poc_dataset",
            "check_image_urls",
            "check_csv_index",
            "check_mall_config",
            "check_marqo_resource",
            "check_load",
            "load_evidence_content_problems",
            "load_text_100_concurrent",
            "quality_report_operational_content_problems",
            "quality_report.operational_quality",
            "quality_report.image_cases_with_file_source",
            "quality_report.case_image_fingerprints",
            "quality_report.source.engine",
            "marqo.gemini_embedding_url",
            "marqo.embedding_url",
            "image_embedding_probe",
            "embedding_contract",
        ]:
            assert expected in collect_operational_evidence, f"collect_operational_evidence.py missing {expected}"

        prepare_operational_bundle = (ROOT / "scripts" / "prepare_operational_bundle.py").read_text(encoding="utf-8")
        for expected in [
            "Prepare Haeorum AI Search operational config and evidence templates",
            "TEMPLATE_FILES",
            "operational-evidence.config.json",
            "representative-sites.config.json",
            "quality-cases.json",
            "real PoC text, image, and mixed-search cases",
            "load.image_file",
            "haeorum-ai-search.env",
            "0600",
            "0640",
            "malls.json",
            "query-synonyms.json",
            "Expand `malls.json` to 1,700 enabled production malls",
            "haeorum-ai-sync.service",
            "haeorum-ai-reindex.timer",
            "deploy/reference",
            "tools/widget_integration_probe.py",
            "representative_sites.require_saved_widget_probe_sources=true",
            "data-hai-auto-init",
            "compose-haeorum-marqo.yaml",
            "compose-haeorum-gemini.yaml",
            "compose-haeorum-existing-8gb.yaml",
            "compose-haeorum-demo.yaml",
            "requirements-mssql.txt",
            "OPERATIONS.md",
            "INTEGRATION.md",
            "REQUIREMENTS_TRACE.md",
            "runtime-stack-gemini-marqo.md",
            "production-handoff-checklist.md",
            "operational-risk-register.md",
            "production-incident-runbook.md",
            "server82-runbook.md",
            "server-db-intake.md",
            "server_db_intake_check.py",
            "compose_exposure_check.py",
            "ready_for_env_and_server_preflight",
            "Marqo + Gemini embedding API",
            "v_ai_search_products_template.sql",
            "widget_integration_probe.py",
            "read-only MSSQL View",
            "standalone bundle build context",
            "base_url",
            "absolute HTTPS non-local API URLs",
            "without credentials",
            "query strings",
            "fragments",
            "CHECKLIST.md",
            "collect_operational_evidence.py",
            "blocker-checklist-source",
            "Current operational blocker checklist",
            "missing-commands-source",
            "Current missing operational evidence command script",
            "local-acceptance-source",
            "Latest local acceptance JSON report",
            "server-db-intake-source",
            "Latest server/DB intake validation JSON report",
            "compose-exposure-source",
            "Latest Docker Compose exposure validation JSON report",
            "copy_json_without_command_output",
            "strip_command_output_fields",
            "stdout_tail",
            "stderr_tail",
            "/var/log/haeorum-ai-search/local-acceptance.json",
            "requirements-audit-source",
            "Latest requirements audit JSON report",
            "/var/log/haeorum-ai-search/requirements-audit.json",
            "operational-readiness-source",
            "Latest operational readiness JSON report",
            "/var/log/haeorum-ai-search/operational-readiness.json",
            "evidence-collection-source",
            "Latest evidence collection dry-run or plan JSON report",
            "/var/log/haeorum-ai-search/evidence-collection-plan.json",
            "requirements-blocker-checklist-output",
            "requirements-blockers.md",
            "missing-evidence.sh",
            "sudo install",
            "operational-evidence.env` with mode `0600`",
            "OPTIONAL_MANAGED_TARGETS",
            "clean_stale_bundle_files",
            "expected_bundle_targets",
            "cleaned",
        ]:
            assert expected in prepare_operational_bundle, f"prepare_operational_bundle.py missing {expected}"

        local_acceptance = (ROOT / "scripts" / "local_acceptance.py").read_text(encoding="utf-8")
        for expected in [
            "SOURCE_FINGERPRINT_PATTERNS",
            "build_source_fingerprint",
            "source_fingerprint_files",
            "source_fingerprint",
            "tests/**/*.py",
            ".env.example",
            ".gitignore",
            "sql/**/*.sql",
        ]:
            assert expected in local_acceptance, f"local_acceptance.py missing {expected}"

        prepare_handoff = (ROOT / "scripts" / "prepare_handoff.py").read_text(encoding="utf-8")
        for expected in [
            "Regenerate local acceptance, operational blocker reports, and the handoff bundle",
            "build_command_plan",
            "local_acceptance",
            "local_quality_report",
            "local_widget_dom_report",
            "local_csv_index_report",
            "server_db_intake_template",
            "server-db-intake-check.json",
            "server-db-intake-check.md",
            "compose_exposure_check",
            "compose-exposure-check.json",
            "compose-exposure-check.md",
            "operational_simulation",
            "operational-simulation.json",
            "sync-lifecycle.json",
            "sync_lifecycle_ok",
            "quality-report.json",
            "widget-dom.json",
            "csv-index.json",
            "csv-index.md",
            "evidence_collection_dry_run",
            "operational_readiness",
            "requirements_audit",
            "prepare_operational_bundle",
            "operational_bundle_check",
            "missing-evidence.sh",
            "handoff_ok",
            "operational_signoff_ok",
            "allowed_exit_codes",
            "deployment-project-root",
            "deployment-evidence-dir",
            "include-command-output",
            "compact_command_result",
            "PROJECT_ROOT_DISPLAY",
        ]:
            assert expected in prepare_handoff, f"prepare_handoff.py missing {expected}"

        operational_bundle_check = (ROOT / "scripts" / "operational_bundle_check.py").read_text(encoding="utf-8")
        for expected in [
            "check_blocker_checklist_paths",
            "check_blocker_resolution_commands",
            "check_missing_evidence_script_paths",
            "check_deployment_reference_files",
            "check_intake_runtime_handoff_docs",
            "check_operational_url_config",
            "check_quality_cases_template",
            "quality_cases_template",
            "image_path_prefix",
            "minimum_type_counts",
            "positive search cases must set expected_min_results at least 3",
            "expected_low_confidence=true",
            "typo_or_synonym",
            "text_variant_case_count",
            "bundle template must not embed base64/data URL images",
            "is_safe_bundle_http_url",
            "is_non_public_host",
            "is_local_bundle_http_host",
            "is_link_or_unspecified_bundle_http_host",
            'rstrip(".")',
            "is_link_local",
            "require_https",
            "allow_local",
            "absolute HTTPS non-local API base URL",
            "urlparse",
            "deployment_reference_files",
            "deploy/reference files are review-only and must not be installed",
            "check_optional_handoff_reports",
            "optional_handoff_reports",
            "find_prohibited_local_paths",
            "PROHIBITED_LOCAL_PATH_PATTERNS",
            "UNRESOLVED_OPERATIONAL_PLACEHOLDERS",
            "REQUIRED_ACCEPTANCE_INNER_CHECKS",
            "check_fastapi_runtime_routes",
            "stdout_json_summary",
            "check_names",
            "acceptance_check_count",
            "missing_acceptance_inner_checks",
            "build_source_fingerprint",
            "source_fingerprint",
            "source_fingerprint_match",
            "current source tree",
            "find_command_output_fields",
            "command_output_fields",
            "stdout_tail",
            "stderr_tail",
            "scripts/mssql_export_csv.py",
            "scripts/poc_dataset_builder.py",
            "scripts/mall_config_builder.py",
            "security.sync_alerting_configured",
            "--api-server-count 1",
            "--api-server-count 2",
            "<quality_report.md>",
            "<env_check.md>",
            'DEPLOYMENT_PROJECT_ROOT = "/opt/haeorum-ai-search"',
            'DEPLOYMENT_EVIDENCE_DIR = "/var/log/haeorum-ai-search"',
            "PROJECT_ROOT='",
            "EVIDENCE_DIR='",
            "PLACEHOLDER_OPEN='<'",
            'mkdir -p "$EVIDENCE_DIR"',
            "[haeorum-evidence]",
            "$ProjectRoot = '",
            "$EvidenceDir = '",
            "$PlaceholderOpen = '<'",
            "New-Item -ItemType Directory -Force -Path $EvidenceDir",
            "sudo install -m 0640 haeorum-ai-search.env /etc/haeorum-ai-search/haeorum-ai-search.env",
            "sudo install -m 0640 quality-cases.json /etc/haeorum-ai-search/quality-cases.json",
            "quality.cases_file",
            "load_image_file",
            "server-db-intake.md",
            "tools/server_db_intake_check.py",
            "tools/compose_exposure_check.py",
            "ready_for_env_and_server_preflight",
            "Marqo + Gemini embedding API",
            "127.0.0.1:${HAEORUM_AI_SEARCH_PORT:-8000}:8000",
            "127.0.0.1:${MARQO_PORT:-8882}:8882",
        ]:
            assert expected in operational_bundle_check, f"operational_bundle_check.py missing {expected}"

        requirements_audit = (ROOT / "scripts" / "requirements_audit.py").read_text(encoding="utf-8")
        for expected in [
            "evidence_collection_report",
            "operational_01_sync_failure_alerting",
            "동기화 실패 알림이 운영에서 구성된다",
            "operational_02_mssql_readonly_no_write",
            "기존 MSSQL 원본 DB는 read-only로 접근하고 쓰기 작업을 하지 않는다",
            "operational_03_sensitive_log_redaction",
            "검색/클릭/오류/동기화 로그는 개인정보와 secret 원문을 저장하지 않는다",
            "operational_04_public_admin_access_control",
            "공개 검색/클릭 API와 관리자 API는 API key, Origin, admin key 검증을 강제한다",
            "operational_05_rate_limit_cache_scale_controls",
            "이미지 검색 rate limit, 큐, 캐시, 850명 부하 확장성 증거를 확인한다",
            "operational_06_domain_filters_and_product_policy",
            "카테고리, 가격, 수량, 납기, 속성 필터와 상품 노출 정책을 검증한다",
            "operational_07_regular_sync_and_daily_reconciliation",
            "1시간 변경 동기화와 매일 새벽 전체 상태 검증 배치를 운영에서 확인한다",
            "haeorum-ai-reindex.timer",
            "architecture_01_search_engine_abstraction",
            "Marqo OSS 중단 리스크에 대비해 검색엔진 교체 가능한 구조를 둔다",
            "evidence_collection_status_counts",
            "evidence_collection_gate",
            "\"completion_ready\": summary[\"completion_ready\"]",
            "local_acceptance_gate",
            "source_fingerprint_match",
            "build_source_fingerprint",
            "current source tree",
            "mssql_export",
            "poc_dataset",
            "evidence_collection_steps",
            "evidence_collection_resolution_map",
            "collection_step_status",
            "collection_step_resolutions",
            "operational_blockers",
            "blocker_next_action",
            "command_hint",
            "resolution_summary",
            "Resolution",
            "Command",
            "to_blocker_checklist",
            "blocker-checklist-output",
            "blocker-checklist-project-root",
            "blocker-checklist-evidence-dir",
            "deployment_project_root",
            "deployment_evidence_dir",
            "render_blocker_text",
            "rewrite_evidence_paths",
            "Operational Blocker Checklist",
            "Readiness Command Template",
            "affected_requirements",
            "Operational Blockers",
            "summarize_collection_blockers",
            "Collection Blockers",
            "missing_input_files",
            "missing_config",
            "evidence-collection-report",
        ]:
            assert expected in requirements_audit, f"requirements_audit.py missing {expected}"

        operational_evidence_config = (ROOT / "contracts" / "operational_evidence.config.example.json").read_text(encoding="utf-8")
        for expected in [
            "api_key_env",
            "admin_key_env",
            "mssql_connection_string_env",
            "input_preparation",
            "mssql_export",
            "\"fetch_size\"",
            "poc_dataset",
            "env_check",
            "haeorum-ai-search.env",
            "representative_sites_config",
            "require_saved_widget_probe_sources",
            "load-mixed-traffic-1-api.json",
            "image_file",
            "nginx_config",
            "sync_alerting_configured",
            "missing_commands",
            "/opt/haeorum-ai-search",
            "/var/log/haeorum-ai-search",
            "gemini_embedding_url",
        ]:
            assert expected in operational_evidence_config, f"operational_evidence.config.example.json missing {expected}"

        operational_evidence_env = (ROOT / "contracts" / "operational_evidence.env.example").read_text(encoding="utf-8")
        for expected in [
            "HAEORUM_PUBLIC_API_KEY",
            "HAEORUM_ADMIN_API_KEY",
            "HAEORUM_MSSQL_READONLY_CONNECTION_STRING",
        ]:
            assert expected in operational_evidence_env, f"operational_evidence.env.example missing {expected}"

        server_preflight_check = (ROOT / "scripts" / "server_preflight_check.py").read_text(encoding="utf-8")
        for expected in [
            "ROLE_REQUIREMENTS",
            "server preflight",
            "require-docker",
            "require-compose",
            "require-pyodbc",
            "expected-odbc-driver",
            "MIN_DOCKER_VERSION",
            "min-open-files",
            "open_file_limit_soft",
            "python_modules",
            "odbc_driver",
            "host_resources",
            "linux_host",
        ]:
            assert expected in server_preflight_check, f"server_preflight_check.py missing {expected}"

        env_check = (ROOT / "scripts" / "env_check.py").read_text(encoding="utf-8")
        for expected in [
            "env preflight",
            "env-file",
            "required_variables",
            "admin_key",
            "cors_origins",
            "data_source",
            "mall_config_path",
            "mall_security",
            "load_mall_configs",
            "placeholder_api_key",
            "non_https_allowed_origins",
            "unsafe_allowed_origins",
            "non_https_product_url_template",
            "unsafe_product_url_template",
            "origins_missing_from_cors",
            "query_synonym_path",
            "mssql_connection_string",
            "validate_mssql_connection_string_value",
            "redis_required_for_scale",
            "redis_url",
            "validate_redis_url_value",
            "cache_ttl_required_for_scale",
            "cache_miss_coordination",
            "backend_retry_budget_seconds",
            "so Redis miss locks outlive Marqo timeout and retry budget",
            "rate_limit_capacity_for_required_load",
            "REQUIRED_MIXED_TRAFFIC_ACTIVE_USERS",
            "trusted_proxy_ips",
            "sync_interval_hourly",
            "MAX_PRODUCTION_SYNC_INTERVAL_SECONDS",
            "marqo_url",
            "validate_marqo_url_value",
            "product_url_template",
            "product_url_template_uses_safe_public_url",
            "product_url_template_uses_https",
            "safe_public",
            "unsafe_public_origins",
            "sync_alert_webhook",
            "env_file_permissions",
            "SERVICE_ENV_FILE_MAX_MODE",
            "check_secret_file_permissions",
            "check_sync_alert_webhook_url",
            'f"{provider}_embedding_config"',
            "gemini_proxy_provider_config",
            "GEMINI_AUTH_MODES",
            "GEMINI_PROXY_RATE_LIMIT_RPM",
            "GEMINI_MAX_RESPONSE_BYTES",
            'f"{provider}_query_runtime_cache_capacity"',
            "HAEORUM_GEMINI_EMBEDDING_URL",
            "HAEORUM_GEMINI_MODEL",
            "HAEORUM_GEMINI_EMBEDDING_DIMENSIONS",
            "HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES",
            "HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES",
            "separate_text_image_quotas",
            "boolean_settings",
            "BOOLEAN_TRUE_VALUES",
            "BOOLEAN_FALSE_VALUES",
            "reserved_adapter",
            "DEPLOYABLE_SEARCH_ENGINES",
            "numeric_settings",
            "INT_MAXIMUMS",
            "MAX_OPERATIONAL_SEARCH_OFFSET",
            "must be at most",
            "api_threadpool_capacity",
            "required_api_threadpool_tokens",
            "HAEORUM_API_THREADPOOL_TOKENS",
            "for configured search and image concurrency",
            "marqo_pool_capacity",
            "marqo_timeout_budget",
            "marqo_transport_tuning",
            "REQUIRED_TEXT_LOAD_CONCURRENCY",
            "REQUIRED_IMAGE_LOAD_CONCURRENCY",
            "REQUIRED_TEXT_LOAD_P95_MS",
            "REQUIRED_IMAGE_LOAD_P95_MS",
            "MARQO_POOL_ENV_NAMES",
            "MARQO_CONCURRENCY_ENV_NAMES",
            "MARQO_API_WORKERS",
            "MARQO_API_KEEPALIVE_TIMEOUT",
            "MARQO_API_GZIP_MINIMUM_SIZE",
            "MARQO_MAX_CONCURRENT_SEARCH",
            "required_marqo_search_concurrency",
            "VESPA_POOL_SIZE",
            "VESPA_SEARCH_TIMEOUT_MS",
            "MARQO_INFERENCE_POOL_SIZE",
            "must be explicitly set for Marqo production traffic",
            "must be at least 2 when one Marqo API backs multiple API servers",
            "for required text load concurrency",
            "Marqo's Vespa HTTP timeout budget",
            "rotate idle backend connections before Uvicorn closes them",
            "large search JSON responses are compressed",
            "settings_load",
            "api-server-count",
            "skip-path-checks",
            "markdown-output",
        ]:
            assert expected in env_check, f"env_check.py missing {expected}"

        marqo_run_script = (ROOT.parents[1] / "components" / "marqo" / "run_marqo.sh").read_text(encoding="utf-8")
        for expected in [
            "MARQO_API_KEEPALIVE_TIMEOUT",
            "MARQO_API_GZIP_MINIMUM_SIZE",
            "--timeout-keep-alive \"$MARQO_API_KEEPALIVE_TIMEOUT\"",
            "exec uvicorn",
        ]:
            assert expected in marqo_run_script, f"run_marqo.sh missing {expected}"

        marqo_api_source = (
            ROOT.parents[1] / "components" / "marqo" / "src" / "marqo" / "tensor_search" / "api.py"
        ).read_text(encoding="utf-8")
        for expected in [
            "GZipMiddleware",
            "MARQO_API_GZIP_MINIMUM_SIZE",
            "app.add_middleware(GZipMiddleware",
        ]:
            assert expected in marqo_api_source, f"marqo api.py missing {expected}"

        marqo_resource_check = (ROOT / "scripts" / "marqo_resource_check.py").read_text(encoding="utf-8")
        for expected in [
            "docker",
            "stats",
            "parse_docker_stats",
            "parse_memory_usage",
            "memory_usage_bytes",
            "cpu_percent",
            "marqo_index_stats",
            "validate_marqo_url_value",
            "skip-docker-stats",
            "QWEN_IMAGE_PROBE_DATA_URL",
            "qwen_image_embedding_probe",
            "qwen_embedding_contract",
            "skip-qwen-embedding-probe",
        ]:
            assert expected in marqo_resource_check, f"marqo_resource_check.py missing {expected}"

        security_check = (ROOT / "scripts" / "security_check.py").read_text(encoding="utf-8")
        for expected in [
            "build_security_report",
            "check_public_base_url",
            "public_base_url",
            "base_url_non_local",
            "base_url_credentials",
            "urlparse",
            "ipaddress",
            'rstrip(".")',
            "is_link_local",
            "mssql_ip_restricted",
            "production_env",
            "production_search_engine",
            "sync_interval_hourly",
            "sync_interval_seconds",
            "sync_failure_alerting",
            "sync-alerting-configured",
            "sync_alert_webhook_configured",
            "sync_alert_webhook_valid",
            "service_env_file_permissions",
            "service_env_file_path",
            "SERVICE_ENV_FILE_MAX_MODE",
            "check_secret_file_permissions",
            "check_sync_alert_webhook_url",
            "load_env_file",
            "patched_environ",
            "clear: bool = False",
            "clear=bool(args.env_file)",
            "env-file",
            "external_sync_alerting_confirmed",
            "nginx_client_max_body_size",
            "nginx-config",
            "nginx_upstream_resilience",
            "nginx_forwarded_for_safety",
            "x_real_ip_safe_header_count",
            "forwarded_header_sanitized_count",
            "Forwarded:unsafe",
            "systemd_restart_policy",
            "systemd_sync_worker",
            "systemd_reindex_service",
            "systemd_reindex_timer",
            "LimitNOFILE",
            "MIN_SYSTEMD_NOFILE",
            "parse_systemd_limit",
            "systemd-service",
            "sync-systemd-service",
            "reindex-systemd-service",
            "reindex-systemd-timer",
            "logrotate_config",
            "logrotate-config",
            "allowed_origins",
            "cors_origins_https",
            "cors_origins_safe_public",
            "cors_covers_allowed_origins",
            "non_https_cors_origins",
            "unsafe_cors_origins",
            "allowed_origins_safe_public",
            "product_url_templates_https",
            "product_url_templates_safe_public",
            "malls_with_non_https_product_url_templates",
            "malls_with_unsafe_product_url_templates",
            "safe_product_url_template_uses_https",
            "safe_product_url_template_uses_safe_public_url",
            "global_product_url_template_safe_public",
            "malls_missing_cors_origins",
            "malls_with_wildcard_allowed_origins",
            "malls_with_non_https_allowed_origins",
            "malls_with_unsafe_allowed_origins",
            "check_nginx_client_max_body_size",
            "check_nginx_upstream_resilience",
            "check_nginx_forwarded_for_safety",
            "check_systemd_restart_policy",
            "check_systemd_sync_worker_service",
            "check_systemd_reindex_service",
            "check_systemd_reindex_timer",
            "check_logrotate_config",
            "parse_nginx_size_to_bytes",
            "markdown-output",
        ]:
            assert expected in security_check, f"security_check.py missing {expected}"

        quality_report = (ROOT / "scripts" / "quality_report.py").read_text(encoding="utf-8")
        for expected in [
            "QUALITY_CASES",
            "load_quality_cases",
            "case_source",
            "case_source_fingerprint",
            "custom_cases",
            "skipped_case_checks",
            "image_cases_with_supplied_source",
            "mixed_cases_with_supplied_source",
            "image_cases_with_file_source",
            "mixed_cases_with_file_source",
            "image_path",
            "cases",
            "text_typo_umbrella",
            "meets_minimum_poc_size",
            "summarize_response_times",
            "response_time_ok",
            "case_contract",
            "summarize_case_contract",
            "and case_contract[\"ok\"]",
            "QUALITY_CASE_MIN_TYPE_COUNTS",
            "QUALITY_CASE_MIN_RESULTS",
            "QUALITY_CASE_MIN_LOW_CONFIDENCE_CASES",
            "QUALITY_CASE_MIN_TEXT_VARIANT_CASES",
            "case_expects_low_confidence",
            "case_has_text_variant_marker",
            "typo_or_synonym",
            "missing_text_variant_case",
            "duplicate_case_names",
            "max-text-ms",
            "max-image-ms",
            "max-mixed-ms",
            "markdown-output",
            "--cases",
            "validate_marqo_url_value",
            "expected_top_product_id",
            "expected_low_confidence",
            "summarize_operational_quality_mode",
            "operational_quality",
            "requires_case_file_images",
        ]:
            assert expected in quality_report, f"quality_report.py missing {expected}"

        search_insights = (ROOT / "scripts" / "search_insights.py").read_text(encoding="utf-8")
        for expected in [
            "low_confidence_queries",
            "zero_result_queries",
            "no_click_queries",
            "top_clicked_products",
            "recommendations",
            "zero_result_query",
            "low_confidence_query",
            "no_click_query",
            "click_through_rate",
            "malformed_lines",
            "result_count",
            "top_product_ids",
            "suggested_categories",
            "inferred_categories",
            "synonym_seed_candidates",
            "suggested_synonyms_entry",
            "quality_case_candidates",
            "build_synonyms_seed_payload",
            "build_quality_cases_seed_payload",
            "review_required",
            "synonyms-output",
            "quality-cases-output",
            "mixed_weight_recommendation",
            "zero_result_regression",
            "typo_or_synonym",
            "Synonym Seed Candidates",
            "Quality Case Candidates",
            "markdown-output",
            "min-searches",
        ]:
            assert expected in search_insights, f"search_insights.py missing {expected}"

        config_module = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        for expected in [
            "HAEORUM_QUERY_SYNONYM_PATH",
            "HAEORUM_CATEGORY_SUGGESTION_LIMIT",
            "HAEORUM_MIN_IMAGE_DIMENSION",
            "load_query_synonyms",
            "validate_query_synonyms",
            "query_synonyms",
            "category_suggestion_limit",
        ]:
            assert expected in config_module, f"config.py missing {expected}"

        search_service = (ROOT / "app" / "search_service.py").read_text(encoding="utf-8")
        for expected in [
            "category_suggestion_limit",
            "suggest_categories(",
            "normalized_query",
            "inferred_categories",
            "inferred_categories=prepared.inferred_categories",
        ]:
            assert expected in search_service, f"search_service.py missing category suggestion config {expected}"

        engine_module = (ROOT / "app" / "engine.py").read_text(encoding="utf-8")
        for expected in [
            "query_synonyms",
            "expanded_query_text",
            "custom_synonyms",
        ]:
            assert expected in engine_module, f"engine.py missing {expected}"

        sample_query_synonyms = (ROOT / "sample_query_synonyms.json").read_text(encoding="utf-8")
        for expected in ["파우치", "에코백", "보조배터리"]:
            assert expected in sample_query_synonyms, f"sample_query_synonyms.json missing {expected}"

        poc_dataset_builder = (ROOT / "scripts" / "poc_dataset_builder.py").read_text(encoding="utf-8")
        for expected in [
            "RECOMMENDED_CATEGORIES",
            "select_balanced",
            "missing_recommended_categories",
            "selected_missing_product_url_count",
            "selected_missing_mall_id_count",
            "selected_unsafe_product_url_count",
            "selected_product_url_product_id_mismatch_count",
            "output-csv",
        ]:
            assert expected in poc_dataset_builder, f"poc_dataset_builder.py missing {expected}"

        mall_config_check = (ROOT / "scripts" / "mall_config_check.py").read_text(encoding="utf-8")
        for expected in [
            "enabled mall requires allowed_origins",
            "enabled mall count is below required minimum",
            "enabled_mall_product_url_prefixes",
            "enabled_mall_api_key_hashes",
            "api_key_hash",
            "REDACTED_API_KEY_VALUE",
            "redacted_api_key_duplicates",
            "product_url_template_prefix",
            "duplicate api_key",
            "allowed_origins must not contain wildcard",
            "allowed_origins must use https",
            "allowed_origins must use safe public origins",
        ]:
            assert expected in mall_config_check, f"mall_config_check.py missing {expected}"

        mall_config_builder = (ROOT / "scripts" / "mall_config_builder.py").read_text(encoding="utf-8")
        for expected in [
            "build_mall_config_from_csv",
            "build_mall_config_from_export",
            "read_xlsx_rows",
            "unsupported mall export file type",
            "generate-missing-api-keys",
            "--input",
            "origin-template",
            "product-url-template",
            "origin_uses_safe_public_url",
            "cors_origins",
            "validate_mall_config",
        ]:
            assert expected in mall_config_builder, f"mall_config_builder.py missing {expected}"

        mssql_export_csv = (ROOT / "scripts" / "mssql_export_csv.py").read_text(encoding="utf-8")
        for expected in [
            "build_export_query",
            "build_wrapped_mssql_query",
            "validate_mssql_connection_string_value",
            "HAEORUM_MSSQL_READONLY_CONNECTION_STRING",
            "StreamingProductCsvWriter",
            "ExportQualityAccumulator",
            "failure_report",
            "sanitize_error_message",
            "output-csv",
            "product-id-column",
            "updated-at-column",
            "DEFAULT_FETCH_SIZE",
            "fetchmany",
            "row_callback",
            "parse_product_rows",
            "fetch-size",
            "fetch_batches",
            "max_fetch_batch_rows",
            "batched_fetch",
            "streaming_parse",
            "streamed_product_csv",
            "retained_product_rows",
            "csv_rows_written",
            "mall-config",
            "mall_config_alignment_report",
            "active_product_url_mismatch_count",
            "active_product_url_product_id_mismatch_count",
            "active_missing_category_count",
            "active_negative_price_count",
            "domain_filter_coverage",
            "DomainFilterCoverageAccumulator",
            "query_fingerprint_report",
            "query_configured",
            "query_fingerprint",
        ]:
            assert expected in mssql_export_csv, f"mssql_export_csv.py missing {expected}"

        mssql_view_check = (ROOT / "scripts" / "mssql_view_check.py").read_text(encoding="utf-8")
        sync_py = (ROOT / "app" / "sync.py").read_text(encoding="utf-8")
        for expected in [
            "REQUIRED_COLUMN_GROUPS",
            "validate_readonly_query",
            "validate_mssql_connection_string_value",
            "HAEORUM_MSSQL_READONLY_CONNECTION_STRING",
            "build_sample_query",
            "build_wrapped_mssql_query",
            "p_idx",
            "site_id",
            "is_deleted_or_display_yn",
            "validate_sample_report",
            "sample_quality_report",
            "permission_report",
            "fn_my_permissions",
            "db_datawriter",
            "failure_report",
            "sanitize_error_message",
            "missing_updated_at_rows",
            "invalid_updated_at_rows",
            "missing_product_id_rows",
            "missing_product_name_rows",
            "active_missing_category_rows",
            "active_negative_price_rows",
            "active_missing_image_url_rows",
            "active_product_url_product_id_mismatch_rows",
            "domain_filter_coverage",
            "price_range_count",
            "min_order_qty_count",
            "delivery_days_count",
            "print_methods_count",
            "duplicate_product_ids",
            "sample rows contain duplicate product_id values",
            "query_fingerprint_report",
            "query_configured",
            "query_fingerprint",
        ]:
            assert expected in mssql_view_check or expected in sync_py, f"mssql_view_check.py missing {expected}"

        operational_simulation = (ROOT / "scripts" / "operational_simulation.py").read_text(encoding="utf-8")
        for expected in [
            "operational-risk-probes.json",
            "parse_error_count",
            "rows contain parse errors",
            "active rows missing main_image_url",
            "mall_origin_policy_blocked",
            "mall_duplicate_api_key_blocked",
            "representative_api_key_mismatch_blocked",
            "representative_response_mall_id_mismatch_blocked",
            "mall_placeholder_api_key_blocked",
            "mall_product_template_blocked",
            "mall_config_problem_fields",
            "search-insights.json",
            "search_insights_quality_loop",
            "synonym_seed_candidate_count",
            "quality_case_candidate_count",
            "query_type_latency_ms",
            "slow_samples",
            "throughput_rps",
            "coverage_ok",
            "mall_config_alignment",
            "active_product_url_mismatch_count",
            "active_product_url_product_id_mismatch_count",
            "source_product_url_product_id_mismatch_blocked",
            "export_mall_config_alignment_blocked",
            "mixed-weight-sweep.json",
            "mixed_weight_sweep",
            "current_default_close_to_best",
            "conflict_sensitivity",
            "sync-lifecycle.json",
            "simulated_sync_lifecycle",
            "widget-integration-probe.json",
            "simulated_widget_integration_probe",
            "simulated_widget_snippet_bundle",
            "simulated_widget_preview_bundle",
            "simulated_widget_manual_install_plan",
            "manual-install-plan.json",
            "preview-validation.json",
            "manual_install_ready_pages",
            "preview_count",
            "preview_validation_ok",
            "widget-snippets",
            "write_snippet_bundle",
            "pages_with_external_widget_csp_risk",
            "hidden_product_removed_from_index",
            "missing_source_product_deleted",
            "search_cache_invalidation_logged",
            "price_min",
            "price_max",
            "public-shop0001-dev-key",
            "allowed_origins",
            "malls-source.csv",
            "mall-config-build.json",
            "mall_config_builder_roundtrip",
            "build_mall_config_from_csv",
            "writer_permission_blocked",
            "normalize_public_http_base_url",
            "normalize_public_http_origin",
            "validate_http_url_resolves_to_public_network",
            "public_evidence_private_target_blocked",
            "public_evidence_private_dns_blocked",
            "collector_primary_api_key_mismatch_blocked",
            "quality_case_mall_id_mismatch_blocked",
            "quality_case_mall_identity_risks",
            "wrong_mall_expected_top",
            "product_url_template_prefix",
            "expected_product_url_prefix_counts",
            "public_evidence_private_dns_errors",
            "public_evidence_private_target_errors",
            "SIMULATED_MSSQL_QUERY",
            "query_fingerprint_report",
            "undersized_mssql_view_sample_blocked",
            "mssql_export_fingerprint_path_mismatch_blocked",
            "batched_fetch",
            "fetch_batches",
            "max_fetch_batch_rows",
            "distributed_coalesced",
            "distributed_lock_contention_events",
            "failure_coalesced",
            "failure_engine_searches",
            "local_wait_timeout_bounded",
            "local_wait_timeout_rejected",
            "local_wait_timeout_duplicate_backend_blocked",
            "SearchCacheMissInFlight",
            "local_wait_timeout_status",
            "image_singleflight_context_released_during_wait",
            "image_singleflight_active_contexts_while_waiting",
            "policy_fingerprint_reused",
            "policy_fingerprint_known_calls",
            "policy_unknown_mall_tokens_skipped",
            "policy_unknown_token_count",
            "policy_unknown_mall_probe_count",
            "policy_fingerprint_synonym_count",
            "marqo-filter-pushdown-probe.json",
            "marqo_filter_pushdown_probe",
            "load-mall-identity-probe.json",
            "load_mall_identity_probe",
            "simulated_load_mall_identity",
            "simulated_load_mall_counts",
            "per_request_api_key_headers",
            "requests_cover_sampled_malls",
            "min_price_range_filter",
            "max_price_range_filter",
            "quantity_range_filter",
            "delivery_range_filter",
            "max_offset_cap_allows_configured_cap",
            "max_offset_cap_rejects_candidate_explosion",
            "runtime_cap_blocks_misconfigured_settings",
            "max_operational_search_offset",
            "deep_post_filter_candidate_limit_capped",
            "deep_post_filter_keeps_fuzzy_filter_for_post_filter",
            "max_marqo_search_candidates",
            "Marqo candidate limit cap keep related-item deep pagination",
            "numeric_only_filter_pushdown_uses_lower_candidate_limit",
            "Numeric-only image/mixed searches keep the lower",
            "load_backend_marqo_gzip_missing_blocked",
            "api_scale_backend_marqo_gzip_missing_blocked",
            "load_backend_marqo_payload_uncompressed_blocked",
            "api_scale_backend_marqo_payload_uncompressed_blocked",
            "load_backend_marqo_zero_attempts_blocked",
            "api_scale_backend_marqo_zero_attempts_blocked",
            "load_backend_marqo_below_unique_profile_blocked",
            "api_scale_backend_marqo_below_unique_profile_blocked",
            "load_backend_qwen_zero_attempts_blocked",
            "api_scale_backend_qwen_zero_attempts_blocked",
            "load_backend_qwen_below_unique_image_profile_blocked",
            "api_scale_backend_qwen_below_unique_image_profile_blocked",
            "server_metrics.delta.backend_marqo_request_attempts_zero",
            "server_metrics.delta.backend_qwen_request_attempts_zero",
            "server_metrics.delta.backend_marqo_request_attempts_below_unique_requests",
            "server_metrics.delta.backend_qwen_request_attempts_below_unique_image_inputs",
            "server_metrics.delta.backend_marqo_gzip_responses_zero",
            "server_metrics.delta.backend_marqo_response_body_bytes_not_below_decoded",
        ]:
            assert expected in operational_simulation, f"operational_simulation.py missing {expected}"

        mssql_view_template = (ROOT / "sql" / "v_ai_search_products_template.sql").read_text(encoding="utf-8")
        for expected in [
            "CREATE OR ALTER VIEW dbo.v_ai_search_products",
            "AS product_id",
            "AS product_name",
            "AS price",
            "AS price_min",
            "AS price_max",
            "AS category_name",
            "AS print_methods",
            "AS materials",
            "AS colors",
            "AS min_order_qty",
            "AS delivery_days",
            "AS product_group_id",
            "AS main_image_url",
            "AS product_url",
            "AS status",
            "AS updated_at",
            "AS is_deleted",
            "AS display_yn",
            "AS mall_id",
            "AS description",
            "AS keywords",
            "inactive",
            "active",
        ]:
            assert expected in mssql_view_template, f"v_ai_search_products_template.sql missing {expected}"

        mall_config_check = (ROOT / "scripts" / "mall_config_check.py").read_text(encoding="utf-8")
        for expected in [
            "duplicate_mall_ids",
            "duplicate_api_keys",
            "redacted_api_key_duplicates",
            "product_url_template",
            "enabled_mall_product_url_prefixes",
            "enabled_mall_api_key_hashes",
            "api_key_hash",
            "product_url_template_prefix",
            "allowed_origins",
            "allowed_origins must not contain wildcard *",
            "allowed_origins must use https",
            "allowed_origins must use safe public origins",
            "excluded_product_ids",
            "excluded_categories",
            "hide_prices",
            "price_multiplier",
            "price_adjustment",
            "price_round_to",
            "min-count",
        ]:
            assert expected in mall_config_check, f"mall_config_check.py missing {expected}"

        sample_malls = (ROOT / "sample_malls.json").read_text(encoding="utf-8")
        for expected in [
            "excluded_product_ids",
            "excluded_categories",
            "allowed_origins",
            "hide_prices",
            "price_multiplier",
            "price_adjustment",
            "price_round_to",
        ]:
            assert expected in sample_malls, f"sample_malls.json missing {expected}"

        with (ROOT / "sample_products.csv").open(encoding="utf-8-sig", newline="") as sample_file:
            rows = list(csv.DictReader(sample_file))
        active_rows = [
            row
            for row in rows
            if str(row.get("status", "")).strip().lower() == "active"
            and str(row.get("display_yn", "")).strip().upper() != "N"
            and str(row.get("is_deleted", "")).strip().lower() not in {"1", "true", "yes", "y"}
        ]
        product_keys = [product_identity_key(row.get("mall_id"), row.get("product_id")) for row in rows]
        duplicate_ids = sorted(
            product_identity_label(*key)
            for key, count in Counter(product_keys).items()
            if key[1] and count > 1
        )
        missing_active_images = [row.get("product_id", "") for row in active_rows if not row.get("main_image_url")]
        active_categories = {row.get("category_name", "") for row in active_rows}
        for expected in ["우산", "텀블러", "볼펜", "타올", "점착메모지", "상패", "달력", "가방", "마우스패드", "생활용품"]:
            assert expected in active_categories, f"sample_products.csv missing recommended category {expected}"
        assert len(active_rows) >= 300, "sample_products.csv must include at least 300 active PoC products"
        assert not duplicate_ids, f"sample_products.csv duplicate product ids: {duplicate_ids[:20]}"
        assert not missing_active_images, f"sample_products.csv active products missing images: {missing_active_images[:20]}"

        image_url_check = (ROOT / "scripts" / "image_url_check.py").read_text(encoding="utf-8")
        for expected in [
            "warning_count",
            "concurrency",
            "timeout_seconds",
            "retry_count",
            "retry_delay_seconds",
            "max_mb",
            "min_dimension",
            "min-dimension",
            "validate_args",
            "max-warnings",
            "retry-count",
            "zero or greater",
        ]:
            assert expected in image_url_check, f"image_url_check.py missing {expected}"

        operational_readiness = (ROOT / "scripts" / "operational_readiness.py").read_text(encoding="utf-8")
        for expected in ["required_checked = 100", "max_concurrency = 5", "min_dimension >= 16", "checked too few images"]:
            assert expected in operational_readiness, f"operational_readiness.py missing image URL gate {expected}"

        engine_py = (ROOT / "app" / "engine.py").read_text(encoding="utf-8")
        for expected in [
            "MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES",
            "MARQO_DELETE_DOCUMENTS_BATCH_SIZE",
            "add_documents_batch_size",
            "add_documents_max_request_bytes",
            "delete_documents_batch_size",
            "chunk_marqo_documents_by_limits",
            "last_upsert_stats",
            "last_delete_stats",
            "max_request_body_bytes",
            "MARQO_BATCH_RESPONSE_SAMPLE_LIMIT",
            "MARQO_BATCH_FAILURE_SAMPLE_LIMIT",
            "batch_response_payload",
            "append_failure_samples",
            "responses_truncated",
            "failed_products_truncated",
        ]:
            assert expected in engine_py, f"engine.py missing Marqo index batching guard {expected}"

        sync_py = (ROOT / "app" / "sync.py").read_text(encoding="utf-8")
        for expected in ["fetchmany", "last_fetch_stats", "active_product_ids", "SYNC_FAILURE_SAMPLE_LIMIT"]:
            assert expected in sync_py, f"sync.py missing MSSQL sync batching guard {expected}"

        config_py = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        for expected in ["mssql_sync_fetch_size", "HAEORUM_MSSQL_SYNC_FETCH_SIZE"]:
            assert expected in config_py, f"config.py missing MSSQL sync batching setting {expected}"

        csv_index_py = (ROOT / "scripts" / "csv_index.py").read_text(encoding="utf-8")
        for expected in ["indexing", "Index batches", "Max index request body bytes"]:
            assert expected in csv_index_py, f"csv_index.py missing index batching evidence {expected}"

        for env_example in [ROOT / ".env.example", ROOT / "deploy" / "haeorum-ai-search.env.example"]:
            env_text = env_example.read_text(encoding="utf-8")
            for expected in [
                "HAEORUM_MARQO_ADD_DOCUMENTS_BATCH_SIZE",
                "HAEORUM_MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES",
                "HAEORUM_MARQO_DELETE_DOCUMENTS_BATCH_SIZE",
                "HAEORUM_MSSQL_SYNC_FETCH_SIZE",
                "GEMINI_PROXY_MAX_CONCURRENT_CALLS",
                "GEMINI_PROXY_RATE_LIMIT_RPM",
            ]:
                assert expected in env_text, f"{env_example.name} missing {expected}"

        widget_js = (ROOT / "widget" / "widget.js").read_text(encoding="utf-8")
        for expected in [
            "attachToSearchInput",
            "attachAfterSelector",
            "autoAttach",
            "mountWaitMs",
            "findAutoSearchInput",
            "isElementHidden(input)",
            "inlineStyleHidesElement",
            "shouldWaitForDomReady",
            "DOMContentLoaded",
            "MutationObserver",
            "waitForLateMountTargets",
            "resetExistingWidget",
            "destroy",
            "data-hai-attach-mode",
            "prefillFromSearchInput",
            "siteId",
            "minImageDimension",
            "imageValidationPending",
            "searchInFlight",
            "isAllowedImageFile",
            "isLoadedImageLargeEnough",
            "insertAdjacentElement",
            "score_percent",
            "formatScore",
            "formatPercent",
            "상품번호",
            "aria-label",
            "query_type",
            "offset",
            "nextOffset",
            "lastQueryType",
            "deriveQueryType",
            "safeLinkUrl",
            "safeImageUrl",
            "normalizeApiBaseUrl",
            "inferApiBaseUrlFromScript",
            "apiBaseUrlFromScriptSrc",
            "autoInitFromScript",
            "data-hai-auto-init",
            "isSafeApiBaseUrl",
            "hasUnsafeApiUrlAuthority",
            "apiBaseUrl must be an absolute HTTP(S) URL",
            "isSafeAbsoluteBrowserUrl",
            "hasUnsafeUrlAuthority",
            "extractAuthorityHost",
            "isUnsafeLocalBrowserHost",
            "parseIpv4Host",
            "169 && ipv4[1] === 254",
            "fe[89ab]",
            "hai-more",
            "sendBeacon",
            "keepalive: true",
            "triggerTitle",
            "triggerAriaLabel",
            "hai-camera-icon",
            "lastFocusedElement",
            "Escape",
            "accentColor",
            "accentSoftColor",
            "zIndex",
            "fallbackFloating",
            "findElementBySimpleIdSelector",
            "escapeHashIdSelectorTokens",
        ]:
            assert expected in widget_js, f"widget.js missing {expected}"

        widget_contract = (ROOT / "contracts" / "widget_init.example.html").read_text(encoding="utf-8")
        for expected in ["attachToSearchInput", "attachAfterSelector", "autoAttach", "fallbackFloating", "mountWaitMs", "siteId", "apiBaseUrl", "minImageDimension", "triggerAriaLabel", "accentColor", "zIndex"]:
            assert expected in widget_contract, f"widget_init.example.html missing {expected}"

        integration_doc = (ROOT / "INTEGRATION.md").read_text(encoding="utf-8")
        for expected in [
            "curl -fsS https://ai-search.haeorumgift.com/api/ai-search",
            '"Origin: https://shop001.haeorumgift.com"',
            '"X-API-Key: replace-with-shop001-public-key"',
            '{"site_id":"shop001","q":"텀블러","limit":20}',
            '"image_base64": "data:image/png;base64,..."',
            '-F "image=@./sample-product.webp;type=image/webp"',
            '-F "text_weight=0.4"',
            '-F "image_weight=0.6"',
            '-F "image=@./sample-product.jpg;type=image/jpeg"',
        ]:
            assert expected in integration_doc, f"INTEGRATION.md missing {expected}"

        widget_dom_check = (ROOT / "scripts" / "widget_dom_check.js").read_text(encoding="utf-8")
        for expected in [
            "attachToSearchInput",
            "attachAfterSelector",
            "shop004-auto-detected-search",
            "attachMode",
            "assertDeferredInitUntilDomReady",
            "deferredInitUntilDomReady",
            "assertRepeatedInitReplacesWidget",
            "repeatedInitReplacesWidget",
            "assertCssSpecialIdSelectorFallback",
            "cssSpecialIdSelectorFallback",
            "assertComplexCssSpecialIdSelectorFallback",
            "complexCssSpecialIdSelectorFallback",
            "assertAmbiguousExplicitSelectorRejected",
            "ambiguousExplicitSelectorRejected",
            "assertDynamicAutoAttachAfterDomMutation",
            "dynamicAutoAttachAfterDomMutation",
            "assertAutoAttachSkipsHiddenAndDisabledSearchInputs",
            "autoAttachSkipsHiddenDisabledSearchInputs",
            "assertFallbackFloatingMountsWithoutSearchForm",
            "fallbackFloatingMountsWithoutSearchForm",
            "FakeMutationObserver",
            "siteId",
            "checked_sites",
            "assertApiBaseUrlValidation",
            "unsafeApiBaseUrlRejected",
            "assertScriptSrcApiBaseUrlFallback",
            "scriptSrcApiBaseUrlFallback",
            "assertScriptDataAttributeAutoInit",
            "scriptDataAttributeAutoInit",
            "assertMallIdValidation",
            "unsafeMallIdRejected",
            "unsafe apiBaseUrl should be rejected",
            "prefilledQuery",
            "triggerTitle",
            "triggerAriaLabel",
            "cameraIconRendered",
            "accentColor",
            "categoryRefetch",
            "moreOffset",
            "mixedQueryType",
            "clickLogTransport",
            "clickLogQueryType",
            "clickLogProductId",
            "click log query_type should preserve the rendered search type",
            "click log API key should not be sent in the URL",
            "click with API key should use fetch headers",
            "image link click log product_url mismatch",
            "detail link URL mismatch",
            "unsafe product_url should be neutralized",
            "https://user:pass@shop.example.test/product/P-CRED",
            "https://token@cdn.example.test/image.png",
            "/product/P-REL",
            "neutralized relative URL click should keep product_id",
            "data:image/png;base64,AAAA",
            "neutralized data image URL click should keep product_id",
            "shop.localhost.",
            "fe80::1",
            "neutralized localhost URL click should keep product_id",
            "neutralized link-local URL click should keep product_id",
            "unsafe product image_url should not be rendered",
            "unsafeProductUrlsNeutralized",
            "parseArgs",
            "--output",
            "writeReport",
            "imageAndDetailClickLogging",
            "invalid image type did not clear previous preview",
            "oversized image did not show an error",
            "oversized image did not clear previous preview",
            "small image did not show a minimum-size error",
            "damaged image did not show a decode error",
            "Escape did not close the modal",
            "closing modal did not restore trigger focus",
            "keyboardCloseRestoresFocus",
            "stale file in the search payload",
            "dragDropUpload",
            "smallImageRejected",
            "damagedImageRejected",
            "drag enter did not mark the upload dropzone",
            "drop did not clear the upload dropzone drag state",
            "mixed search did not include the dropped image",
            "duplicate submit while loading should not start another search",
            "duplicateSubmitBlocked",
            "responsiveLayout",
            "resultFieldsRendered",
            "score percent was not rendered clearly",
            "product id label was not rendered",
            "quote price was not rendered",
            "product image alt text should use product name",
            "desktop related-products grid should use four columns",
            "narrow mobile grid should collapse to one column",
            "wide mobile grid should use two columns",
            "tablet grid should use three related-product columns",
        ]:
            assert expected in widget_dom_check, f"widget_dom_check.js missing {expected}"

        widget_integration_probe = (ROOT / "scripts" / "widget_integration_probe.py").read_text(encoding="utf-8")
        for expected in [
            "data-hai-auto-init",
            "inline_init_blocked_or_risky",
            "external_widget_src_blocked_or_risky",
            "Content-Security-Policy",
            "csp_widget_src_allowed",
            "existing_relative_widget_script_cannot_infer_api_base_url",
            "attachToSearchInput",
            "attachAfterSelector",
            "fallback_floating",
            "allow-fallback-floating",
            "Widget Integration Probe",
            "data_auto_init_ready",
            "pages_ready_for_data_auto_init",
            "blocking_risks",
            "element_is_hidden",
            "element_is_disabled_or_readonly",
            "is_non_public_host",
            "open_public_http_request",
            "must not use non-public hosts",
            "not_operational_readiness",
            "snippets-output-dir",
            "resolve_probe_source",
            "probe_source_entries",
            "source_variant",
            "source_fingerprint",
            "write_snippet_bundle",
            "contains_public_api_keys",
            "review_required",
            "manual-install-plan.json",
            "preview-validation.json",
            "manual_install_summary",
            "selector_confidence",
            "preview_count",
            "preview_file",
            "preview_validation_ok",
            "validate_preview_html_body",
            "data_auto_init_script_multiple",
            "Haeorum AI Search preview insertion",
            "developer_unavailable_saved_html_install_plan",
        ]:
            assert expected in widget_integration_probe, f"widget_integration_probe.py missing {expected}"

        representative_site_check = (ROOT / "scripts" / "representative_site_check.py").read_text(encoding="utf-8")
        for expected in [
            "load_site_configs",
            "site_config",
            "validate_site_config",
            "PLACEHOLDER_PATTERNS",
            "production https URL",
            "is_local_host",
            "is_non_public_host",
            "open_public_http_request",
            "non-public hosts",
            "validate_api_base_url",
            "API base URL must not include query string or fragment",
            "whitespace, control characters, or backslashes",
            "desktop_page",
            "mobile_page",
            "widget_init",
            "auto_attach_selector_found",
            "detect_auto_attach_mount",
            "fallback_floating_mount",
            "data-fallback-floating",
            "text_search",
            "image_search",
            "mixed_search",
            "REQUIRED_RESULT_FIELDS",
            "score",
            "score_percent",
            "missing_top_fields",
            "missing_item_fields",
            "len(items) > 0",
            "len(categories) > 0",
            "engine_ok",
            "expected_mall_id",
            "meta_mall_id",
            "mall_id_matches_request",
            "result_mall_id_mismatches",
            'meta.get("engine")',
            "mode_check_name",
            "check_category_refetch",
            "text_category_refetch",
            "mismatched_categories",
            "text_product_url_rule",
            "image_product_url_rule",
            "mixed_product_url_rule",
            "expected_product_url_prefix",
            "expected_product_url_contains",
            "expected_product_url_pattern",
            "validate_product_url_prefix",
            "query string or fragment",
            "build_product_url_rule_result",
            "url_matches_prefix",
            "matched_prefixes",
            "detail_url fetch skipped",
            "value must not include credentials",
            "text_detail_url",
            "image_detail_url",
            "mixed_detail_url",
            "text_click_log",
            "image_click_log",
            "mixed_click_log",
            "representative site config",
            "hidden_selectors",
            "disabled_or_readonly_selectors",
            "selector_hidden",
            "selector_disabled_or_readonly",
            "simple_hash_id_selector_value",
            "api_base_url_init_option",
            "api_base_url_script_src",
            "api_base_url_source",
            "apiBaseUrl/scriptSrc=",
            "extract_widget_script_data_options",
            "auto_init_found",
            "data-hai-auto-init",
            "api_key_hash",
            "representative_execution_mode",
            "not_operational_readiness",
            "partial_run",
            "image_input.source",
        ]:
            assert expected in representative_site_check, f"representative_site_check.py missing {expected}"

        representative_sites = (ROOT / "contracts" / "representative_sites.example.json").read_text(encoding="utf-8")
        for expected in [
            "shop001",
            "shop002",
            "partner003",
            "required_markers",
            "api_base_url",
            "expected_product_url_prefix",
            "widget_probe_sources",
            "shop001-pc.html",
            "shop001-mobile.html",
        ]:
            assert expected in representative_sites, f"representative_sites.example.json missing {expected}"
        return {
            "name": "deployment_files",
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        return {
            "name": "deployment_files",
            "ok": False,
            "error": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def main() -> int:
    checks = [*check_requests(), check_response(), check_click_log_request(), check_openapi(), check_deployment_files()]
    report = {"ok": all(check["ok"] for check in checks), "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
