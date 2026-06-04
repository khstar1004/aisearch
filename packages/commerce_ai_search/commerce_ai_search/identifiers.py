from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, unquote


MAX_MALL_ID_LENGTH = 64
MALL_ID_PATTERN_TEXT = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?$"
MALL_ID_PATTERN = re.compile(MALL_ID_PATTERN_TEXT)
MALL_ID_REQUIREMENT = "letters, numbers, and hyphens, starting and ending with a letter or number"
DOCUMENT_ID_MALL_PREFIX = "mall-"
DOCUMENT_ID_PRODUCT_SEPARATOR = "-product-"


def normalize_mall_id(value: Any, *, field_name: str = "mall_id", required: bool = False) -> str | None:
    mall_id = str(value or "").strip()
    if not mall_id:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    if len(mall_id) > MAX_MALL_ID_LENGTH:
        raise ValueError(f"{field_name} must be at most {MAX_MALL_ID_LENGTH} characters")
    if not MALL_ID_PATTERN.fullmatch(mall_id):
        raise ValueError(f"{field_name} must contain only {MALL_ID_REQUIREMENT}")
    return mall_id


def product_identity_key(mall_id: Any, product_id: Any) -> tuple[str, str]:
    return str(mall_id or "").strip(), str(product_id or "").strip()


def product_identity_label(mall_id: Any, product_id: Any) -> str:
    normalized_mall_id, normalized_product_id = product_identity_key(mall_id, product_id)
    if normalized_mall_id:
        return f"{normalized_mall_id}:{normalized_product_id}"
    return normalized_product_id


def product_document_id(mall_id: Any, product_id: Any) -> str:
    normalized_mall_id, normalized_product_id = product_identity_key(mall_id, product_id)
    if not normalized_product_id:
        raise ValueError("product_id is required")
    if not normalized_mall_id:
        return normalized_product_id
    return (
        f"{DOCUMENT_ID_MALL_PREFIX}{quote(normalized_mall_id, safe='')}"
        f"{DOCUMENT_ID_PRODUCT_SEPARATOR}{quote(normalized_product_id, safe='')}"
    )


def legacy_product_document_id(mall_id: Any, product_id: Any) -> str | None:
    normalized_mall_id, normalized_product_id = product_identity_key(mall_id, product_id)
    if not normalized_mall_id or not normalized_product_id:
        return None
    document_id = product_document_id(normalized_mall_id, normalized_product_id)
    if document_id == normalized_product_id:
        return None
    return normalized_product_id


def product_delete_document_ids(mall_id: Any, product_id: Any) -> list[str]:
    document_id = product_document_id(mall_id, product_id)
    ids = [document_id]
    legacy_id = legacy_product_document_id(mall_id, product_id)
    if legacy_id and legacy_id not in ids:
        ids.append(legacy_id)
    return ids


def public_product_id_from_document_id(document_id: Any) -> str:
    text = str(document_id or "").strip()
    if not text.startswith(DOCUMENT_ID_MALL_PREFIX) or DOCUMENT_ID_PRODUCT_SEPARATOR not in text:
        return text
    _, encoded_product_id = text.rsplit(DOCUMENT_ID_PRODUCT_SEPARATOR, 1)
    return unquote(encoded_product_id)
