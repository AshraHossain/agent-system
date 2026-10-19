"""Tests for the alerting monitor."""

from __future__ import annotations

from unittest.mock import patch

from trafficflow.alerts.monitor import AlertConfig, AlertEvent, AlertMonitor, AlertRule
from trafficflow.contracts import CongestionLevel
from trafficflow.data.live import MockLiveFeed


def _make_monitor(level=CongestionLevel.MODERATE, webhook=""):
    feed = MockLiveFeed()
    rules = [
        AlertRule(segment_id="S1", min_level=level, webhook_url=webhook, name="Test S1"),
        AlertRule(segment_id="S2", min_level=level, webhook_url=webhook),
    ]
    return AlertMonitor(AlertConfig(rules=rules, feed=feed))


def test_check_returns_list():
    monitor = _make_monitor()
    # Seed a reading by polling
    monitor.config.feed.poll()
    result = monitor.check()
    assert isinstance(result, list)
    # Every element is an AlertEvent
    for ev in result:
        assert isinstance(ev, AlertEvent)


def test_check_event_fields():
    monitor = _make_monitor(level=CongestionLevel.FREE)  # FREE always triggers
    monitor.config.feed.poll()
    events = monitor.check()
    assert len(events) > 0
    ev = events[0]
    assert ev.segment_id in {"S1", "S2"}
    assert isinstance(ev.level, CongestionLevel)
    assert ev.speed_kmh > 0


def test_check_threshold_not_met():
    """With SEVERE threshold, FREE-flow roads should not alert."""
    monitor = _make_monitor(level=CongestionLevel.SEVERE)
    monitor.config.feed.poll()
    # Synthetic data near free-flow — SEVERE should rarely fire
    events = monitor.check()
    for ev in events:
        assert ev.level == CongestionLevel.SEVERE


def test_payload_json():
    from datetime import datetime

    monitor = _make_monitor()
    rule = monitor.config.rules[0]
    ev = AlertEvent(
        ts=datetime(2026, 1, 1, 8, 0),
        segment_id="S1",
        level=CongestionLevel.HEAVY,
        speed_kmh=40.0,
        rule=rule,
    )
    payload = AlertMonitor.payload_json(ev)
    import json

    obj = json.loads(payload)
    assert obj["segment_id"] == "S1"
    assert obj["level"] == "heavy"
    assert obj["speed_kmh"] == 40.0


def test_webhook_fires_on_new_alert():
    """Webhook POST is called when threshold first crossed."""
    monitor = _make_monitor(level=CongestionLevel.FREE, webhook="https://example.com/hook")
    monitor.config.feed.poll()
    with patch.object(monitor, "_fire") as mock_fire:
        monitor.check()
        # _fire should have been called for at least one segment
        assert mock_fire.called


def test_webhook_not_refired_on_second_check():
    """Webhook is not repeated if segment is already in active set."""
    monitor = _make_monitor(level=CongestionLevel.FREE, webhook="https://example.com/hook")
    monitor.config.feed.poll()
    # First check — fires
    with patch.object(monitor, "_fire"):
        monitor.check()

    # Second check — already active, should not fire again
    with patch.object(monitor, "_fire") as mock_fire2:
        monitor.check()
        assert mock_fire2.call_count == 0


def test_stop_cancels_run():
    """stop() terminates run() quickly."""
    import threading

    monitor = _make_monitor()
    done = threading.Event()

    def _run():
        monitor.run(poll_interval_seconds=60)
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    monitor.stop()
    t.join(timeout=3)
    assert done.is_set()
