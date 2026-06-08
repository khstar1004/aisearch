from __future__ import annotations

import json

from commerce_ai_search.config import load_query_synonyms
from commerce_ai_search.engine import LocalSearchEngine, audit_query_synonyms, expand_terms


def test_load_query_synonyms_normalizes_and_links_groups_bidirectionally(tmp_path):
    path = tmp_path / "query-synonyms.json"
    path.write_text(
        json.dumps(
            {
                "synonyms": {
                    "브랜드 타월": ["송월", "수건"],
                    "고속-충전": "급속/충전",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    synonyms = load_query_synonyms(path)

    assert synonyms["브랜드 타월"] == ("송월", "수건")
    assert synonyms["송월"] == ("브랜드 타월", "수건")
    assert synonyms["수건"] == ("브랜드 타월", "송월")
    assert synonyms["고속 충전"] == ("급속 충전",)
    assert synonyms["급속 충전"] == ("고속 충전",)


def test_audit_query_synonyms_reports_duplicates_self_references_and_builtin_overlap():
    audit = audit_query_synonyms(
        {
            "스텐": ["스테인리스", "스테인리스", "스텐"],
            "스텐 ": ["스뎅"],
            "행사 타월": ["타올", "타올"],
        }
    )

    assert audit["duplicate_terms"] == ["스텐"]
    assert audit["duplicate_values"] == ["스텐:스테인리스", "행사 타월:타올"]
    assert audit["self_references"] == ["스텐"]
    assert audit["built_in_term_overlaps"] == ["스텐"]
    assert audit["built_in_value_overlaps"] == ["스텐:스텐", "행사 타월:타올"]


def test_custom_query_synonyms_expand_without_duplicate_terms():
    terms = expand_terms(["행사타월"], {"행사타월": ["송월", "수건", "송월"]})

    assert terms.count("송월") == 1
    assert "타월" in terms
    assert "수건" in terms


def test_compound_and_custom_synonym_expansion_stays_bounded():
    custom_synonyms = {
        "텀블러": ["보틀", "물병", "보온병", "보냉병"],
        "우산": ["장우산", "3단", "자동우산"],
        "타올": ["타월", "수건", "답례품"],
        "송월타올": ["브랜드타월", "송월"],
    }

    terms = expand_terms(["검정우산", "스텐텀블러", "송월타올"], custom_synonyms)

    assert len(terms) <= 40
    assert len(terms) == len(set(terms))
    assert {"검정", "우산", "스텐", "텀블러", "송월", "타올", "브랜드타월"}.issubset(set(terms))


def test_local_engine_custom_synonym_record_term_cache_reuses_equivalent_digest():
    engine = LocalSearchEngine(expanded_terms_cache_max_entries=4)
    record_terms = frozenset({"텀블러"})

    first = engine._expanded_record_terms(record_terms, {"텀블러": ["보틀", "물병"]})
    equivalent_terms = frozenset(["텀블러"])
    assert equivalent_terms is not record_terms
    second = engine._expanded_record_terms(equivalent_terms, {"텀블러": ["물병", "보틀", ""]})

    assert first == second
    assert "보틀" in second
    assert "물병" in second
    assert len(engine._expanded_terms_cache) == 1


def test_local_engine_custom_synonym_record_term_cache_is_bounded_lru():
    engine = LocalSearchEngine(expanded_terms_cache_max_entries=2)
    synonyms = {"텀블러": ["보틀"]}
    token = engine._custom_synonym_cache_token(synonyms)
    first_terms = frozenset({"텀블러"})
    second_terms = frozenset({"보틀"})
    third_terms = frozenset({"물병"})

    engine._expanded_record_terms(first_terms, synonyms)
    engine._expanded_record_terms(second_terms, synonyms)
    engine._expanded_record_terms(first_terms, synonyms)
    engine._expanded_record_terms(third_terms, synonyms)

    assert token is not None
    assert len(engine._expanded_terms_cache) == 2
    assert (token, first_terms) in engine._expanded_terms_cache
    assert (token, second_terms) not in engine._expanded_terms_cache
    assert (token, third_terms) in engine._expanded_terms_cache
    assert engine.health()["expanded_terms_cache"] == {"entry_count": 2, "max_entries": 2}
