"""Read-only access to a synthetic dataset (one SQLite file per dataset).

Connections are opened with SQLite's `mode=ro` URI flag, so even a bug in a
tool cannot modify the data. Dataset IDs are validated to prevent path
traversal.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

import pandas as pd

from opspilot.core.errors import DataUnavailable, InvalidArgument, NotFound

DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE components (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, redundancy_group TEXT, tier INTEGER,
  interfaces TEXT NOT NULL DEFAULT '[]', endpoints TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE edges (src TEXT NOT NULL, dst TEXT NOT NULL, grp TEXT, confidence TEXT NOT NULL);
CREATE TABLE telemetry (ts TEXT NOT NULL, entity_id TEXT NOT NULL, metric TEXT NOT NULL,
  value REAL NOT NULL);
CREATE INDEX ix_tel ON telemetry (entity_id, metric, ts);
CREATE TABLE documents (
  doc_id TEXT PRIMARY KEY, doc_type TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
  updated TEXT NOT NULL, superseded_by TEXT, supersedes TEXT, components TEXT NOT NULL,
  tags TEXT NOT NULL, root_cause_category TEXT, body TEXT NOT NULL
);
"""


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fmt_ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_dataset_id(dataset_id: str) -> str:
    if not isinstance(dataset_id, str) or not DATASET_ID_RE.match(dataset_id):
        raise InvalidArgument(f"invalid dataset_id {dataset_id!r}")
    return dataset_id


@dataclass(frozen=True)
class Document:
    doc_id: str
    doc_type: str
    title: str
    status: str
    updated: str
    superseded_by: str | None
    supersedes: str | None
    components: list[str]
    tags: list[str]
    root_cause_category: str | None
    body: str


class Dataset:
    def __init__(self, path: Path, dataset_id: str):
        self.path = path
        self.dataset_id = dataset_id

    def _connect(self) -> sqlite3.Connection:
        try:
            return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        except sqlite3.Error as exc:  # pragma: no cover - depends on filesystem
            raise DataUnavailable(f"dataset {self.dataset_id} unavailable: {exc}") from exc

    def _rows(self, sql: str, params: Iterable = ()) -> list[tuple]:
        con = self._connect()
        try:
            return con.execute(sql, tuple(params)).fetchall()
        except sqlite3.Error as exc:
            raise DataUnavailable(f"query failed on dataset {self.dataset_id}: {exc}") from exc
        finally:
            con.close()

    @cached_property
    def meta(self) -> dict[str, str]:
        return dict(self._rows("SELECT key, value FROM meta"))

    @property
    def reported_at(self) -> datetime:
        return parse_ts(self.meta["reported_at"])

    @property
    def interval_min(self) -> int:
        return int(self.meta.get("sample_interval_min", "5"))

    @cached_property
    def components(self) -> dict[str, dict]:
        out = {}
        for cid, ctype, grp, tier, ifaces, endpoints in self._rows(
            "SELECT id, type, redundancy_group, tier, interfaces, endpoints FROM components"
        ):
            out[cid] = {
                "id": cid,
                "type": ctype,
                "redundancy_group": grp,
                "tier": tier,
                "interfaces": json.loads(ifaces),
                "endpoints": json.loads(endpoints),
            }
        return out

    @cached_property
    def edges(self) -> list[tuple[str, str, str | None, str]]:
        return [tuple(r) for r in self._rows("SELECT src, dst, grp, confidence FROM edges")]

    def telemetry(
        self,
        entity_ids: list[str] | None,
        metrics: list[str] | None,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        sql = "SELECT ts, entity_id, metric, value FROM telemetry WHERE ts >= ? AND ts < ?"
        params: list = [fmt_ts(start), fmt_ts(end)]
        if entity_ids:
            sql += f" AND entity_id IN ({','.join('?' * len(entity_ids))})"
            params += entity_ids
        if metrics:
            sql += f" AND metric IN ({','.join('?' * len(metrics))})"
            params += metrics
        con = self._connect()
        try:
            return pd.read_sql_query(sql, con, params=params)
        except (sqlite3.Error, pd.errors.DatabaseError) as exc:
            raise DataUnavailable(f"telemetry query failed: {exc}") from exc
        finally:
            con.close()

    def metric_catalog(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for entity, metric in self._rows(
            "SELECT DISTINCT entity_id, metric FROM telemetry ORDER BY entity_id, metric"
        ):
            out.setdefault(entity, []).append(metric)
        return out

    @cached_property
    def documents(self) -> list[Document]:
        rows = self._rows(
            "SELECT doc_id, doc_type, title, status, updated, superseded_by, supersedes,"
            " components, tags, root_cause_category, body FROM documents ORDER BY doc_id"
        )
        return [
            Document(
                r[0],
                r[1],
                r[2],
                r[3],
                r[4],
                r[5],
                r[6],
                json.loads(r[7]),
                json.loads(r[8]),
                r[9],
                r[10],
            )
            for r in rows
        ]


def open_dataset(dataset_id: str, data_dir: Path) -> Dataset:
    validate_dataset_id(dataset_id)
    path = (data_dir / f"{dataset_id}.db").resolve()
    if path.parent != data_dir.resolve():
        raise InvalidArgument("dataset path escapes the data directory")
    if not path.exists():
        raise NotFound(f"dataset {dataset_id} not found (run `opspilot build-data`)")
    return Dataset(path, dataset_id)
