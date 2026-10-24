"""Incident detection: sustained, large negative deviation from the seasonal profile."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trafficflow.contracts import Anomaly
from trafficflow.core.forecast import _key, residuals, robust_sigma, seasonal_profile

Z_THRESHOLD = -3.5
MIN_RUN = 3  # consecutive 5-minute readings
LOOKBACK_POINTS = 24  # last 2 hours


def detect(
    history: pd.Series,
    segment_id: str,
    free_flow_kmh: float = 80.0,
    min_drop_kmh: float = 5.0,
    min_drop_frac: float = 0.05,
) -> list[Anomaly]:
    """Return sustained anomalous slowdowns in the last 2 hours.

    A candidate run must satisfy both the statistical threshold (robust z ≤ -3.5
    for ≥ 3 consecutive readings) and a practical-significance floor: the mean
    speed drop from baseline must be at least ``max(min_drop_kmh,
    min_drop_frac * free_flow_kmh)`` km/h.  The fraction-based floor prevents false
    alarms on high-speed segments where an absolute 5 km/h drop is negligible noise.
    """
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
                seg_idx = seg.index
                # Practical-significance floor: mean actual speed vs profile baseline
                actual_mean = float(history.loc[seg_idx].mean())
                baseline_mean = float(profile.reindex(_key(seg_idx)).mean())
                effective_floor = max(min_drop_kmh, min_drop_frac * free_flow_kmh)
                if not np.isnan(baseline_mean) and (baseline_mean - actual_mean) >= effective_floor:
                    found.append(
                        Anomaly(
                            segment_id=segment_id,
                            start=seg_idx[0].to_pydatetime(),
                            points=n,
                            min_speed_kmh=round(float(history.loc[seg_idx].min()), 2),
                            robust_z=round(float(seg.min()), 2),
                        )
                    )
            run_start = None
    return found
