"""Input and output schemas for the detection tools."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netpulse.models import DetectorMethod, Metric


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DetectionInputError(ValueError):
    """Raised for malformed detector input. Callers get a predictable, typed failure."""


class DetectionRequest(_Model):
    entity_id: str = Field(min_length=1, max_length=64)
    metric: Metric
    window_start: datetime
    window_end: datetime

    @model_validator(mode="after")
    def _check_window(self) -> DetectionRequest:
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ValueError("window bounds must be timezone-aware")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class TimeRange(_Model):
    start: datetime
    end: datetime


class CoverageReport(_Model):
    """How much of a period actually has data. Missing points are reported, never imputed."""

    start: datetime
    end: datetime
    expected_points: int = Field(ge=0)
    present_points: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    gaps: list[TimeRange] = Field(default_factory=list)
    longest_gap_minutes: float = Field(default=0.0, ge=0)


class DetectedInterval(_Model):
    """A run of flagged points from a single method."""

    method: DetectorMethod
    start: datetime
    end: datetime
    n_points: int = Field(ge=1)
    direction: Literal["high", "low"]
    peak_value: float
    peak_at: datetime
    baseline_value: float | None = None
    threshold: float | None = None  # absolute level, or the deviation that was required
    score: float | None = None  # method-specific: z-score, or deviation / required deviation
    implausible: bool = False


class WindowComparison(_Model):
    """Window vs. the same-length window one comparison lag earlier."""

    current_median: float
    reference_median: float
    delta: float
    ratio: float | None
    current_points: int
    reference_points: int
    required_delta: float
    significant: bool


class SkippedMethod(_Model):
    method: DetectorMethod
    reason: str


class DetectorFailure(_Model):
    method: DetectorMethod
    error_type: str
    message: str = Field(max_length=500)


class ConsolidatedAnomaly(_Model):
    """Overlapping intervals from different methods on one series, merged."""

    entity_id: str
    metric: Metric
    start: datetime
    end: datetime
    direction: Literal["high", "low"]
    peak_value: float
    peak_at: datetime
    baseline_value: float | None
    threshold: float | None
    methods: list[DetectorMethod]
    confirmed: bool
    implausible: bool
    details: list[DetectedInterval]


class SeriesDetectionReport(_Model):
    request: DetectionRequest
    window_coverage: CoverageReport
    reference_coverage: CoverageReport
    intervals: list[DetectedInterval] = Field(default_factory=list)
    anomalies: list[ConsolidatedAnomaly] = Field(default_factory=list)
    comparison: WindowComparison | None = None
    skipped: list[SkippedMethod] = Field(default_factory=list)
    failures: list[DetectorFailure] = Field(default_factory=list)

    @property
    def confirmed(self) -> list[ConsolidatedAnomaly]:
        return [a for a in self.anomalies if a.confirmed]
