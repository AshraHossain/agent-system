"""Data freshness: how old the latest reading is relative to the feed clock."""

from __future__ import annotations

from datetime import datetime

STALE_AFTER_MINUTES = 15  # three missed 5-minute readings


def _naive(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def data_age_minutes(last_reading: datetime, now: datetime) -> float:
    """Minutes between the latest reading and `now` (never negative)."""
    if (last_reading.tzinfo is None) != (now.tzinfo is None):
        last_reading, now = _naive(last_reading), _naive(now)
    return max(0.0, (now - last_reading).total_seconds() / 60)


def is_stale(last_reading: datetime, now: datetime) -> bool:
    return data_age_minutes(last_reading, now) > STALE_AFTER_MINUTES
