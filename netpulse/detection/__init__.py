"""Deterministic anomaly detection. No LLM is involved anywhere in this package."""

from netpulse.detection.config import DEFAULT_POLICIES, DetectionConfig, MetricPolicy
from netpulse.detection.engine import consolidate, detect_series, to_evidence
from netpulse.detection.schemas import (
    ConsolidatedAnomaly,
    CoverageReport,
    DetectedInterval,
    DetectionInputError,
    DetectionRequest,
    SeriesDetectionReport,
    WindowComparison,
)

__all__ = [
    "DEFAULT_POLICIES",
    "ConsolidatedAnomaly",
    "CoverageReport",
    "DetectedInterval",
    "DetectionConfig",
    "DetectionInputError",
    "DetectionRequest",
    "MetricPolicy",
    "SeriesDetectionReport",
    "WindowComparison",
    "consolidate",
    "detect_series",
    "to_evidence",
]
