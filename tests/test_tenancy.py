"""Tenant isolation: incidents are namespaced per tenant, so one tenant cannot see or touch another's."""

import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from netpulse.api.app import create_app
from netpulse.api.auth import TokenStore
from netpulse.api.tenancy import DatasetPolicy, scope_incident_id, valid_tenant
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService
from tests.graph_helpers import CASES, shared_deps

TOKENS = {
    "olga": "olga-token-0123456789",  # acme operator
    "sam": "sam-token-0123456789a",  # acme senior operator
    "greta": "greta-token-012345678",  # globex operator
    "pat": "pat-token-0123456789ab",  # platform senior operator
}


def bearer(who: str) -> dict:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


SPEC = (
    f"{TOKENS['olga']}:operator:olga:acme,{TOKENS['sam']}:senior_operator:sam:acme,"
    f"{TOKENS['greta']}:operator:greta:globex,{TOKENS['pat']}:senior_operator:pat:_platform"
)
OPERATOR_CASE = CASES["case-04"]["submission"]  # an operator may decide this one


# ---------------------------------------------------------------- unit


def test_default_tenant_keeps_plain_keys_and_others_are_namespaced():
    assert scope_incident_id("default", "inc-1") == "inc-1"
    assert scope_incident_id("acme", "inc-1") == "acme~inc-1"
    assert scope_incident_id("acme", "inc-1") != scope_incident_id("globex", "inc-1")


@pytest.mark.parametrize("bad", ["acme~inc-1", "~x", "../x", "Upper", "a b", "", "x" * 65, "-lead", "a/b", "a\nb"])
def test_ids_that_could_cross_namespaces_are_rejected_for_every_tenant(bad):
    for tenant in ("default", "acme", "_platform"):
        assert scope_incident_id(tenant, bad) is None


@pytest.mark.parametrize(
    "tenant, ok",
    [("acme", True), ("a", True), ("_platform", True), ("Acme", False), ("a~b", False)]
    + [("", False), ("x" * 33, False), ("_other", False), ("a b", False)],
)
def test_tenant_names_are_validated(tenant, ok):
    assert valid_tenant(tenant) is ok


def test_dataset_policy_parses_and_denies_by_default():
    policy = DatasetPolicy.parse("acme=case-01,case-02; globex=case-03 ;internal=*")
    assert policy.allows("acme", "case-02") and not policy.allows("acme", "case-03")
    assert policy.allows("internal", "anything") and not policy.allows("unlisted", "case-01")
    for bad in ("acme", "acme=", "=case-01", "Acme=case-01"):
        with pytest.raises(ValueError):
            DatasetPolicy.parse(bad)


def test_static_tokens_carry_an_optional_tenant():
    store = TokenStore.parse(SPEC)
    assert store.multi_tenant and store.resolve(TOKENS["olga"]).tenant == "acme"
    single = TokenStore.parse("tok-0123456789abcdef:operator:x")
    assert not single.multi_tenant and single.resolve("tok-0123456789abcdef").tenant == "default"
    with pytest.raises(ValueError, match="tenant"):
        TokenStore.parse("tok-0123456789abcdef:operator:x:Bad~Tenant")


# ---------------------------------------------------------------- API


@pytest.fixture
def api(tmp_path):
    service = InvestigationService(shared_deps(), PersistentStore(tmp_path / "t.db", audit_key="k" * 40))
    policy = DatasetPolicy.parse("acme=case-04,case-13;globex=case-04")
    with TestClient(create_app(service, TokenStore.parse(SPEC), max_workers=2, datasets=policy)) as client:
        client.service = service
        yield client


def submit(api, who, incident_id, submission=OPERATOR_CASE):
    return api.post("/incidents", headers=bearer(who), json={"submission": submission, "incident_id": incident_id})


def wait_for(api, who, incident_id, status):
    for _ in range(300):
        got = api.get(f"/incidents/{incident_id}", headers=bearer(who)).json()
        if got.get("status") == status:
            return got
        time.sleep(0.1)
    raise AssertionError(f"{incident_id} never reached {status}: {got}")


