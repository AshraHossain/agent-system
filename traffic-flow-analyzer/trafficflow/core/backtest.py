"""Rolling-origin backtest of the forecaster against a persistence baseline."""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from trafficflow.core.forecast import forecast_segment, persistence_baseline
from trafficflow.core.simulate import STEP_MINUTES
from trafficflow.data.feed import SyntheticFeed


def backtest(feed: SyntheticFeed, horizon_minutes: int = 60, origins: int = 24) -> dict:
    """MAE (km/h) of model vs persistence over `origins` origins spaced 1h apart,
    starting at the feed's current `now`. Ground truth is read via `feed.truth` only here."""
    steps = horizon_minutes // STEP_MINUTES
    start = feed.now
    errs: dict[str, list[tuple[float, float]]] = {s.id: [] for s in feed.segments()}
    try:
        for _ in range(origins):
            for seg in feed.segments():
                hist = feed.history(seg.id, days=7)
                fc = forecast_segment(hist, seg, horizon_minutes)
                truth = feed.truth(seg.id, feed.now, feed.now + timedelta(minutes=horizon_minutes))
                if len(truth) < steps:
                    continue
                pred = np.array([p.speed_kmh for p in fc.points])
                base = persistence_baseline(hist, steps)
                errs[seg.id].append(
                    (
                        float(np.mean(np.abs(pred - truth.to_numpy()))),
                        float(np.mean(np.abs(base - truth.to_numpy()))),
                    )
                )
            feed.advance(12)
    finally:
        feed.now = start
    per = {
        sid: {
            "model_mae": round(float(np.mean([e[0] for e in v])), 2),
            "persistence_mae": round(float(np.mean([e[1] for e in v])), 2),
        }
        for sid, v in errs.items()
        if v
    }
    allv = [e for v in errs.values() for e in v]
    return {
        "horizon_minutes": horizon_minutes,
        "model_mae": round(float(np.mean([e[0] for e in allv])), 2),
        "persistence_mae": round(float(np.mean([e[1] for e in allv])), 2),
        "per_segment": per,
    }
