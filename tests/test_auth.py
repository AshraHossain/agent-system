"""Bearer-token authentication for the shared-use API (ADR-0013)."""

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from opspilot import cli
from opspilot.api import BearerTokenAuth, create_app
from opspilot.config import Settings
from opspilot.core import auth
from opspilot.core.auth import hash_token, new_token, parse_bearer, parse_digests, verify_token

TOKEN = new_token()
DIGEST = hash_token(TOKEN)
UNAUTHORIZED = {"detail": "missing or invalid bearer token"}


# --- primitives ------------------------------------------------------------


def test_tokens_are_long_random_and_digests_are_sha256_hex():
    assert len(TOKEN) >= 43 and new_token() != new_token()
    assert re.fullmatch(r"[0-9a-f]{64}", DIGEST) and hash_token(TOKEN) == DIGEST
    assert hash_token("a") != hash_token("b")


@pytest.mark.parametrize("raw", [None, "", "  ", ",,"])
def test_no_digests_means_no_authentication(raw):
    assert parse_digests(raw) == ()


def test_digests_accept_commas_whitespace_and_either_case_and_drop_duplicates():
    other = hash_token("other")
    parsed = parse_digests(f" {DIGEST.upper()},{other}\n{DIGEST} ")
    assert parsed == (DIGEST, other)


@pytest.mark.parametrize("bad", ["abc", "g" * 64, "a" * 63, "a" * 65, hash_token("x") + "z"])
def test_a_malformed_digest_is_rejected_without_echoing_it(bad):
    with pytest.raises(ValueError, match="entry 1 is not a SHA-256 hex digest") as err:
        parse_digests(bad)
    assert bad not in str(err.value)


def test_the_position_of_the_bad_entry_is_reported():
    with pytest.raises(ValueError, match="entry 2"):
        parse_digests(f"{DIGEST},nope")


def test_verify_accepts_any_configured_digest_and_nothing_else():
    other = new_token()
    digests = (DIGEST, hash_token(other))
    assert verify_token(TOKEN, digests) and verify_token(other, digests)
    assert not verify_token("wrong", digests) and not verify_token("", digests)
    assert not verify_token(TOKEN.upper(), digests) and not verify_token(TOKEN, ())


def test_verify_compares_every_digest_without_an_early_exit(monkeypatch):
    calls = []
    real = auth.hmac.compare_digest
    monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    assert verify_token(TOKEN, (DIGEST, hash_token("b"), hash_token("c")))  # matches the first
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["Bearer abc"], "abc"),
        (["bearer abc"], "abc"),
        (["BEARER abc"], "abc"),
        ([], None),
        ([""], None),
        (["Bearer"], None),
        (["Bearer "], None),
        (["Bearer  abc"], None),
        (["Bearer a b"], None),
        (["Basic abc"], None),
        (["Token abc"], None),
        (["abc"], None),
        (["Bearer " + "x" * 513], None),
        (["Bearer abc", "Bearer abc"], None),
    ],
)
def test_only_one_well_formed_bearer_header_yields_a_token(values, expected):
    assert parse_bearer(values) == expected


# --- the middleware ------------------------------------------------------------


def make_app(digests=(DIGEST,)):
    app = FastAPI()

    @app.get("/ping")
    def ping_get():
        return {"ok": "get"}

    @app.post("/ping")
    def ping_post():
        return {"ok": "post"}

    @app.get("/health")
    def health_get():
        return {"status": "ok"}

    @app.post("/health")
    def health_post():
        return {"changed": True}

    @app.get("/stream")
    async def stream():
        async def chunks():
            for i in range(3):
                yield f"data: {i}\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    app.add_middleware(BearerTokenAuth, digests=digests)
    return app


