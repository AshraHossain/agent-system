"""The durable job queue: shared state, atomic one-job-per-incident, crash recovery, caps."""

import sqlite3
import threading
import time

import pytest

from netpulse.api.jobs import JobInFlightError, JobQueue, JobQueueFullError, public_error
from netpulse.errors import ToolInputError
from netpulse.persistence.store import PersistentStore

KEY = "k" * 40


def make_queue(path, handlers=None, **options) -> tuple[JobQueue, PersistentStore]:
    """Each call opens its own connection to the file, as a separate process would."""
    store = PersistentStore(path, audit_key=KEY)
    options.setdefault("workers", 1)
    options.setdefault("poll_seconds", 0.02)
    return JobQueue(store.conn, store.lock, handlers or {"work": lambda key, payload, attempt: None}, **options), store


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def db(tmp_path):
    return tmp_path / "jobs.db"


# ---------------------------------------------------------------- basics


def test_a_submitted_job_runs_and_records_its_outcome(db):
    ran = []
    queue, _ = make_queue(db, {"work": lambda key, payload, attempt: ran.append((key, payload, attempt))})
    queue.submit("inc-1", "work", {"x": 1}, tenant="acme")
    assert queue.wait_idle(10) and ran == [("inc-1", {"x": 1}, 1)]
    assert queue.state("inc-1") == "done" and queue.error("inc-1") is None
    queue.stop()


def test_failures_are_recorded_with_internals_hidden_and_not_retried(db):
    calls = []

    def boom(key, payload, attempt):
        calls.append(attempt)
        raise RuntimeError("sqlite3 path /var/lib/secret.db is locked")

    def refuse(key, payload, attempt):
        raise ToolInputError("the window is in the future")

    queue, _ = make_queue(db, {"boom": boom, "refuse": refuse})
    queue.submit("a", "boom", {})
    queue.submit("b", "refuse", {})
    assert queue.wait_idle(10)
    assert queue.state("a") == "error" and queue.error("a") == "internal error; see the server log"
    assert queue.error("b") == "ToolInputError: the window is in the future"  # written for operators, so shown
    assert calls == [1]  # an exception is a result, not a reason to retry
    queue.stop()


def test_public_error_only_exposes_expected_errors():
    assert "secret" not in public_error(RuntimeError("secret"))
    assert public_error(ToolInputError("bad window")) == "ToolInputError: bad window"


def test_unknown_job_kind_is_rejected_at_submit(db):
    queue, _ = make_queue(db)
    with pytest.raises(ValueError, match="unknown job kind"):
        queue.submit("a", "nope", {})


def test_status_after_a_job_finishes_reports_the_latest_job_for_that_incident(db):
    queue, _ = make_queue(db)
    queue.submit("a", "work", {})
    assert queue.wait_idle(10)
    queue.submit("a", "work", {})  # a later operation on the same incident
    assert queue.wait_idle(10)
    assert queue.state("a") == "done"
    assert queue.state("never-seen") is None
    queue.stop()


# ---------------------------------------------------------------- shared between processes


def test_another_process_sees_the_same_state_and_error(db):
    """The baseline defect: job_state depended on which worker process answered."""
    gate = threading.Event()
    a, _ = make_queue(db, {"work": lambda key, payload, attempt: gate.wait(10)})
    b, _ = make_queue(db, workers=0)  # a different process: its own connection, no workers of its own
    a.submit("inc-1", "work", {})
    assert wait_for(lambda: b.state("inc-1") == "running")
    assert b.pending() == 1
    gate.set()
    assert a.wait_idle(10) and b.state("inc-1") == "done"
    a.stop()


def test_simultaneous_submissions_from_many_processes_are_accepted_exactly_once(db):
    """The baseline defect: 8 identical simultaneous submissions were accepted three times with 3 workers."""
    queues = [make_queue(db, workers=0)[0] for _ in range(6)]
    for round_no in range(20):
        key, outcomes, barrier = f"inc-{round_no}", [], threading.Barrier(len(queues))

        def attempt(queue, key=key, outcomes=outcomes, barrier=barrier):
            barrier.wait()
            try:
                queue.submit(key, "work", {})
                outcomes.append("accepted")
            except JobInFlightError:
                outcomes.append("refused")

        threads = [threading.Thread(target=attempt, args=(q,)) for q in queues]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert outcomes.count("accepted") == 1 and outcomes.count("refused") == len(queues) - 1, (round_no, outcomes)


def test_each_job_runs_exactly_once_even_with_several_competing_processes(db):
    ran, lock = [], threading.Lock()

    def record(key, payload, attempt):
        with lock:
            ran.append(key)
        time.sleep(0.01)

    queues = [make_queue(db, {"work": record}, workers=2)[0] for _ in range(3)]
    for i in range(40):
        queues[i % 3].submit(f"inc-{i}", "work", {})
    assert queues[0].wait_idle(30)
    assert sorted(ran) == sorted(f"inc-{i}" for i in range(40))
    for q in queues:
        q.stop()


