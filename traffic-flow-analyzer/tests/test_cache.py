import threading
import time

import pandas as pd
import pytest

from trafficflow.core.cache import ResultCache, series_fingerprint


def _series(values, start="2026-01-05 08:00"):
    idx = pd.date_range(start, periods=len(values), freq="5min")
    return pd.Series(values, index=idx, dtype=float)


def test_fingerprint_is_stable_for_equal_series():
    assert series_fingerprint(_series([50, 51, 52])) == series_fingerprint(_series([50, 51, 52]))


def test_fingerprint_changes_when_a_value_changes():
    assert series_fingerprint(_series([50, 51, 52])) != series_fingerprint(_series([50, 51, 53]))


def test_fingerprint_changes_when_the_window_moves():
    a = _series([50, 51, 52])
    b = _series([50, 51, 52], start="2026-01-05 08:05")
    assert series_fingerprint(a) != series_fingerprint(b)


def test_fingerprint_distinguishes_timezones():
    idx = pd.date_range("2026-01-01", periods=5, freq="5min")
    naive = pd.Series(range(5), index=idx, dtype=float)
    utc = naive.copy()
    utc.index = idx.tz_localize("UTC")
    assert series_fingerprint(naive) != series_fingerprint(utc)


def test_cache_returns_the_stored_result_without_recomputing():
    cache, calls = ResultCache(), []
    first = cache.get_or_compute("k", lambda: calls.append(1) or object())
    second = cache.get_or_compute("k", lambda: calls.append(1) or object())
    assert first is second
    assert len(calls) == 1


def test_cache_does_not_store_exceptions():
    cache, calls = ResultCache(), []

    def boom():
        calls.append(1)
        raise ValueError("not enough history")

    for _ in range(2):
        with pytest.raises(ValueError, match="not enough history"):
            cache.get_or_compute("k", boom)
    assert len(calls) == 2
    assert len(cache) == 0


def test_concurrent_misses_compute_once():
    cache, calls, barrier = ResultCache(), [], threading.Barrier(8)
    results = []

    def slow():
        calls.append(1)
        time.sleep(0.05)
        return "value"

    def worker():
        barrier.wait()
        results.append(cache.get_or_compute("k", slow))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert results == ["value"] * 8


def test_cache_evicts_least_recently_used():
    cache = ResultCache(maxsize=2)
    cache.get_or_compute("a", lambda: 1)
    cache.get_or_compute("b", lambda: 2)
    cache.get_or_compute("a", lambda: 99)  # hit: "a" becomes most recent
    cache.get_or_compute("c", lambda: 3)  # evicts "b"
    assert len(cache) == 2
    assert cache.get_or_compute("a", lambda: 99) == 1
    assert cache.get_or_compute("b", lambda: 22) == 22


def test_cache_rejects_a_non_positive_size():
    with pytest.raises(ValueError):
        ResultCache(maxsize=0)
