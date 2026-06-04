from __future__ import annotations

import csv
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .cache import SearchCache
from .config import Settings, validate_sql_identifier_value
from .engine import SearchEngine
from .identifiers import (
    normalize_mall_id,
    product_delete_document_ids,
    product_identity_key,
    product_identity_label,
    public_product_id_from_document_id,
)
from .image_probe import ImageProbeResult, ProductImageProbe
from .models import INACTIVE_STATUSES, ProductDocument, SyncResult, SyncStatus
from .search_service import read_jsonl_tail, read_reverse_lines, sanitize_log_entry
from .sql_safety import clean_readonly_query, validate_readonly_query


SYNC_FAILURE_SAMPLE_LIMIT = 100


class SyncAlreadyRunning(RuntimeError):
    pass


class SyncOperationLock:
    def __init__(self, path: Path, mode: str, stale_seconds: int = 0):
        self.path = path
        self.mode = mode
        self.stale_seconds = stale_seconds
        self._fd: int | None = None

    def __enter__(self) -> "SyncOperationLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._create_lock_file()
        except FileExistsError as exc:
            if self._remove_stale_lock():
                try:
                    self._create_lock_file()
                except FileExistsError as retry_exc:
                    raise SyncAlreadyRunning(f"sync operation already running; lock file exists: {self.path}") from retry_exc
            else:
                raise SyncAlreadyRunning(f"sync operation already running; lock file exists: {self.path}") from exc
        return self

    def _create_lock_file(self) -> None:
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise
        payload = {
            "mode": self.mode,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        with os.fdopen(self._fd, "w", encoding="utf-8") as output:
            self._fd = None
            output.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _remove_stale_lock(self) -> bool:
        if self.stale_seconds <= 0:
            return False
        try:
            payload = read_sync_lock_payload(self.path)
            age_seconds = sync_lock_age_seconds(self.path, payload)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if age_seconds <= self.stale_seconds:
            return False
        if sync_lock_owner_is_running(payload):
            return False
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class ProductSource(Protocol):
    def fetch_all(self) -> list[ProductDocument]:
        ...

    def fetch_updated(self, since: str | None = None) -> list[ProductDocument]:
        ...

    def fetch_one(self, product_id: str, mall_id: str | None = None) -> ProductDocument | None:
        ...


PRODUCT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "product_id": (
        "product_id",
        "p_idx",
        "id",
        "product_no",
        "product_code",
        "goods_no",
        "goods_id",
        "item_id",
        "item_no",
        "sku",
        "상품번호",
        "상품코드",
        "상품ID",
        "상품아이디",
        "제품번호",
        "품번",
    ),
    "product_name": (
        "product_name",
        "name",
        "title",
        "product_nm",
        "goods_name",
        "goods_nm",
        "item_name",
        "item_nm",
        "상품명",
        "제품명",
        "품명",
    ),
    "category_name": (
        "category_name",
        "category",
        "category_nm",
        "cat_name",
        "cat_nm",
        "cate_name",
        "cate_nm",
        "분류",
        "카테고리",
        "카테고리명",
    ),
    "price": (
        "price",
        "sell_price",
        "sale_price",
        "consumer_price",
        "product_price",
        "goods_price",
        "판매가",
        "가격",
        "상품가격",
        "단가",
    ),
    "price_min": ("price_min", "min_price", "minimum_price", "lowest_price", "최저가", "최소가격"),
    "price_max": ("price_max", "max_price", "maximum_price", "highest_price", "최고가", "최대가격"),
    "main_image_url": (
        "main_image_url",
        "image_url",
        "image",
        "img_url",
        "main_img",
        "main_img_url",
        "thumbnail_url",
        "대표이미지",
        "대표이미지URL",
        "이미지",
        "이미지URL",
        "썸네일",
    ),
    "product_url": (
        "product_url",
        "url",
        "detail_url",
        "product_detail_url",
        "product_link",
        "item_url",
        "link",
        "상품URL",
        "상품상세URL",
        "상세URL",
        "상품링크",
    ),
    "status": ("status", "state", "product_status", "goods_status", "상품상태", "상태"),
    "updated_at": (
        "updated_at",
        "updated",
        "update_dt",
        "updated_dt",
        "mod_dt",
        "modified_at",
        "modified_dt",
        "last_modified_at",
        "수정일",
        "수정일시",
        "변경일시",
        "최종수정일",
    ),
    "is_deleted": ("is_deleted", "deleted", "delete_yn", "del_yn", "is_del", "삭제여부"),
    "display_yn": (
        "display_yn",
        "display",
        "show_yn",
        "view_yn",
        "use_yn",
        "visible_yn",
        "노출여부",
        "전시여부",
        "표시여부",
        "사용여부",
    ),
    "mall_id": (
        "mall_id",
        "site_id",
        "mall",
        "mall_no",
        "mall_code",
        "site",
        "site_code",
        "shop_id",
        "shop_no",
        "shop_code",
        "가맹점ID",
        "가맹점아이디",
        "가맹점",
        "사이트ID",
        "사이트아이디",
        "사이트",
        "몰ID",
        "몰아이디",
        "쇼핑몰ID",
        "쇼핑몰아이디",
    ),
    "description": ("description", "desc", "product_description", "goods_description", "설명", "상품설명", "제품설명"),
    "keywords": ("keywords", "tags", "search_keywords", "검색어", "키워드", "태그"),
    "print_methods": (
        "print_methods",
        "print_method",
        "printing_methods",
        "printing",
        "print_type",
        "인쇄방법",
        "인쇄",
        "프린팅",
    ),
    "materials": ("materials", "material", "material_name", "소재", "재질"),
    "colors": ("colors", "color", "color_name", "색상", "컬러"),
    "min_order_qty": (
        "min_order_qty",
        "minimum_order_qty",
        "moq",
        "min_qty",
        "minimum_qty",
        "최소주문수량",
        "최소수량",
        "최소주문",
    ),
    "delivery_days": (
        "delivery_days",
        "lead_time_days",
        "delivery_lead_days",
        "lead_time",
        "delivery_period",
        "납기",
        "납기일",
        "납기일수",
        "배송일",
        "배송기간",
    ),
    "product_group_id": (
        "product_group_id",
        "group_id",
        "group_code",
        "group_no",
        "parent_product_id",
        "상품그룹ID",
        "그룹ID",
        "그룹코드",
    ),
    "image_tags": ("image_tags", "image_tag", "이미지태그"),
    "image_hash": ("image_hash", "image_checksum", "이미지해시"),
}


