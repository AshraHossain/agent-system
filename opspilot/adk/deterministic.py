"""Deterministic (non-LLM) agents: intake, evidence verifier, re-investigation gate, finalizer.

They subclass ADK's `BaseAgent` and communicate exclusively through event
`state_delta`s, like the LLM agents, so they appear in traces, persist with the
session, and are skipped on resume when their output already exists.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import ValidationError

from opspilot.adk.plugins import BudgetPlugin
from opspilot.adk.rounds import plan_next_round, rounds_info
from opspilot.adk.runtime import register, resolve
from opspilot.config import Settings
from opspilot.contracts.evidence import Evidence, EvidenceKind
from opspilot.contracts.findings import (
    IncidentAnalysis,
    KnowledgeFinding,
    ReportDraft,
    ReviewResult,
    TelemetryFinding,
    TopologyFinding,
)
from opspilot.contracts.report import RunMetrics
from opspilot.contracts.request import InvestigationScope
from opspilot.contracts.verification import VerificationResult
from opspilot.core import report as report_core
from opspilot.core.errors import ToolError
from opspilot.core.security import validate_request
from opspilot.core.telemetry import compare_to_baseline, default_windows
from opspilot.core.verification import evidence_from_state, verify

SYMPTOMS = (
    "latency",
    "packet loss",
    "loss",
    "errors",
    "timeouts",
    "slow",
    "jitter",
    "drops",
    "congestion",
    "dns",
    "wan",
    "firewall",
    "load balancer",
    "external",
)


def _event(agent: BaseAgent, ctx: InvocationContext, delta: dict, text: str) -> Event:
    return Event(
        author=agent.name,
        invocation_id=ctx.invocation_id,
        branch=ctx.branch,
        actions=EventActions(state_delta=delta),
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def _parse(model, raw):
    if raw is None:
        return None, None
    try:
        return model.model_validate(raw), None
    except ValidationError as exc:
        return None, f"{model.__name__} failed validation: {exc.error_count()} error(s)"


class IntakeAgent(BaseAgent):
    """Validate + redact the request, bind the dataset, and fix the investigation scope."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if state.get("scope"):
            yield _event(self, ctx, {}, "intake: scope already present; skipped on resume")
            return
        settings = Settings.from_env()
        inv = state.get("investigation_id") or f"inv-{uuid.uuid4().hex[:12]}"
        dataset_id = state.get("dataset_id") or settings.default_dataset
        try:
            rt = (
                resolve({"investigation_id": inv, "dataset_id": dataset_id})
                if state.get("investigation_id")
                else register(inv, settings, dataset_id)
            )
            raw = " ".join(
                p.text for p in (ctx.user_content.parts if ctx.user_content else []) if p.text
            )
            req = validate_request(raw, rt.settings.limits.max_request_chars)
        except ToolError as exc:
            # Later stages check `intake_error` and skip themselves (ADK 2.x workflow
            # agents do not stop on ctx.end_invocation).
            yield _event(
                self,
                ctx,
                {
                    "investigation_id": inv,
                    "dataset_id": dataset_id,
                    "intake_error": f"{exc.code}: {exc}",
                },
                f"intake rejected the request: {exc}",
            )
            return
        ds, topo = rt.dataset, rt.topology
        window, baseline = default_windows(ds.reported_at)
        lowered = req.text.lower()
        named = [s for s in topo.services if re.search(rf"\b{re.escape(s)}\b", lowered)]
        flags = list(req.flags)
        symptomatic, delta = [], {}
        try:
            for s in topo.services:
                for metric in ("latency_p95_ms", "error_pct"):
                    cmp = compare_to_baseline(ds, f"svc:{s}", metric, window, baseline)
                    if cmp.verdict in ("elevated", "critical", "recovered"):
                        symptomatic.append(s)
                        ev = cmp.evidence[0]
                        delta[ev.state_key()] = ev.model_dump(mode="json")
        except ToolError as exc:
            flags.append("service_health_unavailable")
            ev = Evidence.make(
                EvidenceKind.DATA_QUALITY,
                f"service health check failed: {exc}",
                "telemetry:service_health",
                data={"issue": "unavailable"},
            )
            delta[ev.state_key()] = ev.model_dump(mode="json")
        candidates = sorted(set(named) | set(symptomatic))
        if not candidates and "service_health_unavailable" in flags:
            candidates = topo.services
        symptoms = [w for w in SYMPTOMS if w in lowered] or ["latency"]
        query = " ".join(dict.fromkeys(symptoms + candidates[:4]))[:300]
        scope = InvestigationScope(
            investigation_id=inv,
            dataset_id=dataset_id,
            request_text=req.text,
            reported_at=ds.reported_at,
            window=window,
            baseline=baseline,
            candidate_services=candidates,
            named_services=named,
            symptoms=symptoms,
            search_query=query,
            request_flags=flags,
        )
        delta.update(
            {
                "investigation_id": inv,
                "dataset_id": dataset_id,
                "scope": scope.model_dump(mode="json"),
            }
        )
        yield _event(
            self,
            ctx,
            delta,
            f"intake: window {window.label()}, candidate services "
            f"{', '.join(candidates) or 'none'}, flags {flags or 'none'}",
        )


