"""Four-eyes rule: the identity that submitted an incident cannot approve it."""

import dataclasses
import time

import pytest
from fastapi.testclient import TestClient

from netpulse.api.app import create_app
from netpulse.api.auth import TokenStore
from netpulse.graph.builder import build_graph
from netpulse.graph.deps import Deps
from netpulse.graph.runner import initial_state, resume_investigation, run_investigation
from netpulse.models import ReviewInput
from netpulse.persistence.store import PersistentStore
from netpulse.policy.engine import four_eyes_violation, same_person
from netpulse.service import AuthorizationError, InvestigationService
from netpulse.ui.client import cannot_approve_reason
from tests.graph_helpers import CASES, shared_deps

OPERATOR_CASE = "case-04"  # operator may decide
SENIOR_CASE = "case-13"  # critical severity: a senior operator must decide


def review(reviewer: str, choice="approve", role="senior_operator") -> ReviewInput:
    return ReviewInput(reviewer_id=reviewer, reviewer_role=role, choice=choice)


def start(service: InvestigationService, case_id: str, submitted_by: str) -> str:
    case = CASES[case_id]
    status = service.start(
        case["submission"], submitted_at=case["submitted_at"], incident_id=case_id, submitted_by=submitted_by
    )
    assert status.status == "awaiting_approval"
    return case_id


@pytest.fixture
def service(tmp_path):
    return InvestigationService(shared_deps(), PersistentStore(tmp_path / "f.db", audit_key="k" * 40))


# ---------------------------------------------------------------- the rule itself


@pytest.mark.parametrize(
    "submitter, reviewer, choice, blocked",
    [
        ("alice", "alice", "approve", True),
        ("alice", "ALICE", "approve", True),
        (" alice ", "alice", "approve", True),
        ("alice", "bob", "approve", False),
        ("alice", "alice", "reject", False),
        ("alice", "alice", "request_more_investigation", False),
        ("", "", "approve", False),  # no recorded submitter: nothing to compare
        ("alice", "alicia", "approve", False),
    ],
)
def test_rule_matrix(submitter, reviewer, choice, blocked):
    assert (four_eyes_violation(submitter, reviewer, choice) is not None) is blocked


def test_same_person_requires_a_non_empty_identity():
    assert same_person("Bob", "bob") and not same_person("", "") and not same_person("a", "b")


# ---------------------------------------------------------------- service


def test_submitter_cannot_approve_but_another_reviewer_can(service):
    incident = start(service, OPERATOR_CASE, submitted_by="alice")
    with pytest.raises(AuthorizationError, match="four-eyes"):
        service.decide(incident, review("alice", role="operator"))
    assert service.status(incident).status == "awaiting_approval"  # nothing was resumed
    denied = [e for e in service.audit_log(incident) if e["event"] == "review_denied"]
    assert len(denied) == 1 and denied[0]["actor"] == "alice" and denied[0]["payload"]["reason"] == "four_eyes"
    done = service.decide(incident, review("bob", role="operator"))
    assert done.status == "completed" and done.approval_status == "approved"
    assert service.store.verify_audit().ok


def test_a_senior_submitter_still_needs_a_different_senior(service):
    incident = start(service, SENIOR_CASE, submitted_by="sam")
    with pytest.raises(AuthorizationError, match="four-eyes"):
        service.decide(incident, review("sam"))
    with pytest.raises(AuthorizationError, match="senior_operator role required"):
        service.decide(incident, review("bob", role="operator"))  # a second person of too low a role is still refused
    assert service.decide(incident, review("dana")).approval_status == "approved"


def test_submitter_may_reject_their_own_incident(service):
    incident = start(service, OPERATOR_CASE, submitted_by="alice")
    done = service.decide(incident, review("alice", choice="reject", role="operator"))
    assert done.approval_status == "rejected"


def test_submitter_may_request_more_investigation(service):
    incident = start(service, OPERATOR_CASE, submitted_by="alice")
    service.decide(incident, review("alice", choice="request_more_investigation", role="operator"))
    events = [(e["event"], e["actor"]) for e in service.audit_log(incident)]
    assert ("review_submitted", "alice") in events
    assert not [e for e in service.audit_log(incident) if e["event"] == "review_denied"]
    decisions = service.state(incident)["reviewer_decisions"]
    assert decisions[0]["choice"] == "request_more_investigation" and decisions[0]["reviewer_id"] == "alice"


def test_review_request_tells_the_reviewer_who_submitted(service):
    incident = start(service, OPERATOR_CASE, submitted_by="alice")
    assert service.status(incident).pending_review["submitted_by"] == "alice"


def test_rule_can_be_disabled_for_single_person_labs(tmp_path):
    deps = dataclasses.replace(shared_deps(), four_eyes=False)
    service = InvestigationService(deps, PersistentStore(tmp_path / "off.db", audit_key="k" * 40))
    incident = start(service, OPERATOR_CASE, submitted_by="alice")
    assert service.decide(incident, review("alice", role="operator")).approval_status == "approved"