def client(app, base="http://test"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


BEARER = {"Authorization": f"Bearer {TOKEN}"}


async def test_a_valid_token_reaches_the_route():
    async with client(make_app()) as c:
        response = await c.get("/ping", headers=BEARER)
    assert response.status_code == 200 and response.json() == {"ok": "get"}


async def test_every_method_is_protected():
    async with client(make_app()) as c:
        for method in ("get", "post", "put", "patch", "delete", "options", "head"):
            response = await getattr(c, method)("/ping")
            assert response.status_code == 401, method


async def test_missing_and_wrong_tokens_get_the_same_response():
    async with client(make_app()) as c:
        missing = await c.get("/ping")
        wrong = await c.get("/ping", headers={"Authorization": "Bearer nope"})
    for response in (missing, wrong):
        assert response.status_code == 401 and response.json() == UNAUTHORIZED
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["cache-control"] == "no-store"
    assert missing.content == wrong.content


@pytest.mark.parametrize(
    "header",
    ["Basic " + TOKEN, "Bearer", "Bearer " + TOKEN + " extra", TOKEN, "Token " + TOKEN, ""],
)
async def test_malformed_authorization_headers_are_refused(header):
    async with client(make_app()) as c:
        response = await c.get("/ping", headers={"Authorization": header})
    assert response.status_code == 401


async def test_a_token_in_the_url_or_a_cookie_is_not_accepted():
    async with client(make_app()) as c:
        assert (await c.get(f"/ping?access_token={TOKEN}&token={TOKEN}")).status_code == 401
        assert (await c.get("/ping", cookies={"token": TOKEN})).status_code == 401


async def test_two_authorization_headers_are_ambiguous_and_refused():
    async with client(make_app()) as c:
        response = await c.get("/ping", headers=[("Authorization", f"Bearer {TOKEN}")] * 2)
    assert response.status_code == 401


async def test_several_tokens_work_and_rotation_revokes_the_old_one():
    second = new_token()
    both = make_app((DIGEST, hash_token(second)))
    async with client(both) as c:
        assert (await c.get("/ping", headers=BEARER)).status_code == 200
        assert (
            await c.get("/ping", headers={"Authorization": f"Bearer {second}"})
        ).status_code == 200
    rotated = make_app((hash_token(second),))
    async with client(rotated) as c:
        assert (await c.get("/ping", headers=BEARER)).status_code == 401


async def test_only_get_health_is_open():
    async with client(make_app()) as c:
        assert (await c.get("/health")).json() == {"status": "ok"}
        assert (await c.post("/health")).status_code == 401  # same path, other method
        for path in ("/healthz", "/health/", "/health/x", "/Health"):
            assert (await c.get(path)).status_code == 401, path


async def test_streaming_responses_are_not_buffered_or_broken():
    async with client(make_app()) as c, c.stream("GET", "/stream", headers=BEARER) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])
    assert response.status_code == 200 and body == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}])
def test_websockets_without_a_valid_token_are_refused(headers):
    with (
        TestClient(make_app()) as c,
        pytest.raises(WebSocketDisconnect) as err,
        c.websocket_connect("/ws", headers=headers),
    ):
        pass
    assert err.value.code == 1008 and err.value.reason == ""  # not a validation failure


def test_a_websocket_with_a_valid_token_connects():
    with TestClient(make_app()) as c, c.websocket_connect("/ws", headers=BEARER) as ws:
        assert ws.receive_text() == "hello"


async def test_a_plain_token_and_digests_are_accepted_together():
    plain, other = "p" * 40, new_token()
    app = FastAPI()
    app.add_api_route("/ping", lambda: {"ok": "get"})
    app.add_middleware(BearerTokenAuth, plain, digests=(hash_token(other),))
    async with client(app) as c:
        for token in (plain, other):
            response = await c.get("/ping", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200, token
        assert (await c.get("/ping", headers=BEARER)).status_code == 401


# --- wired into the served app ------------------------------------------------------------


@pytest.fixture
def secured(settings, tmp_path):
    return replace(
        settings,
        api_token_digests=(DIGEST,),
        session_db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}",
    )


async def test_the_whole_served_api_requires_a_token_except_health(secured):
    async with client(create_app(secured)) as c:
        for path in ("/list-apps", "/version", "/runs/x", "/openapi.json", "/docs"):
            assert (await c.get(path)).status_code == 401, path
        assert (await c.post("/run", json={})).status_code == 401
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/list-apps", headers=BEARER)).json() == ["opspilot_ai"]
        assert (await c.get("/runs/x", headers=BEARER)).status_code == 404


