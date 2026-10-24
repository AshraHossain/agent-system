"""SQLite store of generated briefings, so the dashboard history survives restarts."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from trafficflow.contracts import TrafficReport


class ReportStore:
    _DDL = """
    CREATE TABLE IF NOT EXISTS reports (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        as_of    TEXT NOT NULL,
        status   TEXT NOT NULL,
        label    TEXT NOT NULL,
        summary  TEXT NOT NULL,
        report   TEXT NOT NULL
    );
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by Gradio's worker threads, serialised by a lock.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(self._DDL)

    def save(self, report: TrafficReport, summary: str) -> str:
        label = f"{report.as_of:%Y-%m-%d %H:%M} — {report.status.value}"
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO reports (as_of, status, label, summary, report) VALUES (?,?,?,?,?)",
                (
                    report.as_of.isoformat(),
                    report.status.value,
                    label,
                    summary,
                    report.model_dump_json(),
                ),
            )
        return label

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Newest `limit` reports, returned oldest-first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, as_of, status, label, summary FROM reports ORDER BY id DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def summary(self, report_id: int) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT summary FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
        return row["summary"] if row else ""

    def clear(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM reports")
