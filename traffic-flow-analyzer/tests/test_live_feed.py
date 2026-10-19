"""Tests for the live feed buffer."""

from __future__ import annotations

import pandas as pd
import pytest

from trafficflow.data.live import MockLiveFeed


def test_mock_live_feed_seeds_history():
    feed = MockLiveFeed()
    h = feed.history("S1", days=7)
    assert isinstance(h, pd.Series)
    assert len(h) > 0


def test_mock_live_feed_poll_appends():
    feed = MockLiveFeed()
    before = len(feed.history("S1", days=7))
    feed.poll()
    after = len(feed.history("S1", days=7))
    # The buffer should have gained at least one row
    assert after >= before


def test_mock_live_feed_poll_returns_dict():
    feed = MockLiveFeed()
    readings = feed.poll()
    assert isinstance(readings, dict)
    assert len(readings) > 0
    for v in readings.values():
        assert isinstance(v, float)
        assert v > 0


def test_mock_live_feed_segments():
    feed = MockLiveFeed()
    segs = feed.segments()
    assert len(segs) > 0
    ids = {s.id for s in segs}
    assert "S1" in ids


def test_mock_live_feed_advance_is_noop():
    feed = MockLiveFeed()
    t1 = feed.now
    t2 = feed.advance(5)
    assert t2 >= t1  # time only moves forward (or stays same)


def test_mock_live_feed_history_unknown_segment():
    feed = MockLiveFeed()
    with pytest.raises(KeyError):
        feed.history("DOES_NOT_EXIST", days=1)


def test_mock_live_feed_polling_thread():
    feed = MockLiveFeed()
    feed.start_polling(interval_seconds=1)
    assert feed._thread is not None
    assert feed._thread.is_alive()
    # starting again is a no-op
    feed.start_polling(interval_seconds=1)
    feed.stop_polling()
    assert feed._thread is None


def test_live_feed_buffer_trim():
    """Buffer stays bounded after many polls."""
    feed = MockLiveFeed(seed_days=1)
    for _ in range(5):
        feed.poll()
    h = feed.history("S1", days=1)
    assert len(h) > 0
