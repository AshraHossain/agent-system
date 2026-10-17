"""Interpretable time-series forecaster: seasonal profile + decaying level correction.

profile(slot, daytype) = median of the same slot on previous days of the same type
forecast(h)            = profile(t+h) + phi**h * recent_residual
interval               = forecast +/- z * robust residual sigma
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from trafficflow.contracts import ForecastPoint, SegmentForecast, SegmentInfo
from trafficflow.core.congestion import congestion_index, level_for
from trafficflow.core.simulate import STEP_MINUTES

PHI = 0.92  # per-step persistence of the recent deviation
Z90 = 1.645
MIN_POINTS = 2 * 288


def _daytype(index: pd.DatetimeIndex) -> np.ndarray:
    return (index.dayofweek >= 5).astype(int)


def _key(index: pd.DatetimeIndex) -> pd.MultiIndex:
    return pd.MultiIndex.from_arrays([index.hour * 60 + index.minute, _daytype(index)])


def seasonal_profile(history: pd.Series) -> pd.Series:
    """Median speed per (minute-of-day, is_weekend) over the history."""
    return history.groupby(_key(history.index)).median()


def residuals(history: pd.Series, profile: pd.Series) -> pd.Series:
    expected = profile.reindex(_key(history.index)).to_numpy()
    return pd.Series(history.to_numpy() - expected, index=history.index)


def robust_sigma(values: pd.Series) -> float:
    v = values.dropna()
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if len(v) else 0.0


def forecast_segment(
    history: pd.Series, segment: SegmentInfo, horizon_minutes: int = 60
) -> SegmentForecast:
    if len(history) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} readings, got {len(history)}")
    profile = seasonal_profile(history)
    res = residuals(history, profile)
    sigma = max(robust_sigma(res), 0.01 * segment.free_flow_kmh)
    recent = float(res.iloc[-3:].mean())  # smooth the last 15 minutes
    steps = max(1, horizon_minutes // STEP_MINUTES)
    last = history.index[-1]
    future = pd.DatetimeIndex(
        [last + timedelta(minutes=STEP_MINUTES * (i + 1)) for i in range(steps)]
    )
    base = profile.reindex(_key(future)).to_numpy()
    if np.isnan(base).any():
        # e.g. weekday-only recordings: fall back to the other day-type's profile
        flipped = pd.MultiIndex.from_arrays(
            [future.hour * 60 + future.minute, 1 - _daytype(future)]
        )
        base = np.where(np.isnan(base), profile.reindex(flipped).to_numpy(), base)
        if np.isnan(base).any():
            raise ValueError("history does not cover every time-of-day needed for the horizon")
    hh = np.arange(1, steps + 1)
    mean = np.clip(base + (PHI**hh) * recent, 3, segment.free_flow_kmh * 1.05)
    width = Z90 * sigma * np.sqrt(1 + 0.05 * hh)
    points = []
    for ts, m, w in zip(future, mean, width, strict=True):
        idx = congestion_index(float(m), segment.free_flow_kmh)
        points.append(
            ForecastPoint(
                ts=ts.to_pydatetime(),
                speed_kmh=round(float(m), 2),
                lower_kmh=round(max(0.0, float(m - w)), 2),
                upper_kmh=round(float(m + w), 2),
                congestion_index=round(idx, 3),
                level=level_for(idx),
            )
        )
    peak = max(points, key=lambda p: p.congestion_index)
    return SegmentForecast(
        segment_id=segment.id,
        method="seasonal-median + decaying residual",
        horizon_minutes=steps * STEP_MINUTES,
        points=points,
        peak_index=peak.congestion_index,
        peak_ts=peak.ts,
        peak_level=peak.level,
    )


def persistence_baseline(history: pd.Series, steps: int) -> np.ndarray:
    return np.full(steps, float(history.iloc[-1]))
