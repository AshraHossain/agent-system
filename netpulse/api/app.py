"""FastAPI service: a thin, authenticated layer over ``InvestigationService``.

Endpoints:

| Method & path | Role | Purpose |
|---|---|---|
| `GET /health` | none | liveness and whether checkpoints are durable |
| `POST /incidents` | operator | submit an incident (runs in the background) |
| `GET /me` | viewer | the caller's identity and role |
| `GET /incidents/{id}` | viewer | status: job/workflow state, pending review, durability |
| `GET /incidents/{id}/results` | viewer | hypotheses, ranking, recommended actions, final report |
| `GET /incidents/{id}/evidence` | viewer | the evidence registry and telemetry/topology references |
| `POST /incidents/{id}/approval` | operator | approve / reject / request more (submitter cannot approve) |
| `POST /incidents/{id}/resume` | operator | continue a run that stalled outside an approval pause |
| `GET /audit/verify` | senior_operator | verify the audit hash chain (`?expected_head=seq:hash` detects truncation) |
| `GET /eval/reports` | viewer | list evaluation reports (written by `python -m eval.report`) |
| `GET /eval/reports/{name}` | viewer | one report as JSON |

Every mutating endpoint (`POST`) enqueues work on a background job registry
and returns 202 immediately; the caller polls `GET /incidents/{id}`. A
request's claimed reviewer identity and role are **never** read from the
body — they come only from the authenticated bearer token (`auth.py`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from netpulse.api.auth import Principal, TokenStore, principal_dependency, require_role
from netpulse.api.oidc import OidcVerifier
from netpulse.api.protection import EdgeProtectionMiddleware, Limits, Protection
from netpulse.api.registry import JobInFlightError, JobQueueFullError, JobRegistry
from netpulse.api.schemas import (
    ApprovalRequest,
    AuditVerifyResponse,
    EvidenceResponse,
    HealthResponse,
    IncidentCreateRequest,
    IncidentStatusResponse,
    JobAccepted,
    MeResponse,
    ResultsResponse,
)
from netpulse.api.tenancy import PLATFORM_TENANT, DatasetPolicy, scope_incident_id
from netpulse.errors import DataCorruptError, DataNotFoundError, ToolInputError
from netpulse.models import SYNTHETIC_DATA_NOTICE, ReviewInput
from netpulse.service import AuthorizationError, InvestigationService


@dataclass(frozen=True)
class IncidentRef:
    external: str  # what the caller calls the incident
    key: str  # the namespaced storage key


EVAL_REPORTS_DIR = Path(__file__).resolve().parents[2] / "eval" / "reports"


def create_app(
    service: InvestigationService,
    tokens: TokenStore | OidcVerifier,
    max_workers: int = 4,
    docs: bool = True,
    protection: Protection | None = None,
    datasets: DatasetPolicy | None = None,
) -> FastAPI:
    app = FastAPI(
        title="NetPulse AI API",
        description="Evidence-backed, read-only network anomaly investigation. " + SYNTHETIC_DATA_NOTICE,
        version="0.1.0",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    registry = JobRegistry(max_workers=max_workers)
    app.state.service = service
    app.state.registry = registry
    limits = Limits(protection or Protection())
    app.state.limits = limits
    app.add_middleware(EdgeProtectionMiddleware, limits=limits)

    get_principal = principal_dependency(tokens)
    viewer = require_role("viewer")
    operator = require_role("operator")
    senior = require_role("senior_operator")

    def principal_viewer(request: Request, p: Principal = Depends(get_principal)) -> Principal:
        limits.check_principal(p.name, request.method, p.tenant)
        return viewer(p)

    def principal_operator(request: Request, p: Principal = Depends(get_principal)) -> Principal:
        limits.check_principal(p.name, request.method, p.tenant)
        return operator(p)

    def principal_senior(request: Request, p: Principal = Depends(get_principal)) -> Principal:
        limits.check_principal(p.name, request.method, p.tenant)
        return senior(p)

    for exc_type, status_code in [
        (ToolInputError, 400),
        (DataNotFoundError, 404),
        (DataCorruptError, 500),
        (AuthorizationError, 403),
        (JobInFlightError, 409),
        (JobQueueFullError, 429),
    ]:

        def _handler(_request, exc, _code=status_code):
            return JSONResponse(status_code=_code, content={"detail": str(exc)})

        app.add_exception_handler(exc_type, _handler)

    # ---------------------------------------------------------------- health

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            durable=service.durable,
            audit_keyed=service.audit_keyed,
            auth_mode=tokens.mode,
            four_eyes=service.four_eyes,
            multi_tenant=multi_tenant,
            data_notice=SYNTHETIC_DATA_NOTICE,
        )

    # ------------------------------------------------------------- incidents

    multi_tenant = bool(getattr(tokens, "multi_tenant", False))
    dataset_policy = datasets or DatasetPolicy({})

    def ref_for(tenant: str, incident_id: str) -> IncidentRef:
        """The only place an incident ID becomes a storage key; every incident route goes through it."""
        if tenant == PLATFORM_TENANT:
            raise HTTPException(status_code=403, detail="platform identities cannot access incidents")
        key = scope_incident_id(tenant, incident_id)
        if key is None:  # malformed, or contains the tenant separator
            raise HTTPException(status_code=404, detail="incident not found")
        return IncidentRef(incident_id, key)

    def incident_ref(incident_id: str, principal: Principal = Depends(get_principal)) -> IncidentRef:
        return ref_for(principal.tenant, incident_id)

    def outward(payload: dict | None, ref: IncidentRef) -> dict | None:
        """Show the caller the ID they use, not the namespaced storage key."""
        if isinstance(payload, dict) and payload.get("incident_id") == ref.key:
            return {**payload, "incident_id": ref.external}
        return payload

    @app.post("/incidents", response_model=JobAccepted, status_code=202)
    def create_incident(body: IncidentCreateRequest, principal: Principal = Depends(principal_operator)):
        ref = ref_for(principal.tenant, body.incident_id or f"inc-{uuid.uuid4().hex[:12]}")
        if multi_tenant and not dataset_policy.allows(principal.tenant, body.submission.dataset_id):
            raise HTTPException(status_code=403, detail="this dataset is not available to your organization")
        try:
            service.status(ref.key)
        except DataNotFoundError:
            pass
        else:  # never let a resubmission overwrite another run's job record
            raise HTTPException(status_code=409, detail=f"incident {ref.external!r} already exists")
        submission = body.submission.model_dump(mode="json")
        submitted_at = service.deps.clock().isoformat()

        def job() -> None:
            service.start(submission, submitted_at=submitted_at, incident_id=ref.key, submitted_by=principal.name)

        try:
            registry.submit(ref.key, job, group=principal.tenant)
        except JobInFlightError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobAccepted(incident_id=ref.external, job_state=registry.state(ref.key) or "queued")

    @app.get("/me", response_model=MeResponse)
    def me(principal: Principal = Depends(principal_viewer)) -> MeResponse:
        """Who the API believes the caller is, so a UI can explain what they may do."""
        return MeResponse(name=principal.name, role=principal.role, tenant=principal.tenant)

    @app.get("/incidents/{incident_id}", response_model=IncidentStatusResponse)
    def incident_status(
        ref: IncidentRef = Depends(incident_ref), _: Principal = Depends(principal_viewer)
    ) -> IncidentStatusResponse:
        job_state = registry.state(ref.key)
        try:
            status = service.status(ref.key)
        except DataNotFoundError:
            if job_state is None:
                raise
            return IncidentStatusResponse(
                incident_id=ref.external,
                job_state=job_state,
                status=None,
                approval_status=None,
                pending_review=None,
                next_nodes=[],
                durable=service.durable,
                error=registry.error(ref.key),
            )
        return IncidentStatusResponse(
            incident_id=ref.external,
            job_state=job_state,
            status=status.status,
            approval_status=status.approval_status,
            pending_review=outward(status.pending_review, ref),
            next_nodes=status.next_nodes,
            durable=status.durable,
            error=registry.error(ref.key),
        )

    @app.get("/incidents/{incident_id}/results", response_model=ResultsResponse)
    def incident_results(
        ref: IncidentRef = Depends(incident_ref), _: Principal = Depends(principal_viewer)
    ) -> ResultsResponse:
        state = service.state(ref.key)
        return ResultsResponse(
            incident_id=ref.external,
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
            final_report=outward(state.get("final_report"), ref),
        )

    @app.get("/incidents/{incident_id}/evidence", response_model=EvidenceResponse)
    def incident_evidence(
        ref: IncidentRef = Depends(incident_ref), _: Principal = Depends(principal_viewer)
    ) -> EvidenceResponse:
        state = service.state(ref.key)
        return EvidenceResponse(
            incident_id=ref.external,
            telemetry_window=state.get("telemetry_window"),
            topology_evidence=state.get("topology_evidence"),
            evidence=sorted((state.get("evidence_references") or {}).values(), key=lambda e: e["evidence_id"]),
        )

    @app.post("/incidents/{incident_id}/approval", response_model=JobAccepted, status_code=202)
    def submit_approval(
        body: ApprovalRequest,
        ref: IncidentRef = Depends(incident_ref),
        principal: Principal = Depends(principal_operator),
    ):
        status = service.status(ref.key)  # 404 if unknown (or not in the caller's namespace)
        if status.pending_review is None:
            raise HTTPException(status_code=409, detail="incident is not awaiting approval")
        review = ReviewInput(
            reviewer_id=principal.name,
            reviewer_role=principal.role,
            choice=body.choice,  # type: ignore[arg-type]
            comment=body.comment,
            approved_action_ids=body.approved_action_ids,
        )
        service.authorize_review(ref.key, review)  # role and four-eyes: refuse now (403), not in the background

        def job() -> None:
            service.decide(ref.key, review)

        try:
            registry.submit(ref.key, job, group=principal.tenant)
        except JobInFlightError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobAccepted(incident_id=ref.external, job_state=registry.state(ref.key) or "queued")

    @app.post("/incidents/{incident_id}/resume", response_model=JobAccepted, status_code=202)
    def resume_incident(ref: IncidentRef = Depends(incident_ref), principal: Principal = Depends(principal_operator)):
        status = service.status(ref.key)  # 404 if unknown (or not in the caller's namespace)
        if status.pending_review is not None:
            raise HTTPException(status_code=409, detail="incident is awaiting a reviewer decision; use /approval")

        def job() -> None:
            service.resume(ref.key)

        try:
            registry.submit(ref.key, job, group=principal.tenant)
        except JobInFlightError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobAccepted(incident_id=ref.external, job_state=registry.state(ref.key) or "queued")

    # ----------------------------------------------------------------- audit

    @app.get("/audit/verify", response_model=AuditVerifyResponse)
    def audit_verify(expected_head: str | None = None, principal: Principal = Depends(principal_senior)):
        """Re-verify the audit hash chain. ``expected_head`` (``seq:hash``) also detects truncated tails.

        The chain is global (it covers every tenant), so in multi-tenant mode only platform identities may run it.
        """
        if multi_tenant and principal.tenant != PLATFORM_TENANT:
            raise HTTPException(
                status_code=403, detail="the audit log spans all organizations; platform operators only"
            )
        result = service.audit_verify(expected_head)
        return AuditVerifyResponse(
            ok=result.ok, rows=result.rows, legacy_rows=result.legacy_rows, keyed=result.keyed,
            head=result.head, error=result.error, first_bad_seq=result.first_bad_seq,
        )  # fmt: skip

    # ------------------------------------------------------------------ eval

    @app.get("/eval/reports")
    def eval_reports(_: Principal = Depends(principal_viewer)) -> dict:
        names = sorted(p.name for p in EVAL_REPORTS_DIR.glob("*.json")) if EVAL_REPORTS_DIR.is_dir() else []
        return {"reports": names, "notice": SYNTHETIC_DATA_NOTICE}

    @app.get("/eval/reports/{name}")
    def eval_report(name: str, _: Principal = Depends(principal_viewer)) -> dict:
        # Only names that exist in the reports directory are served; the name is never joined into a path unchecked.
        known = {p.name: p for p in EVAL_REPORTS_DIR.glob("*.json")} if EVAL_REPORTS_DIR.is_dir() else {}
        if name not in known:
            raise HTTPException(status_code=404, detail="no such evaluation report")
        return json.loads(known[name].read_text())

    @app.on_event("shutdown")
    def _shutdown() -> None:
        registry.shutdown()

    return app
