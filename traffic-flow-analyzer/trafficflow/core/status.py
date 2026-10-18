"""Network status and escalation by rule; the LLM never sets it."""

from __future__ import annotations

from trafficflow.contracts import Anomaly, CongestionLevel, NetworkStatus, SegmentForecast

_RANK = {
    CongestionLevel.FREE: 0,
    CongestionLevel.MODERATE: 1,
    CongestionLevel.HEAVY: 2,
    CongestionLevel.SEVERE: 3,
}


def network_status(forecasts: list[SegmentForecast], anomalies: list[Anomaly]) -> NetworkStatus:
    worst = max((_RANK[f.peak_level] for f in forecasts), default=0)
    if worst >= 3 or (anomalies and worst >= 2):
        return NetworkStatus.DEGRADED
    if worst >= 2 or anomalies:
        return NetworkStatus.WATCH
    return NetworkStatus.NORMAL
