"""Run, resume and inspect investigations.

Session service: `DatabaseSessionService` on SQLite by default (durable on local
disk), `InMemorySessionService` when `persist=False` (tests). See
docs/session_management.md for guarantees.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field

from google.adk.agents import RunConfig
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types

from opspilot.adk.agents import TOOL_ALLOWLIST, build_root_agent
from opspilot.adk.plugins import BudgetPlugin
from opspilot.adk.rounds import ROUNDS_KEY, STAGE_GROUPS, rounds_info
from opspilot.adk.runtime import RunFaults, register, resolve, unregister
from opspilot.config import Settings
from opspilot.contracts.report import InvestigationReport, RunMetrics
from opspilot.contracts.request import InvestigationScope, TimeWindow
from opspilot.core import report as report_core
from opspilot.core.verification import evidence_from_state

APP_NAME = "opspilot"
USER_ID = "operator"


@dataclass
class TraceStep:
    author: str
    tool_calls: list[str]
    text: str


@dataclass
class Outcome:
    session_id: str
    report: InvestigationReport
    state: dict
    trace: list[TraceStep] = field(default_factory=list)
    aborted: str | None = None
    duration_s: float = 0.0


def session_service(settings: Settings, persist: bool = True) -> BaseSessionService:
    if not persist:
        return InMemorySessionService()
    from google.adk.sessions import DatabaseSessionService

    if settings.session_db_url.startswith("sqlite"):
        from pathlib import Path

        path = settings.session_db_url.split("///", 1)[-1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return DatabaseSessionService(db_url=settings.session_db_url)


def _fallback_report(state: dict, aborted: str, metrics: RunMetrics | None) -> InvestigationReport:
    """Deterministic report when the pipeline did not reach the finalizer."""
    from opspilot.adk.deterministic import _load

    rt = resolve(state)
    p, _ = _load(state)
    scope_raw = state.get("scope")
    if scope_raw:
        scope = InvestigationScope.model_validate(scope_raw)
    else:
        ds = rt.dataset
        scope = InvestigationScope(
            investigation_id=state.get("investigation_id", "unknown"),
            dataset_id=state.get("dataset_id", "unknown"),
            request_text="(request rejected or not processed)",
            reported_at=ds.reported_at,
            window=TimeWindow(start=ds.reported_at, end=ds.reported_at),
            baseline=TimeWindow(start=ds.reported_at, end=ds.reported_at),
            candidate_services=[],
            search_query="-",
        )
    return report_core.build_report(
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
        run_metrics=metrics,
        aborted=aborted,
        max_chars=rt.settings.limits.max_report_chars,
    )


def resume_invalidation(state: dict) -> dict:
    """State delta that clears failed/missing stages and everything downstream of them.

    Completed upstream stages are kept (their agents skip on resume); evidence
    records are content-addressed, so re-running a stage cannot duplicate them.
    The re-investigation round counter restarts, so a resumed run gets its own retry.
    """
    delta: dict = {"final_report": None}
    if state.get(ROUNDS_KEY) is not None:
        delta[ROUNDS_KEY] = None
    stale = False
    for group in STAGE_GROUPS:
        if stale:
            delta.update({k: None for k in group if state.get(k) is not None})
            continue
        bad = [
            k
            for k in group
            if state.get(k) is None
            or (isinstance(state[k], dict) and state[k].get("status") == "failed")
        ]
        if bad:
            delta.update({k: None for k in bad if state.get(k) is not None})
            stale = True
    return delta


async def run_investigation(
    request: str,
    dataset_id: str,
    *,
    settings: Settings | None = None,
    service: BaseSessionService | None = None,
    session_id: str | None = None,
    faults: RunFaults | None = None,
    persist: bool = True,
) -> Outcome:
    """Run (or resume, when `session_id` exists) one investigation end to end."""
    settings = settings or Settings.from_env()
    service = service or session_service(settings, persist)
    faults = faults or RunFaults()
    session = None
    if session_id:
        session = await service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
    if session is None:
        inv = f"inv-{uuid.uuid4().hex[:12]}"
        session = await service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
            state={"investigation_id": inv, "dataset_id": dataset_id},
        )
    elif session.state.get("final_report") is not None or session.state.get("scope"):
        await service.append_event(
            session,
            Event(
                author="runner",
                invocation_id=f"resume-{uuid.uuid4().hex[:8]}",
                actions=EventActions(state_delta=resume_invalidation(dict(session.state))),
            ),
        )
    inv = session.state["investigation_id"]
    register(inv, settings, session.state.get("dataset_id", dataset_id), faults)
    plugin = BudgetPlugin(settings.limits, TOOL_ALLOWLIST)
    runner = Runner(
        app_name=APP_NAME,
        agent=build_root_agent(settings, faults),
        session_service=service,
        plugins=[plugin],
    )
    trace: list[TraceStep] = []
    aborted = None
    started = time.monotonic()
    message = types.Content(role="user", parts=[types.Part(text=request)])
    try:
        async with asyncio.timeout(settings.limits.max_duration_s):
            async for ev in runner.run_async(
                user_id=USER_ID,
                session_id=session.id,
                new_message=message,
                run_config=RunConfig(max_llm_calls=settings.limits.max_llm_calls + 5),
            ):
                text = ""
                if ev.content and ev.content.parts:
                    text = " ".join(p.text for p in ev.content.parts if p.text)[:300]
                calls = [f.name for f in ev.get_function_calls()]
                if calls or text:
                    trace.append(TraceStep(ev.author, calls, text))
    except TimeoutError:
        aborted = f"duration limit of {settings.limits.max_duration_s:.0f}s exceeded"
    except LlmCallsLimitExceededError:
        aborted = "model request limit exceeded"
    except Exception as exc:
        aborted = f"unexpected error: {type(exc).__name__}: {str(exc)[:200]}"
    session = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)
    state = dict(session.state)
    raw = state.get("final_report")
    if aborted or raw is None:
        reason = aborted or state.get("intake_error") or "pipeline ended before the finalizer"
        c = plugin.runs.get(next(iter(plugin.runs), ""), None)
        metrics = None
        if c is not None:
            rounds = rounds_info(state)
            metrics = RunMetrics(
                provider=settings.provider,
                model=settings.model,
                llm_calls=c.llm_calls,
                tool_calls=c.tool_calls,
                duration_s=round(c.elapsed(), 3),
                budget_events=c.events,
                investigation_rounds=rounds["round"],
                retried_stages=list(rounds["retried"]),
            )
        report = _fallback_report(state, reason, metrics)
        with contextlib.suppress(Exception):
            await service.append_event(
                session,
                Event(
                    author="runner",
                    invocation_id=f"fallback-{uuid.uuid4().hex[:8]}",
                    actions=EventActions(
                        state_delta={"final_report": report.model_dump(mode="json")}
                    ),
                ),
            )
        state["final_report"] = report.model_dump(mode="json")
    else:
        report = InvestigationReport.model_validate(raw)
    unregister(inv)
    return Outcome(
        session_id=session.id,
        report=report,
        state=state,
        trace=trace,
        aborted=aborted,
        duration_s=round(time.monotonic() - started, 3),
    )


async def list_investigations(settings: Settings | None = None) -> list[dict]:
    settings = settings or Settings.from_env()
    service = session_service(settings)
    resp = await service.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    out = []
    for s in resp.sessions:
        full = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=s.id)
        fr = (full.state or {}).get("final_report") or {}
        out.append(
            {
                "session_id": s.id,
                "dataset_id": full.state.get("dataset_id"),
                "status": fr.get("status", "incomplete"),
                "updated": s.last_update_time,
            }
        )
    return out


async def load_investigation(session_id: str, settings: Settings | None = None) -> dict | None:
    settings = settings or Settings.from_env()
    service = session_service(settings)
    s = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    return None if s is None else {"state": dict(s.state), "events": len(s.events)}
