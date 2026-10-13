"""Audit log integrity: a keyed hash chain makes rewriting, deleting, inserting and reordering rows detectable."""

import sqlite3
import subprocess
import sys
import textwrap
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from netpulse.api.app import create_app
from netpulse.api.auth import TokenStore
from netpulse.cli import main as cli_main
from netpulse.persistence.audit_chain import GENESIS, row_hash
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService
from tests.graph_helpers import shared_deps

KEY = "k" * 40
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_store(tmp_path, n=5, key=KEY, name="a.db") -> PersistentStore:
    store = PersistentStore(tmp_path / name, audit_key=key)
    for i in range(n):
        store.audit(f"inc-{i % 2}", "tick", "alice", {"n": i}, NOW)
    return store


def raw(store: PersistentStore) -> sqlite3.Connection:
    """A second connection with the append-only triggers removed, as an attacker with file access would have."""
    store.conn.commit()
    conn = sqlite3.connect(store.path)
    conn.execute("DROP TRIGGER IF EXISTS netpulse_audit_no_update")
    conn.execute("DROP TRIGGER IF EXISTS netpulse_audit_no_delete")
    return conn


def test_untouched_chain_verifies_and_exposes_a_head(tmp_path):
    result = make_store(tmp_path).verify_audit()
    assert result.ok and result.rows == 5 and result.keyed and result.legacy_rows == 0
    assert result.head.startswith("5:")


def test_empty_log_verifies(tmp_path):
    assert PersistentStore(tmp_path / "e.db", audit_key=KEY).verify_audit().ok


def test_rewriting_a_row_is_detected(tmp_path):
    store = make_store(tmp_path)
    db = raw(store)
    db.execute("UPDATE netpulse_audit SET actor = 'mallory' WHERE seq = 2")
    db.commit()
    result = store.verify_audit()
    assert not result.ok and result.first_bad_seq == 2


def test_deleting_a_middle_row_is_detected(tmp_path):
    store = make_store(tmp_path)
    db = raw(store)
    db.execute("DELETE FROM netpulse_audit WHERE seq = 3")
    db.commit()
    result = store.verify_audit()
    assert not result.ok and "missing" in result.error


def test_deleting_the_first_row_is_detected(tmp_path):
    store = make_store(tmp_path)
    db = raw(store)
    db.execute("DELETE FROM netpulse_audit WHERE seq = 1")
    db.commit()
    assert not store.verify_audit().ok


def test_inserting_a_forged_row_is_detected(tmp_path):
    store = make_store(tmp_path)
    db = raw(store)
    db.execute(
        "INSERT INTO netpulse_audit (seq, incident_id, event, actor, payload, at, prev_hash, row_hash) "
        "VALUES (6, 'inc-0', 'review_submitted', 'mallory', '{}', 'x', ?, ?)",
        (store.verify_audit().head_hash, "f" * 64),
    )
    db.commit()
    assert not store.verify_audit().ok


def test_attacker_who_recomputes_the_whole_chain_without_the_key_is_still_detected(tmp_path):
    """The point of the key: rewrite a row, then recompute every later hash with plain SHA-256."""
    store = make_store(tmp_path)
    db = raw(store)
    db.execute("UPDATE netpulse_audit SET actor = 'mallory' WHERE seq = 2")
    prev = db.execute("SELECT row_hash FROM netpulse_audit WHERE seq = 1").fetchone()[0]
    for seq, inc, event, actor, payload, at in db.execute(
        "SELECT seq, incident_id, event, actor, payload, at FROM netpulse_audit WHERE seq >= 2 ORDER BY seq"
    ).fetchall():
        digest = row_hash(None, seq, inc, event, actor, payload, at, prev)
        db.execute("UPDATE netpulse_audit SET prev_hash = ?, row_hash = ? WHERE seq = ?", (prev, digest, seq))
        prev = digest
    db.commit()
    assert not store.verify_audit().ok


