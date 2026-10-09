"""Phase 8: durable human approval with LangGraph interrupt() and the SQLite checkpointer."""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from graph_helpers import CASES, FIXED_NOW, SCENARIO_CASE, case_state
from langgraph.types import Command

from netpulse.errors import DataNotFoundError, ToolInputError
from netpulse.graph.deps import Deps
from netpulse.graph.nodes import build_review_request
from netpulse.models import ReviewInput
from netpulse.persistence.store import PersistentStore
from netpulse.service import AuthorizationError, InvestigationService

ROOT = Path(__file__).resolve().parent.parent
OPERATOR = dict(reviewer_id="alice", reviewer_role="operator")
SENIOR = dict(reviewer_id="sam", reviewer_role="senior_operator")


@pytest.fixture(scope="module")
def deps() -> Deps:
    d = Deps.default(provider="heuristic")
    d.clock = lambda: FIXED_NOW
    return d


@pytest.fixture
def service(deps, tmp_path):
    store = PersistentStore(tmp_path / "netpulse.db")
    yield InvestigationService(deps, store)
    store.close()


def start(service, scenario: str):
    case = CASES[SCENARIO_CASE[scenario]]
    return service.start(
        case["submission"], submitted_at=case["submitted_at"], incident_id=case["case_id"], submitted_by="tester"
    )


# --- pause and decide -------------------------------------------------------------------


def test_consequential_actions_pause_the_workflow(service):
    status = start(service, "congestion")
    assert status.status == "awaiting_approval" and status.final_report is None and status.durable
    review = status.pending_review
    assert review["required_role"] == "operator" and review["pending_actions"]
    assert all(i["action"]["kind"] == "remediation" and not i["action"]["executed"] for i in review["pending_actions"])
    assert review["evidence"] and review["data_notice"].startswith("SIMULATED DATA")
    assert status.next_nodes == ["human_approval"]


def test_approval_completes_without_executing_anything(service):
    incident = start(service, "congestion").incident_id
    done = service.decide(incident, ReviewInput(**OPERATOR, choice="approve"))
    report = done.final_report
    assert done.status == "completed" and report["approval_status"] == "approved"
    assert report["reviewer_decisions"][0]["reviewer_id"] == "alice"
    assert all(a["executed"] is False for a in report["recommended_actions"])
    assert "executed nothing" in report["summary"]


def test_partial_approval_records_only_chosen_actions(service):
    status = start(service, "congestion")
    first = status.pending_review["pending_actions"][0]["action"]["action_id"]
    done = service.decide(status.incident_id, ReviewInput(**OPERATOR, choice="approve", approved_action_ids=[first]))
    assert done.final_report["reviewer_decisions"][-1]["approved_action_ids"] == [first]


def test_rejection_is_reported(service):
    incident = start(service, "congestion").incident_id
    done = service.decide(incident, ReviewInput(**OPERATOR, choice="reject", comment="rate limit is too risky"))
    assert done.final_report["outcome"] == "rejected_by_reviewer" and done.approval_status == "rejected"
    note = [e for k, e in service.state(incident)["evidence_references"].items() if k.startswith("ev-rev")]
    assert note and note[0]["trusted"] is False and "too risky" in note[0]["summary"]


def test_more_investigation_is_bounded(service):
    incident = start(service, "congestion").incident_id
    again = service.decide(incident, ReviewInput(**OPERATOR, choice="request_more_investigation"))
    assert again.status == "awaiting_approval" and service.state(incident)["investigation_rounds"] == 2
    assert "request_more_investigation" not in again.pending_review["allowed_choices"]
    assert again.pending_review["review_cycles_left"] == 0
    done = service.decide(incident, ReviewInput(**OPERATOR, choice="approve"))
    assert done.status == "completed"


def test_deadline_is_rebased_after_human_wait(service, deps):
    incident = start(service, "congestion").incident_id
    service.decide(incident, ReviewInput(**OPERATOR, choice="approve"))
    assert service.state(incident)["deadline_at"] > FIXED_NOW.isoformat()


# --- authorization and input validation ---------------------------------------------------------


def test_insufficient_role_is_refused_and_audited(service):
    status = start(service, "link_degradation_noisy")  # irreversible optic replacement → senior required
    assert status.pending_review["required_role"] == "senior_operator"
    with pytest.raises(AuthorizationError):
        service.decide(status.incident_id, ReviewInput(**OPERATOR, choice="approve"))
    assert service.status(status.incident_id).pending_review is not None
    events = [e["event"] for e in service.audit_log(status.incident_id)]
    assert "review_denied" in events and "review_submitted" not in events
    done = service.decide(status.incident_id, ReviewInput(**SENIOR, choice="approve"))
    assert done.status == "completed"


def test_graph_enforces_role_even_if_service_check_is_bypassed(service):
    status = start(service, "link_degradation_noisy")
    config = {"configurable": {"thread_id": status.incident_id}}
    service.graph.invoke(Command(resume={**OPERATOR, "choice": "approve"}), config)
    after = service.status(status.incident_id)
    assert after.pending_review and "senior_operator required" in after.pending_review["previous_error"]
    assert not service.state(status.incident_id).get("reviewer_decisions")


