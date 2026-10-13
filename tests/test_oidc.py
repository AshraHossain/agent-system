"""OIDC authentication against a fake identity provider (real RSA keys, mock JWKS endpoint)."""

import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from netpulse.api.app import create_app
from netpulse.api.oidc import OidcConfig, OidcVerifier, parse_role_map
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService
from tests.graph_helpers import CASES, shared_deps

ISSUER = "https://idp.example.test"
AUDIENCE = "netpulse-api"
ROLE_MAP = {"np-viewers": "viewer", "np-operators": "operator", "np-seniors": "senior_operator"}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeIdp:
    """Serves a discovery document and a JWKS; counts fetches; can be switched off."""

    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {"k1": rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.fetches = 0
        self.down = False
        self.discovery_issuer = ISSUER
        self.jwks_uri = f"{ISSUER}/keys"
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def add_key(self, kid: str) -> rsa.RSAPrivateKey:
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return self.keys[kid]

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": self.discovery_issuer, "jwks_uri": self.jwks_uri})
        if request.url.path == "/keys":
            self.fetches += 1
            keys = []
            for kid, key in self.keys.items():
                jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
                keys.append({**jwk, "kid": kid, "use": "sig", "alg": "RS256"})
            return httpx.Response(200, json={"keys": keys})
        return httpx.Response(404)

    def mint(self, kid="k1", key=None, alg="RS256", **overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": AUDIENCE, "sub": "user-1234", "iat": now, "exp": now + 300,
            "groups": ["np-operators"],
        }  # fmt: skip
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        headers = {"kid": kid} if kid else {}
        return jwt.encode(claims, key or self.keys[kid or "k1"], algorithm=alg, headers=headers)


@pytest.fixture
def idp():
    return FakeIdp()


def verifier(idp, clock=time.time, **config) -> OidcVerifier:
    cfg = OidcConfig(issuer=ISSUER, audience=AUDIENCE, role_map=ROLE_MAP, **config)
    return OidcVerifier(cfg, http=idp.client, clock=clock)


# ---------------------------------------------------------------- accepted


def test_valid_token_resolves_to_principal_with_stable_identity_and_role(idp):
    principal = verifier(idp).resolve(idp.mint(email="olga@example.test"))
    assert (principal.name, principal.role) == ("user-1234", "operator")
    assert principal.token == ""  # the credential is never kept


def test_highest_mapped_role_wins_and_unmapped_groups_are_ignored(idp):
    token = idp.mint(groups=["random", "np-viewers", "np-seniors", "np-operators"])
    assert verifier(idp).resolve(token).role == "senior_operator"


def test_role_claim_may_be_a_space_separated_string_and_name_claim_is_configurable(idp):
    v = verifier(idp, role_claim="scp", name_claim="preferred_username")
    token = idp.mint(scp="np-viewers np-operators", preferred_username="olga", groups=None)
    principal = v.resolve(token)
    assert (principal.name, principal.role) == ("olga", "operator")


def test_discovery_is_used_when_no_jwks_url_is_configured(idp):
    assert verifier(idp).resolve(idp.mint()) is not None
    assert idp.fetches == 1


# ---------------------------------------------------------------- rejected (401)


def test_expired_token_is_rejected(idp):
    assert verifier(idp, leeway=0).resolve(idp.mint(exp=int(time.time()) - 10)) is None


def test_small_clock_skew_is_tolerated_but_not_large(idp):
    v = verifier(idp, leeway=60)
    assert v.resolve(idp.mint(exp=int(time.time()) - 20)) is not None
    assert v.resolve(idp.mint(exp=int(time.time()) - 600)) is None


def test_token_not_yet_valid_is_rejected(idp):
    assert verifier(idp, leeway=0).resolve(idp.mint(nbf=int(time.time()) + 600)) is None


@pytest.mark.parametrize("claims", [{"iss": "https://evil.example.test"}, {"aud": "someone-else"}, {"aud": None}])
def test_wrong_issuer_or_audience_or_missing_audience_is_rejected(idp, claims):
    assert verifier(idp).resolve(idp.mint(**claims)) is None


@pytest.mark.parametrize("claim", ["sub", "exp"])
def test_required_claims_must_be_present(idp, claim):
    assert verifier(idp).resolve(idp.mint(**{claim: None})) is None


def test_token_signed_by_an_unpublished_key_is_rejected(idp):
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert verifier(idp).resolve(idp.mint(kid="k1", key=attacker)) is None  # right kid, wrong key


def test_token_without_kid_is_rejected(idp):
    assert verifier(idp).resolve(idp.mint(kid=None, key=idp.keys["k1"])) is None


def test_alg_none_is_rejected(idp):
    header = b64(json.dumps({"alg": "none", "kid": "k1"}).encode())
    payload = b64(json.dumps({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 300,
                              "groups": ["np-seniors"]}).encode())  # fmt: skip
    assert verifier(idp).resolve(f"{header}.{payload}.") is None


def test_hmac_signed_with_the_public_key_is_rejected(idp):
    """Algorithm confusion: sign HS256 using the published public key as the HMAC secret."""
    public_pem = idp.keys["k1"].public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )  # fmt: skip
    header = b64(json.dumps({"alg": "HS256", "kid": "k1", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 300,
                              "groups": ["np-seniors"]}).encode())  # fmt: skip
    signature = b64(hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    assert verifier(idp).resolve(f"{header}.{payload}.{signature}") is None


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "Bearer x"])
def test_malformed_tokens_are_rejected(idp, token):
    assert verifier(idp).resolve(token) is None


