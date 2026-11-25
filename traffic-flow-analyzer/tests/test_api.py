"""Tests for the FastAPI query layer."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import trafficflow.core.forecast as forecast_module
from trafficflow.api import build_app
from trafficflow.data.feed import SyntheticFeed


@pytest.fixture(scope="module")
def client():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)  # move into middle of data
    app = build_app(feed)
    return TestClient(app)


def test_list_segments(client):
    r = client.get("/segments")
    assert r.status_code == 200
    segs = r.json()
    assert isinstance(segs, list)
    assert len(segs) >= 1
    first = segs[0]
    assert "id" in first
    assert "free_flow_kmh" in first


def test_get_status(client):
    r = client.get("/status")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert data["status"] in ("normal", "watch", "degraded")
    assert "segment_levels" in data
    assert "as_of" in data


def test_get_forecast(client):
    r = client.get("/forecast/S1")
    assert r.status_code == 200
    fc = r.json()
    assert fc["segment_id"] == "S1"
    assert fc["method"] != ""
    assert len(fc["points"]) > 0
    p = fc["points"][0]
    assert "speed_kmh" in p
    assert "level" in p


def test_get_forecast_custom_horizon(client):
    r = client.get("/forecast/S1?horizon=30")
    assert r.status_code == 200
    fc = r.json()
    assert fc["horizon_minutes"] == 30
    assert len(fc["points"]) == 6  # 30 min / 5 min


def test_get_forecast_horizon_at_cap(client):
    r = client.get("/forecast/S1?horizon=180")
    assert r.status_code == 200
    assert len(r.json()["points"]) == 36


def test_get_forecast_horizon_above_cap_is_rejected(client):
    r = client.get("/forecast/S1?horizon=10000000")
    assert r.status_code == 422


def test_get_forecast_unknown_segment(client):
    r = client.get("/forecast/DOES_NOT_EXIST")
    assert r.status_code == 404


def test_get_history(client):
    r = client.get("/history/S1?days=1")
    assert r.status_code == 200
    h = r.json()
    assert h["segment_id"] == "S1"
    assert len(h["readings"]) > 0
    assert "ts" in h["readings"][0]
    assert "speed_kmh" in h["readings"][0]


def test_get_history_unknown_segment(client):
    r = client.get("/history/DOES_NOT_EXIST")
    assert r.status_code == 404


def test_openapi_schema(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert "paths" in schema
    assert "/segments" in schema["paths"]
    assert "/forecast/{segment_id}" in schema["paths"]


def test_get_path(client):
    r = client.get("/path/S1/S6")
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "S1"
    assert data["target"] == "S6"
    assert data["path"][0] == "S1"
    assert data["path"][-1] == "S6"
    assert data["total_km"] > 0
    assert data["travel_minutes"] > 0
    assert "bottleneck" in data


def test_get_path_unknown_source(client):
    r = client.get("/path/UNKNOWN/S1")
    assert r.status_code == 404


def test_get_path_unknown_target(client):
    r = client.get("/path/S1/UNKNOWN")
    assert r.status_code == 404


def test_stream_endpoint_in_openapi(client):
    """The /stream endpoint appears in the OpenAPI schema."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/stream" in r.json()["paths"]


def test_ask_with_history(client):
    history = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]
    r = client.post("/ask", json={"question": "And now?", "history": history})
    assert r.status_code == 200
    assert r.json()["answer"]


@pytest.mark.parametrize("item", [{"content": "hi"}, {"role": "user"}, {}])
def test_ask_incomplete_history_item_is_rejected(client, item):
    r = client.post("/ask", json={"question": "ok?", "history": [item]})
    assert r.status_code == 422


@pytest.mark.parametrize("horizon", [0, -5, 7])
def test_get_forecast_horizon_must_be_whole_steps(client, horizon):
    r = client.get(f"/forecast/S1?horizon={horizon}")
    assert r.status_code == 422


@pytest.mark.parametrize("days", [0, -1, 31])
def test_get_history_days_out_of_range(client, days):
    r = client.get(f"/history/S1?days={days}")
    assert r.status_code == 422


def test_get_history_timestamps_are_iso(client):
    ts = client.get("/history/S1?days=0.05").json()["readings"][0]["ts"]
    assert "T" in ts


def test_get_path_unknown_weight(client):
    r = client.get("/path/S1/S4?weight=bogus")
    assert r.status_code == 422


def test_openapi_documents_404s_and_local_times(client):
    spec = client.get("/openapi.json").json()
    for path in ("/forecast/{segment_id}", "/history/{segment_id}", "/path/{source}/{target}"):
        assert "404" in spec["paths"][path]["get"]["responses"]
    as_of = spec["components"]["schemas"]["StatusOut"]["properties"]["as_of"]
    assert "format" not in as_of  # no RFC 3339 promise: the values carry no offset


