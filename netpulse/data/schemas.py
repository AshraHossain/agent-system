"""Schemas for the data-access tools."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netpulse.models import Metric

DATASET_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
TableName = Literal["links", "nodes", "services"]

TABLE_METRICS: dict[str, tuple[Metric, ...]] = {
    "links": (Metric.UTILIZATION_PCT, Metric.LATENCY_MS, Metric.PACKET_LOSS_PCT, Metric.ERROR_RATE),
    "nodes": (Metric.CPU_PCT, Metric.MEMORY_PCT),
    "services": (Metric.SERVICE_LATENCY_MS, Metric.SERVICE_SUCCESS_PCT),
}
METRIC_TABLE: dict[Metric, str] = {m: table for table, metrics in TABLE_METRICS.items() for m in metrics}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_tz(*values: datetime) -> None:
    if any(v.tzinfo is None for v in values):
        raise ValueError("timestamps must be timezone-aware")


class TelemetryQuery(_Model):
    """Input of the telemetry-window tool."""

    dataset_id: str = Field(pattern=DATASET_ID_PATTERN)
    window_start: datetime
    window_end: datetime
    as_of: datetime  # rows not ingested by this time are invisible
    history_hours: float = Field(default=26.0, ge=0, le=72)  # context before the window for baselines
    entity_ids: list[str] | None = Field(default=None, max_length=200)
    metrics: list[Metric] | None = None

    @model_validator(mode="after")
    def _check(self) -> TelemetryQuery:
        _require_tz(self.window_start, self.window_end, self.as_of)
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.as_of < self.window_start:
            raise ValueError("as_of must not precede the window")
        return self


class TimeSpan(_Model):
    start: datetime
    end: datetime


class EntityCoverage(_Model):
    entity_id: str
    table: TableName
    expected_points: int = Field(ge=0)
    visible_points: int = Field(ge=0)
    pending_points: int = Field(ge=0)  # present in the source but not yet ingested at as_of
    coverage_ratio: float = Field(ge=0, le=1)
    gaps: list[TimeSpan] = Field(default_factory=list)


class EventRecord(_Model):
    timestamp: datetime
    entity_id: str
    event_type: str
    severity: str
    message: str = Field(max_length=500)


class MaintenanceWindow(_Model):
    entity_id: str
    start: datetime
    end: datetime
    ticket: str
    description: str = Field(max_length=500)
