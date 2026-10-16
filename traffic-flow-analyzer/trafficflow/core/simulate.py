"""Deterministic synthetic network: daily rush-hour demand, weekend shape, noise and incidents."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from trafficflow.contracts import SegmentInfo

STEP_MINUTES = 5
SLOTS_PER_DAY = 24 * 60 // STEP_MINUTES

SEGMENTS: tuple[SegmentInfo, ...] = (
    SegmentInfo(id="S1", name="Harbor Bridge (inbound)", free_flow_kmh=80),
    SegmentInfo(id="S2", name="Ring Road North", free_flow_kmh=90),
    SegmentInfo(id="S3", name="Central Avenue", free_flow_kmh=50),
    SegmentInfo(id="S4", name="Airport Link", free_flow_kmh=100),
    SegmentInfo(id="S5", name="University Corridor", free_flow_kmh=60),
    SegmentInfo(id="S6", name="Riverside Drive", free_flow_kmh=70),
)

# (am_weight, pm_weight, midday_weight) per segment: inbound roads load in the morning, etc.
_PROFILE = {
    "S1": (1.0, 0.5, 0.15),
    "S2": (0.8, 0.9, 0.2),
    "S3": (0.7, 0.8, 0.5),
    "S4": (0.3, 0.4, 0.45),
    "S5": (0.9, 0.7, 0.3),
    "S6": (0.4, 1.0, 0.2),
}

# Scheduled incidents: (segment, start, minutes, speed drop fraction).
INCIDENTS = (
    ("S2", datetime(2026, 9, 16, 7, 0), 60, 0.55),
    ("S4", datetime(2026, 9, 10, 15, 0), 45, 0.5),
    ("S6", datetime(2026, 9, 12, 17, 0), 90, 0.5),
)


def _gauss(h: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    return np.exp(-0.5 * ((h - mu) / sigma) ** 2)


def demand(index: pd.DatetimeIndex, am: float, pm: float, mid: float) -> np.ndarray:
    h = index.hour.to_numpy() + index.minute.to_numpy() / 60
    weekend = index.dayofweek.to_numpy() >= 5
    wk = am * _gauss(h, 8.0, 1.1) + pm * _gauss(h, 17.5, 1.4) + mid * _gauss(h, 12.5, 2.0)
    we = 0.55 * _gauss(h, 13.0, 3.0)
    return np.where(weekend, we, wk)


def simulate(start: datetime, days: int, seed: int = 7) -> pd.DataFrame:
    """Speed (km/h) per segment on a 5-minute grid."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, start + timedelta(days=days), freq=f"{STEP_MINUTES}min")
    out = {}
    for seg in SEGMENTS:
        d = demand(index, *_PROFILE[seg.id])
        speed = seg.free_flow_kmh * (1 - 0.6 * np.clip(d, 0, 1.2) ** 1.6)
        # day-level demand variation, then short-term noise
        day_factor = rng.normal(1.0, 0.04, size=days + 1)[(index - index[0]).days.to_numpy()]
        speed = speed * day_factor + rng.normal(0, 0.015 * seg.free_flow_kmh, size=len(index))
        for sid, t0, minutes, drop in INCIDENTS:
            if sid == seg.id:
                mask = (index >= t0) & (index < t0 + timedelta(minutes=minutes))
                speed = np.where(mask, speed * (1 - drop), speed)
        out[seg.id] = np.clip(speed, 3, seg.free_flow_kmh * 1.05)
    return pd.DataFrame(out, index=index).round(2)
