"""Routing, the question endpoint and the live stream."""

from __future__ import annotations

import itertools
import json
import time

import pytest

from api_tests.assertions import assert_ok
from api_tests.schemas import Answer, Route, StreamEvent

pytestmark = pytest.mark.functional


def _route(api, source, target, weight="time") -> Route:
    return assert_ok(api.get(f"/path/{source}/{target}", params={"weight": weight}), Route)


def test_every_route_is_a_simple_path_between_its_ends(api, segment_ids):
    for source, target in itertools.permutations(segment_ids, 2):
        response = api.get(f"/path/{source}/{target}")
        if response.status_code == 404:  # unreachable is allowed; checked below
            continue
        route = assert_ok(response, Route)
        assert (route.source, route.target) == (source, target)
        assert route.path[0] == source and route.path[-1] == target
        assert len(route.path) == len(set(route.path)), f"cycle in {route.path}"
        assert set(route.path) <= set(segment_ids)
        assert route.bottleneck in route.path


def test_at_least_one_pair_is_connected(api, segment_ids):
    statuses = {
        api.get(f"/path/{s}/{t}").status_code for s, t in itertools.permutations(segment_ids, 2)
    }
    assert 200 in statuses


def test_route_to_itself_is_empty(api, segment_id):
    route = _route(api, segment_id, segment_id)
    assert route.path == [segment_id]
    assert route.total_km == 0 and route.travel_minutes == 0


def test_each_weight_optimises_its_own_metric(api, segment_ids):
    for source, target in itertools.permutations(segment_ids, 2):
        if api.get(f"/path/{source}/{target}").status_code != 200:
            continue
        by_time = _route(api, source, target, "time")
        by_distance = _route(api, source, target, "distance")
        assert by_time.travel_minutes <= by_distance.travel_minutes + 1e-6
        assert by_distance.total_km <= by_time.total_km + 1e-6


# -- /ask --------------------------------------------------------------------


def test_ask_returns_an_answer(api):
    answer = assert_ok(api.post("/ask", json={"question": "Which road is busiest?"}), Answer)
    assert answer.answer.strip()


def test_ask_accepts_conversation_history(api):
    body = {
        "question": "And in an hour?",
        "history": [
            {"role": "user", "content": "Is the bridge congested?"},
            {"role": "assistant", "content": "Yes, heavy."},
        ],
    }
    assert_ok(api.post("/ask", json=body), Answer)


# -- /stream -----------------------------------------------------------------


def _events(api, n: int, interval: float) -> list[tuple[float, StreamEvent]]:
    events = []
    with api.stream("GET", "/stream", params={"interval": interval}) as response:
        for line in response.iter_lines():
            if line.startswith("data:"):
                payload = json.loads(line.removeprefix("data:"))
                events.append((time.monotonic(), StreamEvent.model_validate(payload)))
                if len(events) == n:
                    break
    return events


def test_stream_events_cover_every_segment(api, segment_ids):
    [(_, event)] = _events(api, 1, interval=1)
    assert set(event.segments) == set(segment_ids)


def test_stream_interval_is_clamped_to_at_least_one_second(api):
    (t1, _), (t2, _) = _events(api, 2, interval=0.01)
    assert t2 - t1 >= 0.9
