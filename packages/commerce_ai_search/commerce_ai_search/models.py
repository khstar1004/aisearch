from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .identifiers import MAX_MALL_ID_LENGTH, MALL_ID_PATTERN_TEXT, normalize_mall_id
from .url_safety import product_url_contains_product_id, safe_absolute_http_url


ACTIVE_STATUSES = {
    "active",
    "sale",
    "selling",
    "display",
    "displayed",
    "y",
    "yes",
    "1",
    "true",
    "on",
    "판매중",
    "노출",
    "정상",
    "승인",
    "사용",
    "활성",
    "예",
    "네",
}
INACTIVE_STATUSES = {
    "inactive",
    "deleted",
    "hidden",
    "soldout",
    "sold_out",
    "n",
    "no",
    "0",
    "false",
    "off",
    "비노출",
    "삭제",
    "승인대기",
    "승인보류",
    "관리비미납",
    "일시품절",
    "가맹점상품",
    "가맹점삭제",
    "알수없음",
    "품절",
    "중지",
    "숨김",
    "미사용",
    "비활성",
    "아니오",
    "아니요",
    "판매중지",
    "단종",
}
MAX_PRODUCT_ID_LENGTH = 100
MAX_QUERY_TEXT_LENGTH = 200
MAX_CATEGORY_LENGTH = 100
MAX_ATTRIBUTE_FILTER_LENGTH = 100
MAX_PRODUCT_URL_LENGTH = 1000
MALFORMED_QUERY_MESSAGE = "q appears to be malformed or incorrectly encoded; send UTF-8 text"


def preferred_mall_id_alias(data: dict[str, Any]) -> str | None:
    mall_id = normalized_optional_string(data.get("mall_id"))
    site_id = normalized_optional_string(data.get("site_id"))
    if mall_id and site_id and mall_id != site_id:
        raise ValueError("mall_id and site_id must match when both are provided")
    return mall_id or site_id


def normalized_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def looks_like_malformed_query_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "\ufffd" in text:
        return True
    compact = "".join(char for char in text if not char.isspace())
    if not compact:
        return False
    question_marks = compact.count("?")
    if question_marks >= 2 and question_marks / len(compact) >= 0.5:
        return True
    return False


class QueryType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    TEXT_IMAGE = "text_image"