@pytest.mark.parametrize("sub", ["has space", "x" * 101, "ctl\x07char", ""])
def test_unusable_identity_values_are_rejected(idp, sub):
    assert verifier(idp).resolve(idp.mint(sub=sub)) is None


def test_authenticated_identity_without_a_mapped_role_gets_403_not_viewer(idp):
    with pytest.raises(HTTPException) as exc:
        verifier(idp).resolve(idp.mint(groups=["some-other-group"]))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------- key handling


def test_keys_are_cached_between_requests(idp):
    v = verifier(idp)
    for _ in range(5):
        assert v.resolve(idp.mint()) is not None
    assert idp.fetches == 1


def test_key_rotation_is_picked_up_after_the_refresh_interval(idp):
    now = [1000.0]
    v = verifier(idp, clock=lambda: now[0], min_refresh=30)
    assert v.resolve(idp.mint()) is not None
    new_key = idp.add_key("k2")
    assert v.resolve(idp.mint(kid="k2", key=new_key)) is None  # too soon to refetch: unknown kid
    now[0] += 31
    assert v.resolve(idp.mint(kid="k2", key=new_key)) is not None


def test_unknown_kid_floods_cannot_force_repeated_fetches(idp):
    v = verifier(idp, min_refresh=30)
    v.resolve(idp.mint())
    before = idp.fetches
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for i in range(20):
        v.resolve(idp.mint(kid=f"fake-{i}", key=attacker))
    assert idp.fetches == before


def test_idp_outage_keeps_cached_keys_for_a_while_then_fails_closed(idp):
    now = [1000.0]
    v = verifier(idp, clock=lambda: now[0], jwks_ttl=60, min_refresh=1, max_stale=3600)
    assert v.resolve(idp.mint()) is not None
    idp.down = True
    now[0] += 120  # past the TTL: a refresh is attempted and fails
    assert v.resolve(idp.mint()) is not None
    now[0] += 4000  # past max_stale
    assert v.resolve(idp.mint()) is None


def test_idp_down_from_the_start_fails_closed(idp):
    idp.down = True
    assert verifier(idp).resolve(idp.mint()) is None


def test_discovery_document_for_a_different_issuer_is_refused(idp):
    idp.discovery_issuer = "https://mixed-up.example.test"
    assert verifier(idp).resolve(idp.mint()) is None


def test_discovered_jwks_uri_must_be_https(idp):
    idp.jwks_uri = "http://idp.example.test/keys"
    assert verifier(idp).resolve(idp.mint()) is None


# ---------------------------------------------------------------- configuration


@pytest.mark.parametrize("issuer", ["http://idp.example.test", "ftp://x", "idp.example.test"])
def test_plain_http_issuer_is_refused_except_for_localhost(issuer):
    with pytest.raises(ValueError, match="https"):
        OidcConfig(issuer=issuer, audience=AUDIENCE, role_map=ROLE_MAP)
    OidcConfig(issuer="http://localhost:8080/realms/x", audience=AUDIENCE, role_map=ROLE_MAP)


@pytest.mark.parametrize("spec", ["", "np=boss", "=operator", "np", "np=operator,,x"])
def test_role_map_is_validated(spec):
    with pytest.raises(ValueError):
        parse_role_map(spec)


def test_role_map_parses():
    assert parse_role_map("a=viewer, b=senior_operator") == {"a": "viewer", "b": "senior_operator"}


