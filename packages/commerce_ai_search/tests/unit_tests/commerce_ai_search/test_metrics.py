from __future__ import annotations

from commerce_ai_search.metrics import metrics_to_prometheus


def test_prometheus_exports_query_vector_runtime_cache_hit_miss_metrics() -> None:
    text = metrics_to_prometheus(
        {
            "engine": {
                "gemini_query_embedding_cache": {
                    "runtime_entries": 2,
                    "runtime_text_entries": 1,
                    "runtime_image_entries": 1,
                    "runtime_max_entries": 4,
                    "runtime_text_max_entries": 2,
                    "runtime_image_max_entries": 2,
                    "runtime_hits": 7,
                    "runtime_text_hits": 3,
                    "runtime_image_hits": 4,
                    "runtime_misses": 5,
                    "runtime_text_misses": 2,
                    "runtime_image_misses": 3,
                    "runtime_evictions": 1,
                    "runtime_text_evictions": 0,
                    "runtime_image_evictions": 1,
                }
            }
        }
    )

    assert "haeorum_gemini_query_vector_runtime_cache_hits 7" in text
    assert "haeorum_gemini_query_vector_runtime_image_cache_hits 4" in text
    assert "haeorum_gemini_query_vector_runtime_image_cache_misses 3" in text
    assert "haeorum_gemini_query_vector_runtime_image_cache_evictions 1" in text
