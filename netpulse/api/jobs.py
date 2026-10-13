"""Durable background jobs shared by every API process on a host.

Each mutating operation (start, decide, resume) becomes a row in ``netpulse_jobs``; worker threads in each process claim
rows, run them, and record the outcome. Because the queue lives in the database rather than in memory:

* **Every process sees the same state.** ``GET /incidents/{id}`` reports the same ``job_state`` whichever
  worker answers.
* **One active job per incident is enforced by the database** (a partial unique index), atomically across processes.
* **Work survives restarts.** A job still queued when a process exits is simply picked up by the next worker.
* **A crashed worker's job is recovered.** A worker holds a lease on its job and renews it while running. If the process
  dies, the lease expires and another worker claims the job again (``attempt`` > 1), up to ``max_attempts`` times.

Claiming and submitting use ``BEGIN IMMEDIATE`` so that two processes cannot take the same job or exceed the pending
caps between a check and an insert. SQLite must be on local disk (see ``netpulse.persistence.store``). A job that
*raises* is recorded as failed and not retried; only a lost worker triggers a retry.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Literal

from netpulse.errors import ToolError
from netpulse.observability import get_logger

log = get_logger("api")

JobState = Literal["queued", "running", "done", "error"]
Handler = Callable[[str, dict[str, Any], int], None]  # (incident key, payload, attempt number)

MAX_PENDING = 100  # queued + running jobs across all processes; beyond this new work is refused with 429
MAX_PENDING_PER_TENANT = 25  # so one tenant cannot fill the shared queue


class JobInFlightError(Exception):
    """Raised when a second operation is requested for an incident that already has one active."""


class JobQueueFullError(Exception):
    """Raised when too many jobs are already queued or running."""


def public_error(exc: BaseException) -> str:
    """Expected tool errors are written for operators; anything else may carry internals (SQL, paths)."""
    return f"{type(exc).__name__}: {exc}" if isinstance(exc, ToolError) else "internal error; see the server log"


class JobQueue:
    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.Lock,
        handlers: dict[str, Handler],
        *,
        workers: int = 4,
        lease_seconds: float = 60.0,
        poll_seconds: float = 0.25,
        max_pending: int = MAX_PENDING,
        max_pending_per_tenant: int = MAX_PENDING_PER_TENANT,
        max_attempts: int = 3,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.conn, self._lock, self.handlers = conn, lock, handlers
        self.workers, self.lease, self.poll = workers, lease_seconds, poll_seconds
        self.max_pending, self.max_per_tenant, self.max_attempts = max_pending, max_pending_per_tenant, max_attempts
        self._clock = clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._running: dict[int, str] = {}  # job_id -> lease owner, for the heartbeat
        self._guard = threading.Lock()  # protects _threads and _running

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        with self._guard:
            if self._threads and any(t.is_alive() for t in self._threads):
                return
            self._stop.clear()
            tag = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"
            self._threads = [
                threading.Thread(target=self._work, args=(f"{tag}-{i}",), name=f"netpulse-job-{i}", daemon=True)
                for i in range(self.workers)
            ]
            self._threads.append(threading.Thread(target=self._heartbeat, name="netpulse-job-lease", daemon=True))
            for thread in self._threads:
                thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        """Stop claiming new jobs and wait (up to ``timeout``) for running ones. Unfinished work is recovered later."""
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._guard:
            self._threads = [t for t in self._threads if t.is_alive()]

    # --------------------------------------------------------------- public

    def submit(self, key: str, kind: str, payload: dict[str, Any], tenant: str = "") -> None:
        if kind not in self.handlers:
            raise ValueError(f"unknown job kind {kind!r}")
        self.start()
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")  # the capacity checks and the insert must not interleave
            try:
                if self.conn.execute(
                    "SELECT 1 FROM netpulse_jobs WHERE incident_key = ? AND state IN ('queued', 'running')", (key,)
                ).fetchone():
                    raise JobInFlightError(f"an operation is already in progress for incident {key!r}")
                pending, mine = self.conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(tenant = ?), 0) FROM netpulse_jobs "
                    "WHERE state IN ('queued', 'running')",
                    (tenant,),
                ).fetchone()
                if pending >= self.max_pending:
                    raise JobQueueFullError("too many investigations are queued; retry later")
                if tenant and mine >= self.max_per_tenant:
                    raise JobQueueFullError("your organization has too many investigations queued; retry later")
                self.conn.execute(
                    "INSERT INTO netpulse_jobs (incident_key, kind, payload, tenant, state, enqueued_at) "
                    "VALUES (?, ?, ?, ?, 'queued', ?)",
                    (key, kind, json.dumps(payload, sort_keys=True, default=str), tenant, self._clock()),
                )
                self.conn.commit()
            except sqlite3.IntegrityError as exc:  # another process won the race for this incident
                self.conn.rollback()
                raise JobInFlightError(f"an operation is already in progress for incident {key!r}") from exc
            except BaseException:
                self.conn.rollback()
                raise
        self._wake.set()

    def state(self, key: str) -> JobState | None:
        row = self._latest(key)
        return row[0] if row else None

    def error(self, key: str) -> str | None:
        row = self._latest(key)
        return row[1] if row and row[0] == "error" else None

    def pending(self) -> int:
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM netpulse_jobs WHERE state IN ('queued', 'running')"
            ).fetchone()[0]

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """Block until no job is queued or running anywhere. For tests and orderly shutdown."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.pending() == 0:
                return True
            time.sleep(0.05)
        return False

    # ------------------------------------------------------------- internals

    def _latest(self, key: str) -> tuple[str, str | None] | None:
        with self._lock:
            return self.conn.execute(
                "SELECT state, error FROM netpulse_jobs WHERE incident_key = ? ORDER BY job_id DESC LIMIT 1", (key,)
            ).fetchone()

    def _claimable(self, now: float) -> bool:
        with self._lock:  # a plain read: idle workers must not take the write lock just to look
            return (
                self.conn.execute(
                    "SELECT 1 FROM netpulse_jobs WHERE state = 'queued' OR (state = 'running' AND lease_expires < ?) "
                    "LIMIT 1",
                    (now,),
                ).fetchone()
                is not None
            )

    def _claim(self, owner: str) -> tuple[int, str, str, dict[str, Any], int] | None:
        now = self._clock()
        if not self._claimable(now):
            return None
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    "SELECT job_id, incident_key, kind, payload, attempts, state FROM netpulse_jobs "
                    "WHERE state = 'queued' OR (state = 'running' AND lease_expires < ?) ORDER BY job_id LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    self.conn.commit()
                    return None
                job_id, key, kind, payload, attempts, state = row
                if state == "running" and attempts >= self.max_attempts:
                    self.conn.execute(
                        "UPDATE netpulse_jobs SET state = 'error', error = ?, finished_at = ?, lease_owner = NULL "
                        "WHERE job_id = ?",
                        (f"abandoned after {attempts} attempts (the worker running it was lost)", now, job_id),
                    )
                    self.conn.commit()
                    log.error("job abandoned", extra={"incident_id": key, "attempts": attempts})
                    return None
                self.conn.execute(
                    "UPDATE netpulse_jobs SET state = 'running', attempts = attempts + 1, lease_owner = ?, "
                    "lease_expires = ?, started_at = COALESCE(started_at, ?) WHERE job_id = ?",
                    (owner, now + self.lease, now, job_id),
                )
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
        if state == "running":
            log.warning("recovering a job whose worker was lost", extra={"incident_id": key, "attempt": attempts + 1})
        return job_id, key, kind, json.loads(payload), attempts + 1

    def _work(self, owner: str) -> None:
        while not self._stop.is_set():
            try:
                job = self._claim(owner)
            except Exception as exc:  # noqa: BLE001 - a database hiccup must not kill the worker
                log.error("job claim failed", extra={"error_type": type(exc).__name__})
                self._stop.wait(self.poll)
                continue
            if job is None:
                self._wake.wait(self.poll)
                self._wake.clear()
                continue
            self._run(owner, *job)

    def _run(self, owner: str, job_id: int, key: str, kind: str, payload: dict[str, Any], attempt: int) -> None:
        with self._guard:
            self._running[job_id] = owner
        state, error = "done", None
        try:
            handler = self.handlers.get(kind)
            if handler is None:
                raise ValueError(f"no handler for job kind {kind!r}")
            handler(key, payload, attempt)
        except Exception as exc:  # noqa: BLE001 - recorded, never raised into the worker loop
            log.error("background job failed", extra={"incident_id": key, "error_type": type(exc).__name__})
            state, error = "error", public_error(exc)
        finally:
            with self._guard:
                self._running.pop(job_id, None)
        self._finish(job_id, owner, state, error)

    def _finish(self, job_id: int, owner: str, state: str, error: str | None) -> None:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self.conn.execute(
                    "UPDATE netpulse_jobs SET state = ?, error = ?, finished_at = ?, lease_owner = NULL "
                    "WHERE job_id = ? AND lease_owner = ?",
                    (state, error, self._clock(), job_id, owner),
                )
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
        if cur.rowcount == 0:  # our lease expired and another worker took the job over; its outcome stands
            log.warning("job finished after its lease was taken over", extra={"job_id": job_id})

    def _heartbeat(self) -> None:
        interval = max(0.05, self.lease / 3)
        while not self._stop.wait(interval):
            with self._guard:
                running = dict(self._running)
            if not running:
                continue
            try:
                with self._lock:
                    self.conn.execute("BEGIN IMMEDIATE")
                    try:
                        for job_id, owner in running.items():
                            self.conn.execute(
                                "UPDATE netpulse_jobs SET lease_expires = ? WHERE job_id = ? AND lease_owner = ?",
                                (self._clock() + self.lease, job_id, owner),
                            )
                        self.conn.commit()
                    except BaseException:
                        self.conn.rollback()
                        raise
            except Exception as exc:  # noqa: BLE001
                log.error("lease renewal failed", extra={"error_type": type(exc).__name__})
