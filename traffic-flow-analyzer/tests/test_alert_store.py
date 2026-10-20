"""Tests for the SQLite-backed AlertStore."""

from datetime import datetime, timedelta

import pytest

from trafficflow.alerts.store import AlertStore
from trafficflow.contracts import CongestionLevel


@pytest.fixture
def store() -> AlertStore:
    return AlertStore(":memory:")


def test_record_and_query(store: AlertStore):
    ts = datetime(2024, 6, 1, 8, 0)
    row_id = store.record(ts, "S1", CongestionLevel.HEAVY, 42.5, "S1-rule")
    assert isinstance(row_id, int) and row_id >= 1

    rows = store.query()
    assert len(rows) == 1
    r = rows[0]
    assert r["segment_id"] == "S1"
    assert r["level"] == CongestionLevel.HEAVY.value
    assert r["speed_kmh"] == pytest.approx(42.5)
    assert r["rule_name"] == "S1-rule"


def test_filter_by_segment(store: AlertStore):
    t = datetime(2024, 6, 1, 8)
    store.record(t, "S1", CongestionLevel.HEAVY, 40.0)
    store.record(t, "S2", CongestionLevel.MODERATE, 55.0)
    assert len(store.query(segment_id="S1")) == 1
    assert len(store.query(segment_id="S2")) == 1
    assert len(store.query()) == 2


def test_filter_by_level(store: AlertStore):
    t = datetime(2024, 6, 1, 9)
    store.record(t, "S1", CongestionLevel.SEVERE, 20.0)
    store.record(t, "S1", CongestionLevel.HEAVY, 38.0)
    severes = store.query(level=CongestionLevel.SEVERE)
    assert len(severes) == 1 and severes[0]["level"] == "severe"


def test_filter_by_time_range(store: AlertStore):
    base = datetime(2024, 6, 1, 8)
    for i in range(5):
        store.record(base + timedelta(hours=i), "S1", CongestionLevel.HEAVY, 40.0)
    after = store.query(since=base + timedelta(hours=2))
    assert len(after) == 3


def test_count(store: AlertStore):
    t = datetime(2024, 6, 1)
    for _ in range(7):
        store.record(t, "S3", CongestionLevel.MODERATE, 55.0)
    assert store.count() == 7
    assert store.count(segment_id="S3") == 7
    assert store.count(segment_id="S9") == 0


def test_limit(store: AlertStore):
    t = datetime(2024, 6, 1)
    for i in range(20):
        store.record(t + timedelta(minutes=i), "S1", CongestionLevel.HEAVY, 40.0)
    assert len(store.query(limit=5)) == 5


def test_mean_time_to_resolve_none_when_too_few(store: AlertStore):
    t = datetime(2024, 6, 1)
    store.record(t, "S1", CongestionLevel.HEAVY, 40.0)
    assert store.mean_time_to_resolve("S1") is None


def test_mean_time_to_resolve(store: AlertStore):
    base = datetime(2024, 6, 1, 8)
    # Two separate incidents, each ~15 minutes apart, separated by a 2-hour gap
    for i in range(3):
        store.record(base + timedelta(minutes=i * 5), "S1", CongestionLevel.HEAVY, 40.0)
    for i in range(3):
        store.record(base + timedelta(hours=2, minutes=i * 5), "S1", CongestionLevel.HEAVY, 38.0)
    mttr = store.mean_time_to_resolve("S1")
    assert mttr is not None
    assert mttr > 0


def test_monitor_records_to_store():
    """AlertMonitor writes events to the store when threshold is met."""
    from trafficflow.alerts.monitor import AlertConfig, AlertMonitor, AlertRule
    from trafficflow.data.feed import SyntheticFeed

    feed = SyntheticFeed()
    feed.advance(12)  # 08:30 — morning congestion
    store = AlertStore(":memory:")
    rules = [AlertRule(segment_id="S1", min_level=CongestionLevel.FREE)]
    monitor = AlertMonitor(AlertConfig(rules=rules, feed=feed), store=store)
    monitor.check()
    # Free threshold is always met → at least one record
    assert store.count() >= 1
