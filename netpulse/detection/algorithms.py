"""Deterministic anomaly detection algorithms.

All functions operate on validated arrays: ``t`` holds UTC epoch
nanoseconds (int64, strictly increasing) and ``v`` holds float64 values.
Missing samples are simply absent from the arrays. Nothing is interpolated
or imputed, and gaps break intervals.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from netpulse.detection.config import MetricPolicy
from netpulse.detection.schemas import (
    CoverageReport,
    DetectedInterval,
    DetectionInputError,
    TimeRange,
    WindowComparison,
)
from netpulse.models import DetectorMethod

NS_PER_MINUTE = 60_000_000_000
MAD_TO_SIGMA = 1.4826
ZSCORE_CAP = 1e6


# --------------------------------------------------------------------------
# Input validation and conversions
# --------------------------------------------------------------------------


def to_arrays(
    timestamps: Sequence[datetime] | pd.DatetimeIndex, values: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    """Validate a series and return (t_ns, values). NaN values are treated as missing samples."""
    if len(timestamps) != len(values):
        raise DetectionInputError(f"length mismatch: {len(timestamps)} timestamps vs {len(values)} values")
    try:
        index = pd.DatetimeIndex(timestamps)
    except (TypeError, ValueError) as exc:
        raise DetectionInputError(f"invalid timestamps: {exc}") from exc
    if len(index) and index.tz is None:
        raise DetectionInputError("timestamps must be timezone-aware")
    try:
        v = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise DetectionInputError(f"values must be numeric: {exc}") from exc
    if np.isinf(v).any():
        raise DetectionInputError("values must be finite")
    t = index.tz_convert(UTC).as_unit("ns").asi8 if len(index) else np.array([], dtype=np.int64)
    keep = ~np.isnan(v)
    t, v = t[keep], v[keep]
    if len(t) > 1 and not (np.diff(t) > 0).all():
        raise DetectionInputError("timestamps must be strictly increasing with no duplicates")
    return t.astype(np.int64), v


def ns_to_dt(ns: int) -> datetime:
    return datetime.fromtimestamp(int(ns) / 1e9, tz=UTC)


def dt_to_ns(dt: datetime) -> int:
    return int(pd.Timestamp(dt).tz_convert(UTC).value)


# --------------------------------------------------------------------------
# Coverage and noise
# --------------------------------------------------------------------------


def assess_coverage(t: np.ndarray, start_ns: int, end_ns: int, interval_ns: int) -> CoverageReport:
    """Count present samples in [start, end] and list gaps longer than one sampling interval."""
    expected = int((end_ns - start_ns) // interval_ns) + 1
    inside = t[(t >= start_ns) & (t <= end_ns)]
    gaps: list[TimeRange] = []
    tolerance = interval_ns * 1.5
    edges = np.concatenate([[start_ns - interval_ns], inside, [end_ns + interval_ns]])
    for prev, nxt in zip(edges[:-1], edges[1:], strict=True):
        if nxt - prev > tolerance:
            gaps.append(TimeRange(start=ns_to_dt(prev + interval_ns), end=ns_to_dt(nxt - interval_ns)))
    longest = max(((g.end - g.start).total_seconds() / 60 + interval_ns / NS_PER_MINUTE for g in gaps), default=0.0)
    return CoverageReport(
        start=ns_to_dt(start_ns),
        end=ns_to_dt(end_ns),
        expected_points=expected,
        present_points=len(inside),
        coverage_ratio=min(1.0, len(inside) / expected) if expected else 0.0,
        gaps=gaps,
        longest_gap_minutes=longest,
    )


def noise_sigma(t: np.ndarray, v: np.ndarray, interval_ns: int) -> float | None:
    """Robust point-to-point noise estimate from first differences of adjacent samples.

    Differencing removes slow trends (such as the daily cycle). The MAD of the
    differences, scaled to sigma and divided by sqrt(2), estimates per-sample
    noise. Returns None when fewer than 3 adjacent pairs exist.
    """
    if len(t) < 4:
        return None
    dt = np.diff(t)
    adjacent = np.abs(dt - interval_ns) <= interval_ns * 0.01
    d = np.diff(v)[adjacent]
    if len(d) < 3:
        return None
    mad = float(np.median(np.abs(d - np.median(d))))
    return MAD_TO_SIGMA * mad / math.sqrt(2)


def required_deviation(policy: MetricPolicy, baseline: np.ndarray | float, k_sigma: float) -> np.ndarray:
    """Smallest deviation worth flagging: a noise band, an absolute floor, or a relative floor (largest wins)."""
    base = np.abs(np.asarray(baseline, dtype=np.float64))
    return np.maximum.reduce([np.full_like(base, k_sigma), np.full_like(base, policy.min_abs), policy.min_rel * base])


# --------------------------------------------------------------------------
# Interval building
# --------------------------------------------------------------------------


def flags_to_intervals(
    method: DetectorMethod,
    t: np.ndarray,
    v: np.ndarray,
    flags: np.ndarray,
    deviation: np.ndarray,
    *,
    min_points: int,
    max_gap_ns: int,
    baseline: np.ndarray | None = None,
    threshold: np.ndarray | None = None,
    score: np.ndarray | None = None,
    implausible: bool = False,
) -> list[DetectedInterval]:
    """Group flagged samples into intervals, separately for high and low deviations.

    Flagged samples merge when they are at most ``max_gap_ns`` apart in time.
    A gap in the data longer than that therefore splits an interval, so
    missing data is never bridged into an anomaly.
    """
    out: list[DetectedInterval] = []
    for direction, side in (("high", deviation > 0), ("low", deviation < 0)):
        idx = np.flatnonzero(flags & side)
        if not len(idx):
            continue
        groups: list[list[int]] = [[int(idx[0])]]
        for i in idx[1:]:
            if t[i] - t[groups[-1][-1]] <= max_gap_ns:
                groups[-1].append(int(i))
            else:
                groups.append([int(i)])
        for g in groups:
            if len(g) < min_points:
                continue
            peak = g[int(np.argmax(np.abs(deviation[g])))]
            out.append(
                DetectedInterval(
                    method=method,
                    start=ns_to_dt(t[g[0]]),
                    end=ns_to_dt(t[g[-1]]),
                    n_points=len(g),
                    direction=direction,
                    peak_value=round(float(v[peak]), 6),
                    peak_at=ns_to_dt(t[peak]),
                    baseline_value=None if baseline is None else round(float(baseline[peak]), 6),
                    threshold=None if threshold is None else round(float(threshold[peak]), 6),
                    score=None if score is None else round(float(score[peak]), 3),
                    implausible=implausible,
                )
            )
    return sorted(out, key=lambda i: (i.start, i.direction))


# --------------------------------------------------------------------------
# Methods
# --------------------------------------------------------------------------


def static_threshold(
    t: np.ndarray, v: np.ndarray, policy: MetricPolicy, *, min_points: int, max_gap_ns: int
) -> list[DetectedInterval]:
    """Flag samples beyond the configured alarm levels."""
    out: list[DetectedInterval] = []
    for bound, flags in (
        (policy.static_upper, None if policy.static_upper is None else v > policy.static_upper),
        (policy.static_lower, None if policy.static_lower is None else v < policy.static_lower),
    ):
        if bound is None:
            continue
        out += flags_to_intervals(
            DetectorMethod.STATIC_THRESHOLD,
            t,
            v,
            flags,
            v - bound,
            min_points=min_points,
            max_gap_ns=max_gap_ns,
            threshold=np.full_like(v, bound),
        )
    return out


def plausibility(t: np.ndarray, v: np.ndarray, policy: MetricPolicy, *, max_gap_ns: int) -> list[DetectedInterval]:
    """Flag physically impossible readings. A single sample is enough."""
    out: list[DetectedInterval] = []
    if policy.physical_max is not None:
        out += flags_to_intervals(
            DetectorMethod.PLAUSIBILITY,
            t,
            v,
            v > policy.physical_max,
            v - policy.physical_max,
            min_points=1,
            max_gap_ns=max_gap_ns,
            threshold=np.full_like(v, policy.physical_max),
            implausible=True,
        )
    if policy.physical_min is not None:
        out += flags_to_intervals(
            DetectorMethod.PLAUSIBILITY,
            t,
            v,
            v < policy.physical_min,
            v - policy.physical_min,
            min_points=1,
            max_gap_ns=max_gap_ns,
            threshold=np.full_like(v, policy.physical_min),
            implausible=True,
        )
    return out


def rolling_baseline(
    t: np.ndarray, v: np.ndarray, at: np.ndarray, *, window_ns: int, lag_ns: int, min_count: int
) -> np.ndarray:
    """Median of samples in (t - lag - window, t - lag] for each timestamp in ``at``; NaN if too few samples.

    The lag keeps the start of a slowly ramping fault out of its own baseline.
    """
    out = np.full(len(at), np.nan)
    for k, ti in enumerate(at):
        lo = np.searchsorted(t, ti - lag_ns - window_ns, side="right")
        hi = np.searchsorted(t, ti - lag_ns, side="right")
        if hi - lo >= min_count:
            out[k] = float(np.median(v[lo:hi]))
    return out


def rolling_baseline_detector(
    t: np.ndarray,
    v: np.ndarray,
    window_mask: np.ndarray,
    policy: MetricPolicy,
    sigma: float,
    *,
    k: float,
    window_ns: int,
    lag_ns: int,
    min_count: int,
    min_points: int,
    max_gap_ns: int,
) -> list[DetectedInterval]:
    tw, vw = t[window_mask], v[window_mask]
    baseline = rolling_baseline(t, v, tw, window_ns=window_ns, lag_ns=lag_ns, min_count=min_count)
    valid = ~np.isnan(baseline)
    dev = np.where(valid, vw - np.nan_to_num(baseline), 0.0)
    need = required_deviation(policy, np.nan_to_num(baseline), k * sigma)
    flags = valid & (np.abs(dev) > need)
    return flags_to_intervals(
        DetectorMethod.ROLLING_BASELINE,
        tw,
        vw,
        flags,
        dev,
        min_points=min_points,
        max_gap_ns=max_gap_ns,
        baseline=baseline,
        threshold=need,
        score=np.abs(dev) / need,
    )


def robust_zscore(
    t: np.ndarray,
    v: np.ndarray,
    window_mask: np.ndarray,
    reference_mask: np.ndarray,
    policy: MetricPolicy,
    *,
    z_threshold: float,
    min_points: int,
    max_gap_ns: int,
) -> list[DetectedInterval]:
    """Median/MAD z-score of window samples against the reference period.

    When MAD is zero (a flat reference), only the effect-size floor decides,
    so a flat series cannot produce infinite z-scores.
    """
    ref = v[reference_mask]
    med = float(np.median(ref))
    scale = MAD_TO_SIGMA * float(np.median(np.abs(ref - med)))
    tw, vw = t[window_mask], v[window_mask]
    dev = vw - med
    # Flat reference: any change is "infinitely" unusual, so cap z at a finite sentinel.
    z = dev / scale if scale > 0 else np.sign(dev) * ZSCORE_CAP
    floor = required_deviation(policy, np.full_like(vw, med), 0.0)
    flags = (np.abs(z) >= z_threshold) & (np.abs(dev) >= floor)
    return flags_to_intervals(
        DetectorMethod.ROBUST_ZSCORE,
        tw,
        vw,
        flags,
        dev,
        min_points=min_points,
        max_gap_ns=max_gap_ns,
        baseline=np.full_like(vw, med),
        threshold=floor,
        score=z,
    )


def window_comparison(
    t: np.ndarray,
    v: np.ndarray,
    window_start_ns: int,
    window_end_ns: int,
    policy: MetricPolicy,
    sigma: float,
    *,
    lag_ns: int,
    k: float,
    min_points: int,
    max_gap_ns: int,
    min_pairs: int,
) -> tuple[list[DetectedInterval], WindowComparison | None]:
    """Compare each window sample with the sample exactly one lag earlier (same time yesterday).

    Differencing against yesterday cancels the daily cycle. The noise of a
    difference of two samples is sqrt(2) * sigma.
    """
    wmask = (t >= window_start_ns) & (t <= window_end_ns)
    tw, vw = t[wmask], v[wmask]
    if not len(tw):
        return [], None
    ref_t = tw - lag_ns
    pos = np.clip(np.searchsorted(t, ref_t), 0, len(t) - 1)
    paired = t[pos] == ref_t
    if int(paired.sum()) < min_pairs:
        return [], None
    v_ref = np.where(paired, v[pos], np.nan)
    dev = np.where(paired, vw - np.nan_to_num(v_ref), 0.0)
    need = required_deviation(policy, np.nan_to_num(v_ref), k * math.sqrt(2) * sigma)
    flags = paired & (np.abs(dev) > need)
    intervals = flags_to_intervals(
        DetectorMethod.WINDOW_COMPARISON,
        tw,
        vw,
        flags,
        dev,
        min_points=min_points,
        max_gap_ns=max_gap_ns,
        baseline=v_ref,
        threshold=need,
        score=np.abs(dev) / need,
    )
    rmask = (t >= window_start_ns - lag_ns) & (t <= window_end_ns - lag_ns)
    cur_med, ref_med = float(np.median(vw)), float(np.median(v[rmask]))
    required = float(required_deviation(policy, ref_med, 0.0))
    delta = cur_med - ref_med
    summary = WindowComparison(
        current_median=round(cur_med, 6),
        reference_median=round(ref_med, 6),
        delta=round(delta, 6),
        ratio=round(cur_med / ref_med, 6) if ref_med != 0 else None,
        current_points=len(vw),
        reference_points=int(rmask.sum()),
        required_delta=round(required, 6),
        significant=abs(delta) >= required,
    )
    return intervals, summary
