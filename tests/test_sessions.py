"""Phase 9: durable sessions (DatabaseSessionService/SQLite), inspection and resume."""

import json
from dataclasses import replace

from opspilot.adk import runner as R
from opspilot.adk.runtime import RunFaults
from opspilot.datasets.spec import get_case


def _settings(settings, tmp_path):
    return replace(settings, session_db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")


async def test_session_persists_to_sqlite_and_can_be_inspected(settings, tmp_path):
    s = _settings(settings, tmp_path)
    out = await R.run_investigation(get_case("C03")["request"], "C03", settings=s)
    # A brand-new service instance (fresh engine) reads it back from disk.
    loaded = await R.load_investigation(out.session_id, settings=s)
    assert loaded["state"]["final_report"]["status"] == "investigated"
    assert loaded["events"] > 10
    assert any(k.startswith("evidence:") for k in loaded["state"])
    listing = await R.list_investigations(settings=s)
    assert [i["session_id"] for i in listing] == [out.session_id]


async def test_state_holds_references_not_datasets(run_case):
    out = await run_case("C05")
    size = len(json.dumps(out.state, default=str))
    assert size < 250_000, size
    evidence = [k for k in out.state if k.startswith("evidence:")]
    assert 0 < len(evidence) <= 200
    assert "telemetry" not in out.state  # no raw series stored


async def test_resume_of_completed_investigation_skips_all_model_work(settings, tmp_path):
    s = _settings(settings, tmp_path)
    req = get_case("C02")["request"]
    first = await R.run_investigation(req, "C02", settings=s)
    again = await R.run_investigation(req, "C02", settings=s, session_id=first.session_id)
    m = again.report.run_metrics
    assert m.llm_calls == 0 and m.tool_calls == 0
    assert again.report.status == first.report.status
    assert again.report.root_cause_hypotheses == first.report.root_cause_hypotheses


async def test_resume_reruns_failed_stage_and_downstream_only(settings, tmp_path):
    s = _settings(settings, tmp_path)
    req = get_case("C02")["request"]
    failed = await R.run_investigation(
        req, "C02", settings=s, faults=RunFaults(model_timeout=frozenset({"incident_analyst"}))
    )
    assert failed.report.status.value == "inconclusive"
    resumed = await R.run_investigation(req, "C02", settings=s, session_id=failed.session_id)
    m = resumed.report.run_metrics
    assert resumed.report.status.value == "investigated"
    assert set(m.llm_calls_by_agent) == {"incident_analyst", "report_drafter", "review_verifier"}
    assert "telemetry_analyst" not in m.tool_calls_by_agent


def test_resume_invalidation_rules():
    state = {
        "telemetry_finding": {"status": "completed"},
        "topology_finding": {"status": "failed"},
        "knowledge_finding": {"status": "completed"},
        "incident_analysis": {"status": "completed"},
        "report_draft": {},
        "verification": {},
        "review": {},
        "final_report": {},
    }
    delta = R.resume_invalidation(state)
    assert delta == {
        "final_report": None,
        "topology_finding": None,
        "incident_analysis": None,
        "report_draft": None,
        "verification": None,
        "review": None,
    }
