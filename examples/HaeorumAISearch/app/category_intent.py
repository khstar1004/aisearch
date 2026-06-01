from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from .query_normalizer import normalize_query_spacing, normalize_query_text


CATEGORY_INTENT_RULES: dict[str, tuple[str, ...]] = {
    "우산": ("우산", "장우산", "3단우산", "양산", "umbrella"),
    "텀블러": ("텀블러", "텐블러", "탬블러", "보틀", "물병", "보온병", "보냉병", "스텐컵", "tumbler", "bottle"),
    "머그컵": ("머그컵", "머그", "컵", "mug"),
    "점착메모지": ("점착메모지", "포스트잇", "포스트 잇", "메모지", "점착", "sticky", "memo"),
    "부채": ("부채", "손부채", "전통부채", "접이식부채", "합죽선", "오죽선"),
    "상패": ("상패", "트로피", "감사패", "공로패", "크리스탈", "명패", "crystal"),
    "볼펜": ("볼펜", "펜", "필기구", "샤프", "만년필", "pen"),
    "가방": ("가방", "백", "파우치", "에코백", "장바구니", "토트백", "bag"),
    "달력": ("달력", "캘린더", "카렌다", "탁상달력", "벽걸이달력", "calendar"),
    "선풍기": ("선풍기", "손선풍기", "핸디선풍기", "휴대용선풍기", "탁상용선풍기"),
    "보조배터리": ("보조배터리", "배터리", "powerbank", "power bank"),
    "물티슈": ("물티슈", "티슈", "위생티슈", "wet tissue"),
    "USB메모리": ("usb", "usb메모리", "유에스비", "메모리"),
    "네임택": ("네임택", "네임텍", "명찰", "name tag", "nametag"),
    "키링": ("키링", "열쇠고리", "키홀더", "keyring", "key holder"),
    "마우스패드": ("마우스패드", "마우스 패드", "장패드", "데스크매트", "mousepad"),
    "타올": ("타올", "타월", "수건", "호텔수건", "핸드타월", "비치타월", "towel"),
    "클리어화일": ("클리어화일", "클리어파일", "화일", "파일", "바인더", "파일철", "clear file"),
    "생활용품": ("생활용품", "주방용품", "욕실용품", "위생용품", "칫솔", "치약", "비누", "핸드워시", "담요", "무릎담요"),
}


@lru_cache(maxsize=4096)
def infer_category_intents(query: str | None, limit: int = 3) -> tuple[str, ...]:
    normalized = normalize_query_text(query)
    if not normalized or limit <= 0:
        return ()

    text = normalize_query_spacing(normalized)
    compact_text = text.replace(" ", "")
    query_terms = set(text.split())
    scored: list[tuple[int, str]] = []

    for category, terms in CATEGORY_INTENT_RULES.items():
        score = 0
        matched = 0
        for term in terms:
            term_text = normalize_query_spacing(term)
            if not term_text:
                continue
            term_compact = term_text.replace(" ", "")
            if term_text == text or term_compact == compact_text:
                score += 6
                matched += 1
            elif term_text in text or term_compact in compact_text:
                score += 4
                matched += 1
            elif term_text in query_terms:
                score += 3
                matched += 1
            elif any(partial_category_match(term_text, query_term) for query_term in query_terms):
                score += 1
                matched += 1
        if matched:
            scored.append((score, category))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(category for _, category in scored[:limit])


def append_inferred_categories(query: str | None, inferred_categories: Iterable[str]) -> str | None:
    text = normalize_query_spacing(query)
    parts = [text] if text else []
    seen = set(parts)
    for category in inferred_categories:
        category_text = normalize_query_spacing(category)
        if not category_text:
            continue
        if category_text in seen or category_text in text:
            continue
        parts.append(category_text)
        seen.add(category_text)
    return " ".join(parts) or None


def partial_category_match(term: str, query_term: str) -> bool:
    if min(len(term), len(query_term)) < 2:
        return False
    return term in query_term or query_term in term
