from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator


class SearchQueueFull(RuntimeError):
    pass


class ImageSearchQueueFull(RuntimeError):
    pass


class SearchExecutionGate:
    def __init__(
        self,
        max_concurrency: int,
        queue_timeout_seconds: float,
        *,
        queue_full_error: type[RuntimeError] = SearchQueueFull,
        queue_full_message: str = "search queue is full",
    ):
        self.max_concurrency = max_concurrency
        self.queue_timeout_seconds = max(0.0, queue_timeout_seconds)
        self.queue_full_error = queue_full_error
        self.queue_full_message = queue_full_message
        self._semaphore = threading.BoundedSemaphore(max_concurrency) if max_concurrency > 0 else None
        self._lock = threading.Lock()
        self._in_flight = 0
        self._acquired_events = 0
        self._queue_full_events = 0
        self._wait_events = 0
        self._total_wait_seconds = 0.0
        self._max_wait_seconds = 0.0
        self._last_wait_seconds = 0.0

    @contextmanager
    def slot(self) -> Iterator[None]:
        wait_started = time.perf_counter()
        if self._semaphore is None:
            self._record_acquired(time.perf_counter() - wait_started)
            try:
                yield
            finally:
                self._record_released()
            return
        if not self._semaphore.acquire(timeout=self.queue_timeout_seconds):
            self._record_queue_full(time.perf_counter() - wait_started)
            raise self.queue_full_error(self.queue_full_message)
        self._record_acquired(time.perf_counter() - wait_started)
        try:
            yield
        finally:
            self._record_released()
            self._semaphore.release()

    def status(self) -> dict[str, int | float | bool | None]:
        with self._lock:
            in_flight = self._in_flight
            acquired_events = self._acquired_events
            queue_full_events = self._queue_full_events
            wait_events = self._wait_events
            total_wait_seconds = self._total_wait_seconds
            max_wait_seconds = self._max_wait_seconds
            last_wait_seconds = self._last_wait_seconds
        enabled = self.max_concurrency > 0
        return {
            "enabled": enabled,
            "max_concurrency": self.max_concurrency,
            "queue_timeout_seconds": self.queue_timeout_seconds,
            "in_flight": in_flight,
            "available_slots": max(self.max_concurrency - in_flight, 0) if enabled else None,
            "acquired_events": acquired_events,
            "queue_full_events": queue_full_events,
            "wait_events": wait_events,
            "total_wait_ms": round(total_wait_seconds * 1000, 3),
            "avg_wait_ms": round((total_wait_seconds / wait_events) * 1000, 3) if wait_events else 0.0,
            "max_wait_ms": round(max_wait_seconds * 1000, 3),
            "last_wait_ms": round(last_wait_seconds * 1000, 3),
        }

    def _record_acquired(self, wait_seconds: float) -> None:
        with self._lock:
            self._in_flight += 1
            self._acquired_events += 1
            self._record_wait_unlocked(wait_seconds)

    def _record_released(self) -> None:
        with self._lock:
            self._in_flight = max(self._in_flight - 1, 0)

    def _record_queue_full(self, wait_seconds: float) -> None:
        with self._lock:
            self._queue_full_events += 1
            self._record_wait_unlocked(wait_seconds)

    def _record_wait_unlocked(self, wait_seconds: float) -> None:
        wait_seconds = max(float(wait_seconds), 0.0)
        self._wait_events += 1
        self._total_wait_seconds += wait_seconds
        self._last_wait_seconds = wait_seconds
        self._max_wait_seconds = max(self._max_wait_seconds, wait_seconds)


class ImageSearchGate(SearchExecutionGate):
    def __init__(self, max_concurrency: int, queue_timeout_seconds: float):
        super().__init__(
            max_concurrency,
            queue_timeout_seconds,
            queue_full_error=ImageSearchQueueFull,
            queue_full_message="image search queue is full",
        )