def test_queued_work_survives_a_restart(db):
    ran = []
    idle, _ = make_queue(db, {"work": lambda key, payload, attempt: ran.append(key)}, workers=0)
    idle.submit("inc-1", "work", {})  # accepted, but this "process" never gets to run it
    idle.stop()
    assert ran == []
    revived, _ = make_queue(db, {"work": lambda key, payload, attempt: ran.append(key)})
    revived.start()
    assert revived.wait_idle(10) and ran == ["inc-1"]
    revived.stop()


# ---------------------------------------------------------------- caps


def test_pending_caps_apply_across_processes_and_per_tenant(db):
    gate = threading.Event()
    handlers = {"work": lambda key, payload, attempt: gate.wait(10)}
    a, _ = make_queue(db, handlers, workers=0, max_pending=5, max_pending_per_tenant=2)
    b, _ = make_queue(db, handlers, workers=0, max_pending=5, max_pending_per_tenant=2)
    a.submit("a1", "work", {}, tenant="acme")
    b.submit("a2", "work", {}, tenant="acme")
    with pytest.raises(JobQueueFullError, match="organization"):
        a.submit("a3", "work", {}, tenant="acme")  # acme's two slots are taken, by two different processes
    b.submit("g1", "work", {}, tenant="globex")  # another tenant is unaffected
    a.submit("g2", "work", {}, tenant="globex")
    a.submit("x1", "work", {}, tenant="initech")
    with pytest.raises(JobQueueFullError, match="too many investigations"):
        b.submit("x2", "work", {}, tenant="initech")  # 5 pending overall
    gate.set()


def test_in_flight_guard_clears_when_the_job_finishes(db):
    queue, _ = make_queue(db)
    queue.submit("a", "work", {})
    assert queue.wait_idle(10)
    queue.submit("a", "work", {})  # no longer in flight: allowed again
    assert queue.wait_idle(10)
    queue.stop()


# ---------------------------------------------------------------- crash recovery


def lose_worker(db, key="inc-1", attempts=1, expired=True):
    """Leave a job 'running' under a worker that no longer exists, as a killed process would."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO netpulse_jobs (incident_key, kind, payload, tenant, state, attempts, enqueued_at, started_at, "
        "lease_owner, lease_expires) VALUES (?, 'work', '{}', '', 'running', ?, ?, ?, 'dead-worker', ?)",
        (key, attempts, time.time() - 100, time.time() - 90, time.time() - 1 if expired else time.time() + 3600),
    )
    conn.commit()
    conn.close()


def test_a_job_whose_worker_died_is_taken_over_as_a_later_attempt(db):
    PersistentStore(db, audit_key=KEY)  # create the schema
    lose_worker(db)
    seen = []
    queue, _ = make_queue(db, {"work": lambda key, payload, attempt: seen.append(attempt)})
    queue.start()
    assert queue.wait_idle(10) and seen == [2]
    assert queue.state("inc-1") == "done"
    queue.stop()


def test_a_job_that_keeps_losing_its_worker_is_abandoned_with_a_visible_error(db):
    PersistentStore(db, audit_key=KEY)
    lose_worker(db, attempts=3)
    seen = []
    queue, _ = make_queue(db, {"work": lambda key, payload, attempt: seen.append(attempt)}, max_attempts=3)
    queue.start()
    assert wait_for(lambda: queue.state("inc-1") == "error")
    assert seen == [] and "abandoned after 3 attempts" in queue.error("inc-1")
    queue.stop()


def test_a_live_workers_lease_is_not_taken_over(db):
    PersistentStore(db, audit_key=KEY)
    lose_worker(db, expired=False)  # lease still valid
    seen = []
    queue, _ = make_queue(db, {"work": lambda key, payload, attempt: seen.append(attempt)})
    queue.start()
    time.sleep(0.3)
    assert seen == [] and queue.state("inc-1") == "running"
    queue.stop()


def test_the_heartbeat_keeps_a_long_job_from_being_stolen(db):
    runs, lock = [], threading.Lock()

    def slow(key, payload, attempt):
        with lock:
            runs.append(attempt)
        time.sleep(1.5)  # far longer than the 0.4 s lease

    owner, _ = make_queue(db, {"work": slow}, lease_seconds=0.4)
    rival, _ = make_queue(db, {"work": slow}, lease_seconds=0.4)
    rival.start()
    owner.submit("inc-1", "work", {})
    assert owner.wait_idle(15)
    assert runs == [1]  # the rival polled constantly but the renewed lease held
    owner.stop()
    rival.stop()


def test_stop_waits_for_the_running_job_to_finish(db):
    finished = []

    def work(key, payload, attempt):
        time.sleep(0.4)
        finished.append(key)

    queue, _ = make_queue(db, {"work": work})
    queue.submit("inc-1", "work", {})
    assert wait_for(lambda: queue.state("inc-1") == "running")
    queue.stop(timeout=10)
    assert finished == ["inc-1"] and queue.state("inc-1") == "done"
