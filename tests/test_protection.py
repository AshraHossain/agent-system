"""Transport and abuse protections: rate limits, body cap, security headers, HTTPS and Host enforcement."""

import pytest
from fastapi.testclient import TestClient

from netpulse.api.app import create_app
from netpulse.api.auth import TokenStore
from netpulse.api.protection import MAX_TRACKED_KEYS, Limits, Protection, RateLimiter
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService
from tests.graph_helpers import CASES, shared_deps

OP = {"Authorization": "Bearer op-token-0123456789"}
OP2 = {"Authorization": "Bearer op2-token-012345678"}
BAD = {"Authorization": "Bearer definitely-not-a-valid-token"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------- the token bucket


def test_bucket_allows_a_burst_then_refills_over_time():
    clock = Clock()
    bucket = RateLimiter(per_min=60, burst=3, clock=clock)  # 1 token per second
    assert [bucket.consume("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = bucket.consume("a")
    assert 0.9 < wait <= 1.0
    clock.now += 1
    assert bucket.consume("a") == 0.0
    assert bucket.consume("a") > 0


def test_buckets_are_independent_per_key_and_never_exceed_the_burst():
    clock = Clock()
    bucket = RateLimiter(per_min=60, burst=2, clock=clock)
    bucket.consume("a"), bucket.consume("a")
    assert bucket.consume("a") > 0 and bucket.consume("b") == 0.0
    clock.now += 3600  # a long idle period must not bank more than the burst
    assert [bucket.consume("a") for _ in range(3)] == [0.0, 0.0, pytest.approx(1.0, abs=0.01)]


def test_wait_needed_does_not_take_tokens():
    bucket = RateLimiter(per_min=60, burst=1, clock=Clock())
    assert bucket.wait_needed("a") == 0.0 and bucket.wait_needed("a") == 0.0
    assert bucket.consume("a") == 0.0 and bucket.wait_needed("a") > 0


def test_tracked_keys_are_bounded_so_an_attacker_cannot_exhaust_memory():
    bucket = RateLimiter(per_min=60, burst=1, clock=Clock())
    for i in range(MAX_TRACKED_KEYS + 500):
        bucket.consume(f"ip-{i}")
    assert len(bucket._buckets) == MAX_TRACKED_KEYS


# ---------------------------------------------------------------- through the app


def drain(client: TestClient) -> None:
    """Let accepted background investigations finish before the app and its database are torn down."""
    assert client.app.state.queue.wait_idle(120)


def make_client(tmp_path, clock=None, base_url="http://testserver", **config):
    service = InvestigationService(shared_deps(), PersistentStore(tmp_path / "p.db", audit_key="k" * 40))
    tokens = TokenStore.parse("op-token-0123456789:operator:olga,op2-token-012345678:operator:omar")
    protection = Protection(clock=clock or Clock(), **config)
    app = create_app(service, tokens, max_workers=1, protection=protection)
    return TestClient(app, base_url=base_url)


def test_ip_rate_limit_returns_429_with_retry_after_and_recovers(tmp_path):
    clock = Clock()
    with make_client(tmp_path, clock, ip_per_min=60, ip_burst=5) as c:
        codes = [c.get("/incidents/x", headers=OP).status_code for _ in range(8)]
        assert codes[:5] == [404] * 5 and codes[5:] == [429] * 3
        blocked = c.get("/incidents/x", headers=OP)
        assert int(blocked.headers["retry-after"]) >= 1 and blocked.json()["detail"] == "rate limit exceeded"
        clock.now += 5
        assert c.get("/incidents/x", headers=OP).status_code == 404


def test_health_is_exempt_from_every_limit(tmp_path):
    with make_client(tmp_path, ip_per_min=60, ip_burst=1, require_https=True, allowed_hosts=("api.example.test",)) as c:
        assert all(c.get("/health").status_code == 200 for _ in range(10))


def test_repeated_authentication_failures_lock_the_address_out_even_for_valid_tokens(tmp_path):
    clock = Clock()
    with make_client(tmp_path, clock, auth_fail_per_min=6, auth_fail_burst=3) as c:
        assert [c.get("/incidents/x", headers=BAD).status_code for _ in range(3)] == [401, 401, 401]
        locked = c.get("/incidents/x", headers=OP)  # a valid token from the same address is refused too
        assert locked.status_code == 429 and locked.json()["detail"] == "too many failed authentications"
        clock.now += 11  # six per minute: one token every ten seconds
        assert c.get("/incidents/x", headers=OP).status_code == 404


def test_authorization_failures_do_not_count_toward_lockout(tmp_path):
    with make_client(tmp_path, auth_fail_per_min=6, auth_fail_burst=2) as c:
        for _ in range(6):
            assert c.get("/audit/verify", headers=OP).status_code == 403  # authenticated, not allowed
        assert c.get("/incidents/x", headers=OP).status_code == 404


def test_write_limit_is_per_principal_and_costs_only_mutating_calls(tmp_path):
    submission = CASES["case-02"]["submission"]
    with make_client(tmp_path, write_per_min=6, write_burst=2) as c:
        first = [c.post("/incidents", headers=OP, json={"submission": submission}).status_code for _ in range(3)]
        assert first[:2] == [202, 202] and first[2] == 429
        assert c.post("/incidents", headers=OP2, json={"submission": submission}).status_code == 202  # other user
        assert c.get("/incidents/x", headers=OP).status_code == 404  # reads use a different bucket
        drain(c)


def test_read_limit_is_per_principal(tmp_path):
    with make_client(tmp_path, read_per_min=6, read_burst=2) as c:
        assert [c.get("/incidents/x", headers=OP).status_code for _ in range(3)] == [404, 404, 429]
        assert c.get("/incidents/x", headers=OP2).status_code == 404


def test_zero_disables_a_limit(tmp_path):
    with make_client(tmp_path, ip_per_min=0, read_per_min=0, auth_fail_per_min=0) as c:
        assert all(c.get("/incidents/x", headers=OP).status_code == 404 for _ in range(50))
        assert all(c.get("/incidents/x", headers=BAD).status_code == 401 for _ in range(30))


# ---------------------------------------------------------------- request size


def test_oversized_declared_body_is_refused_before_it_is_read(tmp_path):
    with make_client(tmp_path, max_body_bytes=1000) as c:
        response = c.post("/incidents", headers=OP, content=b"x" * 5000)
        assert response.status_code == 413 and response.json()["detail"] == "request body too large"


def test_chunked_body_without_content_length_is_capped_while_streaming(tmp_path):
    def chunks():
        for _ in range(50):
            yield b"x" * 100

    with make_client(tmp_path, max_body_bytes=1000) as c:
        response = c.post("/incidents", headers=OP, content=chunks())
        assert response.status_code == 413


def test_normal_sized_requests_still_work(tmp_path):
    with make_client(tmp_path, max_body_bytes=64 * 1024) as c:
        r = c.post("/incidents", headers=OP, json={"submission": CASES["case-02"]["submission"]})
        assert r.status_code == 202
        drain(c)


# ---------------------------------------------------------------- headers, https, host


def test_security_headers_are_set_on_success_and_error_responses(tmp_path):
    with make_client(tmp_path) as c:
        for response in (c.get("/health"), c.get("/incidents/x", headers=OP), c.get("/incidents/x")):
            h = response.headers
            assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
            assert h["cache-control"] == "no-store" and h["referrer-policy"] == "no-referrer"
            assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_hsts_is_sent_only_over_https(tmp_path):
    with make_client(tmp_path) as plain:
        assert "strict-transport-security" not in plain.get("/health").headers
    with make_client(tmp_path, base_url="https://testserver") as secure:
        assert "max-age=31536000" in secure.get("/health").headers["strict-transport-security"]


def test_require_https_refuses_plain_http_but_not_health(tmp_path):
    with make_client(tmp_path, require_https=True) as plain:
        refused = plain.get("/incidents/x", headers=OP)
        assert refused.status_code == 400 and refused.json()["detail"] == "https is required"
        assert plain.get("/health").status_code == 200
    with make_client(tmp_path, base_url="https://testserver", require_https=True) as secure:
        assert secure.get("/incidents/x", headers=OP).status_code == 404


def test_https_refusal_happens_before_credentials_are_examined(tmp_path):
    with make_client(tmp_path, require_https=True, auth_fail_per_min=60, auth_fail_burst=1) as c:
        assert [c.get("/incidents/x", headers=BAD).status_code for _ in range(3)] == [400, 400, 400]


def test_allowed_hosts_rejects_other_host_headers(tmp_path):
    with make_client(tmp_path, allowed_hosts=("api.example.test",)) as c:
        assert c.get("/incidents/x", headers={**OP, "Host": "evil.example.test"}).status_code == 400
        assert c.get("/incidents/x", headers={**OP, "Host": "API.example.test:8443"}).status_code == 404


def test_docs_page_keeps_working_csp_exemption(tmp_path):
    with make_client(tmp_path) as c:
        assert "content-security-policy" not in c.get("/docs").headers
        assert "content-security-policy" in c.get("/openapi.json").headers


# ---------------------------------------------------------------- configuration


def test_protection_reads_environment_and_rejects_bad_values(monkeypatch):
    monkeypatch.setenv("NETPULSE_RATE_LIMIT_WRITE_PER_MIN", "7")
    monkeypatch.setenv("NETPULSE_ALLOWED_HOSTS", "a.example.test, b.example.test")
    monkeypatch.setenv("NETPULSE_MAX_BODY_BYTES", "1024")
    p = Protection.from_env(require_https_default=True)
    assert (p.write_per_min, p.max_body_bytes, p.require_https) == (7, 1024, True)
    assert p.allowed_hosts == ("a.example.test", "b.example.test")
    monkeypatch.setenv("NETPULSE_REQUIRE_HTTPS", "false")
    assert Protection.from_env(require_https_default=True).require_https is False
    monkeypatch.setenv("NETPULSE_RATE_LIMIT_IP_PER_MIN", "-5")
    with pytest.raises(ValueError):
        Protection.from_env(require_https_default=False)


def test_oidc_mode_requires_https_by_default_and_tokens_mode_does_not(monkeypatch, tmp_path):
    from netpulse.api.main import build_app

    for var in ("NETPULSE_REQUIRE_HTTPS", "NETPULSE_API_TOKENS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NETPULSE_AUDIT_KEY", "a" * 40)
    monkeypatch.setenv("NETPULSE_DB", str(tmp_path / "x.db"))
    monkeypatch.setenv("NETPULSE_API_TOKENS", "0123456789abcdef-token:operator:olga")
    assert build_app().state.limits.config.require_https is False
    monkeypatch.setenv("NETPULSE_AUTH_MODE", "oidc")
    monkeypatch.setenv("NETPULSE_OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("NETPULSE_OIDC_AUDIENCE", "a")
    monkeypatch.setenv("NETPULSE_OIDC_ROLE_MAP", "g=viewer")
    assert build_app().state.limits.config.require_https is True


def test_limits_object_builds_only_the_enabled_limiters():
    limits = Limits(Protection(ip_per_min=0, write_per_min=5))
    assert limits.ip is None and limits.write is not None
