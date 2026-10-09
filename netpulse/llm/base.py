"""Hypothesis-generator contract.

A generator receives a read-only ``GenerationContext`` built from graph
state (evidence summaries by ID, consolidated anomalies, topology
localization, coverage gaps, verifier feedback) and returns structured
``Hypothesis`` objects. Generators never receive raw telemetry and never
compute numbers. They can only cite evidence IDs that already exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from netpulse.models import Anomaly, EvidenceSource, Hypothesis, IncidentCategory, Metric
from netpulse.topology.analysis import LocalizationResult


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceView(_Model):
    evidence_id: str
    source: EvidenceSource
    trusted: bool
    summary: str
    entity_ids: list[str]
    metric: Metric | None = None


class GenerationContext(_Model):
    incident_id: str
    category: IncidentCategory
    window_start: datetime
    window_end: datetime
    evidence: list[EvidenceView]
    anomalies: list[Anomaly]
    localization: LocalizationResult | None
    coverage_gaps: list[str] = Field(default_factory=list)  # entities with < 50% visible telemetry in window
    verifier_feedback: list[str] = Field(default_factory=list)
    attempt: int = 0


class GenerationResult(_Model):
    hypotheses: list[Hypothesis]
    provider: str
    model: str | None = None
    raw_output: str | None = None  # truncated, for audit; never contains secrets


class GenerationError(RuntimeError):
    """The generator could not produce a parseable result (counts as a failed attempt)."""


class HypothesisGenerator(Protocol):
    provider: str
    model: str | None

    def generate(self, context: GenerationContext) -> GenerationResult: ...
