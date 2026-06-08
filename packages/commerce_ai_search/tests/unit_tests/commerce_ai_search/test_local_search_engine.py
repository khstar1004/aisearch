from __future__ import annotations

import pytest

from commerce_ai_search.category_intent import infer_category_intents
from commerce_ai_search.config import Settings
from commerce_ai_search.engine import (
    EngineHit,
    EngineQuery,
    LocalSearchEngine,
    build_text_relevance_query,
    category_match_profile,
    category_query_relevance_score_for_query,
    decompose_compound_query_term,
    expand_terms,
    rerank_text_hits,
    rerank_text_to_image_hits,
    term_matches_document,
)
from commerce_ai_search.models import ProductDocument, SearchRequest
from commerce_ai_search.search_service import AISearchService, SearchLogger


def product(
    product_id: str,
    name: str,
    category: str,
    *,
    mall_id: str = "shop001",
    price: float = 1000,
    image_tags: list[str] | None = None,
    **kwargs,
) -> ProductDocument:
    return ProductDocument.model_validate(
        {
            "product_id": product_id,
            "product_name": name,
            "category_name": category,
            "price": price,
            "main_image_url": f"https://cdn.example.com/{product_id}.jpg",
            "mall_id": mall_id,
            "status": "active",
            "display_yn": "Y",
            "extra": {"image_tags": image_tags or []},
            **kwargs,
        }
    )


def sample_engine() -> LocalSearchEngine:
    return LocalSearchEngine(
        [
            product(
                "UM-BLACK-3",
                "검정 3단 자동 우산",
                "우산",
                colors=["블랙"],
                keywords=["접이식", "판촉"],
                image_tags=["검정", "우산"],
            ),
            product(
                "UM-BLUE-LONG",
                "고급 장우산",
                "우산",
                colors=["파랑"],
                image_tags=["파랑", "우산"],
            ),
            product(
                "TB-STAINLESS",
                "스테인리스 보온 텀블러",
                "텀블러",
                materials=["스테인리스"],
                colors=["실버"],
                min_order_qty=100,
                price_min=5000,
                price_max=8000,
                delivery_days=5,
                image_tags=["보틀", "실버"],
            ),
            product(
                "TW-SONGWOL",
                "송월 타월 선물세트",
                "타올",
                keywords=["수건", "답례품"],
                image_tags=["타월", "수건"],
            ),
            product(
                "TB-OTHER-MALL",
                "블랙 보온병",
                "텀블러",
                mall_id="shop002",
                colors=["블랙"],
                image_tags=["텀블러", "검정"],
            ),
            product(
                "MEMO-HIDDEN",
                "비노출 점착 메모지",
                "점착메모지",
                status="inactive",
                display_yn="N",
            ),
        ]
    )


