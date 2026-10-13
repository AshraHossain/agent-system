"""Operating the database: versioned migrations, concurrent startup, online backup, retention."""

import sqlite3
import stat
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta

import pytest

from netpulse.cli import main as cli_main
from netpulse.persistence.migrations import SCHEMA_VERSION, SchemaTooNewError, migrate, schema_version
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService
from tests.graph_helpers import CASES, FIXED_NOW, shared_deps

KEY = "k" * 40


def tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


# ---------------------------------------------------------------- migrations


def test_a_new_database_is_created_at_the_current_schema_version(tmp_path):
    store = PersistentStore(tmp_path / "a.db", audit_key=KEY)
    assert schema_version(store.conn) == SCHEMA_VERSION
    assert {"netpulse_audit", "netpulse_jobs"} <= tables(store.conn)


def test_migrating_again_changes_nothing(tmp_path):
    store = PersistentStore(tmp_path / "a.db", audit_key=KEY)
    assert migrate(store.conn) == 0
    PersistentStore(tmp_path / "a.db", audit_key=KEY)  # reopening is a no-op too


def test_a_database_from_before_versioning_is_upgraded_and_keeps_its_audit_rows(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)  # what the first releases created: no chain columns, no version, no jobs table
    conn.execute(
        "CREATE TABLE netpulse_audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, "
        "event TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO netpulse_audit (incident_id, event, actor, payload, at) VALUES ('i', 'old', 'x', '{}', 't')"
    )
    conn.commit()
    assert schema_version(conn) == 0
    conn.close()
    store = PersistentStore(path, audit_key=KEY)
    store.audit("i", "new", "alice", {}, FIXED_NOW)
    assert schema_version(store.conn) == SCHEMA_VERSION and "netpulse_jobs" in tables(store.conn)
    result = store.verify_audit()
    assert result.ok and result.legacy_rows == 1 and result.rows == 2


def test_a_database_from_a_newer_release_is_refused_not_guessed_at(tmp_path):
    path = tmp_path / "future.db"
    PersistentStore(path, audit_key=KEY).conn.close()
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaTooNewError, match="newer than this NetPulse supports"):
        PersistentStore(path, audit_key=KEY)


def test_a_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    import netpulse.persistence.migrations as m

    def explode(conn):
        conn.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("boom")

    monkeypatch.setattr(m, "MIGRATIONS", [*m.MIGRATIONS, explode])
    monkeypatch.setattr(m, "SCHEMA_VERSION", len(m.MIGRATIONS))
    conn = sqlite3.connect(tmp_path / "r.db")
    with pytest.raises(RuntimeError, match="boom"):
        m.migrate(conn)
    assert schema_version(conn) == 0 and "half_done" not in tables(conn) and "netpulse_audit" not in tables(conn)


def test_many_processes_starting_on_a_fresh_database_do_not_collide(tmp_path):
    """Rolling restarts and `uvicorn --workers N` open the same new file at the same moment."""
    db = tmp_path / "race.db"
    script = textwrap.dedent(
        """
        import sys
        from netpulse.persistence.store import PersistentStore
        store = PersistentStore(sys.argv[1], audit_key="k" * 40)
        print(store.conn.execute("PRAGMA user_version").fetchone()[0])
        """
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", script, str(db)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: S603
        for _ in range(8)
    ]
    results = [p.communicate(timeout=120) + (p.returncode,) for p in procs]
    assert [r[2] for r in results] == [0] * 8, [r[1].decode()[-300:] for r in results if r[2]]
    assert {r[0].decode().strip() for r in results} == {str(SCHEMA_VERSION)}


# ---------------------------------------------------------------- backup


def test_backup_is_a_consistent_verified_owner_only_copy(tmp_path):
    store = PersistentStore(tmp_path / "live.db", audit_key=KEY)
    for i in range(20):
        store.audit(f"inc-{i}", "tick", "alice", {"n": i}, FIXED_NOW)
    result = store.backup(tmp_path / "backups" / "snap.db")
    assert result.ok and result.rows == 20
    copy = tmp_path / "backups" / "snap.db"
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    restored = PersistentStore(copy, audit_key=KEY)
    assert restored.verify_audit().ok and restored.verify_audit().head == store.verify_audit().head
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_backup_never_overwrites(tmp_path):
    store = PersistentStore(tmp_path / "live.db", audit_key=KEY)
    store.backup(tmp_path / "snap.db")
    with pytest.raises(FileExistsError):
        store.backup(tmp_path / "snap.db")


def test_backup_reports_a_tampered_audit_chain_instead_of_blessing_it(tmp_path):
    store = PersistentStore(tmp_path / "live.db", audit_key=KEY)
    store.audit("inc-1", "tick", "alice", {}, FIXED_NOW)
    store.audit("inc-1", "tick", "alice", {}, FIXED_NOW)
    store.conn.execute("DROP TRIGGER netpulse_audit_no_update")
    store.conn.execute("UPDATE netpulse_audit SET actor = 'mallory' WHERE seq = 1")
    store.conn.commit()
    result = store.backup(tmp_path / "snap.db")
    assert not result.ok and result.first_bad_seq == 1


