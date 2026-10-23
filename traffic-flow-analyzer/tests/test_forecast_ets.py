"""Tests for the ETS (Holt-Winters) forecaster."""

from __future__ import annotations

import pytest

from trafficflow.contracts import CongestionLevel
from trafficflow.core.forecast import MIN_POINTS, forecast_segment, forecast_segment_ets
from trafficflow.data.feed import SyntheticFeed


@pytest.fixture(scope="module")
def history_and_segment():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)  # move well past the seed window
    segs = {s.id: s for s in feed.segments()}
    seg = segs["S1"]
    h = feed.history("S1", days=14)
    return h, seg


def test_ets_returns_forecast(history_and_segment):
    pytest.importorskip("statsmodels")
    h, seg = history_and_segment
    if len(h) < MIN_POINTS:
        pytest.skip("not enough history")
    fc = forecast_segment_ets(h, seg, horizon_minutes=60)
    assert fc.segment_id == "S1"
    assert fc.method == "holt-winters-ets"
    assert fc.horizon_minutes == 60
    assert len(fc.points) == 12  # 60 min / 5 min


def test_ets_speeds_in_range(history_and_segment):
    pytest.importorskip("statsmodels")
    h, seg = history_and_segment
    if len(h) < MIN_POINTS:
        pytest.skip("not enough history")
    fc = forecast_segment_ets(h, seg, horizon_minutes=30)
    for p in fc.points:
        assert p.speed_kmh >= 3
        assert p.speed_kmh <= seg.free_flow_kmh * 1.1
        assert p.lower_kmh <= p.speed_kmh
        assert p.upper_kmh >= p.speed_kmh
        assert isinstance(p.level, CongestionLevel)


def test_ets_peak_is_consistent(history_and_segment):
    pytest.importorskip("statsmodels")
    h, seg = history_and_segment
    if len(h) < MIN_POINTS:
        pytest.skip("not enough history")
    fc = forecast_segment_ets(h, seg, horizon_minutes=60)
    # peak must be one of the points
    peak_idxs = [p.congestion_index for p in fc.points]
    assert fc.peak_index == max(peak_idxs)


def test_ets_too_little_history():
    pytest.importorskip("statsmodels")
    import pandas as pd

    from trafficflow.contracts import SegmentInfo

    seg = SegmentInfo(id="X1", name="Test", free_flow_kmh=80)
    short = pd.Series([60.0, 55.0, 50.0])
    with pytest.raises(ValueError, match="need at least"):
        forecast_segment_ets(short, seg, horizon_minutes=30)


def test_seasonal_vs_ets_both_succeed(history_and_segment):
    """Both forecasters produce valid results on the same history."""
    pytest.importorskip("statsmodels")
    h, seg = history_and_segment
    if len(h) < MIN_POINTS:
        pytest.skip("not enough history")
    fc_seasonal = forecast_segment(h, seg, horizon_minutes=30)
    fc_ets = forecast_segment_ets(h, seg, horizon_minutes=30)
    assert fc_seasonal.method == "seasonal-median + decaying residual"
    assert fc_ets.method == "holt-winters-ets"
    assert len(fc_seasonal.points) == len(fc_ets.points)
