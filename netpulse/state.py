"""LangGraph state schema for the NetPulse investigation workflow.

State values are JSON-compatible dicts/lists (``Model.model_dump(mode="json")``)
so the SQLite checkpointer can persist them without custom serializers. The
Pydantic models in ``netpulse.models`` are the schema for those dicts.

Update rules (see docs/state_model.md for the per-field owner table):

* Fields without a reducer are *replaced* by whichever node returns them.
  Each has exactly one owning node, so the last write is the only write per
  step.
* ``evidence_references`` is merged by ``merge_evidence``: new IDs are added,
  re-adding an identical item is a no-op, and changing an existing item
  raises. Evidence is immutable once recorded.
* ``verification_results``, ``reviewer_decisions``, ``errors`` and
  ``node_trace`` are append-only audit logs (``append_list``).
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

JSONDict = dict[str, Any]


class EvidenceConflictError(ValueError):
    """Raised when a node tries to overwrite an existing evidence item."""


def merge_evidence(current: dict[str, JSONDict] | None, update: dict[str, JSONDict] | None) -> dict[str, JSONDict]:
    merged = dict(current or {})
    for evidence_id, item in (update or {}).items():
        existing = merged.get(evidence_id)
        if existing is not None and existing != item:
            raise EvidenceConflictError(f"evidence {evidence_id!r} is immutable and already recorded")
        merged[evidence_id] = item
    return merged


def append_list(current: list[Any] | None, update: list[Any] | None) -> list[Any]:
    return [*(current or []), *(update or [])]


class InvestigationState(TypedDict, total=False):
    # --- identity & request (owner: intake) -------------------------------
    incident_id: str
    request_metadata: JSONDict  # RequestMetadata
    submission: JSONDict  # IncidentSubmission (free text is untrusted)
    input_warnings: list[str]  # e.g. instruction-like text found in the submission (owner: intake)
    budget: JSONDict  # Budget
    deadline_at: str  # ISO timestamp derived from budget at intake

    # --- classification (owner: classify) ---------------------------------
    classification: JSONDict | None  # Classification

    # --- evidence gathering ----------------------------------------------
    telemetry_window: JSONDict | None  # TelemetryWindowRef (owner: retrieve_telemetry)
    detected_anomalies: list[JSONDict]  # Anomaly[] (owner: detect_anomalies)
    affected_nodes: list[str]  # (owner: analyze_topology)
    affected_services: list[str]  # (owner: analyze_topology)
    topology_evidence: JSONDict | None  # candidate roots, blast radius, evidence ids (owner: analyze_topology)
    historical_incidents: list[JSONDict]  # RetrievedDocumentRef[] (owner: retrieve_history)
    retrieved_runbooks: list[JSONDict]  # RetrievedDocumentRef[] (owner: retrieve_runbooks)
    evidence_references: Annotated[dict[str, JSONDict], merge_evidence]  # EvidenceItem by id (many writers, immutable)

    # --- investigation ----------------------------------------------------
    hypotheses: list[JSONDict]  # Hypothesis[] for the current attempt (owner: generate_hypotheses)
    verification_results: Annotated[list[JSONDict], append_list]  # VerificationResult[] (owner: verify_evidence)
    confidence_assessment: JSONDict | None  # ConfidenceAssessment (owner: rank_hypotheses)
    recommended_actions: list[JSONDict]  # ProposedAction[] (owner: recommend_actions)
    policy_decisions: list[JSONDict]  # PolicyDecision[] (owner: policy_review)

    # --- control flow -----------------------------------------------------
    retry_count: int  # hypothesis regeneration attempts (owner: generate_hypotheses)
    investigation_rounds: int  # evidence-gathering rounds (owner: retrieve_telemetry)
    verifier_feedback: list[str]  # blocking issues fed into the next attempt (owner: verify_evidence)
    approval_status: str  # ApprovalStatus (owner: policy_review, human_approval, escalate)
    reviewer_decisions: Annotated[list[JSONDict], append_list]  # ReviewerDecision[] (owner: human_approval)
    status: str  # WorkflowStatus (owner: whichever node changes the lifecycle stage)
    fatal_error: bool  # set by the node wrapper; routes to failure_report

    # --- audit ------------------------------------------------------------
    errors: Annotated[list[JSONDict], append_list]  # ErrorRecord[]
    node_trace: Annotated[list[JSONDict], append_list]  # NodeTrace[]

    # --- output (owner: compile_report / failure_report) -------------------
    final_report: JSONDict | None  # FinalReport