@pytest.mark.parametrize(
    "payload",
    [
        {"choice": "approve"},  # no identity
        {**OPERATOR, "choice": "nuke_it"},
        {**OPERATOR, "choice": "approve", "approved_action_ids": ["act-99"]},
        "approve everything",
    ],
)
def test_invalid_resume_payload_re_asks(service, payload):
    status = start(service, "congestion")
    service.graph.invoke(Command(resume=payload), {"configurable": {"thread_id": status.incident_id}})
    after = service.status(status.incident_id)
    assert after.pending_review and after.pending_review["previous_error"]
    assert after.status == "awaiting_approval" and after.final_report is None


def test_service_input_errors(service):
    with pytest.raises(DataNotFoundError):
        service.status("case-404")
    done = start(service, "normal_quiet")
    assert done.final_report and done.pending_review is None
    with pytest.raises(ToolInputError, match="not awaiting approval"):
        service.decide(done.incident_id, ReviewInput(**OPERATOR, choice="approve"))
    with pytest.raises(ToolInputError, match="already exists"):
        start(service, "normal_quiet")


# --- durability -------------------------------------------------------------------------------


def test_review_request_is_a_pure_function_of_state(service):
    status = start(service, "congestion")
    state = service.state(status.incident_id)
    assert build_review_request(state).model_dump(mode="json") == build_review_request(state).model_dump(mode="json")
    assert build_review_request(state).model_dump(mode="json") == status.pending_review


def _python(code: str, db: Path) -> dict:
    env = {**os.environ, "NETPULSE_LLM_PROVIDER": "heuristic", "PYTHONPATH": str(ROOT)}
    out = subprocess.run(  # noqa: S603 - test harness launching the interpreter on our own code
        [sys.executable, "-c", textwrap.dedent(code), str(db)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_pause_survives_process_restart(tmp_path):
    db = tmp_path / "durable.db"
    case = CASES[SCENARIO_CASE["congestion"]]
    started = _python(
        f"""
        import json, sys
        from netpulse.graph.deps import Deps
        from netpulse.persistence.store import PersistentStore
        from netpulse.service import InvestigationService
        case = json.loads({json.dumps(json.dumps(case))})
        svc = InvestigationService(Deps.default(), PersistentStore(sys.argv[1]))
        s = svc.start(case["submission"], submitted_at=case["submitted_at"], incident_id=case["case_id"])
        print(json.dumps(s.model_dump(mode="json")))
    """,
        db,
    )
    assert started["status"] == "awaiting_approval" and started["pending_review"]

    finished = _python(
        f"""
        import json, sys
        from netpulse.graph.deps import Deps
        from netpulse.models import ReviewInput
        from netpulse.persistence.store import PersistentStore
        from netpulse.service import InvestigationService
        svc = InvestigationService(Deps.default(), PersistentStore(sys.argv[1]))
        pending = svc.status("{case["case_id"]}").pending_review
        assert pending is not None, "pending review lost across restart"
        s = svc.decide("{case["case_id"]}", ReviewInput(reviewer_id="night-shift", reviewer_role="operator",
                                                        choice="approve"))
        print(json.dumps({{"status": s.status, "report": s.final_report,
                          "audit": [e["event"] for e in svc.audit_log("{case["case_id"]}")]}}))
    """,
        db,
    )
    assert finished["status"] == "completed"
    assert finished["report"]["reviewer_decisions"][0]["reviewer_id"] == "night-shift"
    assert finished["audit"] == ["submitted", "run_paused", "review_submitted", "run_finished"]


class Crash(BaseException):
    """Simulates the process dying mid-run (not an Exception, so nothing inside the graph can catch it)."""


class CrashingGenerator:
    provider, model = "crashing", None

    def generate(self, context):
        raise Crash()


def test_interrupted_run_resumes_from_last_checkpoint(deps, tmp_path):
    db = tmp_path / "crash.db"
    crashing = Deps(
        store=deps.store,
        retriever=deps.retriever,
        topology=deps.topology,
        generator=CrashingGenerator(),
        clock=deps.clock,
    )
    store = PersistentStore(db)
    case = CASES[SCENARIO_CASE["congestion"]]
    with pytest.raises(Crash):
        InvestigationService(crashing, store).start(
            case["submission"], submitted_at=case["submitted_at"], incident_id=case["case_id"]
        )
    store.close()

    store = PersistentStore(db)  # "new process": fresh connection and service, healthy generator
    service = InvestigationService(deps, store)
    stalled = service.status(case["case_id"])
    assert stalled.next_nodes == ["generate_hypotheses"] and stalled.pending_review is None
    resumed = service.resume(case["case_id"])
    assert resumed.status == "awaiting_approval"
    nodes_run = [t["node"] for t in service.state(case["case_id"])["node_trace"]]
    assert nodes_run.count("retrieve_telemetry") == 1  # earlier work was not redone
    store.close()


def test_in_memory_mode_is_flagged_as_not_durable(deps):
    status = InvestigationService(deps).start(
        **{
            "submission": case_state("congestion", deps)["submission"],
            "submitted_at": CASES[SCENARIO_CASE["congestion"]]["submitted_at"],
            "incident_id": "mem-1",
        }
    )
    assert status.durable is False


def test_audit_log_is_append_only(service):
    incident = start(service, "congestion").incident_id
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        service.store.conn.execute("UPDATE netpulse_audit SET actor = 'mallory' WHERE incident_id = ?", (incident,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        service.store.conn.execute("DELETE FROM netpulse_audit WHERE incident_id = ?", (incident,))