class ProductDocument(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    product_id: str = Field(max_length=MAX_PRODUCT_ID_LENGTH)
    name: str = Field(alias="product_name")
    category: str = Field(default="", alias="category_name", max_length=MAX_CATEGORY_LENGTH)
    price: float | None = None
    image_url: str | None = Field(default=None, alias="main_image_url", max_length=MAX_PRODUCT_URL_LENGTH)
    product_url: str | None = Field(default=None, max_length=MAX_PRODUCT_URL_LENGTH)
    status: str = "active"
    updated_at: str | datetime | None = None
    is_deleted: bool = False
    display_yn: str | None = None
    mall_id: str | None = Field(default=None, max_length=MAX_MALL_ID_LENGTH, pattern=MALL_ID_PATTERN_TEXT)
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    print_methods: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    min_order_qty: int | None = Field(default=None, ge=0)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    delivery_days: int | None = Field(default=None, ge=0)
    product_group_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("product_id", "name", "category", "image_url", "product_url", "status", "display_yn", "mall_id", mode="before")
    @classmethod
    def strip_source_strings(cls, value: Any, info) -> str | None:
        if value is None:
            if info.field_name in {"product_id", "name"}:
                raise ValueError(f"{info.field_name} is required")
            return None
        stripped = str(value).strip()
        if not stripped and info.field_name in {"product_id", "name"}:
            raise ValueError(f"{info.field_name} must not be blank")
        if info.field_name == "mall_id":
            return normalize_mall_id(stripped)
        if info.field_name in {"image_url", "product_url", "display_yn"}:
            return stripped or None
        return stripped

    @field_validator("keywords", "print_methods", "materials", "colors", mode="before")
    @classmethod
    def parse_text_list(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).replace("|", ",").replace(";", ",").split(",") if item.strip()]

    @property
    def active(self) -> bool:
        status = str(self.status or "").strip().lower()
        display_yn = str(self.display_yn or "Y").strip().lower()
        if self.is_deleted:
            return False
        if display_yn in INACTIVE_STATUSES:
            return False
        if status in INACTIVE_STATUSES:
            return False
        return status in ACTIVE_STATUSES or status == ""

    @model_validator(mode="after")
    def validate_product_ranges(self) -> "ProductDocument":
        for field_name in ["price", "price_min", "price_max"]:
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            raise ValueError("price_min cannot be greater than price_max")
        return self

    def text_blob(self) -> str:
        return " ".join(
            part
            for part in [
                self.name,
                self.category,
                self.description or "",
                " ".join(self.keywords),
                " ".join(self.print_methods),
                " ".join(self.materials),
                " ".join(self.colors),
                f"최소주문수량 {self.min_order_qty}" if self.min_order_qty else "",
                f"납기 {self.delivery_days}일" if self.delivery_days else "",
            ]
            if part
        )


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mall_id: str | None = Field(
        default=None,
        max_length=MAX_MALL_ID_LENGTH,
        pattern=MALL_ID_PATTERN_TEXT,
        validation_alias=AliasChoices("mall_id", "site_id"),
    )
    q: str | None = Field(default=None, max_length=MAX_QUERY_TEXT_LENGTH)
    image_base64: str | None = None
    limit: int = 20
    offset: int = 0
    category: str | None = Field(default=None, max_length=MAX_CATEGORY_LENGTH)
    print_method: str | None = Field(default=None, max_length=MAX_ATTRIBUTE_FILTER_LENGTH)
    material: str | None = Field(default=None, max_length=MAX_ATTRIBUTE_FILTER_LENGTH)
    color: str | None = Field(default=None, max_length=MAX_ATTRIBUTE_FILTER_LENGTH)
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    quantity: int | None = Field(default=None, ge=1, validation_alias=AliasChoices("quantity", "order_qty"))
    max_delivery_days: int | None = Field(default=None, ge=0)
    text_weight: float | None = None
    image_weight: float | None = None

    @model_validator(mode="before")
    @classmethod
    def prefer_nonblank_site_id_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            values = dict(data)
            mall_id = preferred_mall_id_alias(values)
            if mall_id:
                values["mall_id"] = mall_id
            values.pop("site_id", None)
            return values
        return data

    @field_validator("q", "category", "print_method", "material", "color", mode="before")
    @classmethod
    def strip_optional_string(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        if info.field_name == "q" and looks_like_malformed_query_text(value):
            raise ValueError(MALFORMED_QUERY_MESSAGE)
        return value or None

    @field_validator("mall_id", mode="before")
    @classmethod
    def validate_search_mall_id(cls, value: str | None) -> str | None:
        return normalize_mall_id(value)

    @model_validator(mode="after")
    def validate_query(self) -> "SearchRequest":
        if not (self.q and self.q.strip()) and not self.image_base64:
            raise ValueError("q or image_base64 is required")
        if self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.offset < 0:
            raise ValueError("offset must be at least 0")
        for field_name in ["min_price", "max_price", "text_weight", "image_weight"]:
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")
        if self.text_weight is not None and self.text_weight < 0:
            raise ValueError("text_weight must be non-negative")
        if self.image_weight is not None and self.image_weight < 0:
            raise ValueError("image_weight must be non-negative")
        if self.text_weight == 0 and self.image_weight == 0:
            raise ValueError("text_weight and image_weight cannot both be zero")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot be greater than max_price")
        return self

    @property
    def query_type(self) -> QueryType:
        has_text = bool(self.q and self.q.strip())
        has_image = bool(self.image_base64)
        if has_text and has_image:
            return QueryType.TEXT_IMAGE
        if has_image:
            return QueryType.IMAGE
        return QueryType.TEXT


class SearchResultItem(BaseModel):
    product_id: str = Field(max_length=MAX_PRODUCT_ID_LENGTH)
    name: str
    category: str = Field(max_length=MAX_CATEGORY_LENGTH)
    price: float | None = Field(default=None, ge=0)
    image_url: str | None = Field(default=None, max_length=MAX_PRODUCT_URL_LENGTH)
    product_url: str = Field(max_length=MAX_PRODUCT_URL_LENGTH)
    score: float
    score_percent: float
    mall_id: str | None = Field(default=None, max_length=MAX_MALL_ID_LENGTH, pattern=MALL_ID_PATTERN_TEXT)
    source_scores: dict[str, float] = Field(default_factory=dict)

    @field_validator("product_id", "name", "category", "mall_id", mode="before")
    @classmethod
    def strip_result_strings(cls, value: Any, info) -> str | None:
        if value is None:
            if info.field_name in {"product_id", "name"}:
                raise ValueError(f"{info.field_name} is required")
            return None
        stripped = str(value).strip()
        if not stripped and info.field_name in {"product_id", "name"}:
            raise ValueError(f"{info.field_name} must not be blank")
        if info.field_name == "mall_id":
            return normalize_mall_id(stripped)
        if info.field_name == "category":
            return stripped
        return stripped or None

    @field_validator("image_url", "product_url", mode="before")
    @classmethod
    def strip_result_urls(cls, value: Any) -> str | None:
        return normalized_optional_string(value)

    @model_validator(mode="after")
    def validate_scores(self) -> "SearchResultItem":
        for field_name in ["price", "score", "score_percent"]:
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")
        for field_name in ["image_url", "product_url"]:
            value = getattr(self, field_name)
            if value is not None and safe_absolute_http_url(value) is None:
                raise ValueError(f"{field_name} must be an absolute safe public http(s) URL")
        if not product_url_contains_product_id(self.product_url, self.product_id):
            raise ValueError("product_url must contain product_id")
        if not 0 <= self.score <= 1:
            raise ValueError("score must be between 0 and 1")
        if not 0 <= self.score_percent <= 100:
            raise ValueError("score_percent must be between 0 and 100")
        for source, value in self.source_scores.items():
            if not math.isfinite(float(value)):
                raise ValueError(f"source_scores.{source} must be finite")
        return self


class SearchMeta(BaseModel):
    query_type: QueryType
    elapsed_ms: float = Field(ge=0)
    engine: str
    embedding_backend: str | None = None
    limit: int = Field(ge=1)
    offset: int = Field(default=0, ge=0)
    has_more: bool = False
    next_offset: int | None = Field(default=None, ge=0)
    mall_id: str | None = Field(default=None, max_length=MAX_MALL_ID_LENGTH, pattern=MALL_ID_PATTERN_TEXT)

    @field_validator("mall_id", mode="before")
    @classmethod
    def validate_meta_mall_id(cls, value: str | None) -> str | None:
        return normalize_mall_id(value)
    text_weight: float | None = Field(default=None, ge=0)
    image_weight: float | None = Field(default=None, ge=0)
    low_confidence: bool = False
    notice: str | None = None

    @model_validator(mode="after")
    def validate_meta_numbers(self) -> "SearchMeta":
        for field_name in ["elapsed_ms", "text_weight", "image_weight"]:
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")
        if self.has_more:
            if self.next_offset is None:
                raise ValueError("next_offset is required when has_more is true")
            if self.next_offset <= self.offset:
                raise ValueError("next_offset must be greater than offset when has_more is true")
        elif self.next_offset is not None:
            raise ValueError("next_offset must be null when has_more is false")
        if self.query_type == QueryType.TEXT:
            if self.text_weight is None:
                raise ValueError("text query meta requires text_weight")
            if self.image_weight is not None:
                raise ValueError("text query meta image_weight must be null")
        elif self.query_type == QueryType.IMAGE:
            if self.text_weight is not None:
                raise ValueError("image query meta text_weight must be null")
            if self.image_weight is None:
                raise ValueError("image query meta requires image_weight")
        elif self.query_type == QueryType.TEXT_IMAGE:
            if self.text_weight is None:
                raise ValueError("mixed query meta requires text_weight")
            if self.image_weight is None:
                raise ValueError("mixed query meta requires image_weight")
            if self.text_weight == 0 and self.image_weight == 0:
                raise ValueError("mixed query meta weights cannot both be zero")
        return self


class SearchResponse(BaseModel):
    top: list[SearchResultItem]
    items: list[SearchResultItem]
    suggested_categories: list[str]
    meta: SearchMeta

    @model_validator(mode="after")
    def validate_response_limits(self) -> "SearchResponse":
        if len(self.top) > 3:
            raise ValueError("top must contain at most 3 products")
        if len(self.items) > self.meta.limit:
            raise ValueError("items must not exceed meta.limit")
        if self.meta.has_more and self.meta.next_offset != self.meta.offset + len(self.items):
            raise ValueError("next_offset must equal offset plus item count when has_more is true")
        if len(self.suggested_categories) > 15:
            raise ValueError("suggested_categories must contain at most 15 categories")
        top_ids = [item.product_id for item in self.top]
        item_ids = [item.product_id for item in self.items]
        if len(set(top_ids)) != len(top_ids):
            raise ValueError("top product_ids must be unique")
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("items product_ids must be unique")
        if set(top_ids).intersection(item_ids):
            raise ValueError("items must not repeat top product_ids")
        if len(set(self.suggested_categories)) != len(self.suggested_categories):
            raise ValueError("suggested_categories must be unique")
        return self


class ClickLogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mall_id: str = Field(
        max_length=MAX_MALL_ID_LENGTH,
        pattern=MALL_ID_PATTERN_TEXT,
        validation_alias=AliasChoices("mall_id", "site_id"),
    )
    product_id: str = Field(max_length=MAX_PRODUCT_ID_LENGTH)
    product_url: str | None = Field(
        default=None,
        max_length=MAX_PRODUCT_URL_LENGTH,
        description="클릭된 상품 상세 URL. 운영 로그에 저장되므로 절대 HTTP(S) URL만 허용하고 URL 사용자 정보와 mall 템플릿 외부 도메인은 허용하지 않습니다.",
    )
    position: int | None = None
    query: str | None = Field(default=None, max_length=MAX_QUERY_TEXT_LENGTH)
    query_type: QueryType | None = None
    score_percent: float | None = None

    @model_validator(mode="before")
    @classmethod
    def prefer_nonblank_site_id_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            values = dict(data)
            mall_id = preferred_mall_id_alias(values)
            if mall_id:
                values["mall_id"] = mall_id
            values.pop("site_id", None)
            return values
        return data

    @field_validator("mall_id", "product_id", "product_url", "query", mode="before")
    @classmethod
    def strip_click_strings(cls, value: str | None, info) -> str | None:
        value = str(value or "").strip()
        if not value and info.field_name in {"mall_id", "product_id"}:
            raise ValueError("field is required")
        if not value:
            return None
        if info.field_name == "mall_id":
            return normalize_mall_id(value, required=True)
        return value

    @field_validator("position")
    @classmethod
    def valid_position(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("position must be at least 1")
        return value

    @field_validator("product_url")
    @classmethod
    def valid_product_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if safe_absolute_http_url(value) is None:
            raise ValueError("product_url must be an absolute http(s) URL")
        return value

    @field_validator("score_percent")
    @classmethod
    def valid_score_percent(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(float(value)):
            raise ValueError("score_percent must be finite")
        if value is not None and not 0 <= value <= 100:
            raise ValueError("score_percent must be between 0 and 100")
        return value

    @model_validator(mode="after")
    def validate_product_url_matches_product_id(self) -> "ClickLogRequest":
        if self.product_url and not product_url_contains_product_id(self.product_url, self.product_id):
            raise ValueError("product_url must contain product_id")
        return self


class AdminProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(max_length=MAX_PRODUCT_ID_LENGTH)
    mall_id: str | None = Field(default=None, max_length=MAX_MALL_ID_LENGTH, pattern=MALL_ID_PATTERN_TEXT)

    @field_validator("product_id", "mall_id", mode="before")
    @classmethod
    def strip_admin_product_fields(cls, value: Any, info) -> str | None:
        stripped = str(value or "").strip()
        if info.field_name == "mall_id":
            return normalize_mall_id(stripped)
        if not stripped:
            raise ValueError("product_id is required")
        return stripped


class SyncStatus(BaseModel):
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_mode: str | None = None
    last_error: str | None = None
    indexed: int = 0
    deleted: int = 0
    failed: int = 0
    engine: str
    index: str


class SyncResult(BaseModel):
    mode: str
    indexed: int
    deleted: int
    failed: int
    elapsed_ms: float
    status: SyncStatus
