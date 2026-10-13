"""SQLite persistence: LangGraph checkpoints, a hash-chained audit log, and the durable job queue.

One database file holds all three. Several processes on **one host** may open it at once: WAL mode lets readers
proceed during a write, writers queue on SQLite's file lock (``busy_timeout``), every multi-statement change runs in
``BEGIN IMMEDIATE``, and the job queue lives in the database rather than in any process's memory. The file must be on
local disk: SQLite's locking is not safe on network filesystems, and WAL cannot span hosts. Running on more than one
host needs a different database (see docs/operations.md). The file contains incident data and reviewer identities, so
treat it as sensitive.
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
from netpulse.persistence.migrations import migrate

_LANGGRAPH_DELETES = {
    "checkpoints": "DELETE FROM checkpoints WHERE thread_id = ?",
    "writes": "DELETE FROM writes WHERE thread_id = ?",
}
BUSY_TIMEOUT_SECONDS = 30.0  # how long a writer waits for another process's write lock


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
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=BUSY_TIMEOUT_SECONDS)
        os.chmod(self.path, 0o600)  # incident data and reviewer identities; WAL/SHM files inherit this mode
        self.conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
        self.conn.execute("PRAGMA journal_mode=WAL")
        migrate(self.conn)
        self.checkpointer = SqliteSaver(self.conn)
        # One connection is shared by the saver and the audit log. Both must take the saver's lock, otherwise one
        # thread's commit lands inside another's transaction ("cannot commit - no transaction is active").
        self._lock = self.checkpointer.lock

    @property
    def lock(self):
        """Serializes every use of ``conn`` within this process (the job queue shares it)."""
        return self._lock

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
            rows = _audit_rows(self.conn)
        return verify_rows(rows, self._audit_key, expected_head)

    def backup(self, destination: Path | str) -> AuditVerification:
        """Write a consistent online snapshot to ``destination`` and verify the audit chain *in the copy*.

        Safe while the service is running and other processes are writing (SQLite's backup API reads a snapshot).
        Refuses to overwrite. The copy is written beside the target and renamed into place, so a crash never leaves
        a half-written file under the final name. The caller must treat a failed verification as a failed backup.
        """
        dest = Path(destination)
        if dest.exists():
            raise FileExistsError(f"{dest} already exists; refusing to overwrite a backup")
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        partial.unlink(missing_ok=True)
        target = sqlite3.connect(str(partial))
        try:
            with self._lock:
                self.conn.backup(target)
            os.chmod(partial, 0o600)
            result = verify_rows(_audit_rows(target), self._audit_key)
        finally:
            target.close()
        os.replace(partial, dest)
        return result

    def prune(self, before: datetime, *, dry_run: bool = False) -> list[str]:
        """Delete checkpoints and job records of incidents that *finished* before ``before``.

        Never touches the audit log: its hash chain must stay contiguous, and the audit is the long-term record.
        An incident counts as finished only if its most recent audit event is ``run_finished``, so paused,
        running and stalled incidents are never removed. Returns the pruned incident keys.
        """
        cutoff = before.isoformat()
        with self._lock:
            finished = [
                row[0]
                for row in self.conn.execute(
                    "SELECT incident_id FROM netpulse_audit a WHERE event = 'run_finished' AND at < ? "
                    "AND seq = (SELECT MAX(seq) FROM netpulse_audit WHERE incident_id = a.incident_id)",
                    (cutoff,),
                )
            ]
            if dry_run or not finished:
                return sorted(finished)
            tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for table, statement in _LANGGRAPH_DELETES.items():  # LangGraph's tables, created on first use
                    if table in tables:
                        self.conn.executemany(statement, [(k,) for k in finished])
                self.conn.executemany(
                    "DELETE FROM netpulse_jobs WHERE incident_key = ? AND state IN ('done', 'error')",
                    [(k,) for k in finished],
                )
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
        return sorted(finished)

    def audit_log(self, incident_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT seq, event, actor, payload, at FROM netpulse_audit WHERE incident_id = ? ORDER BY seq",
                (incident_id,),
            ).fetchall()
        return [{"seq": r[0], "event": r[1], "actor": r[2], "payload": json.loads(r[3]), "at": r[4]} for r in rows]

    def close(self) -> None:
        self.conn.close()


def _audit_rows(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT seq, incident_id, event, actor, payload, at, prev_hash, row_hash FROM netpulse_audit ORDER BY seq"
    ).fetchall()
