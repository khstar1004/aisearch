from __future__ import annotations

import math
from pathlib import Path
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .cache import make_search_cache
from .concurrency import ImageSearchGate, ImageSearchQueueFull, SearchExecutionGate, SearchQueueFull
from .config import ROOT, Settings, load_settings
from .engine import BackendCircuitOpenError, BackendRequestError, SearchEngine
from .engine_factory import create_search_engine
from .http_errors import backend_error_detail, backend_error_status, input_error_status
from .image_validation import (
    max_base64_json_body_bytes,
    read_upload_bytes_limited,
    upload_bytes_to_data_url,
    validate_json_image_content_length,
    validate_multipart_content_length,
)
from .instance import API_INSTANCE_HEADER, api_instance_id
from .metrics import build_admin_metrics, metrics_to_prometheus, safe_engine_health
from .models import MAX_PRODUCT_ID_LENGTH, AdminProductRequest, ClickLogRequest, SearchRequest, preferred_mall_id_alias
from .openapi_contract import load_public_openapi_schema
from .rate_limit import RateLimitBucketStore, make_rate_limiter
from .request_body import read_json_object_limited
from .request_context import resolve_client_ip
from .search_service import (
    AISearchService,
    SearchCacheMissInFlight,
    SearchLogger,
    log_detail_value,
    normalize_tail_limit,
)
from .security import (
    PublicAccessError,
    public_api_key_field_names,
    unsupported_multipart_field_names,
    validate_public_header_access,
    validate_click_product_url,
    validate_mall_access,
)
from .sync import SyncService, make_product_source
from scripts.search_insights import build_report as build_search_insights_report

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.concurrency import run_in_threadpool
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
    from fastapi.openapi.utils import get_openapi
    from fastapi.responses import FileResponse
    from fastapi.responses import JSONResponse
    from fastapi.responses import PlainTextResponse
    from starlette.middleware.gzip import GZipMiddleware
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install FastAPI dependencies: pip install -r requirements.txt") from exc


settings = load_settings()
engine = None
search_service = None
sync_service = None
error_logger = SearchLogger(settings.error_log_path, keep_open_seconds=settings.log_keep_open_seconds)
rate_limit_bucket_store = RateLimitBucketStore(
    settings.rate_limit_max_buckets,
    prune_interval_seconds=settings.rate_limit_prune_interval_seconds,
)
rate_limit_lock = threading.RLock()
shared_rate_limiter = None
search_execution_gate = None
image_search_gate = None
api_threadpool_status: dict[str, Any] = {}


@dataclass
class ParsedSearchInput:
    payload: dict[str, Any]
    upload: Any | None = None

    @property
    def has_image(self) -> bool:
        return self.upload is not None or bool(self.payload.get("image_base64"))


def create_engine(active_settings: Settings) -> SearchEngine:
    return create_search_engine(active_settings, preload_local_products=True)


def get_search_service() -> AISearchService:
    assert search_service is not None
    return search_service


def get_sync_service() -> SyncService:
    assert sync_service is not None
    return sync_service


def require_admin(
    request: Request,
    x_admin_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    if public_api_key_field_names(request.query_params):
        raise HTTPException(
            status_code=400,
            detail="API key query parameter is not supported; use X-Admin-Key or Authorization header",
        )
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    if not secure_key_matches(x_admin_key, settings.admin_api_key) and not secure_key_matches(bearer, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="admin authentication required")


def secure_key_matches(candidate: str | None, expected: str) -> bool:
    if not candidate:
        return False
    return secrets.compare_digest(str(candidate), str(expected))


def normalize_admin_product_id(product_id: str) -> str:
    value = str(product_id or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="product_id is required")
    if len(value) > MAX_PRODUCT_ID_LENGTH:
        raise HTTPException(status_code=400, detail=f"product_id must be at most {MAX_PRODUCT_ID_LENGTH} characters")
    return value


app = FastAPI(title="Haeorum Gift AI Search", version="0.1.0")


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    try:
        schema = load_public_openapi_schema()
    except Exception:
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Key", "X-API-Key"],
    expose_headers=[API_INSTANCE_HEADER],
)
if settings.api_gzip_minimum_size > 0:
    app.add_middleware(GZipMiddleware, minimum_size=settings.api_gzip_minimum_size)

MAX_REJECTED_BODY_DRAIN_BYTES = 32 * 1024 * 1024


