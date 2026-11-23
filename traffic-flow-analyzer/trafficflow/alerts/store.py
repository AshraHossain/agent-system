"""SQLite-backed persistent event store for AlertMonitor.

Keeps every alert event (segment, level, speed, timestamp) in a local SQLite
database so past incidents can be replayed, filtered, and used to compute MTTR.

Usage::

    from trafficflow.alerts.store import AlertStore
    from trafficflow.alerts.monitor import AlertMonitor, AlertConfig, AlertRule
    from trafficflow.contracts import CongestionLevel
    from trafficflow.data.live import MockLiveFeed

    store = AlertStore("var/alerts.db")   # creates the file + schema on first open
    feed = MockLiveFeed()
    rules = [AlertRule("S1", min_level=CongestionLevel.HEAVY)]
    monitor = AlertMonitor(AlertConfig(rules=rules, feed=feed), store=store)
    monitor.run()

    # Later …
    incidents = store.query(segment_id="S1", limit=50)
    mttr = store.mean_time_to_resolve("S1")
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from trafficflow.contracts import CongestionLevel


class AlertStore:
    """Append-only SQLite store for alert events.

    Thread-safe: each call opens a short-lived connection with WAL mode.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS alert_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT    NOT NULL,
        segment_id  TEXT    NOT NULL,
        level       TEXT    NOT NULL,
        speed_kmh   REAL    NOT NULL,
        rule_name   TEXT    NOT NULL,
        meta        TEXT    NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS ix_alert_events_segment
        ON alert_events (segment_id, ts);
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        # In-memory databases live only in a single connection; keep one open.
        self._mem_conn: sqlite3.Connection | None = None
        self._closed = False
        if self._path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.execute("PRAGMA journal_mode=WAL")
        else:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(
        self,
        ts: datetime,
        segment_id: str,
        level: CongestionLevel,
        speed_kmh: float,
        rule_name: str = "",
        meta: dict[str, Any] | None = None,
    ) -> int:
        """Insert one event and return its row id."""
        with self._session() as conn:
            cur = conn.execute(
                "INSERT INTO alert_events (ts, segment_id, level, speed_kmh, rule_name, meta)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ts.isoformat(),
                    segment_id,
                    level.value,
                    speed_kmh,
                    rule_name or segment_id,
                    json.dumps(meta or {}),
                ),
            )
            return int(cur.lastrowid)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def query(
        self,
        segment_id: str | None = None,
        level: CongestionLevel | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return matching rows as dicts, newest-first."""
        clauses: list[str] = []
        params: list[Any] = []
        if segment_id:
            clauses.append("segment_id = ?")
            params.append(segment_id)
        if level:
            clauses.append("level = ?")
            params.append(level.value)
        if since:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("ts <= ?")
            params.append(until.isoformat())
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, limit))
        sql = f"SELECT * FROM alert_events {where} ORDER BY ts DESC LIMIT ?"  # noqa: S608
        with self._session() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count(self, segment_id: str | None = None) -> int:
        where = "WHERE segment_id = ?" if segment_id else ""
        params: list[Any] = [segment_id] if segment_id else []
        sql = f"SELECT COUNT(*) FROM alert_events {where}"  # noqa: S608
        with self._session() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def mean_time_to_resolve(self, segment_id: str) -> float | None:
        """Return mean gap (seconds) between consecutive alert onset→clear pairs.

        An 'onset' is the first event after a gap of ≥ 10 minutes; a 'clear' is
        the timestamp of the next onset (or None if still active).  Returns None
        when fewer than two onsets exist.
        """
        rows = self.query(segment_id=segment_id, limit=10_000)
        if len(rows) < 2:
            return None
        rows.sort(key=lambda r: r["ts"])
        GAP = 600  # 10 minutes in seconds
        onsets: list[datetime] = []
        prev_ts: datetime | None = None
        for r in rows:
            ts = datetime.fromisoformat(r["ts"])
            if prev_ts is None or (ts - prev_ts).total_seconds() >= GAP:
                onsets.append(ts)
            prev_ts = ts
        if len(onsets) < 2:
            return None
        gaps = [(onsets[i + 1] - onsets[i]).total_seconds() for i in range(len(onsets) - 1)]
        return sum(gaps) / len(gaps)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            # Without this an in-memory store would silently reconnect to a fresh,
            # empty ":memory:" database and return wrong answers instead of failing.
            raise sqlite3.ProgrammingError("Cannot operate on a closed AlertStore.")
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextlib.contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection, committing on success and closing if we opened it.

        `sqlite3.Connection.__exit__` commits or rolls back but never closes, so
        using `with self._connect()` directly leaked one open connection per call
        on a file-backed store. The in-memory connection is shared and deliberately
        kept open: closing it would discard the database.
        """
        conn = self._connect()
        borrowed = conn is self._mem_conn
        try:
            with conn:
                yield conn
        finally:
            if not borrowed:
                conn.close()

    def close(self) -> None:
        """Close the store. Idempotent; further use raises `sqlite3.ProgrammingError`.

        Only an in-memory store owns a long-lived connection, so for a file-backed
        store this just marks it closed — each call already closes its own connection.
        """
        if self._mem_conn is not None:
            self._mem_conn.close()
            self._mem_conn = None
        self._closed = True

    def __enter__(self) -> AlertStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _init_schema(self) -> None:
        with self._session() as conn:
            conn.executescript(self._DDL)
