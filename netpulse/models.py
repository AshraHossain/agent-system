"""Typed domain models for NetPulse AI.

These Pydantic models are the contracts between graph nodes, tools, the API,
and the evaluation harness. Graph state stores their ``model_dump(mode="json")``
form (plain JSON-compatible dicts) so checkpoints serialize without custom
types; nodes re-validate on read through ``netpulse.state`` helpers.

Design rules encoded here:

* Observed facts live only in ``EvidenceItem`` records produced by
  deterministic tools. Hypotheses are inferences and may only *cite* evidence
  by ID; they cannot carry their own measurements.
* Confidence is ordinal (``ConfidenceLevel``), assigned by deterministic
  ranking rules, never a free-form probability emitted by an LLM.
* Every proposed action is a proposal: ``executed`` is fixed to ``False``.
* Every evidence item carries ``provenance``; today it can only be
  ``synthetic`` so nothing can be presented as real network observation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVIDENCE_ID_PATTERN = r"^ev-[a-z]+-\d{4}$"
ENTITY_ID_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentCategory(StrEnum):
    CONGESTION = "congestion"
    PACKET_LOSS = "packet_loss"
    LATENCY = "latency"
    LINK_DEGRADATION = "link_degradation"
    DEVICE_RESOURCE = "device_resource"
    TRAFFIC_SPIKE = "traffic_spike"
    TELEMETRY_GAP = "telemetry_gap"
    UNKNOWN = "unknown"


class RootCauseCategory(StrEnum):
    """Closed taxonomy so top-k accuracy can be scored without free-text matching."""

    LINK_DEGRADATION = "link_degradation"
    LINK_CONGESTION = "link_congestion"
    TRAFFIC_SURGE = "traffic_surge"
    DEVICE_CPU_SATURATION = "device_cpu_saturation"
    DEVICE_MEMORY_EXHAUSTION = "device_memory_exhaustion"
    CONFIGURATION_CHANGE = "configuration_change"
    MAINTENANCE_SIDE_EFFECT = "maintenance_side_effect"
    UPSTREAM_DEPENDENCY = "upstream_dependency"
    TELEMETRY_FAULT = "telemetry_fault"
    UNKNOWN = "unknown"


class Metric(StrEnum):
    UTILIZATION_PCT = "utilization_pct"
    PACKET_LOSS_PCT = "packet_loss_pct"
    LATENCY_MS = "latency_ms"
    ERROR_RATE = "error_rate"
    CPU_PCT = "cpu_pct"
    MEMORY_PCT = "memory_pct"
    SERVICE_LATENCY_MS = "service_latency_ms"
    SERVICE_SUCCESS_PCT = "service_success_pct"


class EvidenceSource(StrEnum):
    TELEMETRY = "telemetry"
    DETECTOR = "detector"
    TOPOLOGY = "topology"
    EVENT_LOG = "event_log"
    MAINTENANCE = "maintenance"
    HISTORICAL_INCIDENT = "historical_incident"
    RUNBOOK = "runbook"
    DATA_QUALITY = "data_quality"
    REVIEWER = "reviewer"


class Provenance(StrEnum):
    # Only one value on purpose: the platform has no path to real telemetry.
    SYNTHETIC = "synthetic"


class DetectorMethod(StrEnum):
    STATIC_THRESHOLD = "static_threshold"
    ROLLING_BASELINE = "rolling_baseline"
    ROBUST_ZSCORE = "robust_zscore"
    WINDOW_COMPARISON = "window_comparison"
    PLAUSIBILITY = "plausibility"  # physically impossible readings (e.g. utilization > 100%)


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient_evidence"


class VerificationCode(StrEnum):
    UNKNOWN_EVIDENCE_REF = "unknown_evidence_ref"
    NO_SUPPORTING_EVIDENCE = "no_supporting_evidence"
    UNKNOWN_ENTITY = "unknown_entity"
    ENTITY_NOT_IN_EVIDENCE = "entity_not_in_evidence"
    CONTRADICTED_BY_TELEMETRY = "contradicted_by_telemetry"
    UNTRUSTED_ONLY_SUPPORT = "untrusted_only_support"
    SCHEMA_ERROR = "schema_error"
    DUPLICATE_HYPOTHESIS = "duplicate_hypothesis"


class ActionKind(StrEnum):
    DIAGNOSTIC = "diagnostic"  # read-only, e.g. "inspect interface counters"
    REMEDIATION = "remediation"  # consequential; simulated only, needs approval


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"
    ESCALATE = "escalate"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MORE_INVESTIGATION_REQUESTED = "more_investigation_requested"
    ESCALATED = "escalated"


class ReviewerChoice(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_MORE_INVESTIGATION = "request_more_investigation"


class ErrorKind(StrEnum):
    VALIDATION = "validation"
    TOOL_FAILURE = "tool_failure"
    TIMEOUT = "timeout"
    LLM_FAILURE = "llm_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INTERNAL = "internal"


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"
    ESCALATED = "escalated"
    FAILED = "failed"


class ReportOutcome(StrEnum):
    ROOT_CAUSE_IDENTIFIED = "root_cause_identified"
    INCONCLUSIVE = "inconclusive"
    ESCALATED = "escalated"
    REJECTED_BY_REVIEWER = "rejected_by_reviewer"
    FAILED = "failed"


SYNTHETIC_DATA_NOTICE = (
    "SIMULATED DATA: all telemetry, topology, incidents and runbooks in this "
    "report come from a synthetic dataset. No real network was observed or modified."
)


# --------------------------------------------------------------------------
# Intake
# --------------------------------------------------------------------------


class IncidentSubmission(_Model):
    """What an operator (or the eval harness) submits. Free text is untrusted."""

    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4000)
    dataset_id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    window_start: datetime
    window_end: datetime
    suspected_entities: list[str] = Field(default_factory=list, max_length=50)
    severity_hint: Severity | None = None

    @model_validator(mode="after")
    def _window_ordered(self) -> IncidentSubmission:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class RequestMetadata(_Model):
    execution_id: str
    submitted_by: str
    submitted_at: datetime
    source: Literal["api", "ui", "eval", "test"]
    llm_provider: str
    llm_model: str | None = None


class Budget(_Model):
    max_hypothesis_retries: int = Field(default=2, ge=0, le=5)
    max_investigation_rounds: int = Field(default=2, ge=1, le=5)
    max_graph_steps: int = Field(default=60, ge=10, le=500)
    wall_clock_seconds: float = Field(default=300.0, gt=0, le=3600)
    tool_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    llm_timeout_seconds: float = Field(default=120.0, gt=0, le=900)


class Classification(_Model):
    category: IncidentCategory
    severity: Severity
    rationale: str
    method: Literal["rules"] = "rules"


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class TelemetryWindowRef(_Model):
    """A pointer to telemetry, not the telemetry itself (rows stay in the data store)."""

    dataset_id: str
    start: datetime
    end: datetime
    entity_ids: list[str]
    metrics: list[Metric]
    row_count: int = Field(ge=0)
    expected_row_count: int = Field(ge=0)
    entities_without_data: list[str] = Field(default_factory=list)
    data_quality_evidence_ids: list[str] = Field(default_factory=list)


class EvidenceItem(_Model):
    """One citable fact. Produced only by deterministic tools or the reviewer."""

    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    source: EvidenceSource
    provenance: Provenance = Provenance.SYNTHETIC
    summary: str = Field(max_length=600)
    entity_ids: list[str] = Field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None
    metric: Metric | None = None
    observed_value: float | None = None
    baseline_value: float | None = None
    threshold: float | None = None
    unit: str | None = None
    method: str | None = None
    source_ref: str | None = Field(default=None, max_length=200)
    # Retrieved prose (runbooks, past incident write-ups) is untrusted: it can
    # inform, but may not on its own establish a cause or authorize an action.
    trusted: bool = True


class Anomaly(_Model):
    """A consolidated detector finding, as stored in graph state."""

    anomaly_id: str
    entity_id: str
    metric: Metric
    detectors: list[DetectorMethod] = Field(min_length=1)
    confirmed: bool  # corroborated by enough independent methods (or physically implausible)
    implausible: bool = False
    direction: Literal["high", "low"]
    start: datetime
    end: datetime
    peak_value: float
    peak_at: datetime
    baseline_value: float | None = None
    threshold: float | None = None
    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)


class RetrievedDocumentRef(_Model):
    """Summary of a retrieved runbook or past incident; full text stays in the corpus."""

    doc_id: str
    title: str
    kind: Literal["runbook", "historical_incident"]
    score: float
    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    section: str | None = None


# --------------------------------------------------------------------------
# Investigation
# --------------------------------------------------------------------------


class Hypothesis(_Model):
    """An inferred cause. It cites evidence; it never contains its own measurements."""

    hypothesis_id: str = Field(pattern=r"^hyp-\d{2}$")
    description: str = Field(min_length=10, max_length=800)
    cause_category: RootCauseCategory
    suspected_root_entity: str | None = None
    affected_components: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    validation_steps: list[str] = Field(default_factory=list)
    confidence_rationale: str = Field(min_length=1, max_length=800)
    generated_by: Literal["llm", "heuristic"]


class VerificationIssue(_Model):
    code: VerificationCode
    detail: str
    blocking: bool
    hypothesis_id: str | None = None
    evidence_id: str | None = None


class VerificationResult(_Model):
    attempt: int = Field(ge=0)
    passed: bool
    issues: list[VerificationIssue] = Field(default_factory=list)
    accepted_hypothesis_ids: list[str] = Field(default_factory=list)
    rejected_hypothesis_ids: list[str] = Field(default_factory=list)


class RankedHypothesis(_Model):
    hypothesis_id: str
    rank: int = Field(ge=1)
    confidence: ConfidenceLevel
    trusted_support_count: int = Field(ge=0)
    contradiction_count: int = Field(ge=0)
    rationale: str


class ConfidenceAssessment(_Model):
    overall: ConfidenceLevel
    evidence_sufficient: bool
    conclusive: bool
    ranking: list[RankedHypothesis] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    rationale: str


# --------------------------------------------------------------------------
# Actions, policy, approval
# --------------------------------------------------------------------------


class ProposedAction(_Model):
    action_id: str = Field(pattern=r"^act-\d{2}$")
    catalog_id: str  # must exist in the static action catalog; anything else is dropped
    kind: ActionKind
    description: str
    target_entities: list[str] = Field(default_factory=list)
    related_hypothesis_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    reversible: bool
    blast_radius_entities: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    executed: Literal[False] = False


class PolicyDecision(_Model):
    action_id: str | None  # None => incident-level decision
    outcome: PolicyOutcome
    reasons: list[str]
    required_role: Literal["none", "operator", "senior_operator"] = "none"


class ReviewerDecision(_Model):
    reviewer_id: str
    reviewer_role: Literal["operator", "senior_operator"]
    choice: ReviewerChoice
    comment: str = Field(default="", max_length=2000)
    approved_action_ids: list[str] = Field(default_factory=list)
    decided_at: datetime


# --------------------------------------------------------------------------
# Observability and output
# --------------------------------------------------------------------------


class ErrorRecord(_Model):
    node: str
    kind: ErrorKind
    message: str = Field(max_length=1000)
    recoverable: bool
    at: datetime


class NodeTrace(_Model):
    node: str
    started_at: datetime
    duration_ms: float = Field(ge=0)
    outcome: Literal["ok", "degraded", "error", "interrupted"]
    detail: str = ""


class FinalReport(_Model):
    incident_id: str
    outcome: ReportOutcome
    summary: str
    top_hypotheses: list[RankedHypothesis] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    recommended_actions: list[ProposedAction] = Field(default_factory=list)
    approval_status: ApprovalStatus
    missing_evidence: list[str] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    data_notice: str = SYNTHETIC_DATA_NOTICE
    generated_at: datetime