def representative_engine() -> LocalSearchEngine:
    return LocalSearchEngine(
        [
            product(
                "UM-AUTO-BLACK",
                "검정 3단 자동 우산",
                "우산",
                colors=["블랙"],
                keywords=["접이식", "자동"],
            ),
            product(
                "TB-STAINLESS-500",
                "스텐 진공 보온 텀블러 500ml",
                "텀블러",
                materials=["스테인리스"],
                colors=["실버"],
                keywords=["보틀", "보온병"],
            ),
            product(
                "TW-SONGWOL-SET",
                "송월 타올 답례품 세트",
                "타올",
                keywords=["송월", "수건"],
            ),
            product(
                "MEMO-STICKY",
                "포스트잇 점착 메모지",
                "점착메모지",
                keywords=["sticky", "memo"],
            ),
            product(
                "PEN-BASIC",
                "로고 인쇄 볼펜",
                "볼펜",
                print_methods=["실크인쇄"],
                materials=["플라스틱"],
                keywords=["펜"],
            ),
            product(
                "BAG-ECO",
                "무지 에코백 장바구니",
                "에코백",
                materials=["면", "캔버스"],
                colors=["아이보리"],
            ),
            product(
                "CAL-DESK",
                "탁상 달력 캘린더",
                "달력",
                keywords=["카렌다", "calendar"],
            ),
            product(
                "AWARD-CRYSTAL",
                "크리스탈 감사패 상패",
                "상패",
                materials=["크리스탈"],
                keywords=["트로피"],
            ),
            product(
                "FAN-HAND",
                "휴대용 손선풍기",
                "선풍기",
                keywords=["핸디선풍기"],
            ),
            product(
                "BAT-POWER",
                "고속충전 보조배터리",
                "보조배터리",
                keywords=["powerbank", "배터리"],
            ),
            product(
                "TISSUE-WET",
                "휴대용 물티슈",
                "물티슈",
                keywords=["위생티슈"],
            ),
            product(
                "TAG-NAME",
                "캐리어 네임택",
                "네임택",
                keywords=["네임텍", "명찰"],
            ),
            product(
                "KEY-RING",
                "금속 키링 열쇠고리",
                "키링",
                materials=["메탈"],
                keywords=["키홀더"],
            ),
            product(
                "PAD-MOUSE",
                "장패드 마우스패드",
                "마우스패드",
                keywords=["mousepad"],
            ),
            product(
                "MUG-CERAMIC",
                "화이트 세라믹 머그컵",
                "머그컵",
                materials=["세라믹"],
                colors=["화이트"],
                keywords=["컵"],
            ),
            product(
                "BOTTLE-SPORT",
                "스포츠 물병 물통",
                "물병",
                materials=["트라이탄"],
                keywords=["물통"],
            ),
            product(
                "USB-CARD",
                "카드형 USB 메모리 32GB",
                "USB",
                keywords=["유에스비", "메모리"],
            ),
            product(
                "CUP-PAPER",
                "친환경 종이컵",
                "종이컵",
                materials=["종이"],
                keywords=["일회용컵"],
            ),
            product(
                "APR-KITCHEN",
                "방수 주방 앞치마",
                "앞치마",
                materials=["폴리에스터"],
                keywords=["에이프런"],
            ),
            product(
                "MASK-KF94",
                "KF94 마스크",
                "마스크",
                keywords=["방역", "위생"],
            ),
            product(
                "KIT-FIRSTAID",
                "휴대용 밴드 구급함",
                "구급함",
                keywords=["밴드", "구급키트"],
            ),
            product(
                "LANYARD-BASIC",
                "사원증 목걸이줄 랜야드",
                "목걸이줄",
                keywords=["랜야드", "lanyard"],
            ),
            product(
                "STICKER-LOGO",
                "원형 로고 스티커",
                "스티커",
                keywords=["라벨"],
            ),
            product(
                "BANNER-MINI",
                "미니 배너 거치대",
                "배너",
                keywords=["홍보배너"],
            ),
            product(
                "POUCH-CANVAS",
                "캔버스 파우치",
                "파우치",
                materials=["캔버스"],
                keywords=["미니백"],
            ),
            product(
                "BAG-PAPER",
                "쇼핑 종이백",
                "종이백",
                materials=["종이"],
                keywords=["쇼핑백"],
            ),
            product(
                "CLIP-MAGNET",
                "자석 집게 클립",
                "클립",
                materials=["자석"],
                keywords=["마그넷"],
            ),
            product(
                "NOTE-DIARY",
                "양장 노트 다이어리",
                "노트",
                keywords=["다이어리", "수첩"],
            ),
            product(
                "CLOCK-DESK",
                "탁상 시계",
                "시계",
                keywords=["알람시계"],
            ),
            product(
                "CHARGER-WIRELESS",
                "무선 충전기",
                "충전기",
                keywords=["충전패드"],
            ),
            product(
                "CABLE-CTYPE",
                "C타입 충전 케이블",
                "케이블",
                keywords=["충전선"],
            ),
            product(
                "STAND-PHONE",
                "휴대폰 거치대",
                "거치대",
                keywords=["스마트폰스탠드"],
            ),
            product(
                "SOAP-HAND",
                "핸드워시 선물세트",
                "핸드워시",
                keywords=["손세정제"],
            ),
            product(
                "MAGNET-FRIDGE",
                "냉장고 홍보 마그넷",
                "마그넷",
                keywords=["자석"],
            ),
        ]
    )


def test_local_search_ranks_exact_color_and_category_match_first():
    hits = sample_engine().search(EngineQuery(q="검은 우산", limit=10))

    assert [hit.document.product_id for hit in hits[:2]] == ["UM-BLACK-3", "UM-BLUE-LONG"]
    assert hits[0].source_scores["text"] > hits[1].source_scores["text"]


def test_local_search_supports_typo_tolerant_korean_terms():
    assert term_matches_document("텀블러", "스텐 텀블라", {"스텐", "텀블라"}) is True
    assert term_matches_document("텀블러", "우산 장우산", {"우산", "장우산"}) is False


