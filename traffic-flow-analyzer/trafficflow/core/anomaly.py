"""Incident detection: sustained, large negative deviation from the seasonal profile."""

from __future__ import annotations

import pandas as pd

from trafficflow.contracts import Anomaly
from trafficflow.core.forecast import residuals, robust_sigma, seasonal_profile

Z_THRESHOLD = -3.5
MIN_RUN = 3  # consecutive 5-minute readings
LOOKBACK_POINTS = 24  # last 2 hours


def detect(history: pd.Series, segment_id: str) -> list[Anomaly]:
    profile = seasonal_profile(history)
    res = residuals(history, profile)
    sigma = max(robust_sigma(res), 1e-6)
    z = res / sigma
    recent = z.iloc[-LOOKBACK_POINTS:]
    flagged = recent <= Z_THRESHOLD
    found, run_start = [], None
    for i, (_ts, bad) in enumerate(flagged.items()):
        if bad and run_start is None:
            run_start = i
        if run_start is not None and (not bad or i == len(flagged) - 1):
            end = i + 1 if bad else i
            n = end - run_start
            if n >= MIN_RUN:
                seg = recent.iloc[run_start:end]
                found.append(
                    Anomaly(
                        segment_id=segment_id,
                        start=seg.index[0].to_pydatetime(),
                        points=n,
                        min_speed_kmh=round(float(history.loc[seg.index].min()), 2),
                        robust_z=round(float(seg.min()), 2),
                    )
                )
            run_start = None
    return found
