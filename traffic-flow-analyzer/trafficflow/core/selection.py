"""Rule-based forecaster selection: rolling-origin backtest on the segment's own history.

Each origin only sees readings before it, so the choice never uses the future. The rule is
fixed (lowest MAE, ties broken by CANDIDATES order); an LLM may explain it but never makes it.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from trafficflow.contracts import SegmentForecast, SegmentInfo
from trafficflow.core.forecast import (
    MIN_POINTS,
    forecast_moving_average,
    forecast_persistence,
    forecast_segment,
)
from trafficflow.core.simulate import STEP_MINUTES

Forecaster = Callable[[pd.Series, SegmentInfo, int], SegmentForecast]

CANDIDATES: dict[str, Forecaster] = {
    "seasonal_residual": forecast_segment,
    "moving_average": forecast_moving_average,
    "persistence": forecast_persistence,
}
SELECTION_RULE = "lowest mean absolute error over the backtest origins"
BACKTEST_ORIGINS = 6
ORIGIN_SPACING = 12  # one hour apart


def backtest_candidates(
    history: pd.Series,
    segment: SegmentInfo,
    horizon_minutes: int = 60,
    origins: int = BACKTEST_ORIGINS,
) -> dict:
    """Return per-candidate MAE over the last `origins` hourly origins and the selected name."""
    steps = max(1, horizon_minutes // STEP_MINUTES)
    cuts = [
        len(history) - steps - k * ORIGIN_SPACING
        for k in range(origins)
        if len(history) - steps - k * ORIGIN_SPACING >= MIN_POINTS
    ]
    errors: dict[str, list[float]] = {name: [] for name in CANDIDATES}
    for cut in cuts:
        train, truth = history.iloc[:cut], history.iloc[cut : cut + steps].to_numpy()
        for name, fn in CANDIDATES.items():
            try:
                pred = np.array([p.speed_kmh for p in fn(train, segment, horizon_minutes).points])
            except ValueError:
                continue
            errors[name].extend(np.abs(pred[: len(truth)] - truth).tolist())
    mae = {name: round(float(np.mean(e)), 3) for name, e in errors.items() if e}
    selected = min(mae, key=lambda n: (mae[n], list(CANDIDATES).index(n))) if mae else None
    return {
        "origins": len(cuts),
        "mae_by_model": mae,
        "selected_model": selected,
        "rule": SELECTION_RULE,
    }
