"""The validated final investigation report."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from opspilot.contracts.findings import Hypothesis, ObservedAnomaly
from opspilot.contracts.verification import VerificationResult


class InvestigationStatus(StrEnum):
    INVESTIGATED = "investigated"
    INCONCLUSIVE = "inconclusive"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
    FAILED = "failed"


class EvidenceRef(BaseModel):
    evidence_id: str
    kind: str
    summary: str
    source: str
    trusted: bool = True


class DocRef(BaseModel):
    doc_id: str
    title: str
    note: str = ""


class ServiceRisk(BaseModel):
    service: str
    tier: int | None
    impact: Literal["critical", "high", "medium", "low", "unknown"]


class RiskAssessment(BaseModel):
    max_impact: Literal["critical", "high", "medium", "low", "none", "unknown"]
    services: list[ServiceRisk]
    narrative: str = Field(max_length=1000)


class Escalation(BaseModel):
    level: Literal["none", "monitor", "escalate"]
    targets: list[Literal["network_oncall", "security_team"]]
    rationale: str


class DiagnosticStep(BaseModel):
    order: int
    step: str
    rationale: str
    runbook_ids: list[str]
    evidence_ids: list[str]


class RunMetrics(BaseModel):
    provider: str
    model: str
    llm_calls: int
    tool_calls: int
    duration_s: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tool_calls_by_agent: dict[str, list[str]] = Field(default_factory=dict)
    llm_calls_by_agent: dict[str, int] = Field(default_factory=dict)
    budget_events: list[str] = Field(default_factory=list)
    model_time_ms: dict[str, float] = Field(default_factory=dict)
    tool_time_ms: dict[str, float] = Field(default_factory=dict)
    investigation_rounds: int = 1
    retried_stages: list[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    incident_id: str
    dataset_id: str
    generated_at: str
    status: InvestigationStatus
    investigation_summary: str = Field(max_length=2500)
    facts: list[str] = Field(description="Statements backed by cited evidence")
    affected_services: list[str]
    affected_network_components: list[str]
    observed_anomalies: list[ObservedAnomaly]
    root_cause_hypotheses: list[Hypothesis] = Field(description="Ranked; hypotheses, not facts")
    supporting_evidence: list[EvidenceRef]
    contradicting_evidence: list[EvidenceRef]
    historical_incident_references: list[DocRef]
    relevant_runbooks: list[DocRef]
    missing_information: list[str]
    recommended_diagnostic_steps: list[DiagnosticStep]
    removed_recommendations: list[str] = Field(
        description="Draft steps removed by the read-only policy"
    )
    risk_and_impact_assessment: RiskAssessment
    confidence_rationale: str
    escalation: Escalation
    verification: VerificationResult | None
    review_concerns: list[str]
    security_notes: list[str]
    specialist_status: dict[str, str]
    unresolved_questions: list[str]
    run_metrics: RunMetrics | None = None