def test_ask_unreadable_body_is_a_documented_400(client):
    r = client.post("/ask", content=b":\xff\xfe", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert "400" in client.get("/openapi.json").json()["paths"]["/ask"]["post"]["responses"]


SECRET = "db at /srv/secret/feed.sqlite refused password=hunter2"


class _BrokenFeed(SyntheticFeed):
    def history(self, segment_id, days):
        raise RuntimeError(SECRET)


def _assert_opaque_500(r, caplog):
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert SECRET not in r.text
    assert detail.startswith("Internal error (ref ")
    ref = detail.removeprefix("Internal error (ref ").removesuffix(")")
    logged = [rec for rec in caplog.records if ref in rec.getMessage()]
    assert logged and SECRET in str(logged[0].exc_info[1])


def test_status_500_does_not_echo_the_error(caplog):
    client = TestClient(build_app(_BrokenFeed(days=21)))
    _assert_opaque_500(client.get("/status"), caplog)


def test_ask_500_does_not_echo_the_error(client, monkeypatch, caplog):
    async def broken_ask(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr("trafficflow.agents.pipeline.ask", broken_ask)
    _assert_opaque_500(client.post("/ask", json={"question": "hi"}), caplog)


def test_500s_are_documented(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "500" in paths["/status"]["get"]["responses"]
    assert "500" in paths["/ask"]["post"]["responses"]


def test_root_redirects_to_docs(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/docs"


def test_root_is_not_in_the_openapi_schema(client):
    assert "/" not in client.get("/openapi.json").json()["paths"]


# -- bearer-token auth (off by default; on when a token is configured) -------

TOKEN = "s3cret-test-token"


@pytest.fixture(scope="module")
def auth_client():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    return TestClient(build_app(feed, api_token=TOKEN))


def test_auth_off_by_default(client):
    assert client.get("/segments").status_code == 200


def test_auth_rejects_missing_token(auth_client):
    r = auth_client.get("/segments")
    assert r.status_code == 401
    assert "www-authenticate" in r.headers


def test_auth_rejects_wrong_token(auth_client):
    r = auth_client.get("/segments", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_auth_rejects_malformed_header(auth_client):
    r = auth_client.get("/segments", headers={"Authorization": TOKEN})  # no "Bearer "
    assert r.status_code == 401


def test_auth_accepts_the_configured_token(auth_client):
    r = auth_client.get("/segments", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_auth_accepts_the_configured_token_via_x_api_key(auth_client):
    r = auth_client.get("/segments", headers={"X-API-Key": TOKEN})
    assert r.status_code == 200


def test_auth_refuses_both_bearer_and_api_key_headers_at_once(auth_client):
    r = auth_client.get(
        "/segments", headers={"Authorization": f"Bearer {TOKEN}", "X-API-Key": TOKEN}
    )
    assert r.status_code == 401


def test_auth_covers_docs_and_openapi(auth_client):
    assert auth_client.get("/docs").status_code == 401
    assert auth_client.get("/openapi.json").status_code == 401


def test_auth_401_does_not_echo_the_configured_token(auth_client):
    r = auth_client.get("/segments")
    assert TOKEN not in r.text


# -- sliding-window rate limiting (off by default; on when configured) ------


def test_rate_limit_returns_429_with_retry_after_once_exceeded(monkeypatch):
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    client = TestClient(build_app(feed, rate_limit_per_minute=2))
    assert client.get("/segments").status_code == 200
    assert client.get("/segments").status_code == 200
    r = client.get("/segments")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60"


def test_rate_limit_is_disabled_by_default(client):
    for _ in range(10):
        assert client.get("/segments").status_code == 200


# -- payload-size guard (always on) -----------------------------------------


def test_oversized_post_body_is_rejected_with_413(client):
    big_question = "x" * (65 * 1024)  # just over the 64 KiB cap
    r = client.post("/ask", json={"question": big_question})
    assert r.status_code == 413


def test_oversized_body_is_rejected_before_auth_runs():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    client = TestClient(build_app(feed, api_token=TOKEN))
    big_question = "x" * (65 * 1024)
    r = client.post("/ask", json={"question": big_question})  # no Authorization header
    assert r.status_code == 413  # not 401 - size guard ran before auth


# -- combined middleware stack (final whole-branch review regression) ------
#
# Pins the real middleware depth, outermost to innermost: TelemetryMiddleware
# -> PayloadSizeGuard -> RateLimitMiddleware -> BearerTokenAuth -> router.


def test_combined_auth_and_rate_limit_and_payload_guard_stack_together():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    combined_client = TestClient(build_app(feed, api_token=TOKEN, rate_limit_per_minute=2))

    r1 = combined_client.get("/segments")  # no credentials
    assert r1.status_code == 401
    assert "x-request-id" in r1.headers

    r2 = combined_client.get("/segments")  # no credentials
    assert r2.status_code == 401
    assert "x-request-id" in r2.headers

    # The two failed-auth attempts above counted against the rate-limit budget,
    # because rate limiting runs before auth: proves it did not stay 401 forever.
    r3 = combined_client.get("/segments")
    assert r3.status_code == 429
    assert "x-request-id" in r3.headers

    # Fresh app/client so this request's rate-limit window is untouched by the
    # GETs above; pins that the payload guard runs regardless of auth/rate-limit
    # state (it sits outside both, just inside TelemetryMiddleware).
    fresh_client = TestClient(build_app(feed, api_token=TOKEN, rate_limit_per_minute=2))
    big_question = "x" * (65 * 1024)
    r4 = fresh_client.post("/ask", json={"question": big_question})  # no credentials
    assert r4.status_code == 413
    assert "x-request-id" in r4.headers


# -- OWASP + telemetry response headers (always on) ------------------------

EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}


def test_every_response_carries_owasp_and_telemetry_headers(client):
    r = client.get("/segments")
    assert "x-request-id" in r.headers
    assert "x-process-time-ms" in r.headers
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers[name] == value


def test_docs_and_openapi_omit_the_strict_csp_but_keep_other_headers(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        assert "content-security-policy" not in r.headers
        assert r.headers["x-content-type-options"] == "nosniff"


def test_a_429_response_still_carries_telemetry_headers():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    client = TestClient(build_app(feed, rate_limit_per_minute=1))
    client.get("/segments")
    r = client.get("/segments")
    assert r.status_code == 429
    assert "x-request-id" in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


# -- forecast cache (recompute only when the feed has new data) ---------------


@pytest.fixture
def counted(monkeypatch):
    """Count forecaster runs for apps built after this fixture."""
    calls = []
    real = forecast_module.forecast_segment

    def counting(*args, **kwargs):
        calls.append(args[1].id)
        return real(*args, **kwargs)

    monkeypatch.setattr(forecast_module, "forecast_segment", counting)
    return calls


def _fresh_feed():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    return feed


def test_repeated_status_runs_the_forecaster_once_per_segment(counted):
    feed = _fresh_feed()
    client = TestClient(build_app(feed))
    n_segments = len(feed.segments())
    assert client.get("/status").status_code == 200
    assert client.get("/status").status_code == 200
    assert len(counted) == n_segments


def test_forecast_at_60_minutes_reuses_the_status_entry(counted):
    feed = _fresh_feed()
    client = TestClient(build_app(feed))
    client.get("/status")
    before = len(counted)
    assert client.get("/forecast/S1?horizon=60").status_code == 200
    assert len(counted) == before


def test_status_responses_match_an_uncached_app():
    cached = TestClient(build_app(_fresh_feed()))
    first = cached.get("/status").json()
    second = cached.get("/status").json()
    oracle = TestClient(build_app(_fresh_feed())).get("/status").json()
    assert first == second == oracle
    f_cached = [cached.get("/forecast/S1?horizon=30").json() for _ in range(2)]
    f_oracle = TestClient(build_app(_fresh_feed())).get("/forecast/S1?horizon=30").json()
    assert f_cached[0] == f_cached[1] == f_oracle


def test_status_recomputes_after_feed_advances(counted):
    feed = _fresh_feed()
    client = TestClient(build_app(feed))
    n_segments = len(feed.segments())
    before_status = client.get("/status").json()
    before_fc = client.get("/forecast/S1").json()
    feed.advance(1)
    after_status = client.get("/status").json()
    after_fc = client.get("/forecast/S1").json()
    assert after_status["as_of"] > before_status["as_of"]
    assert after_fc["points"][0]["ts"] > before_fc["points"][0]["ts"]
    assert len(counted) == 2 * n_segments


def test_forecast_error_is_not_cached(monkeypatch):
    calls = []

    def failing(*args, **kwargs):
        calls.append(1)
        raise ValueError("need at least 288 readings, got 3")

    monkeypatch.setattr(forecast_module, "forecast_segment", failing)
    client = TestClient(build_app(_fresh_feed()))
    for _ in range(2):
        assert client.get("/forecast/S1").status_code == 422
    assert len(calls) == 2


def test_each_app_has_its_own_cache(counted):
    feed_a, feed_b = _fresh_feed(), _fresh_feed()
    n_segments = len(feed_a.segments())
    TestClient(build_app(feed_a)).get("/status")
    TestClient(build_app(feed_b)).get("/status")  # identical data, but a separate app
    assert len(counted) == 2 * n_segments


def test_forecast_at_another_horizon_does_not_reuse_the_status_entry(counted):
    feed = _fresh_feed()
    client = TestClient(build_app(feed))
    client.get("/status")
    before = len(counted)
    body = client.get("/forecast/S1?horizon=30").json()
    assert body["horizon_minutes"] == 30
    assert len(counted) == before + 1
    oracle = TestClient(build_app(_fresh_feed())).get("/forecast/S1?horizon=30").json()
    assert body == oracle