def test_a_tenant_cannot_read_or_change_another_tenants_incident_through_any_route(api):
    assert submit(api, "olga", "inc-a").status_code == 202
    wait_for(api, "olga", "inc-a", "awaiting_approval")
    before = api.get("/incidents/inc-a", headers=bearer("olga")).json()
    for method, path in [
        ("GET", "/incidents/inc-a"),
        ("GET", "/incidents/inc-a/results"),
        ("GET", "/incidents/inc-a/evidence"),
        ("POST", "/incidents/inc-a/approval"),
        ("POST", "/incidents/inc-a/resume"),
    ]:
        response = api.request(
            method, path, headers=bearer("greta"), json={"choice": "approve"} if "approval" in path else None
        )
        assert response.status_code == 404, (method, path, response.text)
    assert api.get("/incidents/inc-a", headers=bearer("olga")).json() == before  # untouched


def test_every_incident_route_is_wired_through_the_scoping_dependency(api):
    """A future route that takes {incident_id} but forgets to scope it would silently leak across tenants."""
    routes = [r for r in api.app.routes if isinstance(r, APIRoute) and "{incident_id}" in r.path]
    assert len(routes) >= 5
    for route in routes:
        deps = {d.call.__name__ for d in route.dependant.dependencies if d.call}
        assert "incident_ref" in deps, route.path


def test_denials_are_indistinguishable_from_never_existed(api):
    submit(api, "olga", "inc-a")
    other = api.get("/incidents/inc-a/results", headers=bearer("greta"))
    never = api.get("/incidents/inc-nothing/results", headers=bearer("greta"))
    assert other.status_code == never.status_code == 404
    assert other.json()["detail"].replace("inc-a", "X") == never.json()["detail"].replace("inc-nothing", "X")


def test_same_incident_id_in_two_tenants_are_two_independent_incidents(api):
    assert submit(api, "olga", "shared-name").status_code == 202
    assert submit(api, "greta", "shared-name").status_code == 202  # no collision, and no hint that it exists elsewhere
    assert submit(api, "olga", "shared-name").status_code == 409  # but a collision inside one tenant still is one
    wait_for(api, "olga", "shared-name", "awaiting_approval")
    wait_for(api, "greta", "shared-name", "awaiting_approval")
    assert api.service.status("acme~shared-name").pending_review["incident_id"] == "acme~shared-name"
    assert api.service.status("globex~shared-name").pending_review["incident_id"] == "globex~shared-name"


@pytest.mark.parametrize("path", ["/incidents/acme~inc-a", "/incidents/acme~inc-a/results", "/incidents/%7Eacme"])
def test_the_namespace_separator_cannot_be_smuggled_in_a_path(api, path):
    submit(api, "olga", "inc-a")
    assert api.get(path, headers=bearer("greta")).status_code == 404


def test_the_namespace_separator_cannot_be_smuggled_in_a_new_incident_id(api):
    assert submit(api, "greta", "acme~inc-a").status_code == 422


def test_callers_see_their_own_ids_never_the_storage_key(api):
    submit(api, "olga", "inc-b")
    status = wait_for(api, "olga", "inc-b", "awaiting_approval")
    assert status["incident_id"] == "inc-b" and status["pending_review"]["incident_id"] == "inc-b"
    assert (
        api.post("/incidents/inc-b/approval", headers=bearer("sam"), json={"choice": "approve"}).json()["incident_id"]
        == "inc-b"
    )
    done = wait_for(api, "olga", "inc-b", "completed")
    results = api.get("/incidents/inc-b/results", headers=bearer("olga")).json()
    assert results["incident_id"] == "inc-b" and results["final_report"]["incident_id"] == "inc-b"
    assert "acme~" not in str(done) + str(results)