class CsvProductSource:
    def __init__(self, path: Path):
        self.path = path

    def fetch_all(self) -> list[ProductDocument]:
        if not self.path.exists():
            raise FileNotFoundError(f"product CSV does not exist: {self.path}")
        with self.path.open("r", encoding="utf-8-sig", newline="") as source:
            return [row_to_product(row) for row in csv.DictReader(source)]

    def fetch_updated(self, since: str | None = None) -> list[ProductDocument]:
        if since is None:
            return self.fetch_all()
        since_datetime = parse_sync_datetime(since, "since")
        products = []
        for product in self.fetch_all():
            updated_at = parse_sync_datetime(product.updated_at, f"updated_at for product {product.product_id}")
            if updated_at >= since_datetime:
                products.append(product)
        return products

    def fetch_one(self, product_id: str, mall_id: str | None = None) -> ProductDocument | None:
        normalized_mall_id = normalize_mall_id(mall_id)
        return unique_source_product(
            [
                product
                for product in self.fetch_all()
                if product.product_id == product_id
                and (normalized_mall_id is None or product.mall_id == normalized_mall_id)
            ],
            product_id,
            normalized_mall_id,
        )


class MssqlProductSource:
    def __init__(
        self,
        connection_string: str,
        query: str,
        product_id_column: str = "product_id",
        updated_at_column: str = "updated_at",
        fetch_size: int = 1000,
    ):
        self.connection_string = connection_string
        validate_readonly_query(query)
        self.query = clean_readonly_query(query)
        self.product_id_column = validate_sql_identifier_value(
            product_id_column,
            "HAEORUM_MSSQL_PRODUCT_ID_COLUMN",
        )
        self.updated_at_column = validate_sql_identifier_value(
            updated_at_column,
            "HAEORUM_MSSQL_UPDATED_AT_COLUMN",
        )
        self.fetch_size = max(1, int(fetch_size))
        self.last_fetch_stats: dict[str, Any] = {
            "fetch_size": self.fetch_size,
            "fetch_batches": 0,
            "max_fetch_batch_rows": 0,
            "rows_read": 0,
            "batched_fetch": True,
        }

    def fetch_all(self) -> list[ProductDocument]:
        return self._fetch(self.query)

    def fetch_updated(self, since: str | None = None) -> list[ProductDocument]:
        if since is None:
            return self.fetch_all()
        since_param = mssql_sync_datetime_param(since, "since")
        query = build_wrapped_mssql_query(self.query, filters=[f"{self.updated_at_column} >= ?"])
        return self._fetch(query, [since_param])

    def fetch_one(self, product_id: str, mall_id: str | None = None) -> ProductDocument | None:
        normalized_mall_id = normalize_mall_id(mall_id)
        query = build_wrapped_mssql_query(self.query, filters=[f"{self.product_id_column} = ?"])
        rows = self._fetch(query, [product_id])
        if normalized_mall_id is not None:
            rows = [product for product in rows if product.mall_id == normalized_mall_id]
        return unique_source_product(rows, product_id, normalized_mall_id)

    def _fetch(self, query: str, params: list[Any] | None = None) -> list[ProductDocument]:
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError("pyodbc is required for MSSQL sync") from exc
        with pyodbc.connect(self.connection_string, readonly=True, autocommit=True) as connection:
            cursor = connection.cursor()
            cursor.execute(query, params or [])
            columns = [column[0] for column in cursor.description]
            products: list[ProductDocument] = []
            fetch_batches = 0
            max_fetch_batch_rows = 0
            rows_read = 0
            while True:
                rows = cursor.fetchmany(self.fetch_size)
                if not rows:
                    break
                fetch_batches += 1
                max_fetch_batch_rows = max(max_fetch_batch_rows, len(rows))
                rows_read += len(rows)
                products.extend(row_to_product(dict(zip(columns, row))) for row in rows)
            self.last_fetch_stats = {
                "fetch_size": self.fetch_size,
                "fetch_batches": fetch_batches,
                "max_fetch_batch_rows": max_fetch_batch_rows,
                "rows_read": rows_read,
                "batched_fetch": True,
            }
            return products


