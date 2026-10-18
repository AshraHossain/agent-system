"""Rolling-origin evaluation of the forecaster against baselines, with interval coverage.

Error is measured at the forecast step exactly `h` minutes ahead (the convention in the
traffic-forecasting literature). Constants of the forecaster were set before looking at any
real data; nothing here tunes them."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from trafficflow.core.forecast import forecast_segment
from trafficflow.core.simulate import STEP_MINUTES

PEAK_HOURS = (6, 7, 8, 9, 15, 16, 17, 18)

Baseline = Callable[[pd.Series, int], np.ndarray]


def persistence(hist: pd.Series, steps: int) -> np.ndarray:
    return np.full(steps, float(hist.iloc[-1]))


def _future_index(hist: pd.Series, steps: int) -> pd.DatetimeIndex:
    last = hist.index[-1]
    return pd.DatetimeIndex(
        [last + pd.Timedelta(minutes=STEP_MINUTES * (i + 1)) for i in range(steps)]
    )


def _minute_of_day(index: pd.DatetimeIndex) -> pd.Index:
    return index.hour * 60 + index.minute


def seasonal_naive(hist: pd.Series, steps: int) -> np.ndarray:
    """Value at the same time of day on the most recent recorded day."""
    last_by_slot = hist.groupby(_minute_of_day(hist.index)).last()
    out = last_by_slot.reindex(_minute_of_day(_future_index(hist, steps))).to_numpy()
    return np.where(np.isnan(out), hist.iloc[-1], out)


def historical_average(hist: pd.Series, steps: int) -> np.ndarray:
    """Mean over the history at the same time of day (ignores day type)."""
    mean_by_slot = hist.groupby(_minute_of_day(hist.index)).mean()
    out = mean_by_slot.reindex(_minute_of_day(_future_index(hist, steps))).to_numpy()
    return np.where(np.isnan(out), hist.iloc[-1], out)


BASELINES: dict[str, Baseline] = {
    "persistence": persistence,
    "seasonal_naive": seasonal_naive,
    "historical_average": historical_average,
}


class _Acc:
    def __init__(self) -> None:
        self.abs: list[float] = []
        self.sq: list[float] = []
        self.pct: list[float] = []

    def add(self, pred: float, actual: float) -> None:
        e = pred - actual
        self.abs.append(abs(e))
        self.sq.append(e * e)
        self.pct.append(abs(e) / max(abs(actual), 1e-6))

    def summary(self) -> dict[str, float]:
        if not self.abs:
            return {"n": 0}
        return {
            "n": len(self.abs),
            "mae": round(float(np.mean(self.abs)), 3),
            "rmse": round(float(np.sqrt(np.mean(self.sq))), 3),
            "mape_pct": round(100 * float(np.mean(self.pct)), 2),
        }


def evaluate(
    feed: Any,
    origins: Sequence[datetime],
    horizons: Sequence[int] = (15, 30, 60),
    segment_ids: Sequence[str] | None = None,
    train_window: int | None = None,
) -> dict[str, Any]:
    """Evaluate on `origins`. `feed` needs `now` (settable), `segments()`, `history()` and
    `next_values()` (held-back future, used only here). Restores `feed.now` afterwards.

    `train_window`: if set, each origin uses only the most recent `train_window` history
    points instead of the full 7-day window, preventing evaluation on training data.
    """
    steps_for = {h: h // STEP_MINUTES for h in horizons}
    max_steps = max(steps_for.values())
    segs = [s for s in feed.segments() if segment_ids is None or s.id in segment_ids]
    names = ["model", *BASELINES]
    acc: dict[tuple[str, int, str], _Acc] = {}
    cover: dict[tuple[int, str], list[float]] = {}
    width: dict[tuple[int, str], list[float]] = {}
    saved = feed.now
    try:
        for ts in origins:
            feed.now = ts
            bucket = "peak" if (ts.hour in PEAK_HOURS and ts.weekday() < 5) else "offpeak"
            for seg in segs:
                hist = feed.history(seg.id, days=7)
                if train_window is not None and len(hist) > train_window:
                    hist = hist.iloc[-train_window:]
                actual = feed.next_values(seg.id, max_steps)
                if len(actual) < max_steps:
                    continue
                fc = forecast_segment(hist, seg, max_steps * STEP_MINUTES)
                base = {n: fn(hist, max_steps) for n, fn in BASELINES.items()}
                for h, k in steps_for.items():
                    i = k - 1
                    pred = {
                        "model": fc.points[i].speed_kmh,
                        **{n: float(b[i]) for n, b in base.items()},
                    }
                    for n in names:
                        for b in ("all", bucket):
                            acc.setdefault((n, h, b), _Acc()).add(pred[n], float(actual[i]))
                    lo, hi = fc.points[i].lower_kmh, fc.points[i].upper_kmh
                    for b in ("all", bucket):
                        cover.setdefault((h, b), []).append(float(lo <= actual[i] <= hi))
                        width.setdefault((h, b), []).append(hi - lo)
    finally:
        feed.now = saved
    result: dict[str, Any] = {
        "horizons": list(horizons),
        "origins": len(origins),
        "segments": len(segs),
        "results": {},
    }
    for h in horizons:
        for b in ("all", "peak", "offpeak"):
            row = {n: acc[(n, h, b)].summary() for n in names if (n, h, b) in acc}
            if row:
                row["interval_90"] = {
                    "coverage": round(float(np.mean(cover[(h, b)])), 3),
                    "mean_width_kmh": round(float(np.mean(width[(h, b)])), 2),
                }
                result["results"][f"{h}min/{b}"] = row
    return result
