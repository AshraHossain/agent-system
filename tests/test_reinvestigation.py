"""Optional bounded re-investigation loop (max_investigation_rounds=2)."""

from dataclasses import replace

import pytest
from google.adk.agents import LoopAgent

from opspilot.adk import mock_policies
from opspilot.adk import runner as R
from opspilot.adk.agents import LOOP_ORDER, PIPELINE_ORDER, build_root_agent
from opspilot.adk.models import ToolCalls
from opspilot.adk.rounds import ROUNDS_KEY, plan_next_round
from opspilot.adk.runtime import RunFaults
from opspilot.config import Limits
from opspilot.contracts.report import InvestigationStatus as S

SPECIALISTS = ("telemetry_analyst", "topology_analyst", "knowledge_researcher")
ANALYST_TIMEOUT = frozenset({"incident_analyst"})


def _limits(settings, **kw):
    return replace(settings, limits=replace(settings.limits, **kw))


def _two_rounds(settings, **kw):
    return _limits(settings, max_investigation_rounds=2, **kw)


def _hypotheses(report):
    return [(h.category, h.component_id) for h in report.root_cause_hypotheses]


# --- configuration and tree -------------------------------------------------


def test_single_round_tree_is_unchanged(settings):
    root = build_root_agent(settings)
    assert [a.name for a in root.sub_agents] == PIPELINE_ORDER
    assert not any(isinstance(a, LoopAgent) for a in root.sub_agents)


def test_two_round_tree_wraps_investigation_in_a_loop(settings):
    root = build_root_agent(_two_rounds(settings))
    assert [a.name for a in root.sub_agents] == [
        "intake",
        "investigation_rounds",
        "review_verifier",
        "finalizer",
    ]
    loop = root.sub_agents[1]
    assert isinstance(loop, LoopAgent) and loop.max_iterations == 2
    assert [a.name for a in loop.sub_agents] == LOOP_ORDER


@pytest.mark.parametrize("rounds", [0, 3])
def test_rounds_outside_one_or_two_are_rejected(rounds):
    with pytest.raises(ValueError, match="max_investigation_rounds"):
        Limits(max_investigation_rounds=rounds)


def test_rounds_from_env(monkeypatch):
    monkeypatch.setenv("OPSPILOT_MAX_INVESTIGATION_ROUNDS", "2")
    assert Limits.from_env().max_investigation_rounds == 2


def test_transient_faults_from_spec():
    f = RunFaults.from_spec({"model_timeout": ["incident_analyst"], "transient": True})
    assert f.transient and f.model_timeout == ANALYST_TIMEOUT
    assert not RunFaults.from_spec({}).transient


# --- end-to-end -----------------------------------------------------------------


async def test_transient_failure_recovers_in_second_round(run_case, settings):
    clean = (await run_case("C11", faults=RunFaults())).report
    out = await run_case(
        "C11",
        settings_override=_two_rounds(settings),
        faults=RunFaults(model_timeout=ANALYST_TIMEOUT, transient=True),
    )
    r, m = out.report, out.report.run_metrics
    assert r.status == clean.status == S.INVESTIGATED
    assert r.specialist_status["incident_analysis"] == "completed"
    assert _hypotheses(r) == _hypotheses(clean) and r.root_cause_hypotheses
    assert m.investigation_rounds == 2 and m.retried_stages == ["incident_analyst"]
    # Completed specialists skip themselves in round two: no extra model calls.
    for agent in SPECIALISTS:
        assert m.llm_calls_by_agent[agent] == clean.run_metrics.llm_calls_by_agent[agent]


async def test_persistent_failure_is_retried_once_then_reported(run_case, settings):
    out = await run_case("C11", settings_override=_two_rounds(settings))
    r, m = out.report, out.report.run_metrics
    assert r.status == S.INCONCLUSIVE and r.specialist_status["incident_analysis"] == "failed"
    assert m.investigation_rounds == 2 and m.retried_stages == ["incident_analyst"]
    assert m.llm_calls_by_agent["incident_analyst"] == 2  # one per round, no more
    assert "round limit (2) reached" in out.state[ROUNDS_KEY]["stop_reason"]


async def test_clean_run_takes_one_round_at_no_extra_cost(run_case, settings):
    single = (await run_case("C02")).report
    out = await run_case("C02", settings_override=_two_rounds(settings))
    m = out.report.run_metrics
    assert out.report.status == single.status
    assert m.investigation_rounds == 1 and m.retried_stages == []
    assert m.llm_calls_by_agent == single.run_metrics.llm_calls_by_agent
    assert [s.author for s in out.trace].count("reinvestigation_gate") == 1


