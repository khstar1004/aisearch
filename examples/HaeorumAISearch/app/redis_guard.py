from __future__ import annotations

import threading
import time
from typing import Any


class RedisFailureBackoff:
    def __init__(self, backoff_seconds: float = 0.0):
        self.backoff_seconds = max(float(backoff_seconds or 0.0), 0.0)
        self.failure_events = 0
        self.skipped_operations = 0
        self.backoff_until = 0.0
        self.last_failure_at: float | None = None
        self.last_failure_operation: str | None = None
        self.last_skipped_operation: str | None = None
        self._lock = threading.RLock()

    def allow(self, operation: str) -> bool:
        if self.backoff_seconds <= 0:
            return True
        now = time.time()
        with self._lock:
            if now >= self.backoff_until:
                return True
            self.skipped_operations += 1
            self.last_skipped_operation = operation
            return False

    def record_failure(self, operation: str) -> None:
        if self.backoff_seconds <= 0:
            return
        now = time.time()
        with self._lock:
            self.failure_events += 1
            self.last_failure_at = now
            self.last_failure_operation = operation
            self.backoff_until = now + self.backoff_seconds

    def record_success(self) -> None:
        if self.backoff_seconds <= 0:
            return
        with self._lock:
            self.backoff_until = 0.0

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            backoff_until = self.backoff_until
            active = self.backoff_seconds > 0 and now < backoff_until
            return {
                "redis_backoff_seconds": self.backoff_seconds,
                "redis_backoff_active": active,
                "redis_backoff_until_epoch": round(backoff_until, 3) if active else None,
                "redis_backoff_remaining_ms": round(max(backoff_until - now, 0.0) * 1000, 3) if active else 0.0,
                "redis_backoff_failure_events": self.failure_events,
                "redis_backoff_skipped_operations": self.skipped_operations,
                "redis_backoff_last_failure_at": round(self.last_failure_at, 3) if self.last_failure_at else None,
                "redis_backoff_last_failure_operation": self.last_failure_operation,
                "redis_backoff_last_skipped_operation": self.last_skipped_operation,
            }
