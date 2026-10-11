"""UI client against the real API app (in-process). The UI never imports the service."""

import ast
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from netpulse.api.app import create_app
from netpulse.api.auth import TokenStore
from netpulse.graph.deps import Deps
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService
from netpulse.ui.client import ApiClient, ApiError, is_settled

SUBMISSION = {
    "title": "Latency spike on core links",
    "description": "synthetic test",
    "dataset_id": "case-04",
    "window_start": "2026-03-08T08:00:00+00:00",
    "window_end": "2026-03-08T13:55:00+00:00",
}


@pytest.fixture
def api(tmp_path):
    service = InvestigationService(Deps.default(), PersistentStore(tmp_path / "t.db"))
    tokens = TokenStore.parse("op:operator:olga,view:viewer:vic")
    with TestClient(create_app(service, tokens, max_workers=1)) as tc:
        yield tc


def _client(api, token):
    http = httpx.Client(transport=api._transport, base_url="http://testserver")
    return ApiClient("http://testserver", token, http=http)


def test_health_and_auth_errors(api):
    assert _client(api, "op").health()["status"] == "ok"
    with pytest.raises(ApiError) as exc:
        _client(api, "nope").submit_incident(SUBMISSION)
    assert exc.value.status_code == 401
    with pytest.raises(ApiError) as exc:
        _client(api, "view").submit_incident(SUBMISSION)
    assert exc.value.status_code == 403


def test_submit_and_poll(api):
    client = _client(api, "op")
    accepted = client.submit_incident(SUBMISSION, incident_id="ui-test-1")
    assert accepted["incident_id"] == "ui-test-1"
    status = client.status("ui-test-1")
    for _ in range(200):
        if is_settled(status):
            break
        import time

        time.sleep(0.1)
        status = client.status("ui-test-1")
    assert is_settled(status)
    assert client.results("ui-test-1")["incident_id"] == "ui-test-1"


def test_is_settled():
    assert not is_settled({"job_state": "running", "status": None})
    assert not is_settled({"job_state": "queued", "status": None})
    assert is_settled({"job_state": "done", "status": "completed"})
    assert is_settled({"job_state": "error", "status": None})


def test_unreachable_api_raises_api_error():
    client = ApiClient("http://127.0.0.1:1", "x", timeout=0.5)
    with pytest.raises(ApiError) as exc:
        client.health()
    assert exc.value.status_code == 0


def test_ui_never_imports_service_or_ground_truth():
    banned = {"netpulse.service", "netpulse.graph", "netpulse.persistence", "synthgen", "eval"}
    for path in (Path(__file__).resolve().parent.parent / "netpulse" / "ui").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = node.names[0].name
            else:
                continue
            assert not any(mod == b or mod.startswith(b + ".") for b in banned), (path, mod)


def test_eval_reports_listing_and_fetch(api, tmp_path, monkeypatch):
    import netpulse.api.app as api_app

    (tmp_path / "v1-heuristic-abc1234.json").write_text('{"notice": "SYNTHETIC", "investigation": {}, "cases": []}')
    monkeypatch.setattr(api_app, "EVAL_REPORTS_DIR", tmp_path)
    client = _client(api, "view")
    assert client.eval_reports()["reports"] == ["v1-heuristic-abc1234.json"]
    assert client.eval_report("v1-heuristic-abc1234.json")["notice"] == "SYNTHETIC"
    for bad in ("../../pyproject.toml", "nope.json", "..%2F..%2Fetc%2Fpasswd"):
        with pytest.raises(ApiError) as exc:
            client.eval_report(bad)
        assert exc.value.status_code == 404
