"""Live traffic API adapter template.

Subclass ``LiveFeed`` and override ``history()`` to connect a real traffic
data provider (HERE, TomTom, Google Maps Traffic, etc.).  The rest of the
pipeline — agents, tools, dashboard — works without modification because
``LiveFeed`` satisfies the ``Feed`` protocol.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from trafficflow.contracts import SegmentInfo

if TYPE_CHECKING:
    pass


class LiveFeed:
    """Base adapter for live traffic APIs.

    Subclasses must override ``history()`` to fetch real speed readings from
    a provider.  ``segments()`` returns the list passed at construction; you
    can also override it to pull segment metadata from the API.

    Example::

        class MyFeed(LiveFeed):
            def history(self, segment_id: str, days: float = 7) -> pd.Series:
                raw = my_client.get_speed_history(segment_id, days=days)
                return pd.Series(raw["speed_kmh"], index=pd.to_datetime(raw["ts"]))

        feed = MyFeed(api_key="...", segments=[SegmentInfo(...)])
    """

    def __init__(self, api_key: str, segments: list[SegmentInfo]) -> None:
        self._api_key = api_key
        self._segments = segments

    @property
    def now(self) -> datetime:
        return datetime.now()

    def segments(self) -> list[SegmentInfo]:
        return self._segments

    def history(self, segment_id: str, days: float = 7) -> pd.Series:
        """Fetch speed history for one segment.

        Override in subclasses to call the live API.  Must return a
        ``pd.Series`` with a ``DatetimeIndex`` and float speed values in km/h,
        sorted ascending, at roughly 5-minute intervals.
        """
        raise NotImplementedError(
            "LiveFeed requires a concrete adapter subclass; override history()."
        )

    def advance(self, steps: int = 1) -> datetime:
        """No-op for live feeds — time advances on its own."""
        return self.now


class HereLiveFeed(LiveFeed):
    """Stub adapter for HERE Traffic API.

    Requires the HERE Python SDK::

        pip install here-sdk

    and a HERE API key (https://developer.here.com/).  Set via::

        feed = HereLiveFeed(api_key=os.environ["HERE_API_KEY"], segments=[...])

    Then pass ``TRAFFICFLOW_MODEL=<provider:model>`` to use a live AI model.
    """

    def history(self, segment_id: str, days: float = 7) -> pd.Series:
        raise NotImplementedError(
            "HERE SDK not installed or not configured; "
            "install here-sdk and implement the HERE Traffic API call."
        )
