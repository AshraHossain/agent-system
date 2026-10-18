"""Data sources. The default is a deterministic simulated feed that behaves like a live one
(`advance()` moves "now" forward). `CsvFeed` loads real readings exported from any system."""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
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
    """Simulated network. Data after `now` exists only as hidden ground truth for backtests.

    `tz`: when set to a `datetime.timezone`, `now` returns timezone-aware datetimes.
    Defaults to `None` (naive datetimes) for backward compatibility.
    """

    def __init__(
        self,
        seed: int = 7,
        now: datetime | None = None,
        days: int = 21,
        tz: timezone | None = None,
    ):
        self._start = datetime(2026, 9, 1)
        self._data = simulate(self._start, days=days, seed=seed)
        self.end = self._data.index[-1]
        self._tz = tz
        self._now = now or datetime(2026, 9, 16, 7, 30)

    @property
    def now(self) -> datetime:
        return self._now.replace(tzinfo=self._tz) if self._tz is not None else self._now

    @now.setter
    def now(self, value: datetime) -> None:
        self._now = value.replace(tzinfo=None) if value.tzinfo is not None else value

    def segments(self) -> list[SegmentInfo]:
        return list(SEGMENTS)

    def _col(self, segment_id: str) -> pd.Series:
        if segment_id not in self._data.columns:
            raise KeyError(segment_id)
        return self._data[segment_id]

    def history(self, segment_id: str, days: float = 7) -> pd.Series:
        s = self._col(segment_id)
        result = s[(s.index <= self._now) & (s.index > self._now - timedelta(days=days))]
        if len(result) > 1:
            gaps = result.index.to_series().diff().iloc[1:]
            gap_threshold = timedelta(minutes=STEP_MINUTES * 2)
            large = gaps[gaps > gap_threshold]
            for ts, gap in large.items():
                warnings.warn(
                    f"history gap at {ts}: {gap} (expected ≤{gap_threshold})",
                    stacklevel=2,
                )
        return result

    def truth(self, segment_id: str, start: datetime, end: datetime) -> pd.Series:
        """Ground truth for evaluation only; never exposed to tools or agents."""
        s = self._col(segment_id)
        return s[(s.index > start) & (s.index <= end)]

    def next_values(self, segment_id: str, steps: int):
        """The next `steps` points after `now` (evaluation only)."""
        s = self._col(segment_id)
        return s[s.index > self._now].iloc[:steps].to_numpy()

    def advance(self, steps: int = 1) -> datetime:
        self._now = min(self._now + timedelta(minutes=STEP_MINUTES * steps), self.end)
        return self.now


class CsvFeed:
    """Wide CSV: `timestamp` + one speed column (km/h) per segment, 5-minute grid.
    Segment ids are the column names; free-flow speed is the 95th percentile of each column.
    Timezone: all-naive or all-aware timestamps are supported; mixed-tz CSVs are not."""

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
