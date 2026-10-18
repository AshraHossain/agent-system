"""Congestion index and level thresholds. Rules live here, never in prompts."""

from __future__ import annotations

from trafficflow.contracts import CongestionLevel

# index = 1 - speed / free_flow
THRESHOLDS = (
    (0.20, CongestionLevel.FREE),
    (0.40, CongestionLevel.MODERATE),
    (0.60, CongestionLevel.HEAVY),
)


def congestion_index(speed_kmh: float, free_flow_kmh: float) -> float:
    return max(0.0, min(1.0, 1.0 - speed_kmh / free_flow_kmh))


def level_for(index: float) -> CongestionLevel:
    for limit, level in THRESHOLDS:
        if index < limit:
            return level
    return CongestionLevel.SEVERE
