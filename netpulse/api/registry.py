"""Background execution of investigations.

Each mutating operation (start, decide, resume) runs in a worker thread and
the HTTP handler returns immediately with a ``queued``/``running`` status;
the caller polls ``GET /incidents/{id}``. At most one operation per
incident runs at a time — a second request for the same incident while one
is in flight is refused (409), rather than racing on the same checkpoint
thread.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Literal

from netpulse.observability import get_logger

log = get_logger("api")

JobState = Literal["queued", "running", "done", "error"]


class JobRegistry:
    def __init__(self, max_workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="netpulse-api")
        self._jobs: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, incident_id: str, fn: Callable[[], None]) -> None:
        with self._lock:
            existing = self._jobs.get(incident_id)
            if existing is not None and not existing.done():
                raise JobInFlightError(f"an operation is already in progress for incident {incident_id!r}")
            self._jobs[incident_id] = self._pool.submit(self._logged, incident_id, fn)

    @staticmethod
    def _logged(incident_id: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:
            log.error("background job failed", extra={"incident_id": incident_id, "error_type": type(exc).__name__})
            raise

    def state(self, incident_id: str) -> JobState | None:
        future = self._jobs.get(incident_id)
        if future is None:
            return None
        if not future.done():
            return "running" if future.running() else "queued"
        return "error" if future.exception() else "done"

    def error(self, incident_id: str) -> str | None:
        future = self._jobs.get(incident_id)
        if future is None or not future.done():
            return None
        exc = future.exception()
        return f"{type(exc).__name__}: {exc}" if exc else None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=False)


class JobInFlightError(Exception):
    """Raised when a second operation is requested for an incident that already has one running."""