def unique_source_product(
    products: list[ProductDocument],
    product_id: str,
    mall_id: str | None = None,
) -> ProductDocument | None:
    if not products:
        return None
    if len(products) > 1:
        scope = f" in mall_id {mall_id}" if mall_id else ""
        raise ValueError(f"multiple source products found for product_id {product_id}{scope}: {len(products)}")
    return products[0]


class SyncService:
    def __init__(
        self,
        engine: SearchEngine,
        source: ProductSource,
        settings: Settings,
        logger: "SyncLogger | None" = None,
        image_probe: ProductImageProbe | None = None,
        notifier: "SyncFailureNotifier | None" = None,
        search_cache: SearchCache | None = None,
    ):
        self.engine = engine
        self.source = source
        self.settings = settings
        self.logger = logger or SyncLogger(settings.sync_log_path)
        self.search_cache = search_cache
        self.notifier = notifier or SyncFailureNotifier(
            settings.sync_alert_webhook_url,
            timeout_seconds=settings.sync_alert_timeout_seconds,
            logger=self.logger,
        )
        self.image_probe = image_probe or ProductImageProbe(
            max_bytes=settings.max_image_mb * 1024 * 1024,
            timeout_seconds=settings.product_image_probe_timeout_seconds,
            retry_count=settings.product_image_probe_retry_count,
            retry_delay_seconds=settings.product_image_probe_retry_delay_seconds,
            min_dimension=settings.min_image_dimension,
        )
        self.lock_path = sync_lock_path(settings.sync_log_path)
        self._status = SyncStatus(engine=engine.name, index=settings.index_name)

    @property
    def status(self) -> SyncStatus:
        return self._status

    def current_status(self) -> SyncStatus:
        latest_result = self.logger.latest_result()
        if latest_result is None:
            return self._status
        if self._status.last_started_at and not self._status.last_finished_at:
            return self._status
        if not self._status.last_finished_at:
            return latest_result.status
        if status_finished_at(latest_result.status) > status_finished_at(self._status):
            return latest_result.status
        return self._status

    def sync_changed(self, since: str | None = None) -> SyncResult:
        return self._run("sync", lambda: self.source.fetch_updated(since))

    def reindex_all(self) -> SyncResult:
        return self._run("reindex", self.source.fetch_all)

    def reindex_product(self, product_id: str, mall_id: str | None = None) -> SyncResult:
        normalized_mall_id = normalize_mall_id(mall_id)
        mode = sync_product_mode("reindex", product_id, normalized_mall_id)
        started = time.perf_counter()
        if normalized_mall_id is None and product_operation_requires_mall_id(self.settings):
            return self._missing_product_mall_id_result(mode, started, product_id, "reindex_product")
        try:
            with self.acquire_sync_lock(mode):
                self._mark_started(mode)
                try:
                    product = self.source.fetch_one(product_id, normalized_mall_id)
                except Exception as exc:
                    self.logger.write_sync_failure(mode, "fetch_product", str(exc), product_id=product_id)
                    self._mark_failed(exc)
                    result = self._result(mode, started)
                    self._finish_result(result)
                    return result
                if product is None:
                    return self._delete_product_unlocked(
                        product_id,
                        normalized_mall_id,
                        mode,
                        started,
                        reason="source_product_missing",
                    )
                return self._run_unlocked(mode, [product], started)
        except SyncAlreadyRunning as exc:
            return self._busy_result(mode, started, str(exc))

    def delete_product(self, product_id: str, mall_id: str | None = None) -> SyncResult:
        normalized_mall_id = normalize_mall_id(mall_id)
        mode = sync_product_mode("delete", product_id, normalized_mall_id)
        started = time.perf_counter()
        if normalized_mall_id is None and product_operation_requires_mall_id(self.settings):
            return self._missing_product_mall_id_result(mode, started, product_id, "delete_product")
        try:
            with self.acquire_sync_lock(mode):
                return self._delete_product_unlocked(product_id, normalized_mall_id, mode, started)
        except SyncAlreadyRunning as exc:
            return self._busy_result(mode, started, str(exc))

    def _missing_product_mall_id_result(
        self,
        mode: str,
        started: float,
        product_id: str,
        action: str,
    ) -> SyncResult:
        message = "mall_id is required for product-level reindex/delete in multi-mall deployments"
        self._mark_started(mode)
        self.logger.write_sync_failure(mode, action, message, product_id=product_id)
        self._status.last_finished_at = datetime.now(timezone.utc).isoformat()
        self._status.failed = 1
        self._status.last_error = message
        result = self._result(mode, started)
        self._finish_result(result)
        return result

    def _delete_product_unlocked(
        self,
        product_id: str,
        mall_id: str | None,
        mode: str,
        started: float,
        reason: str = "manual_admin_delete",
    ) -> SyncResult:
        self._mark_started(mode)
        try:
            delete_ids = product_delete_document_ids(mall_id, product_id) if mall_id else [product_id]
            result = self.engine.delete_products(delete_ids)
            failures = product_failures_from_result(result, delete_ids, "delete_from_index")
            for failure in failures:
                self.logger.write_product_event(
                    mode,
                    failure["product_id"],
                    action="delete_from_index",
                    outcome="failed",
                    reason=failure["reason"],
                    details={"document_id": failure.get("document_id")} if failure.get("document_id") else None,
                )
            if not failures:
                self.logger.write_product_event(
                    mode,
                    product_id,
                    action="delete_from_index",
                    outcome="requested",
                    reason=reason,
                    details={"document_ids": delete_ids} if delete_ids != [product_id] else None,
                )
            logical_deleted = 1 if int(result.get("deleted", 0) or 0) > 0 else 0
            self._mark_finished(deleted=logical_deleted, failed=len(failures))
        except Exception as exc:
            self.logger.write_sync_failure(mode, "delete_from_index", str(exc), product_id=product_id)
            self._mark_failed(exc)
        result = self._result(mode, started)
        self._finish_result(result)
        return result

    def _run(self, mode: str, products: Iterable[ProductDocument] | Callable[[], Iterable[ProductDocument]]) -> SyncResult:
        started = time.perf_counter()
        try:
            with self.acquire_sync_lock(mode):
                return self._run_unlocked(mode, products, started)
        except SyncAlreadyRunning as exc:
            return self._busy_result(mode, started, str(exc))

    def acquire_sync_lock(self, mode: str) -> SyncOperationLock:
        return SyncOperationLock(self.lock_path, mode, stale_seconds=self.settings.sync_lock_stale_seconds)

    def _run_unlocked(
        self,
        mode: str,
        products: Iterable[ProductDocument] | Callable[[], Iterable[ProductDocument]],
        started: float,
    ) -> SyncResult:
        self._mark_started(mode)
        try:
            try:
                product_iterable = products() if callable(products) else products
            except Exception as exc:
                self.logger.write_sync_failure(mode, "fetch_products", str(exc))
                self._mark_failed(exc)
                result = self._result(mode, started)
                self._finish_result(result)
                return result

            product_list = product_iterable if isinstance(product_iterable, list) else list(product_iterable)
            product_key_counts = Counter(
                product_identity_key(product.mall_id, product.product_id)
                for product in product_list
                if product.product_id
            )
            duplicate_product_keys = {
                key for key, count in product_key_counts.items() if key[1] and count > 1
            }
            active_product_ids: list[str] = []
            inactive_ids = []
            inactive_reasons: dict[str, list[str]] = {}
            inactive_products: dict[str, ProductDocument] = {}
            failed = 0

            def iter_active_products() -> Iterable[ProductDocument]:
                nonlocal failed
                for product in product_list:
                    product_key = product_identity_key(product.mall_id, product.product_id)
                    if product_key in duplicate_product_keys:
                        failed += 1
                        self.logger.write_product_event(
                            mode,
                            product.product_id,
                            action="validate_source",
                            outcome="failed",
                            reason="duplicate_product_id",
                            product=product,
                            details={
                                "duplicate_count": product_key_counts[product_key],
                                "identity": product_identity_label(*product_key),
                            },
                        )
                        continue
                    if product.active:
                        if self.settings.validate_product_images:
                            probe_result = self.image_probe.validate(product)
                            if not probe_result.ok:
                                failed += 1
                                for document_id in product_delete_document_ids(product.mall_id, product.product_id):
                                    inactive_ids.append(document_id)
                                    inactive_reasons[document_id] = ["image_validation_failed"]
                                    inactive_products[document_id] = product
                                self.logger.write_image_failure(probe_result)
                                self.logger.write_product_event(
                                    mode,
                                    product.product_id,
                                    action="validate_image",
                                    outcome="failed",
                                    reason=probe_result.message or "image_validation_failed",
                                    product=product,
                                    details={"image_url": product.image_url, "attempts": probe_result.attempts},
                                )
                                continue
                            if probe_result.warnings:
                                self.logger.write_image_warning(probe_result)
                        active_product_ids.append(product.product_id)
                        yield product
                    else:
                        for document_id in product_delete_document_ids(product.mall_id, product.product_id):
                            inactive_ids.append(document_id)
                            inactive_reasons[document_id] = inactive_product_reasons(product)
                            inactive_products[document_id] = product

            upsert_result = self.engine.upsert_products(iter_active_products())
            indexed = int(upsert_result.get("indexed", len(active_product_ids)))
            upsert_failures = product_failures_from_result(
                upsert_result,
                active_product_ids,
                "upsert_to_index",
            )
            for failure in upsert_failures:
                self.logger.write_product_event(
                    mode,
                    failure["product_id"],
                    action="upsert_to_index",
                    outcome="failed",
                    reason=failure["reason"],
                )
            failed += max(int(upsert_result.get("failed", 0) or 0), len(upsert_failures))

            inactive_ids = unique_preserving_order(inactive_ids)
            delete_result = self.engine.delete_products(inactive_ids) if inactive_ids else {"deleted": 0}
            deleted = min(int(delete_result.get("deleted", len(inactive_ids)) or 0), len({product_identity_key(product.mall_id, product.product_id) for product in inactive_products.values()}))
            delete_failures = product_failures_from_result(delete_result, inactive_ids, "delete_from_index")
            failed_delete_ids = {failure["product_id"] for failure in delete_failures}
            failed_delete_document_ids = {
                str(failure.get("document_id") or "")
                for failure in delete_failures
                if str(failure.get("document_id") or "")
            }
            for document_id in inactive_ids:
                product = inactive_products.get(document_id)
                public_product_id = product.product_id if product else document_id
                if document_id in failed_delete_document_ids or (
                    public_product_id in failed_delete_ids and public_product_id == document_id
                ):
                    continue
                self.logger.write_product_event(
                    mode,
                    public_product_id,
                    action="delete_from_index",
                    outcome="requested",
                    reason=inactive_reasons.get(document_id, ["inactive_or_deleted"]),
                    product=product,
                    details={"document_id": document_id} if document_id != public_product_id else None,
                )
            for failure in delete_failures:
                failure_document_id = str(failure.get("document_id") or "")
                product = inactive_products.get(failure_document_id) or inactive_products.get(failure["product_id"])
                self.logger.write_product_event(
                    mode,
                    product.product_id if product else failure["product_id"],
                    action="delete_from_index",
                    outcome="failed",
                    reason=failure["reason"],
                    product=product,
                    details={"document_id": failure_document_id} if failure_document_id else None,
                )
            failed += max(int(delete_result.get("failed", 0) or 0), len(delete_failures))
            self._mark_finished(indexed=indexed, deleted=deleted, failed=failed)
        except Exception as exc:
            self.logger.write_sync_failure(mode, "sync_batch", str(exc))
            self._mark_failed(exc)
        result = self._result(mode, started)
        self._finish_result(result)
        return result

    def _busy_result(self, mode: str, started: float, message: str) -> SyncResult:
        now = datetime.now(timezone.utc).isoformat()
        status = SyncStatus(
            last_started_at=now,
            last_finished_at=now,
            last_mode=mode,
            last_error=message,
            failed=1,
            engine=self.engine.name,
            index=self.settings.index_name,
        )
        result = SyncResult(
            mode=mode,
            indexed=0,
            deleted=0,
            failed=1,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            status=status,
        )
        self.logger.write_sync_failure(mode, "acquire_sync_lock", message)
        self._finish_result(result)
        return result

    def _mark_started(self, mode: str) -> None:
        self._status.last_started_at = datetime.now(timezone.utc).isoformat()
        self._status.last_finished_at = None
        self._status.last_mode = mode
        self._status.last_error = None
        self._status.indexed = 0
        self._status.deleted = 0
        self._status.failed = 0

    def _mark_finished(self, indexed: int = 0, deleted: int = 0, failed: int = 0) -> None:
        self._status.last_finished_at = datetime.now(timezone.utc).isoformat()
        self._status.indexed = indexed
        self._status.deleted = deleted
        self._status.failed = failed

    def _mark_failed(self, exc: Exception) -> None:
        self._status.last_finished_at = datetime.now(timezone.utc).isoformat()
        self._status.failed = 1
        self._status.last_error = str(exc)

    def _result(self, mode: str, started: float) -> SyncResult:
        return SyncResult(
            mode=mode,
            indexed=self._status.indexed,
            deleted=self._status.deleted,
            failed=self._status.failed,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            status=self._status,
        )

    def _notify_if_failed(self, result: SyncResult) -> None:
        if result.failed <= 0:
            return
        self.notifier.notify(result)

    def _finish_result(self, result: SyncResult) -> None:
        self.logger.write(result)
        self._clear_search_cache_if_mutated(result)
        self._notify_if_failed(result)

    def _clear_search_cache_if_mutated(self, result: SyncResult) -> None:
        if result.indexed <= 0 and result.deleted <= 0:
            return
        if self.search_cache is None:
            return
        try:
            report = self.search_cache.clear()
        except Exception as exc:
            self.logger.write_cache_invalidation(
                result,
                ok=False,
                report={"error": str(exc)},
            )
            return
        ok = bool(report.get("ok", True)) if isinstance(report, dict) else True
        self.logger.write_cache_invalidation(result, ok=ok, report=report if isinstance(report, dict) else {})


