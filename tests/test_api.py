"""GET /runs/{run_id}: read-only lookup of persisted investigations (ADR-0010)."""

import logging
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from google.adk.sessions import DatabaseSessionService

from opspilot import cli
from opspilot.adk import runner as R
from opspilot.api import ADK_APP_NAME, create_app, runs_router
from opspilot.contracts.report import InvestigationReport
from opspilot.datasets.spec import get_case


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
    monkeypatch.setattr("opspilot.api.create_app", lambda settings: sentinel)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    assert cli.main(["serve", "--port", "9001"]) == 0
    assert calls == [(sentinel, {"host": "127.0.0.1", "port": 9001})]
    assert capsys.readouterr().err == ""


def test_serve_warns_when_binding_beyond_loopback(monkeypatch, capsys):
    import uvicorn

    monkeypatch.setattr("opspilot.api.create_app", lambda settings: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 0
    assert "no authentication" in capsys.readouterr().err