def test_local_search_decomposes_common_compound_product_queries():
    engine = sample_engine()

    with_cases = [
        ("검정우산", "UM-BLACK-3"),
        ("스텐텀블러", "TB-STAINLESS"),
        ("송월타올", "TW-SONGWOL"),
    ]
    for query, expected_product_id in with_cases:
        hits = engine.search(EngineQuery(q=query, limit=10))
        assert hits[0].document.product_id == expected_product_id


@pytest.mark.parametrize(
    ("query", "expected_product_id"),
    [
        ("검정우산", "UM-AUTO-BLACK"),
        ("스텐텀블러", "TB-STAINLESS-500"),
        ("스텐 보온 텀블라", "TB-STAINLESS-500"),
        ("송월타올", "TW-SONGWOL-SET"),
        ("포스트잇", "MEMO-STICKY"),
        ("볼펜", "PEN-BASIC"),
        ("에코백", "BAG-ECO"),
        ("캘린더", "CAL-DESK"),
        ("크리스탈 상패", "AWARD-CRYSTAL"),
        ("손선풍기", "FAN-HAND"),
        ("보조배터리", "BAT-POWER"),
        ("물티슈", "TISSUE-WET"),
        ("네임텍", "TAG-NAME"),
        ("열쇠고리", "KEY-RING"),
        ("마우스패드", "PAD-MOUSE"),
        ("화이트 머그컵", "MUG-CERAMIC"),
        ("스포츠 물병", "BOTTLE-SPORT"),
        ("유에스비 메모리", "USB-CARD"),
        ("친환경 종이컵", "CUP-PAPER"),
        ("방수 앞치마", "APR-KITCHEN"),
        ("KF94 마스크", "MASK-KF94"),
        ("밴드 구급키트", "KIT-FIRSTAID"),
        ("랜야드 목걸이줄", "LANYARD-BASIC"),
        ("로고 스티커", "STICKER-LOGO"),
        ("미니 배너", "BANNER-MINI"),
        ("캔버스 파우치", "POUCH-CANVAS"),
        ("쇼핑백", "BAG-PAPER"),
        ("자석 클립", "CLIP-MAGNET"),
        ("다이어리 노트", "NOTE-DIARY"),
        ("탁상 시계", "CLOCK-DESK"),
        ("무선 충전기", "CHARGER-WIRELESS"),
        ("C타입 케이블", "CABLE-CTYPE"),
        ("휴대폰 거치대", "STAND-PHONE"),
        ("핸드워시", "SOAP-HAND"),
        ("홍보 마그넷", "MAGNET-FRIDGE"),
    ],
)
def test_representative_product_queries_rank_expected_product_first(query, expected_product_id):
    hits = representative_engine().search(EngineQuery(q=query, limit=10))

    assert hits
    assert hits[0].document.product_id == expected_product_id


def test_compound_query_decomposition_keeps_brand_remainder_and_known_terms():
    assert decompose_compound_query_term("송월타올") == ["타올", "송월"]
    assert decompose_compound_query_term("검정우산") == ["검정", "우산"]


def test_compound_query_decomposition_applies_custom_synonyms_to_parts():
    terms = expand_terms(["송월타올"], {"송월": ["브랜드타월"]})

    assert "송월" in terms
    assert "타월" in terms
    assert "브랜드타월" in terms


def test_compound_opener_query_does_not_expand_bottle_to_tumbler_family():
    terms = set(expand_terms(["보틀오프너"]))

    assert {"보틀오프너", "오프너", "병따개", "와인오프너", "보틀"}.issubset(terms)
    assert "텀블러" not in terms
    assert "보온병" not in terms
    assert "물병" not in terms


def test_opener_category_intent_does_not_infer_tumbler_from_bottle_prefix():
    assert infer_category_intents("보틀오프너", limit=2) == ("오프너",)


def test_text_to_image_rerank_prioritizes_clear_text_evidence_over_visual_score():
    hits = [
        EngineHit(
            document=product("PAD-ODD", "c", "컴퓨터/전자 > 마우스패드"),
            score=0.695,
            source_scores={"marqo": 0.695, "gemini_image_vector": 0.695},
        ),
        EngineHit(
            document=product("KEYBOARD", "3컬러 유선키보드", "컴퓨터/전자 > 마우스/키보드"),
            score=0.675,
            source_scores={"marqo": 0.675, "gemini_image_vector": 0.675},
        ),
    ]
    query = EngineQuery(q="키보드", inferred_categories=("마우스/키보드",), limit=2)

    reranked = rerank_text_to_image_hits(query, hits)

    assert [hit.document.product_id for hit in reranked] == ["KEYBOARD", "PAD-ODD"]
    assert reranked[0].source_scores["text_evidence"] > reranked[1].source_scores["text_evidence"]