async def test_without_configured_digests_nothing_is_enforced(settings, tmp_path):
    open_settings = replace(
        settings, session_db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}"
    )
    app = create_app(open_settings)
    assert BearerTokenAuth not in [m.cls for m in app.user_middleware]
    async with client(app) as c:
        assert (await c.get("/list-apps")).status_code == 200


async def test_a_loopback_bind_rejects_rebound_host_headers(settings, tmp_path):
    local = replace(settings, session_db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    app = create_app(local, bind_host="127.0.0.1")
    async with client(app, "http://localhost:8000") as c:
        assert (await c.get("/list-apps")).status_code == 200
        assert (await c.get("/list-apps", headers={"Host": "evil.example"})).status_code == 403
    async with client(create_app(local), "http://test") as c:  # no bind host: no protection
        assert (await c.get("/list-apps", headers={"Host": "evil.example"})).status_code == 200


# --- configuration and commands ------------------------------------------------------------


def test_digests_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("OPSPILOT_API_TOKEN_SHA256", raising=False)
    monkeypatch.delenv("OPSPILOT_API_TOKEN", raising=False)
    assert Settings.from_env().api_token_digests == () and Settings.from_env().token_digests == ()
    monkeypatch.setenv("OPSPILOT_API_TOKEN_SHA256", f"{DIGEST},{hash_token('b')}")
    assert Settings.from_env().api_token_digests == (DIGEST, hash_token("b"))


def test_the_plain_token_and_the_digests_merge_without_duplicates(settings):
    plain = "p" * 40
    both = replace(settings, api_token=plain, api_token_digests=(DIGEST,))
    assert both.token_digests == (DIGEST, hash_token(plain))
    same = replace(settings, api_token=TOKEN, api_token_digests=(DIGEST,))
    assert same.token_digests == (DIGEST,)
    assert replace(settings, api_token=plain).token_digests == (hash_token(plain),)
    assert plain not in repr(both)  # the plain token never shows up in logs or tracebacks


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("OPSPILOT_API_TOKEN_SHA256", "not-a-digest", "API token entry 1"),
        ("OPSPILOT_API_TOKEN", "too-short", "OPSPILOT_API_TOKEN must be at least 32"),
    ],
)
def test_bad_token_settings_stop_every_command_with_a_clear_error(
    monkeypatch, capsys, variable, value, message
):
    monkeypatch.setenv(variable, value)
    assert cli.main(["list"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"error: {message}") and "opspilot token" in err
    assert value not in err  # not echoed: it may be a secret


def test_the_token_command_prints_a_token_and_its_digest(capsys):
    assert cli.main(["token"]) == 0
    out = capsys.readouterr().out
    token = re.search(r"cannot be recovered\): (\S+)", out).group(1)
    digest = re.search(r"OPSPILOT_API_TOKEN_SHA256=(\S+)", out).group(1)
    assert hash_token(token) == digest and len(token) >= 43
    assert token not in out.split("OPSPILOT_API_TOKEN_SHA256=")[1]  # the server line has no token
    assert cli.main(["token"]) == 0
    assert token not in capsys.readouterr().out  # a new one every time


@pytest.fixture
def served(monkeypatch):
    import uvicorn

    calls = {"uvicorn": [], "create_app": []}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls["uvicorn"].append(kw))

    def fake_create_app(settings, bind_host=None):
        calls["create_app"].append(bind_host)
        return object()

    monkeypatch.setattr("opspilot.api.create_app", fake_create_app)
    monkeypatch.delenv("OPSPILOT_API_TOKEN_SHA256", raising=False)
    monkeypatch.delenv("OPSPILOT_API_TOKEN", raising=False)
    return calls


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_needs_no_token_and_tells_adk_the_bind_host(served, capsys, host):
    assert cli.main(["serve", "--host", host]) == 0
    assert served["create_app"] == [host] and capsys.readouterr().err == ""


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.internal"])
def test_other_hosts_are_refused_without_tokens(served, capsys, host):
    assert cli.main(["serve", "--host", host]) == 2
    err = capsys.readouterr().err
    assert "refusing to serve" in err and "opspilot token" in err
    assert "OPSPILOT_API_TOKEN " in err and "OPSPILOT_API_TOKEN_SHA256" in err
    assert "--allow-unauthenticated" in err
    assert served == {"uvicorn": [], "create_app": []}