def make_product_source(settings: Settings) -> ProductSource:
    if settings.mssql_connection_string:
        return MssqlProductSource(
            settings.mssql_connection_string,
            settings.mssql_query,
            product_id_column=settings.mssql_product_id_column,
            updated_at_column=settings.mssql_updated_at_column,
            fetch_size=settings.mssql_sync_fetch_size,
        )
    return CsvProductSource(settings.product_csv_path)


def sync_product_mode(action: str, product_id: str, mall_id: str | None = None) -> str:
    if mall_id:
        return f"{action}:{mall_id}:{product_id}"
    return f"{action}:{product_id}"


def product_operation_requires_mall_id(settings: Settings) -> bool:
    return bool(settings.filter_by_mall_id or len(settings.malls) > 1)


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    unique = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            unique.append(text)
            seen.add(text)
    return unique


def sync_lock_path(sync_log_path: Path) -> Path:
    return sync_log_path.with_name(sync_log_path.name + ".lock")


def read_sync_lock_payload(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text.splitlines()[0])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def sync_lock_age_seconds(path: Path, payload: dict[str, Any]) -> float:
    started_at = str(payload.get("started_at") or "").strip()
    if started_at:
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())
        except ValueError:
            pass
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - mtime).total_seconds())


def sync_lock_owner_is_running(payload: dict[str, Any]) -> bool:
    host = str(payload.get("host") or "").strip()
    if host and host != socket.gethostname():
        return False
    try:
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def build_wrapped_mssql_query(
    query: str,
    filters: list[str] | None = None,
    top: int = 0,
    order_by: str | None = None,
) -> str:
    base_query = clean_readonly_query(query)
    cte_prefix = ""
    inner_select = base_query
    if base_query.lower().startswith("with "):
        cte_prefix, inner_select = split_cte_query(base_query)
    top_clause = f"TOP ({top}) " if top > 0 else ""
    prefix = f"{cte_prefix} " if cte_prefix else ""
    sql = f"{prefix}SELECT {top_clause}* FROM ({inner_select}) AS ai_products"
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    if order_by:
        sql += f" ORDER BY {order_by}"
    return sql


