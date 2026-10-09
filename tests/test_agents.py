"""Phase 7: agent contract tests — each specialist alone after intake, real ADK Runner."""

import pytest
from adk_helpers import run_tree
from google.adk.agents import SequentialAgent

from opspilot.adk import agents as A
from opspilot.adk.deterministic import IntakeAgent
from opspilot.contracts.findings import (
    IncidentAnalysis,
    KnowledgeFinding,
    ReportDraft,
    TelemetryFinding,
    TopologyFinding,
)
from opspilot.core.policy import check_step


async def _run(settings, case, *builders, text="Several services are slow with packet loss"):
    root = SequentialAgent(
        name="t", sub_agents=[IntakeAgent(name="intake"), *(b(settings) for b in builders)]
    )
    return await run_tree(root, settings, case, text=text)


def _cited_exist(state, ids):
    return all(f"evidence:{i}" in state for i in ids)


async def test_topology_contract(settings):
    state, _, plugin = await _run(settings, "C04", A.topology_analyst)
    f = TopologyFinding.model_validate(state["topology_finding"])
    assert f.status == "completed"
    assert set(f.services_examined) == {"catalog", "checkout", "search", "video-stream"}
    assert {"lb-1", "fw-1"} <= {s.component_id for s in f.shared_dependencies}
    fw = next(b for b in f.blast_radius if b.component_id == "fw-1")
    assert {"service": "checkout", "impact": "critical", "exposure": "single_point"} in [
        i.model_dump() for i in fw.impacts
    ]
    assert f.propagation_paths and _cited_exist(state, f.evidence_ids)
    tools = next(iter(plugin.runs.values())).tools_by_agent["topology_analyst"]
    assert set(tools) <= A.TOOL_ALLOWLIST["topology_analyst"]


async def test_topology_reports_incomplete_topology(settings):
    state, _, _ = await _run(settings, "C14", A.topology_analyst)
    f = TopologyFinding.model_validate(state["topology_finding"])
    assert any("video-stream does not depend on" in u for u in f.uncertainties)


async def test_knowledge_contract_flags_outdated_and_irrelevant(settings):
    state, _, _ = await _run(settings, "C03", A.knowledge_researcher)
    f = KnowledgeFinding.model_validate(state["knowledge_finding"])
    assert f.status == "completed"
    assert "RB-002" in {r.doc_id for r in f.relevant_runbooks}
    assert "RB-003" in {d.doc_id for d in f.outdated_or_conflicting}
    assert "RB-003" not in {r.doc_id for r in f.relevant_runbooks}
    assert _cited_exist(state, f.evidence_ids)
    assert all(state[f"evidence:{i}"]["trusted"] is False for i in f.evidence_ids)


async def test_knowledge_quarantines_malicious_document(settings):
    state, _, _ = await _run(
        settings, "C10", A.knowledge_researcher, text="Packet loss on leaf-1 uplink, checkout slow"
    )
    f = KnowledgeFinding.model_validate(state["knowledge_finding"])
    assert [d.doc_id for d in f.suspicious_documents] == ["DOC-666"]
    dumped = str(state["knowledge_finding"])
    assert "shutdown" not in dumped and "AIza" not in dumped


async def test_knowledge_excludes_irrelevant_runbooks(settings):
    case_text = "Payments and notifications are slow with packet loss towards external providers"
    state, _, _ = await _run(settings, "C09", A.knowledge_researcher, text=case_text)
    f = KnowledgeFinding.model_validate(state["knowledge_finding"])
    relevant = {r.doc_id for r in f.relevant_runbooks}
    assert "RB-006" in relevant and not relevant & {"RB-090", "RB-007", "RB-008"}


@pytest.mark.parametrize(
    "case,expected", [("C02", "link_congestion"), ("C07", "telemetry_artifact")]
)
async def test_incident_analyst_contract(settings, case, expected):
    state, _, plugin = await _run(settings, case, A.telemetry_analyst, A.incident_analyst)
    f = IncidentAnalysis.model_validate(state["incident_analysis"])
    assert f.status == "completed" and f.hypotheses[0].category.value == expected
    for h in f.hypotheses:
        assert _cited_exist(state, h.supporting_evidence_ids + h.contradicting_evidence_ids)
        assert all(check_step(c).allowed for c in h.diagnostic_checks)


async def test_incident_analyst_checks_misleading_history(settings):
    state, _, plugin = await _run(settings, "C08", A.incident_analyst)
    f = IncidentAnalysis.model_validate(state["incident_analysis"])
    assert any("INC-2025-0412 compared" in a for a in f.hypotheses[0].alternative_explanations)
    assert (
        "compare_with_incident"
        in next(iter(plugin.runs.values())).tools_by_agent["incident_analyst"]
    )


async def test_report_drafter_contract(settings):
    state, _, _ = await _run(
        settings,
        "C03",
        A.telemetry_analyst,
        A.knowledge_researcher,
        A.incident_analyst,
        A.report_drafter,
    )
    d = ReportDraft.model_validate(state["report_draft"])
    assert d.affected_components[0] == "lnk-l3-s2"
    assert all(check_step(s.step).allowed for s in d.recommended_steps)
    assert all(_cited_exist(state, k.evidence_ids) for k in d.key_facts)
    assert "RB-002" in {rb for s in d.recommended_steps for rb in s.runbook_ids}


async def test_telemetry_reports_failure_explicitly(settings):
    from opspilot.adk.runtime import RunFaults

    root = SequentialAgent(
        name="t", sub_agents=[IntakeAgent(name="intake"), A.telemetry_analyst(settings)]
    )
    state, _, _ = await run_tree(
        root, settings, "C12", text="slow", faults=RunFaults(telemetry_unavailable=True)
    )
    f = TelemetryFinding.model_validate(state["telemetry_finding"])
    assert f.status == "failed" and "data_unavailable" in f.errors[0]
    assert "service_health_unavailable" in state["scope"]["request_flags"]
