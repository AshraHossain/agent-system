"""SQLite persistence for checkpoints (LangGraph ``SqliteSaver``) and an append-only audit log.

One database file holds both. SQLite allows a single writer at a time, so one
process should own the file (see docs/human_approval.md and ADR-0004). The
database contains incident data and reviewer identities, so treat it as
sensitive.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from netpulse.persistence.audit_chain import GENESIS, MIN_KEY_LENGTH, AuditVerification, row_hash, verify_rows

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS netpulse_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    at TEXT NOT NULL,
    prev_hash TEXT,
    row_hash TEXT
);
CREATE INDEX IF NOT EXISTS netpulse_audit_incident ON netpulse_audit (incident_id, seq);
CREATE TRIGGER IF NOT EXISTS netpulse_audit_no_update BEFORE UPDATE ON netpulse_audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS netpulse_audit_no_delete BEFORE DELETE ON netpulse_audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def _resolve_key(audit_key: bytes | str | None) -> bytes | None:
    if audit_key is None:
        audit_key = os.environ.get("NETPULSE_AUDIT_KEY") or None
    if audit_key is None:
        return None
    raw = audit_key.encode() if isinstance(audit_key, str) else audit_key
    if len(raw) < MIN_KEY_LENGTH:
        raise ValueError(f"the audit key must be at least {MIN_KEY_LENGTH} characters")
    return raw


class PersistentStore:
    """Checkpoints plus a hash-chained audit log (see ``audit_chain``).

    ``audit_key`` (or ``NETPULSE_AUDIT_KEY``) keys the chain. Without one the chain is plain SHA-256 and ``keyed``
    is False: it catches accidents and naive edits, not an attacker who can rewrite the file.
    """

    def __init__(self, path: Path | str, audit_key: bytes | str | None = None) -> None:
        self._audit_key = _resolve_key(audit_key)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        os.chmod(self.path, 0o600)  # incident data and reviewer identities; WAL/SHM files inherit this mode
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(AUDIT_SCHEMA)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(netpulse_audit)")}
        for column in ("prev_hash", "row_hash"):  # databases created before the chain existed
            if column not in columns:
                self.conn.execute(f"ALTER TABLE netpulse_audit ADD COLUMN {column} TEXT")
        self.conn.commit()
        self.checkpointer = SqliteSaver(self.conn)
        # One connection is shared by the saver and the audit log. Both must take the saver's lock, otherwise one
        # thread's commit lands inside another's transaction ("cannot commit - no transaction is active").
        self._lock = self.checkpointer.lock

    @property
    def keyed(self) -> bool:
        return self._audit_key is not None

    def audit(self, incident_id: str, event: str, actor: str, payload: dict[str, Any], at: datetime) -> None:
        payload_text = json.dumps(payload, sort_keys=True, default=str)
        with self._lock:
            # BEGIN IMMEDIATE takes the write lock before the chain head is read, so another process (for example
            # the CLI) cannot append between our read and our insert and fork the chain.
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                last = self.conn.execute(
                    "SELECT seq, row_hash FROM netpulse_audit ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                seq = (last[0] + 1) if last else 1
                prev = last[1] if last and last[1] else GENESIS
                digest = row_hash(self._audit_key, seq, incident_id, event, actor, payload_text, at.isoformat(), prev)
                self.conn.execute(
                    "INSERT INTO netpulse_audit (seq, incident_id, event, actor, payload, at, prev_hash, row_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (seq, incident_id, event, actor, payload_text, at.isoformat(), prev, digest),
                )
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def verify_audit(self, expected_head: str | None = None) -> AuditVerification:
        """Verify the whole chain. Pass a previously recorded head (``seq:hash``) to also detect tail truncation."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT seq, incident_id, event, actor, payload, at, prev_hash, row_hash "
                "FROM netpulse_audit ORDER BY seq"
            ).fetchall()
        return verify_rows(rows, self._audit_key, expected_head)

    def audit_log(self, incident_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT seq, event, actor, payload, at FROM netpulse_audit WHERE incident_id = ? ORDER BY seq",
                (incident_id,),
            ).fetchall()
        return [{"seq": r[0], "event": r[1], "actor": r[2], "payload": json.loads(r[3]), "at": r[4]} for r in rows]

    def close(self) -> None:
        self.conn.close()