@pytest.mark.parametrize(
    "value, expected", [(None, True), ("on", True), ("off", False), ("FALSE", False), ("0", False)]
)
def test_environment_switch(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("NETPULSE_FOUR_EYES", raising=False)
    else:
        monkeypatch.setenv("NETPULSE_FOUR_EYES", value)
    assert Deps.default(provider="heuristic").four_eyes is expected


# ---------------------------------------------------------------- inside the graph


def test_graph_refuses_the_submitter_even_when_resumed_directly(tmp_path):
    """The service is not the only way in: resuming the graph itself must also enforce the rule."""
    deps = shared_deps()
    case = CASES[OPERATOR_CASE]
    state = initial_state(
        case["submission"], submitted_at=case["submitted_at"], incident_id="direct", submitted_by="alice", deps=deps
    )
    graph = build_graph(deps)
    paused = run_investigation(deps, state, graph)
    assert "__interrupt__" in paused
    retry = resume_investigation(deps, graph, "direct", review("alice", role="operator").model_dump(mode="json"))
    assert "__interrupt__" in retry  # still waiting
    assert "four-eyes" in graph.get_state({"configurable": {"thread_id": "direct"}}).values["review_error"]
    done = resume_investigation(deps, graph, "direct", review("bob", role="operator").model_dump(mode="json"))
    assert "__interrupt__" not in done and done["approval_status"] == "approved"


# ---------------------------------------------------------------- API


@pytest.fixture
def api(tmp_path):
    service = InvestigationService(shared_deps(), PersistentStore(tmp_path / "api.db", audit_key="k" * 40))
    tokens = TokenStore.parse(
        "olga-token-0123456789:operator:olga,omar-token-0123456789:operator:omar,"
        "sam-token-012345678901:senior_operator:sam,dana-token-01234567890:senior_operator:dana"
    )
    with TestClient(create_app(service, tokens, max_workers=1)) as client:
        client.service = service
        yield client


TOKENS = {"olga": "0123456789", "omar": "0123456789", "sam": "012345678901", "dana": "01234567890"}


def bearer(name: str) -> dict:
    return {"Authorization": f"Bearer {name}-token-{TOKENS[name]}"}


def wait_for(client, incident, who, status):
    for _ in range(300):
        if client.get(f"/incidents/{incident}", headers=bearer(who)).json()["status"] == status:
            return
        time.sleep(0.1)
    raise AssertionError(f"{incident} never reached {status}")


def test_api_refuses_self_approval_immediately_with_403_and_allows_a_colleague(api):
    case = CASES[OPERATOR_CASE]
    body = {"submission": case["submission"], "incident_id": "api-1"}
    assert api.post("/incidents", headers=bearer("olga"), json=body).status_code == 202
    wait_for(api, "api-1", "olga", "awaiting_approval")
    own = api.post("/incidents/api-1/approval", headers=bearer("olga"), json={"choice": "approve"})
    assert own.status_code == 403 and "four-eyes" in own.json()["detail"]
    status = api.get("/incidents/api-1", headers=bearer("olga")).json()
    assert status["status"] == "awaiting_approval" and status["error"] is None  # refused synchronously, no failed job
    assert api.post("/incidents/api-1/approval", headers=bearer("olga"), json={"choice": "reject"}).status_code == 202
    for _ in range(300):
        done = api.get("/incidents/api-1", headers=bearer("olga")).json()
        if done["job_state"] == "done" and done["approval_status"] == "rejected":
            break
        time.sleep(0.1)
    assert done["approval_status"] == "rejected"
    assert api.service.store.verify_audit().ok


def test_api_colleague_can_approve_and_audit_names_both_people(api):
    case = CASES[OPERATOR_CASE]
    api.post("/incidents", headers=bearer("olga"), json={"submission": case["submission"], "incident_id": "api-2"})
    wait_for(api, "api-2", "omar", "awaiting_approval")
    assert api.post("/incidents/api-2/approval", headers=bearer("omar"), json={"choice": "approve"}).status_code == 202
    wait_for(api, "api-2", "omar", "completed")
    events = [(e["event"], e["actor"]) for e in api.service.audit_log("api-2")]
    assert ("submitted", "olga") in events and ("review_submitted", "omar") in events
    assert api.get("/audit/verify", headers=bearer("sam")).json()["ok"] is True


def test_api_senior_submitter_needs_another_senior(api):
    case = CASES[SENIOR_CASE]
    api.post("/incidents", headers=bearer("sam"), json={"submission": case["submission"], "incident_id": "api-3"})
    wait_for(api, "api-3", "sam", "awaiting_approval")
    assert api.post("/incidents/api-3/approval", headers=bearer("sam"), json={"choice": "approve"}).status_code == 403
    assert api.post("/incidents/api-3/approval", headers=bearer("dana"), json={"choice": "approve"}).status_code == 202


def test_me_and_health_report_identity_and_the_rule(api):
    assert api.get("/me", headers=bearer("olga")).json() == {"name": "olga", "role": "operator"}
    assert api.get("/health").json()["four_eyes"] is True


# ---------------------------------------------------------------- UI helper


def test_ui_explains_why_the_submitter_cannot_approve():
    pending = {"submitted_by": "Olga"}
    assert "another reviewer" in cannot_approve_reason(pending, "olga", True)
    assert cannot_approve_reason(pending, "omar", True) is None
    assert cannot_approve_reason(pending, "olga", False) is None
    assert cannot_approve_reason({}, "olga", True) is None
