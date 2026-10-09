"""Phase 9: failure handling and bounded execution."""

import asyncio
from dataclasses import replace

from opspilot.adk import mock_policies
from opspilot.adk.models import ScriptedLlm, ToolCalls
from opspilot.adk.runtime import RunFaults
from opspilot.config import Limits
from opspilot.contracts.report import InvestigationStatus as S


def _limits(settings, **kw):
    return replace(settings, limits=replace(settings.limits, **kw))


async def test_model_timeout_degrades_one_stage(run_case):
    out = await run_case("C11")
    r = out.report
    assert r.status == S.INCONCLUSIVE and r.specialist_status["incident_analysis"] == "failed"
    assert r.root_cause_hypotheses == [] and r.affected_services == ["checkout", "video-stream"]
    assert any("incident analysis stage failed" in m for m in r.missing_information)


async def test_quota_error_is_not_retried_forever(run_case):
    out = await run_case("C03", faults=RunFaults(model_quota=frozenset({"knowledge_researcher"})))
    r = out.report
    assert r.specialist_status["knowledge"] == "failed" and r.status == S.INCONCLUSIVE
    assert r.run_metrics.llm_calls_by_agent["knowledge_researcher"] == 1


async def test_all_specialists_failing_fails_the_investigation(run_case):
    f = RunFaults(
        model_timeout=frozenset({"telemetry_analyst", "topology_analyst", "knowledge_researcher"})
    )
    out = await run_case("C02", faults=f)
    assert out.report.status == S.FAILED
    assert out.report.escalation.level == "escalate"


async def test_tool_failure_reported_explicitly(run_case):
    out = await run_case("C12")
    r = out.report
    assert r.status == S.INCONCLUSIVE and r.specialist_status["telemetry"] == "failed"
    assert any("data_unavailable" in m for m in r.missing_information)


async def test_tool_budget(run_case, settings):
    out = await run_case("C02", settings_override=_limits(settings, max_tool_calls=3))
    m = out.report.run_metrics
    assert m.tool_calls == 3 and any("tool budget" in e for e in m.budget_events)
    assert out.report.status in (S.INCONCLUSIVE, S.FAILED, S.REQUIRES_HUMAN_REVIEW)


async def test_runaway_agent_loop_is_capped(run_case, settings, monkeypatch):
    monkeypatch.setitem(
        mock_policies.POLICIES,
        "telemetry_analyst",
        lambda turn: ToolCalls([("summarize_anomalies", {})]),
    )
    out = await run_case("C02", settings_override=_limits(settings, max_llm_calls_per_agent=4))
    m = out.report.run_metrics
    assert m.llm_calls_by_agent["telemetry_analyst"] == 4
    assert any("iteration budget exhausted for telemetry_analyst" in e for e in m.budget_events)
    assert out.report.specialist_status["telemetry"] == "failed"


async def test_global_model_budget(run_case, settings):
    out = await run_case("C02", settings_override=_limits(settings, max_llm_calls=5))
    m = out.report.run_metrics
    assert m.llm_calls == 5 and any("model request budget" in e for e in m.budget_events)


async def test_duration_limit_aborts_and_still_reports(run_case, settings, monkeypatch):
    original = ScriptedLlm.generate_content_async

    async def slow(self, llm_request, stream=False):
        await asyncio.sleep(0.4)
        async for r in original(self, llm_request, stream):
            yield r

    monkeypatch.setattr(ScriptedLlm, "generate_content_async", slow)
    out = await run_case("C02", settings_override=_limits(settings, max_duration_s=1.0))
    assert out.aborted and "duration limit" in out.aborted
    assert out.report.status == S.FAILED
    assert out.state["final_report"]["status"] == "failed"


async def test_invalid_request_yields_failed_report(run_case):
    out = await run_case("C02", request="   ")
    assert out.report.status == S.FAILED
    assert "request is empty" in out.report.confidence_rationale


def test_limits_from_env(monkeypatch):
    monkeypatch.setenv("OPSPILOT_MAX_TOOL_CALLS", "7")
    monkeypatch.setenv("OPSPILOT_MAX_DURATION_S", "9.5")
    lim = Limits.from_env()
    assert lim.max_tool_calls == 7 and lim.max_duration_s == 9.5
