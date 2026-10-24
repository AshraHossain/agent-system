"""Expected response shapes, written from the API's documented behaviour.

Deliberately independent of the target's own models: if the service changes a
field, these fail instead of silently following along. ``extra="forbid"``
catches undocumented fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Level = Literal["free", "moderate", "heavy", "severe"]
NetworkStatus = Literal["normal", "watch", "degraded"]
LEVEL_ORDER: dict[str, int] = {"free": 0, "moderate": 1, "heavy": 2, "severe": 3}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Segment(Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    free_flow_kmh: float = Field(gt=0)


class Status(Strict):
    as_of: datetime
    status: NetworkStatus
    segment_levels: dict[str, Level]


class ForecastPoint(Strict):
    ts: datetime
    speed_kmh: float = Field(gt=0)
    lower_kmh: float = Field(ge=0)
    upper_kmh: float = Field(gt=0)
    congestion_index: float = Field(ge=0, le=1)
    level: Level


class Forecast(Strict):
    segment_id: str
    method: str = Field(min_length=1)
    horizon_minutes: int = Field(gt=0)
    peak_index: float = Field(ge=0, le=1)
    peak_level: Level
    points: list[ForecastPoint] = Field(min_length=1)


class Reading(Strict):
    ts: datetime
    speed_kmh: float = Field(ge=0)


class History(Strict):
    segment_id: str
    days: float
    readings: list[Reading]


class Route(Strict):
    source: str
    target: str
    path: list[str] = Field(min_length=1)
    total_km: float = Field(ge=0)
    travel_minutes: float = Field(ge=0)
    bottleneck: str


class Answer(Strict):
    answer: str = Field(min_length=1)


class ErrorDetail(Strict):
    detail: str


class ValidationIssue(BaseModel):
    type: str
    loc: list[str | int]
    msg: str


class ValidationErrorBody(Strict):
    detail: list[ValidationIssue] = Field(min_length=1)


class StreamSegment(Strict):
    speed_kmh: float = Field(ge=0)
    congestion_index: float = Field(ge=0, le=1)
    level: Level


class StreamEvent(Strict):
    ts: datetime
    segments: dict[str, StreamSegment]
