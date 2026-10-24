"""Live traffic feed adapters with rolling in-memory history buffer.

Quick start — mock feed (no API key, backed by SyntheticFeed):
    from trafficflow.data.live import MockLiveFeed
    feed = MockLiveFeed()
    feed.start_polling(interval_seconds=5)   # background thread; optional
    # or just call feed.poll() manually

Real traffic:
    feed = TomTomLiveFeed(api_key=os.environ["TOMTOM_API_KEY"])
    feed.start_polling(interval_seconds=60)

    feed = HereLiveFeed(api_key=os.environ["HERE_API_KEY"])
    feed.start_polling(interval_seconds=60)

All adapters satisfy the Feed protocol, so the full pipeline — agents,
tools, Gradio dashboard — works without modification.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from trafficflow.contracts import SegmentInfo
from trafficflow.core.simulate import SEGMENTS

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Fixed coordinates for the synthetic segment network (near Los Angeles).
# Override _COORDS in subclasses to use your own road network.
_DEFAULT_COORDS: dict[str, tuple[float, float]] = {
    "S1": (34.05, -118.25),
    "S2": (34.06, -118.24),
    "S3": (34.07, -118.23),
    "S4": (34.05, -118.22),
    "S5": (34.04, -118.24),
    "S6": (34.06, -118.26),
}

_BUFFER_DAYS = 7  # how many days of history to keep in memory


class LiveFeedBuffer:
    """Rolling in-memory buffer for any live traffic API.

    Subclass and override ``_fetch_current()`` to connect a real provider.
    The buffer is seeded with synthetic data on start-up so forecasts work
    immediately; live readings layer on top as they arrive.

    Thread-safe: ``history()`` and ``poll()`` share a lock.

    Subclassing example::

        class MyFeed(LiveFeedBuffer):
            def _fetch_current(self) -> dict[str, float]:
                # return {segment_id: speed_kmh}
                return my_client.latest_speeds()

        feed = MyFeed(segments=[SegmentInfo(id="A1", name="Main St", free_flow_kmh=80)])
        feed.start_polling(interval_seconds=30)
    """

    _COORDS: dict[str, tuple[float, float]] = _DEFAULT_COORDS

    def __init__(
        self,
        api_key: str = "",
        segments: list[SegmentInfo] | None = None,
        seed_days: int = _BUFFER_DAYS,
    ) -> None:
        self._api_key = api_key
        self._segments = segments or list(SEGMENTS)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._buf: pd.DataFrame = self._seed(seed_days)

    # ------------------------------------------------------------------
    # Feed protocol
    # ------------------------------------------------------------------

    @property
    def now(self) -> datetime:
        return datetime.now()

    def segments(self) -> list[SegmentInfo]:
        return self._segments

    def history(self, segment_id: str, days: float = 7) -> pd.Series:
        """Return buffered speed history for *segment_id* (km/h, ascending)."""
        cutoff = datetime.now() - timedelta(days=days)
        with self._lock:
            if segment_id not in self._buf.columns:
                raise KeyError(segment_id)
            s = self._buf[segment_id].dropna()
        return s[s.index > cutoff]

    def advance(self, steps: int = 1) -> datetime:
        """No-op: time advances naturally for live feeds."""
        return self.now

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll(self) -> dict[str, float]:
        """Fetch current speeds once and append to the buffer.

        Rounds the timestamp to the nearest minute for a tidy time index.
        Returns the readings dict (may be empty if the provider is unreachable).
        """
        ts = datetime.now().replace(second=0, microsecond=0)
        readings = self._fetch_current()
        if readings:
            row = pd.DataFrame({k: [v] for k, v in readings.items()}, index=[ts])
            with self._lock:
                self._buf = pd.concat([self._buf, row])
                # Trim to keep only the last _BUFFER_DAYS worth of data.
                cutoff = datetime.now() - timedelta(days=_BUFFER_DAYS)
                self._buf = self._buf[self._buf.index >= cutoff]
        return readings

    def start_polling(self, interval_seconds: int = 30) -> None:
        """Start a background daemon thread calling ``poll()`` repeatedly."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(interval_seconds,),
            daemon=True,
            name=f"LiveFeedBuffer-{type(self).__name__}",
        )
        self._thread.start()

    def stop_polling(self) -> None:
        """Stop the background polling thread (blocks until it exits, max 5 s)."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    # ------------------------------------------------------------------
    # Extension point
    # ------------------------------------------------------------------

    def _fetch_current(self) -> dict[str, float]:
        """Return {segment_id: speed_kmh} for the current moment.

        Override in subclasses to call a live API.
        Exceptions raised here are silently swallowed by the poll loop.
        """
        return {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _seed(self, days: int) -> pd.DataFrame:
        """Seed the buffer with synthetic history so forecasts work immediately."""
        from trafficflow.core.simulate import simulate

        now = datetime.now().replace(second=0, microsecond=0)
        start = now - timedelta(days=days)
        sim = simulate(start, days=days)
        seg_ids = [s.id for s in self._segments]
        cols = [c for c in seg_ids if c in sim.columns]
        return sim.loc[sim.index <= now, cols].copy() if cols else pd.DataFrame(index=[])

    def _poll_loop(self, interval_seconds: int) -> None:
        while not self._stop.wait(interval_seconds):
            try:
                self.poll()
            except Exception:
                logger.debug("poll() failed", exc_info=True)


# ---------------------------------------------------------------------------
# Concrete adapters
# ---------------------------------------------------------------------------


class TomTomLiveFeed(LiveFeedBuffer):
    """Live feed via TomTom Flow Segment Data API (free tier: 2 500 req/day).

    API key: https://developer.tomtom.com/

    Usage::

        import os
        from trafficflow.data.live import TomTomLiveFeed

        feed = TomTomLiveFeed(api_key=os.environ["TOMTOM_API_KEY"])
        feed.start_polling(interval_seconds=60)

    Response field used: ``flowSegmentData.currentSpeed`` (km/h).
    One HTTP request is made per segment per poll; the free tier accommodates
    six segments polled every minute within the daily quota.
    """

    _BASE = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

    def _fetch_current(self) -> dict[str, float]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required: pip install httpx") from exc

        results: dict[str, float] = {}
        for seg in self._segments:
            lat, lon = self._COORDS.get(seg.id, (34.05, -118.25))
            try:
                r = httpx.get(
                    self._BASE,
                    params={"point": f"{lat},{lon}", "key": self._api_key},
                    timeout=10,
                )
                r.raise_for_status()
                data = r.json()
                speed = data.get("flowSegmentData", {}).get("currentSpeed")
                if speed is not None:
                    results[seg.id] = float(speed)
            except Exception:
                logger.debug("TomTom fetch failed for %s", seg.id, exc_info=True)
        return results


class HereLiveFeed(LiveFeedBuffer):
    """Live feed via HERE Traffic Flow API v7 (free tier: 250 000 req/month).

    API key: https://developer.here.com/

    Usage::

        import os
        from trafficflow.data.live import HereLiveFeed

        feed = HereLiveFeed(api_key=os.environ["HERE_API_KEY"])
        feed.start_polling(interval_seconds=60)

    Response field used: ``results[0].currentFlow.speed`` (km/h).
    One HTTP request per segment per poll; the free tier handles six segments
    polled every minute well within the monthly quota.
    """

    _BASE = "https://data.traffic.hereapi.com/v7/flow"

    def _fetch_current(self) -> dict[str, float]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required: pip install httpx") from exc

        results: dict[str, float] = {}
        for seg in self._segments:
            lat, lon = self._COORDS.get(seg.id, (34.05, -118.25))
            try:
                r = httpx.get(
                    self._BASE,
                    params={"in": f"circle:{lat},{lon};r=200", "apiKey": self._api_key},
                    timeout=10,
                )
                r.raise_for_status()
                data = r.json()
                items = data.get("results", [])
                if items:
                    speed = items[0].get("currentFlow", {}).get("speed")
                    if speed is not None:
                        results[seg.id] = float(speed)
            except Exception:
                logger.debug("HERE fetch failed for %s", seg.id, exc_info=True)
        return results


class MockLiveFeed(LiveFeedBuffer):
    """Deterministic mock adapter backed by SyntheticFeed.

    Useful for offline tests and demos — no API key needed.
    Each ``poll()`` advances the underlying SyntheticFeed by one step (5 min).

    Usage::

        from trafficflow.data.live import MockLiveFeed

        feed = MockLiveFeed()
        feed.start_polling(interval_seconds=5)   # optional background thread
        # or: feed.poll()  # manual step
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._syn: object | None = None  # lazy

    def _syn_feed(self):
        if self._syn is None:
            from trafficflow.data.feed import SyntheticFeed

            self._syn = SyntheticFeed()
        return self._syn

    def _fetch_current(self) -> dict[str, float]:
        syn = self._syn_feed()
        syn.advance(1)
        readings: dict[str, float] = {}
        for seg in self._segments:
            try:
                h = syn.history(seg.id, days=0.01)  # tiny window — last reading
                if not h.empty:
                    readings[seg.id] = float(h.iloc[-1])
            except Exception:
                logger.debug("Mock fetch failed for %s", seg.id, exc_info=True)
        return readings
