"""Phase 4: unit tests for deterministic anomaly detection."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from netpulse.detection import (
    DEFAULT_POLICIES,
    DetectionConfig,
    DetectionInputError,
    DetectionRequest,
    detect_series,
    engine,
    to_evidence,
)
from netpulse.detection import algorithms as alg
from netpulse.models import DetectorMethod, EvidenceItem, Metric

START = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)
N = 360  # 24 h history + 6 h window, like the dataset
WINDOW = (START + 300 * STEP, START + 359 * STEP)
FIVE_MIN_NS = 5 * alg.NS_PER_MINUTE


def series(
    base: float = 40.0,
    noise: float = 2.0,
    diurnal: float = 0.0,
    seed: int = 0,
    step_at: int | None = None,
    step: float = 0.0,
    drop: slice | None = None,
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    rng = np.random.default_rng(seed)
    ts = pd.date_range(START, periods=N, freq="5min")
    hours = np.asarray(ts.hour + ts.minute / 60, dtype=float)
    v = base + diurnal * np.sin(2 * np.pi * (hours - 8) / 24) + rng.normal(0, noise, N)
    if step_at is not None:
        v[step_at:] += step
    keep = np.ones(N, dtype=bool)
    if drop is not None:
        keep[drop] = False
    return ts[keep], v[keep]


def request(metric: Metric = Metric.UTILIZATION_PCT, entity: str = "link-a-b") -> DetectionRequest:
    return DetectionRequest(entity_id=entity, metric=metric, window_start=WINDOW[0], window_end=WINDOW[1])


# --- input validation -----------------------------------------------------


@pytest.mark.parametrize(
    ("timestamps", "values", "message"),
    [
        (pd.date_range("2026-01-01", periods=3, freq="5min"), [1, 2, 3], "timezone-aware"),
        (pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC"), [1, 2], "length mismatch"),
        (pd.DatetimeIndex(["2026-01-01T00:00Z", "2026-01-01T00:00Z"]), [1, 2], "strictly increasing"),
        (pd.date_range("2026-01-01", periods=2, freq="5min", tz="UTC"), [1, np.inf], "finite"),
        (pd.date_range("2026-01-01", periods=2, freq="5min", tz="UTC"), ["a", "b"], "numeric"),
    ],
)
def test_to_arrays_rejects_bad_input(timestamps, values, message):
    with pytest.raises(DetectionInputError, match=message):
        alg.to_arrays(timestamps, values)


def test_to_arrays_drops_nan_as_missing_and_converts_to_ns():
    ts = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
    t, v = alg.to_arrays(ts, [1.0, np.nan, 3.0])
    assert list(v) == [1.0, 3.0]
    assert t[1] - t[0] == 2 * FIVE_MIN_NS


def test_to_arrays_normalises_timezones_and_coarse_units():
    ts = pd.to_datetime(["2026-01-01T10:00+02:00", "2026-01-01T08:05Z"], utc=True).as_unit("s")
    t, _ = alg.to_arrays(ts, [1, 2])
    assert t[1] - t[0] == FIVE_MIN_NS


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window_start": datetime(2026, 1, 1), "window_end": datetime(2026, 1, 2)},
        {"window_start": WINDOW[1], "window_end": WINDOW[0]},
    ],
)
def test_detection_request_validates_window(kwargs):
    with pytest.raises(ValidationError):
        DetectionRequest(entity_id="x", metric=Metric.CPU_PCT, **kwargs)


def test_config_requires_policy_for_every_metric():
    with pytest.raises(ValidationError):
        DetectionConfig(policies={Metric.CPU_PCT: DEFAULT_POLICIES[Metric.CPU_PCT]})


# --- coverage and noise ---------------------------------------------------


def test_coverage_full_window():
    t, _ = alg.to_arrays(*series())
    cov = alg.assess_coverage(t, alg.dt_to_ns(WINDOW[0]), alg.dt_to_ns(WINDOW[1]), FIVE_MIN_NS)
    assert (cov.expected_points, cov.present_points, cov.coverage_ratio, cov.gaps) == (60, 60, 1.0, [])


def test_coverage_reports_leading_middle_and_trailing_gaps():
    ts, v = series(drop=slice(300, 303))
    keep = [
        i for i, x in enumerate(ts) if not (START + 320 * STEP <= x < START + 330 * STEP) and x < START + 355 * STEP
    ]
    t, _ = alg.to_arrays(ts[keep], v[keep])
    cov = alg.assess_coverage(t, alg.dt_to_ns(WINDOW[0]), alg.dt_to_ns(WINDOW[1]), FIVE_MIN_NS)
    assert [(g.start, g.end) for g in cov.gaps] == [
        (START + 300 * STEP, START + 302 * STEP),
        (START + 320 * STEP, START + 329 * STEP),
        (START + 355 * STEP, START + 359 * STEP),
    ]
    assert cov.present_points == 60 - 3 - 10 - 5
    assert cov.longest_gap_minutes == 50


def test_coverage_with_no_data_is_one_gap():
    cov = alg.assess_coverage(np.array([], dtype=np.int64), 0, 11 * FIVE_MIN_NS, FIVE_MIN_NS)
    assert cov.coverage_ratio == 0 and len(cov.gaps) == 1


def test_noise_sigma_estimates_gaussian_noise_and_ignores_trend():
    t, v = alg.to_arrays(*series(noise=2.0, diurnal=15.0, seed=3))
    sigma = alg.noise_sigma(t, v, FIVE_MIN_NS)
    assert 1.6 < sigma < 2.4
    assert alg.noise_sigma(t[:3], v[:3], FIVE_MIN_NS) is None


# --- individual methods ---------------------------------------------------

POLICY = DEFAULT_POLICIES[Metric.UTILIZATION_PCT]


def test_static_threshold_needs_min_points():
    t = np.arange(10) * FIVE_MIN_NS
    v = np.full(10, 50.0)
    v[2] = 95  # single spike: ignored
    v[6:8] = 95  # two samples: an interval
    out = alg.static_threshold(t, v, POLICY, min_points=2, max_gap_ns=2 * FIVE_MIN_NS)
    assert len(out) == 1 and out[0].n_points == 2 and out[0].threshold == 85.0 and out[0].direction == "high"


def test_static_lower_bound_flags_low_direction():
    t = np.arange(5) * FIVE_MIN_NS
    v = np.array([99.9, 97.0, 96.5, 99.9, 99.9])
    out = alg.static_threshold(t, v, DEFAULT_POLICIES[Metric.SERVICE_SUCCESS_PCT], min_points=2, max_gap_ns=FIVE_MIN_NS)
    assert out[0].direction == "low" and out[0].peak_value == 96.5


def test_plausibility_flags_single_impossible_sample():
    t = np.arange(5) * FIVE_MIN_NS
    v = np.array([40, 40, 812.5, 40, -1.0])
    out = alg.plausibility(t, v, POLICY, max_gap_ns=FIVE_MIN_NS)
    assert [(i.direction, i.peak_value, i.implausible) for i in out] == [("high", 812.5, True), ("low", -1.0, True)]


def test_intervals_split_across_data_gaps_and_by_direction():
    t = np.array([0, 1, 2, 10, 11]) * FIVE_MIN_NS  # 40-minute hole between index 2 and 3
    v = np.array([90, 91, 92, 93, 94.0])
    dev = v - 50
    out = alg.flags_to_intervals(
        DetectorMethod.STATIC_THRESHOLD, t, v, np.ones(5, bool), dev, min_points=2, max_gap_ns=2 * FIVE_MIN_NS
    )
    assert [i.n_points for i in out] == [3, 2]
    mixed = alg.flags_to_intervals(
        DetectorMethod.STATIC_THRESHOLD,
        t[:4],
        v[:4],
        np.ones(4, bool),
        np.array([5, -5, 5, -5.0]),
        min_points=1,
        max_gap_ns=100 * FIVE_MIN_NS,
    )
    assert {i.direction for i in mixed} == {"high", "low"}


def test_rolling_baseline_lag_excludes_recent_ramp():
    t = np.arange(100) * FIVE_MIN_NS
    v = np.where(np.arange(100) >= 90, 90.0, 40.0)
    baseline = alg.rolling_baseline(t, v, t[95:96], window_ns=24 * FIVE_MIN_NS, lag_ns=12 * FIVE_MIN_NS, min_count=6)
    assert baseline[0] == 40.0
    sparse = alg.rolling_baseline(t[::10], v[::10], t[95:96], window_ns=24 * FIVE_MIN_NS, lag_ns=0, min_count=6)
    assert np.isnan(sparse[0])


def test_robust_zscore_flat_reference_is_finite_and_respects_floor():
    t = np.arange(20) * FIVE_MIN_NS
    v = np.full(20, 40.0)
    v[15:] = [45, 45, 60, 60, 60]  # +5 is below the 10-point floor; +20 is not
    window = np.arange(20) >= 15
    out = alg.robust_zscore(t, v, window, ~window, POLICY, z_threshold=3.5, min_points=2, max_gap_ns=FIVE_MIN_NS)
    assert len(out) == 1 and out[0].n_points == 3
    assert np.isfinite(out[0].score)


def test_window_comparison_cancels_daily_cycle():
    ts, v = series(base=50, noise=1.0, diurnal=20.0, seed=4)
    t, v = alg.to_arrays(ts, v)
    sigma = alg.noise_sigma(t, v, FIVE_MIN_NS)
    found, summary = alg.window_comparison(
        t,
        v,
        alg.dt_to_ns(WINDOW[0]),
        alg.dt_to_ns(WINDOW[1]),
        POLICY,
        sigma,
        lag_ns=288 * FIVE_MIN_NS,
        k=4,
        min_points=2,
        max_gap_ns=2 * FIVE_MIN_NS,
        min_pairs=30,
    )
    assert found == [] and summary is not None and not summary.significant


def test_window_comparison_needs_yesterday():
    ts, v = series(drop=slice(0, 288))
    t, v = alg.to_arrays(ts, v)
    found, summary = alg.window_comparison(
        t,
        v,
        alg.dt_to_ns(WINDOW[0]),
        alg.dt_to_ns(WINDOW[1]),
        POLICY,
        2.0,
        lag_ns=288 * FIVE_MIN_NS,
        k=4,
        min_points=2,
        max_gap_ns=FIVE_MIN_NS,
        min_pairs=30,
    )
    assert found == [] and summary is None


# --- engine ---------------------------------------------------------------


def test_step_change_is_confirmed_by_multiple_methods():
    report = detect_series(request(), *series(step_at=320, step=40))
    assert len(report.confirmed) == 1
    anomaly = report.confirmed[0]
    assert anomaly.start == START + 320 * STEP and anomaly.end == WINDOW[1]
    assert set(anomaly.methods) >= {DetectorMethod.ROBUST_ZSCORE, DetectorMethod.WINDOW_COMPARISON}
    assert report.comparison.significant


@pytest.mark.parametrize("seed", range(15))
def test_noise_only_series_produce_no_confirmed_anomalies(seed):
    report = detect_series(request(), *series(base=45, noise=2.5, diurnal=15, seed=seed))
    assert report.confirmed == []


def test_small_change_below_effect_floor_is_not_confirmed():
    report = detect_series(request(), *series(noise=0.5, step_at=320, step=4))
    assert report.confirmed == []


def test_no_data_in_window_skips_every_method():
    report = detect_series(request(), *series(drop=slice(300, 360)))
    assert report.anomalies == [] and report.window_coverage.present_points == 0
    assert {s.reason for s in report.skipped} == {"no_data_in_window"}


def test_fault_hidden_by_gap_is_not_detected_and_gap_is_reported():
    report = detect_series(request(), *series(step_at=320, step=40, drop=slice(318, 360)))
    assert report.confirmed == []
    assert report.window_coverage.gaps[-1].end == WINDOW[1]


def test_insufficient_history_skips_statistical_methods_but_keeps_static():
    report = detect_series(request(), *series(base=95, drop=slice(0, 290)))
    assert {s.method for s in report.skipped} == {
        DetectorMethod.ROLLING_BASELINE,
        DetectorMethod.ROBUST_ZSCORE,
        DetectorMethod.WINDOW_COMPARISON,
    }
    assert all(s.reason.startswith("insufficient_history") for s in report.skipped)
    assert report.anomalies and not report.confirmed  # one method alone cannot confirm


def test_impossible_reading_is_confirmed_and_marked_implausible():
    ts, v = series()
    v[330:332] = 812.5
    report = detect_series(request(), ts, v)
    assert len(report.confirmed) == 1 and report.confirmed[0].implausible


def test_detector_failure_is_isolated(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated detector crash")

    monkeypatch.setattr(engine.alg, "robust_zscore", boom)
    report = detect_series(request(), *series(step_at=320, step=40))
    assert [(f.method, f.error_type) for f in report.failures] == [(DetectorMethod.ROBUST_ZSCORE, "RuntimeError")]
    assert report.confirmed  # remaining methods still corroborate


def test_min_methods_controls_confirmation():
    strict = DetectionConfig(min_methods=5)
    report = detect_series(request(), *series(step_at=320, step=40), strict)
    assert report.anomalies and not report.confirmed


def test_detection_is_deterministic():
    data = series(step_at=320, step=40, seed=9)
    assert detect_series(request(), *data) == detect_series(request(), *data)


def test_evidence_numbers_come_from_the_detector():
    report = detect_series(request(), *series(step_at=320, step=40))
    anomaly = report.confirmed[0]
    item = to_evidence(anomaly, "ev-anom-0001")
    assert isinstance(item, EvidenceItem) and item.provenance == "synthetic"
    assert item.observed_value == anomaly.peak_value and item.baseline_value == anomaly.baseline_value
    assert f"{anomaly.peak_value:g}" in item.summary and item.unit == "%"
    assert item.entity_ids == ["link-a-b"] and item.metric == Metric.UTILIZATION_PCT
