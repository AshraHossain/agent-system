import pandas as pd
import pytest

from trafficflow.contracts import CongestionLevel, NetworkStatus
from trafficflow.core.anomaly import detect
from trafficflow.core.backtest import backtest
from trafficflow.core.congestion import congestion_index, level_for
from trafficflow.core.forecast import forecast_segment
from trafficflow.core.status import network_status
from trafficflow.data.feed import SyntheticFeed


@pytest.fixture
def feed():
    return SyntheticFeed()


def test_simulation_is_deterministic():
    a, b = SyntheticFeed(seed=1), SyntheticFeed(seed=1)
    pd.testing.assert_frame_equal(a._data, b._data)


@pytest.mark.parametrize(
    "idx,level", [(0.0, "free"), (0.25, "moderate"), (0.5, "heavy"), (0.8, "severe")]
)
def test_levels(idx, level):
    assert level_for(idx) == CongestionLevel(level)


def test_congestion_index_clamped():
    assert congestion_index(120, 100) == 0.0
    assert congestion_index(0, 100) == 1.0


def test_forecast_shape_and_intervals(feed):
    seg = feed.segments()[0]
    fc = forecast_segment(feed.history(seg.id), seg, 60)
    assert len(fc.points) == 12
    assert all(p.lower_kmh <= p.speed_kmh <= p.upper_kmh for p in fc.points)


def test_forecast_requires_enough_history(feed):
    seg = feed.segments()[0]
    with pytest.raises(ValueError):
        forecast_segment(feed.history(seg.id).iloc[:50], seg, 60)


def test_forecast_beats_persistence(feed):
    r = backtest(feed, 60, origins=12)
    assert r["model_mae"] < r["persistence_mae"]
    assert feed.now == SyntheticFeed().now  # backtest restores the clock


def test_incident_detected_only_where_injected(feed):
    feed.advance(12)  # 08:30, S2 incident since 07:00
    hits = {s.id for s in feed.segments() if detect(feed.history(s.id), s.id)}
    assert hits == {"S2"}


def test_status_rules(feed):
    feed.advance(12)
    segs = feed.segments()
    fcs = [forecast_segment(feed.history(s.id), s, 60) for s in segs]
    anomalies = [a for s in segs for a in detect(feed.history(s.id), s.id)]
    assert network_status(fcs, anomalies) == NetworkStatus.DEGRADED
    assert network_status([], []) == NetworkStatus.NORMAL
