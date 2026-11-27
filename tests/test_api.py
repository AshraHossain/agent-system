"""GET /runs/{run_id}, /ready, /readyz, OWASP headers, rate limiting, and boundary guards."""

import logging
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI, Request
from google.adk.sessions import DatabaseSessionService

from opspilot import cli
from opspilot.adk import runner as R
from opspilot.api import (
    ADK_APP_NAME,
    BearerTokenAuth,
    InputBoundaryMiddleware,
    RateLimitMiddleware,
    TelemetryMiddleware,
    create_app,
    runs_router,
)
from opspilot.config import Limits, Settings
from opspilot.contracts.report import InvestigationReport
from opspilot.datasets.spec import get_case

pytestmark = pytest.mark.integration


@pytest.fixture
def stored(settings, tmp_path):
    return replace(settings, session_db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def client(stored):
    app = FastAPI()
    app.include_router(runs_router(stored))
    return _client(app)


async def _seed_adk_session(stored, session_id, user_id="alice", **state):
    service = DatabaseSessionService(db_url=stored.session_db_url)
    await service.create_session(
        app_name=ADK_APP_NAME, user_id=user_id, session_id=session_id, state=state
    )


# --- lookup --------------------------------------------------------------------------


async def test_returns_the_validated_report_of_a_cli_run(client, stored):
    out = await R.run_investigation(get_case("C03")["request"], "C03", settings=stored)
    async with client as c:
        response = await c.get(f"/runs/{out.session_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == out.session_id and body["dataset_id"] == "C03"
    assert body["status"] == "investigated" and body["updated"] > 0
    assert InvestigationReport.model_validate(body["report"]) == out.report


async def test_rejected_request_is_still_retrievable_as_failed(client, stored):
    out = await R.run_investigation("   ", "C02", settings=stored)
    async with client as c:
        body = (await c.get(f"/runs/{out.session_id}")).json()
    assert body["status"] == "failed" and body["report"]["status"] == "failed"


async def test_finds_a_session_created_through_the_adk_api(client, stored):
    out = await R.run_investigation(get_case("C02")["request"], "C02", settings=stored)
    await _seed_adk_session(
        stored,
        "adk-run-1",
        user_id="alice",
        dataset_id="C02",
        final_report=out.report.model_dump(mode="json"),
    )
    async with client as c:
        body = (await c.get("/runs/adk-run-1")).json()
    assert body["run_id"] == "adk-run-1" and body["dataset_id"] == "C02"
    assert InvestigationReport.model_validate(body["report"]) == out.report


async def test_unfinished_run_is_incomplete_without_a_report(client, stored):
    await _seed_adk_session(stored, "adk-run-2", dataset_id="C05")
    async with client as c:
        response = await c.get("/runs/adk-run-2")
    assert response.status_code == 200
    assert response.json() == {
        "run_id": "adk-run-2",
        "dataset_id": "C05",
        "status": "incomplete",
        "updated": response.json()["updated"],
        "report": None,
    }


async def test_unknown_run_is_404(client):
    async with client as c:
        response = await c.get("/runs/does-not-exist")
    assert response.status_code == 404 and "does-not-exist" in response.json()["detail"]


@pytest.mark.parametrize("run_id", ["x" * 129, "has space", "semi;colon", "quote'", "dots.."])
async def test_malformed_run_ids_are_rejected_before_any_lookup(client, run_id):
    async with client as c:
        response = await c.get(f"/runs/{run_id}")
    assert response.status_code == 422


async def test_stored_report_that_no_longer_validates_is_a_500_that_leaks_nothing(
    client, stored, caplog
):
    await _seed_adk_session(stored, "adk-run-3", final_report={"status": "investigated"})
    with caplog.at_level(logging.ERROR, logger="opspilot.api"):
        async with client as c:
            response = await c.get("/runs/adk-run-3")
    assert response.status_code == 500
    assert response.json() == {"detail": "stored report does not match the current schema"}
    assert "adk-run-3" in caplog.text and "investigated" not in caplog.text


async def test_the_route_is_read_only(client):
    async with client as c:
        for method in ("post", "put", "patch", "delete"):
            response = await getattr(c, method)("/runs/anything")
            assert response.status_code == 405, method
        schema = (await c.get("/openapi.json")).json()
    assert list(schema["paths"]["/runs/{run_id}"]) == ["get"]


# --- readiness probes ------------------------------------------------------------------


async def test_readiness_probes_succeed_and_report_503_on_store_failure(stored, monkeypatch):
    app = FastAPI()
    app.include_router(runs_router(stored))
    async with _client(app) as c:
        for path in ("/ready", "/readyz"):
            res = await c.get(path)
            assert res.status_code == 200
            assert res.json() == {
                "status": "ready",
                "session_store": "ok",
                "data_dir_ready": True,
            }

    class BrokenStore:
        async def list_sessions(self, **kw):
            raise RuntimeError("db down")

    monkeypatch.setattr("opspilot.api.session_service", lambda s: BrokenStore())
    broken_app = FastAPI()
    broken_app.include_router(runs_router(stored))
    async with _client(broken_app) as c:
        res = await c.get("/readyz")
        assert res.status_code == 503 and res.json() == {"detail": "session store unavailable"}


# --- served next to ADK's API ----------------------------------------------------------


async def test_create_app_serves_adk_and_the_run_lookup_together(stored):
    out = await R.run_investigation(get_case("C02")["request"], "C02", settings=stored)
    async with _client(create_app(stored)) as c:
        assert ADK_APP_NAME in (await c.get("/list-apps")).json()
        body = (await c.get(f"/runs/{out.session_id}")).json()
    assert body["status"] == out.report.status.value


# --- CLI ---------------------------------------------------------------------------------


def test_serve_command_runs_uvicorn_on_loopback_by_default(monkeypatch, capsys):
    import uvicorn

    sentinel, calls = object(), []
    monkeypatch.setattr("opspilot.api.create_app", lambda settings, bind_host=None: sentinel)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    assert cli.main(["serve", "--port", "9001"]) == 0
    assert calls == [(sentinel, {"host": "127.0.0.1", "port": 9001})]
    assert capsys.readouterr().err == ""


def test_serve_refuses_unauthenticated_beyond_loopback(monkeypatch, capsys):
    import uvicorn

    calls = []
    monkeypatch.delenv("OPSPILOT_API_TOKEN", raising=False)
    monkeypatch.delenv("OPSPILOT_API_TOKEN_SHA256", raising=False)
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("opspilot.api.create_app", lambda settings, bind_host=None: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 2
    assert "refusing" in capsys.readouterr().err and calls == []


def test_serve_beyond_loopback_with_explicit_opt_out_warns(monkeypatch, capsys):
    import uvicorn

    monkeypatch.delenv("OPSPILOT_API_TOKEN", raising=False)
    monkeypatch.delenv("OPSPILOT_API_TOKEN_SHA256", raising=False)
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("opspilot.api.create_app", lambda settings, bind_host=None: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    assert cli.main(["serve", "--host", "0.0.0.0", "--allow-unauthenticated"]) == 0
    assert "no authentication" in capsys.readouterr().err


def test_serve_beyond_loopback_with_token(monkeypatch, capsys):
    import uvicorn

    monkeypatch.setenv("OPSPILOT_API_TOKEN", TOKEN)
    monkeypatch.setattr("opspilot.api.create_app", lambda settings, bind_host=None: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 0
    assert "bearer token required" in capsys.readouterr().err


# --- bearer token & X-API-Key ----------------------------------------------------------

TOKEN = "t" * 40


async def test_token_protects_adk_routes_and_run_lookup_via_bearer_or_api_key(stored):
    app = create_app(replace(stored, api_token=TOKEN))
    async with _client(app) as c:
        for path in ("/list-apps", "/runs/abc"):
            missing = await c.get(path)
            assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
            wrong = await c.get(path, headers={"Authorization": "Bearer " + "x" * 40})
            assert wrong.status_code == 401
            wrong_key = await c.get(path, headers={"X-API-Key": "x" * 40})
            assert wrong_key.status_code == 401
        ok = await c.get("/list-apps", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200 and ADK_APP_NAME in ok.json()
        ok_key = await c.get("/list-apps", headers={"X-API-Key": TOKEN})
        assert ok_key.status_code == 200 and ADK_APP_NAME in ok_key.json()
        lookup = await c.get("/runs/abc", headers={"Authorization": f"Bearer {TOKEN}"})
        assert lookup.status_code == 404  # past auth, then a normal "no such run"
        # Probes stay unauthenticated even when auth is enabled:
        for probe in ("/health", "/ready", "/readyz"):
            assert (await c.get(probe)).status_code == 200


async def test_websockets_are_rejected_without_token():
    sent = []

    async def app(scope, receive, send):  # pragma: no cover - must not be reached
        raise AssertionError("inner app reached without a token")

    async def send(message):
        sent.append(message)

    mw = BearerTokenAuth(app, TOKEN)
    await mw({"type": "websocket", "headers": []}, None, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_short_or_empty_token_settings_and_api_auth_token(monkeypatch):
    monkeypatch.setenv("OPSPILOT_API_TOKEN", "  ")
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    assert Settings.from_env().api_token is None
    monkeypatch.setenv("API_AUTH_TOKEN", "my-api-auth-token")
    assert Settings.from_env().api_token == "my-api-auth-token"
    monkeypatch.setenv("OPSPILOT_API_TOKEN", "short")
    with pytest.raises(ValueError, match="at least"):
        Settings.from_env()
    with pytest.raises(ValueError, match="rate_limit_per_minute"):
        Limits(rate_limit_per_minute=-1)
    with pytest.raises(ValueError, match="max_payload_bytes"):
        Limits(max_payload_bytes=0)


# --- OWASP headers, rate limiting, and boundary guards (400 / 413 / 415 / 429) --------


def _guarded_test_app(*, rate_limit: int = 3, max_bytes: int = 64) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/items")
    async def get_items():
        return {"items": []}

    @app.post("/items")
    async def post_item(request: Request):
        data = await request.json()
        return {"created": data}

    app.add_middleware(InputBoundaryMiddleware, max_payload_bytes=max_bytes)
    app.add_middleware(RateLimitMiddleware, limit_per_minute=rate_limit)
    app.add_middleware(TelemetryMiddleware)
    return app


async def test_owasp_security_and_telemetry_headers_attached_to_responses():
    app = _guarded_test_app()
    async with _client(app) as c:
        res = await c.get("/items", headers={"X-Request-ID": "trace-abc-123"})
        assert res.status_code == 200
        assert res.headers["x-request-id"] == "trace-abc-123"
        assert float(res.headers["x-process-time-ms"]) >= 0.0
        assert float(res.headers["x-response-time-ms"]) >= 0.0
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["x-frame-options"] == "DENY"
        assert res.headers["referrer-policy"] == "no-referrer"
        assert res.headers["cache-control"] == "no-store"
        assert (
            res.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
        )
        # Malformed incoming X-Request-ID is replaced with a fresh generated ID
        bad_id = await c.get("/items", headers={"X-Request-ID": "bad id with spaces"})
        assert bad_id.headers["x-request-id"].startswith("req-")


async def test_rate_limit_middleware_returns_429_with_retry_after():
    app = _guarded_test_app(rate_limit=2)
    async with _client(app) as c:
        assert (await c.get("/items")).status_code == 200
        assert (await c.get("/items")).status_code == 200
        limited = await c.get("/items")
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"
        assert limited.headers["x-content-type-options"] == "nosniff"
        assert limited.json() == {"detail": "rate limit exceeded"}
        # Container health probe is never rate-limited
        assert (await c.get("/health")).status_code == 200


def _stacked_app(stored, *, rate_limit: int, max_bytes: int):
    limits = Limits(rate_limit_per_minute=rate_limit, max_payload_bytes=max_bytes)
    return create_app(replace(stored, api_token=TOKEN, limits=limits))


async def test_create_app_rate_limits_before_auth(stored):
    """ADR-0014 order: failed auth attempts still count toward a client's rate limit."""
    async with _client(_stacked_app(stored, rate_limit=2, max_bytes=1024)) as c:
        responses = [await c.get("/list-apps") for _ in range(3)]
    assert [r.status_code for r in responses] == [401, 401, 429]
    assert all(r.headers["x-request-id"] for r in responses)


async def test_create_app_authenticates_before_input_boundary(stored):
    """ADR-0014 order: an unauthenticated caller gets 401, never the size check's 413."""
    body = b'{"pad": "' + b"x" * 64 + b'"}'
    json_header = {"content-type": "application/json"}
    async with _client(_stacked_app(stored, rate_limit=10, max_bytes=32)) as c:
        anonymous = await c.post("/run", content=body, headers=json_header)
        authed = await c.post(
            "/run", content=body, headers={**json_header, "Authorization": f"Bearer {TOKEN}"}
        )
    assert anonymous.status_code == 401
    assert authed.status_code == 413
    assert anonymous.headers["x-request-id"] and authed.headers["x-request-id"]


async def test_input_boundary_middleware_enforces_400_413_415():
    app = _guarded_test_app(rate_limit=100, max_bytes=32)
    async with _client(app) as c:
        # Valid JSON POST within byte cap succeeds
        ok = await c.post("/items", json={"a": 1})
        assert ok.status_code == 200 and ok.json() == {"created": {"a": 1}}

        # 415 Unsupported Media Type on disallowed Content-Type
        bad_mime = await c.post(
            "/items", content=b"<xml/>", headers={"Content-Type": "application/xml"}
        )
        assert bad_mime.status_code == 415
        assert "unsupported media type" in bad_mime.json()["detail"]

        # 413 Content Too Large when body exceeds max_bytes
        too_big = await c.post("/items", json={"payload": "x" * 100})
        assert too_big.status_code == 413
        assert "exceeds limit" in too_big.json()["detail"]

        # 400 Bad Request on path traversal attempt in raw URL path
        traversal = await c.get("/items/%2e%2e/secret")
        assert traversal.status_code == 400
        assert traversal.json() == {"detail": "invalid request path"}


async def test_input_boundary_handles_chunked_oversize_and_bad_content_length():
    app = _guarded_test_app(rate_limit=100, max_bytes=16)
    sent_messages: list[dict] = []

    async def send(msg):
        sent_messages.append(msg)

    # Non-integer Content-Length -> 400
    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/items",
            "raw_path": b"/items",
            "headers": [(b"content-length", b"not-a-number")],
        },
        None,
        send,
    )
    assert sent_messages[0]["status"] == 400

    # Streaming body without Content-Length exceeding max_bytes -> 413
    sent_messages.clear()
    chunks = iter(
        [
            {"type": "http.request", "body": b'{"a":"' + b"x" * 32 + b'"}', "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/items",
            "raw_path": b"/items",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
        send,
    )
    assert sent_messages[0]["status"] == 413

    # Mutation body with missing Content-Type header -> 415
    sent_messages.clear()
    chunks2 = iter([{"type": "http.request", "body": b"raw", "more_body": False}])

    async def receive2():
        return next(chunks2)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/items",
            "raw_path": b"/items",
            "query_string": b"",
            "headers": [],
        },
        receive2,
        send,
    )
    assert sent_messages[0]["status"] == 415
