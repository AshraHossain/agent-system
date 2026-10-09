"""Structured outputs of the LLM agents (validated by ADK via `output_schema`).

Kept to lists/objects/enums (no free-form dicts) so the schemas are accepted as
Gemini response schemas.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

StageStatus = Literal["completed", "partial", "failed"]
Confidence = Literal["strong", "moderate", "weak"]


class HypothesisCategory(StrEnum):
    LINK_CONGESTION = "link_congestion"
    LINK_PHYSICAL = "link_physical_degradation"
    DEVICE_SATURATION = "device_resource_saturation"
    DNS = "dns_degradation"
    WAN = "wan_degradation"
    TELEMETRY_ARTIFACT = "telemetry_artifact"
    APPLICATION = "application_side"
    UNKNOWN = "unknown"


class ObservedAnomaly(BaseModel):
    entity_id: str
    metric: str
    description: str = Field(max_length=300)
    evidence_ids: list[str]


class TelemetryFinding(BaseModel):
    status: StageStatus
    summary: str = Field(max_length=1200)
    time_window: str
    anomalies: list[ObservedAnomaly]
    affected_entities: list[str]
    affected_services: list[str]
    data_gaps: list[str] = Field(description="Missing or delayed measurements")
    evidence_ids: list[str]
    errors: list[str] = Field(default_factory=list)


class SharedDependency(BaseModel):
    component_id: str
    services: list[str]
    evidence_ids: list[str]


class ImpactEntry(BaseModel):
    service: str
    impact: Literal["critical", "high", "medium", "low"]
    exposure: Literal["single_point", "redundant", "indirect"]


class BlastRadiusEntry(BaseModel):
    component_id: str
    impacts: list[ImpactEntry]
    evidence_ids: list[str]


class TopologyFinding(BaseModel):
    status: StageStatus
    summary: str = Field(max_length=1200)
    services_examined: list[str]
    shared_dependencies: list[SharedDependency]
    blast_radius: list[BlastRadiusEntry]
    propagation_paths: list[str]
    uncertainties: list[str]
    evidence_ids: list[str]
    errors: list[str] = Field(default_factory=list)


class DocReference(BaseModel):
    doc_id: str
    title: str
    note: str = Field(max_length=300)
    evidence_ids: list[str]


class DocIssue(BaseModel):
    doc_id: str
    issue: str = Field(max_length=300)


class KnowledgeFinding(BaseModel):
    status: StageStatus
    summary: str = Field(max_length=1200)
    relevant_runbooks: list[DocReference]
    related_incidents: list[DocReference]
    technical_references: list[DocReference]
    outdated_or_conflicting: list[DocIssue]
    suspicious_documents: list[DocIssue]
    evidence_ids: list[str]
    errors: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    hypothesis_id: str
    category: HypothesisCategory
    component_id: str
    statement: str = Field(max_length=500)
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    historical_references: list[str] = Field(
        description="Incident IDs that look similar. Similarity is NOT proof of causation."
    )
    explained_services: list[str]
    confidence: Confidence
    confidence_rationale: str = Field(max_length=500)
    alternative_explanations: list[str]
    diagnostic_checks: list[str]


class IncidentAnalysis(BaseModel):
    status: StageStatus
    hypotheses: list[Hypothesis] = Field(description="Ranked, most likely first")
    unexplained_observations: list[str]
    errors: list[str] = Field(default_factory=list)


class KeyFact(BaseModel):
    statement: str = Field(max_length=400)
    evidence_ids: list[str]


class RecommendedStep(BaseModel):
    step: str = Field(max_length=300)
    rationale: str = Field(max_length=300)
    runbook_ids: list[str]
    evidence_ids: list[str]


class ReportDraft(BaseModel):
    summary: str = Field(max_length=2000)
    key_facts: list[KeyFact]
    affected_services: list[str]
    affected_components: list[str]
    recommended_steps: list[RecommendedStep]
    risk_and_impact: str = Field(max_length=1000)
    open_questions: list[str]


class ReviewConcern(BaseModel):
    severity: Literal["info", "warning"]
    description: str = Field(max_length=400)
    related_ids: list[str]


class ReviewResult(BaseModel):
    overall: Literal["no_concerns", "concerns"]
    concerns: list[ReviewConcern]
