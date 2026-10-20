"""Segment road-graph: adjacency topology and path-level travel-time queries.

The graph is a simple undirected weighted graph where nodes are segment IDs
and edges carry a distance (km).  Edge weights are updated from live speed
readings to give real-time travel times.

Default topology is the six synthetic segments near Los Angeles; override
``DEFAULT_EDGES`` or pass a custom adjacency list to ``SegmentGraph``.

Usage::

    from trafficflow.core.graph import SegmentGraph, DEFAULT_EDGES
    from trafficflow.data.feed import SyntheticFeed

    feed = SyntheticFeed()
    feed.advance(12)
    g = SegmentGraph()
    g.update_speeds(feed)
    result = g.shortest_path("S1", "S6")
    print(result)
    # PathResult(path=["S1", "S2", "S6"], total_km=3.4, travel_minutes=2.8, bottleneck="S2")
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trafficflow.data.feed import Feed

# ---------------------------------------------------------------------------
# Default topology
# ---------------------------------------------------------------------------

# Each edge is (node_a, node_b, distance_km).
# The network roughly matches the six synthetic segments laid out near LA.
DEFAULT_EDGES: list[tuple[str, str, float]] = [
    ("S1", "S2", 1.2),
    ("S1", "S5", 1.0),
    ("S2", "S3", 0.9),
    ("S2", "S4", 1.5),
    ("S2", "S6", 1.8),
    ("S3", "S4", 0.7),
    ("S4", "S5", 1.1),
    ("S5", "S6", 2.0),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PathResult:
    path: list[str]
    total_km: float
    travel_minutes: float
    bottleneck: str  # segment on the path with the worst congestion index


@dataclass
class SegmentGraph:
    """Undirected weighted road graph.

    Parameters
    ----------
    edges:
        List of (node_a, node_b, distance_km) tuples.  Defaults to the six
        synthetic segments.
    """

    edges: list[tuple[str, str, float]] = field(default_factory=lambda: list(DEFAULT_EDGES))
    _speeds: dict[str, float] = field(default_factory=dict, init=False)
    _free_flow: dict[str, float] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------
    # Graph construction helpers
    # ------------------------------------------------------------------

    def nodes(self) -> set[str]:
        s: set[str] = set()
        for a, b, _ in self.edges:
            s.add(a)
            s.add(b)
        return s

    def neighbors(self, node: str) -> list[tuple[str, float]]:
        result: list[tuple[str, float]] = []
        for a, b, d in self.edges:
            if a == node:
                result.append((b, d))
            elif b == node:
                result.append((a, d))
        return result

    # ------------------------------------------------------------------
    # Speed / free-flow updates
    # ------------------------------------------------------------------

    def update_speeds(self, feed: Feed) -> None:
        """Refresh segment speeds from the feed's latest readings."""
        for seg in feed.segments():
            self._free_flow[seg.id] = seg.free_flow_kmh
            try:
                hist = feed.history(seg.id, days=0.02)
                if not hist.empty:
                    self._speeds[seg.id] = float(hist.iloc[-1])
            except Exception:  # noqa: S110
                pass

    def set_speed(self, segment_id: str, speed_kmh: float, free_flow_kmh: float = 80.0) -> None:
        self._speeds[segment_id] = speed_kmh
        self._free_flow[segment_id] = free_flow_kmh

    def travel_time_minutes(self, segment_id: str, distance_km: float) -> float:
        """Minutes to traverse *distance_km* of *segment_id* at its current speed."""
        speed = self._speeds.get(segment_id, 60.0)
        speed = max(speed, 1.0)
        return (distance_km / speed) * 60.0

    # ------------------------------------------------------------------
    # Path queries
    # ------------------------------------------------------------------

    def shortest_path(self, source: str, target: str, weight: str = "time") -> PathResult | None:
        """Dijkstra's shortest path by travel time (or distance).

        Parameters
        ----------
        weight:
            ``"time"`` (default) minimises travel time; ``"distance"`` minimises km.

        Returns ``None`` when no path exists.
        """
        if source not in self.nodes() or target not in self.nodes():
            return None

        dist: dict[str, float] = {source: 0.0}
        prev: dict[str, str | None] = {source: None}
        pq: list[tuple[float, str]] = [(0.0, source)]

        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, math.inf):
                continue
            if u == target:
                break
            for v, edge_km in self.neighbors(u):
                w = self.travel_time_minutes(u, edge_km) if weight == "time" else edge_km
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        if target not in dist:
            return None

        # Reconstruct path
        path: list[str] = []
        cur: str | None = target
        while cur is not None:
            path.append(cur)
            cur = prev.get(cur)
        path.reverse()

        # Compute total_km and bottleneck
        total_km = 0.0
        total_minutes = 0.0
        bottleneck = path[0]
        worst_idx = 0.0
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            edge_km = next(
                (d for (x, y, d) in self.edges if (x == a and y == b) or (x == b and y == a)),
                0.0,
            )
            total_km += edge_km
            total_minutes += self.travel_time_minutes(a, edge_km)
            from trafficflow.core.congestion import congestion_index

            spd = self._speeds.get(a, 60.0)
            ff = self._free_flow.get(a, 80.0)
            idx = congestion_index(spd, ff)
            if idx > worst_idx:
                worst_idx = idx
                bottleneck = a

        return PathResult(
            path=path,
            total_km=round(total_km, 2),
            travel_minutes=round(total_minutes, 2),
            bottleneck=bottleneck,
        )

    def worst_segment(self, path: list[str] | None = None) -> str | None:
        """Return the segment ID with the highest congestion index.

        If *path* is given, restrict to that subset; otherwise consider all
        segments that have a known speed.
        """
        from trafficflow.core.congestion import congestion_index

        candidates = path if path is not None else list(self._speeds.keys())
        best: str | None = None
        best_idx = -1.0
        for seg in candidates:
            spd = self._speeds.get(seg)
            ff = self._free_flow.get(seg, 80.0)
            if spd is None:
                continue
            idx = congestion_index(spd, ff)
            if idx > best_idx:
                best_idx = idx
                best = seg
        return best