@pytest.mark.parametrize(
    "env, message",
    [
        ({"NETPULSE_AUTH_MODE": "oidc"}, "needs NETPULSE_OIDC_ISSUER"),
        ({"NETPULSE_AUTH_MODE": "magic"}, "must be 'oidc' or 'tokens'"),
        (
            {
                "NETPULSE_AUTH_MODE": "oidc", "NETPULSE_OIDC_ISSUER": "http://idp.example.test",
                "NETPULSE_OIDC_AUDIENCE": "a", "NETPULSE_OIDC_ROLE_MAP": "g=viewer",
            },
            "https",
        ),
        (
            {
                "NETPULSE_AUTH_MODE": "oidc", "NETPULSE_OIDC_ISSUER": ISSUER,
                "NETPULSE_OIDC_AUDIENCE": "a", "NETPULSE_OIDC_ROLE_MAP": "g=root",
            },
            "role mapping",
        ),
    ],
)  # fmt: skip
def test_api_refuses_to_start_with_incomplete_oidc_config(monkeypatch, tmp_path, env, message):
    from netpulse.api.main import build_app

    for var in list(__import__("os").environ):
        if var.startswith(("NETPULSE_OIDC", "NETPULSE_AUTH")) or var == "NETPULSE_API_TOKENS":
            monkeypatch.delenv(var)
    monkeypatch.setenv("NETPULSE_AUDIT_KEY", "a" * 40)
    monkeypatch.setenv("NETPULSE_DB", str(tmp_path / "x.db"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match=message):
        build_app()


def test_oidc_mode_does_not_need_static_tokens(monkeypatch, tmp_path):
    from netpulse.api.main import build_app

    monkeypatch.delenv("NETPULSE_API_TOKENS", raising=False)
    monkeypatch.setenv("NETPULSE_AUTH_MODE", "oidc")
    monkeypatch.setenv("NETPULSE_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("NETPULSE_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("NETPULSE_OIDC_ROLE_MAP", "np-seniors=senior_operator")
    monkeypatch.setenv("NETPULSE_AUDIT_KEY", "a" * 40)
    monkeypatch.setenv("NETPULSE_DB", str(tmp_path / "x.db"))
    with TestClient(build_app()) as client:
        assert client.get("/health").json()["auth_mode"] == "oidc"


# ---------------------------------------------------------------- through the API


@pytest.fixture
def api(tmp_path, idp):
    service = InvestigationService(shared_deps(), PersistentStore(tmp_path / "o.db", audit_key="k" * 40))
    with TestClient(create_app(service, verifier(idp), max_workers=1)) as client:
        client.service = service
        yield client


def auth(idp, **overrides):
    return {"Authorization": f"Bearer {idp.mint(**overrides)}"}


def test_api_challenges_unauthenticated_and_invalid_callers(api, idp):
    missing = api.get("/incidents/x")
    assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
    assert api.get("/incidents/x", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert api.get("/incidents/x", headers=auth(idp, exp=int(time.time()) - 3600)).status_code == 401


def test_static_looking_tokens_are_not_accepted_in_oidc_mode(api):
    assert api.get("/incidents/x", headers={"Authorization": "Bearer dev-operator-token-0001"}).status_code == 401


def test_roles_come_from_group_claims(api, idp):
    viewer = auth(idp, groups=["np-viewers"])
    assert (
        api.post("/incidents", headers=viewer, json={"submission": CASES["case-02"]["submission"]}).status_code == 403
    )
    assert api.get("/audit/verify", headers=auth(idp, groups=["np-operators"])).status_code == 403
    assert api.get("/audit/verify", headers=auth(idp, groups=["np-seniors"])).status_code == 200
    assert api.get("/incidents/x", headers=auth(idp, groups=["unmapped"])).status_code == 403
    assert api.get("/health").json()["auth_mode"] == "oidc"


def test_audit_records_the_idp_subject_for_submit_and_review(api, idp):
    case = CASES["case-13"]  # critical severity: a senior operator must decide
    operator = auth(idp, sub="oidc|operator-1", groups=["np-operators"])
    senior = auth(idp, sub="oidc|senior-9", groups=["np-seniors"])
    assert api.post("/incidents", headers=operator, json={"submission": case["submission"], "incident_id": "oidc-1"})
    for _ in range(200):
        if api.get("/incidents/oidc-1", headers=operator).json()["status"] == "awaiting_approval":
            break
        time.sleep(0.2)
    # the operator's token cannot approve a senior-only decision, whatever the request body says
    denied = api.post("/incidents/oidc-1/approval", headers=operator, json={"choice": "approve"})
    assert denied.status_code == 403
    assert api.post("/incidents/oidc-1/approval", headers=senior, json={"choice": "approve"}).status_code == 202
    for _ in range(200):
        if api.get("/incidents/oidc-1", headers=senior).json()["status"] == "completed":
            break
        time.sleep(0.2)
    events = [(e["event"], e["actor"]) for e in api.service.audit_log("oidc-1")]
    assert ("submitted", "oidc|operator-1") in events and ("review_submitted", "oidc|senior-9") in events
    assert api.get("/audit/verify", headers=senior).json()["ok"] is True
