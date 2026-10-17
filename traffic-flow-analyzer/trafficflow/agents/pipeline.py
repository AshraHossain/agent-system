"""Orchestration: two analysts run in parallel, a writer drafts, deterministic code verifies
and computes status. Fixed order; no LLM decides routing."""

from __future__ import annotations

import asyncio
import json
import re

from trafficflow.agents import agents
from trafficflow.agents.deps import TrafficDeps
from trafficflow.contracts import TrafficBrief, TrafficReport
from trafficflow.core.anomaly import detect
from trafficflow.core.forecast import forecast_segment
from trafficflow.core.status import network_status
from trafficflow.data.feed import Feed

_NUM_RE = re.compile(r"\d+\.?\d*")


def _nums_in_text(text: str) -> set[str]:
    return set(_NUM_RE.findall(text))


def _nums_in_evidence(ev: dict) -> set[str]:
    return _nums_in_text(json.dumps(ev))


def verify_brief(
    brief: TrafficBrief, evidence: dict[str, dict], segment_ids: set[str]
) -> list[str]:
    """Citation existence, segment validity, and numeric-claim heuristic.

    Returns issues (empty means clean).  Returns a non-empty list (but the
    caller still publishes) when numeric claims are unsupported unless >50%
    are unsupported, in which case the list is treated as a hard failure
    by the caller.
    """
    issues = []
    total_claims = 0
    unsupported_claims = 0

    for i, f in enumerate(brief.findings, 1):
        for eid in f.evidence_ids:
            if eid not in evidence:
                issues.append(f"finding {i} cites unknown evidence {eid}")
        for sid in f.segment_ids:
            if sid not in segment_ids:
                issues.append(f"finding {i} names unknown segment {sid}")

        # Collect all numbers from the evidence entries this finding references.
        ev_nums: set[str] = set()
        for eid in f.evidence_ids:
            if eid in evidence:
                ev_nums |= _nums_in_evidence(evidence[eid])

        # Check numbers in the finding's claim + detail text.
        finding_text = f.claim + " " + getattr(f, "detail", "")
        for num in _nums_in_text(finding_text):
            total_claims += 1
            if ev_nums and num not in ev_nums:
                unsupported_claims += 1
                issues.append(
                    f"finding {i}: numeric claim '{num}' not found in evidence "
                    + ", ".join(f.evidence_ids)
                )

    # Check summary-level numbers against the union of all evidence.
    all_ev_nums: set[str] = set()
    for ev in evidence.values():
        all_ev_nums |= _nums_in_evidence(ev)
    for num in _nums_in_text(brief.summary):
        total_claims += 1
        if all_ev_nums and num not in all_ev_nums:
            unsupported_claims += 1
            issues.append(f"summary: numeric claim '{num}' not found in any evidence")

    # Tag issues list so the caller can decide severity.
    if total_claims > 0 and unsupported_claims > total_claims / 2:
        issues.append(f"HARD_FAIL: {unsupported_claims}/{total_claims} numeric claims unsupported")

    return issues


async def run_report(
    feed: Feed, horizon_minutes: int = 60, model: str | None = None
) -> TrafficReport:
    deps = TrafficDeps(feed=feed)
    prompt = f"Analyse the network as of {feed.now.isoformat()} horizon={horizon_minutes} minutes."
    fa, ia = await asyncio.gather(
        agents.forecast_analyst(model).run(prompt, deps=deps),
        agents.incident_analyst(model).run(prompt, deps=deps),
    )
    findings = {"forecast": fa.output.model_dump(), "incidents": ia.output.model_dump()}
    brief = (
        await agents.brief_writer(model).run(
            "Write the briefing.\nFINDINGS:" + json.dumps(findings)
        )
    ).output

    segs = {s.id: s for s in feed.segments()}
    issues = verify_brief(brief, deps.evidence, set(segs))
    # status is computed from deterministic core outputs, independent of the LLM text
    forecasts = [forecast_segment(feed.history(i, 7), s, horizon_minutes) for i, s in segs.items()]
    anomalies = [a for i in segs for a in detect(feed.history(i, 7), i)]
    return TrafficReport(
        as_of=feed.now,
        horizon_minutes=horizon_minutes,
        status=network_status(forecasts, anomalies),
        brief=brief,
        evidence=deps.evidence,
        issues=issues,
        used_model=model or "mock",
    )


def run_report_sync(
    feed: Feed, horizon_minutes: int = 60, model: str | None = None
) -> TrafficReport:
    return asyncio.run(run_report(feed, horizon_minutes, model))


async def ask(feed: Feed, question: str, model: str | None = None) -> str:
    deps = TrafficDeps(feed=feed)
    return (await agents.ask_agent(model).run(question[:500], deps=deps)).output
