from __future__ import annotations

import base64
from contextlib import redirect_stdout
import io
import asyncio
import json
import os
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.cache import MemorySearchCache
from app.config import MallConfig, Settings, load_mall_configs, load_query_synonyms, validate_settings
from app.concurrency import ImageSearchGate, ImageSearchQueueFull, SearchExecutionGate, SearchQueueFull
from app.engine import EngineQuery, LocalSearchEngine, MarqoSearchEngine, ReservedSearchEngineUnavailable
from app.engine_factory import create_search_engine
from app.image_probe import ImageProbeResult, ProductImageProbe, is_retryable_message
from app.image_validation import (
    read_upload_bytes_limited,
    validate_image_base64,
    validate_image_bytes,
    validate_multipart_content_length,
)
from app.metrics import build_admin_metrics, metrics_to_prometheus
from app.models import MAX_PRODUCT_ID_LENGTH, ClickLogRequest, ProductDocument, SearchRequest
from app.rate_limit import RedisRateLimiter, record_rate_limit_hit
from app.search_service import AISearchService, SearchLogger
from app.security import PublicAccessError, unsupported_multipart_field_names, validate_mall_access
from app.sql_safety import validate_readonly_query
from app.sync import CsvProductSource, MssqlProductSource, SyncService, build_wrapped_mssql_query
from app.sync_worker import resolve_sync_since, run_once
from app.url_safety import product_url_contains_product_id
from scripts.api_smoke_test import run as run_api_smoke
from scripts.local_acceptance import build_source_fingerprint
from scripts.mall_config_builder import build_mall_config_from_csv
from scripts.mall_config_check import validate_mall_config
from scripts.mssql_view_check import analyze_readonly_permissions, analyze_sample, validate_columns, validate_sample_report
from scripts.load_compare import build_report as build_load_compare_report
from scripts.load_test import (
    LoadRequestIdentity,
    annotate_server_metrics_expectations,
    annotate_server_metrics_guardrails,
    api_instance_coverage_report,
    build_payloads,
    build_request_specs,
    build_run_log_coverage,
    build_server_metrics_report,
    expected_query_type,
    image_data_urls_for_args as load_image_data_urls_for_args,
    load_identity_summary,
    planned_request_count,
    summarize_request_profile,
    summarize_response_contract,
    validate_args as validate_load_test_args,
)
from scripts.representative_site_check import evaluate_site, site_markers
from scripts.requirements_audit import (
    REQUIREMENTS as REQUIREMENT_AUDIT_REQUIREMENTS,
    build_report as build_requirements_audit_report,
    to_markdown as requirements_audit_to_markdown,
)
from scripts.security_check import build_security_report
from scripts.server_preflight_check import build_report as build_server_preflight_report


def make_image_bytes(
    image_format: str = "PNG",
    width: int = 32,
    height: int = 32,
    color: tuple[int, ...] = (12, 120, 110),
    mode: str = "RGB",
) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(mode, (width, height), color=color).save(buffer, format=image_format)
    return buffer.getvalue()


def make_png_bytes() -> bytes:
    return make_image_bytes("PNG")