def split_cte_query(query: str) -> tuple[str, str]:
    final_select_index = find_top_level_keyword(query, "select", start=4)
    if final_select_index < 0:
        raise ValueError("MSSQL CTE query must include a final SELECT statement")
    return query[:final_select_index].strip(), query[final_select_index:].strip()


def find_top_level_keyword(sql: str, keyword: str, start: int = 0) -> int:
    keyword_lower = keyword.lower()
    depth = 0
    in_single_quote = False
    in_double_quote = False
    in_bracket = False
    index = start
    while index < len(sql):
        char = sql[index]
        if in_single_quote:
            if char == "'" and index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            if char == "'":
                in_single_quote = False
            index += 1
            continue
        if in_double_quote:
            if char == '"':
                in_double_quote = False
            index += 1
            continue
        if in_bracket:
            if char == "]":
                in_bracket = False
            index += 1
            continue
        if char == "'":
            in_single_quote = True
            index += 1
            continue
        if char == '"':
            in_double_quote = True
            index += 1
            continue
        if char == "[":
            in_bracket = True
            index += 1
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0 and sql[index : index + len(keyword)].lower() == keyword_lower:
            before = sql[index - 1] if index > 0 else " "
            after_index = index + len(keyword)
            after = sql[after_index] if after_index < len(sql) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                return index
        index += 1
    return -1