@app.middleware("http")
async def add_api_instance_header(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers[API_INSTANCE_HEADER] = api_instance_id()
    return response


def configure_api_threadpool(active_settings: Settings) -> dict[str, Any]:
    requested_tokens = max(1, int(active_settings.api_threadpool_tokens))
    try:
        import anyio.to_thread

        limiter = anyio.to_thread.current_default_thread_limiter()
        previous_tokens = int(limiter.total_tokens)
        limiter.total_tokens = requested_tokens
        return {
            "ok": True,
            "configured": True,
            "requested_tokens": requested_tokens,
            "previous_tokens": previous_tokens,
            "total_tokens": int(limiter.total_tokens),
        }
    except Exception as exc:  # pragma: no cover - defensive for non-ASGI test contexts
        return {
            "ok": False,
            "configured": False,
            "requested_tokens": requested_tokens,
            "previous_tokens": None,
            "total_tokens": None,
            "last_error": f"{exc.__class__.__name__}: {exc}",
        }


@app.exception_handler(HTTPException)
async def log_http_exception(request: Request, exc: HTTPException):
    log_api_error(request, exc.status_code, exc.detail)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def log_request_validation_exception(request: Request, exc: RequestValidationError):
    log_api_error(request, 422, exc.errors(), error_type="RequestValidationError")
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception):
    log_api_error(request, 500, "internal server error", error_type=exc.__class__.__name__)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.on_event("startup")