def test_text_to_image_rerank_uses_exact_category_when_text_evidence_ties():
    hits = [
        EngineHit(
            document=product("CABLE", "USB Type C 스마트폰 충전케이블", "컴퓨터/전자 > 외장하드/케이블"),
            score=0.84,
            source_scores={"marqo": 0.84, "gemini_text_vector": 0.84},
        ),
        EngineHit(
            document=product("CHARGER", "고속 USB 충전독", "컴퓨터/전자 > 스마트폰충전기"),
            score=0.82,
            source_scores={"marqo": 0.82, "gemini_text_vector": 0.82},
        ),
    ]
    query = EngineQuery(q="컴퓨터/전자 > 스마트폰충전기", limit=2)

    reranked = rerank_text_to_image_hits(query, hits)

    assert [hit.document.product_id for hit in reranked] == ["CHARGER", "CABLE"]
    assert reranked[0].source_scores["category_query"] == 1.0
    assert reranked[1].source_scores["text_evidence"] > 0


def test_text_to_image_category_match_uses_original_query_before_inferred_category_expansion():
    hits = [
        EngineHit(
            document=product("TOWEL-EXPENSIVE", "타올 150g 3P 세트", "타올 > 타올/세트(1만~2만원)"),
            score=0.88,
            source_scores={"marqo": 0.88, "gemini_text_vector": 0.88},
        ),
        EngineHit(
            document=product("TOWEL-UNDER-10000", "수건 150g 2p 세트", "타올 > 타올/세트(1만원미만)"),
            score=0.84,
            source_scores={"marqo": 0.84, "gemini_text_vector": 0.84},
        ),
    ]
    query = EngineQuery(q="타올 > 타올/세트(1만원미만)", inferred_categories=("타올",), limit=2)

    reranked = rerank_text_to_image_hits(query, hits)

    assert [hit.document.product_id for hit in reranked] == ["TOWEL-UNDER-10000", "TOWEL-EXPENSIVE"]
    assert reranked[0].source_scores["category_query"] == 1.0


def test_text_rerank_prefers_exact_leaf_category_over_substring_category():
    hits = [
        EngineHit(
            document=product("POT-STAND", "디자인 냄비받침대", "주방용품 > 냄비받침/컵받침"),
            score=0.91,
            source_scores={"marqo": 0.91},
        ),
        EngineHit(
            document=product("POT", "스텐 냄비", "주방용품 > 냄비"),
            score=0.82,
            source_scores={"marqo": 0.82},
        ),
    ]

    reranked = rerank_text_hits(EngineQuery(q="냄비", limit=2), hits)

    assert [hit.document.product_id for hit in reranked] == ["POT", "POT-STAND"]
    assert reranked[0].source_scores["category_query"] > reranked[1].source_scores["category_query"]


def test_text_rerank_prefers_full_category_match_over_related_accessory():
    hits = [
        EngineHit(
            document=product("CABLE", "USB Type C 충전케이블", "컴퓨터/전자 > 외장하드/케이블"),
            score=0.9,
            source_scores={"marqo": 0.9},
        ),
        EngineHit(
            document=product("CHARGER", "스마트폰 고속 충전기", "컴퓨터/전자 > 스마트폰충전기"),
            score=0.81,
            source_scores={"marqo": 0.81},
        ),
    ]

    reranked = rerank_text_hits(EngineQuery(q="컴퓨터/전자 > 스마트폰충전기", limit=2), hits)

    assert [hit.document.product_id for hit in reranked] == ["CHARGER", "CABLE"]
    assert reranked[0].source_scores["category_query"] == 1.0


def test_text_rerank_keeps_specific_product_terms_ahead_of_broad_category_match():
    hits = [
        EngineHit(
            document=product("POT", "스텐 냄비", "주방용품 > 냄비"),
            score=0.9,
            source_scores={"marqo": 0.9},
        ),
        EngineHit(
            document=product("POT-HANDLE", "실리콘 냄비 손잡이", "주방용품 > 주방잡화"),
            score=0.76,
            source_scores={"marqo": 0.76},
        ),
    ]

    reranked = rerank_text_hits(EngineQuery(q="냄비 손잡이", limit=2), hits)

    assert [hit.document.product_id for hit in reranked] == ["POT-HANDLE", "POT"]
    assert reranked[0].source_scores["lexical"] > reranked[1].source_scores["lexical"]


