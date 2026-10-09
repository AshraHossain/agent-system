"""Telemetry tool contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from opspilot.contracts.evidence import Evidence

Verdict = Literal["normal", "elevated", "critical", "recovered", "insufficient_data"]


class MetricStats(BaseModel):
    count: int
    expected_count: int
    missing_count: int
    mean: float | None = None
    p95: float | None = None
    min: float | None = None
    max: float | None = None
    last_ts: str | None = None


class BaselineComparison(BaseModel):
    entity_id: str
    metric: str
    baseline: MetricStats
    window: MetricStats
    delta_pct: float | None
    zscore: float | None
    verdict: Verdict
    rule: str = Field(description="The explicit threshold rule that produced the verdict")
    evidence: list[Evidence] = Field(default_factory=list)


class Anomaly(BaseModel):
    entity_id: str
    entity_type: str
    metric: str
    verdict: Literal["elevated", "critical", "recovered"]
    baseline_mean: float
    window_mean: float
    peak: float
    delta_pct: float | None
    zscore: float | None
    first_seen: str | None
    last_seen: str | None = None
    rule: str
    evidence_id: str


class DataQualityIssue(BaseModel):
    entity_id: str
    metric: str | None
    issue: Literal["missing", "delayed", "unknown_entity"]
    detail: str
    evidence_id: str


class AnomalySummary(BaseModel):
    window: str
    entities_checked: int
    anomalies: list[Anomaly]
    normal_metrics_checked: int
    data_quality: list[DataQualityIssue]
    evidence: list[Evidence] = Field(default_factory=list)


class TelemetrySnapshot(BaseModel):
    entity_id: str
    window: str
    metrics: dict[str, MetricStats]
    evidence: list[Evidence] = Field(default_factory=list)