def test_unkeyed_chain_catches_naive_edits_but_not_a_recomputed_chain(tmp_path):
    store = make_store(tmp_path, key=None, name="u.db")
    assert store.verify_audit().ok and not store.verify_audit().keyed
    db = raw(store)
    db.execute("UPDATE netpulse_audit SET actor = 'mallory' WHERE seq = 5")
    db.commit()
    assert not store.verify_audit().ok  # naive edit caught
    prev = db.execute("SELECT prev_hash FROM netpulse_audit WHERE seq = 5").fetchone()[0]
    inc, event, actor, payload, at = db.execute(
        "SELECT incident_id, event, actor, payload, at FROM netpulse_audit WHERE seq = 5"
    ).fetchone()
    db.execute(
        "UPDATE netpulse_audit SET row_hash = ? WHERE seq = 5",
        (row_hash(None, 5, inc, event, actor, payload, at, prev),),
    )
    db.commit()
    assert store.verify_audit().ok  # documented limitation of unkeyed mode


def test_wrong_key_fails_verification(tmp_path):
    make_store(tmp_path)
    other = PersistentStore(tmp_path / "a.db", audit_key="z" * 40)
    assert not other.verify_audit().ok


def test_tail_truncation_needs_a_recorded_head(tmp_path):
    store = make_store(tmp_path)
    head = store.verify_audit().head
    db = raw(store)
    db.execute("DELETE FROM netpulse_audit WHERE seq IN (4, 5)")
    db.commit()
    assert store.verify_audit().ok  # a shorter chain is internally consistent: truncation is invisible alone
    result = store.verify_audit(expected_head=head)
    assert not result.ok and "truncated" in result.error


def test_recorded_head_still_matches_after_honest_appends(tmp_path):
    store = make_store(tmp_path, n=3)
    head = store.verify_audit().head
    store.audit("inc-0", "more", "bob", {}, NOW)
    assert store.verify_audit(expected_head=head).ok


def test_a_rewritten_row_at_the_recorded_head_is_detected(tmp_path):
    store = make_store(tmp_path, n=3)
    seq = 3
    assert not store.verify_audit(expected_head=f"{seq}:{'0' * 64}").ok
    assert not store.verify_audit(expected_head="garbage").ok


def test_database_created_before_the_chain_existed_is_migrated(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE netpulse_audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, "
        "event TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO netpulse_audit (incident_id, event, actor, payload, at) VALUES ('i', 'old', 'x', '{}', 't')",
        [()] * 2,
    )
    conn.commit()
    conn.close()
    store = PersistentStore(path, audit_key=KEY)
    store.audit("i", "new", "alice", {}, NOW)
    store.audit("i", "newer", "alice", {}, NOW)
    result = store.verify_audit()
    assert result.ok and result.legacy_rows == 2 and result.rows == 4
    assert store.conn.execute("SELECT prev_hash FROM netpulse_audit WHERE seq = 3").fetchone()[0] == GENESIS


def test_concurrent_writers_in_separate_processes_cannot_fork_the_chain(tmp_path):
    """The CLI can append while the API is running: both must serialize on the database write lock."""
    db = tmp_path / "multi.db"
    PersistentStore(db, audit_key=KEY)
    script = textwrap.dedent(
        """
        import sys
        from datetime import UTC, datetime
        from netpulse.persistence.store import PersistentStore
        store = PersistentStore(sys.argv[1], audit_key=sys.argv[2])
        for i in range(40):
            store.audit(f"inc-{sys.argv[3]}", "tick", f"proc-{sys.argv[3]}", {"i": i}, datetime.now(UTC))
        """
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, str(db), KEY, str(n)]) for n in range(3)]  # noqa: S603
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0]
    result = PersistentStore(db, audit_key=KEY).verify_audit()
    assert result.ok and result.rows == 120


def test_key_must_be_long_enough_and_can_come_from_the_environment(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="at least 32"):
        PersistentStore(tmp_path / "s.db", audit_key="short")
    monkeypatch.setenv("NETPULSE_AUDIT_KEY", "e" * 40)
    assert PersistentStore(tmp_path / "env.db").keyed


