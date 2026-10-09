"""Phase 8: orchestration, parallel execution, verification and end-to-end runs."""

import asyncio
from itertools import pairwise

import pytest

from opspilot.adk.agents import TOOL_ALLOWLIST, build_root_agent
from opspilot.adk.models import ScriptedLlm
from opspilot.contracts.report import InvestigationReport
from opspilot.core.policy import check_step
from opspilot.datasets.spec import case_ids, get_case

SPECIALISTS = ("telemetry_analyst", "topology_analyst", "knowledge_researcher")
ORDER = [
    "intake",
    *SPECIALISTS,
    "incident_analyst",
    "report_drafter",
    "evidence_verifier",
    "review_verifier",
    "finalizer",
]


def test_tree_structure(settings):
    root = build_root_agent(settings)
    assert [a.name for a in root.sub_agents] == [
        "intake",
        "specialists",
        "incident_analyst",
        "report_drafter",
        "evidence_verifier",
        "review_verifier",
        "finalizer",
    ]
    assert type(root.sub_agents[1]).__name__ == "ParallelAgent"
    assert [a.name for a in root.sub_agents[1].sub_agents] == list(SPECIALISTS)
    for a in root.sub_agents[1].sub_agents + root.sub_agents[2:4]:
        assert a.sub_agents == []  # leaves: no delegation possible
        assert a.disallow_transfer_to_parent and a.disallow_transfer_to_peers


async def test_stage_order_and_single_execution(run_case):
    out = await run_case("C03")
    first = {}
    for i, step in enumerate(out.trace):
        first.setdefault(step.author, i)
    assert set(ORDER) <= set(first)
    assert first["intake"] < min(first[s] for s in SPECIALISTS)
    assert max(first[s] for s in SPECIALISTS) < first["incident_analyst"]
    for a, b in pairwise(ORDER[4:]):
        assert first[a] < first[b]
    m = out.report.run_metrics
    assert set(m.llm_calls_by_agent) == set(TOOL_ALLOWLIST)  # every LLM agent ran
    assert all(n <= 4 for n in m.llm_calls_by_agent.values())  # no runaway loops
    for agent, tools in m.tool_calls_by_agent.items():
        assert set(tools) <= TOOL_ALLOWLIST[agent]


async def test_specialists_run_concurrently(run_case, monkeypatch):
    """Each specialist blocks on a 3-party barrier; sequential execution would time out."""
    barrier, arrived = asyncio.Barrier(3), set()
    original = ScriptedLlm.generate_content_async

    async def patched(self, llm_request, stream=False):
        if self.agent in SPECIALISTS and self.agent not in arrived:
            arrived.add(self.agent)
            await asyncio.wait_for(barrier.wait(), timeout=5)
        async for r in original(self, llm_request, stream):
            yield r

    monkeypatch.setattr(ScriptedLlm, "generate_content_async", patched)
    out = await run_case("C02")
    assert arrived == set(SPECIALISTS)
    assert all(
        out.report.specialist_status[k] == "completed"
        for k in ("telemetry", "topology", "knowledge")
    )


@pytest.mark.parametrize("case_id", case_ids())
async def test_end_to_end_matches_labels(run_case, case_id):
    case = get_case(case_id)
    labels = case["labels"]
    out = await run_case(case_id)
    r = InvestigationReport.model_validate(out.report.model_dump())
    assert r.status.value == labels["status"]
    assert r.escalation.level == labels["escalation"]
    for t in labels.get("escalation_targets", []):
        assert t in r.escalation.targets
    if labels.get("score_root_cause", True) and labels["root_causes"]:
        expected = {(c["category"], c["component"]) for c in labels["root_causes"]}
        ranked = [(h.category.value, h.component_id) for h in r.root_cause_hypotheses]
        assert ranked[0] in expected  # top-1 is a true cause
        assert expected <= set(ranked[:3])  # every true cause is in the top 3
    assert set(r.affected_services) == set(labels["affected_services"])
    assert r.verification is not None and r.verification.invalid_citations == []
    assert all(check_step(s.step).allowed for s in r.recommended_diagnostic_steps)
    for rb in labels.get("relevant_runbooks", []):
        if r.status.value != "inconclusive":
            assert rb in {d.doc_id for d in r.relevant_runbooks}
    for bad in labels.get("irrelevant_runbooks", []) + labels.get("outdated_documents", []):
        assert bad not in {d.doc_id for d in r.relevant_runbooks}
    for e in r.supporting_evidence:
        assert f"evidence:{e.evidence_id}" in out.state
