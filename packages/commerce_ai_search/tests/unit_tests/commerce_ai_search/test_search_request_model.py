from __future__ import annotations

import pytest
from pydantic import ValidationError

from commerce_ai_search.models import SearchRequest


def test_search_request_rejects_weight_sum_overflow():
    with pytest.raises(ValidationError, match="text_weight and image_weight sum must be finite"):
        SearchRequest(q="스텐텀블러", text_weight=1e308, image_weight=1e308)


def test_search_request_accepts_large_finite_single_weight():
    request = SearchRequest(q="스텐텀블러", text_weight=1e308, image_weight=1.0)

    assert request.text_weight == 1e308
    assert request.image_weight == 1.0
