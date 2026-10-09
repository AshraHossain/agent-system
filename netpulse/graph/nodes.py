"""Graph node implementations (Phase 6: linear happy path, heuristic or pluggable generator).

Each node has the signature ``(state, deps) -> partial update`` and writes only
the fields it owns (docs/state_model.md). State values are JSON dicts; nodes
re-validate whatever they read through the Pydantic models.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta

from netpulse.data.schemas import METRIC_TABLE, TelemetryQuery
from netpulse.data.store import coverage_evidence
from netpulse.detection import DetectionRequest, detect_series, to_evidence
from netpulse.errors import ToolError, ToolInputError
from netpulse.evidence import EvidenceIdAllocator
from netpulse.graph.deps import Deps
from netpulse.graph.ranking import assess
from netpulse.graph.verification import verify
from netpulse.graph.wrapper import error_record
from netpulse.llm.base import EvidenceView, GenerationContext
from netpulse.models import (
    SYNTHETIC_DATA_NOTICE,
    ActionKind,
    Anomaly,
    ApprovalStatus,
    Budget,
    Classification,
    ConfidenceAssessment,
    ErrorKind,
    EvidenceItem,
    FinalReport,
    Hypothesis,
    IncidentCategory,
    IncidentSubmission,
    Metric,
    ProposedAction,
    RankedHypothesis,
    ReportOutcome,
    RequestMetadata,
    RetrievedDocumentRef,
    RootCauseCategory,
    Severity,
    WorkflowStatus,
)
from netpulse.policy.catalog import load_catalog
from netpulse.retrieval.retriever import RetrievalQuery, document_evidence
from netpulse.retrieval.sanitize import sanitize
from netpulse.topology.analysis import (
    LocalizationQuery,
    LocalizationResult,
    blast_radius_evidence,
    event_evidence,
    localization_evidence,
    maintenance_evidence,
)

COVERAGE_WARN = 0.9  # below this, coverage becomes data-quality evidence
COVERAGE_GAP = 0.5  # below this, an entity counts as a visibility gap for ranking

METRIC_CATEGORIES: dict[Metric, list[RootCauseCategory]] = {
    Metric.UTILIZATION_PCT: [RootCauseCategory.LINK_CONGESTION, RootCauseCategory.TRAFFIC_SURGE],
    Metric.LATENCY_MS: [RootCauseCategory.LINK_CONGESTION],
    Metric.PACKET_LOSS_PCT: [RootCauseCategory.LINK_DEGRADATION],
    Metric.ERROR_RATE: [RootCauseCategory.LINK_DEGRADATION],
    Metric.CPU_PCT: [RootCauseCategory.DEVICE_CPU_SATURATION],
    Metric.MEMORY_PCT: [RootCauseCategory.DEVICE_MEMORY_EXHAUSTION],
    Metric.SERVICE_LATENCY_MS: [RootCauseCategory.UPSTREAM_DEPENDENCY],
    Metric.SERVICE_SUCCESS_PCT: [RootCauseCategory.UPSTREAM_DEPENDENCY],
}

_CLASSIFY_RULES: list[tuple[IncidentCategory, re.Pattern[str]]] = [
    (IncidentCategory.TELEMETRY_GAP, re.compile(r"\b(dashboards? (lag|missing)|no data|telemetry)\b", re.I)),
    (IncidentCategory.DEVICE_RESOURCE, re.compile(r"\b(cpu|memory|sluggish)\b", re.I)),
    (IncidentCategory.CONGESTION, re.compile(r"\b(utili[sz]ation|saturat\w*|congest\w*)\b", re.I)),
    (IncidentCategory.TRAFFIC_SPIKE, re.compile(r"\b(spike|surge|broadcast)\b", re.I)),
    (IncidentCategory.LINK_DEGRADATION, re.compile(r"\b(crc|flaky|errors?)\b", re.I)),
    (IncidentCategory.PACKET_LOSS, re.compile(r"\b(loss|drops?|dropping|choppy|retransmission\w*|stalls?)\b", re.I)),
    (IncidentCategory.LATENCY, re.compile(r"\b(slow\w*|latency|lag\w*|buffering|sound\w* .*off)\b", re.I)),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _dump(model) -> dict:
    return model.model_dump(mode="json")


def _submission(state: dict) -> IncidentSubmission:
    return IncidentSubmission.model_validate(state["submission"])


def _as_of(state: dict) -> datetime:
    return RequestMetadata.model_validate(state["request_metadata"]).submitted_at


def _registry(state: dict) -> dict[str, EvidenceItem]:
    return {k: EvidenceItem.model_validate(v) for k, v in (state.get("evidence_references") or {}).items()}


def _anomalies(state: dict) -> list[Anomaly]:
    return [Anomaly.model_validate(a) for a in state.get("detected_anomalies") or []]


def _localization(state: dict) -> LocalizationResult | None:
    topo = state.get("topology_evidence") or {}
    return LocalizationResult.model_validate(topo["localization"]) if topo.get("localization") else None


def _coverage_gaps(state: dict) -> list[str]:
    return list((state.get("telemetry_window") or {}).get("gap_entities", []))


def _window(state: dict, deps: Deps) -> tuple[datetime, datetime]:
    """Investigation window; widened by one hour per extra investigation round (Phase 7 routing)."""
    sub = _submission(state)
    extra = timedelta(hours=max(0, state.get("investigation_rounds", 1) - 1))
    return sub.window_start - extra, sub.window_end


# --------------------------------------------------------------------------
# 1. intake
# --------------------------------------------------------------------------


def intake_validate(state: dict, deps: Deps) -> dict:
    try:
        submission = IncidentSubmission.model_validate(state.get("submission") or {})
        metadata = RequestMetadata.model_validate(state.get("request_metadata") or {})
        budget = Budget.model_validate(state.get("budget") or {})
    except ValueError as exc:
        raise ToolInputError(f"invalid incident submission: {exc}") from exc
    if submission.window_end > metadata.submitted_at:
        raise ToolInputError("window_end is after submitted_at; cannot investigate the future")
    known = deps.topology.topology.entity_ids()
    unknown = sorted(set(submission.suspected_entities) - known)
    warnings = []
    if unknown:
        warnings.append(f"ignored unknown suspected entities: {', '.join(unknown)}")
        submission = submission.model_copy(
            update={"suspected_entities": [e for e in submission.suspected_entities if e in known]}
        )
    flags = sanitize(f"{submission.title}\n{submission.description}").injection_flags
    if flags:
        warnings.append(
            "submission text contains instruction-like content (" + ", ".join(flags) + "); treated as data only"
        )
    return {
        "incident_id": state.get("incident_id") or f"inc-{uuid.uuid4().hex[:12]}",
        "submission": _dump(submission),
        "request_metadata": _dump(metadata),
        "budget": _dump(budget),
        "deadline_at": (deps.clock() + timedelta(seconds=budget.wall_clock_seconds)).isoformat(),
        "input_warnings": warnings,
        "retry_count": 0,
        "investigation_rounds": 0,
        "approval_status": ApprovalStatus.NOT_REQUIRED.value,
        "status": WorkflowStatus.RUNNING.value,
        "fatal_error": False,
    }


# --------------------------------------------------------------------------
# 2. classification (rules over untrusted text; drives retrieval and display only)
# --------------------------------------------------------------------------


def classify_incident(state: dict, deps: Deps) -> dict:
    sub = _submission(state)
    text = sanitize(f"{sub.title}. {sub.description}").text
    category = next((cat for cat, rx in _CLASSIFY_RULES if rx.search(text)), IncidentCategory.UNKNOWN)
    severity = sub.severity_hint or Severity.MEDIUM
    rationale = (
        f"Keyword rules on the operator's description matched '{category.value}'. "
        f"Severity {'from operator hint' if sub.severity_hint else 'defaulted to medium'}. "
        "This classification steers retrieval only; it is not evidence of a cause."
    )
    return {"classification": _dump(Classification(category=category, severity=severity, rationale=rationale))}


# --------------------------------------------------------------------------
# 3. telemetry retrieval
# --------------------------------------------------------------------------


def retrieve_telemetry(state: dict, deps: Deps) -> dict:
    rounds = state.get("investigation_rounds", 0) + 1
    start, end = _window({**state, "investigation_rounds": rounds}, deps)
    window = deps.store.telemetry_window(
        TelemetryQuery(
            dataset_id=_submission(state).dataset_id, window_start=start, window_end=end, as_of=_as_of(state)
        )
    )
    alloc = EvidenceIdAllocator(state.get("evidence_references") or {})
    evidence, dq_ids = {}, []
    for cov in window.coverage:
        if cov.coverage_ratio < COVERAGE_WARN:
            item = coverage_evidence(cov, alloc.next("dq"))
            evidence[item.evidence_id] = _dump(item)
            dq_ids.append(item.evidence_id)
    ref = window.ref.model_copy(update={"data_quality_evidence_ids": dq_ids})
    gaps = sorted(c.entity_id for c in window.coverage if c.coverage_ratio < COVERAGE_GAP)
    reset = {"retry_count": 0, "verifier_feedback": []} if rounds > 1 else {}
    return {
        **reset,
        "telemetry_window": {**_dump(ref), "gap_entities": gaps},
        "evidence_references": evidence,
        "investigation_rounds": rounds,
        "_trace_detail": (
            f"{window.ref.row_count}/{window.ref.expected_row_count} rows visible; {len(dq_ids)} coverage issues"
        ),
    }


# --------------------------------------------------------------------------
# 4. deterministic detection
# --------------------------------------------------------------------------


def detect_anomalies(state: dict, deps: Deps) -> dict:
    ref = state["telemetry_window"]
    start, end = datetime.fromisoformat(ref["start"]), datetime.fromisoformat(ref["end"])
    window = deps.store.telemetry_window(
        TelemetryQuery(dataset_id=ref["dataset_id"], window_start=start, window_end=end, as_of=_as_of(state))
    )
    alloc = EvidenceIdAllocator(state.get("evidence_references") or {})
    evidence, anomalies, errors = {}, [], []
    for cov in window.coverage:
        for metric in [m for m in ref["metrics"] if _table_of(m) == cov.table]:
            request = DetectionRequest(entity_id=cov.entity_id, metric=metric, window_start=start, window_end=end)
            report = detect_series(request, *window.series(cov.entity_id, Metric(metric)), deps.detection)
            for failure in report.failures:
                errors.append(
                    error_record(
                        "detect_anomalies",
                        ErrorKind.TOOL_FAILURE,
                        f"{cov.entity_id}/{metric}: {failure.method.value} {failure.error_type}: {failure.message}",
                        True,
                        deps,
                    )
                )
            for found in report.anomalies:
                item = to_evidence(found, alloc.next("anom"), deps.detection)
                evidence[item.evidence_id] = _dump(item)
                anomalies.append(
                    _dump(
                        Anomaly(
                            anomaly_id=f"anom-{len(anomalies) + 1:03d}",
                            entity_id=found.entity_id,
                            metric=found.metric,
                            detectors=found.methods,
                            confirmed=found.confirmed,
                            implausible=found.implausible,
                            direction=found.direction,
                            start=found.start,
                            end=found.end,
                            peak_value=found.peak_value,
                            peak_at=found.peak_at,
                            baseline_value=found.baseline_value,
                            threshold=found.threshold,
                            evidence_id=item.evidence_id,
                        )
                    )
                )
    confirmed = sum(a["confirmed"] for a in anomalies)
    return {
        "detected_anomalies": anomalies,
        "evidence_references": evidence,
        "errors": errors,
        "_trace_detail": f"{confirmed} confirmed, {len(anomalies) - confirmed} unconfirmed anomalies",
    }


def _table_of(metric: str) -> str:
    return METRIC_TABLE[Metric(metric)]


# --------------------------------------------------------------------------
# 5. topology and dependency analysis
# --------------------------------------------------------------------------


def analyze_topology(state: dict, deps: Deps) -> dict:
    graph = deps.topology
    anomalies = [a for a in _anomalies(state) if a.confirmed]
    symptoms = sorted({a.entity_id for a in anomalies})
    alloc = EvidenceIdAllocator(state.get("evidence_references") or {})
    evidence: dict[str, dict] = {}

    def add(item: EvidenceItem) -> str:
        evidence[item.evidence_id] = _dump(item)
        return item.evidence_id

    if not symptoms:
        return {
            "affected_nodes": [],
            "affected_services": [],
            "topology_evidence": {"localization": None, "blast_radius": [], "evidence_ids": []},
            "_trace_detail": "no confirmed anomalies to localize",
        }

    loc = graph.localize(LocalizationQuery(symptomatic_entities=symptoms, max_candidates=20))
    ids = [add(localization_evidence(loc, alloc.next("topo")))]
    roots = loc.minimal_cover or [c.entity_id for c in loc.candidates[:3]]
    blast = []
    for root in roots:
        br = graph.blast_radius(root)
        blast.append(_dump(br))
        ids.append(add(blast_radius_evidence(br, alloc.next("topo"))))

    start, end = (
        datetime.fromisoformat(state["telemetry_window"]["start"]),
        datetime.fromisoformat(state["telemetry_window"]["end"]),
    )
    dataset = _submission(state).dataset_id
    scope = sorted(set(symptoms) | set(roots))
    for event in graph.related_events(
        deps.store.events(dataset, start - timedelta(hours=2), end, _as_of(state)), scope, start, end
    ):
        if event.severity != "info":
            ids.append(add(event_evidence(event, alloc.next("evt"))))
    for window in graph.overlapping_maintenance(deps.store.maintenance(dataset), scope, start, end):
        ids.append(add(maintenance_evidence(window, alloc.next("mnt"))))

    services = {s for s in symptoms if graph.topology.kind(s) == "service"}
    nodes = set()
    for e in symptoms:
        kind = graph.topology.kind(e)
        if kind == "node":
            nodes.add(e)
        elif kind == "link":
            nodes.update(graph.endpoints(e))
    for br in blast:
        services.update(br["affected_services"])
        nodes.update(br["isolated_nodes"])
    return {
        "affected_nodes": sorted(nodes),
        "affected_services": sorted(services),
        "topology_evidence": {"localization": _dump(loc), "blast_radius": blast, "evidence_ids": ids},
        "evidence_references": evidence,
        "_trace_detail": f"cover={loc.minimal_cover} corroborated={loc.corroborated}",
    }


# --------------------------------------------------------------------------
# 6–7. retrieval (untrusted evidence)
# --------------------------------------------------------------------------


def _retrieval_query(state: dict) -> RetrievalQuery:
    anomalies = [a for a in _anomalies(state) if a.confirmed]
    categories: list[RootCauseCategory] = []
    for a in anomalies:
        categories += [c for c in METRIC_CATEGORIES[a.metric] if c not in categories]
        if a.implausible and RootCauseCategory.TELEMETRY_FAULT not in categories:
            categories.append(RootCauseCategory.TELEMETRY_FAULT)
    loc = _localization(state)
    entities = (loc.minimal_cover if loc else []) or sorted({a.entity_id for a in anomalies})
    category = (state.get("classification") or {}).get("category", "unknown")
    terms = sorted({a.metric.value.replace("_pct", "").replace("_", " ") for a in anomalies})
    text = (
        " ".join([category.replace("_", " "), *terms, *(c.value.replace("_", " ") for c in categories)]) or "incident"
    )
    return RetrievalQuery(text=text, categories=categories, entity_ids=entities[:10], as_of=_as_of(state), top_k=3)


def retrieve_history(state: dict, deps: Deps) -> dict:
    result = deps.retriever.search_incidents(_retrieval_query(state))
    alloc = EvidenceIdAllocator(state.get("evidence_references") or {})
    evidence, refs = {}, []
    for doc in result.documents:
        item = document_evidence(doc, alloc.next("hist"))
        evidence[item.evidence_id] = _dump(item)
        refs.append(
            _dump(
                RetrievedDocumentRef(
                    doc_id=doc.doc_id,
                    title=doc.title,
                    kind="historical_incident",
                    score=doc.score,
                    evidence_id=item.evidence_id,
                )
            )
        )
    return {"historical_incidents": refs, "evidence_references": evidence}


def retrieve_runbooks(state: dict, deps: Deps) -> dict:
    result = deps.retriever.search_runbooks(_retrieval_query(state))
    alloc = EvidenceIdAllocator(state.get("evidence_references") or {})
    evidence, refs = {}, []
    for doc in result.documents:
        item = document_evidence(doc, alloc.next("rb"))
        evidence[item.evidence_id] = _dump(item)
        refs.append(
            _dump(
                RetrievedDocumentRef(
                    doc_id=doc.doc_id, title=doc.title, kind="runbook", score=doc.score, evidence_id=item.evidence_id
                )
            )
        )
    for conflict in result.conflicts:
        item = EvidenceItem(
            evidence_id=alloc.next("rb"),
            source="runbook",
            summary=f"Runbook conflict: {conflict.note}. Neither runbook is applied automatically."[:600],
            source_ref="runbook-conflict:" + "+".join(conflict.runbooks),
            method="declared_conflict",
            trusted=False,
        )
        evidence[item.evidence_id] = _dump(item)
    return {"retrieved_runbooks": refs, "evidence_references": evidence}


# --------------------------------------------------------------------------
# 8. hypothesis generation (the only LLM-capable step)
# --------------------------------------------------------------------------


def generation_context(state: dict) -> GenerationContext:
    registry = _registry(state)
    ref = state["telemetry_window"]
    return GenerationContext(
        incident_id=state["incident_id"],
        category=(state.get("classification") or {}).get("category", "unknown"),
        window_start=ref["start"],
        window_end=ref["end"],
        evidence=[
            EvidenceView(
                evidence_id=e.evidence_id,
                source=e.source,
                trusted=e.trusted,
                summary=e.summary,
                entity_ids=e.entity_ids,
                metric=e.metric,
            )
            for e in registry.values()
        ],
        anomalies=_anomalies(state),
        localization=_localization(state),
        coverage_gaps=_coverage_gaps(state),
        verifier_feedback=state.get("verifier_feedback") or [],
        attempt=state.get("retry_count", 0),
    )


def generate_hypotheses(state: dict, deps: Deps) -> dict:
    attempt = state.get("retry_count", 0) + 1
    t0 = time.perf_counter()
    log = {"attempt": attempt, "round": state.get("investigation_rounds", 1)}
    try:
        result = deps.generator.generate(generation_context(state))
    except ToolError:
        raise
    except Exception as exc:  # generator failures are recoverable: the attempt counts, hypotheses are empty
        log.update(
            provider=deps.generator.provider,
            model=deps.generator.model,
            outcome="error",
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
            hypotheses=0,
        )
        return {
            "hypotheses": [],
            "retry_count": attempt,
            "generation_attempts": [{**log, "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}],
            "errors": [error_record("generate_hypotheses", ErrorKind.LLM_FAILURE, exc, True, deps)],
        }
    log.update(
        provider=result.provider,
        model=result.model,
        outcome="ok",
        hypotheses=len(result.hypotheses),
        note=(result.raw_output or "")[:200] if "fallback" in result.provider else None,
    )
    return {
        "hypotheses": [_dump(h) for h in result.hypotheses],
        "retry_count": attempt,
        "generation_attempts": [{**log, "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}],
        "_trace_detail": f"attempt {attempt}: {len(result.hypotheses)} hypotheses from {result.provider}",
    }


def on_generation_timeout(state: dict, deps: Deps) -> dict:
    """Wrapper callback: an LLM timeout is a failed attempt, not a fatal error."""
    attempt = state.get("retry_count", 0) + 1
    return {
        "hypotheses": [],
        "retry_count": attempt,
        "generation_attempts": [
            {
                "attempt": attempt,
                "round": state.get("investigation_rounds", 1),
                "provider": deps.generator.provider,
                "model": deps.generator.model,
                "outcome": "timeout",
                "hypotheses": 0,
            }
        ],
    }


# --------------------------------------------------------------------------
# 9. evidence verification (deterministic)
# --------------------------------------------------------------------------


def verify_evidence(state: dict, deps: Deps) -> dict:
    hypotheses = [Hypothesis.model_validate(h) for h in state.get("hypotheses") or []]
    window = state.get("telemetry_window") or {}
    outcome = verify(
        hypotheses,
        _registry(state),
        _anomalies(state),
        deps.topology,
        _coverage_gaps(state),
        observed_entities=window.get("entity_ids", []),
        attempt=state.get("retry_count", 0),
    )
    r = outcome.result
    return {
        "verification_results": [_dump(r)],
        "verifier_feedback": outcome.feedback,
        "evidence_references": {k: _dump(v) for k, v in outcome.new_evidence.items()},
        "_trace_detail": f"attempt {r.attempt}: passed={r.passed} accepted={len(r.accepted_hypothesis_ids)} "
        f"rejected={len(r.rejected_hypothesis_ids)}",
    }


def _accepted(state: dict) -> list[Hypothesis]:
    results = state.get("verification_results") or []
    hypotheses = [Hypothesis.model_validate(h) for h in state.get("hypotheses") or []]
    if not results:
        return []
    accepted = set(results[-1]["accepted_hypothesis_ids"])
    return [h for h in hypotheses if h.hypothesis_id in accepted]


# --------------------------------------------------------------------------
# 10. ranking (only verified hypotheses are ranked)
# --------------------------------------------------------------------------


def rank_hypotheses(state: dict, deps: Deps) -> dict:
    loc = _localization(state)
    assessment = assess(
        _accepted(state),
        _registry(state),
        _anomalies(state),
        _coverage_gaps(state),
        loc.minimal_cover if loc else [],
    )
    return {"confidence_assessment": _dump(assessment)}


# --------------------------------------------------------------------------
# 11. recommendations (Phase 6: read-only diagnostics only)
# --------------------------------------------------------------------------


def recommend_actions(state: dict, deps: Deps) -> dict:
    catalog = load_catalog()
    assessment = ConfidenceAssessment.model_validate(state["confidence_assessment"])
    hypotheses = {h["hypothesis_id"]: Hypothesis.model_validate(h) for h in state.get("hypotheses") or []}
    actions: list[ProposedAction] = []
    seen: set[tuple[str, str]] = set()

    def propose(catalog_id: str, target: list[str], hyp: Hypothesis | None) -> None:
        key = (catalog_id, ",".join(target))
        if key in seen or len(actions) >= 6:
            return
        seen.add(key)
        entry = catalog[catalog_id]
        actions.append(
            ProposedAction(
                action_id=f"act-{len(actions) + 1:02d}",
                catalog_id=catalog_id,
                kind=ActionKind.DIAGNOSTIC,
                description=entry.description,
                target_entities=target,
                related_hypothesis_id=hyp.hypothesis_id if hyp else None,
                evidence_ids=hyp.supporting_evidence[:5] if hyp else [],
                reversible=True,
            )
        )

    for ranked in assessment.ranking[:3]:
        hyp = hypotheses[ranked.hypothesis_id]
        target = [hyp.suspected_root_entity] if hyp.suspected_root_entity else hyp.affected_components[:3]
        for entry in catalog.values():
            if entry.kind == "diagnostic" and hyp.cause_category.value in entry.applies_to:
                propose(entry.catalog_id, target, hyp)
    if _coverage_gaps(state):
        propose("check_collector_health", _coverage_gaps(state), None)
    if not actions:
        propose("run_path_trace", state.get("affected_services") or [], None)
    return {"recommended_actions": [_dump(a) for a in actions]}


# --------------------------------------------------------------------------
# 14. report
# --------------------------------------------------------------------------


def escalate(state: dict, deps: Deps) -> dict:
    """Hand the incident to a human operator. Records why; never asserts a cause on its own."""
    reasons = []
    if state.get("deadline_at") and deps.clock() > datetime.fromisoformat(state["deadline_at"]):
        reasons.append("investigation time budget exhausted")
    raw = state.get("confidence_assessment")
    assessment = ConfidenceAssessment.model_validate(raw) if raw else None
    if assessment is None:
        reasons.append("investigation stopped before hypotheses were ranked")
    elif not assessment.conclusive:
        reasons.append(f"evidence insufficient after {state.get('investigation_rounds', 0)} investigation round(s)")
    severity = (state.get("classification") or {}).get("severity")
    if severity == Severity.CRITICAL.value:
        reasons.append("operator-reported severity is critical")
    rejected = sum(len(v.get("rejected_hypothesis_ids", [])) for v in state.get("verification_results") or [])
    if rejected:
        reasons.append(f"{rejected} generated hypothesis(es) were rejected by verification")
    errors = (
        [error_record("escalate", ErrorKind.BUDGET_EXHAUSTED, "time budget exhausted before completion", True, deps)]
        if "investigation time budget exhausted" in reasons
        else []
    )
    return {
        "approval_status": ApprovalStatus.ESCALATED.value,
        "status": WorkflowStatus.ESCALATED.value,
        "escalation_reasons": reasons or ["escalation requested by workflow policy"],
        "errors": errors,
    }


def compile_report(state: dict, deps: Deps) -> dict:
    raw = state.get("confidence_assessment")
    assessment = ConfidenceAssessment.model_validate(raw) if raw else None
    hypotheses = {h["hypothesis_id"]: Hypothesis.model_validate(h) for h in state.get("hypotheses") or []}
    registry = _registry(state)
    top: list[RankedHypothesis] = assessment.ranking[:3] if assessment else []
    escalated = state.get("approval_status") == ApprovalStatus.ESCALATED.value
    if assessment and assessment.conclusive:
        leaders = [r for r in top if r.confidence == top[0].confidence]
        causes = "; ".join(
            f"{hypotheses[r.hypothesis_id].cause_category.value} at "
            f"{hypotheses[r.hypothesis_id].suspected_root_entity} ({r.confidence.value} confidence)"
            for r in leaders
        )
        outcome, status = ReportOutcome.ROOT_CAUSE_IDENTIFIED, WorkflowStatus.COMPLETED
        summary = f"Most likely cause(s): {causes}. These are evidence-backed inferences, not verified facts."
    else:
        outcome, status = ReportOutcome.INCONCLUSIVE, WorkflowStatus.INCONCLUSIVE
        summary = (
            "Inconclusive: the available evidence does not single out a root cause. "
            + (f"Leading candidate: {top[0].hypothesis_id} ({top[0].confidence.value}). " if top else "")
            + "See missing evidence and recommended diagnostics."
        )
    if escalated:
        status = WorkflowStatus.ESCALATED
        if outcome != ReportOutcome.ROOT_CAUSE_IDENTIFIED:
            outcome = ReportOutcome.ESCALATED
        summary += " Escalated to a human operator: " + "; ".join(state.get("escalation_reasons") or []) + "."
    cited = sorted({e for r in top for e in hypotheses[r.hypothesis_id].supporting_evidence if e in registry})
    verification = state.get("verification_results") or []
    rejected = sorted({h for v in verification for h in v.get("rejected_hypothesis_ids", [])})
    limitations = [
        "Synthetic data only; no real network was observed.",
        "Confidence levels are rule-based and ordinal, not probabilities.",
    ]
    if rejected:
        codes = sorted({i["code"] for v in verification for i in v["issues"] if i["blocking"]})
        limitations.append(
            f"{len(rejected)} generated hypothesis(es) were rejected by deterministic verification "
            f"({', '.join(codes)}) and are excluded from ranking."
        )
    attempts = state.get("generation_attempts") or []
    if any("fallback" in (a.get("provider") or "") for a in attempts):
        limitations.append("The configured LLM was unavailable; the rule-based fallback generator was used.")
    limitations += [f"Input warning: {w}" for w in state.get("input_warnings") or []]
    report = {
        "incident_id": state["incident_id"],
        "outcome": outcome,
        "summary": summary,
        "top_hypotheses": [_dump(r) for r in top],
        "cited_evidence_ids": cited,
        "recommended_actions": state.get("recommended_actions") or [],
        "approval_status": state.get("approval_status", ApprovalStatus.NOT_REQUIRED.value),
        "missing_evidence": assessment.missing_evidence if assessment else ["investigation did not reach ranking"],
        "errors": state.get("errors") or [],
        "limitations": limitations,
        "data_notice": SYNTHETIC_DATA_NOTICE,
        "generated_at": deps.clock(),
    }
    return {"final_report": _dump(FinalReport.model_validate(report)), "status": status.value}


def failure_report(state: dict, deps: Deps) -> dict:
    """Last resort: always produces a structured report, even from a badly broken state."""
    errors = state.get("errors") or []
    try:
        report = FinalReport(
            incident_id=state.get("incident_id") or "unknown",
            outcome=ReportOutcome.FAILED,
            summary="The investigation could not complete: "
            + ("; ".join(e.get("message", "")[:200] for e in errors[-3:]) or "unknown error"),
            approval_status=ApprovalStatus.ESCALATED,
            errors=errors,
            limitations=["No root cause is asserted. A human operator must take over this incident."],
            generated_at=deps.clock(),
        )
    except Exception as exc:  # noqa: BLE001 - even malformed error records must not lose the incident
        report = FinalReport(
            incident_id=str(state.get("incident_id") or "unknown"),
            outcome=ReportOutcome.FAILED,
            summary=(
                f"The investigation could not complete, and its error records were unreadable ({type(exc).__name__})."
            ),
            approval_status=ApprovalStatus.ESCALATED,
            generated_at=deps.clock(),
        )
    return {
        "final_report": _dump(report),
        "status": WorkflowStatus.FAILED.value,
        "approval_status": ApprovalStatus.ESCALATED.value,
    }