async def startup() -> None:
    global engine, search_service, sync_service, shared_rate_limiter, search_execution_gate, image_search_gate, api_threadpool_status, rate_limit_bucket_store
    api_threadpool_status = configure_api_threadpool(settings)
    engine = create_engine(settings)
    search_cache = make_search_cache(settings)
    search_service = AISearchService(
        engine,
        settings,
        SearchLogger(settings.search_log_path, keep_open_seconds=settings.log_keep_open_seconds),
        cache=search_cache,
    )
    sync_service = SyncService(engine, make_product_source(settings), settings, search_cache=search_cache)
    shared_rate_limiter = make_rate_limiter(
        settings.redis_url,
        settings.redis_key_prefix,
        fallback_max_buckets=settings.rate_limit_max_buckets,
        fallback_prune_interval_seconds=settings.rate_limit_prune_interval_seconds,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
        socket_connect_timeout_seconds=settings.redis_socket_connect_timeout_seconds,
        failure_backoff_seconds=settings.redis_failure_backoff_seconds,
    )
    rate_limit_bucket_store = RateLimitBucketStore(
        settings.rate_limit_max_buckets,
        prune_interval_seconds=settings.rate_limit_prune_interval_seconds,
    )
    search_execution_gate = SearchExecutionGate(
        settings.search_max_concurrency,
        settings.search_queue_timeout_seconds,
    )
    image_search_gate = ImageSearchGate(
        settings.image_search_max_concurrency,
        settings.image_search_queue_timeout_seconds,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    if engine is not None:
        engine.close()
    if search_service is not None:
        search_service.logger.close()
    error_logger.close()


@app.get("/health")
def health() -> dict[str, Any]:
    assert engine is not None
    return safe_engine_health(settings, engine)


@app.post("/api/ai-search")
async def ai_search(request: Request, service: AISearchService = Depends(get_search_service)) -> dict[str, Any]:
    upload_slot_context = None
    try:
        validate_public_access_headers(request)
        await run_in_threadpool(enforce_search_client_rate_limit, request)
        max_image_bytes = settings.max_image_mb * 1024 * 1024
        if is_multipart_search_request(request):
            await validate_multipart_content_length_or_drain(request, max_image_bytes=max_image_bytes)
        else:
            await validate_json_image_content_length_or_drain(request, max_image_bytes=max_image_bytes)
        parsed = await parse_search_request(request)
        payload = parsed.payload
        validate_public_access(payload, request)
        mall_id = request_mall_id(payload)
        await run_in_threadpool(enforce_search_mall_rate_limit, mall_id)
        if parsed.has_image:
            await run_in_threadpool(enforce_image_rate_limit, request, mall_id)
            if parsed.upload is not None:
                upload_slot_context = await run_in_threadpool(enter_image_search_slot_context)
                try:
                    payload["image_base64"] = await read_multipart_image_data_url(parsed.upload)
                finally:
                    await run_in_threadpool(exit_image_search_slot_context, upload_slot_context)
                    upload_slot_context = None
        search_request = SearchRequest.model_validate(payload)
        if search_request.image_base64:
            result = await run_in_threadpool(service.search, search_request, acquire_image_search_slot)
        else:
            result = await run_in_threadpool(service.search, search_request, acquire_search_execution_slot)
    except ValueError as exc:
        raise HTTPException(status_code=input_error_status(str(exc)), detail=str(exc)) from exc
    except SearchQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ImageSearchQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except SearchCacheMissInFlight as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except BackendCircuitOpenError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except BackendRequestError as exc:
        raise HTTPException(status_code=backend_error_status(exc), detail=backend_error_detail(exc)) from exc
    finally:
        if upload_slot_context is not None:
            await run_in_threadpool(exit_image_search_slot_context, upload_slot_context)
    return result.model_dump(mode="json")


@app.post("/api/click-log")
async def click_log(request: Request, service: AISearchService = Depends(get_search_service)) -> dict[str, Any]:
    try:
        validate_public_access_headers(request)
        await run_in_threadpool(enforce_click_client_rate_limit, request)
        max_image_bytes = settings.max_image_mb * 1024 * 1024
        await validate_json_image_content_length_or_drain(request, max_image_bytes=max_image_bytes)
        payload = await read_json_object(request, max_bytes=max_base64_json_body_bytes(max_image_bytes))
        validate_public_access(payload, request)
        click = ClickLogRequest.model_validate(payload)
        validate_click_product_url(settings, click.mall_id, click.product_url, click.product_id)
        await run_in_threadpool(enforce_click_mall_rate_limit, click.mall_id)
    except PublicAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await run_in_threadpool(service.log_click, click)
    return {"ok": True}


@app.post("/admin/sync", dependencies=[Depends(require_admin)])
def admin_sync(since: str | None = None, service: SyncService = Depends(get_sync_service)) -> dict[str, Any]:
    return service.sync_changed(since).model_dump(mode="json")


@app.post("/admin/reindex", dependencies=[Depends(require_admin)])
def admin_reindex(service: SyncService = Depends(get_sync_service)) -> dict[str, Any]:
    return service.reindex_all().model_dump(mode="json")


@app.post("/admin/reindex/{product_id:path}", dependencies=[Depends(require_admin)])
def admin_reindex_product(
    product_id: str,
    mall_id: str | None = None,
    service: SyncService = Depends(get_sync_service),
) -> dict[str, Any]:
    return service.reindex_product(normalize_admin_product_id(product_id), mall_id=mall_id).model_dump(mode="json")


@app.post("/admin/reindex-product", dependencies=[Depends(require_admin)])
def admin_reindex_product_body(payload: AdminProductRequest, service: SyncService = Depends(get_sync_service)) -> dict[str, Any]:
    return service.reindex_product(normalize_admin_product_id(payload.product_id), mall_id=payload.mall_id).model_dump(mode="json")


@app.delete("/admin/product/{product_id:path}", dependencies=[Depends(require_admin)])
def admin_delete_product(
    product_id: str,
    mall_id: str | None = None,
    service: SyncService = Depends(get_sync_service),
) -> dict[str, Any]:
    return service.delete_product(normalize_admin_product_id(product_id), mall_id=mall_id).model_dump(mode="json")


@app.post("/admin/delete-product", dependencies=[Depends(require_admin)])
def admin_delete_product_body(payload: AdminProductRequest, service: SyncService = Depends(get_sync_service)) -> dict[str, Any]:
    return service.delete_product(normalize_admin_product_id(payload.product_id), mall_id=payload.mall_id).model_dump(mode="json")


@app.get("/admin/sync-status", dependencies=[Depends(require_admin)])
def admin_sync_status(service: SyncService = Depends(get_sync_service)) -> dict[str, Any]:
    return service.current_status().model_dump(mode="json")


@app.get("/admin/search-log", dependencies=[Depends(require_admin)])
def admin_search_log(limit: int = 100, service: AISearchService = Depends(get_search_service)) -> list[dict[str, Any]]:
    return service.logger.tail(normalize_tail_limit(limit))


@app.get("/admin/sync-log", dependencies=[Depends(require_admin)])
def admin_sync_log(limit: int = 100, service: SyncService = Depends(get_sync_service)) -> list[dict[str, Any]]:
    return service.logger.tail(normalize_tail_limit(limit))


@app.get("/admin/search-insights", dependencies=[Depends(require_admin)])
def admin_search_insights(
    limit: int = 20,
    min_searches: int = 1,
    slow_text_ms: float = 3000.0,
    slow_image_ms: float = 5000.0,
    slow_mixed_ms: float = 5000.0,
    service: AISearchService = Depends(get_search_service),
) -> dict[str, Any]:
    return build_search_insights_report(
        service.logger.path,
        limit=normalize_tail_limit(limit),
        min_searches=normalize_tail_limit(min_searches),
        slow_text_ms=slow_text_ms,
        slow_image_ms=slow_image_ms,
        slow_mixed_ms=slow_mixed_ms,
    )


@app.get("/admin/error-log", dependencies=[Depends(require_admin)])
def admin_error_log(limit: int = 100) -> list[dict[str, Any]]:
    return error_logger.tail(normalize_tail_limit(limit))


@app.get("/admin/metrics", dependencies=[Depends(require_admin)])
def admin_metrics(
    limit: int = 1000,
    service: AISearchService = Depends(get_search_service),
    sync: SyncService = Depends(get_sync_service),
) -> dict[str, Any]:
    assert engine is not None
    return build_admin_metrics(
        settings,
        engine,
        service.logger,
        error_logger,
        sync,
        limit=normalize_tail_limit(limit),
        rate_limiter=shared_rate_limiter,
        memory_rate_limit_bucket_count=memory_rate_limit_bucket_count(),
        search_cache=service.cache,
        search_singleflight=service,
        image_validation_singleflight=service,
        search_execution_gate=search_execution_gate,
        image_search_gate=image_search_gate,
        api_threadpool_status=api_threadpool_status,
    )


@app.get("/admin/metrics.prom", dependencies=[Depends(require_admin)])
def admin_metrics_prometheus(
    limit: int = 1000,
    service: AISearchService = Depends(get_search_service),
    sync: SyncService = Depends(get_sync_service),
) -> PlainTextResponse:
    assert engine is not None
    metrics = build_admin_metrics(
        settings,
        engine,
        service.logger,
        error_logger,
        sync,
        limit=normalize_tail_limit(limit),
        rate_limiter=shared_rate_limiter,
        memory_rate_limit_bucket_count=memory_rate_limit_bucket_count(),
        search_cache=service.cache,
        search_singleflight=service,
        image_validation_singleflight=service,
        search_execution_gate=search_execution_gate,
        image_search_gate=image_search_gate,
        api_threadpool_status=api_threadpool_status,
    )
    return PlainTextResponse(metrics_to_prometheus(metrics), media_type="text/plain; version=0.0.4")


@app.get("/admin-ui")
def admin_dashboard() -> FileResponse:
    return FileResponse(Path(ROOT) / "admin_dashboard.html", media_type="text/html")


@app.get("/widget.js")
def widget_js() -> FileResponse:
    return FileResponse(Path(ROOT) / "widget" / "widget.js", media_type="text/javascript")


async def parse_search_request(request: Request) -> ParsedSearchInput:
    max_image_bytes = settings.max_image_mb * 1024 * 1024
    if is_multipart_search_request(request):
        validate_multipart_content_length(request.headers.get("content-length"), max_image_bytes=max_image_bytes)
        form = await read_multipart_form(request)
        reject_body_api_key_fields(form)
        reject_unsupported_multipart_fields(form)
        payload = {
            "mall_id": request_mall_id({"mall_id": form.get("mall_id"), "site_id": form.get("site_id")}),
            "q": form.get("q"),
            "limit": parse_int_field(form.get("limit"), settings.default_limit, "limit"),
            "offset": parse_int_field(form.get("offset"), 0, "offset"),
            "category": form.get("category"),
            "print_method": form.get("print_method"),
            "material": form.get("material"),
            "color": form.get("color"),
        }
        if form.get("min_price"):
            payload["min_price"] = parse_float_field(form.get("min_price"), "min_price")
        if form.get("max_price"):
            payload["max_price"] = parse_float_field(form.get("max_price"), "max_price")
        if form.get("quantity"):
            payload["quantity"] = parse_int_field(form.get("quantity"), 0, "quantity")
        if form.get("order_qty"):
            payload["quantity"] = parse_int_field(form.get("order_qty"), 0, "order_qty")
        if form.get("max_delivery_days"):
            payload["max_delivery_days"] = parse_int_field(form.get("max_delivery_days"), 0, "max_delivery_days")
        if form.get("text_weight"):
            payload["text_weight"] = parse_float_field(form.get("text_weight"), "text_weight")
        if form.get("image_weight"):
            payload["image_weight"] = parse_float_field(form.get("image_weight"), "image_weight")
        upload = form.get("image")
        return ParsedSearchInput(payload, upload=upload)
    validate_json_image_content_length(request.headers.get("content-length"), max_image_bytes=max_image_bytes)
    data = await read_json_object(request, max_bytes=max_base64_json_body_bytes(max_image_bytes))
    if "limit" not in data:
        data["limit"] = settings.default_limit
    return ParsedSearchInput(data)


async def read_multipart_image_data_url(upload: Any) -> str:
    max_image_bytes = settings.max_image_mb * 1024 * 1024
    raw = await read_upload_bytes_limited(upload, max_bytes=max_image_bytes)
    return upload_bytes_to_data_url(raw, getattr(upload, "content_type", None))


async def read_multipart_form(request: Request) -> Any:
    try:
        return await request.form()
    except Exception as exc:
        raise ValueError("invalid multipart form body") from exc


async def read_json_object(request: Request, max_bytes: int | None = None) -> dict[str, Any]:
    return await read_json_object_limited(request, max_bytes=max_bytes)


def is_multipart_search_request(request: Request) -> bool:
    return request.headers.get("content-type", "").lower().startswith("multipart/form-data")


async def validate_multipart_content_length_or_drain(request: Request, max_image_bytes: int) -> None:
    try:
        validate_multipart_content_length(request.headers.get("content-length"), max_image_bytes=max_image_bytes)
    except ValueError as exc:
        await drain_rejected_request_body_if_payload_too_large(request, exc)
        raise


async def validate_json_image_content_length_or_drain(request: Request, max_image_bytes: int) -> None:
    try:
        validate_json_image_content_length(request.headers.get("content-length"), max_image_bytes=max_image_bytes)
    except ValueError as exc:
        await drain_rejected_request_body_if_payload_too_large(request, exc)
        raise


async def drain_rejected_request_body_if_payload_too_large(request: Request, exc: ValueError) -> None:
    if input_error_status(str(exc)) != 413:
        return
    content_length_text = request.headers.get("content-length")
    try:
        content_length = int(str(content_length_text or "0").strip())
    except ValueError:
        return
    if content_length < 1 or content_length > MAX_REJECTED_BODY_DRAIN_BYTES:
        return
    total = 0
    async for chunk in request.stream():
        data = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
        total += len(data)
        if total >= content_length:
            break


def parse_int_field(value: Any, default: int, field_name: str) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def parse_float_field(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite")
    return parsed


def enforce_search_client_rate_limit(request: Request) -> None:
    client = request_client_ip(request)
    now = time.time()
    client_key = f"search:ip:{client}"
    if shared_rate_limiter is not None:
        client_allowed, _ = shared_rate_limiter.hit(client_key, settings.search_rate_limit_per_minute, now=now)
    else:
        client_allowed, _ = record_memory_rate_limit_hit(client_key, settings.search_rate_limit_per_minute, now)
    if not client_allowed:
        raise HTTPException(status_code=429, detail="search rate limit exceeded for client")


def enforce_search_mall_rate_limit(mall_id: str | None) -> None:
    now = time.time()
    mall_key = str(mall_id or "unknown").strip() or "unknown"
    mall_limit_key = f"search:mall:{mall_key}"
    if shared_rate_limiter is not None:
        mall_allowed, _ = shared_rate_limiter.hit(mall_limit_key, settings.mall_search_rate_limit_per_minute, now=now)
    else:
        mall_allowed, _ = record_memory_rate_limit_hit(mall_limit_key, settings.mall_search_rate_limit_per_minute, now)
    if not mall_allowed:
        raise HTTPException(status_code=429, detail="search rate limit exceeded for mall")


def enforce_image_rate_limit(request: Request, mall_id: str | None) -> None:
    client = request_client_ip(request)
    now = time.time()
    client_key = f"image:ip:{client}"
    if shared_rate_limiter is not None:
        client_allowed, _ = shared_rate_limiter.hit(client_key, settings.image_rate_limit_per_minute, now=now)
    else:
        client_allowed, _ = record_memory_rate_limit_hit(client_key, settings.image_rate_limit_per_minute, now)
    if not client_allowed:
        raise HTTPException(status_code=429, detail="image search rate limit exceeded for client")

    mall_key = str(mall_id or "unknown").strip() or "unknown"
    rate_limit_key = f"image:mall:{mall_key}"
    if shared_rate_limiter is not None:
        mall_allowed, _ = shared_rate_limiter.hit(rate_limit_key, settings.mall_image_rate_limit_per_minute, now=now)
    else:
        mall_allowed, _ = record_memory_rate_limit_hit(rate_limit_key, settings.mall_image_rate_limit_per_minute, now)
    if not mall_allowed:
        raise HTTPException(status_code=429, detail="image search rate limit exceeded for mall")


def enforce_search_rate_limit(request: Request, mall_id: str | None) -> None:
    enforce_search_client_rate_limit(request)
    enforce_search_mall_rate_limit(mall_id)


def enforce_click_client_rate_limit(request: Request) -> None:
    client = request_client_ip(request)
    now = time.time()
    client_key = f"click:ip:{client}"
    if shared_rate_limiter is not None:
        client_allowed, _ = shared_rate_limiter.hit(client_key, settings.click_rate_limit_per_minute, now=now)
    else:
        client_allowed, _ = record_memory_rate_limit_hit(client_key, settings.click_rate_limit_per_minute, now)
    if not client_allowed:
        raise HTTPException(status_code=429, detail="click log rate limit exceeded for client")


def enforce_click_mall_rate_limit(mall_id: str | None) -> None:
    now = time.time()
    mall_key = str(mall_id or "unknown").strip() or "unknown"
    mall_limit_key = f"click:mall:{mall_key}"
    if shared_rate_limiter is not None:
        mall_allowed, _ = shared_rate_limiter.hit(mall_limit_key, settings.mall_click_rate_limit_per_minute, now=now)
    else:
        mall_allowed, _ = record_memory_rate_limit_hit(mall_limit_key, settings.mall_click_rate_limit_per_minute, now)
    if not mall_allowed:
        raise HTTPException(status_code=429, detail="click log rate limit exceeded for mall")


def enforce_click_rate_limit(request: Request, mall_id: str | None) -> None:
    enforce_click_client_rate_limit(request)
    enforce_click_mall_rate_limit(mall_id)


def record_memory_rate_limit_hit(key: str, limit: int, now: float) -> tuple[bool, int]:
    with rate_limit_lock:
        return rate_limit_bucket_store.hit(
            key,
            limit,
            now=now,
        )


def memory_rate_limit_bucket_count() -> int:
    with rate_limit_lock:
        return int(rate_limit_bucket_store.status()["bucket_count"])


@contextmanager
def acquire_search_execution_slot():
    assert search_execution_gate is not None
    with search_execution_gate.slot():
        yield


@contextmanager
def acquire_image_search_slot():
    assert image_search_gate is not None
    with acquire_search_execution_slot():
        with image_search_gate.slot():
            yield


def enter_image_search_slot_context():
    context = acquire_image_search_slot()
    context.__enter__()
    return context


def exit_image_search_slot_context(context: Any) -> None:
    context.__exit__(None, None, None)


def validate_public_access(payload: dict[str, Any], request: Request) -> None:
    if public_api_key_field_names(request.query_params):
        raise HTTPException(status_code=400, detail="api_key query parameter is not supported; use X-API-Key header")
    reject_body_api_key_fields(payload)
    api_key = request.headers.get("x-api-key")
    origin = request.headers.get("origin")
    try:
        validate_mall_access(settings, request_mall_id(payload), api_key, origin=origin)
    except PublicAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def validate_public_access_headers(request: Request) -> None:
    if public_api_key_field_names(request.query_params):
        raise HTTPException(status_code=400, detail="api_key query parameter is not supported; use X-API-Key header")
    try:
        validate_public_header_access(
            settings,
            request.headers.get("x-api-key"),
            origin=request.headers.get("origin"),
        )
    except PublicAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def reject_body_api_key_fields(payload: Any) -> None:
    if public_api_key_field_names(payload):
        raise HTTPException(status_code=400, detail="API key request body field is not supported; use X-API-Key header")


def reject_unsupported_multipart_fields(payload: Any) -> None:
    fields = unsupported_multipart_field_names(payload)
    if fields:
        label = "field" if len(fields) == 1 else "fields"
        raise ValueError(f"unsupported multipart {label}: {', '.join(fields)}")


def request_mall_id(payload: dict[str, Any]) -> str | None:
    return preferred_mall_id_alias(payload)


def request_client_ip(request: Request) -> str:
    return resolve_client_ip(request.client.host if request.client else None, request.headers, settings)


def log_api_error(request: Request, status_code: int, detail: Any, error_type: str | None = None) -> None:
    if request.url.path == "/health":
        return
    try:
        error_logger.write(
            {
                "type": "api_error",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "detail": log_detail_value(detail),
                "client": request_client_ip(request),
                "error_type": error_type,
            }
        )
    except Exception:
        pass
