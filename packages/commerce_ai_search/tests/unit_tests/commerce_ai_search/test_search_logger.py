from __future__ import annotations

import time

from commerce_ai_search.search_service import SearchLogger


def test_search_logger_counts_idle_close_when_keep_open_window_expires(tmp_path):
    logger = SearchLogger(tmp_path / "search.jsonl", keep_open_seconds=0.01)
    try:
        logger.write({"type": "search", "q": "텀블러"})

        assert logger.status()["buffer_open"] is True

        time.sleep(0.02)
        status = logger.status()

        assert status["buffer_open"] is False
        assert status["idle_closes"] == 1
        assert status["output_closes"] == 1
    finally:
        logger.close()


def test_search_logger_redacts_sensitive_values_and_limits_tail(tmp_path):
    logger = SearchLogger(tmp_path / "search.jsonl")
    try:
        logger.write(
            {
                "type": "search",
                "q": "contact user@example.com Authorization: Bearer secret-token",
                "image_base64": "data:image/png;base64,AAAA",
                "nested": {"admin_api_key": "secret"},
            }
        )

        entries = logger.tail(limit=10)

        assert entries == [
            {
                "type": "search",
                "q": "contact [redacted-email] Authorization: Bearer [redacted-secret]",
                "image_base64": "[redacted-secret]",
                "nested": {"admin_api_key": "[redacted-secret]"},
            }
        ]
    finally:
        logger.close()
