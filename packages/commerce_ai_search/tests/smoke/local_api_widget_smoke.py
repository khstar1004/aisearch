from __future__ import annotations

import argparse
import base64
import io
import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi.testclient import TestClient

import commerce_ai_search.main as app_module
from commerce_ai_search.cache import MemorySearchCache
from commerce_ai_search.concurrency import ImageSearchGate, SearchExecutionGate
from commerce_ai_search.config import ROOT, Settings
from commerce_ai_search.engine import LocalSearchEngine
from commerce_ai_search.models import ProductDocument, SyncStatus
from commerce_ai_search.rate_limit import RateLimitBucketStore
from commerce_ai_search.search_service import AISearchService, SearchLogger


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class SmokeConfig:
    admin_key: str = "dev-admin-key"
    mall_id: str = "shop001"
    query: str = "스텐텀블러"
    api_key: str = ""
    origin: str = ""
    expected_first_product_id: str = "TB001"
    skip_click_log: bool = False
    include_image_search: bool = False


class FakeSyncService:
    def __init__(self, logger: SearchLogger):
        self.logger = logger

    def current_status(self) -> SyncStatus:
        return SyncStatus(engine="local", index="local-products")


class InProcessClient:
    def __init__(self, client: TestClient):
        self.client = client

    def get_json(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        response = self.client.get(path, headers=headers or {})
        return response.status_code, response.json()

    def get_text(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
        response = self.client.get(path, headers=headers or {})
        return response.status_code, response.text

    def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        response = self.client.post(path, json=payload, headers=headers or {})
        return response.status_code, response.json()


class UrlClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def get_json(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        status, text = self.get_text(path, headers=headers)
        return status, json.loads(text)

    def get_text(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
        request = urllib.request.Request(self.base_url + path, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return int(response.status), response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read().decode("utf-8")

    def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        request = urllib.request.Request(self.base_url + path, data=body, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return int(exc.code), json.loads(exc.read().decode("utf-8"))


def configure_in_process_app(log_dir: Path) -> tuple[TestClient, list[SearchLogger]]:
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
        search_max_concurrency=8,
        image_search_max_concurrency=2,
        api_threadpool_tokens=18,
        search_log_path=log_dir / "search.jsonl",
        error_log_path=log_dir / "error.jsonl",
        sync_log_path=log_dir / "sync.jsonl",
        product_url_template="https://{mall_id}.example.com/product/{product_id}",
    )
    engine = LocalSearchEngine(
        [
            product("TB001", "스텐 진공 텀블러", "텀블러", ["스텐", "보온병"]),
            product("UM001", "검정 3단 자동 우산", "우산", ["검정", "접이식"]),
            product("TW001", "송월 타올 답례품", "타올", ["송월", "수건"]),
        ]
    )
    search_logger = SearchLogger(settings.search_log_path)
    error_logger = SearchLogger(settings.error_log_path)
    sync_logger = SearchLogger(settings.sync_log_path)
    cache = MemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries)
    service = AISearchService(engine, settings, logger=search_logger, cache=cache)
    app_module.settings = settings
    app_module.engine = engine
    app_module.search_service = service
    app_module.sync_service = FakeSyncService(sync_logger)
    app_module.shared_rate_limiter = None
    app_module.rate_limit_bucket_store = RateLimitBucketStore(settings.rate_limit_max_buckets)
    app_module.search_execution_gate = SearchExecutionGate(8, 0.1)
    app_module.image_search_gate = ImageSearchGate(2, 0.1)
    app_module.api_threadpool_status = {
        "ok": True,
        "configured": True,
        "requested_tokens": 18,
        "previous_tokens": 40,
        "total_tokens": 18,
    }
    app_module.error_logger = error_logger
    return TestClient(app_module.app, raise_server_exceptions=False), [search_logger, error_logger, sync_logger]


def product(product_id: str, name: str, category: str, keywords: list[str]) -> ProductDocument:
    return ProductDocument.model_validate(
        {
            "product_id": product_id,
            "product_name": name,
            "category_name": category,
            "main_image_url": f"https://cdn.example.com/{product_id}.jpg",
            "mall_id": "shop001",
            "status": "active",
            "display_yn": "Y",
            "keywords": keywords,
        }
    )


def make_png_data_url(width: int = 32, height: int = 32) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(12, 120, 110)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def append_check(checks: list[SmokeCheck], name: str, condition: bool, detail: str = "") -> None:
    checks.append(SmokeCheck(name=name, ok=bool(condition), detail=detail))


def summarize_health(health: dict[str, Any]) -> str:
    stats = health.get("stats") if isinstance(health.get("stats"), dict) else {}
    summary = {
        "engine": health.get("engine"),
        "backend": health.get("backend"),
        "ready": health.get("ready"),
        "index": health.get("index"),
        "marqo_ready": health.get("marqo_ready"),
        "gemini_ready": health.get("gemini_ready"),
        "documents": stats.get("numberOfDocuments"),
        "vectors": stats.get("numberOfVectors"),
    }
    return json.dumps(summary, ensure_ascii=False)


def summarize_search(search: dict[str, Any], status_code: int) -> str:
    top = search.get("top") or []
    first = top[0] if top else {}
    meta = search.get("meta") or {}
    product_url = str(first.get("product_url") or "")
    parsed_url = urlparse(product_url)
    summary = {
        "status": status_code,
        "top_count": len(top),
        "first_product_id": first.get("product_id"),
        "first_score_percent": first.get("score_percent"),
        "first_product_url_origin": f"{parsed_url.scheme}://{parsed_url.netloc}" if parsed_url.netloc else None,
        "meta_engine": meta.get("engine"),
        "meta_elapsed_ms": meta.get("elapsed_ms"),
    }
    return json.dumps(summary, ensure_ascii=False)


def public_headers(config: SmokeConfig) -> dict[str, str]:
    headers: dict[str, str] = {}
    if config.api_key:
        headers["X-API-Key"] = config.api_key
    if config.origin:
        headers["Origin"] = config.origin
    return headers


def run_smoke(client: InProcessClient | UrlClient, config: SmokeConfig) -> list[SmokeCheck]:
    checks: list[SmokeCheck] = []
    health_status, health = client.get_json("/health")
    append_check(checks, "health", health_status == 200 and health.get("ready") is True, summarize_health(health))

    widget_status, widget_js = client.get_text("/widget.js")
    append_check(
        checks,
        "widget_js_served",
        widget_status == 200 and "window.HaeorumAISearch" in widget_js,
        f"status={widget_status}",
    )

    headers = public_headers(config)
    search_status, search = client.post_json(
        "/api/ai-search",
        {"mall_id": config.mall_id, "q": config.query, "limit": 5},
        headers=headers,
    )
    top = search.get("top") or []
    first = top[0] if top else {}
    expected_product_matches = (
        first.get("product_id") == config.expected_first_product_id if config.expected_first_product_id else bool(first)
    )
    append_check(
        checks,
        "text_search",
        search_status == 200 and expected_product_matches,
        summarize_search(search, search_status),
    )

    if config.include_image_search:
        image_payload = make_png_data_url()
        image_status, image_search = client.post_json(
            "/api/ai-search",
            {"mall_id": config.mall_id, "image_base64": image_payload, "limit": 5},
            headers=headers,
        )
        image_meta = image_search.get("meta") or {}
        append_check(
            checks,
            "image_search",
            image_status == 200 and image_meta.get("query_type") == "image" and bool(image_search.get("top")),
            summarize_search(image_search, image_status),
        )

        mixed_status, mixed_search = client.post_json(
            "/api/ai-search",
            {"mall_id": config.mall_id, "q": config.query, "image_base64": image_payload, "limit": 5},
            headers=headers,
        )
        mixed_meta = mixed_search.get("meta") or {}
        append_check(
            checks,
            "mixed_search",
            mixed_status == 200 and mixed_meta.get("query_type") == "text_image" and bool(mixed_search.get("top")),
            summarize_search(mixed_search, mixed_status),
        )

    if config.skip_click_log:
        append_check(checks, "click_log_skipped", True, "skipped by --skip-click-log")
    else:
        click_status, click = client.post_json(
            "/api/click-log",
            {
                "mall_id": config.mall_id,
                "product_id": first.get("product_id") or config.expected_first_product_id or "TB001",
                "product_url": first.get("product_url") or "https://shop001.example.com/product/TB001",
                "position": 1,
                "query": config.query,
            },
            headers=headers,
        )
        append_check(checks, "click_log", click_status == 200 and click.get("ok") is True, str(click))

    metrics_status, metrics = client.get_json("/admin/metrics", headers={"X-Admin-Key": config.admin_key})
    append_check(
        checks,
        "admin_metrics",
        metrics_status == 200 and (metrics.get("api_threadpool") or {}).get("ok") is True,
        str(metrics.get("api_threadpool")),
    )

    prewarm_status, prewarm = client.post_json(
        "/admin/prewarm-query-cache",
        {"queries": [config.query, "검정우산"], "batch_size": 2},
        headers={"X-Admin-Key": config.admin_key},
    )
    append_check(
        checks,
        "admin_query_cache_prewarm",
        prewarm_status == 200 and prewarm.get("ok") is True and "supported" in prewarm,
        str({key: prewarm.get(key) for key in ["supported", "computed", "cached", "skipped"]}),
    )

    prom_status, prometheus = client.get_text("/admin/metrics.prom", headers={"X-Admin-Key": config.admin_key})
    append_check(
        checks,
        "prometheus_metrics",
        prom_status == 200 and "haeorum_api_threadpool_required_tokens" in prometheus,
        f"status={prom_status}",
    )

    return checks + static_asset_checks()


def static_asset_checks() -> list[SmokeCheck]:
    widget_dir = Path(ROOT) / "widget"
    demo_html = (widget_dir / "demo.html").read_text(encoding="utf-8")
    result_html = (widget_dir / "ai-search.html").read_text(encoding="utf-8")
    return [
        SmokeCheck("demo_mount_selector", 'target: "#haeorum-ai-search"' in demo_html),
        SmokeCheck("demo_result_page_link", 'resultPageUrl: "./ai-search.html"' in demo_html),
        SmokeCheck("result_upload_accessibility", 'aria-label="상품 이미지 업로드"' in result_html),
        SmokeCheck("result_url_guard", "isUnsafeLocalBrowserHost" in result_html),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test local Commerce AI Search API and widget assets.")
    parser.add_argument("--base-url", default="", help="Optional running API base URL. Defaults to in-process app.")
    parser.add_argument("--admin-key", default="dev-admin-key")
    parser.add_argument("--mall-id", default="shop001")
    parser.add_argument("--query", default="스텐텀블러")
    parser.add_argument("--api-key", default="", help="Optional public X-API-Key for restricted mall API smoke.")
    parser.add_argument("--origin", default="", help="Optional Origin header for restricted mall API smoke.")
    parser.add_argument(
        "--expected-first-product-id",
        default="TB001",
        help="Expected top product id. Pass an empty string to require only a non-empty result.",
    )
    parser.add_argument(
        "--skip-click-log",
        action="store_true",
        help="Skip click-log write verification. Useful for read-only live checks before deploying URL contract fixes.",
    )
    parser.add_argument(
        "--include-image-search",
        action="store_true",
        help="Also verify image and mixed search. Defaults on for in-process smoke and off for --base-url live smoke.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loggers: list[SearchLogger] = []
    try:
        if args.base_url:
            client: InProcessClient | UrlClient = UrlClient(args.base_url)
        else:
            temp_dir = tempfile.TemporaryDirectory()
            client_raw, loggers = configure_in_process_app(Path(temp_dir.name))
            client = InProcessClient(client_raw)
        config = SmokeConfig(
            admin_key=args.admin_key,
            mall_id=args.mall_id,
            query=args.query,
            api_key=args.api_key,
            origin=args.origin,
            expected_first_product_id=args.expected_first_product_id,
            skip_click_log=args.skip_click_log,
            include_image_search=bool(args.include_image_search or not args.base_url),
        )
        checks = run_smoke(client, config)
        report = {
            "ok": all(check.ok for check in checks),
            "checks": [asdict(check) for check in checks],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["ok"]:
            raise SystemExit(1)
    finally:
        for logger in loggers:
            logger.close()


if __name__ == "__main__":
    main()
