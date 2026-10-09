"""Phase 6: the smallest working agent — intake + telemetry analyst through ADK's Runner."""

from adk_helpers import run_tree
from google.adk.agents import SequentialAgent

from opspilot.adk import models
from opspilot.adk.agents import telemetry_analyst
from opspilot.adk.deterministic import IntakeAgent
from opspilot.contracts.findings import TelemetryFinding
from opspilot.contracts.request import InvestigationScope


async def test_intake_and_telemetry_agent(settings):
    root = SequentialAgent(
        name="mini", sub_agents=[IntakeAgent(name="intake"), telemetry_analyst(settings)]
    )
    state, _events, plugin = await run_tree(
        root, settings, "C02", text="Checkout is slow. password=hunter2"
    )
    scope = InvestigationScope.model_validate(state["scope"])
    assert scope.candidate_services == ["checkout", "video-stream"]
    assert "hunter2" not in scope.request_text and "secret_redacted" in scope.request_flags
    finding = TelemetryFinding.model_validate(state["telemetry_finding"])
    assert finding.status == "completed" and finding.affected_entities == ["lnk-l1-s1"]
    for a in finding.anomalies:
        for eid in a.evidence_ids:
            assert f"evidence:{eid}" in state
    c = next(iter(plugin.runs.values()))
    assert c.tools_by_agent["telemetry_analyst"] == ["summarize_anomalies"]
    assert c.llm_by_agent["telemetry_analyst"] == 2


async def test_raw_request_never_reaches_the_model(settings, monkeypatch):
    seen = []
    original = models.parse_turn

    def spy(agent, req):
        seen.extend(p.text for c in req.contents for p in c.parts if p.text)
        seen.append(str(req.config.system_instruction))
        return original(agent, req)

    monkeypatch.setattr(models, "parse_turn", spy)
    root = SequentialAgent(
        name="mini", sub_agents=[IntakeAgent(name="intake"), telemetry_analyst(settings)]
    )
    await run_tree(root, settings, "C02", text="slow checkout api_key=AIzaSyA1234567890abcdefghijk")
    assert seen and not any("AIzaSy" in s for s in seen)


async def test_invalid_request_ends_invocation(settings):
    root = SequentialAgent(
        name="mini", sub_agents=[IntakeAgent(name="intake"), telemetry_analyst(settings)]
    )
    state, _, plugin = await run_tree(root, settings, "C02", text="   ")
    assert "intake_error" in state and "scope" not in state
    assert state["telemetry_finding"]["status"] == "failed"
    c = next(iter(plugin.runs.values()), None)
    assert c is None or (c.tool_calls == 0 and c.llm_calls == 0)