def test_text_to_image_rerank_keeps_specific_text_evidence_ahead_of_parent_category():
    hits = [
        EngineHit(
            document=product("CHARGER", "스마트폰 고속 충전기", "컴퓨터/전자 > 스마트폰충전기"),
            score=0.91,
            source_scores={"marqo": 0.91, "gemini_image_vector": 0.91},
        ),
        EngineHit(
            document=product("CABLE", "USB Type C 스마트폰 충전 케이블", "컴퓨터/전자 > 외장하드/케이블"),
            score=0.84,
            source_scores={"marqo": 0.84, "gemini_image_vector": 0.84},
        ),
    ]
    query = EngineQuery(q="스마트폰 충전 케이블", inferred_categories=("스마트폰충전기",), limit=2)

    reranked = rerank_text_to_image_hits(query, hits)

    assert [hit.document.product_id for hit in reranked] == ["CABLE", "CHARGER"]
    assert reranked[0].source_scores["text_evidence"] > reranked[1].source_scores["text_evidence"]


def test_local_search_applies_strict_mall_and_numeric_attribute_filters():
    hits = sample_engine().search(
        EngineQuery(
            q="텀블러",
            mall_id="shop001",
            strict_mall_filter=True,
            material="스텐",
            min_price=6000,
            quantity=100,
            max_delivery_days=7,
            limit=10,
        )
    )

    assert [hit.document.product_id for hit in hits] == ["TB-STAINLESS"]


def test_local_search_combines_brand_price_quantity_and_delivery_filters():
    engine = LocalSearchEngine(
        [
            product(
                "TW-SONGWOL-FAST",
                "송월 호텔 타올 답례품",
                "타올",
                keywords=["송월", "수건", "기념품"],
                price_min=3200,
                price_max=4800,
                min_order_qty=100,
                delivery_days=3,
            ),
            product(
                "TW-SONGWOL-SLOW",
                "송월 프리미엄 타올 세트",
                "타올",
                keywords=["송월", "수건"],
                price_min=7000,
                price_max=9000,
                min_order_qty=100,
                delivery_days=12,
            ),
            product(
                "TW-BASIC-CHEAP",
                "무지 타올 답례품",
                "타올",
                keywords=["수건"],
                price_min=1800,
                price_max=2600,
                min_order_qty=500,
                delivery_days=2,
            ),
        ]
    )

    hits = engine.search(
        EngineQuery(
            q="송월 타올 답례품",
            min_price=3000,
            max_price=5000,
            quantity=200,
            max_delivery_days=5,
            limit=10,
        )
    )

    assert [hit.document.product_id for hit in hits] == ["TW-SONGWOL-FAST"]


def test_search_service_returns_top_three_without_repeating_related_items(tmp_path):
    service = AISearchService(
        sample_engine(),
        Settings(engine_backend="local", low_score_threshold=0.05),
        logger=SearchLogger(tmp_path / "search.jsonl"),
    )

    response = service.search(SearchRequest(q="우산", limit=2))

    assert [item.product_id for item in response.top] == ["UM-BLACK-3", "UM-BLUE-LONG"]
    assert response.items == []
    assert response.suggested_categories[0] == "우산"
    assert response.meta.query_type == "text"
    assert response.meta.has_more is False


def test_search_cache_key_changes_when_query_synonym_policy_changes(tmp_path):
    request = SearchRequest(q="행사타월", limit=5)
    service_without_synonyms = AISearchService(
        sample_engine(),
        Settings(engine_backend="local", low_score_threshold=0.05, query_synonyms={}),
        logger=SearchLogger(tmp_path / "search-no-synonyms.jsonl"),
    )
    service_with_synonyms = AISearchService(
        sample_engine(),
        Settings(engine_backend="local", low_score_threshold=0.05, query_synonyms={"행사타월": ("송월", "수건")}),
        logger=SearchLogger(tmp_path / "search-with-synonyms.jsonl"),
    )

    without_synonyms = service_without_synonyms._prepare_search(request)
    with_synonyms = service_with_synonyms._prepare_search(request)

    assert without_synonyms.cache_key != with_synonyms.cache_key


def test_category_query_relevance_reuses_repeated_category_profile():
    category_match_profile.cache_clear()
    query = build_text_relevance_query("스텐텀블러")
    category = "주방용품 > 스텐텀블러"

    assert category_query_relevance_score_for_query(query, category) == 0.98
    first = category_match_profile.cache_info()

    for _ in range(10):
        assert category_query_relevance_score_for_query(query, category) == 0.98

    repeated = category_match_profile.cache_info()
    assert repeated.misses == first.misses
    assert repeated.hits >= first.hits + 10
