from __future__ import annotations

from commerce_ai_search.engine import EngineQuery, MarqoSearchEngine


def test_marqo_prewarm_text_query_vectors_batches_dedupes_and_reuses_runtime_cache(monkeypatch) -> None:
    engine = MarqoSearchEngine(
        "http://marqo.example",
        "products",
        embedding_backend="gemini",
        qwen_embedding_dimensions=3,
    )
    calls: list[list[str]] = []

    def fake_qwen_embed_query_texts(texts: list[str], **_kwargs) -> list[list[float]]:
        calls.append(list(texts))
        return [[float(index), float(index + 1), float(index + 2)] for index, _text in enumerate(texts, start=1)]

    monkeypatch.setattr(engine, "qwen_embed_query_texts", fake_qwen_embed_query_texts)
    queries = [
        EngineQuery(q="스텐텀블러"),
        EngineQuery(q="스텐텀블러"),
        EngineQuery(q="검정우산"),
    ]

    try:
        first = engine.prewarm_text_query_vectors(queries, batch_size=1)
        second = engine.prewarm_text_query_vectors(queries, batch_size=2)
    finally:
        engine.close()

    assert first["ok"] is True
    assert first["supported"] is True
    assert first["computed"] == 2
    assert first["deduplicated"] == 1
    assert first["runtime_text_entries"] == 2
    assert len(calls) == 2
    assert all(len(batch) == 1 for batch in calls)
    assert calls[0][0] != calls[1][0]

    assert second["computed"] == 0
    assert second["cached"] == 3
    assert second["runtime_text_entries"] == 2


def test_marqo_prewarm_text_query_vectors_is_noop_for_native_embedding_backend() -> None:
    engine = MarqoSearchEngine(
        "http://marqo.example",
        "products",
        embedding_backend="native",
    )
    try:
        result = engine.prewarm_text_query_vectors([EngineQuery(q="스텐텀블러")])
    finally:
        engine.close()

    assert result["ok"] is True
    assert result["supported"] is False
    assert result["computed"] == 0
    assert result["skipped"] == 1


def test_marqo_runtime_image_query_vector_cache_reports_hits_misses_and_evictions() -> None:
    engine = MarqoSearchEngine(
        "http://marqo.example",
        "products",
        embedding_backend="gemini",
        qwen_embedding_dimensions=3,
        qwen_query_runtime_image_cache_entries=1,
    )
    try:
        assert engine._get_runtime_qwen_query_vector("image:first") is None
        engine._set_runtime_qwen_query_vector("image:first", [1.0, 2.0, 3.0])
        assert engine._get_runtime_qwen_query_vector("image:first") == [1.0, 2.0, 3.0]
        engine._set_runtime_qwen_query_vector("image:second", [4.0, 5.0, 6.0])
        status = engine.qwen_query_vector_status()
    finally:
        engine.close()

    assert status["runtime_image_entries"] == 1
    assert status["runtime_image_hits"] == 1
    assert status["runtime_image_misses"] == 1
    assert status["runtime_image_evictions"] == 1
