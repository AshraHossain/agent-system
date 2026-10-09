"""Investigator-visible data access.

``DatasetStore`` is the only path from ``netpulse`` to on-disk data. It can
reach the topology, the per-case telemetry, events and maintenance, and the
retrieval corpus. It has no notion of evaluation labels and refuses any root
directory that sits inside a labels tree.

Visibility rule: a telemetry row is visible at ``as_of`` only when
``timestamp + delay_s <= as_of``. Rows that exist in the source but have not
been ingested yet are counted as ``pending``; their values are never returned.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from netpulse.data.schemas import (
    DATASET_ID_PATTERN,
    METRIC_TABLE,
    TABLE_METRICS,
    EntityCoverage,
    EventRecord,
    MaintenanceWindow,
    TelemetryQuery,
    TimeSpan,
)
from netpulse.errors import DataCorruptError, DataNotFoundError, ToolInputError
from netpulse.models import EvidenceItem, EvidenceSource, Metric, TelemetryWindowRef
from netpulse.topology.model import Topology

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "v1"


def default_store() -> DatasetStore:
    return DatasetStore(Path(os.environ.get("NETPULSE_DATA_DIR", DEFAULT_DATA_DIR)))


@dataclass(frozen=True)
class TelemetryWindow:
    """Output of the telemetry-window tool. Frames hold visible rows only (history + window)."""

    query: TelemetryQuery
    frames: dict[str, pd.DataFrame]
    coverage: list[EntityCoverage]
    ref: TelemetryWindowRef

    def series(self, entity_id: str, metric: Metric) -> tuple[pd.Series, pd.Series]:
        frame = self.frames[METRIC_TABLE[metric]]
        rows = frame[frame["entity_id"] == entity_id]
        return rows["timestamp"], rows[metric.value]

    def entities(self, table: str) -> list[str]:
        return sorted(self.frames[table]["entity_id"].unique())


class DatasetStore:
    def __init__(self, root: Path) -> None:
        root = Path(root).resolve()
        if "labels" in root.parts:
            raise ToolInputError("refusing a data root inside a labels directory")
        if not (root / "topology.json").is_file():
            raise DataNotFoundError(f"no topology.json under {root}")
        self.root = root
        self._tables: dict[tuple[str, str], pd.DataFrame] = {}

    # ---------------------------------------------------------------- paths

    def _case_dir(self, dataset_id: str) -> Path:
        if not re.fullmatch(DATASET_ID_PATTERN, dataset_id):
            raise ToolInputError(f"invalid dataset_id {dataset_id!r}")
        cases = (self.root / "cases").resolve()
        path = (cases / dataset_id).resolve()
        if path.parent != cases:
            raise ToolInputError(f"invalid dataset_id {dataset_id!r}")
        if not path.is_dir():
            raise DataNotFoundError(f"dataset {dataset_id!r} not found")
        return path

    @property
    def corpus_dir(self) -> Path:
        return self.root / "corpus"

    def meta(self, dataset_id: str) -> dict:
        return json.loads((self._case_dir(dataset_id) / "meta.json").read_text())

    # ------------------------------------------------------------- topology

    @cached_property
    def topology(self) -> Topology:
        try:
            return Topology.model_validate_json((self.root / "topology.json").read_text())
        except ValueError as exc:
            raise DataCorruptError(f"topology.json invalid: {exc}") from exc

    # ------------------------------------------------------------ telemetry

    def _table(self, dataset_id: str, table: str) -> pd.DataFrame:
        key = (dataset_id, table)
        if key not in self._tables:
            path = self._case_dir(dataset_id) / f"{table}.csv.gz"
            expected = self.meta(dataset_id)["tables"][table]
            try:
                df = pd.read_csv(path)
            except (OSError, ValueError) as exc:
                raise DataCorruptError(f"{path.name}: {exc}") from exc
            if list(df.columns) != expected:
                raise DataCorruptError(f"{path.name}: columns {list(df.columns)} != {expected}")
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df["visible_at"] = df["timestamp"] + pd.to_timedelta(df["delay_s"], unit="s")
            self._tables[key] = df
        return self._tables[key]

    def telemetry_window(self, query: TelemetryQuery) -> TelemetryWindow:
        meta = self.meta(query.dataset_id)
        step = pd.Timedelta(minutes=meta["sample_minutes"])
        start = pd.Timestamp(query.window_start)
        end = pd.Timestamp(query.window_end)
        as_of = pd.Timestamp(query.as_of)
        history_start = start - pd.Timedelta(hours=query.history_hours)
        tables = sorted({METRIC_TABLE[m] for m in query.metrics}) if query.metrics else list(TABLE_METRICS)
        known = self.topology.entity_ids()
        if query.entity_ids:
            unknown = sorted(set(query.entity_ids) - known)
            if unknown:
                raise ToolInputError(f"unknown entities: {unknown}")

        expected_points = int((end - start) / step) + 1
        frames, coverage = {}, []
        visible_rows = 0
        for table in tables:
            df = self._table(query.dataset_id, table)
            if query.entity_ids:
                df = df[df["entity_id"].isin(query.entity_ids)]
            in_range = df[(df["timestamp"] >= history_start) & (df["timestamp"] <= end)]
            visible = in_range[in_range["visible_at"] <= as_of]
            metric_cols = [m.value for m in TABLE_METRICS[table] if not query.metrics or m in query.metrics]
            frames[table] = visible[["timestamp", "entity_id", *metric_cols]].reset_index(drop=True)

            table_entities = self._entities_of(table)
            entities = sorted(e for e in (query.entity_ids or table_entities) if e in table_entities)
            for entity in entities:
                rows = in_range[(in_range["entity_id"] == entity) & (in_range["timestamp"] >= start)]
                seen = rows[rows["visible_at"] <= as_of]["timestamp"]
                visible_rows += len(seen)
                coverage.append(
                    EntityCoverage(
                        entity_id=entity,
                        table=table,
                        expected_points=expected_points,
                        visible_points=len(seen),
                        pending_points=int((rows["visible_at"] > as_of).sum()),
                        coverage_ratio=min(1.0, len(seen) / expected_points),
                        gaps=_gaps(seen, start, end, step),
                    )
                )
        ref = TelemetryWindowRef(
            dataset_id=query.dataset_id,
            start=query.window_start,
            end=query.window_end,
            entity_ids=sorted({c.entity_id for c in coverage}),
            metrics=list(query.metrics or [m for t in tables for m in TABLE_METRICS[t]]),
            row_count=visible_rows,
            expected_row_count=expected_points * len(coverage),
            entities_without_data=sorted(c.entity_id for c in coverage if c.visible_points == 0),
        )
        return TelemetryWindow(query=query, frames=frames, coverage=coverage, ref=ref)

    def _entities_of(self, table: str) -> set[str]:
        topo = self.topology
        if table == "links":
            return {ln.link_id for ln in topo.links}
        if table == "nodes":
            return {n.node_id for n in topo.nodes if n.monitored}
        return {s.service_id for s in topo.services}

    # ------------------------------------------------------ events/maintenance

    def events(self, dataset_id: str, start: datetime, end: datetime, as_of: datetime) -> list[EventRecord]:
        raw = json.loads((self._case_dir(dataset_id) / "events.json").read_text())
        records = [EventRecord(**e) for e in raw]
        return [r for r in records if start <= r.timestamp <= end and r.timestamp <= as_of]

    def maintenance(self, dataset_id: str) -> list[MaintenanceWindow]:
        raw = json.loads((self._case_dir(dataset_id) / "maintenance.json").read_text())
        return [MaintenanceWindow(**m) for m in raw]


def _gaps(seen: pd.Series, start: pd.Timestamp, end: pd.Timestamp, step: pd.Timedelta) -> list[TimeSpan]:
    """Missing stretches inside [start, end] given the visible timestamps (computed in UTC nanoseconds)."""
    points = np.sort(pd.DatetimeIndex(seen).as_unit("ns").asi8)
    edges = np.concatenate([[start.value - step.value], points, [end.value + step.value]])
    out = []
    for prev, nxt in zip(edges[:-1], edges[1:], strict=True):
        if nxt - prev > step.value * 1.5:
            out.append(
                TimeSpan(
                    start=pd.Timestamp(int(prev + step.value), tz="UTC").to_pydatetime(),
                    end=pd.Timestamp(int(nxt - step.value), tz="UTC").to_pydatetime(),
                )
            )
    return out


def coverage_evidence(cov: EntityCoverage, evidence_id: str) -> EvidenceItem:
    """Data-quality evidence: what is missing, never what the missing values might have been."""
    gaps = ", ".join(f"{g.start:%H:%M}–{g.end:%H:%M}" for g in cov.gaps) or "none"
    pending = f"; {cov.pending_points} samples exist but were not yet ingested (delayed)" if cov.pending_points else ""
    return EvidenceItem(
        evidence_id=evidence_id,
        source=EvidenceSource.DATA_QUALITY,
        summary=(
            f"Telemetry coverage for {cov.entity_id} ({cov.table}): {cov.visible_points}/{cov.expected_points} "
            f"samples visible ({cov.coverage_ratio:.0%}); gaps UTC: {gaps}{pending}. "
            "Missing data is not evidence of health or of failure."
        )[:600],
        entity_ids=[cov.entity_id],
        window_start=cov.gaps[0].start if cov.gaps else None,
        window_end=cov.gaps[-1].end if cov.gaps else None,
        method="coverage",
    )
