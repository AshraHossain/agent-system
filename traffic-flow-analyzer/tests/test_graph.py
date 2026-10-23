"""Tests for the segment road graph and path queries."""

import pytest

from trafficflow.core.graph import DEFAULT_EDGES, PathResult, SegmentGraph


@pytest.fixture
def graph() -> SegmentGraph:
    g = SegmentGraph()
    # Seed speeds so travel times are deterministic
    for seg_id in ["S1", "S2", "S3", "S4", "S5", "S6"]:
        g.set_speed(seg_id, 60.0, free_flow_kmh=80.0)
    return g


def test_nodes_count(graph: SegmentGraph):
    assert len(graph.nodes()) == 6


def test_neighbors(graph: SegmentGraph):
    neighbors = dict(graph.neighbors("S1"))
    assert "S2" in neighbors
    assert "S5" in neighbors


def test_shortest_path_returns_path_result(graph: SegmentGraph):
    result = graph.shortest_path("S1", "S6")
    assert isinstance(result, PathResult)
    assert result.path[0] == "S1"
    assert result.path[-1] == "S6"


def test_shortest_path_connected(graph: SegmentGraph):
    result = graph.shortest_path("S1", "S3")
    assert result is not None
    assert "S1" in result.path and "S3" in result.path


def test_shortest_path_none_for_missing_node(graph: SegmentGraph):
    result = graph.shortest_path("S1", "UNKNOWN")
    assert result is None


def test_shortest_path_self(graph: SegmentGraph):
    result = graph.shortest_path("S1", "S1")
    assert result is not None
    assert result.path == ["S1"]
    assert result.travel_minutes == pytest.approx(0.0)


def test_path_by_distance(graph: SegmentGraph):
    r_time = graph.shortest_path("S1", "S6", weight="time")
    r_dist = graph.shortest_path("S1", "S6", weight="distance")
    assert r_time is not None and r_dist is not None
    # Both paths are valid even if they differ
    assert r_time.total_km > 0
    assert r_dist.total_km > 0


def test_bottleneck_is_on_path(graph: SegmentGraph):
    result = graph.shortest_path("S1", "S6")
    assert result is not None
    assert result.bottleneck in result.path


def test_travel_time_scales_with_speed(graph: SegmentGraph):
    g_fast = SegmentGraph()
    g_slow = SegmentGraph()
    for seg_id in ["S1", "S2", "S3", "S4", "S5", "S6"]:
        g_fast.set_speed(seg_id, 100.0, 100.0)
        g_slow.set_speed(seg_id, 20.0, 80.0)
    fast = g_fast.shortest_path("S1", "S6")
    slow = g_slow.shortest_path("S1", "S6")
    assert fast is not None and slow is not None
    assert fast.travel_minutes < slow.travel_minutes


def test_worst_segment(graph: SegmentGraph):
    # Manually set one segment to a very slow speed
    graph.set_speed("S3", 10.0, 80.0)
    worst = graph.worst_segment()
    assert worst == "S3"


def test_worst_segment_subset(graph: SegmentGraph):
    graph.set_speed("S2", 10.0, 80.0)
    worst = graph.worst_segment(path=["S1", "S2", "S3"])
    assert worst == "S2"


def test_update_speeds_from_feed():
    from trafficflow.data.feed import SyntheticFeed

    feed = SyntheticFeed()
    feed.advance(12)
    g = SegmentGraph()
    g.update_speeds(feed)
    # After update, travel times should reflect feed speeds
    result = g.shortest_path("S1", "S6")
    assert result is not None
    assert result.travel_minutes > 0


def test_default_edges_are_symmetric():
    g = SegmentGraph()
    for a, b, _ in DEFAULT_EDGES:
        nb_a = dict(g.neighbors(a))
        nb_b = dict(g.neighbors(b))
        assert b in nb_a, f"{b} not a neighbor of {a}"
        assert a in nb_b, f"{a} not a neighbor of {b}"
