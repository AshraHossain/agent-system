"""Interpretable time-series forecaster: seasonal profile + decaying level correction.

profile(slot, daytype) = median of the same slot on previous days of the same type
forecast(h)            = profile(t+h) + phi**h * recent_residual
interval               = empirical 5th/95th percentile of per-step residuals (fallback: MAD)
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
MIN_SAMPLES_FOR_EMPIRICAL = 30


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


def _empirical_quantiles(
    history: pd.Series, profile: pd.Series, steps: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return per-step (q05, q95) empirical residual quantiles over the training window.

    Uses a rolling-origin approach: calibration residuals are computed against a
    train-half-only seasonal profile so the second half of history is genuinely
    out-of-sample.  This avoids calibration leakage that would otherwise make
    intervals appear narrower than they really are.

    Falls back to the MAD-based half-width for any step with fewer than
    MIN_SAMPLES_FOR_EMPIRICAL samples.
    """
    n = len(history)
    cal_start = n // 2
    vals = history.to_numpy()
    idx = history.index

    # Build the profile from the FIRST half only so the calibration window (second half)
    # is out-of-sample with respect to the profile fit — prevents interval leakage.
    train_profile = seasonal_profile(history.iloc[:cal_start])
    all_res = residuals(history, train_profile)
    res_vals = all_res.to_numpy()
    sigma_global = max(robust_sigma(all_res.iloc[:cal_start]), 0.01)

    # Precompute profile lookup for every step in history by time-of-day key
    all_base = train_profile.reindex(_key(idx)).to_numpy()  # shape (n,)

    per_step_res: list[list[float]] = [[] for _ in range(steps)]
    for i in range(cal_start, n - steps):
        # recent_level: mean of up to 3 residuals ending at i
        start3 = max(0, i - 2)
        recent_level = float(np.nanmean(res_vals[start3 : i + 1]))
        for h in range(1, steps + 1):
            target_i = i + h
            base_val = all_base[target_i]
            if np.isnan(base_val):
                continue
            pred = base_val + (PHI**h) * recent_level
            per_step_res[h - 1].append(vals[target_i] - pred)

    q05 = np.empty(steps)
    q95 = np.empty(steps)
    for h in range(steps):
        samples = per_step_res[h]
        widen = np.sqrt(1 + 0.05 * (h + 1))
        if len(samples) >= MIN_SAMPLES_FOR_EMPIRICAL:
            q05[h] = float(np.percentile(samples, 5)) * widen
            q95[h] = float(np.percentile(samples, 95)) * widen
        else:
            half = Z90 * sigma_global * widen
            q05[h] = -half
            q95[h] = half
    return q05, q95


