from __future__ import annotations

import re
from functools import lru_cache


QUERY_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("포스트 잇", "포스트잇"),
    ("포스트-잇", "포스트잇"),
    ("텐블러", "텀블러"),
    ("탬블러", "텀블러"),
    ("우싼", "우산"),
    ("에코빽", "에코백"),
    ("카랜더", "캘린더"),
    ("캘린다", "캘린더"),
    ("스테인레스", "스테인리스"),
    ("스뎅", "스텐"),
    ("스댄", "스텐"),
    ("유에스비", "usb"),
    ("검정색", "검정"),
    ("검은색", "검은"),
    ("무선 마우스", "무선마우스"),
    ("블루투스 마우스", "블루투스마우스"),
)

EXACT_QUERY_REPLACEMENTS: dict[str, str] = {
    "마우스": "무선마우스",
}


@lru_cache(maxsize=4096)
def normalize_query_text(value: str | None) -> str | None:
    text = normalize_query_spacing(value)
    if not text:
        return None
    text = EXACT_QUERY_REPLACEMENTS.get(text, text)
    for source, replacement in QUERY_REPLACEMENTS:
        text = text.replace(source, replacement)
    return normalize_query_spacing(text)


@lru_cache(maxsize=4096)
def build_search_query(original_query: str | None, normalized_query: str | None) -> str | None:
    normalized = normalize_query_spacing(normalized_query)
    original = normalize_query_spacing(original_query)
    if not normalized:
        return original or None
    if (
        not original
        or original == normalized
        or original.replace(" ", "") == normalized.replace(" ", "")
        or EXACT_QUERY_REPLACEMENTS.get(original) == normalized
    ):
        return normalized
    return f"{normalized} {original}"


@lru_cache(maxsize=4096)
def normalize_query_spacing(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[\s/_-]+", " ", text)
    return " ".join(text.split())
