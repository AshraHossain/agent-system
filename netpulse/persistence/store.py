"""SQLite persistence for checkpoints (LangGraph ``SqliteSaver``) and an append-only audit log.

One database file holds both. SQLite allows a single writer at a time, so one
process should own the file (see docs/human_approval.md and ADR-0004). The
database contains incident data and reviewer identities, so treat it as
sensitive.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS netpulse_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS netpulse_audit_incident ON netpulse_audit (incident_id, seq);
CREATE TRIGGER IF NOT EXISTS netpulse_audit_no_update BEFORE UPDATE ON netpulse_audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS netpulse_audit_no_delete BEFORE DELETE ON netpulse_audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


class PersistentStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(AUDIT_SCHEMA)
        self._lock = threading.Lock()
        self.checkpointer = SqliteSaver(self.conn)

    def audit(self, incident_id: str, event: str, actor: str, payload: dict[str, Any], at: datetime) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO netpulse_audit (incident_id, event, actor, payload, at) VALUES (?, ?, ?, ?, ?)",
                (incident_id, event, actor, json.dumps(payload, sort_keys=True, default=str), at.isoformat()),
            )

    def audit_log(self, incident_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT seq, event, actor, payload, at FROM netpulse_audit WHERE incident_id = ? ORDER BY seq",
            (incident_id,),
        ).fetchall()
        return [{"seq": r[0], "event": r[1], "actor": r[2], "payload": json.loads(r[3]), "at": r[4]} for r in rows]

    def close(self) -> None:
        self.conn.close()
