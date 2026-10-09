"""Detector configuration.

These values are operational choices: alarm levels an operator would set,
and minimum effect sizes worth reporting. They are tuned by reasoning about
the metrics, not fitted to the evaluation labels. They are intentionally
independent of the ground-truth materiality rules used by the dataset
generator, which this package must never import.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netpulse.models import Metric


class MetricPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: str
    static_upper: float | None = None  # alarm when value > upper
    static_lower: float | None = None  # alarm when value < lower
    min_abs: float = Field(ge=0)  # smallest absolute deviation worth reporting
    min_rel: float = Field(default=0.0, ge=0)  # ... or relative to the baseline, whichever is larger
    physical_min: float | None = None
    physical_max: float | None = None


DEFAULT_POLICIES: dict[Metric, MetricPolicy] = {
    Metric.UTILIZATION_PCT: MetricPolicy(
        unit="%", static_upper=85.0, min_abs=10.0, physical_min=0.0, physical_max=100.0
    ),
    Metric.LATENCY_MS: MetricPolicy(unit="ms", min_abs=0.5, min_rel=0.3, physical_min=0.0),
    Metric.PACKET_LOSS_PCT: MetricPolicy(unit="%", static_upper=1.0, min_abs=0.3, physical_min=0.0, physical_max=100.0),
    Metric.ERROR_RATE: MetricPolicy(unit="errors/s", static_upper=10.0, min_abs=3.0, physical_min=0.0),
    Metric.CPU_PCT: MetricPolicy(unit="%", static_upper=90.0, min_abs=10.0, physical_min=0.0, physical_max=100.0),
    Metric.MEMORY_PCT: MetricPolicy(unit="%", static_upper=90.0, min_abs=8.0, physical_min=0.0, physical_max=100.0),
    Metric.SERVICE_LATENCY_MS: MetricPolicy(unit="ms", min_abs=3.0, min_rel=0.15, physical_min=0.0),
    Metric.SERVICE_SUCCESS_PCT: MetricPolicy(
        unit="%", static_lower=99.0, min_abs=0.5, physical_min=0.0, physical_max=100.0
    ),
}


class DetectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_interval_minutes: float = Field(default=5.0, gt=0)
    reference_hours: float = Field(default=24.0, gt=0)  # history used by robust z-score
    min_reference_coverage: float = Field(default=0.5, gt=0, le=1)  # below this, statistical methods are skipped
    rolling_window_minutes: float = Field(default=120.0, gt=0)
    rolling_lag_minutes: float = Field(default=60.0, ge=0)  # keeps a ramping fault out of its own baseline
    rolling_k: float = Field(default=4.0, gt=0)
    zscore_threshold: float = Field(default=3.5, gt=0)
    comparison_lag_hours: float = Field(default=24.0, gt=0)  # same window yesterday
    comparison_k: float = Field(default=4.0, gt=0)
    min_points: int = Field(default=2, ge=1)  # flagged points needed to form an interval
    max_gap_minutes: float = Field(default=10.0, ge=0)  # flagged points this close merge into one interval
    min_methods: int = Field(default=2, ge=1)  # independent methods needed to confirm an anomaly
    policies: dict[Metric, MetricPolicy] = Field(default_factory=lambda: dict(DEFAULT_POLICIES))

    @model_validator(mode="after")
    def _all_metrics_have_policies(self) -> DetectionConfig:
        missing = set(Metric) - set(self.policies)
        if missing:
            raise ValueError(f"missing metric policies: {sorted(missing)}")
        return self
