from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from commerce_ai_search.config import Settings
from commerce_ai_search.engine import LocalSearchEngine
from commerce_ai_search.image_validation import ValidatedImage
from commerce_ai_search.models import ProductDocument, SearchRequest
from commerce_ai_search.search_service import AISearchService, SearchLogger


def test_identical_images_share_one_validation_work_item(monkeypatch, tmp_path):
    calls = 0

    def counted_validate_image_base64(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return ValidatedImage(
            data_url="data:image/png;base64,AAAA",
            mime_type="image/png",
            size_bytes=4,
            sha256="same-image",
            width=32,
            height=32,
        )

    monkeypatch.setattr(
        "commerce_ai_search.search_service.validate_image_base64",
        counted_validate_image_base64,
    )
    engine = LocalSearchEngine(
        [
            ProductDocument.model_validate(
                {
                    "product_id": "P001",
                    "product_name": "이미지 검색용 텀블러",
                    "category_name": "텀블러",
                    "main_image_url": "https://cdn.example.com/P001.jpg",
                    "status": "active",
                    "display_yn": "Y",
                }
            )
        ]
    )
    service = AISearchService(
        engine,
        Settings(
            engine_backend="local",
            image_validation_cache_ttl_seconds=60.0,
            image_validation_wait_seconds=1.0,
            low_score_threshold=0.05,
        ),
        logger=SearchLogger(tmp_path / "search.jsonl"),
    )

    request = SearchRequest(image_base64="same-image", limit=1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _index: service.search(request), range(4)))

    assert calls == 1
    assert all(response.top[0].product_id == "P001" for response in responses)
    status = service.image_validation_status()
    assert status["wait_events"] >= 1
    assert status["cache_entry_count"] == 1


def make_image_validation_service(monkeypatch, tmp_path, validator, **settings_overrides):
    monkeypatch.setattr("commerce_ai_search.search_service.validate_image_base64", validator)
    engine = LocalSearchEngine(
        [
            ProductDocument.model_validate(
                {
                    "product_id": "P001",
                    "product_name": "이미지 검색용 텀블러",
                    "category_name": "텀블러",
                    "main_image_url": "https://cdn.example.com/P001.jpg",
                    "status": "active",
                    "display_yn": "Y",
                }
            )
        ]
    )
    return AISearchService(
        engine,
        Settings(
            engine_backend="local",
            image_validation_cache_ttl_seconds=60.0,
            image_validation_wait_seconds=1.0,
            low_score_threshold=0.05,
            **settings_overrides,
        ),
        logger=SearchLogger(tmp_path / "search.jsonl"),
    )


def test_image_validation_cache_reuses_mime_alias_data_urls(monkeypatch, tmp_path):
    calls = 0

    def counted_validate_image_base64(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ValidatedImage(
            data_url="data:image/jpeg;base64,AAAA",
            mime_type="image/jpeg",
            size_bytes=4,
            sha256="same-image",
            width=32,
            height=32,
        )

    service = make_image_validation_service(monkeypatch, tmp_path, counted_validate_image_base64)

    service.search(SearchRequest(image_base64="data:image/jpg;base64,AAAA", limit=1))
    service.search(SearchRequest(image_base64="data:image/jpeg;base64,AAAA", limit=1))

    assert calls == 1
    status = service.image_validation_status()
    assert status["cache_hits"] == 1
    assert status["cache_entry_count"] == 1


def test_image_validation_cache_keeps_mime_spoofing_separate(monkeypatch, tmp_path):
    calls = 0

    def counted_validate_image_base64(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ValidatedImage(
            data_url="data:image/png;base64,AAAA",
            mime_type="image/png",
            size_bytes=4,
            sha256=f"image-{calls}",
            width=32,
            height=32,
        )

    service = make_image_validation_service(monkeypatch, tmp_path, counted_validate_image_base64)

    service.search(SearchRequest(image_base64="data:image/png;base64,AAAA", limit=1))
    service.search(SearchRequest(image_base64="data:image/jpeg;base64,AAAA", limit=1))

    assert calls == 2
    assert service.image_validation_status()["cache_entry_count"] == 2


def test_invalid_image_validation_error_is_cached(monkeypatch, tmp_path):
    calls = 0

    def failing_validate_image_base64(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("image is damaged or cannot be decoded")

    service = make_image_validation_service(monkeypatch, tmp_path, failing_validate_image_base64)
    request = SearchRequest(image_base64="data:image/png;base64,AAAA", limit=1)

    with pytest.raises(ValueError, match="image is damaged"):
        service.search(request)
    with pytest.raises(ValueError, match="image is damaged"):
        service.search(request)

    status = service.image_validation_status()
    assert calls == 1
    assert status["error_cache_entry_count"] == 1
    assert status["error_cache_hits"] == 1


def test_query_image_analysis_setting_is_passed_to_validation(monkeypatch, tmp_path):
    analyze_feature_values = []

    def counted_validate_image_base64(*_args, **kwargs):
        analyze_feature_values.append(kwargs["analyze_features"])
        return ValidatedImage(
            data_url="data:image/png;base64,AAAA",
            mime_type="image/png",
            size_bytes=4,
            sha256="same-image",
            width=32,
            height=32,
        )

    service = make_image_validation_service(
        monkeypatch,
        tmp_path,
        counted_validate_image_base64,
        query_image_analysis=False,
    )

    service.search(SearchRequest(image_base64="data:image/png;base64,AAAA", limit=1))

    assert analyze_feature_values == [False]
