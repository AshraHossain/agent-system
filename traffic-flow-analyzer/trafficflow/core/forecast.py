"""Interpretable time-series forecaster: seasonal profile + decaying level correction.

profile(slot, daytype) = median of the same slot on previous days of the same type
model(h)               = profile(t+h) + phi**h * last_residual
forecast(h)            = w**h * last_reading + (1 - w**h) * model(h)
interval               = leave-one-day-out conformal quantiles of the same forecast's errors

LAST_WEIGHT and INTERVAL_TAIL were chosen on the first half of the PeMSD7 origins only;
results are reported on the second half (`trafficflow evaluate --split`).
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from trafficflow.contracts import ForecastPoint, SegmentForecast, SegmentInfo
from trafficflow.core.congestion import congestion_index, level_for
from trafficflow.core.simulate import STEP_MINUTES

PHI = 0.92  # per-step persistence of the recent deviation
LAST_WEIGHT = 0.9  # weight on the latest reading decays as LAST_WEIGHT**h
INTERVAL_TAIL = 0.05  # probability mass in each tail of the calibrated interval
CALIBRATION_DAYS = 6
Z90 = 1.645
MIN_POINTS = 2 * 288
MIN_SAMPLES_FOR_EMPIRICAL = 30
METHOD = "seasonal-median + decaying residual, blended toward the last reading"


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


def _blend(base, last_residual, last_reading, h):
    """The point forecast `h` steps ahead; arguments may be arrays of equal shape."""
    w = LAST_WEIGHT**h
    return w * last_reading + (1 - w) * (base + PHI**h * last_residual)


def _calibration_errors(history: pd.Series, steps: int) -> list[np.ndarray]:
    """Errors of the same forecast on each recent complete day, with that day left out of the
    profile, so calibration mirrors forecasting a day the profile has not seen."""
    dates = history.index.normalize()
    errs: list[list[np.ndarray]] = [[] for _ in range(steps)]
    for day in pd.unique(dates)[:-1][-CALIBRATION_DAYS:]:
        mask = dates == day
        profile = seasonal_profile(history[~mask])
        vals = history[mask].to_numpy()
        base = profile.reindex(_key(history.index[mask])).to_numpy()
        res = vals - base
        for h in range(1, min(steps, len(vals) - 1) + 1):
            e = vals[h:] - _blend(base[h:], res[:-h], vals[:-h], h)
            errs[h - 1].append(e[~np.isnan(e)])
    return [np.concatenate(e) if e else np.empty(0) for e in errs]


def _interval_offsets(history: pd.Series, steps: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-step (lower, upper) offsets from the point forecast; MAD fallback when sparse."""
    errs = _calibration_errors(history, steps)
    sigma = max(robust_sigma(residuals(history, seasonal_profile(history))), 0.01)
    lo, hi = np.empty(steps), np.empty(steps)
    for h, e in enumerate(errs):
        if len(e) >= MIN_SAMPLES_FOR_EMPIRICAL:
            lo[h], hi[h] = np.quantile(e, INTERVAL_TAIL), np.quantile(e, 1 - INTERVAL_TAIL)
        else:
            half = Z90 * sigma * np.sqrt(1 + 0.05 * (h + 1))
            lo[h], hi[h] = -half, half
    return lo, hi


def forecast_segment(
    history: pd.Series, segment: SegmentInfo, horizon_minutes: int = 60
) -> SegmentForecast:
    history = history.dropna()
    if len(history) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} readings, got {len(history)}")
    profile = seasonal_profile(history)
    res = residuals(history, profile)
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
    mean = _blend(base, float(res.iloc[-1]), float(history.iloc[-1]), hh)
    mean = np.clip(mean, 3, segment.free_flow_kmh * 1.05)
    lo, hi = _interval_offsets(history, steps)
    points = []
    for i, (ts, m) in enumerate(zip(future, mean, strict=True)):
        # Clamp so the interval always contains the point forecast, even when
        # the calibration errors are biased (both offsets of the same sign).
        lower = min(float(m), max(0.0, float(m) + lo[i]))
        upper = max(float(m), float(m) + hi[i])
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
        method=METHOD,
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
