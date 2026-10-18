"""Replay of real recorded speeds as a pseudo-live feed, with held-back ground truth.

Supports PeMSD7 (Caltrans PeMS, 228 sensors, 44 weekdays of May-June 2012, 5-minute speeds).
The file is headerless and has no timestamps, so timestamps are RECONSTRUCTED assuming the 44
weekdays starting 2012-05-01 at 288 slots/day. Speeds are assumed to be mph (PeMS convention)
and are converted to km/h. Both are assumptions, not facts recorded in the file."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

from trafficflow.contracts import SegmentInfo
from trafficflow.core.simulate import SLOTS_PER_DAY, STEP_MINUTES

MPH_TO_KMH = 1.609344
PEMSD7_START = "2012-05-01"
PEMSD7_MEMBER = "PeMSD7_V_228.csv"
# Fixed HTTPS location of a public research mirror; content is pinned by SHA-256 below.
PEMSD7_URL = (
    "https://raw.githubusercontent.com/VeritasYin/STGCN_IJCAI-18/master/dataset/PeMSD7_Full.zip"
)
PEMSD7_ZIP_SHA256 = "79e03f4b3054ceb19b6c6488b78b2bc8884e4bfaabd9d7266ad5933332fb1580"
WARMUP_DAYS = 7  # recorded days used to set free-flow speeds and as minimum history


def fetch_pemsd7(dest: str | Path) -> Path:
    """Download the PeMSD7 zip, verify its SHA-256, and extract only the 228-sensor speed file."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / PEMSD7_MEMBER
    if out.exists():
        return out
    with urlopen(PEMSD7_URL, timeout=120) as resp:
        blob = resp.read()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != PEMSD7_ZIP_SHA256:
        raise ValueError(f"unexpected PeMSD7 archive hash {digest}")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        out.write_bytes(zf.read(PEMSD7_MEMBER))  # read one named member; no path extraction
    return out


def load_pemsd7(path: str | Path, sensors: int | None = None) -> pd.DataFrame:
    """Speeds (km/h), weekday-only 5-minute index, columns P000... (original column numbers).

    `sensors` keeps an evenly spaced deterministic subset of that many columns."""
    raw = pd.read_csv(path, header=None)
    if len(raw) % SLOTS_PER_DAY:
        raise ValueError(f"{len(raw)} rows is not a whole number of {SLOTS_PER_DAY}-slot days")
    days = pd.bdate_range(PEMSD7_START, periods=len(raw) // SLOTS_PER_DAY)
    slots = pd.timedelta_range(0, periods=SLOTS_PER_DAY, freq=f"{STEP_MINUTES}min")
    index = pd.DatetimeIndex([d + s for d in days for s in slots])
    cols = list(range(raw.shape[1]))
    if sensors is not None and sensors < len(cols):
        cols = sorted({round(float(i)) for i in np.linspace(0, len(cols) - 1, sensors)})
    df = raw.iloc[:, cols] * MPH_TO_KMH
    df.columns = [f"P{c:03d}" for c in cols]
    df.index = index
    return df


class ReplayFeed:
    """Replays a recorded table. `now` moves through recorded points only (weekend gaps are
    skipped), history is counted in recorded points, and the future is held back as truth."""

    def __init__(self, data: pd.DataFrame, start: datetime | None = None):
        if len(data) <= WARMUP_DAYS * SLOTS_PER_DAY:
            raise ValueError("need more than 7 recorded days")
        self._data = data
        warm = data.iloc[: WARMUP_DAYS * SLOTS_PER_DAY]
        self._segments = [
            SegmentInfo(
                id=c, name=f"Sensor {c[1:]} (PeMSD7)", free_flow_kmh=float(warm[c].quantile(0.95))
            )
            for c in data.columns
        ]
        first = WARMUP_DAYS * SLOTS_PER_DAY
        self._pos = first if start is None else max(first, int(data.index.get_loc(start)))

    @property
    def now(self) -> datetime:
        return self._data.index[self._pos].to_pydatetime()

    @now.setter
    def now(self, ts: datetime) -> None:
        self._pos = int(self._data.index.get_loc(pd.Timestamp(ts)))

    @property
    def index(self) -> pd.DatetimeIndex:
        return self._data.index

    def segments(self) -> list[SegmentInfo]:
        return list(self._segments)

    def history(self, segment_id: str, days: float = 7) -> pd.Series:
        n = int(days * SLOTS_PER_DAY)
        return self._data[segment_id].iloc[max(0, self._pos + 1 - n) : self._pos + 1]

    def truth(self, segment_id: str, start: datetime, end: datetime) -> pd.Series:
        """Held-back future; for evaluation only, never exposed to tools or agents."""
        s = self._data[segment_id]
        return s[(s.index > pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]

    def next_values(self, segment_id: str, steps: int) -> np.ndarray:
        """The next `steps` recorded points after `now` (evaluation only)."""
        return self._data[segment_id].iloc[self._pos + 1 : self._pos + 1 + steps].to_numpy()

    def advance(self, steps: int = 1) -> datetime:
        self._pos = min(self._pos + steps, len(self._data) - 1)
        return self.now

    def origins(self, times: list[str], max_steps: int) -> list[datetime]:
        """Evaluation origins: every recorded day after warm-up at the given HH:MM times, where
        at least `max_steps` later points exist and are contiguous in time (no weekend gap)."""
        idx = self._data.index
        out = []
        for pos in range(WARMUP_DAYS * SLOTS_PER_DAY, len(idx) - max_steps):
            if idx[pos].strftime("%H:%M") not in times:
                continue
            if idx[pos + max_steps] - idx[pos] == pd.Timedelta(minutes=STEP_MINUTES * max_steps):
                out.append(idx[pos].to_pydatetime())
        return out
