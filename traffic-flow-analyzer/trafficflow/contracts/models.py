"""Typed contracts shared by core, tools, agents and the UI. No AI-framework imports."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Anomaly",
    "CongestionLevel",
    "Finding",
    "ForecastFinding",
    "ForecastPoint",
    "IncidentFinding",
    "IncidentNote",
    "NetworkStatus",
    "SegmentForecast",
    "SegmentInfo",
    "SegmentOutlook",
    "TrafficBrief",
    "TrafficReport",
]


class CongestionLevel(StrEnum):
    FREE = "free"
    MODERATE = "moderate"
    HEAVY = "heavy"
    SEVERE = "severe"


class NetworkStatus(StrEnum):
    NORMAL = "normal"
    WATCH = "watch"
    DEGRADED = "degraded"


class SegmentInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    free_flow_kmh: float = Field(gt=0)


class ForecastPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    ts: datetime
    speed_kmh: float
    lower_kmh: float
    upper_kmh: float
    congestion_index: float
    level: CongestionLevel


class SegmentForecast(BaseModel):
    model_config = ConfigDict(frozen=True)
    segment_id: str
    method: str
    horizon_minutes: int
    points: list[ForecastPoint]
    peak_index: float
    peak_ts: datetime
    peak_level: CongestionLevel


class Anomaly(BaseModel):
    model_config = ConfigDict(frozen=True)
    segment_id: str
    start: datetime
    points: int
    min_speed_kmh: float
    robust_z: float


# --- LLM-facing output contracts (analysts and writer) ---------------------------------


class SegmentOutlook(BaseModel):
    model_config = ConfigDict(frozen=True)
    segment_id: str
    note: str = Field(max_length=300)
    evidence_ids: list[str] = Field(min_length=1)


class ForecastFinding(BaseModel):
    model_config = ConfigDict(frozen=True)
    outlooks: list[SegmentOutlook]


class IncidentNote(BaseModel):
    model_config = ConfigDict(frozen=True)
    segment_id: str
    summary: str = Field(max_length=300)
    evidence_ids: list[str] = Field(min_length=1)


class IncidentFinding(BaseModel):
    model_config = ConfigDict(frozen=True)
    incidents: list[IncidentNote]


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)
    claim: str = Field(max_length=400)
    segment_ids: list[str]
    evidence_ids: list[str] = Field(min_length=1)


class TrafficBrief(BaseModel):
    model_config = ConfigDict(frozen=True)
    headline: str = Field(max_length=160)
    summary: str = Field(max_length=1200)
    findings: list[Finding]
    recommendations: list[str] = Field(max_length=6)


class TrafficReport(BaseModel):
    """Final, verified report. `status` is computed by rule in core, never by an LLM."""

    model_config = ConfigDict(frozen=True)
    as_of: datetime
    horizon_minutes: int
    status: NetworkStatus
    brief: TrafficBrief
    evidence: dict[str, dict]
    issues: list[str] = Field(default_factory=list)
    used_model: str
    stale_segments: list[str] = Field(default_factory=list)