async def test_failed_specialist_is_retried_alone(run_case, settings):
    clean = (await run_case("C03", faults=RunFaults())).report
    out = await run_case(
        "C03",
        settings_override=_two_rounds(settings),
        faults=RunFaults(model_quota=frozenset({"knowledge_researcher"}), transient=True),
    )
    r, m = out.report, out.report.run_metrics
    base = clean.run_metrics.llm_calls_by_agent
    assert r.specialist_status["knowledge"] == "completed" and r.status == clean.status
    assert m.retried_stages == ["knowledge_researcher"]
    assert m.llm_calls_by_agent["telemetry_analyst"] == base["telemetry_analyst"]
    assert m.llm_calls_by_agent["topology_analyst"] == base["topology_analyst"]
    # Downstream stages re-ran with the recovered findings.
    assert m.llm_calls_by_agent["incident_analyst"] == 2 * base["incident_analyst"]


async def test_budget_failure_is_not_retried(run_case, settings, monkeypatch):
    monkeypatch.setitem(
        mock_policies.POLICIES,
        "telemetry_analyst",
        lambda turn: ToolCalls([("summarize_anomalies", {})]),
    )
    out = await run_case("C02", settings_override=_two_rounds(settings, max_llm_calls_per_agent=4))
    m = out.report.run_metrics
    assert out.report.specialist_status["telemetry"] == "failed"
    assert m.investigation_rounds == 1 and m.llm_calls_by_agent["telemetry_analyst"] == 4
    assert "budget" in out.state[ROUNDS_KEY]["stop_reason"]


# --- round planning rules -----------------------------------------------------

DONE = {"status": "completed"}
FAILED = {"status": "failed", "errors": ["model error (TimeoutError): simulated"]}


def _state(**overrides):
    state = {
        "telemetry_finding": DONE,
        "topology_finding": DONE,
        "knowledge_finding": DONE,
        "incident_analysis": DONE,
        "report_draft": DONE,
        "verification": {"verdict": "pass"},
    }
    state.update(overrides)
    return state


def test_plan_stops_when_nothing_failed():
    d = plan_next_round(_state(), max_rounds=2)
    assert d.stop and d.reason == "no failed stages"
    assert set(d.state_delta) == {ROUNDS_KEY}


def test_plan_stops_on_rejected_request():
    d = plan_next_round(_state(intake_error="empty_request: request is empty"), 2)
    assert d.stop and "intake" in d.reason


def test_plan_retries_failed_specialist_and_clears_downstream():
    d = plan_next_round(_state(topology_finding=FAILED), max_rounds=2)
    assert d.retry == ["topology_finding"]
    assert d.state_delta == {
        "topology_finding": None,
        "incident_analysis": None,
        "report_draft": None,
        "verification": None,
        ROUNDS_KEY: {"round": 2, "retried": ["topology_analyst"], "stop_reason": ""},
    }


def test_plan_keeps_specialists_when_only_analysis_failed():
    d = plan_next_round(_state(incident_analysis=FAILED), max_rounds=2)
    assert d.retry == ["incident_analysis"]
    assert not {"telemetry_finding", "topology_finding", "knowledge_finding"} & set(d.state_delta)


def test_plan_treats_missing_stage_as_failed():
    d = plan_next_round(_state(knowledge_finding=None), max_rounds=2)
    assert d.retry == ["knowledge_finding"]


def test_plan_stops_at_round_limit():
    state = _state(incident_analysis=FAILED)
    state[ROUNDS_KEY] = {"round": 2, "retried": ["incident_analyst"], "stop_reason": ""}
    d = plan_next_round(state, max_rounds=2)
    assert d.stop and "round limit (2) reached; still failed: incident_analyst" in d.reason
    assert d.state_delta[ROUNDS_KEY]["retried"] == ["incident_analyst"]


def test_plan_does_not_retry_budget_failures():
    exhausted = {"status": "failed", "errors": ["iteration budget exhausted for telemetry_analyst"]}
    d = plan_next_round(_state(telemetry_finding=exhausted), max_rounds=2)
    assert d.stop and "budget" in d.reason


def test_resume_restarts_round_counter():
    state = {**_state(), ROUNDS_KEY: {"round": 2, "retried": ["x"], "stop_reason": "done"}}
    assert R.resume_invalidation(state)[ROUNDS_KEY] is None
