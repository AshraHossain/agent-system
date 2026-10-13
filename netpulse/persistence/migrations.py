"""Versioned schema migrations for NetPulse's own SQLite tables.

LangGraph's ``SqliteSaver`` manages its checkpoint tables itself; everything else (the audit log, the job queue) is
versioned here with ``PRAGMA user_version``. Migrations run inside ``BEGIN IMMEDIATE``, which takes the write lock
first, so several processes starting at once cannot apply the same migration twice: the loser waits, re-reads the
version and finds nothing to do. A database written by a *newer* NetPulse is refused rather than guessed at.

To change the schema, append a function to ``MIGRATIONS``. Never edit one that has shipped.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


class SchemaTooNewError(RuntimeError):
    """The database was migrated by a newer NetPulse than this one."""


def _v1_audit(conn: sqlite3.Connection) -> None:
    """Audit log with its hash-chain columns. Idempotent: databases from before versioning already have it."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS netpulse_audit ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, event TEXT NOT NULL, "
        "actor TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL, prev_hash TEXT, row_hash TEXT)"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(netpulse_audit)")}
    for column in ("prev_hash", "row_hash"):  # tables created before the hash chain existed
        if column not in columns:
            conn.execute(f"ALTER TABLE netpulse_audit ADD COLUMN {column} TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS netpulse_audit_incident ON netpulse_audit (incident_id, seq)")
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS netpulse_audit_no_update BEFORE UPDATE ON netpulse_audit "
        "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS netpulse_audit_no_delete BEFORE DELETE ON netpulse_audit "
        "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
    )


def _v2_jobs(conn: sqlite3.Connection) -> None:
    """Durable job queue shared by every API process on the host."""
    conn.execute(
        "CREATE TABLE netpulse_jobs ("
        "job_id INTEGER PRIMARY KEY AUTOINCREMENT, incident_key TEXT NOT NULL, kind TEXT NOT NULL, "
        "payload TEXT NOT NULL, tenant TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
        "error TEXT, enqueued_at REAL NOT NULL, started_at REAL, finished_at REAL, "
        "lease_owner TEXT, lease_expires REAL)"
    )
    # The database, not any one process, guarantees one active operation per incident.
    conn.execute(
        "CREATE UNIQUE INDEX netpulse_jobs_one_active ON netpulse_jobs (incident_key) "
        "WHERE state IN ('queued', 'running')"
    )
    conn.execute("CREATE INDEX netpulse_jobs_latest ON netpulse_jobs (incident_key, job_id)")
    conn.execute("CREATE INDEX netpulse_jobs_claim ON netpulse_jobs (state, job_id)")


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_v1_audit, _v2_jobs]
SCHEMA_VERSION = len(MIGRATIONS)


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Bring ``conn``'s database to the current schema. Returns the number of migrations applied."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = schema_version(conn)
        if version > SCHEMA_VERSION:
            raise SchemaTooNewError(
                f"database schema version {version} is newer than this NetPulse supports ({SCHEMA_VERSION}); "
                "upgrade NetPulse or restore an older backup"
            )
        for index in range(version, SCHEMA_VERSION):
            MIGRATIONS[index](conn)
            conn.execute(f"PRAGMA user_version = {index + 1}")
        conn.commit()
        return SCHEMA_VERSION - version
    except BaseException:
        conn.rollback()
        raise