# ------------------------------------------------------------------ service, API and CLI


@pytest.fixture
def api(tmp_path):
    service = InvestigationService(shared_deps(), PersistentStore(tmp_path / "api.db", audit_key=KEY))
    tokens = TokenStore.parse("op-token-0123456789:operator:olga,sr-token-0123456789:senior_operator:sam")
    with TestClient(create_app(service, tokens, max_workers=1)) as client:
        client.service = service
        yield client


SENIOR = {"Authorization": "Bearer sr-token-0123456789"}
OPERATOR = {"Authorization": "Bearer op-token-0123456789"}


def test_health_reports_whether_the_audit_chain_is_keyed(api):
    assert api.get("/health").json()["audit_keyed"] is True


def test_audit_verify_endpoint_is_senior_only_and_reports_tampering(api, tmp_path):
    api.service.store.audit("inc-x", "submitted", "olga", {}, NOW)
    assert api.get("/audit/verify").status_code == 401
    assert api.get("/audit/verify", headers=OPERATOR).status_code == 403
    ok = api.get("/audit/verify", headers=SENIOR).json()
    assert ok["ok"] and ok["rows"] == 1 and ok["keyed"] and ok["head"].startswith("1:")
    db = raw(api.service.store)
    db.execute("UPDATE netpulse_audit SET actor = 'mallory'")
    db.commit()
    bad = api.get("/audit/verify", headers=SENIOR).json()
    assert bad["ok"] is False and bad["first_bad_seq"] == 1
    assert api.get("/audit/verify", params={"expected_head": "nonsense"}, headers=SENIOR).json()["ok"] is False


def test_real_investigation_produces_a_verifiable_trail_with_the_token_identity(api):
    import time

    from tests.graph_helpers import CASES

    case = CASES["case-04"]
    api.post("/incidents", headers=OPERATOR, json={"submission": case["submission"], "incident_id": "inc-chain"})
    for _ in range(200):
        if api.get("/incidents/inc-chain", headers=OPERATOR).json()["status"] == "awaiting_approval":
            break
        time.sleep(0.2)
    api.post("/incidents/inc-chain/approval", headers=SENIOR, json={"choice": "approve"})
    for _ in range(200):
        if api.get("/incidents/inc-chain", headers=OPERATOR).json()["status"] == "completed":
            break
        time.sleep(0.2)
    actors = [(e["event"], e["actor"]) for e in api.service.audit_log("inc-chain")]
    assert ("submitted", "olga") in actors and ("review_submitted", "sam") in actors
    assert api.get("/audit/verify", headers=SENIOR).json()["ok"] is True


def test_api_refuses_to_start_without_an_audit_key(monkeypatch, tmp_path):
    from netpulse.api.main import build_app

    monkeypatch.setenv("NETPULSE_API_TOKENS", "0123456789abcdef-token:operator:olga")
    monkeypatch.setenv("NETPULSE_DB", str(tmp_path / "x.db"))
    monkeypatch.delenv("NETPULSE_AUDIT_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NETPULSE_AUDIT_KEY"):
        build_app()


def test_cli_audit_verify_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NETPULSE_AUDIT_KEY", KEY)
    store = make_store(tmp_path, name="cli.db")
    db_path = str(store.path)
    assert cli_main(["--db", db_path, "audit-verify"]) == 0
    head = store.verify_audit().head
    assert cli_main(["--db", db_path, "audit-verify", "--expect-head", head]) == 0
    db = raw(store)
    db.execute("DELETE FROM netpulse_audit WHERE seq = 5")
    db.commit()
    assert cli_main(["--db", db_path, "audit-verify", "--expect-head", head]) == 1
    db.execute("DROP TRIGGER IF EXISTS netpulse_audit_no_update")  # reopening the store recreated it
    db.execute("UPDATE netpulse_audit SET actor = 'mallory' WHERE seq = 1")
    db.commit()
    assert cli_main(["--db", db_path, "audit-verify"]) == 1
    assert "FAILED" in capsys.readouterr().out