class SyncLogger:
    def __init__(self, path: Path):
        self.path = path

    def write(self, result: SyncResult) -> None:
        self._write_entry(result.model_dump(mode="json"))

    def write_image_failure(self, result: ImageProbeResult) -> None:
        self._write_entry(
            {
                "type": "image_probe_failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "product_id": result.product_id,
                "image_url": result.image_url,
                "message": result.message,
                "attempts": result.attempts,
            }
        )

    def write_image_warning(self, result: ImageProbeResult) -> None:
        self._write_entry(
            {
                "type": "image_quality_warning",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "product_id": result.product_id,
                "image_url": result.image_url,
                "warnings": list(result.warnings),
                "attempts": result.attempts,
            }
        )

    def write_product_event(
        self,
        mode: str,
        product_id: str,
        action: str,
        outcome: str,
        reason: str | list[str],
        product: ProductDocument | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        event_type = "sync_product_failed" if outcome == "failed" else "sync_product_event"
        entry: dict[str, Any] = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "product_id": product_id,
            "action": action,
            "outcome": outcome,
            "reason": reason,
        }
        if product is not None:
            entry.update(
                {
                    "status": product.status,
                    "display_yn": product.display_yn,
                    "is_deleted": product.is_deleted,
                    "mall_id": product.mall_id,
                }
            )
        if details:
            entry["details"] = details
        self._write_entry(entry)

    def write_sync_failure(self, mode: str, action: str, message: str, product_id: str | None = None) -> None:
        entry = {
            "type": "sync_batch_failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "action": action,
            "message": message,
        }
        if product_id:
            entry["product_id"] = product_id
        self._write_entry(entry)

    def write_alert_failure(self, message: str) -> None:
        self._write_entry(
            {
                "type": "sync_alert_failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": message,
            }
        )

    def write_cache_invalidation(self, result: SyncResult, ok: bool, report: dict[str, Any]) -> None:
        self._write_entry(
            {
                "type": "search_cache_cleared" if ok else "search_cache_clear_failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": result.mode,
                "indexed": result.indexed,
                "deleted": result.deleted,
                "failed": result.failed,
                "cache": report,
            }
        )

    def _write_entry(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        safe_entry = sanitize_log_entry(entry)
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(safe_entry, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        return read_jsonl_tail(self.path, limit)

    def latest_result(self) -> SyncResult | None:
        if not self.path.exists():
            return None
        for line in read_reverse_lines(self.path):
            result = self._parse_result_line(line)
            if result is not None:
                return result
        return None

    def latest_successful_result(self, modes: set[str] | None = None) -> SyncResult | None:
        if not self.path.exists():
            return None
        for line in read_reverse_lines(self.path):
            result = self._parse_result_line(line)
            if result is None:
                continue
            if result.failed > 0 or result.status.last_error:
                continue
            if modes and result.mode not in modes:
                continue
            return result
        return None

    def _parse_result_line(self, line: str) -> SyncResult | None:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(entry, dict):
            return None
        if entry.get("type"):
            return None
        if "mode" not in entry or "status" not in entry:
            return None
        try:
            return SyncResult.model_validate(entry)
        except (TypeError, ValueError):
            return None


class SyncFailureNotifier:
    def __init__(self, webhook_url: str | None, timeout_seconds: int = 5, logger: SyncLogger | None = None):
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.logger = logger

    def notify(self, result: SyncResult) -> bool:
        if not self.webhook_url:
            return False
        payload = {
            "type": "sync_failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": result.mode,
            "indexed": result.indexed,
            "deleted": result.deleted,
            "failed": result.failed,
            "elapsed_ms": result.elapsed_ms,
            "status": result.status.model_dump(mode="json"),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response.read()
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if self.logger:
                self.logger.write_alert_failure(str(exc))
            return False


def row_to_product(row: dict[str, Any]) -> ProductDocument:
    product_id = str(product_value(row, "product_id")).strip()
    product_name = str(product_value(row, "product_name")).strip()
    if not product_id:
        raise ValueError("product_id is required")
    if not product_name:
        raise ValueError("product_name is required")
    image_tags = product_value(row, "image_tags", default="")
    extra = {}
    if image_tags:
        extra["image_tags"] = [item.strip() for item in str(image_tags).replace("|", ",").split(",") if item.strip()]
    image_hash = product_value(row, "image_hash", default="")
    if image_hash:
        extra["image_hash"] = str(image_hash)
    return ProductDocument.model_validate(
        {
            "product_id": str(product_id),
            "product_name": product_name,
            "category_name": product_value(row, "category_name"),
            "price": parse_float(product_value(row, "price", default=None)),
            "main_image_url": product_value(row, "main_image_url", default=None),
            "product_url": product_value(row, "product_url", default=None),
            "status": product_value(row, "status", default="active"),
            "updated_at": product_value(row, "updated_at", default=None),
            "is_deleted": parse_bool(product_value(row, "is_deleted", default=False)),
            "display_yn": product_value(row, "display_yn", default="Y"),
            "mall_id": product_value(row, "mall_id", default=None),
            "description": product_value(row, "description", default=None),
            "keywords": product_value(row, "keywords", default=""),
            "print_methods": product_value(row, "print_methods", default=""),
            "materials": product_value(row, "materials", default=""),
            "colors": product_value(row, "colors", default=""),
            "min_order_qty": parse_int(product_value(row, "min_order_qty", default=None)),
            "price_min": parse_float(product_value(row, "price_min", default=None)),
            "price_max": parse_float(product_value(row, "price_max", default=None)),
            "delivery_days": parse_int(product_value(row, "delivery_days", default=None)),
            "product_group_id": product_value(row, "product_group_id", default=None),
            "extra": extra,
        }
    )


def product_value(row: dict[str, Any], field: str, default: Any = "") -> Any:
    return value(row, *PRODUCT_FIELD_ALIASES[field], default=default)


def value(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    lower = {str(key).lower(): row_value for key, row_value in row.items()}
    for name in names:
        row_value = lower.get(name.lower())
        if row_value not in (None, ""):
            return row_value
    normalized = {normalize_external_field_name(key): row_value for key, row_value in row.items()}
    for name in names:
        row_value = normalized.get(normalize_external_field_name(name))
        if row_value not in (None, ""):
            return row_value
    return default


def normalize_external_field_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "_", str(value or "").strip().lower()).strip("_")


def parse_float(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if not text:
        return None
    normalized = re.sub(r"[^\d.\-]", "", text)
    if normalized in {"", "-", ".", "-."}:
        return None
    try:
        parsed = float(normalized)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def parse_int(raw: Any) -> int | None:
    value = parse_float(raw)
    if value is None:
        return None
    return int(value)


def parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on", "예", "네", "참", "삭제", "삭제됨"}


def status_finished_at(status: SyncStatus) -> datetime:
    if not status.last_finished_at:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(status.last_finished_at.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def parse_sync_datetime(value: str | datetime | None, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError(f"{field_name} is required for incremental sync")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def mssql_sync_datetime_param(value: str | datetime | None, field_name: str) -> datetime:
    return parse_sync_datetime(value, field_name).replace(tzinfo=None)


def inactive_product_reasons(product: ProductDocument) -> list[str]:
    reasons = []
    status = str(product.status or "").strip()
    display_yn = str(product.display_yn or "").strip()
    if product.is_deleted:
        reasons.append("is_deleted=true")
    if status and status.lower() in INACTIVE_STATUSES:
        reasons.append(f"status={status}")
    if display_yn and display_yn.lower() in INACTIVE_STATUSES:
        reasons.append(f"display_yn={display_yn}")
    return reasons or ["inactive_or_deleted"]


def product_failures_from_result(result: dict[str, Any], fallback_ids: list[str], action: str) -> list[dict[str, str]]:
    raw_failures = result.get("failed_products") or result.get("failures") or []
    sample_limit = max(int(result.get("failed_product_sample_limit") or SYNC_FAILURE_SAMPLE_LIMIT), 0)
    failures: list[dict[str, str]] = []
    if isinstance(raw_failures, list):
        for index, raw_failure in enumerate(raw_failures[:sample_limit]):
            failure = normalize_product_failure(raw_failure, fallback_ids, index, action)
            if failure:
                failures.append(failure)
    if not failures and int(result.get("failed", 0) or 0) > 0 and fallback_ids:
        fallback_count = min(int(result.get("failed", 0) or 0), sample_limit)
        for product_id in fallback_ids[:fallback_count]:
            failures.append({"product_id": product_id, "reason": f"{action}_failed"})
    return failures


def normalize_product_failure(
    raw_failure: Any,
    fallback_ids: list[str],
    index: int,
    action: str,
) -> dict[str, str] | None:
    fallback_id = fallback_ids[index] if index < len(fallback_ids) else None
    if isinstance(raw_failure, str):
        return {"product_id": raw_failure or fallback_id or "", "reason": f"{action}_failed"}
    if not isinstance(raw_failure, dict):
        if fallback_id:
            return {"product_id": fallback_id, "reason": f"{action}_failed"}
        return None
    raw_id = str(raw_failure.get("_id") or raw_failure.get("document_id") or raw_failure.get("id") or fallback_id or "")
    product_id = str(raw_failure.get("product_id") or public_product_id_from_document_id(raw_id))
    if not product_id:
        return None
    reason = str(
        raw_failure.get("reason")
        or raw_failure.get("message")
        or raw_failure.get("error")
        or raw_failure.get("detail")
        or f"{action}_failed"
    )
    failure = {"product_id": product_id, "reason": reason}
    if raw_id and raw_id != product_id:
        failure["document_id"] = raw_id
    return failure