def test_four_eyes_works_inside_a_tenant(api):
    submit(api, "olga", "inc-c")
    wait_for(api, "olga", "inc-c", "awaiting_approval")
    own = api.post("/incidents/inc-c/approval", headers=bearer("olga"), json={"choice": "approve"})
    assert own.status_code == 403 and "four-eyes" in own.json()["detail"]
    assert api.post("/incidents/inc-c/approval", headers=bearer("sam"), json={"choice": "approve"}).status_code == 202


def test_platform_identities_manage_the_service_but_cannot_touch_incidents(api):
    submit(api, "olga", "inc-d")
    assert api.get("/incidents/inc-d", headers=bearer("pat")).status_code == 403
    assert submit(api, "pat", "inc-p").status_code == 403
    verify = api.get("/audit/verify", headers=bearer("pat"))
    assert verify.status_code == 200 and verify.json()["ok"] is True


def test_the_global_audit_log_is_not_available_to_tenant_seniors(api):
    assert api.get("/audit/verify", headers=bearer("sam")).status_code == 403
    assert api.get("/audit/verify", headers=bearer("greta")).status_code == 403


def test_datasets_are_restricted_per_tenant(api):
    assert submit(api, "olga", "ds-1", CASES["case-04"]["submission"]).status_code == 202
    denied = submit(api, "olga", "ds-2", CASES["case-02"]["submission"])  # acme may not use case-02
    assert denied.status_code == 403 and "dataset" in denied.json()["detail"]
    assert submit(api, "greta", "ds-3", CASES["case-13"]["submission"]).status_code == 403  # nor globex case-13


def test_a_tenant_with_no_dataset_entry_can_use_none(tmp_path):
    service = InvestigationService(shared_deps(), PersistentStore(tmp_path / "n.db", audit_key="k" * 40))
    with TestClient(create_app(service, TokenStore.parse(SPEC), datasets=DatasetPolicy.parse(""))) as client:
        assert submit(client, "olga", "x").status_code == 403


def test_me_and_health_report_tenant_and_mode(api):
    assert api.get("/me", headers=bearer("olga")).json() == {"name": "olga", "role": "operator", "tenant": "acme"}
    assert api.get("/health").json()["multi_tenant"] is True


def test_rate_limits_do_not_mix_two_tenants_users_who_share_a_name(tmp_path):
    from netpulse.api.protection import Protection

    service = InvestigationService(shared_deps(), PersistentStore(tmp_path / "r.db", audit_key="k" * 40))
    spec = "a-token-0123456789abc:operator:alice:acme,b-token-0123456789abc:operator:alice:globex"
    protection = Protection(read_per_min=6, read_burst=2)
    with TestClient(create_app(service, TokenStore.parse(spec), protection=protection)) as c:
        a, b = {"Authorization": "Bearer a-token-0123456789abc"}, {"Authorization": "Bearer b-token-0123456789abc"}
        assert [c.get("/incidents/x", headers=a).status_code for _ in range(3)] == [404, 404, 429]
        assert c.get("/incidents/x", headers=b).status_code == 404


def test_single_tenant_deployments_behave_exactly_as_before(tmp_path):
    service = InvestigationService(shared_deps(), PersistentStore(tmp_path / "s.db", audit_key="k" * 40))
    tokens = TokenStore.parse("op-token-0123456789ab:operator:olga,sr-token-0123456789ab:senior_operator:sam")
    with TestClient(create_app(service, tokens)) as c:
        op, sr = {"Authorization": "Bearer op-token-0123456789ab"}, {"Authorization": "Bearer sr-token-0123456789ab"}
        assert (
            c.post("/incidents", headers=op, json={"submission": OPERATOR_CASE, "incident_id": "plain"}).status_code
            == 202
        )
        for _ in range(200):
            if c.get("/incidents/plain", headers=op).json()["status"] == "awaiting_approval":
                break
            time.sleep(0.1)
        assert service.status("plain").pending_review["incident_id"] == "plain"  # unprefixed key
        assert c.get("/audit/verify", headers=sr).status_code == 200  # seniors may verify when there is one tenant
        assert c.get("/health").json()["multi_tenant"] is False
        assert c.get("/incidents/acme~plain", headers=op).status_code == 404