def _load(state):
    parsed, errors = {}, []
    for key, model in (
        ("telemetry_finding", TelemetryFinding),
        ("topology_finding", TopologyFinding),
        ("knowledge_finding", KnowledgeFinding),
        ("incident_analysis", IncidentAnalysis),
        ("report_draft", ReportDraft),
        ("review", ReviewResult),
        ("verification", VerificationResult),
    ):
        obj, err = _parse(model, state.get(key))
        parsed[key] = obj
        if err:
            errors.append(f"{key}: {err}")
    return parsed, errors


def _suspicious(evidence: dict[str, Evidence]) -> set[str]:
    return {
        e.entity_id
        for e in evidence.values()
        if e.entity_id and "suspected_injection" in str(e.data.get("flags", ""))
    }


class EvidenceVerifierAgent(BaseAgent):
    """Primary, deterministic verification (see core/verification.py)."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if state.get("intake_error") or state.get("verification"):
            yield _event(self, ctx, {}, "verification skipped (rejected request or resume)")
            return
        rt = resolve(state)
        p, errors = _load(state)
        evidence = evidence_from_state(state)
        result = verify(
            evidence=evidence,
            telemetry=p["telemetry_finding"],
            topology=p["topology_finding"],
            knowledge=p["knowledge_finding"],
            analysis=p["incident_analysis"],
            draft=p["report_draft"],
            known_components=set(rt.topology.components),
            known_services=set(rt.topology.services),
            suspicious_docs=_suspicious(evidence),
            endpoints={c: v.get("endpoints", []) for c, v in rt.topology.components.items()},
        )
        if errors:
            from opspilot.contracts.verification import VerificationIssue

            result.issues += [
                VerificationIssue(severity="error", code="schema_violation", message=e)
                for e in errors
            ]
            result.verdict = "needs_review"
        yield _event(
            self,
            ctx,
            {"verification": result.model_dump(mode="json")},
            f"verification: {result.verdict}; {len(result.issues)} issue(s), "
            f"{len(result.invalid_citations)} invalid citation(s), "
            f"{len(result.policy_violations)} policy violation(s)",
        )


class ReinvestigationGateAgent(BaseAgent):
    """Last stage of each round: retry failed stages in another round, or end the loop."""

    max_rounds: int = 2

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        decision = plan_next_round(dict(ctx.session.state), self.max_rounds)
        text = decision.reason if decision.retry else f"re-investigation done: {decision.reason}"
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            branch=ctx.branch,
            actions=EventActions(state_delta=decision.state_delta, escalate=decision.stop),
            content=types.Content(role="model", parts=[types.Part(text=text)]),
        )


def run_metrics(ctx: InvocationContext, settings: Settings) -> RunMetrics | None:
    plugin = next(
        (p for p in getattr(ctx.plugin_manager, "plugins", []) if isinstance(p, BudgetPlugin)), None
    )
    if plugin is None:
        return None
    c = plugin.counters(ctx.invocation_id)
    rounds = rounds_info(ctx.session.state)
    return RunMetrics(
        provider=settings.provider,
        model=settings.model if settings.provider == "gemini" else "scripted-mock",
        llm_calls=c.llm_calls,
        tool_calls=c.tool_calls,
        duration_s=round(c.elapsed(), 3),
        prompt_tokens=c.prompt_tokens,
        completion_tokens=c.completion_tokens,
        total_tokens=c.total_tokens,
        tool_calls_by_agent={k: list(v) for k, v in c.tools_by_agent.items()},
        llm_calls_by_agent=dict(c.llm_by_agent),
        budget_events=list(c.events),
        model_time_ms={k: round(v, 1) for k, v in c.model_time_ms.items()},
        tool_time_ms={k: round(v, 1) for k, v in c.tool_time_ms.items()},
        investigation_rounds=rounds["round"],
        retried_stages=list(rounds["retried"]),
    )


class FinalizerAgent(BaseAgent):
    """Assemble the validated InvestigationReport; status/escalation by rule."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if state.get("intake_error"):
            yield _event(self, ctx, {}, "finalizer skipped: request rejected at intake")
            return
        rt = resolve(state)
        p, _ = _load(state)
        scope = InvestigationScope.model_validate(state["scope"])
        report = report_core.build_report(
            scope=scope,
            evidence=evidence_from_state(state),
            telemetry=p["telemetry_finding"],
            topology=p["topology_finding"],
            knowledge=p["knowledge_finding"],
            analysis=p["incident_analysis"],
            draft=p["report_draft"],
            verification=p["verification"],
            review=p["review"],
            topo=rt.topology,
            run_metrics=run_metrics(ctx, rt.settings),
            max_chars=rt.settings.limits.max_report_chars,
        )
        yield _event(
            self,
            ctx,
            {"final_report": report.model_dump(mode="json")},
            f"final report: status={report.status.value}, escalation="
            f"{report.escalation.level} {report.escalation.targets}",
        )
