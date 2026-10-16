"""Data sources. The default is a deterministic simulated feed that behaves like a live one
(`advance()` moves "now" forward). `CsvFeed` loads real readings exported from any system."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import pandas as pd

from trafficflow.contracts import SegmentInfo
from trafficflow.core.simulate import SEGMENTS, STEP_MINUTES, simulate


class Feed(Protocol):
    now: datetime

    def segments(self) -> list[SegmentInfo]: ...
    def history(self, segment_id: str, days: float = 7) -> pd.Series: ...
    def advance(self, steps: int = 1) -> datetime: ...


class SyntheticFeed:
    """Simulated network. Data after `now` exists only as hidden ground truth for backtests."""

    def __init__(self, seed: int = 7, now: datetime | None = None, days: int = 21):
        self._start = datetime(2026, 9, 1)
        self._data = simulate(self._start, days=days, seed=seed)
        self.end = self._data.index[-1]
        self.now = now or datetime(2026, 9, 16, 7, 30)

    def segments(self) -> list[SegmentInfo]:
        return list(SEGMENTS)

    def _col(self, segment_id: str) -> pd.Series:
        if segment_id not in self._data.columns:
            raise KeyError(segment_id)
        return self._data[segment_id]

    def history(self, segment_id: str, days: float = 7) -> pd.Series:
        s = self._col(segment_id)
        return s[(s.index <= self.now) & (s.index > self.now - timedelta(days=days))]

    def truth(self, segment_id: str, start: datetime, end: datetime) -> pd.Series:
        """Ground truth for evaluation only; never exposed to tools or agents."""
        s = self._col(segment_id)
        return s[(s.index > start) & (s.index <= end)]

    def advance(self, steps: int = 1) -> datetime:
        self.now = min(self.now + timedelta(minutes=STEP_MINUTES * steps), self.end)
        return self.now


class CsvFeed:
    """Wide CSV: `timestamp` + one speed column (km/h) per segment, 5-minute grid.
    Segment ids are the column names; free-flow speed is the 95th percentile of each column."""

    def __init__(self, path: str | Path):
        df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp").sort_index()
        self._data = df.asfreq(f"{STEP_MINUTES}min").interpolate(limit=6)
        self.now = self._data.index[-1].to_pydatetime()
        self._end = self.now

    def segments(self) -> list[SegmentInfo]:
        return [
            SegmentInfo(id=c, name=c, free_flow_kmh=float(self._data[c].quantile(0.95)))
            for c in self._data.columns
        ]

    def history(self, segment_id: str, days: float = 7) -> pd.Series:
        s = self._data[segment_id].dropna()
        return s[(s.index <= self.now) & (s.index > self.now - timedelta(days=days))]

    def advance(self, steps: int = 1) -> datetime:
        return self.now  # a static file has no future; re-load the file to refresh
