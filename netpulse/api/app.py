"""FastAPI service: a thin, authenticated layer over ``InvestigationService``.

Endpoints:

| Method & path | Role | Purpose |
|---|---|---|
| `GET /health` | none | liveness and whether checkpoints are durable |
| `POST /incidents` | operator | submit an incident (runs in the background) |
| `GET /incidents/{id}` | viewer | status: job/workflow state, pending review, durability |
| `GET /incidents/{id}/results` | viewer | hypotheses, ranking, recommended actions, final report |
| `GET /incidents/{id}/evidence` | viewer | the evidence registry and telemetry/topology references |
| `POST /incidents/{id}/approval` | operator | approve / reject / request more investigation |
| `POST /incidents/{id}/resume` | operator | continue a run that stalled outside an approval pause |
| `GET /eval/reports` | viewer | list evaluation reports (Phase 10; empty until then) |

Every mutating endpoint (`POST`) enqueues work on a background job registry
and returns 202 immediately; the caller polls `GET /incidents/{id}`. A
request's claimed reviewer identity and role are **never** read from the
body — they come only from the authenticated bearer token (`auth.py`).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from netpulse.api.auth import API_ROLE_RANK, Principal, TokenStore, principal_dependency, require_role
from netpulse.api.registry import JobInFlightError, JobRegistry
from netpulse.api.schemas import (
    ApprovalRequest,
    EvidenceResponse,
    HealthResponse,
    IncidentCreateRequest,
    IncidentStatusResponse,
    JobAccepted,
    ResultsResponse,
)
from netpulse.errors import DataCorruptError, DataNotFoundError, ToolInputError
from netpulse.models import SYNTHETIC_DATA_NOTICE, ReviewInput
from netpulse.service import AuthorizationError, InvestigationService

EVAL_REPORTS_DIR = Path(__file__).resolve().parents[2] / "eval" / "reports"


def create_app(service: InvestigationService, tokens: TokenStore, max_workers: int = 4) -> FastAPI:
    app = FastAPI(
        title="NetPulse AI API",
        description="Evidence-backed, read-only network anomaly investigation. " + SYNTHETIC_DATA_NOTICE,
        version="0.1.0",
    )
    registry = JobRegistry(max_workers=max_workers)
    app.state.service = service
    app.state.registry = registry

    get_principal = principal_dependency(tokens)
    viewer = require_role("viewer")
    operator = require_role("operator")

    def principal_viewer(p: Principal = Depends(get_principal)) -> Principal:
        return viewer(p)

    def principal_operator(p: Principal = Depends(get_principal)) -> Principal:
        return operator(p)

    for exc_type, status_code in [
        (ToolInputError, 400),
        (DataNotFoundError, 404),
        (DataCorruptError, 500),
        (AuthorizationError, 403),
        (JobInFlightError, 409),
    ]:

        def _handler(_request, exc, _code=status_code):
            return JSONResponse(status_code=_code, content={"detail": str(exc)})

        app.add_exception_handler(exc_type, _handler)

    # ---------------------------------------------------------------- health

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", durable=service.durable, data_notice=SYNTHETIC_DATA_NOTICE)

    # ------------------------------------------------------------- incidents

    @app.post("/incidents", response_model=JobAccepted, status_code=202)
    def create_incident(body: IncidentCreateRequest, principal: Principal = Depends(principal_operator)):
        incident_id = body.incident_id or f"inc-{uuid.uuid4().hex[:12]}"
        submission = body.submission.model_dump(mode="json")
        submitted_at = service.deps.clock().isoformat()

        def job() -> None:
            service.start(submission, submitted_at=submitted_at, incident_id=incident_id, submitted_by=principal.name)

        try:
            registry.submit(incident_id, job)
        except JobInFlightError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobAccepted(incident_id=incident_id, job_state=registry.state(incident_id) or "queued")

    @app.get("/incidents/{incident_id}", response_model=IncidentStatusResponse)
    def incident_status(incident_id: str, _: Principal = Depends(principal_viewer)) -> IncidentStatusResponse:
        job_state = registry.state(incident_id)
        try:
            status = service.status(incident_id)
        except DataNotFoundError:
            if job_state is None:
                raise
            return IncidentStatusResponse(
                incident_id=incident_id,
                job_state=job_state,
                status=None,
                approval_status=None,
                pending_review=None,
                next_nodes=[],
                durable=service.durable,
                error=registry.error(incident_id),
            )
        return IncidentStatusResponse(
            incident_id=incident_id,
            job_state=job_state,
            status=status.status,
            approval_status=status.approval_status,
            pending_review=status.pending_review,
            next_nodes=status.next_nodes,
            durable=status.durable,
            error=registry.error(incident_id),
        )

    @app.get("/incidents/{incident_id}/results", response_model=ResultsResponse)
    def incident_results(incident_id: str, _: Principal = Depends(principal_viewer)) -> ResultsResponse:
        state = service.state(incident_id)
        return ResultsResponse(
            incident_id=incident_id,
            classification=state.get("classification"),
            detected_anomalies=state.get("detected_anomalies") or [],
            affected_nodes=state.get("affected_nodes") or [],
            affected_services=state.get("affected_services") or [],
            hypotheses=state.get("hypotheses") or [],
            confidence_assessment=state.get("confidence_assessment"),
            recommended_actions=state.get("recommended_actions") or [],
            policy_decisions=state.get("policy_decisions") or [],
            verification_results=state.get("verification_results") or [],
            node_trace=state.get("node_trace") or [],
            final_report=state.get("final_report"),
        )

    @app.get("/incidents/{incident_id}/evidence", response_model=EvidenceResponse)
    def incident_evidence(incident_id: str, _: Principal = Depends(principal_viewer)) -> EvidenceResponse:
        state = service.state(incident_id)
        return EvidenceResponse(
            incident_id=incident_id,
            telemetry_window=state.get("telemetry_window"),
            topology_evidence=state.get("topology_evidence"),
            evidence=sorted((state.get("evidence_references") or {}).values(), key=lambda e: e["evidence_id"]),
        )

    @app.post("/incidents/{incident_id}/approval", response_model=JobAccepted, status_code=202)
    def submit_approval(incident_id: str, body: ApprovalRequest, principal: Principal = Depends(principal_operator)):
        status = service.status(incident_id)  # 404 if unknown
        if status.pending_review is None:
            raise HTTPException(status_code=409, detail="incident is not awaiting approval")
        required_role = status.pending_review["required_role"]
        if API_ROLE_RANK[principal.role] < API_ROLE_RANK[required_role]:
            raise HTTPException(status_code=403, detail=f"{required_role} role or higher required for this decision")
        review = ReviewInput(
            reviewer_id=principal.name,
            reviewer_role=principal.role,
            choice=body.choice,  # type: ignore[arg-type]
            comment=body.comment,
            approved_action_ids=body.approved_action_ids,
        )

        def job() -> None:
            service.decide(incident_id, review)

        try:
            registry.submit(incident_id, job)
        except JobInFlightError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobAccepted(incident_id=incident_id, job_state=registry.state(incident_id) or "queued")

    @app.post("/incidents/{incident_id}/resume", response_model=JobAccepted, status_code=202)
    def resume_incident(incident_id: str, _: Principal = Depends(principal_operator)):
        status = service.status(incident_id)  # 404 if unknown
        if status.pending_review is not None:
            raise HTTPException(status_code=409, detail="incident is awaiting a reviewer decision; use /approval")

        def job() -> None:
            service.resume(incident_id)

        try:
            registry.submit(incident_id, job)
        except JobInFlightError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobAccepted(incident_id=incident_id, job_state=registry.state(incident_id) or "queued")

    # ------------------------------------------------------------------ eval

    @app.get("/eval/reports")
    def eval_reports(_: Principal = Depends(principal_viewer)) -> dict:
        reports = sorted(p.name for p in EVAL_REPORTS_DIR.glob("*.json")) if EVAL_REPORTS_DIR.is_dir() else []
        return {"reports": reports, "note": "Evaluation report generation arrives in Phase 10."}

    @app.on_event("shutdown")
    def _shutdown() -> None:
        registry.shutdown()

    return app
