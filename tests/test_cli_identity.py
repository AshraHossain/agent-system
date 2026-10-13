"""The CLI no longer lets anyone claim an identity: reviews go through the API as the token's identity."""

import json
import time

import pytest
from fastapi.testclient import TestClient

import netpulse.cli as cli
from netpulse.api.app import create_app
from netpulse.api.auth import TokenStore
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService
from netpulse.ui.client import ApiClient
from tests.graph_helpers import CASES, shared_deps

KEY = "k" * 40
TOKENS = {
    "olga": "olga-token-0123456789",  # operator, submits the incidents
    "omar": "omar-token-0123456789",  # operator
    "sam": "sam-token-0123456789ab",  # senior operator
}
SPEC = f"{TOKENS['olga']}:operator:olga,{TOKENS['omar']}:operator:omar,{TOKENS['sam']}:senior_operator:sam"
OPERATOR_CASE, SENIOR_CASE = "case-04", "case-13"


@pytest.fixture
def env(monkeypatch):
    for var in ("NETPULSE_API_URL", "NETPULSE_API_TOKEN", "NETPULSE_ALLOW_LOCAL_REVIEW", "NETPULSE_AUDIT_KEY"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def api(tmp_path, env):
    store = PersistentStore(tmp_path / "api.db", audit_key=KEY)
    service = InvestigationService(shared_deps(), store)
    with TestClient(create_app(service, TokenStore.parse(SPEC), max_workers=1)) as client:
        client.service = service
        # the CLI builds its own ApiClient; route it into the in-process app
        env.setattr(cli, "_api_client", lambda url, token: ApiClient(url, token, http=client))
        yield client


def submit_and_pause(api, who: str, case_id: str, incident: str) -> None:
    body = {"submission": CASES[case_id]["submission"], "incident_id": incident}
    assert api.post("/incidents", headers={"Authorization": f"Bearer {TOKENS[who]}"}, json=body).status_code == 202
    for _ in range(300):
        if api.service.status(incident).status == "awaiting_approval" if _exists(api, incident) else False:
            return
        time.sleep(0.1)
    raise AssertionError("never paused")


def _exists(api, incident) -> bool:
    try:
        api.service.status(incident)
        return True
    except Exception:  # noqa: BLE001 - not created yet
        return False


def review(*extra: str, incident="inc-1", choice="approve") -> list[str]:
    return ["review", "--incident", incident, "--choice", choice, "--api-url", "https://api.example.test", *extra]


def authed(env, who: str) -> None:
    env.setenv("NETPULSE_API_TOKEN", TOKENS[who])


# ---------------------------------------------------------------- the forgery path is gone


def test_identity_flags_are_refused_without_local(env, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(review("--reviewer", "sam", "--role", "senior_operator"))
    assert exit_info.value.code == 2
    assert "only valid with --local" in capsys.readouterr().err


def test_local_mode_is_disabled_by_default(env, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--db", str(tmp_path / "x.db"), "review", "--local", "--incident", "i", "--choice", "approve",
                  "--reviewer", "alice", "--role", "senior_operator"])  # fmt: skip
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "NETPULSE_ALLOW_LOCAL_REVIEW" in err and "self-asserted" in err
    assert not (tmp_path / "x.db").exists()  # refused before the database was even opened


def test_local_mode_needs_both_identity_flags(env, tmp_path):
    env.setenv("NETPULSE_ALLOW_LOCAL_REVIEW", "true")
    with pytest.raises(SystemExit):
        cli.main(["--db", str(tmp_path / "x.db"), "review", "--local", "--incident", "i", "--choice", "approve",
                  "--reviewer", "alice"])  # fmt: skip


def test_a_normal_review_never_opens_the_database(env, tmp_path, capsys):
    env.setenv("NETPULSE_API_TOKEN", "whatever-token-0123456789")
    never = tmp_path / "never.db"
    env.setattr(cli, "_api_client", lambda url, token: pytest.fail("no request expected"))
    assert cli.main(["--db", str(never), "review", "--incident", "i", "--choice", "approve"]) == 2  # no URL
    assert "NETPULSE_API_URL" in capsys.readouterr().err and not never.exists()


def test_a_missing_token_is_an_error_and_the_token_is_never_an_argument(env, capsys):
    assert cli.main(review()) == 2
    assert "NETPULSE_API_TOKEN" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main([*review(), "--token", "abc"])  # there is no such flag


@pytest.mark.parametrize("url", ["http://api.example.test", "ftp://x", "api.example.test"])
def test_the_token_is_never_sent_over_plain_http_to_a_remote_host(env, url, capsys):
    authed(env, "sam")
    env.setattr(cli, "_api_client", lambda u, t: pytest.fail("a request must not be built"))
    assert cli.main(["review", "--incident", "i", "--choice", "approve", "--api-url", url]) == 2
    assert "must be https" in capsys.readouterr().err


def test_plain_http_is_fine_for_localhost(env, api):
    authed(env, "sam")
    assert cli.main(["review", "--incident", "ghost", "--choice", "approve", "--api-url", "http://localhost:8000"]) == 3


# ---------------------------------------------------------------- reviews through the API


def test_the_identity_recorded_is_the_tokens_not_anything_typed(env, api, capsys):
    submit_and_pause(api, "olga", SENIOR_CASE, "inc-1")
    authed(env, "sam")
    assert cli.main(review("--wait")) == 0
    out = capsys.readouterr().out
    assert "submitted as sam (senior_operator, tenant default)" in out and "workflow=completed" in out
    events = {e["event"]: e for e in api.service.audit_log("inc-1")}
    assert events["review_submitted"]["actor"] == "sam"
    assert events["review_submitted"]["payload"]["channel"] == "api"
    assert api.service.store.verify_audit().ok


def test_role_is_enforced_server_side(env, api, capsys):
    submit_and_pause(api, "olga", SENIOR_CASE, "inc-1")
    authed(env, "omar")  # an operator cannot decide a critical incident, whatever the CLI is asked
    assert cli.main(review()) == 3
    assert "senior_operator role required" in capsys.readouterr().err
    assert api.service.status("inc-1").status == "awaiting_approval"


def test_four_eyes_is_enforced_server_side(env, api, capsys):
    submit_and_pause(api, "olga", OPERATOR_CASE, "inc-1")
    authed(env, "olga")  # the submitter
    assert cli.main(review()) == 3
    assert "four-eyes" in capsys.readouterr().err
    assert api.service.status("inc-1").status == "awaiting_approval"
    authed(env, "omar")
    assert cli.main(review("--wait")) == 0


def test_a_bad_token_is_rejected_and_not_echoed(env, api, capsys):
    env.setenv("NETPULSE_API_TOKEN", "definitely-not-valid-0123456789")
    assert cli.main(review()) == 3
    captured = capsys.readouterr()
    assert "invalid bearer token" in captured.err
    assert "definitely-not-valid" not in captured.out + captured.err


def test_the_token_never_appears_in_output(env, api, capsys):
    submit_and_pause(api, "olga", OPERATOR_CASE, "inc-1")
    authed(env, "omar")
    assert cli.main(review("--json", "--wait")) == 0
    captured = capsys.readouterr()
    assert TOKENS["omar"] not in captured.out + captured.err
    payload = json.loads(captured.out)
    assert payload["reviewer"]["name"] == "omar" and payload["status"]["approval_status"] == "approved"


def test_comment_and_partial_action_approval_are_passed_through(env, api):
    submit_and_pause(api, "olga", OPERATOR_CASE, "inc-1")
    authed(env, "omar")
    action = api.service.status("inc-1").pending_review["pending_actions"][0]["action"]["action_id"]
    assert cli.main(review("--comment", "checked the parallel uplink", "--actions", action, "--wait")) == 0
    decision = api.service.state("inc-1")["reviewer_decisions"][0]
    assert decision["approved_action_ids"] == [action] and "parallel uplink" in decision["comment"]


# ---------------------------------------------------------------- the explicit, audited development path


def test_local_review_still_enforces_role_and_four_eyes_and_marks_its_channel(env, tmp_path, capsys):
    db = tmp_path / "local.db"
    env.setenv("NETPULSE_ALLOW_LOCAL_REVIEW", "true")
    env.setenv("NETPULSE_AUDIT_KEY", KEY)
    store = PersistentStore(db)
    service = InvestigationService(shared_deps(), store)
    case = CASES[SENIOR_CASE]
    service.start(case["submission"], submitted_at=case["submitted_at"], incident_id="inc-1", submitted_by="olga")
    store.conn.commit()

    def local(*extra):
        return cli.main(["--db", str(db), "--provider", "heuristic", "review", "--local", "--incident", "inc-1",
                         "--choice", "approve", *extra])  # fmt: skip

    assert local("--reviewer", "bob", "--role", "operator") == 3  # role still enforced
    assert local("--reviewer", "olga", "--role", "senior_operator") == 3  # four-eyes still enforced
    assert "four-eyes" in capsys.readouterr().err
    assert local("--reviewer", "dana", "--role", "senior_operator") == 0
    rows = PersistentStore(db).audit_log("inc-1")
    submitted = next(r for r in rows if r["event"] == "review_submitted")
    assert submitted["actor"] == "dana" and submitted["payload"]["channel"] == "local-cli"  # visibly self-asserted
    assert all(r["payload"].get("channel") == "local-cli" for r in rows if r["event"].startswith("review_"))