class AcceptanceRunner:
    def __init__(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            engine_backend="local",
            product_csv_path=ROOT / "sample_products.csv",
            search_log_path=Path(self.temp_dir.name) / "search.jsonl",
            sync_log_path=Path(self.temp_dir.name) / "sync.jsonl",
            product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
            cache_ttl_seconds=0,
        )
        products = CsvProductSource(self.settings.product_csv_path).fetch_all()
        self.engine = LocalSearchEngine(products)
        self.service = AISearchService(self.engine, self.settings, SearchLogger(self.settings.search_log_path))
        self.image = validate_image_bytes(make_png_bytes(), max_bytes=self.settings.max_image_mb * 1024 * 1024)
        self.image_base64 = base64.b64encode(base64.b64decode(self.image.data_url.split(",", 1)[1])).decode("ascii")

    def close(self) -> None:
        self.temp_dir.cleanup()

    def run(self) -> dict[str, object]:
        checks = [
            self.check_contract_fixtures,
            self.check_fastapi_runtime_routes,
            self.check_fastapi_admin_runtime_routes,
            self.check_text_search,
            self.check_text_typo_and_synonym_quality,
            self.check_image_search,
            self.check_image_upload_validation_and_preprocessing,
            self.check_product_image_probe_and_sync_image_validation,
            self.check_mixed_search,
            self.check_multipart_search_contract,
            self.check_search_execution_queue_metrics,
            self.check_image_queue_metrics,
            self.check_rate_limit_controls_and_metrics,
            self.check_search_cache_and_sync_invalidation,
            self.check_marqo_engine_adapter_and_reserved_backends,
            self.check_low_confidence_notice,
            self.check_no_result_notice,
            self.check_top3_categories_and_items,
            self.check_related_items_pagination,
            self.check_active_filter_and_product_url,
            self.check_product_click_log_and_url_safety,
            self.check_domain_filters_price_quantity_delivery,
            self.check_public_mall_config,
            self.check_production_env_fail_closed_and_access_policy,
            self.check_api_smoke_security_and_admin_contracts,
            self.check_security_and_server_preflight_contracts,
            self.check_mall_price_and_visibility_policy,
            self.check_scaled_mall_config_1700,
            self.check_representative_site_checker_contract,
            self.check_mssql_readonly_view_and_incremental_contract,
            self.check_sync_status_and_logs,
            self.check_sync_worker_schedule_and_alerting_contract,
            self.check_search_insights_quality_loop,
            self.check_load_and_scale_evidence_contracts,
            self.check_requirements_audit_completion_gate_contract,
            self.check_local_performance_smoke,
        ]
        results = [run_check(check) for check in checks]
        return {
            "ok": all(item["ok"] for item in results),
            "checks": results,
        }

    def check_text_search(self) -> None:
        result = self.service.search(SearchRequest(mall_id="shop001", q="검은 우산", limit=20))
        assert result.meta.query_type.value == "text"
        assert result.top, "text search should return results"
        assert result.top[0].product_id == "P001"

    def check_text_typo_and_synonym_quality(self) -> None:
        synonym_path = Path(self.temp_dir.name) / "query-synonyms.json"
        synonym_path.write_text(
            json.dumps({"synonyms": {"파우치": ["가방", "백"]}}, ensure_ascii=False),
            encoding="utf-8",
        )
        log_path = Path(self.temp_dir.name) / "typo-synonym-search.jsonl"
        settings = Settings(
            engine_backend="local",
            search_log_path=log_path,
            product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
            query_synonym_path=synonym_path,
            query_synonyms=load_query_synonyms(synonym_path),
            cache_ttl_seconds=0,
        )
        service = AISearchService(
            LocalSearchEngine(
                [
                    ProductDocument(
                        product_id="P-TEXT-1",
                        name="검정 3단 자동 우산",
                        category="우산",
                        status="active",
                    ),
                    ProductDocument(
                        product_id="P-TEXT-2",
                        name="스테인리스 텀블러",
                        category="텀블러",
                        status="active",
                    ),
                    ProductDocument(
                        product_id="P-TEXT-3",
                        name="친환경 가방",
                        category="가방",
                        status="active",
                    ),
                    ProductDocument(
                        product_id="P-TEXT-4",
                        name="고급 볼펜",
                        category="볼펜",
                        status="active",
                    ),
                ]
            ),
            settings,
            SearchLogger(log_path),
        )

        typo = service.search(SearchRequest(mall_id="shop001", q="우싼", limit=10))
        assert typo.top[0].product_id == "P-TEXT-1"
        assert "우산" in typo.suggested_categories

        normalized = service.search(SearchRequest(mall_id="shop001", q="텐블러", limit=10))
        assert normalized.top[0].product_id == "P-TEXT-2"

        synonym = service.search(SearchRequest(mall_id="shop001", q="파우치", limit=10))
        assert synonym.top[0].product_id == "P-TEXT-3"

        entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert any(entry["q"] == "텐블러" and entry["normalized_query"] == "텀블러" for entry in entries)

    def check_contract_fixtures(self) -> None:
        from scripts.contract_check import check_requests, check_response

        checks = [*check_requests(), check_response()]
        assert all(check["ok"] for check in checks), checks

    def check_fastapi_runtime_routes(self) -> None:
        import importlib

        try:
            from fastapi.testclient import TestClient
        except ModuleNotFoundError as exc:
            if exc.name != "fastapi":
                raise
            self.check_fastapi_route_declarations()
            return

        env_overrides = {
            "HAEORUM_SEARCH_ENGINE": "local",
            "HAEORUM_ADMIN_API_KEY": "dev-admin-key",
            "HAEORUM_PRODUCT_CSV": str(ROOT / "sample_products.csv"),
            "HAEORUM_SEARCH_LOG_PATH": str(Path(self.temp_dir.name) / "runtime-search.jsonl"),
            "HAEORUM_ERROR_LOG_PATH": str(Path(self.temp_dir.name) / "runtime-error.jsonl"),
            "HAEORUM_SYNC_LOG_PATH": str(Path(self.temp_dir.name) / "runtime-sync.jsonl"),
            "HAEORUM_CACHE_TTL_SECONDS": "0",
        }
        previous_env = {key: os.environ.get(key) for key in env_overrides}
        os.environ.update(env_overrides)
        try:
            app_main = importlib.reload(sys.modules["app.main"]) if "app.main" in sys.modules else importlib.import_module("app.main")
            with TestClient(app_main.app) as client:
                health = client.get("/health")
                assert health.status_code == 200, health.text
                health_data = health.json()
                assert health_data["engine"] == "local"
                assert health_data["ok"] is True

                widget = client.get("/widget.js")
                assert widget.status_code == 200, widget.text[:200]
                assert "HaeorumAISearch" in widget.text

                search = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "검은 우산", "limit": 3})
                assert search.status_code == 200, search.text
                payload = search.json()
                assert payload["meta"]["query_type"] == "text"
                assert payload["top"] or payload["items"]
                assert payload["suggested_categories"]

                click = client.post(
                    "/api/click-log",
                    json={
                        "mall_id": "shop001",
                        "query_type": "text",
                        "product_id": "P001",
                        "product_url": "https://shop001.haeorumgift.com/product_view.asp?p_idx=P001",
                    },
                )
                assert click.status_code == 200, click.text
                assert click.json()["ok"] is True
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def check_fastapi_admin_runtime_routes(self) -> None:
        import importlib

        try:
            from fastapi.testclient import TestClient
        except ModuleNotFoundError as exc:
            if exc.name != "fastapi":
                raise
            self.check_fastapi_route_declarations()
            return

        env_overrides = {
            "HAEORUM_SEARCH_ENGINE": "local",
            "HAEORUM_ADMIN_API_KEY": "dev-admin-key",
            "HAEORUM_PRODUCT_CSV": str(ROOT / "sample_products.csv"),
            "HAEORUM_SEARCH_LOG_PATH": str(Path(self.temp_dir.name) / "admin-runtime-search.jsonl"),
            "HAEORUM_ERROR_LOG_PATH": str(Path(self.temp_dir.name) / "admin-runtime-error.jsonl"),
            "HAEORUM_SYNC_LOG_PATH": str(Path(self.temp_dir.name) / "admin-runtime-sync.jsonl"),
            "HAEORUM_CACHE_TTL_SECONDS": "0",
        }
        previous_env = {key: os.environ.get(key) for key in env_overrides}
        os.environ.update(env_overrides)
        try:
            app_main = importlib.reload(sys.modules["app.main"]) if "app.main" in sys.modules else importlib.import_module("app.main")
            with TestClient(app_main.app) as client:
                admin_headers = {"X-Admin-Key": "dev-admin-key"}
                invalid_headers = {"X-Admin-Key": "invalid-admin-key"}

                protected_routes = [
                    ("get", "/admin/sync-status"),
                    ("post", "/admin/sync"),
                    ("post", "/admin/reindex"),
                    ("post", "/admin/reindex/P001"),
                    ("post", "/admin/reindex/P/SLASH"),
                    ("post", "/admin/reindex-product"),
                    ("delete", "/admin/product/P001"),
                    ("delete", "/admin/product/P/SLASH"),
                    ("post", "/admin/delete-product"),
                    ("get", "/admin/search-log"),
                    ("get", "/admin/sync-log"),
                    ("get", "/admin/search-insights"),
                    ("get", "/admin/error-log"),
                    ("get", "/admin/metrics"),
                    ("get", "/admin/metrics.prom"),
                ]
                for method, path in protected_routes:
                    response = getattr(client, method)(path, headers=invalid_headers)
                    assert response.status_code == 401, f"{method.upper()} {path} should require admin auth: {response.text}"

                seed = client.post("/api/ai-search", json={"mall_id": "shop001", "q": "우산", "limit": 3})
                assert seed.status_code == 200, seed.text

                sync_status = client.get("/admin/sync-status", headers=admin_headers)
                assert sync_status.status_code == 200, sync_status.text
                sync_status_data = sync_status.json()
                assert sync_status_data["engine"] == "local"
                assert sync_status_data["index"] == "haeorum-products"

                reindex = client.post("/admin/reindex/P001", headers=admin_headers)
                assert reindex.status_code == 200, reindex.text
                reindex_data = reindex.json()
                assert reindex_data["mode"] == "reindex:P001"
                assert reindex_data["indexed"] >= 1
                assert reindex_data["failed"] == 0

                slash_reindex = client.post("/admin/reindex/P/SLASH", headers=admin_headers)
                assert slash_reindex.status_code == 200, slash_reindex.text
                assert slash_reindex.json()["mode"] == "reindex:P/SLASH"

                slash_delete = client.delete("/admin/product/P/SLASH", headers=admin_headers)
                assert slash_delete.status_code == 200, slash_delete.text
                assert slash_delete.json()["mode"] == "delete:P/SLASH"

                reserved_id = "P/SLASH?Q#F"
                body_reindex = client.post(
                    "/admin/reindex-product",
                    json={"product_id": reserved_id},
                    headers=admin_headers,
                )
                assert body_reindex.status_code == 200, body_reindex.text
                assert body_reindex.json()["mode"] == f"reindex:{reserved_id}"

                body_delete = client.post(
                    "/admin/delete-product",
                    json={"product_id": reserved_id},
                    headers=admin_headers,
                )
                assert body_delete.status_code == 200, body_delete.text
                assert body_delete.json()["mode"] == f"delete:{reserved_id}"

                blank_reindex = client.post("/admin/reindex/", headers=admin_headers)
                assert blank_reindex.status_code == 400, blank_reindex.text
                too_long_reindex = client.post("/admin/reindex/" + ("P" * 101), headers=admin_headers)
                assert too_long_reindex.status_code == 400, too_long_reindex.text
                blank_body_reindex = client.post("/admin/reindex-product", json={"product_id": "  "}, headers=admin_headers)
                assert blank_body_reindex.status_code == 422, blank_body_reindex.text

                for path in ["/admin/search-log", "/admin/sync-log", "/admin/error-log"]:
                    response = client.get(f"{path}?limit=10", headers=admin_headers)
                    assert response.status_code == 200, response.text
                    assert isinstance(response.json(), list), path

                insights = client.get("/admin/search-insights?limit=10&min_searches=1", headers=admin_headers)
                assert insights.status_code == 200, insights.text
                insights_data = insights.json()
                assert "search_events" in insights_data
                assert "recommendations" in insights_data

                metrics = client.get("/admin/metrics?limit=100", headers=admin_headers)
                assert metrics.status_code == 200, metrics.text
                metrics_data = metrics.json()
                assert metrics_data["engine"]["backend"] == "local"
                assert metrics_data["search_queue"]["enabled"] is True
                assert metrics_data["search_queue"]["max_concurrency"] >= 1
                assert metrics_data["image_queue"]["enabled"] is True
                assert metrics_data["image_queue"]["max_concurrency"] >= 1
                assert "rate_limit" in metrics_data
                assert "cache" in metrics_data
                assert "alerts" in metrics_data

                prometheus = client.get("/admin/metrics.prom?limit=100", headers=admin_headers)
                assert prometheus.status_code == 200, prometheus.text[:200]
                assert "haeorum_engine_up" in prometheus.text
                assert "haeorum_search_queue_enabled" in prometheus.text
                assert "haeorum_image_search_queue_enabled" in prometheus.text
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def check_fastapi_route_declarations(self) -> None:
        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        expected_tokens = [
            "app = FastAPI",
            '@app.get("/health")',
            '@app.get("/widget.js")',
            '@app.post("/api/ai-search")',
            '@app.post("/api/click-log")',
            '@app.post("/admin/sync"',
            '@app.post("/admin/reindex"',
            '@app.post("/admin/reindex/{product_id:path}"',
            '@app.post("/admin/reindex-product"',
            '@app.delete("/admin/product/{product_id:path}"',
            '@app.post("/admin/delete-product"',
            '@app.get("/admin/sync-status"',
            '@app.get("/admin/search-log"',
            '@app.get("/admin/sync-log"',
            '@app.get("/admin/search-insights"',
            '@app.get("/admin/error-log"',
            '@app.get("/admin/metrics"',
            '@app.get("/admin/metrics.prom"',
            "FileResponse(Path(ROOT) / \"widget\" / \"widget.js\"",
            "validate_public_access(payload, request)",
            "require_admin",
            "normalize_admin_product_id",
            "build_admin_metrics",
            "metrics_to_prometheus",
            "service.log_click",
        ]
        missing = [token for token in expected_tokens if token not in source]
        assert not missing, missing

    def check_image_search(self) -> None:
        result = self.service.search(SearchRequest(mall_id="shop001", image_base64=self.image_base64, limit=20))
        assert result.meta.query_type.value == "image"
        assert result.top, "image search should return results"
        assert all(0 <= item.score <= 1 for item in result.top + result.items)

    def check_image_upload_validation_and_preprocessing(self) -> None:
        max_image_bytes = self.settings.max_image_mb * 1024 * 1024
        for image_format, mime_type in [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]:
            image = validate_image_bytes(
                make_image_bytes(image_format),
                max_bytes=max_image_bytes,
                declared_mime_type=mime_type,
                max_dimension=self.settings.max_image_dimension,
                min_dimension=self.settings.min_image_dimension,
            )
            assert image.mime_type == mime_type
            assert image.sha256
            assert image.perceptual_hash
            assert image.width == 32
            assert image.height == 32

        large = validate_image_bytes(
            make_image_bytes("PNG", width=80, height=40),
            max_bytes=max_image_bytes,
            max_dimension=32,
            min_dimension=self.settings.min_image_dimension,
        )
        assert large.normalized is True
        assert max(large.width or 0, large.height or 0) <= 32

        transparent = validate_image_bytes(
            make_image_bytes("PNG", width=32, height=32, color=(0, 0, 0, 0), mode="RGBA"),
            max_bytes=max_image_bytes,
            min_dimension=self.settings.min_image_dimension,
        )
        assert "transparent_or_cutout_background" in transparent.quality_warnings

        data_url_image = validate_image_base64(self.image.data_url, max_bytes=max_image_bytes)
        assert data_url_image.mime_type == "image/png"

        rejected_cases = [
            (lambda: validate_image_bytes(b"not an image", max_bytes=max_image_bytes), "only JPG, PNG, and WEBP"),
            (
                lambda: validate_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, max_bytes=max_image_bytes),
                "damaged",
            ),
            (
                lambda: validate_image_bytes(
                    make_image_bytes("PNG", width=8, height=8),
                    max_bytes=max_image_bytes,
                    min_dimension=self.settings.min_image_dimension,
                ),
                "too small",
            ),
            (lambda: validate_image_base64("not-base64", max_bytes=max_image_bytes), "not valid base64"),
        ]
        for call, expected_message in rejected_cases:
            try:
                call()
            except ValueError as exc:
                assert expected_message in str(exc)
            else:
                raise AssertionError(f"invalid upload should be rejected: {expected_message}")

    def check_product_image_probe_and_sync_image_validation(self) -> None:
        class Headers:
            def get(self, name: str, default: str = "") -> str:
                return "image/png"

        class FakeResponse:
            headers = Headers()

            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, traceback):  # type: ignore[no-untyped-def]
                return False

            def read(self, size: int) -> bytes:
                return make_png_bytes()

        product = ProductDocument(
            product_id="P-PROBE-1",
            name="워터마크 샘플 이미지 상품",
            category="우산",
            status="active",
            main_image_url="https://images.example.test/watermark/sample/p001.png",
        )
        with patch("app.image_probe.time.sleep") as sleep:
            with patch(
                "app.image_probe.open_image_request",
                side_effect=[TimeoutError("timed out"), FakeResponse()],
            ) as image_open:
                result = ProductImageProbe(
                    max_bytes=1024 * 1024,
                    retry_count=1,
                    retry_delay_seconds=0.01,
                    min_dimension=16,
                ).validate(product)

        assert result.ok is True
        assert result.attempts == 2
        assert "possible_watermark" in result.warnings
        assert "placeholder_or_sample_image" in result.warnings
        assert image_open.call_count == 2
        request = image_open.call_args.args[0]
        assert "image/jpeg" in request.headers["Accept"]
        assert "image/png" in request.headers["Accept"]
        assert "image/webp" in request.headers["Accept"]
        sleep.assert_called_once()
        assert is_retryable_message("image download failed: timed out") is True
        assert is_retryable_message("image download failed: HTTP 404") is False

        unsafe = ProductDocument(
            product_id="P-PROBE-2",
            name="위험한 이미지 URL 상품",
            category="우산",
            status="active",
            main_image_url="https://token@images.example.test/p002.jpg",
        )
        with patch("app.image_probe.open_image_request") as unsafe_image_open:
            unsafe_result = ProductImageProbe(max_bytes=1024 * 1024, retry_count=1).validate(unsafe)
        assert unsafe_result.ok is False
        assert unsafe_result.message and "safe http(s)" in unsafe_result.message
        unsafe_image_open.assert_not_called()

        class FakeImageProbe:
            def validate(self, candidate: ProductDocument) -> ImageProbeResult:
                if candidate.product_id == "P-IMG-OK":
                    return ImageProbeResult(
                        True,
                        candidate.product_id,
                        candidate.image_url,
                        attempts=1,
                        warnings=("possible_watermark",),
                    )
                return ImageProbeResult(
                    False,
                    candidate.product_id,
                    candidate.image_url,
                    "image download failed: HTTP 404",
                    attempts=2,
                )

        csv_path = Path(self.temp_dir.name) / "image-sync-products.csv"
        sync_log_path = Path(self.temp_dir.name) / "image-sync.jsonl"
        csv_path.write_text(
            "\n".join(
                [
                    "product_id,product_name,category_name,status,display_yn,price,main_image_url",
                    "P-IMG-OK,워터마크 우산,우산,active,Y,8500,https://images.example.test/p-ok-watermark.jpg",
                    "P-IMG-BAD,깨진 이미지 우산,우산,active,Y,8500,https://images.example.test/p-bad.jpg",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        settings = Settings(
            engine_backend="local",
            index_name="image-sync",
            product_csv_path=csv_path,
            sync_log_path=sync_log_path,
            validate_product_images=True,
        )
        engine = LocalSearchEngine(
            [
                ProductDocument(
                    product_id="P-IMG-BAD",
                    name="기존 깨진 이미지 우산",
                    category="우산",
                    status="active",
                )
            ]
        )
        sync = SyncService(engine, CsvProductSource(csv_path), settings, image_probe=FakeImageProbe())
        sync_result = sync.reindex_all()
        search = engine.search(EngineQuery(q="우산", limit=10))
        log_text = sync_log_path.read_text(encoding="utf-8")

        assert sync_result.indexed == 1
        assert sync_result.deleted == 1
        assert sync_result.failed == 1
        assert [hit.document.product_id for hit in search] == ["P-IMG-OK"]
        assert "image_probe_failed" in log_text
        assert "image_quality_warning" in log_text
        assert "image_validation_failed" in log_text
        metrics = build_admin_metrics(
            settings,
            engine,
            SearchLogger(Path(self.temp_dir.name) / "image-probe-search.jsonl"),
            SearchLogger(Path(self.temp_dir.name) / "image-probe-error.jsonl"),
            sync,
        )
        assert metrics["sync"]["events"]["image_probe_failed_events"] == 1
        assert metrics["sync"]["events"]["image_quality_warning_events"] == 1

    def check_mixed_search(self) -> None:
        result = self.service.search(SearchRequest(mall_id="shop001", q="검은색", image_base64=self.image_base64, limit=20))
        assert result.meta.query_type.value == "text_image"
        assert result.meta.text_weight == 0.4
        assert result.meta.image_weight == 0.6
        assert result.top, "mixed search should return results"

    def check_multipart_search_contract(self) -> None:
        openapi = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
        request_content = openapi["paths"]["/api/ai-search"]["post"]["requestBody"]["content"]
        multipart = request_content["multipart/form-data"]
        schema = multipart["schema"]
        properties = set(schema["properties"])
        expected_fields = {
            "mall_id",
            "site_id",
            "q",
            "image",
            "limit",
            "offset",
            "category",
            "print_method",
            "material",
            "color",
            "min_price",
            "max_price",
            "quantity",
            "order_qty",
            "max_delivery_days",
            "text_weight",
            "image_weight",
        }
        unsafe_fields = {"api_key", "apiKey", "apikey", "api-key", "x-api-key", "admin_key", "x-admin-key"}
        assert schema["additionalProperties"] is False
        assert expected_fields <= properties
        assert not unsafe_fields & properties
        assert {"required": ["q"]} in schema["anyOf"]
        assert {"required": ["image"]} in schema["anyOf"]
        assert "image" in multipart["examples"]
        assert "mixed" in multipart["examples"]
        assert "site_id_alias" in multipart["examples"]

        allowed_payload = {field: "value" for field in expected_fields}
        assert unsupported_multipart_field_names(allowed_payload) == []
        assert unsupported_multipart_field_names({**allowed_payload, "api_key": "secret"}) == ["api_key"]
        assert unsupported_multipart_field_names({**allowed_payload, "unexpected": "value"}) == ["unexpected"]

        max_image_bytes = self.settings.max_image_mb * 1024 * 1024
        validate_multipart_content_length(str(max_image_bytes), max_image_bytes=max_image_bytes)
        try:
            validate_multipart_content_length(str(max_image_bytes + 1024 * 1024 + 1), max_image_bytes=max_image_bytes)
        except ValueError as exc:
            assert "multipart body exceeds" in str(exc)
        else:
            raise AssertionError("oversized multipart body should be rejected before form parsing")

        class FakeUpload:
            def __init__(self, data: bytes):
                self.data = data
                self.offset = 0

            async def read(self, size: int = -1) -> bytes:
                if self.offset >= len(self.data):
                    return b""
                if size is None or size < 0:
                    size = len(self.data) - self.offset
                chunk = self.data[self.offset : self.offset + size]
                self.offset += len(chunk)
                return chunk

        raw = asyncio.run(read_upload_bytes_limited(FakeUpload(make_png_bytes()), max_bytes=max_image_bytes, chunk_size=7))
        image = validate_image_bytes(
            raw,
            max_bytes=max_image_bytes,
            declared_mime_type="image/png",
            max_dimension=self.settings.max_image_dimension,
            min_dimension=self.settings.min_image_dimension,
        )
        try:
            validate_image_bytes(raw, max_bytes=max_image_bytes, declared_mime_type="image/jpeg")
        except ValueError as exc:
            assert "declared image type" in str(exc)
        else:
            raise AssertionError("multipart image MIME mismatch should be rejected")

        request = SearchRequest.model_validate(
            {
                "site_id": "shop001",
                "q": "검은색",
                "image_base64": image.data_url,
                "limit": 5,
                "text_weight": 0.4,
                "image_weight": 0.6,
            }
        )
        assert request.mall_id == "shop001"
        assert request.query_type.value == "text_image"
        result = self.service.search(request)
        assert result.meta.query_type.value == "text_image"
        assert result.top, "multipart-equivalent mixed search should return results"

    def check_image_queue_metrics(self) -> None:
        gate = ImageSearchGate(max_concurrency=1, queue_timeout_seconds=0)
        with gate.slot():
            assert gate.status()["in_flight"] == 1
            try:
                with gate.slot():
                    pass
            except ImageSearchQueueFull:
                pass
            else:
                raise AssertionError("image queue should reject work when its only slot is occupied")

        queue_status = gate.status()
        assert queue_status["enabled"] is True
        assert queue_status["in_flight"] == 0
        assert queue_status["acquired_events"] == 1
        assert queue_status["queue_full_events"] == 1

        sync = SyncService(self.engine, CsvProductSource(self.settings.product_csv_path), self.settings)
        metrics = build_admin_metrics(
            self.settings,
            self.engine,
            self.service.logger,
            SearchLogger(Path(self.temp_dir.name) / "metrics-error.jsonl"),
            sync,
            image_search_gate=gate,
        )
        assert metrics["image_queue"]["enabled"] is True
        assert metrics["image_queue"]["max_concurrency"] == 1
        assert metrics["image_queue"]["queue_full_events"] == 1
        assert "image_search_queue_full" in {alert["code"] for alert in metrics["alerts"]}
        prometheus = metrics_to_prometheus(metrics)
        assert "haeorum_image_search_queue_enabled 1" in prometheus
        assert "haeorum_image_search_queue_full_events 1" in prometheus

    def check_search_execution_queue_metrics(self) -> None:
        gate = SearchExecutionGate(max_concurrency=1, queue_timeout_seconds=0)
        with gate.slot():
            assert gate.status()["in_flight"] == 1
            try:
                with gate.slot():
                    pass
            except SearchQueueFull:
                pass
            else:
                raise AssertionError("search queue should reject work when its only slot is occupied")

        queue_status = gate.status()
        assert queue_status["enabled"] is True
        assert queue_status["in_flight"] == 0
        assert queue_status["acquired_events"] == 1
        assert queue_status["queue_full_events"] == 1

        sync = SyncService(self.engine, CsvProductSource(self.settings.product_csv_path), self.settings)
        metrics = build_admin_metrics(
            self.settings,
            self.engine,
            self.service.logger,
            SearchLogger(Path(self.temp_dir.name) / "search-queue-error.jsonl"),
            sync,
            search_execution_gate=gate,
        )
        assert metrics["search_queue"]["enabled"] is True
        assert metrics["search_queue"]["max_concurrency"] == 1
        assert metrics["search_queue"]["queue_full_events"] == 1
        assert "search_queue_full" in {alert["code"] for alert in metrics["alerts"]}
        prometheus = metrics_to_prometheus(metrics)
        assert "haeorum_search_queue_enabled 1" in prometheus
        assert "haeorum_search_queue_full_events 1" in prometheus

    def check_rate_limit_controls_and_metrics(self) -> None:
        buckets: dict[str, list[float]] = {}
        assert record_rate_limit_hit(buckets, "search:ip:203.0.113.10", 2, now=100.0) == (True, 1)
        assert record_rate_limit_hit(buckets, "search:ip:203.0.113.10", 2, now=101.0) == (True, 2)
        assert record_rate_limit_hit(buckets, "search:ip:203.0.113.10", 2, now=102.0) == (False, 2)
        assert record_rate_limit_hit(buckets, "search:mall:shop001", 1, now=102.0) == (True, 1)
        assert record_rate_limit_hit(buckets, "search:mall:shop001", 1, now=103.0) == (False, 1)
        assert record_rate_limit_hit(buckets, "image:ip:203.0.113.10", 1, now=104.0) == (True, 1)
        assert record_rate_limit_hit(buckets, "image:ip:203.0.113.10", 1, now=105.0) == (False, 1)
        assert record_rate_limit_hit(buckets, "click:ip:203.0.113.10", 0, now=106.0) == (True, 0)
        assert record_rate_limit_hit(buckets, "search:ip:203.0.113.10", 2, now=200.0) == (True, 1)

        class FailingRedisClient:
            def incr(self, key: str) -> int:
                raise RuntimeError("redis unavailable")

            def expire(self, key: str, seconds: int) -> None:
                pass

        limiter = object.__new__(RedisRateLimiter)
        limiter.client = FailingRedisClient()
        limiter.key_prefix = "hai"
        assert limiter.hit("search:ip:203.0.113.10", 1, now=120.0) == (True, 1)
        assert limiter.hit("search:ip:203.0.113.10", 1, now=121.0) == (False, 1)
        status = limiter.status()
        assert status["backend"] == "redis"
        assert status["redis_enabled"] is True
        assert status["fallback_events"] == 2
        assert status["fallback_active"] is True
        assert status["fallback_bucket_count"] == 1
        assert status["fallback_max_buckets"] == 10000
        assert status["last_error"] == "redis unavailable"

        error_logger = SearchLogger(Path(self.temp_dir.name) / "rate-limit-error.jsonl")
        error_logger.write(
            {
                "type": "api_error",
                "timestamp": "2026-05-21T00:00:00Z",
                "method": "POST",
                "path": "/api/ai-search",
                "status_code": 429,
                "detail": "image search rate limit exceeded for client",
            }
        )
        sync = SyncService(self.engine, CsvProductSource(self.settings.product_csv_path), self.settings)
        metrics = build_admin_metrics(
            Settings(engine_backend="local", redis_url="redis://cache.example.test:6379/0"),
            self.engine,
            self.service.logger,
            error_logger,
            sync,
            rate_limiter=limiter,
        )
        assert metrics["errors"]["rate_limited_events"] == 1
        assert metrics["rate_limit"]["fallback_events"] == 2
        alert_codes = {alert["code"] for alert in metrics["alerts"]}
        assert "rate_limited_requests" in alert_codes
        assert "redis_rate_limit_fallback" in alert_codes
        prometheus = metrics_to_prometheus(metrics)
        assert "haeorum_recent_rate_limited_events 1" in prometheus
        assert "haeorum_rate_limit_redis_enabled 1" in prometheus
        assert "haeorum_rate_limit_fallback_events 2" in prometheus
        assert "haeorum_rate_limit_fallback_active 1" in prometheus

    def check_search_cache_and_sync_invalidation(self) -> None:
        class CountingEngine(LocalSearchEngine):
            def __init__(self, products):
                super().__init__(products)
                self.search_count = 0

            def search(self, query):  # type: ignore[no-untyped-def]
                self.search_count += 1
                return super().search(query)

        csv_path = Path(self.temp_dir.name) / "cache-products.csv"
        csv_path.write_text(
            "\n".join(
                [
                    "product_id,product_name,category_name,status,display_yn,price",
                    "P-CACHE-1,검정 우산,우산,inactive,N,8500",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        settings = Settings(
            engine_backend="local",
            index_name="cache-acceptance",
            product_csv_path=csv_path,
            search_log_path=Path(self.temp_dir.name) / "cache-search.jsonl",
            sync_log_path=Path(self.temp_dir.name) / "cache-sync.jsonl",
            cache_ttl_seconds=30,
            product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
        )
        cache = MemorySearchCache(settings.cache_ttl_seconds)
        engine = CountingEngine(
            [
                ProductDocument(
                    product_id="P-CACHE-1",
                    name="검정 우산",
                    category="우산",
                    status="active",
                )
            ]
        )
        service = AISearchService(engine, settings, SearchLogger(settings.search_log_path), cache=cache)

        first = service.search(SearchRequest(mall_id="shop001", q="우산", limit=5))
        second = service.search(SearchRequest(mall_id="shop001", q="우산", limit=5))
        assert first.top[0].product_id == "P-CACHE-1"
        assert second.top[0].product_id == "P-CACHE-1"
        assert engine.search_count == 1
        assert cache.status()["entry_count"] == 1
        assert [entry["cached"] for entry in service.logger.tail(10)] == [False, True]

        sync = SyncService(engine, CsvProductSource(csv_path), settings, search_cache=cache)
        sync_result = sync.sync_changed()
        assert sync_result.failed == 0
        assert sync_result.deleted == 1
        assert cache.status()["entry_count"] == 0
        cache_events = [entry for entry in sync.logger.tail(20) if entry.get("type") == "search_cache_cleared"]
        assert len(cache_events) == 1
        assert cache_events[0]["cache"]["cleared"] == 1

        fresh = service.search(SearchRequest(mall_id="shop001", q="우산", limit=5))
        assert not fresh.top
        assert engine.search_count == 2

        metrics = build_admin_metrics(
            settings,
            engine,
            service.logger,
            SearchLogger(Path(self.temp_dir.name) / "cache-error.jsonl"),
            sync,
            search_cache=cache,
        )
        assert metrics["search"]["cached_search_events"] == 1
        assert metrics["search"]["cache_hit_rate_percent"] == 33.3
        assert metrics["sync"]["events"]["cache_invalidation_events"] == 1
        assert metrics["cache"]["backend"] == "memory"
        prometheus = metrics_to_prometheus(metrics)
        assert "haeorum_recent_cached_search_events 1" in prometheus
        assert "haeorum_sync_recent_search_cache_clear_events 1" in prometheus

    def check_marqo_engine_adapter_and_reserved_backends(self) -> None:
        engine = MarqoSearchEngine(
            "http://marqo.example.test:8882",
            "haeorum-products",
            "custom-model",
            image_download_thread_count=4,
        )
        text_payload = engine.build_search_payload(
            EngineQuery(
                q="파우치",
                limit=10,
                query_synonyms={"파우치": ("가방", "백")},
                inferred_categories=("가방",),
            )
        )
        assert text_payload["searchMethod"] == "TENSOR"
        assert text_payload["q"] == "파우치 가방 백"
        assert text_payload["searchableAttributes"] == [
            "product_name",
            "category_name",
            "description",
            "keywords",
            "print_methods",
            "materials",
            "colors",
        ]
        assert text_payload["filter"] == "status:(active)"

        image_payload = engine.build_search_payload(
            EngineQuery(image_data_url=self.image.data_url, limit=10)
        )
        assert image_payload["q"] == {self.image.data_url: 1.0}
        assert image_payload["searchableAttributes"] == ["main_image_url"]

        mixed_payload = engine.build_search_payload(
            EngineQuery(
                q="검은색",
                image_data_url=self.image.data_url,
                mall_id="shop001",
                limit=10,
                text_weight=0.4,
                image_weight=0.6,
                strict_mall_filter=True,
            )
        )
        assert mixed_payload["q"][self.image.data_url] == 0.6
        assert mixed_payload["q"]["검은색"] == 0.4
        assert "mall_id:(shop001)" in mixed_payload["filter"]
        assert "searchableAttributes" not in mixed_payload

        filtered_payload = engine.build_search_payload(
            EngineQuery(
                q="텀블러",
                limit=10,
                print_method="UV",
                min_price=1000,
                max_price=10000,
                quantity=100,
                max_delivery_days=5,
            )
        )
        assert filtered_payload["limit"] == 100
        assert "(price:[1000 TO *] OR price_min:[1000 TO *] OR price_max:[1000 TO *])" in filtered_payload["filter"]
        assert "(price:[* TO 10000] OR price_min:[* TO 10000] OR price_max:[* TO 10000])" in filtered_payload["filter"]
        assert "min_order_qty:[* TO 100]" in filtered_payload["filter"]
        assert "delivery_days:[* TO 5]" in filtered_payload["filter"]
        assert "print_methods:(" not in filtered_payload["filter"]
        assert "print_methods" in filtered_payload["attributesToRetrieve"]
        assert "min_order_qty" in filtered_payload["attributesToRetrieve"]
        assert "price_max" in filtered_payload["attributesToRetrieve"]

        upsert_payload = engine.build_upsert_payload(
            [
                ProductDocument(
                    product_id="P-MARQO-1",
                    name="안전한 이미지 텀블러",
                    category="텀블러",
                    status="active",
                    main_image_url="https://images.example.test/p-marqo-1.jpg",
                    print_methods=["UV 인쇄"],
                    materials=["스텐"],
                    colors=["검정"],
                    min_order_qty=100,
                    price_min=9000,
                    price_max=12000,
                    delivery_days=2,
                    product_group_id="G-MARQO",
                    mall_id="shop001",
                ),
                ProductDocument(
                    product_id="P-MARQO-2",
                    name="위험한 이미지 URL 상품",
                    category="텀블러",
                    status="active",
                    main_image_url="https://token@images.example.test/p-marqo-2.jpg",
                ),
            ]
        )
        assert upsert_payload["mediaDownloadThreadCount"] == 4
        assert "main_image_url" in upsert_payload["tensorFields"]
        assert upsert_payload["documents"][0]["main_image_url"] == "https://images.example.test/p-marqo-1.jpg"
        assert upsert_payload["documents"][0]["print_methods"] == "UV 인쇄"
        assert upsert_payload["documents"][0]["materials"] == "스텐"
        assert upsert_payload["documents"][0]["colors"] == "검정"
        assert upsert_payload["documents"][0]["status"] == "active"
        assert upsert_payload["documents"][0]["product_id"] == "P-MARQO-1"
        assert upsert_payload["documents"][0]["_id"].endswith("-product-P-MARQO-1")
        assert upsert_payload["documents"][0]["document_id"] == upsert_payload["documents"][0]["_id"]
        assert "main_image_url" not in upsert_payload["documents"][1]

        factory_engine = create_search_engine(
            Settings(
                engine_backend="marqo",
                marqo_url="http://marqo.example.test:8882",
                index_name="haeorum-products",
                marqo_model="factory-model",
                product_image_download_thread_count=5,
            ),
            preload_local_products=False,
        )
        assert isinstance(factory_engine, MarqoSearchEngine)
        assert factory_engine.model_name == "factory-model"
        assert factory_engine.image_download_thread_count == 5

        for backend, required_component in [
            ("typesense", "multimodal text and image embedding pipeline"),
            ("qdrant", "OpenCLIP text and image embedding service"),
        ]:
            reserved = create_search_engine(Settings(engine_backend=backend), preload_local_products=False)
            health = reserved.health()
            assert health["ready"] is False
            assert health["reserved_adapter"] is True
            assert required_component in health["required_components"]
            try:
                reserved.search(EngineQuery(q="우산"))
            except ReservedSearchEngineUnavailable:
                pass
            else:
                raise AssertionError(f"{backend} reserved adapter should fail closed")

    def check_low_confidence_notice(self) -> None:
        result = self.service.search(SearchRequest(mall_id="shop001", image_base64=self.image_base64, limit=20))
        assert result.top, "image search should return results"
        assert result.meta.low_confidence is True
        assert result.meta.notice, "low confidence result should include notice"

    def check_no_result_notice(self) -> None:
        result = self.service.search(
            SearchRequest(mall_id="shop001", q="검은 우산", category="존재하지않는카테고리", limit=20)
        )
        assert not result.top
        assert not result.items
        assert result.meta.low_confidence is True
        assert result.meta.notice and "검색 결과가 없습니다" in result.meta.notice

    def check_top3_categories_and_items(self) -> None:
        result = self.service.search(SearchRequest(mall_id="shop001", q="우산", limit=20))
        assert 1 <= len(result.top) <= 3
        assert result.items, "related item list should contain products beyond top results"
        assert "우산" in result.suggested_categories

    def check_related_items_pagination(self) -> None:
        first = self.service.search(SearchRequest(mall_id="shop001", image_base64=self.image_base64, limit=2, offset=0))
        second = self.service.search(SearchRequest(mall_id="shop001", image_base64=self.image_base64, limit=2, offset=2))
        assert first.meta.offset == 0
        assert first.meta.has_more is True
        assert first.meta.next_offset == 2
        assert second.meta.offset == 2
        assert first.items and second.items
        assert {item.product_id for item in first.items}.isdisjoint({item.product_id for item in second.items})

    def check_active_filter_and_product_url(self) -> None:
        result = self.service.search(SearchRequest(mall_id="shop001", q="우산", limit=20))
        product_ids = [item.product_id for item in result.top + result.items]
        assert "P015" not in product_ids, "inactive products must not be returned"
        assert all(item.product_url for item in result.top + result.items)
        assert all("shop001.haeorumgift.com" in item.product_url for item in result.top + result.items if item.product_url)

    def check_product_click_log_and_url_safety(self) -> None:
        result = self.service.search(SearchRequest(mall_id="shop001", q="검은 우산", limit=5))
        product = result.top[0]
        assert product.product_url == "https://shop001.haeorumgift.com/product_view.asp?p_idx=P001"

        log_path = Path(self.temp_dir.name) / "click-log-contract.jsonl"
        service = AISearchService(self.engine, self.settings, SearchLogger(log_path))
        click = ClickLogRequest.model_validate(
            {
                "site_id": "shop001",
                "product_id": product.product_id,
                "position": 1,
                "query": "검은 우산",
                "query_type": "text",
                "score_percent": product.score_percent,
                "product_url": product.product_url,
            }
        )
        assert click.mall_id == "shop001"
        service.log_click(click)

        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert entry["type"] == "click"
        assert entry["mall_id"] == "shop001"
        assert entry["product_id"] == "P001"
        assert entry["position"] == 1
        assert entry["query_type"] == "text"
        assert entry["product_url"] == product.product_url

        unsafe_urls = [
            "javascript:alert(1)",
            "//evil.example.test/product/P001",
            "https://user:pass@shop001.haeorumgift.com/product/P001",
            "https://localhost/product/P001",
            "http://169.254.169.254/latest/meta-data",
        ]
        for unsafe_url in unsafe_urls:
            try:
                ClickLogRequest.model_validate(
                    {
                        "mall_id": "shop001",
                        "product_id": "P001",
                        "product_url": unsafe_url,
                    }
                )
            except Exception as exc:
                assert "product_url" in str(exc)
            else:
                raise AssertionError(f"unsafe click product_url should be rejected: {unsafe_url}")

    def check_domain_filters_price_quantity_delivery(self) -> None:
        products = [
            ProductDocument(
                product_id="P-FILTER-1",
                name="UV 인쇄 스텐 검정 텀블러",
                category="텀블러",
                price=8900,
                price_min=8500,
                price_max=9200,
                status="active",
                print_methods=["UV 인쇄", "실크"],
                materials=["스테인리스"],
                colors=["블랙", "실버"],
                min_order_qty=100,
                delivery_days=3,
            ),
            ProductDocument(
                product_id="P-FILTER-2",
                name="전사 인쇄 플라스틱 검정 텀블러",
                category="텀블러",
                price=3900,
                status="active",
                print_methods=["전사"],
                materials=["플라스틱"],
                colors=["블랙"],
                min_order_qty=500,
                delivery_days=7,
            ),
        ]
        log_path = Path(self.temp_dir.name) / "domain-filter-search.jsonl"
        service = AISearchService(LocalSearchEngine(products), self.settings, SearchLogger(log_path))
        result = service.search(
            SearchRequest(
                mall_id="shop001",
                q="텀블러",
                print_method="UV",
                material="스텐",
                color="검정",
                min_price=8000,
                max_price=9500,
                quantity=100,
                max_delivery_days=3,
                limit=10,
            )
        )
        assert [item.product_id for item in result.top + result.items] == ["P-FILTER-1"]
        log_entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert log_entry["filters"]["print_method"] == "UV"
        assert log_entry["filters"]["material"] == "스텐"
        assert log_entry["filters"]["color"] == "검정"
        assert log_entry["filters"]["min_price"] == 8000
        assert log_entry["filters"]["max_price"] == 9500
        assert log_entry["filters"]["quantity"] == 100
        assert log_entry["filters"]["max_delivery_days"] == 3

    def check_public_mall_config(self) -> None:
        malls = load_mall_configs(ROOT / "sample_malls.json")
        settings = Settings(
            engine_backend="local",
            product_csv_path=ROOT / "sample_products.csv",
            search_log_path=Path(self.temp_dir.name) / "mall-search.jsonl",
            malls=malls,
        )
        service = AISearchService(self.engine, settings, SearchLogger(settings.search_log_path))
        validate_mall_access(
            settings,
            "shop001",
            "public-shop001-dev-key",
            origin="https://shop001.haeorumgift.com",
        )
        try:
            validate_mall_access(
                settings,
                "shop001",
                "wrong-key",
                origin="https://shop001.haeorumgift.com",
            )
        except PublicAccessError as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("wrong public API key should be rejected")
        try:
            validate_mall_access(
                settings,
                "shop001",
                "public-shop001-dev-key",
                origin="https://evil.example.test",
            )
        except PublicAccessError as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("wrong Origin should be rejected")
        result = service.search(SearchRequest(mall_id="shop001", q="우산", limit=5))
        assert result.top[0].product_url == "https://shop001.haeorumgift.com/product_view.asp?p_idx=P001"

    def check_production_env_fail_closed_and_access_policy(self) -> None:
        production_mall = MallConfig(
            mall_id="shop001",
            api_key="public-shop001-production-key",
            allowed_origins=("https://shop001.haeorumgift.com",),
            product_url_template="https://shop001.haeorumgift.com/product_view.asp?p_idx={product_id}",
        )
        production_settings = Settings(
            environment="production",
            engine_backend="marqo",
            admin_api_key="admin-production-key-123456",
            cors_origins=("https://shop001.haeorumgift.com",),
            malls={"shop001": production_mall},
            product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
            sync_interval_seconds=3600,
        )
        validate_settings(production_settings)
        validate_mall_access(
            production_settings,
            "shop001",
            "public-shop001-production-key",
            origin="https://shop001.haeorumgift.com",
        )

        def assert_rejected(settings: Settings, message: str) -> None:
            try:
                validate_settings(settings)
            except ValueError as exc:
                assert message in str(exc), str(exc)
            else:
                raise AssertionError(f"production settings should reject {message}")

        assert_rejected(
            Settings(
                environment="production",
                engine_backend="local",
                admin_api_key="admin-production-key-123456",
                cors_origins=("https://shop001.haeorumgift.com",),
                malls={"shop001": production_mall},
            ),
            "HAEORUM_SEARCH_ENGINE",
        )
        assert_rejected(
            Settings(
                environment="production",
                engine_backend="marqo",
                admin_api_key="dev-admin-key",
                cors_origins=("https://shop001.haeorumgift.com",),
                malls={"shop001": production_mall},
            ),
            "HAEORUM_ADMIN_API_KEY",
        )
        assert_rejected(
            Settings(
                environment="production",
                engine_backend="marqo",
                admin_api_key="admin-production-key-123456",
                cors_origins=("*",),
                malls={"shop001": production_mall},
            ),
            "HAEORUM_CORS_ORIGINS",
        )
        assert_rejected(
            Settings(
                environment="production",
                engine_backend="marqo",
                admin_api_key="admin-production-key-123456",
                cors_origins=("https://shop001.haeorumgift.com",),
                malls={},
            ),
            "HAEORUM_MALL_CONFIG_PATH",
        )
        assert_rejected(
            Settings(
                environment="production",
                engine_backend="marqo",
                admin_api_key="admin-production-key-123456",
                cors_origins=("https://shop001.haeorumgift.com",),
                malls={
                    "shop001": MallConfig(
                        mall_id="shop001",
                        api_key="sample",
                        allowed_origins=("https://shop001.haeorumgift.com",),
                        product_url_template="https://shop001.haeorumgift.com/product_view.asp?p_idx={product_id}",
                    )
                },
            ),
            "placeholder api_key",
        )
        assert_rejected(
            Settings(
                environment="production",
                engine_backend="marqo",
                admin_api_key="admin-production-key-123456",
                cors_origins=("https://shop001.haeorumgift.com",),
                malls={"shop001": production_mall},
                sync_interval_seconds=3601,
            ),
            "HAEORUM_SYNC_INTERVAL_SECONDS",
        )
        try:
            validate_mall_access(
                production_settings,
                "shop001",
                "wrong-key",
                origin="https://shop001.haeorumgift.com",
            )
        except PublicAccessError as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("production mall access should reject wrong API key")
        try:
            validate_mall_access(
                production_settings,
                "shop001",
                "public-shop001-production-key",
                origin="https://evil.example.test",
            )
        except PublicAccessError as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("production mall access should reject wrong Origin")

    def check_api_smoke_security_and_admin_contracts(self) -> None:
        click_log_success_calls = 0

        def search_result(product_id: str = "P001") -> dict[str, object]:
            return {
                "product_id": product_id,
                "name": "검정 3단 자동 우산",
                "category": "우산",
                "price": 8500,
                "image_url": f"https://image.example.test/{product_id}.jpg",
                "product_url": f"https://shop001.example.test/product/{product_id}",
                "score": 0.91,
                "score_percent": 91.0,
                "mall_id": "shop001",
                "source_scores": {"text": 0.91, "image": 0.9},
            }

        def search_response(query_type: str) -> dict[str, object]:
            return {
                "top": [search_result("P001")],
                "items": [search_result("P002")],
                "suggested_categories": ["우산"],
                "meta": {
                    "query_type": query_type,
                    "elapsed_ms": 120.0,
                    "engine": "marqo",
                    "limit": 20,
                    "offset": 0,
                    "has_more": False,
                    "next_offset": None,
                    "mall_id": "shop001",
                    "text_weight": None if query_type == "image" else 1.0 if query_type == "text" else 0.4,
                    "image_weight": None if query_type == "text" else 1.0 if query_type == "image" else 0.6,
                    "low_confidence": False,
                    "notice": None,
                },
            }

        def has_conflicting_mall_alias(payload: dict[str, object] | None) -> bool:
            if not payload:
                return False
            mall_id = str(payload.get("mall_id") or "").strip()
            site_id = str(payload.get("site_id") or "").strip()
            return bool(mall_id and site_id and mall_id != site_id)

        def fake_request_json(
            method: str,
            url: str,
            payload: dict[str, object] | None = None,
            headers: dict[str, str] | None = None,
        ) -> tuple[int, object, float]:
            nonlocal click_log_success_calls
            headers = headers or {}
            if url.endswith("/health"):
                return 200, {"ready": True}, 1.0
            if url.endswith("/openapi.json"):
                filter_properties = {
                    "print_method": {},
                    "material": {},
                    "color": {},
                    "min_price": {},
                    "max_price": {},
                    "quantity": {},
                    "order_qty": {},
                    "max_delivery_days": {},
                }
                return (
                    200,
                    {
                        "paths": {
                            "/api/ai-search": {
                                "post": {
                                    "requestBody": {
                                        "content": {
                                            "multipart/form-data": {
                                                "schema": {"properties": filter_properties}
                                            }
                                        }
                                    }
                                }
                            },
                            "/api/click-log": {
                                "post": {
                                    "responses": {"429": {"$ref": "#/components/responses/RateLimited"}}
                                }
                            },
                        },
                        "components": {"schemas": {"SearchRequest": {"properties": filter_properties}}},
                    },
                    1.0,
                )
            if headers.get("X-Admin-Key") == "invalid-smoke-admin-key":
                return 401, {"detail": "admin authentication required"}, 1.0
            if "api_key=" in url or "api-key=" in url or "admin_key=" in url:
                return 400, {"detail": "api_key query parameter is not supported; use X-API-Key header"}, 1.0
            if payload and any(
                key in payload
                for key in ("api_key", "apiKey", "api-key", "x-api-key", "admin_key", "x-admin-key")
            ):
                return 400, {"detail": "API key request body field is not supported; use X-API-Key header"}, 1.0
            if has_conflicting_mall_alias(payload):
                return 400, {"detail": "mall_id and site_id must match when both are provided"}, 1.0
            if headers.get("X-API-Key") == "invalid-smoke-api-key":
                return 401, {"detail": "invalid API key"}, 1.0
            if headers.get("Origin") == "https://invalid-origin.example.test":
                return 403, {"detail": "origin is not allowed"}, 1.0
            if "/admin/sync-status" in url:
                return 200, {"engine": "marqo", "index": "products"}, 1.0
            if "/admin/search-log" in url:
                return (
                    200,
                    [
                        {
                            "type": "search",
                            "query_type": "text",
                            "q": "우산",
                            "normalized_query": "우산",
                            "inferred_categories": ["우산"],
                            "image_perceptual_hash": None,
                        },
                        {
                            "type": "search",
                            "query_type": "image",
                            "q": None,
                            "normalized_query": None,
                            "inferred_categories": [],
                            "image_perceptual_hash": "0000000000000000",
                            "image_size_bytes": 120,
                            "image_width": 32,
                            "image_height": 32,
                            "image_normalized": False,
                            "image_quality_warnings": [],
                        },
                    ],
                    1.0,
                )
            if "/admin/sync-log" in url:
                return 200, [{"mode": "sync", "indexed": 1}], 1.0
            if "/admin/error-log" in url:
                return 200, [], 1.0
            if "/admin/search-insights" in url:
                return (
                    200,
                    {
                        "search_events": 2,
                        "click_events": 1,
                        "top_queries": [],
                        "mixed_weight_performance": [],
                        "image_quality_warning_counts": {},
                        "recommendations": [],
                    },
                    1.0,
                )
            if "/admin/metrics" in url:
                return (
                    200,
                    {
                        "engine": {"ok": True, "backend": "marqo"},
                        "sync": {},
                        "search": {},
                        "errors": {},
                        "rate_limit": {
                            "backend": "memory",
                            "redis_enabled": False,
                            "fallback_events": 0,
                            "fallback_active": False,
                            "fallback_bucket_count": 0,
                            "fallback_max_buckets": 10000,
                        },
                        "cache": {"backend": "memory", "redis_enabled": False, "ttl_seconds": 30, "error_count": 0},
                        "search_queue": {
                            "enabled": True,
                            "max_concurrency": 64,
                            "queue_timeout_seconds": 2.0,
                            "in_flight": 0,
                            "queue_full_events": 0,
                        },
                        "image_queue": {
                            "enabled": True,
                            "max_concurrency": 8,
                            "queue_timeout_seconds": 2.0,
                            "in_flight": 0,
                            "queue_full_events": 0,
                        },
                        "logs": {},
                        "disk": {},
                        "process": {},
                        "system": {},
                        "alerts": [],
                    },
                    1.0,
                )
            if url.endswith("/api/ai-search") and payload and "unexpected" in payload:
                return 400, {"detail": "extra inputs are not permitted: unexpected"}, 1.0
            if url.endswith("/api/ai-search") and payload and not payload.get("q") and not payload.get("image_base64"):
                return 400, {"detail": "q or image_base64 is required"}, 1.0
            if (
                url.endswith("/api/ai-search")
                and payload
                and payload.get("min_price") == 2000
                and payload.get("max_price") == 1000
            ):
                return 400, {"detail": "min_price cannot be greater than max_price"}, 1.0
            if url.endswith("/api/ai-search") and payload and payload.get("q") == "small image smoke":
                return 400, {"detail": "image is too small; minimum dimension is 16px"}, 1.0
            if url.endswith("/api/click-log"):
                if payload and "unexpected" in payload:
                    return 400, {"detail": "extra inputs are not permitted: unexpected"}, 1.0
                if payload and not payload.get("product_id"):
                    return 400, {"detail": "product_id is required"}, 1.0
                product_url = str(payload.get("product_url") or "") if payload else ""
                product_id = str(payload.get("product_id") or "") if payload else ""
                authority = product_url.split("://", 1)[1].split("/", 1)[0] if "://" in product_url else ""
                if product_url.startswith("javascript:") or "@" in authority:
                    return 400, {"detail": "product_url must be an absolute http(s) URL"}, 1.0
                if product_url.startswith("https://foreign.example.test/"):
                    return 400, {"detail": "product_url is not allowed for mall"}, 1.0
                if product_url.startswith("https://shop001.example.test/__wrong_product_path__/"):
                    return 400, {"detail": "product_url does not match mall product URL template"}, 1.0
                if product_url and not product_url_contains_product_id(product_url, product_id):
                    return 400, {"detail": "product_url must contain product_id"}, 1.0
                click_log_success_calls += 1
                if click_log_success_calls >= 3:
                    return 429, {"detail": "click log rate limit exceeded for client"}, 1.0
                return 200, {"ok": True}, 1.0
            query_type = (
                "text_image"
                if payload and payload.get("image_base64") and payload.get("q")
                else "image"
                if payload and payload.get("image_base64")
                else "text"
            )
            return 200, search_response(query_type), 1.0

        def fake_request_raw_json(
            method: str,
            url: str,
            body: bytes,
            headers: dict[str, str] | None = None,
        ) -> tuple[int, dict[str, object], float]:
            if url.endswith("/api/ai-search") and len(body) > 1024 * 1024:
                return 413, {"detail": "JSON body exceeds limit"}, 1.0
            if url.endswith("/api/ai-search") or url.endswith("/api/click-log"):
                return 400, {"detail": "invalid JSON body"}, 1.0
            return 200, {}, 1.0

        def fake_request_text(
            method: str,
            url: str,
            headers: dict[str, str] | None = None,
        ) -> tuple[int, str, float]:
            headers = headers or {}
            if headers.get("X-Admin-Key") == "invalid-smoke-admin-key":
                return 401, "admin authentication required", 1.0
            return (
                200,
                "\n".join(
                    [
                        "# TYPE haeorum_engine_up gauge",
                        "haeorum_engine_up 1",
                        "haeorum_recent_search_events 3",
                        "haeorum_rate_limit_redis_enabled 0",
                        "haeorum_search_cache_redis_enabled 0",
                        "haeorum_search_cache_error_events 0",
                        "haeorum_search_queue_enabled 1",
                        "haeorum_search_queue_full_events 0",
                        "haeorum_image_search_queue_enabled 1",
                        "haeorum_image_search_queue_full_events 0",
                        "haeorum_operational_alerts 0",
                    ]
                )
                + "\n",
                1.0,
            )

        def fake_request_multipart(
            url: str,
            fields: dict[str, object],
            files: dict[str, tuple[str, str, bytes]],
            headers: dict[str, str] | None = None,
        ) -> tuple[int, dict[str, object], float]:
            if any(
                key in fields for key in ("api_key", "apiKey", "api-key", "x-api-key", "admin_key", "x-admin-key")
            ):
                return 400, {"detail": "API key request body field is not supported; use X-API-Key header"}, 1.0
            unsupported = unsupported_multipart_field_names(fields)
            if unsupported:
                return 400, {"detail": f"unsupported multipart field: {unsupported[0]}"}, 1.0
            if has_conflicting_mall_alias(fields):
                return 400, {"detail": "mall_id and site_id must match when both are provided"}, 1.0
            if any(filename == "damaged.png" for filename, _, _ in files.values()):
                return 400, {"detail": "image is damaged or cannot be decoded"}, 1.0
            if any(filename == "small.png" for filename, _, _ in files.values()):
                return 400, {"detail": "image is too small; minimum dimension is 16px"}, 1.0
            if any(len(content) >= 1024 * 1024 for _, _, content in files.values()):
                return 413, {"detail": "multipart body exceeds limit"}, 1.0
            if any(content_type == "text/plain" for _, content_type, _ in files.values()):
                return 400, {"detail": "unsupported image MIME type"}, 1.0
            return 200, search_response("image"), 1.0

        def fake_request_raw_multipart(
            url: str,
            body: bytes,
            boundary: str = "----haeorum-ai-search-smoke-boundary",
            headers: dict[str, str] | None = None,
        ) -> tuple[int, dict[str, object], float]:
            return 400, {"detail": "invalid multipart form body"}, 1.0

        def fake_request_preflight(
            url: str,
            origin: str,
            request_method: str = "POST",
            request_headers: str = "content-type,x-api-key",
        ) -> tuple[int, dict[str, str], float]:
            if origin == "https://invalid-origin.example.test":
                return 400, {}, 1.0
            return 200, {"access-control-allow-origin": origin}, 1.0

        args = type(
            "Args",
            (),
            {
                "base_url": "https://ai.example.test",
                "mall_id": "shop001",
                "api_key": "public-shop001-production-key",
                "origin": "https://shop001.example.test",
                "expected_product_url_prefix": "https://shop001.example.test/product",
                "mall_config": "",
                "invalid_api_key": "invalid-smoke-api-key",
                "invalid_origin": "https://invalid-origin.example.test",
                "invalid_admin_key": "invalid-smoke-admin-key",
                "admin_key": "admin-production-key-123456",
                "oversized_upload_mb": 1,
                "expect_click_rate_limit": True,
                "click_rate_limit_probe_count": 3,
            },
        )()
        with patch("scripts.api_smoke_test.request_json", fake_request_json), patch(
            "scripts.api_smoke_test.request_multipart",
            fake_request_multipart,
        ), patch(
            "scripts.api_smoke_test.request_raw_multipart",
            fake_request_raw_multipart,
        ), patch(
            "scripts.api_smoke_test.request_raw_json",
            fake_request_raw_json,
        ), patch(
            "scripts.api_smoke_test.request_text",
            fake_request_text,
        ), patch(
            "scripts.api_smoke_test.request_preflight",
            fake_request_preflight,
        ):
            report = run_api_smoke(args)

        assert report["ok"] is True, report
        checks = {check["name"]: check for check in report["checks"]}
        for name in [
            "cors_preflight",
            "invalid_cors_preflight_rejected",
            "click_log_cors_preflight",
            "invalid_click_log_cors_preflight_rejected",
            "text_search",
            "site_id_search",
            "conflicting_site_id_rejected",
            "image_search",
            "multipart_image_search",
            "mixed_search",
            "invalid_api_key_rejected",
            "query_api_key_rejected",
            "body_api_key_rejected",
            "multipart_body_api_key_rejected",
            "invalid_origin_rejected",
            "invalid_search_payload_rejected",
            "malformed_search_json_rejected",
            "site_id_click_log",
            "conflicting_click_site_id_rejected",
            "invalid_click_api_key_rejected",
            "unsafe_click_product_url_rejected",
            "foreign_click_product_url_rejected",
            "click_product_url_template_prefix_mismatch_rejected",
            "click_product_url_product_id_mismatch_rejected",
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
        ]:
            assert checks[name]["ok"] is True, checks[name]
        assert checks["text_search"]["response_contract_ok"] is True
        assert checks["image_search"]["response_contract_ok"] is True
        assert checks["mixed_search"]["response_contract_ok"] is True
        assert checks["sync_status"]["sync_status_engine"] == "marqo"
        assert checks["metrics"]["engine_backend"] == "marqo"
        assert checks["sensitive_log_redaction"]["sensitive_log_redaction_ok"] is True
        assert checks["sensitive_log_redaction"]["checked_log_endpoints"] == ["error_log", "search_log", "sync_log"]

    def check_security_and_server_preflight_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nginx_config = temp_path / "haeorum-ai-search.conf"
            nginx_config.write_text(
                """
                upstream haeorum_ai_search_api {
                    least_conn;
                    server 127.0.0.1:8000 max_fails=3 fail_timeout=10s;
                    server 127.0.0.1:8001 max_fails=3 fail_timeout=10s;
                    keepalive 64;
                }
                server {
                    client_max_body_size 6m;
                    location / {
                        proxy_set_header X-Real-IP $remote_addr;
                        proxy_set_header X-Forwarded-For $remote_addr;
                        proxy_set_header Forwarded "";
                    }
                }
                """,
                encoding="utf-8",
            )
            logrotate_config = temp_path / "haeorum-ai-search.logrotate"
            logrotate_config.write_text(
                """
                /var/log/haeorum-ai-search/*.jsonl {
                    daily
                    rotate 14
                    missingok
                    notifempty
                    compress
                    delaycompress
                    copytruncate
                    create 0640 haeorum haeorum
                }
                """,
                encoding="utf-8",
            )
            systemd_service = temp_path / "haeorum-ai-search.service"
            systemd_service.write_text(
                """
                [Service]
                User=haeorum
                WorkingDirectory=/opt/haeorum-ai-search
                EnvironmentFile=/etc/haeorum-ai-search/haeorum-ai-search.env
                ExecStart=/opt/haeorum-ai-search/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
                Restart=always
                RestartSec=5
                LimitNOFILE=65535
                NoNewPrivileges=true
                ReadWritePaths=/var/log/haeorum-ai-search
                """,
                encoding="utf-8",
            )
            sync_systemd_service = temp_path / "haeorum-ai-sync.service"
            sync_systemd_service.write_text(
                """
                [Service]
                User=haeorum
                WorkingDirectory=/opt/haeorum-ai-search
                EnvironmentFile=/etc/haeorum-ai-search/haeorum-ai-search.env
                ExecStart=/opt/haeorum-ai-search/.venv/bin/python -m app.sync_worker --mode sync
                Restart=always
                RestartSec=10
                LimitNOFILE=65535
                NoNewPrivileges=true
                ReadWritePaths=/var/log/haeorum-ai-search
                """,
                encoding="utf-8",
            )
            reindex_systemd_service = temp_path / "haeorum-ai-reindex.service"
            reindex_systemd_service.write_text(
                """
                [Service]
                Type=oneshot
                User=haeorum
                WorkingDirectory=/opt/haeorum-ai-search
                EnvironmentFile=/etc/haeorum-ai-search/haeorum-ai-search.env
                ExecStart=/opt/haeorum-ai-search/.venv/bin/python -m app.sync_worker --mode reindex --once
                LimitNOFILE=65535
                NoNewPrivileges=true
                ReadWritePaths=/var/log/haeorum-ai-search
                """,
                encoding="utf-8",
            )
            reindex_systemd_timer = temp_path / "haeorum-ai-reindex.timer"
            reindex_systemd_timer.write_text(
                """
                [Timer]
                OnCalendar=*-*-* 03:00:00
                Persistent=true
                Unit=haeorum-ai-reindex.service

                [Install]
                WantedBy=timers.target
                """,
                encoding="utf-8",
            )
            service_env = temp_path / "haeorum-ai-search.env"
            service_env.write_text("HAEORUM_ENV=production\nHAEORUM_ADMIN_API_KEY=admin-production-key-123456\n", encoding="utf-8")
            if os.name == "posix":
                service_env.chmod(0o640)

            settings = Settings(
                environment="production",
                engine_backend="marqo",
                admin_api_key="admin-production-key-123456",
                cors_origins=("https://shop001.example.test",),
                sync_alert_webhook_url="https://alerts.example.test/sync",
                product_url_template="https://{mall_id}.example.test/product/{product_id}",
                sync_interval_seconds=3600,
                malls={
                    "shop001": MallConfig(
                        mall_id="shop001",
                        api_key="public-shop001-production-key",
                        product_url_template="https://shop001.example.test/product/{product_id}",
                        allowed_origins=("https://shop001.example.test",),
                    )
                },
            )
            security = build_security_report(
                settings,
                base_url="https://ai-search.example.test",
                mssql_ip_restricted=True,
                nginx_config_path=nginx_config,
                logrotate_config_path=logrotate_config,
                systemd_service_path=systemd_service,
                sync_systemd_service_path=sync_systemd_service,
                reindex_systemd_service_path=reindex_systemd_service,
                reindex_systemd_timer_path=reindex_systemd_timer,
                service_env_file_path=service_env,
            )
            assert security["ok"] is True, security
            assert security["failed_checks"] == []
            assert security["public_base_url"] is True
            assert security["nginx_client_max_body_size"] is True
            assert security["nginx_upstream_resilience"] is True
            assert security["nginx_upstream"]["server_count"] == 2
            assert security["nginx_forwarded_for_safety"] is True
            assert security["systemd_restart_policy"] is True
            assert security["systemd_sync_worker"] is True
            assert security["systemd_reindex_service"] is True
            assert security["systemd_reindex_timer"] is True
            assert security["logrotate_config"] is True
            assert security["service_env_file_permissions"] is True
            assert security["sync_failure_alerting"] is True

            insecure = build_security_report(
                Settings(),
                base_url="http://localhost:8000",
                mssql_ip_restricted=False,
                nginx_config_path=nginx_config,
                logrotate_config_path=logrotate_config,
                systemd_service_path=systemd_service,
                sync_systemd_service_path=sync_systemd_service,
                reindex_systemd_service_path=reindex_systemd_service,
                reindex_systemd_timer_path=reindex_systemd_timer,
            )
            assert insecure["ok"] is False
            assert "https" in insecure["failed_checks"]
            assert "public_base_url" in insecure["failed_checks"]
            assert "admin_key" in insecure["failed_checks"]
            assert "production_env" in insecure["failed_checks"]

            unsafe_cors = build_security_report(
                Settings(
                    environment="production",
                    engine_backend="marqo",
                    admin_api_key="admin-production-key-123456",
                    cors_origins=("https://localhost.",),
                    malls={
                        "shop001": MallConfig(
                            mall_id="shop001",
                            api_key="public-shop001-production-key",
                            product_url_template="https://169.254.169.254/product/{product_id}",
                            allowed_origins=("https://169.254.169.254",),
                        )
                    },
                ),
                base_url="https://ai-search.example.test",
                mssql_ip_restricted=True,
                sync_alerting_configured=True,
                nginx_config_path=nginx_config,
                logrotate_config_path=logrotate_config,
                systemd_service_path=systemd_service,
                sync_systemd_service_path=sync_systemd_service,
                reindex_systemd_service_path=reindex_systemd_service,
                reindex_systemd_timer_path=reindex_systemd_timer,
            )
            assert unsafe_cors["ok"] is False
            assert "cors_origins_safe_public" in unsafe_cors["failed_checks"]
            assert "allowed_origins_safe_public" in unsafe_cors["failed_checks"]
            assert "product_url_templates_safe_public" in unsafe_cors["failed_checks"]

            args = type(
                "Args",
                (),
                {
                    "role": "api",
                    "disk_path": "/var/log/haeorum-ai-search",
                    "min_cpu": 0,
                    "min_memory_gb": 0,
                    "min_disk_free_gb": 0,
                    "min_open_files": 0,
                    "require_docker": True,
                    "require_compose": True,
                    "require_pyodbc": True,
                    "expected_odbc_driver": "ODBC Driver 18 for SQL Server",
                    "allow_non_linux": False,
                    "allow_unsupported_os": False,
                    "timeout": 5,
                },
            )()
            system_info = {
                "platform": "Linux",
                "platform_release": "5.15.0",
                "machine": "x86_64",
                "python_version": "3.13.1",
                "python_executable": "/usr/bin/python3",
                "cpu_count": 8,
                "memory_total_bytes": 16 * 1024**3,
                "disk_free_bytes": 80 * 1024**3,
                "disk_total_bytes": 200 * 1024**3,
                "open_file_limit_soft": 65535,
                "open_file_limit_hard": 65535,
                "os_release": {"ID": "ubuntu", "VERSION_ID": "22.04"},
            }

            def fake_runner(command: list[str], timeout: int) -> dict[str, object]:
                if command == ["docker", "--version"]:
                    return {"ok": True, "output": "Docker version 25.0.3, build abc"}
                if command == ["docker", "compose", "version"]:
                    return {"ok": True, "output": "Docker Compose version v2.24.0"}
                return {"ok": False, "error": "unexpected command"}

            preflight = build_server_preflight_report(
                args,
                command_runner=fake_runner,
                module_checker=lambda module: True,
                odbc_driver_provider=lambda: ["ODBC Driver 18 for SQL Server"],
                system_info=system_info,
            )
            assert preflight["ok"] is True, preflight
            assert preflight["failed_checks"] == []
            checks_by_name = {check["name"]: check for check in preflight["checks"]}
            assert checks_by_name["linux_host"]["ok"] is True
            assert checks_by_name["supported_linux_release"]["baseline"] == "Ubuntu 20.04+"
            assert checks_by_name["python_version"]["ok"] is True
            assert checks_by_name["python_modules"]["missing"] == []
            assert checks_by_name["odbc_driver"]["ok"] is True
            assert checks_by_name["host_resources"]["ok"] is True
            assert checks_by_name["docker"]["version"] == "25.0.3"
            assert checks_by_name["docker_compose"]["ok"] is True

            weak_preflight = build_server_preflight_report(
                args,
                command_runner=fake_runner,
                module_checker=lambda module: module != "pyodbc",
                odbc_driver_provider=lambda: [],
                system_info={**system_info, "cpu_count": 2},
            )
            assert weak_preflight["ok"] is False
            assert "python_modules" in weak_preflight["failed_checks"]
            assert "host_resources" in weak_preflight["failed_checks"]

            old_linux = build_server_preflight_report(
                args,
                command_runner=fake_runner,
                module_checker=lambda module: True,
                odbc_driver_provider=lambda: ["ODBC Driver 18 for SQL Server"],
                system_info={**system_info, "os_release": {"ID": "centos", "VERSION_ID": "7"}},
            )
            assert old_linux["ok"] is False
            assert "supported_linux_release" in old_linux["failed_checks"]

    def check_mall_price_and_visibility_policy(self) -> None:
        config_path = Path(self.temp_dir.name) / "malls-policy.json"
        config_path.write_text(
            json.dumps(
                {
                    "malls": [
                        {
                            "mall_id": "shop001",
                            "api_key": "public-shop001-policy-key",
                            "product_url_template": "https://shop001.haeorumgift.com/product_view.asp?p_idx={product_id}",
                            "allowed_origins": ["https://shop001.haeorumgift.com"],
                            "price_multiplier": 1.1,
                            "price_adjustment": 500,
                            "price_round_to": 100,
                        },
                        {
                            "mall_id": "shop002",
                            "api_key": "public-shop002-policy-key",
                            "product_url_template": "https://shop002.haeorumgift.com/product_view.asp?p_idx={product_id}",
                            "allowed_origins": ["https://shop002.haeorumgift.com"],
                            "hide_prices": True,
                            "excluded_product_ids": ["P-MALL-2"],
                            "excluded_categories": ["비노출카테고리"],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        products = [
            ProductDocument(
                product_id="P-MALL-1",
                name="판촉 텀블러",
                category="텀블러",
                price=10000,
                status="active",
            ),
            ProductDocument(
                product_id="P-MALL-2",
                name="판촉 볼펜",
                category="필기구",
                price=2000,
                status="active",
            ),
            ProductDocument(
                product_id="P-MALL-3",
                name="판촉 비노출 상품",
                category="비노출카테고리",
                price=3000,
                status="active",
            ),
        ]
        settings = Settings(
            engine_backend="local",
            product_csv_path=ROOT / "sample_products.csv",
            search_log_path=Path(self.temp_dir.name) / "mall-policy-search.jsonl",
            malls=load_mall_configs(config_path),
            product_url_template="https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}",
            cache_ttl_seconds=0,
        )
        service = AISearchService(LocalSearchEngine(products), settings, SearchLogger(settings.search_log_path))

        shop001 = service.search(SearchRequest(mall_id="shop001", q="텀블러", limit=5))
        assert shop001.top[0].product_id == "P-MALL-1"
        assert shop001.top[0].price == 11500
        assert shop001.top[0].product_url == "https://shop001.haeorumgift.com/product_view.asp?p_idx=P-MALL-1"

        shop002_price = service.search(SearchRequest(mall_id="shop002", q="텀블러", limit=5))
        assert shop002_price.top[0].product_id == "P-MALL-1"
        assert shop002_price.top[0].price is None
        assert shop002_price.top[0].product_url == "https://shop002.haeorumgift.com/product_view.asp?p_idx=P-MALL-1"

        shop002_visibility = service.search(SearchRequest(mall_id="shop002", q="판촉", limit=10))
        assert [item.product_id for item in shop002_visibility.top + shop002_visibility.items] == ["P-MALL-1"]

    def check_scaled_mall_config_1700(self) -> None:
        csv_path = Path(self.temp_dir.name) / "malls-1700.csv"
        config_path = Path(self.temp_dir.name) / "malls-1700.json"
        rows = ["mall_id,domain,api_key"]
        rows.extend(
            f"shop{index:04d},shop{index:04d}.haeorumgift.com,public-shop{index:04d}-acceptance-key"
            for index in range(1, 1701)
        )
        csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        report = build_mall_config_from_csv(csv_path, sort_by_mall_id=True, min_count=1700)
        assert report["ok"] is True, report["problems"][:5]
        config_path.write_text(json.dumps(report["config"], ensure_ascii=False), encoding="utf-8")
        validation = validate_mall_config(config_path, min_count=1700)
        assert validation["ok"] is True, validation["problems"][:5]

        malls = load_mall_configs(config_path)
        assert len(malls) == 1700
        settings = Settings(
            engine_backend="local",
            product_csv_path=ROOT / "sample_products.csv",
            search_log_path=Path(self.temp_dir.name) / "scaled-mall-search.jsonl",
            malls=malls,
        )
        validate_mall_access(
            settings,
            "shop1700",
            "public-shop1700-acceptance-key",
            origin="https://shop1700.haeorumgift.com",
        )
        service = AISearchService(self.engine, settings, SearchLogger(settings.search_log_path))
        result = service.search(SearchRequest(mall_id="shop1700", q="우산", limit=5))
        assert result.top
        assert result.top[0].product_url == "https://shop1700.haeorumgift.com/product_view.asp?p_idx=P001"

    def check_representative_site_checker_contract(self) -> None:
        calls: list[dict[str, object]] = []
        site = {
            "name": "shop001-main",
            "mall_id": "shop001",
            "url": "https://shop001.example.test/",
            "origin": "https://shop001.example.test",
            "api_base_url": "https://ai.example.test",
            "api_key": "public-shop001-production-key",
            "expected_product_url_prefix": "https://shop001.example.test/product",
            "required_markers": ["haeorum-ai-search"],
            "expect_api_base_url_marker": True,
        }
        args = type(
            "Args",
            (),
            {
                "api_base_url": "https://ai.example.test",
                "api_key": "",
                "limit": 20,
                "timeout": 5,
                "skip_page": False,
                "skip_api": False,
            },
        )()

        def fake_fetch_text(url: str, user_agent: str, timeout: int) -> dict[str, object]:
            calls.append({"kind": "fetch", "url": url, "user_agent": user_agent, "timeout": timeout})
            if str(url).split("?", 1)[0].rstrip("/").endswith("/widget.js"):
                return {
                    "ok": True,
                    "status": 200,
                    "elapsed_ms": 1.0,
                    "body": "window.HaeorumAISearch = { init: function () {} };",
                    "content_type": "application/javascript",
                }
            if "/product/P001" in url:
                return {"ok": True, "status": 200, "elapsed_ms": 1.0, "body": "detail"}
            return {
                "ok": True,
                "status": 200,
                "elapsed_ms": 1.0,
                "body": '<div id="haeorum-ai-search"></div><script src="/widget.js"></script>'
                '<script>HaeorumAISearch.init({mallId:"shop001",apiBaseUrl:"https://ai.example.test"})</script>',
                "content_type": "text/html",
            }

        def fake_request_json(
            method: str,
            url: str,
            payload: dict[str, object] | None,
            headers: dict[str, str],
            timeout: int,
        ) -> dict[str, object]:
            calls.append(
                {
                    "kind": "json",
                    "method": method,
                    "url": url,
                    "payload": payload,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            if url.endswith("/api/click-log"):
                return {"ok": True, "status": 200, "elapsed_ms": 1.0, "data": {"ok": True}}

            request_payload = payload or {}
            query_type = (
                "text_image"
                if "q" in request_payload and "image_base64" in request_payload
                else "image"
                if "image_base64" in request_payload
                else "text"
            )
            category = str(request_payload.get("category") or "우산")
            top = [
                {
                    "product_id": "P001",
                    "name": "검정 우산",
                    "category": category,
                    "price": 8500,
                    "image_url": "https://images.example.test/p001.jpg",
                    "product_url": "https://shop001.example.test/product/P001",
                    "score": 0.912,
                    "score_percent": 91.2,
                    "mall_id": "shop001",
                    "source_scores": {"text": 0.91, "image": 0.9},
                }
            ]
            items = [
                {
                    "product_id": "P002",
                    "name": "3단 우산",
                    "category": category,
                    "price": 12000,
                    "image_url": "https://images.example.test/p002.jpg",
                    "product_url": "https://shop001.example.test/product/P002",
                    "score": 0.824,
                    "score_percent": 82.4,
                    "mall_id": "shop001",
                    "source_scores": {"text": 0.82, "image": 0.81},
                }
            ]
            return {
                "ok": True,
                "status": 200,
                "elapsed_ms": 1.0,
                "data": {
                    "top": top,
                    "items": items,
                    "suggested_categories": ["우산"],
                    "meta": {
                        "query_type": query_type,
                        "elapsed_ms": 120.0,
                        "engine": "marqo",
                        "limit": 20,
                        "offset": 0,
                        "has_more": False,
                        "next_offset": None,
                        "mall_id": "shop001",
                        "text_weight": None if query_type == "image" else 1.0 if query_type == "text" else 0.4,
                        "image_weight": None if query_type == "text" else 1.0 if query_type == "image" else 0.6,
                        "low_confidence": False,
                        "notice": None,
                    },
                },
            }

        with patch("scripts.representative_site_check.fetch_text", fake_fetch_text), patch(
            "scripts.representative_site_check.request_json",
            fake_request_json,
        ):
            evidence = evaluate_site(site, args, self.image.data_url)

        assert evidence["ok"] is True, evidence
        assert "HaeorumAISearch" in site_markers(site)
        assert "widget.js" in site_markers(site)
        check_names = [check["name"] for check in evidence["checks"]]
        assert check_names == [
            "site_config",
            "desktop_page",
            "mobile_page",
            "saved_widget_probe_sources",
            "widget_init",
            "widget_script_asset",
            "text_search",
            "image_search",
            "mixed_search",
            "result_image_csp",
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
        ]
        json_calls = [call for call in calls if call["kind"] == "json"]
        assert json_calls[0]["headers"]["X-API-Key"] == "public-shop001-production-key"
        assert json_calls[0]["headers"]["Origin"] == "https://shop001.example.test"
        text_search = next(check for check in evidence["checks"] if check["name"] == "text_search")
        assert text_search["engine"] == "marqo"
        assert text_search["engine_ok"] is True
        assert text_search["missing_meta_fields"] == []
        assert text_search["missing_top_fields"] == []
        assert text_search["missing_item_fields"] == []
        category_refetch = next(check for check in evidence["checks"] if check["name"] == "text_category_refetch")
        assert category_refetch["ok"] is True
        assert category_refetch["category"] == "우산"
        assert category_refetch["mismatched_categories"] == []
        all_product_url_rule = next(
            check for check in evidence["checks"] if check["name"] == "text_all_product_url_rules"
        )
        assert all_product_url_rule["ok"] is True
        assert all_product_url_rule["checked"] == 2
        assert all_product_url_rule["failed"] == 0
        widget_init = next(check for check in evidence["checks"] if check["name"] == "widget_init")
        assert widget_init["init_call_found"] is True
        assert widget_init["widget_script_found"] is True
        assert widget_init["mall_or_site_id_found"] is True
        assert widget_init["api_base_url_marker_found"] is True
        saved_probe = next(check for check in evidence["checks"] if check["name"] == "saved_widget_probe_sources")
        assert saved_probe["ok"] is True
        assert saved_probe["skipped"] is True
        widget_script_asset = next(check for check in evidence["checks"] if check["name"] == "widget_script_asset")
        assert widget_script_asset["ok"] is True
        assert widget_script_asset["content_type_ok"] is True
        assert widget_script_asset["body_looks_html"] is False
        assert widget_script_asset["widget_global_found"] is True
        result_image_csp = next(check for check in evidence["checks"] if check["name"] == "result_image_csp")
        assert result_image_csp["ok"] is True
        assert result_image_csp["blocked_image_urls"] == []
        click_checks = [check for check in evidence["checks"] if str(check["name"]).endswith("_click_log")]
        assert [check["query_type"] for check in click_checks] == ["text", "image", "text_image"]

        def fake_request_json_local_engine(
            method: str,
            url: str,
            payload: dict[str, object] | None,
            headers: dict[str, str],
            timeout: int,
        ) -> dict[str, object]:
            response = fake_request_json(method, url, payload, headers, timeout)
            data = response.get("data")
            if isinstance(data, dict) and isinstance(data.get("meta"), dict):
                data["meta"]["engine"] = "local"
            return response

        with patch("scripts.representative_site_check.fetch_text", fake_fetch_text), patch(
            "scripts.representative_site_check.request_json",
            fake_request_json_local_engine,
        ):
            local_engine_evidence = evaluate_site(site, args, self.image.data_url)
        assert local_engine_evidence["ok"] is False
        local_engine_text = next(check for check in local_engine_evidence["checks"] if check["name"] == "text_search")
        assert local_engine_text["engine"] == "local"
        assert local_engine_text["engine_ok"] is False

        placeholder_site = {**site, "api_key": "replace-with-shop001-public-key"}
        calls.clear()
        placeholder_evidence = evaluate_site(placeholder_site, args, self.image.data_url)
        assert placeholder_evidence["ok"] is False
        assert [check["name"] for check in placeholder_evidence["checks"]] == ["site_config"]
        assert [problem["field"] for problem in placeholder_evidence["checks"][0]["problems"]] == ["api_key"]
        assert calls == []

        def fake_request_json_evil_product_host(
            method: str,
            url: str,
            payload: dict[str, object] | None,
            headers: dict[str, str],
            timeout: int,
        ) -> dict[str, object]:
            response = fake_request_json(method, url, payload, headers, timeout)
            data = response.get("data")
            if isinstance(data, dict):
                for section in ["top", "items"]:
                    for product in data.get(section) or []:
                        if isinstance(product, dict):
                            product["product_url"] = "https://shop001.example.test.evil/product/P001"
            return response

        calls.clear()
        with patch("scripts.representative_site_check.fetch_text", fake_fetch_text), patch(
            "scripts.representative_site_check.request_json",
            fake_request_json_evil_product_host,
        ):
            evil_host_evidence = evaluate_site(site, args, self.image.data_url)
        assert evil_host_evidence["ok"] is False
        evil_product_url_rule = next(check for check in evil_host_evidence["checks"] if check["name"] == "text_product_url_rule")
        assert evil_product_url_rule["ok"] is False
        assert evil_product_url_rule["matched_prefixes"] == []
        evil_all_product_url_rule = next(
            check for check in evil_host_evidence["checks"] if check["name"] == "text_all_product_url_rules"
        )
        assert evil_all_product_url_rule["ok"] is False
        assert evil_all_product_url_rule["failed"] == 2
        evil_detail_url = next(check for check in evil_host_evidence["checks"] if check["name"] == "text_detail_url")
        assert evil_detail_url["ok"] is False
        assert not any(call["kind"] == "fetch" and "shop001.example.test.evil" in str(call["url"]) for call in calls)

    def check_mssql_readonly_view_and_incremental_contract(self) -> None:
        readonly_query = (
            "WITH changed AS ("
            "SELECT product_id, product_name, price, category_name, main_image_url, product_url, "
            "status, updated_at, is_deleted, display_yn, mall_id FROM dbo.v_ai_search_products"
            ") SELECT product_id, product_name, price, category_name, main_image_url, product_url, "
            "status, updated_at, is_deleted, display_yn, mall_id FROM changed"
        )
        validate_readonly_query(readonly_query)
        wrapped = build_wrapped_mssql_query(readonly_query, filters=["updated_at >= ?"], top=25, order_by="product_id")
        assert wrapped.startswith("WITH changed AS (SELECT product_id")
        assert "SELECT TOP (25) * FROM (SELECT product_id" in wrapped
        assert "FROM (WITH" not in wrapped
        assert "WHERE updated_at >= ?" in wrapped
        assert wrapped.endswith("ORDER BY product_id")

        for unsafe_query in [
            "UPDATE products SET status='active'",
            "SELECT * FROM dbo.v_ai_search_products; DELETE FROM products",
            "SELECT * INTO dbo.ai_search_copy FROM dbo.v_ai_search_products",
            "SELECT * FROM dbo.v_ai_search_products -- hidden write",
            "EXEC dbo.refresh_products",
        ]:
            try:
                validate_readonly_query(unsafe_query)
            except ValueError:
                pass
            else:
                raise AssertionError(f"unsafe MSSQL query should be rejected: {unsafe_query}")

        class CapturingMssqlSource(MssqlProductSource):
            def __init__(self) -> None:
                super().__init__(
                    "Driver={ODBC Driver 18 for SQL Server};Server=example;",
                    readonly_query,
                    product_id_column="product_id",
                    updated_at_column="updated_at",
                )
                self.captured: list[tuple[str, list[object] | None]] = []

            def _fetch(self, query, params=None):  # type: ignore[no-untyped-def]
                self.captured.append((query, params))
                return []

        source = CapturingMssqlSource()
        source.fetch_updated("2026-05-01T09:00:00+09:00")
        source.fetch_one("P001")
        assert "WHERE updated_at >= ?" in source.captured[0][0]
        assert source.captured[0][1] and source.captured[0][1][0].isoformat() == "2026-05-01T00:00:00"
        assert "WHERE product_id = ?" in source.captured[1][0]
        assert source.captured[1][1] == ["P001"]

        column_report = validate_columns(
            [
                "product_id",
                "product_name",
                "price",
                "price_min",
                "price_max",
                "category_name",
                "print_methods",
                "materials",
                "colors",
                "min_order_qty",
                "delivery_days",
                "main_image_url",
                "product_url",
                "status",
                "updated_at",
                "is_deleted",
                "display_yn",
                "mall_id",
            ]
        )
        assert column_report["ok"] is True
        assert not column_report["missing_required_columns"]
        weak_column_report = validate_columns(["product_id", "product_name", "price"])
        assert "updated_at" in weak_column_report["missing_required_columns"]
        assert "is_deleted_or_display_yn" in weak_column_report["missing_required_columns"]

        sample_report = analyze_sample(
            [
                {
                    "product_id": "P001",
                    "product_name": "검정 우산",
                    "price": 8500,
                    "price_min": 8000,
                    "price_max": 9000,
                    "category_name": "우산",
                    "print_methods": "실크,UV",
                    "materials": "폴리",
                    "colors": "검정",
                    "min_order_qty": 100,
                    "delivery_days": 3,
                    "main_image_url": "https://images.example.test/p001.jpg",
                    "product_url": "https://shop001.haeorumgift.com/product_view.asp?p_idx=P001",
                    "status": "active",
                    "updated_at": "2026-05-19T09:00:00Z",
                    "is_deleted": False,
                    "display_yn": "Y",
                    "mall_id": "shop001",
                },
                {
                    "product_id": "P002",
                    "product_name": "비노출 우산",
                    "price": 1000,
                    "price_min": 900,
                    "price_max": 1100,
                    "category_name": "우산",
                    "print_methods": "실크",
                    "materials": "폴리",
                    "colors": "검정",
                    "min_order_qty": 100,
                    "delivery_days": 3,
                    "main_image_url": "https://images.example.test/p002.jpg",
                    "status": "inactive",
                    "updated_at": "2026-05-19T10:00:00Z",
                    "display_yn": "N",
                    "mall_id": "shop001",
                },
            ]
        )
        assert validate_sample_report(sample_report)["ok"] is True
        assert sample_report["active_rows"] == 1
        assert sample_report["inactive_rows"] == 1
        bad_sample = analyze_sample([{"product_id": "P003", "product_name": "잘못된 상품", "updated_at": "not-a-date"}])
        bad_quality = validate_sample_report(bad_sample)
        assert bad_quality["ok"] is False
        assert any("invalid updated_at" in problem for problem in bad_quality["problems"])
        try:
            ProductDocument(product_id="P" * (MAX_PRODUCT_ID_LENGTH + 1), name="너무 긴 상품번호")
        except ValueError:
            pass
        else:
            raise AssertionError("oversized product_id should be rejected before indexing")

        permission_report = analyze_readonly_permissions(
            {"db_datareader": 1, "db_datawriter": 0, "db_owner": 0},
            [{"permission_name": "SELECT", "state_desc": "GRANT"}],
        )
        assert permission_report["ok"] is True
        assert permission_report["select_permission_reported"] is True
        dangerous_permissions = analyze_readonly_permissions(
            {"db_datareader": 1, "db_datawriter": 1},
            [{"permission_name": "UPDATE", "state_desc": "GRANT"}],
        )
        assert dangerous_permissions["ok"] is False
        assert "db_datawriter" in dangerous_permissions["dangerous_roles"]
        assert "UPDATE" in dangerous_permissions["dangerous_permissions"]

    def check_sync_status_and_logs(self) -> None:
        sync = SyncService(self.engine, CsvProductSource(self.settings.product_csv_path), self.settings)
        result = sync.sync_changed()
        assert result.failed == 0
        assert result.indexed >= 1
        assert self.settings.sync_log_path.exists()

        duplicate_csv = Path(self.temp_dir.name) / "duplicate-products.csv"
        duplicate_log = Path(self.temp_dir.name) / "duplicate-sync.jsonl"
        duplicate_csv.write_text(
            "\n".join(
                [
                    "product_id,product_name,category_name,status,display_yn,price,mall_id",
                    "P-DUP,중복 우산 A,우산,active,Y,8500,shop001",
                    "P-DUP,중복 우산 B,우산,active,Y,8500,shop001",
                    "P-OK,정상 우산,우산,active,Y,9000,shop001",
                ]
            ),
            encoding="utf-8",
        )
        duplicate_settings = Settings(
            engine_backend="local",
            index_name="duplicate-source-test",
            product_csv_path=duplicate_csv,
            sync_log_path=duplicate_log,
        )
        duplicate_engine = LocalSearchEngine([])
        duplicate_sync = SyncService(duplicate_engine, CsvProductSource(duplicate_csv), duplicate_settings)
        duplicate_result = duplicate_sync.reindex_all()
        duplicate_log_text = duplicate_log.read_text(encoding="utf-8")

        assert duplicate_result.indexed == 1
        assert duplicate_result.failed == 2
        assert [hit.document.product_id for hit in duplicate_engine.search(EngineQuery(q="우산", limit=10))] == ["P-OK"]
        assert "duplicate_product_id" in duplicate_log_text
        assert "validate_source" in duplicate_log_text

        duplicate_single_engine = LocalSearchEngine(
            [ProductDocument(product_id="P-DUP", name="기존 우산", category="우산", status="active")]
        )
        duplicate_single_sync = SyncService(
            duplicate_single_engine,
            CsvProductSource(duplicate_csv),
            duplicate_settings,
        )
        duplicate_single_result = duplicate_single_sync.reindex_product("P-DUP")
        duplicate_log_text = duplicate_log.read_text(encoding="utf-8")
        assert duplicate_single_result.indexed == 0
        assert duplicate_single_result.deleted == 0
        assert duplicate_single_result.failed == 1
        assert [hit.document.product_id for hit in duplicate_single_engine.search(EngineQuery(q="우산", limit=10))] == [
            "P-DUP"
        ]
        assert "fetch_product" in duplicate_log_text
        assert "multiple source products found for product_id P-DUP: 2" in duplicate_log_text

    def check_sync_worker_schedule_and_alerting_contract(self) -> None:
        csv_path = Path(self.temp_dir.name) / "worker-products.csv"
        sync_log_path = Path(self.temp_dir.name) / "worker-sync.jsonl"
        csv_path.write_text(
            "\n".join(
                [
                    "product_id,product_name,category_name,status,display_yn,price,updated_at",
                    "P-WORKER-1,정기 동기화 우산,우산,active,Y,8500,2026-05-19T09:00:00Z",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        settings = Settings(
            engine_backend="local",
            index_name="worker-test",
            product_csv_path=csv_path,
            sync_log_path=sync_log_path,
            sync_interval_seconds=3600,
            sync_lock_stale_seconds=21600,
        )
        service = SyncService(LocalSearchEngine([]), CsvProductSource(csv_path), settings)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = run_once(service, "reindex")
        assert exit_code == 0
        reindex_output = json.loads(output.getvalue())
        assert reindex_output["mode"] == "reindex"
        assert reindex_output["indexed"] == 1
        assert resolve_sync_since(service) == reindex_output["status"]["last_started_at"]
        assert resolve_sync_since(service, "2026-05-01T00:00:00Z") == "2026-05-01T00:00:00Z"
        assert resolve_sync_since(service, auto_since=False) is None

        output = io.StringIO()
        with redirect_stdout(output):
            sync_exit_code = run_once(service, "sync")
        assert sync_exit_code == 0
        sync_output = json.loads(output.getvalue())
        assert sync_output["mode"] == "sync"
        assert sync_output["failed"] == 0

        class FailingEngine(LocalSearchEngine):
            def upsert_products(self, products):  # type: ignore[no-untyped-def]
                raise RuntimeError("marqo unavailable")

        class CapturingNotifier:
            def __init__(self) -> None:
                self.results = []

            def notify(self, result):  # type: ignore[no-untyped-def]
                self.results.append(result)
                return True

        notifier = CapturingNotifier()
        failing_settings = Settings(
            engine_backend="local",
            index_name="worker-test",
            product_csv_path=csv_path,
            sync_log_path=Path(self.temp_dir.name) / "worker-failure-sync.jsonl",
        )
        failing_service = SyncService(
            FailingEngine(),
            CsvProductSource(csv_path),
            failing_settings,
            notifier=notifier,
        )
        failed_result = failing_service.reindex_all()
        assert failed_result.failed == 1
        assert "marqo unavailable" in (failed_result.status.last_error or "")
        assert len(notifier.results) == 1
        assert notifier.results[0].mode == "reindex"

        with failing_service.acquire_sync_lock("sync"):
            busy_result = failing_service.sync_changed()
        assert busy_result.failed == 1
        assert "lock file exists" in (busy_result.status.last_error or "")
        assert len(notifier.results) == 2

        failure_log = failing_settings.sync_log_path.read_text(encoding="utf-8")
        assert "sync_batch_failed" in failure_log
        assert "acquire_sync_lock" in failure_log

        sync_service_template = (ROOT / "deploy" / "systemd" / "haeorum-ai-sync.service").read_text(encoding="utf-8")
        reindex_service_template = (ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.service").read_text(encoding="utf-8")
        reindex_timer_template = (ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.timer").read_text(encoding="utf-8")
        assert "ExecStart=/opt/haeorum-ai-search/.venv/bin/python -m app.sync_worker --mode sync" in sync_service_template
        assert "--once" not in sync_service_template
        assert "Restart=always" in sync_service_template
        assert "EnvironmentFile=/etc/haeorum-ai-search/haeorum-ai-search.env" in sync_service_template
        assert "ExecStart=/opt/haeorum-ai-search/.venv/bin/python -m app.sync_worker --mode reindex --once" in reindex_service_template
        assert "Type=oneshot" in reindex_service_template
        assert "OnCalendar=*-*-* 03:00:00" in reindex_timer_template
        assert "Persistent=true" in reindex_timer_template
        assert "Unit=haeorum-ai-reindex.service" in reindex_timer_template

    def check_search_insights_quality_loop(self) -> None:
        from scripts.search_insights import build_report as build_search_insights_report

        image_result = self.service.search(SearchRequest(mall_id="shop001", image_base64=self.image_base64, limit=20))
        mixed_result = self.service.search(
            SearchRequest(mall_id="shop001", q="검은색", image_base64=self.image_base64, limit=20)
        )
        product = (mixed_result.top or image_result.top)[0]
        self.service.log_click(
            {
                "mall_id": "shop001",
                "product_id": product.product_id,
                "position": 1,
                "query": "검은색",
                "query_type": "text_image",
                "score_percent": product.score_percent,
                "product_url": product.product_url,
            }
        )

        report = build_search_insights_report(self.settings.search_log_path, limit=10, min_searches=1)

        assert report["ok"] is True, report
        assert report["search_events"] >= 2, report
        assert report["click_events"] >= 1, report
        assert report["image_search_events"] >= 2, report
        assert report["mixed_search_events"] >= 1, report
        assert report["mixed_weight_performance"], report
        assert report["average_image_size_bytes"] is not None, report
        assert isinstance(report["image_quality_warning_counts"], dict), report
        for warning in self.image.quality_warnings:
            assert report["image_quality_warning_counts"].get(warning, 0) >= 1, report

    def check_load_and_scale_evidence_contracts(self) -> None:
        image_path = Path(self.temp_dir.name) / "load-reference.png"
        image_path_2 = Path(self.temp_dir.name) / "load-reference-2.png"
        image_path_3 = Path(self.temp_dir.name) / "load-reference-3.png"
        image_path.write_bytes(make_image_bytes("PNG", width=48, height=40))
        image_path_2.write_bytes(make_image_bytes("PNG", width=49, height=40))
        image_path_3.write_bytes(make_image_bytes("PNG", width=50, height=40))
        args = type(
            "Args",
            (),
            {
                "base_url": "https://ai-search.example.test",
                "origin": "https://shop001.example.test",
                "api_key": "public-shop001-production-key",
                "admin_key": "admin-production-key-123456",
                "api_server_count": 2,
                "scenario": "mixed-traffic",
                "active_users": 850,
                "mode": "mixed",
                "traffic_mix": "text=70,image=10,mixed=20",
                "requests": 1,
                "concurrency": 100,
                "mall_id": "shop001",
                "limit": 20,
                "p95_ms": 5000,
                "max_error_rate": 1.0,
                "image_file": str(image_path),
                "additional_image_file": [str(image_path_2), str(image_path_3)],
                "image_files": "",
                "additional_image_files": [],
                "image_max_mb": 1,
            },
        )()
        validate_load_test_args(args)
        assert planned_request_count(args) == 850
        image_base64_values, image_input = load_image_data_urls_for_args(args)
        payloads, mode_counts, traffic_mix_percent = build_payloads(args, image_base64_values)
        identities = [
            LoadRequestIdentity(
                mall_id=f"shop{index:03d}",
                api_key=f"public-shop{index:03d}-production-key",
                origin=f"https://shop{index:03d}.example.test",
                expected_product_url_prefix=f"https://shop{index:03d}.example.test/product",
            )
            for index in range(1, 51)
        ]
        request_specs = build_request_specs(payloads, identities)
        mall_identity = load_identity_summary(
            identities,
            len(identities),
            sampling_strategy="spread",
            source_enabled_count=len(identities),
            eligible_mall_count=len(identities),
        )
        request_profile = summarize_request_profile(request_specs)
        expected_mall_counts = {
            identity.mall_id: 850 // len(identities)
            for identity in identities
        }

        assert len(payloads) == 850
        assert len(request_specs) == 850
        assert mode_counts == {"text": 610, "image": 80, "mixed": 160}
        assert traffic_mix_percent == {"text": 70.0, "image": 10.0, "mixed": 20.0}
        assert image_input["source"] == "files"
        assert image_input["file"] == str(image_path)
        assert image_input["file_count"] == 3
        assert image_input["unique_sha256_count"] == 3
        assert request_profile["unique_image_inputs"] == 3

        results = [
            {
                "ok": True,
                "status": 200,
                "elapsed_ms": 120.0,
                "expected_query_type": expected_query_type(spec.payload),
                "query_type": expected_query_type(spec.payload),
                "engine": "marqo",
                "expected_mall_id": spec.payload["mall_id"],
                "expected_product_url_prefix": spec.expected_product_url_prefix,
                "meta_mall_id": spec.payload["mall_id"],
                "result_mall_ids": [spec.payload["mall_id"], spec.payload["mall_id"]],
                "mall_id_matches_request": True,
                "product_url_prefix_mismatch_count": 0,
                "top_count": 3,
                "item_count": 17,
                "category_count": 4,
                "response_valid": True,
                "api_instance_id": f"hai-acceptance-api-{(index % args.api_server_count) + 1}",
            }
            for index, spec in enumerate(request_specs)
        ]
        response_contract = summarize_response_contract(results)
        assert response_contract["ok"] is True
        assert response_contract["total_requests"] == 850
        assert response_contract["valid_successful_responses"] == 850
        assert response_contract["query_type_counts"] == {"image": 80, "text": 610, "text_image": 160}
        assert response_contract["engine_counts"] == {"marqo": 850}
        assert response_contract["non_marqo_engine_responses"] == 0
        assert response_contract["expected_mall_id_counts"] == expected_mall_counts
        assert response_contract["meta_mall_id_counts"] == expected_mall_counts
        assert response_contract["result_mall_id_counts"] == {
            mall_id: count * 2 for mall_id, count in expected_mall_counts.items()
        }
        assert response_contract["product_url_prefix_required"] is True
        assert response_contract["mall_id_mismatch_count"] == 0

        before = {
            "ok": True,
            "snapshot": {
                "generated_at": "2026-05-21T00:00:00Z",
                "engine_backend": "marqo",
                "engine_index": "haeorum-products",
                "marqo_model": "Marqo/marqo-ecommerce-embeddings-L",
                "embedding_backend": "qwen",
                "qwen_model": "Qwen/Qwen3-VL-Embedding-2B",
                "qwen_embedding_dimensions": 2048,
                "qwen_query_vector_runtime_entries": 5,
                "qwen_query_vector_runtime_text_entries": 4,
                "qwen_query_vector_runtime_image_entries": 1,
                "qwen_query_vector_runtime_max_entries": 2560,
                "qwen_query_vector_runtime_text_max_entries": 2048,
                "qwen_query_vector_runtime_image_max_entries": 512,
                "qwen_query_vector_in_flight": 0,
                "qwen_query_vector_wait_timeout_seconds": 31.1,
                "qwen_query_vector_wait_events": 0,
                "qwen_query_vector_wait_timeouts": 0,
                "qwen_query_vector_total_wait_ms": 0.0,
                "qwen_query_vector_avg_wait_ms": 0.0,
                "qwen_query_vector_max_wait_ms": 0.0,
                "engine_search_attempts": 1000,
                "engine_adaptive_refetches": 0,
                "engine_adaptive_refetch_searches": 0,
                "engine_underfilled_after_max_candidates_events": 0,
                "engine_average_search_attempts": 1.0,
                "engine_max_search_attempts": 1,
                "engine_average_final_candidate_limit": 24.0,
                "engine_max_final_candidate_limit": 24,
                "backend_marqo_request_attempts": 1000,
                "backend_marqo_connections_opened": 100,
                "backend_marqo_connection_reuses": 900,
                "backend_marqo_idle_reconnects": 0,
                "backend_marqo_stale_reconnects": 0,
                "backend_marqo_error_responses": 0,
                "backend_marqo_connection_close_responses": 0,
                "backend_marqo_gzip_responses": 1000,
                "backend_marqo_total_elapsed_ms": 95000.0,
                "backend_marqo_avg_elapsed_ms": 95.0,
                "backend_marqo_max_elapsed_ms": 400.0,
                "backend_marqo_last_elapsed_ms": 90.0,
                "backend_marqo_total_request_body_bytes": 1536000,
                "backend_marqo_max_request_body_bytes": 4096,
                "backend_marqo_last_request_body_bytes": 1536,
                "backend_marqo_total_response_body_bytes": 8192000,
                "backend_marqo_max_response_body_bytes": 32768,
                "backend_marqo_last_response_body_bytes": 8192,
                "backend_marqo_total_decoded_response_body_bytes": 16384000,
                "backend_marqo_max_decoded_response_body_bytes": 65536,
                "backend_marqo_last_decoded_response_body_bytes": 16384,
                "backend_qwen_request_attempts": 1000,
                "backend_qwen_connections_opened": 100,
                "backend_qwen_connection_reuses": 900,
                "backend_qwen_idle_reconnects": 0,
                "backend_qwen_stale_reconnects": 0,
                "backend_qwen_error_responses": 0,
                "backend_qwen_connection_close_responses": 0,
                "backend_qwen_gzip_responses": 1000,
                "backend_qwen_total_elapsed_ms": 45000.0,
                "backend_qwen_avg_elapsed_ms": 45.0,
                "backend_qwen_max_elapsed_ms": 180.0,
                "backend_qwen_last_elapsed_ms": 42.0,
                "backend_qwen_total_request_body_bytes": 2048000,
                "backend_qwen_max_request_body_bytes": 8192,
                "backend_qwen_last_request_body_bytes": 2048,
                "backend_qwen_total_response_body_bytes": 8192000,
                "backend_qwen_max_response_body_bytes": 32768,
                "backend_qwen_last_response_body_bytes": 8192,
                "backend_qwen_total_decoded_response_body_bytes": 32768000,
                "backend_qwen_max_decoded_response_body_bytes": 65536,
                "backend_qwen_last_decoded_response_body_bytes": 32768,
                "search_events": 1000,
                "image_search_events": 100,
                "result_mall_id_mismatch_events": 0,
                "result_mall_id_mismatch_count": 0,
                "rate_limit_backend": "redis",
                "rate_limit_redis_enabled": True,
                "rate_limited_events": 0,
                "rate_limit_fallback_events": 0,
                "cache_backend": "redis",
                "cache_redis_enabled": True,
                "cache_ttl_seconds": 30,
                "cache_error_count": 0,
                "cache_clear_errors": 0,
                "cache_lock_claims": 10,
                "cache_lock_contention_events": 4,
                "cache_lock_errors": 0,
                "cache_lock_release_errors": 0,
                "cache_lock_wait_events": 4,
                "cache_lock_wait_timeouts": 0,
                "cache_lock_total_wait_ms": 4.0,
                "cache_lock_avg_wait_ms": 1.0,
                "cache_lock_max_wait_ms": 3.0,
                "singleflight_enabled": True,
                "singleflight_in_flight": 0,
                "singleflight_wait_timeout_seconds": 5.0,
                "singleflight_wait_events": 20,
                "singleflight_wait_timeouts": 0,
                "singleflight_total_wait_ms": 20.0,
                "singleflight_avg_wait_ms": 1.0,
                "singleflight_max_wait_ms": 5.0,
                "search_queue_full_events": 0,
                "search_queue_wait_events": 0,
                "search_queue_total_wait_ms": 0.0,
                "search_queue_avg_wait_ms": 0.0,
                "search_queue_max_wait_ms": 0.0,
                "search_queue_last_wait_ms": 0.0,
                "search_queue_enabled": True,
                "search_queue_max_concurrency": 64,
                "search_queue_timeout_seconds": 2.0,
                "image_queue_full_events": 0,
                "image_queue_wait_events": 0,
                "image_queue_total_wait_ms": 0.0,
                "image_queue_avg_wait_ms": 0.0,
                "image_queue_max_wait_ms": 0.0,
                "image_queue_last_wait_ms": 0.0,
                "image_queue_enabled": True,
                "image_queue_max_concurrency": 8,
                "image_queue_timeout_seconds": 5.0,
                "api_threadpool_ok": True,
                "api_threadpool_configured_tokens": 96,
                "api_threadpool_runtime_tokens": 96,
                "api_threadpool_required_tokens": 80,
                "search_log_write_errors": 0,
                "error_log_write_errors": 0,
            },
        }
        after = {
            "ok": True,
            "snapshot": {
                "generated_at": "2026-05-21T00:10:00Z",
                "engine_backend": "marqo",
                "engine_index": "haeorum-products",
                "marqo_model": "Marqo/marqo-ecommerce-embeddings-L",
                "embedding_backend": "qwen",
                "qwen_model": "Qwen/Qwen3-VL-Embedding-2B",
                "qwen_embedding_dimensions": 2048,
                "qwen_query_vector_runtime_entries": 42,
                "qwen_query_vector_runtime_text_entries": 34,
                "qwen_query_vector_runtime_image_entries": 8,
                "qwen_query_vector_runtime_max_entries": 2560,
                "qwen_query_vector_runtime_text_max_entries": 2048,
                "qwen_query_vector_runtime_image_max_entries": 512,
                "qwen_query_vector_in_flight": 0,
                "qwen_query_vector_wait_timeout_seconds": 31.1,
                "qwen_query_vector_wait_events": 70,
                "qwen_query_vector_wait_timeouts": 0,
                "qwen_query_vector_total_wait_ms": 34.0,
                "qwen_query_vector_avg_wait_ms": 0.486,
                "qwen_query_vector_max_wait_ms": 7.0,
                "engine_search_attempts": 1850,
                "engine_adaptive_refetches": 0,
                "engine_adaptive_refetch_searches": 0,
                "engine_underfilled_after_max_candidates_events": 0,
                "engine_average_search_attempts": 1.0,
                "engine_max_search_attempts": 1,
                "engine_average_final_candidate_limit": 24.0,
                "engine_max_final_candidate_limit": 24,
                "backend_marqo_request_attempts": 1850,
                "backend_marqo_connections_opened": 200,
                "backend_marqo_connection_reuses": 1650,
                "backend_marqo_idle_reconnects": 0,
                "backend_marqo_stale_reconnects": 0,
                "backend_marqo_error_responses": 0,
                "backend_marqo_connection_close_responses": 0,
                "backend_marqo_gzip_responses": 1850,
                "backend_marqo_total_elapsed_ms": 175750.0,
                "backend_marqo_avg_elapsed_ms": 95.0,
                "backend_marqo_max_elapsed_ms": 420.0,
                "backend_marqo_last_elapsed_ms": 88.0,
                "backend_marqo_total_request_body_bytes": 2841600,
                "backend_marqo_max_request_body_bytes": 4096,
                "backend_marqo_last_request_body_bytes": 1536,
                "backend_marqo_total_response_body_bytes": 15155200,
                "backend_marqo_max_response_body_bytes": 32768,
                "backend_marqo_last_response_body_bytes": 8192,
                "backend_marqo_total_decoded_response_body_bytes": 30310400,
                "backend_marqo_max_decoded_response_body_bytes": 65536,
                "backend_marqo_last_decoded_response_body_bytes": 16384,
                "backend_qwen_request_attempts": 1850,
                "backend_qwen_connections_opened": 200,
                "backend_qwen_connection_reuses": 1650,
                "backend_qwen_idle_reconnects": 0,
                "backend_qwen_stale_reconnects": 0,
                "backend_qwen_error_responses": 0,
                "backend_qwen_connection_close_responses": 0,
                "backend_qwen_gzip_responses": 1850,
                "backend_qwen_total_elapsed_ms": 83250.0,
                "backend_qwen_avg_elapsed_ms": 45.0,
                "backend_qwen_max_elapsed_ms": 180.0,
                "backend_qwen_last_elapsed_ms": 42.0,
                "backend_qwen_total_request_body_bytes": 3788800,
                "backend_qwen_max_request_body_bytes": 8192,
                "backend_qwen_last_request_body_bytes": 2048,
                "backend_qwen_total_response_body_bytes": 15155200,
                "backend_qwen_max_response_body_bytes": 32768,
                "backend_qwen_last_response_body_bytes": 8192,
                "backend_qwen_total_decoded_response_body_bytes": 60620800,
                "backend_qwen_max_decoded_response_body_bytes": 65536,
                "backend_qwen_last_decoded_response_body_bytes": 32768,
                "search_events": 1850,
                "image_search_events": 340,
                "result_mall_id_mismatch_events": 0,
                "result_mall_id_mismatch_count": 0,
                "rate_limit_backend": "redis",
                "rate_limit_redis_enabled": True,
                "rate_limited_events": 0,
                "rate_limit_fallback_events": 0,
                "cache_backend": "redis",
                "cache_redis_enabled": True,
                "cache_ttl_seconds": 30,
                "cache_error_count": 0,
                "cache_clear_errors": 0,
                "cache_lock_claims": 44,
                "cache_lock_contention_events": 21,
                "cache_lock_errors": 0,
                "cache_lock_release_errors": 0,
                "cache_lock_wait_events": 21,
                "cache_lock_wait_timeouts": 0,
                "cache_lock_total_wait_ms": 25.0,
                "cache_lock_avg_wait_ms": 1.19,
                "cache_lock_max_wait_ms": 8.0,
                "singleflight_enabled": True,
                "singleflight_in_flight": 0,
                "singleflight_wait_timeout_seconds": 5.0,
                "singleflight_wait_events": 62,
                "singleflight_wait_timeouts": 0,
                "singleflight_total_wait_ms": 62.5,
                "singleflight_avg_wait_ms": 1.008,
                "singleflight_max_wait_ms": 6.0,
                "search_queue_full_events": 0,
                "search_queue_wait_events": 850,
                "search_queue_total_wait_ms": 340.0,
                "search_queue_avg_wait_ms": 0.4,
                "search_queue_max_wait_ms": 8.5,
                "search_queue_last_wait_ms": 0.1,
                "search_queue_enabled": True,
                "search_queue_max_concurrency": 64,
                "search_queue_timeout_seconds": 2.0,
                "image_queue_full_events": 0,
                "image_queue_wait_events": 240,
                "image_queue_total_wait_ms": 192.0,
                "image_queue_avg_wait_ms": 0.8,
                "image_queue_max_wait_ms": 13.0,
                "image_queue_last_wait_ms": 0.1,
                "image_queue_enabled": True,
                "image_queue_max_concurrency": 8,
                "image_queue_timeout_seconds": 5.0,
                "api_threadpool_ok": True,
                "api_threadpool_configured_tokens": 96,
                "api_threadpool_runtime_tokens": 96,
                "api_threadpool_required_tokens": 80,
                "search_log_write_errors": 0,
                "error_log_write_errors": 0,
            },
        }
        api_instance_ids = [f"hai-acceptance-api-{index + 1}" for index in range(args.api_server_count)]
        for phase in (before, after):
            phase["requested"] = True
            phase["successful_source_count"] = len(api_instance_ids)
            phase["sources"] = [
                {
                    "base_url": f"{args.base_url.rstrip('/')}/api-{index + 1}",
                    "ok": True,
                    "process_instance_id": instance_id,
                }
                for index, instance_id in enumerate(api_instance_ids)
            ]
            phase["snapshot"]["admin_metrics_source_count"] = len(api_instance_ids)
            phase["snapshot"]["admin_metrics_instance_ids"] = api_instance_ids
        server_metrics = build_server_metrics_report(
            args.base_url,
            args.admin_key,
            before,
            after,
            expected_api_server_count=args.api_server_count,
        )
        search_log_entries = [
            {
                "type": "search",
                "timestamp": "2026-05-21T00:00:01Z",
                "query_type": result["query_type"],
            }
            for result in results
        ]
        run_log_coverage = build_run_log_coverage(
            search_log_entries,
            before["snapshot"]["generated_at"],
            response_contract,
        )
        annotate_server_metrics_expectations(server_metrics, response_contract, run_log_coverage)
        annotate_server_metrics_guardrails(server_metrics, request_profile)
        assert server_metrics["ok"] is True
        assert server_metrics["coverage_ok"] is True
        assert server_metrics["runtime_guardrails_ok"] is True
        assert server_metrics["delta"]["search_events"] == 850
        assert server_metrics["delta"]["image_search_events"] == 240
        assert server_metrics["delta"]["engine_search_attempts"] == 850
        assert server_metrics["delta"]["engine_adaptive_refetches"] == 0
        assert server_metrics["delta"]["engine_underfilled_after_max_candidates_events"] == 0
        assert server_metrics["delta"]["result_mall_id_mismatch_count"] == 0
        assert server_metrics["delta"]["search_log_write_errors"] == 0
        assert server_metrics["delta"]["error_log_write_errors"] == 0
        assert run_log_coverage["ok"] is True

        multi_api_instance_coverage = api_instance_coverage_report(results, args.api_server_count)
        single_api_instance_coverage = api_instance_coverage_report(
            [{**result, "api_instance_id": "hai-acceptance-api-1"} for result in results],
            1,
        )
        assert multi_api_instance_coverage["ok"] is True
        assert multi_api_instance_coverage["distinct_api_instance_count"] == 2

        query_type_latency = {
            query_type: {
                "count": count,
                "min": 80.0,
                "avg": 110.0,
                "p50": 105.0,
                "p95": 1200.0,
                "p99": 1800.0,
                "max": 2000.0,
            }
            for query_type, count in response_contract["query_type_counts"].items()
        }
        base_report = {
            "ok": True,
            "base_url": args.base_url,
            "mall_id": args.mall_id,
            "origin": args.origin,
            "target_validation": {"ok": True, "base_url": args.base_url, "origin": args.origin},
            "api_server_count": 2,
            "scenario": "mixed-traffic",
            "active_users": 850,
            "mode": "mixed",
            "mode_counts": mode_counts,
            "request_profile": request_profile,
            "traffic_mix_percent": traffic_mix_percent,
            "requests": 850,
            "concurrency": 100,
            "ok_count": 850,
            "error_count": 0,
            "error_rate": 0,
            "total_ms": 10000,
            "requests_per_second": 90.0,
            "latency_ms": {"min": 80.0, "avg": 110.0, "p50": 105.0, "p95": 1200.0, "p99": 1800.0, "max": 2000.0},
            "expected_query_type_latency_ms": query_type_latency,
            "response_query_type_latency_ms": query_type_latency,
            "client_transport": {
                "connection_reuse": "thread_local_keep_alive",
                "search_requests": {
                    "requests_sent": 850,
                    "request_attempts": 850,
                    "connections_opened": 100,
                    "connection_reuses": 750,
                    "stale_reconnects": 0,
                    "connection_close_responses": 0,
                    "gzip_responses": 850,
                    "total_response_body_bytes": 6963200,
                    "max_response_body_bytes": 8192,
                    "last_response_body_bytes": 8192,
                    "total_decoded_response_body_bytes": 13926400,
                    "max_decoded_response_body_bytes": 16384,
                    "last_decoded_response_body_bytes": 16384,
                },
            },
            "thresholds": {
                "p95_ms": 5000,
                "p99_ms": 8000,
                "request_timeout_seconds": 16,
                "max_server_wait_avg_ms": 1000,
                "min_requests_per_second": 5.0,
                "max_error_rate": 1.0,
            },
            "image_input": image_input,
            "mall_identity": mall_identity,
            "response_contract": response_contract,
            "server_metrics": server_metrics,
        }
        single_report = {
            **base_report,
            "api_server_count": 1,
            "requests_per_second": 80.0,
            "api_instance_coverage": single_api_instance_coverage,
        }
        multi_report = {
            **base_report,
            "api_server_count": 2,
            "requests_per_second": 90.0,
            "api_instance_coverage": multi_api_instance_coverage,
        }
        scale_report = build_load_compare_report(single_report, multi_report)
        assert scale_report["ok"] is True, scale_report
        assert scale_report["comparison"]["comparable"] is True
        assert scale_report["comparison"]["rps_ratio"] >= 1.0
        assert scale_report["single"]["response_engine_ok"] is True
        assert scale_report["multi"]["server_metrics_coverage_ok"] is True

        generated_image_report = {**multi_report, "image_input": {"source": "generated"}}
        generated_image_scale = build_load_compare_report(single_report, generated_image_report)
        assert generated_image_scale["ok"] is False
        assert "multi.image_input" in generated_image_scale["problems"]

        local_target_report = {
            **multi_report,
            "base_url": "http://127.0.0.1:8000",
            "target_validation": {
                "ok": True,
                "base_url": "http://127.0.0.1:8000",
                "origin": args.origin,
            },
        }
        local_target_scale = build_load_compare_report(single_report, local_target_report)
        assert local_target_scale["ok"] is False
        assert "multi.base_url_https" in local_target_scale["problems"]
        assert "multi.base_url_non_local" in local_target_scale["problems"]

    def check_requirements_audit_completion_gate_contract(self) -> None:
        local_check_names = sorted(
            {
                check_name
                for requirement in REQUIREMENT_AUDIT_REQUIREMENTS
                for check_name in requirement.get("local_checks", [])
            }
        )
        operational_check_names = sorted(
            {
                check_name
                for requirement in REQUIREMENT_AUDIT_REQUIREMENTS
                for check_name in requirement.get("operational_checks", [])
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            local_path = temp_root / "local-acceptance.json"
            operational_path = temp_root / "operational-readiness.json"
            collection_path = temp_root / "evidence-collection.json"

            local_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "local_only": True,
                        "not_operational_readiness": True,
                        "source_fingerprint": build_source_fingerprint(),
                        "checks": [{"name": name, "ok": True} for name in local_check_names],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            args = type(
                "Args",
                (),
                {
                    "local_acceptance_report": str(local_path),
                    "operational_readiness_report": str(operational_path),
                    "evidence_collection_report": str(collection_path),
                },
            )()

            local_only_report = build_requirements_audit_report(args)
            assert local_only_report["ok"] is False
            assert local_only_report["completion_ready"] is False
            assert local_only_report["summary"]["completion_ready"] is False
            assert local_only_report["summary"]["requirement_count"] == len(REQUIREMENT_AUDIT_REQUIREMENTS)
            assert local_only_report["summary"]["local_acceptance_gate_ok"] is True
            assert local_only_report["summary"]["operational_readiness_ok"] is None
            assert "requirements_not_passed" in local_only_report["summary"]["not_complete_reasons"]
            assert "operational_readiness" in local_only_report["summary"]["not_complete_reasons"]
            assert local_only_report["summary"]["operational_blocker_count"] >= 1
            local_only_markdown = requirements_audit_to_markdown(local_only_report)
            assert "Completion ready: `False`" in local_only_markdown
            assert "Operational Blockers" in local_only_markdown

            operational_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "checks": [
                            {"name": name, "ok": True, "status": "passed"} for name in operational_check_names
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            collection_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": False,
                        "ready_to_execute": True,
                        "evidence_complete": True,
                        "status_counts": {
                            "passed": len(operational_check_names),
                            "failed": 0,
                            "skipped": 0,
                            "planned": 0,
                            "pending": 0,
                        },
                        "failed_steps": [],
                        "skipped_steps": [],
                        "steps": [
                            {"name": name, "status": "passed", "ok": True} for name in operational_check_names
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            operational_path.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "checks": [
                            {"name": name, "ok": True, "status": "passed"} for name in operational_check_names
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            operational_not_ready_report = build_requirements_audit_report(args)
            assert operational_not_ready_report["ok"] is False
            assert operational_not_ready_report["requirements_ok"] is True
            assert operational_not_ready_report["completion_ready"] is False
            assert "operational_readiness" in operational_not_ready_report["summary"]["not_complete_reasons"]

            operational_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "checks": [
                            {"name": name, "ok": True, "status": "passed"} for name in operational_check_names
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            complete_report = build_requirements_audit_report(args)
            assert complete_report["ok"] is True
            assert complete_report["completion_ready"] is True
            assert complete_report["requirements_ok"] is True
            assert complete_report["summary"]["completion_ready"] is True
            assert complete_report["summary"]["not_complete_reasons"] == []
            assert complete_report["summary"]["operational_readiness_ok"] is True
            assert complete_report["summary"]["evidence_collection_complete"] is True

    def check_local_performance_smoke(self) -> None:
        queries = ["검은 우산", "스텐 텀블러", "점착 메모지", "크리스탈 상패", "고급 볼펜"] * 20
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda query: self.service.search(SearchRequest(mall_id="shop001", q=query, limit=20)), queries))
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert elapsed_ms < 3000, f"local 100-query smoke took {elapsed_ms:.1f}ms"


def run_check(check: Callable[[], None]) -> dict[str, object]:
    started = time.perf_counter()
    try:
        check()
        return {
            "name": check.__name__,
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        return {
            "name": check.__name__,
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "traceback_tail": traceback.format_exc()[-4000:],
        }


def main() -> int:
    runner = AcceptanceRunner()
    try:
        report = runner.run()
    finally:
        runner.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
