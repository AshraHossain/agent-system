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

from netpulse.errors import ToolError
from netpulse.observability import get_logger

log = get_logger("api")

JobState = Literal["queued", "running", "done", "error"]


MAX_PENDING = 100  # queued + running jobs; beyond this new work is refused with 429
MAX_PENDING_PER_GROUP = 25  # per tenant, so one tenant cannot fill the shared queue
MAX_FINISHED = 500  # finished job records kept for status; the checkpoint is the source of truth after that


class JobRegistry:
    def __init__(
        self,
        max_workers: int = 4,
        max_pending: int = MAX_PENDING,
        max_finished: int = MAX_FINISHED,
        max_pending_per_group: int = MAX_PENDING_PER_GROUP,
    ) -> None:
        self._max_pending = max_pending
        self._max_per_group = max_pending_per_group
        self._groups: dict[str, str] = {}
        self._max_finished = max_finished
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="netpulse-api")
        self._jobs: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, incident_id: str, fn: Callable[[], None], group: str = "") -> None:
        with self._lock:
            existing = self._jobs.get(incident_id)
            if existing is not None and not existing.done():
                raise JobInFlightError(f"an operation is already in progress for incident {incident_id!r}")
            pending = [k for k, f in self._jobs.items() if not f.done()]
            if len(pending) >= self._max_pending:
                raise JobQueueFullError("too many investigations are queued; retry later")
            if group and sum(self._groups.get(k) == group for k in pending) >= self._max_per_group:
                raise JobQueueFullError("your organization has too many investigations queued; retry later")
            self._groups[incident_id] = group
            self._jobs.pop(incident_id, None)  # re-insert so insertion order tracks recency
            self._jobs[incident_id] = self._pool.submit(self._logged, incident_id, fn)
            self._prune()

    def _prune(self) -> None:
        finished = [k for k, f in self._jobs.items() if f.done()]
        for key in finished[: max(0, len(finished) - self._max_finished)]:
            del self._jobs[key]
            self._groups.pop(key, None)

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
        if exc is None:
            return None
        # Expected tool errors are written for operators; anything else may carry internals (SQL, paths).
        return f"{type(exc).__name__}: {exc}" if isinstance(exc, ToolError) else "internal error; see the server log"

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=False)


class JobInFlightError(Exception):
    """Raised when a second operation is requested for an incident that already has one running."""


class JobQueueFullError(Exception):
    """Raised when too many jobs are already queued or running."""
