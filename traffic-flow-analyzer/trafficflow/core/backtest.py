"""Quick synthetic-feed backtest: model vs persistence MAE (kept for the CLI and tests).
For the full evaluation with more baselines and interval coverage see `core/evaluation.py`."""

from __future__ import annotations

from datetime import timedelta

from trafficflow.core.evaluation import evaluate
from trafficflow.core.simulate import STEP_MINUTES
from trafficflow.data.feed import SyntheticFeed


def backtest(feed: SyntheticFeed, horizon_minutes: int = 60, origins: int = 24) -> dict:
    """MAE (km/h) of model vs persistence at `horizon_minutes` ahead, over `origins` origins
    spaced 1h apart starting at the feed's current `now`."""
    times = [feed.now + timedelta(minutes=STEP_MINUTES * 12 * k) for k in range(origins)]
    res = evaluate(feed, times, horizons=(horizon_minutes,))["results"][f"{horizon_minutes}min/all"]
    return {
        "horizon_minutes": horizon_minutes,
        "model_mae": res["model"]["mae"],
        "persistence_mae": res["persistence"]["mae"],
    }