def test_the_override_is_explicit_and_still_warns(served, capsys):
    assert cli.main(["serve", "--host", "0.0.0.0", "--allow-unauthenticated"]) == 0
    assert "no authentication" in capsys.readouterr().err and served["uvicorn"]


@pytest.mark.parametrize(
    ("variable", "value"), [("OPSPILOT_API_TOKEN_SHA256", DIGEST), ("OPSPILOT_API_TOKEN", TOKEN)]
)
def test_either_token_form_allows_other_hosts_with_a_tls_reminder(
    served, monkeypatch, capsys, variable, value
):
    monkeypatch.setenv(variable, value)
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 0
    err = capsys.readouterr().err
    assert "bearer token required (1 accepted)" in err and "clear text" in err
    assert TOKEN not in err and served["create_app"] == ["0.0.0.0"]


# --- end to end: the real server ------------------------------------------------------------


@pytest.fixture(scope="module")
def live_server(data_dir, tmp_path_factory):
    token = new_token()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "ADK_CAPTURE"))}
    env.update(
        OPSPILOT_MODEL_PROVIDER="mock",
        OPSPILOT_DATA_DIR=str(data_dir),
        OPSPILOT_SESSION_DB=f"sqlite+aiosqlite:///{tmp_path_factory.mktemp('auth') / 's.db'}",
        OPSPILOT_API_TOKEN_SHA256=hash_token(token),
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "opspilot.cli", "serve", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"{base}/health", timeout=2)
                break
            except OSError:
                time.sleep(1)
        yield base, port, token
    finally:
        server.send_signal(signal.SIGINT)
        server.wait(timeout=60)


def call(base, method, path, token=None, body=None, host=None):
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    if host:
        headers["Host"] = host
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_real_server_enforces_tokens_and_the_host_guard(live_server):
    base, _, token = live_server
    assert call(base, "GET", "/health")[0] == 200
    for path in ("/list-apps", "/version", "/openapi.json", "/runs/x"):
        assert call(base, "GET", path)[0] == 401, path
        assert call(base, "GET", path, "wrong")[0] == 401, path
    assert call(base, "GET", "/list-apps", token) == (200, b'["opspilot_ai"]')
    assert call(base, "GET", "/list-apps", token, host="evil.example")[0] == 403


def test_real_server_streams_a_run_and_serves_it_back_with_a_token(live_server):
    from opspilot.datasets.spec import get_case

    base, _, token = live_server
    assert (
        call(base, "POST", "/apps/opspilot_ai/users/u/sessions/s1", token, {"dataset_id": "C02"})[0]
        == 200
    )
    message = {"role": "user", "parts": [{"text": get_case("C02")["request"]}]}
    body = {"app_name": "opspilot_ai", "user_id": "u", "session_id": "s1", "new_message": message}
    assert call(base, "POST", "/run_sse", None, body)[0] == 401
    status, stream = call(base, "POST", "/run_sse", token, body)
    assert status == 200 and stream.count(b"data:") > 10
    status, run = call(base, "GET", "/runs/s1", token)
    assert status == 200 and json.loads(run)["status"] == "investigated"


def test_real_server_websocket_needs_the_token(live_server):
    websockets = pytest.importorskip("websockets")
    import asyncio

    base, port, token = live_server
    assert (
        call(base, "POST", "/apps/opspilot_ai/users/u/sessions/ws1", token, {"dataset_id": "C02"})[
            0
        ]
        == 200
    )
    url = f"ws://127.0.0.1:{port}/run_live?app_name=opspilot_ai&user_id=u&session_id=ws1"

    async def handshake(headers):
        try:
            async with websockets.connect(url, additional_headers=headers, open_timeout=10):
                return 101
        except websockets.exceptions.InvalidStatus as e:
            return e.response.status_code

    assert asyncio.run(handshake({})) == 403
    assert asyncio.run(handshake({"Authorization": "Bearer nope"})) == 403
    assert asyncio.run(handshake({"Authorization": f"Bearer {token}"})) == 101
