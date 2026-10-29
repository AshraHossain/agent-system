"""Tests for the FastAPI query layer."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

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
