"""Happy paths and data invariants for the read endpoints."""

from __future__ import annotations

from datetime import timedelta

import pytest

from api_tests.assertions import assert_ok
from api_tests.schemas import LEVEL_ORDER, Forecast, History, Segment, Status

pytestmark = pytest.mark.functional

STEP = timedelta(minutes=5)


def test_segments_match_schema_and_ids_are_unique(api):
    response = api.get("/segments")
    segments = [Segment.model_validate(s) for s in response.json()]
    ids = [s.id for s in segments]
    assert len(ids) == len(set(ids)), f"duplicate segment ids: {ids}"


def test_status_covers_exactly_the_known_segments(api, segment_ids):
    status = assert_ok(api.get("/status"), Status)
    assert set(status.segment_levels) == set(segment_ids)


def test_reads_are_repeatable(api, segment_id):
    for path in ("/segments", "/status", f"/forecast/{segment_id}"):
        first, second = api.get(path), api.get(path)
        assert first.json() == second.json(), f"{path} changed between identical reads"


# -- forecast ----------------------------------------------------------------


@pytest.mark.parametrize("horizon", [5, 15, 60, 180])
def test_forecast_horizon_sets_the_number_of_steps(api, segment_id, horizon):
    fc = assert_ok(api.get(f"/forecast/{segment_id}", params={"horizon": horizon}), Forecast)
    assert fc.segment_id == segment_id
    assert fc.horizon_minutes == horizon
    assert len(fc.points) == horizon // 5


def test_forecast_is_evenly_spaced_and_starts_after_now(api, segment_id):
    as_of = assert_ok(api.get("/status"), Status).as_of
    fc = assert_ok(api.get(f"/forecast/{segment_id}", params={"horizon": 120}), Forecast)
    assert fc.points[0].ts == as_of + STEP
    gaps = {b.ts - a.ts for a, b in zip(fc.points, fc.points[1:], strict=False)}
    assert gaps == {STEP}


def test_forecast_interval_contains_the_point_estimate(api, segment_ids):
    for sid in segment_ids:
        fc = assert_ok(api.get(f"/forecast/{sid}", params={"horizon": 120}), Forecast)
        for p in fc.points:
            assert p.lower_kmh <= p.speed_kmh <= p.upper_kmh, f"{sid} {p.ts}: {p}"


def test_forecast_peak_is_the_highest_point(api, segment_ids):
    for sid in segment_ids:
        fc = assert_ok(api.get(f"/forecast/{sid}", params={"horizon": 120}), Forecast)
        peak = max(fc.points, key=lambda p: p.congestion_index)
        assert fc.peak_index == pytest.approx(peak.congestion_index, abs=1e-3)
        assert fc.peak_level == peak.level


def test_congestion_level_never_drops_as_the_index_rises(api, segment_ids):
    points = []
    for sid in segment_ids:
        fc = assert_ok(api.get(f"/forecast/{sid}", params={"horizon": 180}), Forecast)
        points += fc.points
    points.sort(key=lambda p: p.congestion_index)
    ranks = [LEVEL_ORDER[p.level] for p in points]
    assert ranks == sorted(ranks), "a higher congestion index mapped to a lower level"


def test_forecast_speed_is_consistent_with_the_index(api, segment_ids):
    """Slower forecast speed => higher congestion index, within one segment."""
    for sid in segment_ids:
        fc = assert_ok(api.get(f"/forecast/{sid}", params={"horizon": 180}), Forecast)
        by_speed = sorted(fc.points, key=lambda p: p.speed_kmh)
        indexes = [p.congestion_index for p in by_speed]
        assert indexes == sorted(indexes, reverse=True), sid


# -- history -----------------------------------------------------------------


def test_history_is_ordered_and_inside_the_window(api, segment_id):
    days = 0.5
    h = assert_ok(api.get(f"/history/{segment_id}", params={"days": days}), History)
    assert h.segment_id == segment_id and h.days == days
    stamps = [r.ts for r in h.readings]
    assert stamps == sorted(stamps) and len(stamps) == len(set(stamps))
    assert stamps[-1] - stamps[0] <= timedelta(days=days)


def test_history_ends_at_the_status_time(api, segment_id):
    as_of = assert_ok(api.get("/status"), Status).as_of
    h = assert_ok(api.get(f"/history/{segment_id}", params={"days": 0.1}), History)
    assert h.readings[-1].ts == as_of


def test_longer_history_contains_the_shorter_one(api, segment_id):
    short = assert_ok(api.get(f"/history/{segment_id}", params={"days": 0.1}), History)
    long = assert_ok(api.get(f"/history/{segment_id}", params={"days": 1}), History)
    assert len(long.readings) > len(short.readings)
    assert long.readings[-len(short.readings) :] == short.readings