def test_backup_taken_while_another_process_writes_is_consistent(tmp_path):
    db = tmp_path / "busy.db"
    store = PersistentStore(db, audit_key=KEY)
    writer = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            "import sys, time\nfrom datetime import UTC, datetime\n"
            "from netpulse.persistence.store import PersistentStore\n"
            "s = PersistentStore(sys.argv[1], audit_key='k' * 40)\n"
            "for i in range(300):\n    s.audit('w', 'tick', 'bg', {'i': i}, datetime.now(UTC))\n",
            str(db),
        ]
    )
    try:
        results = [store.backup(tmp_path / f"snap-{i}.db") for i in range(5)]
    finally:
        assert writer.wait(timeout=120) == 0
    assert all(r.ok for r in results)  # every snapshot, taken mid-write, has an unbroken chain
    assert store.verify_audit().rows == 300


def test_cli_backup_and_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NETPULSE_AUDIT_KEY", KEY)
    db = tmp_path / "cli.db"
    PersistentStore(db).audit("i", "tick", "a", {}, FIXED_NOW)
    assert cli_main(["--db", str(db), "backup", "--to", str(tmp_path / "b.db")]) == 0
    assert "audit chain verified in the copy" in capsys.readouterr().out
    assert cli_main(["--db", str(db), "backup", "--to", str(tmp_path / "b.db")]) == 2  # exists


# ---------------------------------------------------------------- retention


@pytest.fixture
def populated(tmp_path):
    store = PersistentStore(tmp_path / "ret.db", audit_key=KEY)
    service = InvestigationService(shared_deps(), store)
    finished = CASES["case-02"]  # completes without needing a reviewer
    paused = CASES["case-04"]  # waits for approval
    service.start(finished["submission"], submitted_at=finished["submitted_at"], incident_id="done-1", submitted_by="a")
    service.start(paused["submission"], submitted_at=paused["submitted_at"], incident_id="paused-1", submitted_by="a")
    assert service.status("done-1").status == "completed" and service.status("paused-1").status == "awaiting_approval"
    store.conn.execute(
        "INSERT INTO netpulse_jobs (incident_key, kind, payload, tenant, state, enqueued_at, finished_at) "
        "VALUES ('done-1', 'start', '{}', '', 'done', 1, 2)"
    )
    store.conn.commit()
    return store, service


def checkpoint_rows(store, thread) -> int:
    return store.conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (thread,)).fetchone()[0]


def test_prune_removes_finished_incidents_data_and_keeps_everything_else(populated):
    store, service = populated
    audit_before = store.verify_audit()
    assert checkpoint_rows(store, "done-1") > 0 and checkpoint_rows(store, "paused-1") > 0
    pruned = store.prune(FIXED_NOW + timedelta(days=1))
    assert pruned == ["done-1"]
    assert checkpoint_rows(store, "done-1") == 0
    assert store.conn.execute("SELECT COUNT(*) FROM netpulse_jobs WHERE incident_key = 'done-1'").fetchone()[0] == 0
    assert checkpoint_rows(store, "paused-1") > 0  # waiting for a reviewer: never pruned
    assert service.status("paused-1").pending_review is not None
    after = store.verify_audit()
    assert after.ok and after.rows == audit_before.rows and after.head == audit_before.head  # audit untouched


def test_prune_respects_the_age_cutoff_and_dry_run(populated):
    store, _ = populated
    assert store.prune(FIXED_NOW - timedelta(days=1)) == []  # finished at FIXED_NOW: not old enough yet
    assert store.prune(FIXED_NOW + timedelta(days=1), dry_run=True) == ["done-1"]
    assert checkpoint_rows(store, "done-1") > 0  # dry run deleted nothing


def test_a_pruned_incident_is_reported_not_found_rather_than_crashing(populated):
    from netpulse.errors import DataNotFoundError

    store, service = populated
    store.prune(FIXED_NOW + timedelta(days=1))
    with pytest.raises(DataNotFoundError):
        service.status("done-1")


def test_cli_prune_dry_run_then_real(populated, monkeypatch, capsys):
    store, _ = populated
    monkeypatch.setenv("NETPULSE_AUDIT_KEY", KEY)
    days = (datetime.now(UTC) - FIXED_NOW).days - 1  # FIXED_NOW is about to look old
    assert days > 0
    assert cli_main(["--db", str(store.path), "prune", "--older-than-days", str(days), "--dry-run"]) == 0
    assert "would remove" in capsys.readouterr().out and checkpoint_rows(store, "done-1") > 0
    assert cli_main(["--db", str(store.path), "prune", "--older-than-days", str(days)]) == 0
    assert "never pruned" in capsys.readouterr().out and checkpoint_rows(store, "done-1") == 0
