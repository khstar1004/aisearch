from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from commerce_ai_search.cache import MemorySearchCache
from commerce_ai_search.config import Settings
from commerce_ai_search.engine import EngineQuery, EngineHit, LocalSearchEngine
from commerce_ai_search.models import ProductDocument, SearchRequest
from commerce_ai_search.search_service import AISearchService, SearchLogger


class SlowCountingLocalEngine(LocalSearchEngine):
    def __init__(self, products):
        super().__init__(products)
        self.search_calls = 0
        self.started = threading.Event()

    def search(self, query: EngineQuery) -> list[EngineHit]:
        self.search_calls += 1
        self.started.set()
        time.sleep(0.05)
        return super().search(query)


class CoordinatedMemorySearchCache(MemorySearchCache):
    def __init__(self, ttl_seconds: int, max_entries: int = 10000):
        super().__init__(ttl_seconds, max_entries)
        self.claims = 0
        self.contentions = 0
        self.releases = 0
        self._owners: set[str] = set()

    def claim_miss_owner(self, key: str, lock_seconds: float) -> bool:
        with self._lock:
            if key in self._owners:
                self.contentions += 1
                return False
            self.claims += 1
            self._owners.add(key)
            return True

    def release_miss_owner(self, key: str) -> None:
        with self._lock:
            if key in self._owners:
                self._owners.remove(key)
                self.releases += 1


def test_identical_uncached_text_searches_share_one_engine_call(tmp_path):
    engine = SlowCountingLocalEngine(
        [
            ProductDocument.model_validate(
                {
                    "product_id": "TB001",
                    "product_name": "스텐 텀블러",
                    "category_name": "텀블러",
                    "main_image_url": "https://cdn.example.com/TB001.jpg",
                    "status": "active",
                    "display_yn": "Y",
                }
            )
        ]
    )
    service = AISearchService(
        engine,
        Settings(engine_backend="local", cache_ttl_seconds=30, cache_miss_wait_seconds=1.0, low_score_threshold=0.05),
        logger=SearchLogger(tmp_path / "search.jsonl"),
    )
    request = SearchRequest(q="스텐텀블러", limit=1)

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _index: service.search(request), range(4)))

    assert engine.search_calls == 1
    assert all(response.top[0].product_id == "TB001" for response in responses)
    status = service.singleflight_status()
    assert status["wait_events"] >= 1
    assert status["wait_timeouts"] == 0
    assert service.cache.status()["entry_count"] == 1


def test_distributed_miss_lock_waits_for_cache_fill_across_workers(tmp_path):
    products = [
        ProductDocument.model_validate(
            {
                "product_id": "TB001",
                "product_name": "스텐 텀블러",
                "category_name": "텀블러",
                "main_image_url": "https://cdn.example.com/TB001.jpg",
                "status": "active",
                "display_yn": "Y",
            }
        )
    ]
    owner_engine = SlowCountingLocalEngine(products)
    waiting_engine = SlowCountingLocalEngine(products)
    settings = Settings(
        engine_backend="local",
        cache_ttl_seconds=30,
        cache_miss_lock_seconds=1.0,
        cache_miss_wait_seconds=1.0,
        cache_miss_poll_seconds=0.005,
        low_score_threshold=0.05,
    )
    cache = CoordinatedMemorySearchCache(settings.cache_ttl_seconds, settings.cache_max_entries)
    owner_service = AISearchService(
        owner_engine,
        settings,
        logger=SearchLogger(tmp_path / "owner-search.jsonl"),
        cache=cache,
    )
    waiting_service = AISearchService(
        waiting_engine,
        settings,
        logger=SearchLogger(tmp_path / "waiting-search.jsonl"),
        cache=cache,
    )
    request = SearchRequest(q="스텐텀블러", limit=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner_future = executor.submit(owner_service.search, request)
        assert owner_engine.started.wait(timeout=1.0)
        waiting_response = waiting_service.search(request)
        owner_response = owner_future.result(timeout=1.0)

    assert owner_response.top[0].product_id == "TB001"
    assert waiting_response.top[0].product_id == "TB001"
    assert owner_engine.search_calls == 1
    assert waiting_engine.search_calls == 0
    assert cache.claims == 1
    assert cache.contentions >= 1
    assert cache.releases == 1
    assert cache.status()["lock_wait_events"] == 1
    assert cache.status()["lock_wait_timeouts"] == 0