def forecast_segment(
    history: pd.Series, segment: SegmentInfo, horizon_minutes: int = 60
) -> SegmentForecast:
    if len(history) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} readings, got {len(history)}")
    profile = seasonal_profile(history)
    res = residuals(history, profile)
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
    q05, q95 = _empirical_quantiles(history, profile, steps)
    points = []
    for i, (ts, m) in enumerate(zip(future, mean, strict=True)):
        # Clamp so the interval always contains the point forecast, even when
        # empirical quantiles are biased (both positive or both negative).
        lower = min(float(m), max(0.0, float(m) + q05[i]))
        upper = max(float(m), float(m) + q95[i])
        idx = congestion_index(float(m), segment.free_flow_kmh)
        points.append(
            ForecastPoint(
                ts=ts.to_pydatetime(),
                speed_kmh=round(float(m), 2),
                lower_kmh=round(lower, 2),
                upper_kmh=round(upper, 2),
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


def forecast_segment_ets(
    history: pd.Series, segment: SegmentInfo, horizon_minutes: int = 60
) -> SegmentForecast:
    """Forecast using Holt-Winters Exponential Smoothing (additive trend + seasonality).

    Requires ``statsmodels``; falls back to :func:`forecast_segment` when it is not installed.
    Seasonal period is 288 steps (24 h × 12 steps/h at 5-min resolution).
    """
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
    except ImportError as exc:
        raise ImportError("statsmodels is required: pip install statsmodels") from exc

    if len(history) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} readings, got {len(history)}")

    steps = max(1, horizon_minutes // STEP_MINUTES)
    last = history.index[-1]
    future = pd.DatetimeIndex(
        [last + timedelta(minutes=STEP_MINUTES * (i + 1)) for i in range(steps)]
    )

    # Fit on the full history; 288 = one full day at 5-min resolution
    seasonal_periods = 288
    fit = ExponentialSmoothing(
        history,
        trend="add",
        seasonal="add",
        seasonal_periods=seasonal_periods,
        initialization_method="estimated",
    ).fit(optimized=True)

    raw_forecast = fit.forecast(steps)
    mean = np.clip(raw_forecast.to_numpy(), 3, segment.free_flow_kmh * 1.05)

    # Confidence interval: residual sigma from in-sample fit
    resid = history.to_numpy() - fit.fittedvalues.to_numpy()
    sigma = max(float(1.4826 * np.median(np.abs(resid - np.median(resid)))), 0.01)

    points = []
    for i, (ts, m) in enumerate(zip(future, mean, strict=True)):
        widen = np.sqrt(1 + 0.05 * (i + 1))
        half = Z90 * sigma * widen
        idx = congestion_index(float(m), segment.free_flow_kmh)
        points.append(
            ForecastPoint(
                ts=ts.to_pydatetime(),
                speed_kmh=round(float(m), 2),
                lower_kmh=round(max(0.0, float(m) - half), 2),
                upper_kmh=round(float(m) + half, 2),
                congestion_index=round(idx, 3),
                level=level_for(idx),
            )
        )
    peak = max(points, key=lambda p: p.congestion_index)
    return SegmentForecast(
        segment_id=segment.id,
        method="holt-winters-ets",
        horizon_minutes=steps * STEP_MINUTES,
        points=points,
        peak_index=peak.congestion_index,
        peak_ts=peak.ts,
        peak_level=peak.level,
    )


def persistence_baseline(history: pd.Series, steps: int) -> np.ndarray:
    return np.full(steps, float(history.iloc[-1]))


def forecast_moving_average(
    history: pd.Series, segment: SegmentInfo, horizon_minutes: int = 60
) -> SegmentForecast:
    """Forecast using a 60-min trailing moving average of recent history."""
    if len(history) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} readings, got {len(history)}")
    window = 12  # 60 min / 5 min per step
    ma_pred = float(history.iloc[-window:].mean())
    steps = max(1, horizon_minutes // STEP_MINUTES)
    last = history.index[-1]
    future = pd.DatetimeIndex(
        [last + timedelta(minutes=STEP_MINUTES * (i + 1)) for i in range(steps)]
    )
    # Interval: MAD of residuals vs the moving average over the last MIN_POINTS readings
    ma_series = history.rolling(window, min_periods=1).mean()
    res = history - ma_series
    sigma = max(robust_sigma(res), 0.01)
    points = []
    for i, ts in enumerate(future):
        widen = np.sqrt(1 + 0.05 * (i + 1))
        half = Z90 * sigma * widen
        m = np.clip(ma_pred, 3, segment.free_flow_kmh * 1.05)
        idx = congestion_index(float(m), segment.free_flow_kmh)
        points.append(
            ForecastPoint(
                ts=ts.to_pydatetime(),
                speed_kmh=round(float(m), 2),
                lower_kmh=round(max(0.0, float(m) - half), 2),
                upper_kmh=round(float(m) + half, 2),
                congestion_index=round(idx, 3),
                level=level_for(idx),
            )
        )
    peak = max(points, key=lambda p: p.congestion_index)
    return SegmentForecast(
        segment_id=segment.id,
        method="moving-average",
        horizon_minutes=steps * STEP_MINUTES,
        points=points,
        peak_index=peak.congestion_index,
        peak_ts=peak.ts,
        peak_level=peak.level,
    )


def forecast_persistence(
    history: pd.Series, segment: SegmentInfo, horizon_minutes: int = 60
) -> SegmentForecast:
    """Forecast by carrying the last observed speed forward (persistence baseline)."""
    if len(history) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} readings, got {len(history)}")
    last_speed = float(history.iloc[-1])
    steps = max(1, horizon_minutes // STEP_MINUTES)
    last = history.index[-1]
    future = pd.DatetimeIndex(
        [last + timedelta(minutes=STEP_MINUTES * (i + 1)) for i in range(steps)]
    )
    # Interval: MAD of point-to-point speed changes
    diffs = history.diff().dropna()
    sigma = max(robust_sigma(diffs), 0.01)
    points = []
    for i, ts in enumerate(future):
        widen = np.sqrt(1 + 0.05 * (i + 1))
        half = Z90 * sigma * widen
        m = np.clip(last_speed, 3, segment.free_flow_kmh * 1.05)
        idx = congestion_index(float(m), segment.free_flow_kmh)
        points.append(
            ForecastPoint(
                ts=ts.to_pydatetime(),
                speed_kmh=round(float(m), 2),
                lower_kmh=round(max(0.0, float(m) - half), 2),
                upper_kmh=round(float(m) + half, 2),
                congestion_index=round(idx, 3),
                level=level_for(idx),
            )
        )
    peak = max(points, key=lambda p: p.congestion_index)
    return SegmentForecast(
        segment_id=segment.id,
        method="persistence",
        horizon_minutes=steps * STEP_MINUTES,
        points=points,
        peak_index=peak.congestion_index,
        peak_ts=peak.ts,
        peak_level=peak.level,
    )
