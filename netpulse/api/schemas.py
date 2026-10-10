"""Request/response bodies for the API.

Deliberately thin: most of the real schema lives in ``netpulse.models``.
These wrapper types exist only where the API shape differs from the
internal one — chiefly, ``ApprovalRequest`` omits ``reviewer_id`` and
``reviewer_role``, because those must come from the authenticated
``Principal``, never from a client-supplied body.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from netpulse.models import IncidentSubmission, ReviewerChoice


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentCreateRequest(_Model):
    submission: IncidentSubmission
    incident_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


class JobAccepted(_Model):
    incident_id: str
    job_state: str


class ApprovalRequest(_Model):
    choice: ReviewerChoice
    comment: str = Field(default="", max_length=2000)
    approved_action_ids: list[str] | None = None


class IncidentStatusResponse(_Model):
    incident_id: str
    job_state: str | None  # background-job state while in flight; None once a checkpoint exists
    status: str | None  # WorkflowStatus, once a checkpoint exists
    approval_status: str | None
    pending_review: dict[str, Any] | None
    next_nodes: list[str]
    durable: bool
    error: str | None = None


class ResultsResponse(_Model):
    incident_id: str
    classification: dict[str, Any] | None
    detected_anomalies: list[dict[str, Any]]
    affected_nodes: list[str]
    affected_services: list[str]
    hypotheses: list[dict[str, Any]]
    confidence_assessment: dict[str, Any] | None
    recommended_actions: list[dict[str, Any]]
    policy_decisions: list[dict[str, Any]]
    verification_results: list[dict[str, Any]]
    node_trace: list[dict[str, Any]]
    final_report: dict[str, Any] | None


class EvidenceResponse(_Model):
    incident_id: str
    telemetry_window: dict[str, Any] | None
    topology_evidence: dict[str, Any] | None
    evidence: list[dict[str, Any]]


class HealthResponse(_Model):
    status: str
    durable: bool
    data_notice: str
