"""Every endpoint answers. Fast; run first (`-m smoke`) to fail early on a dead target."""

from __future__ import annotations

import json

import pytest

from api_tests.assertions import assert_json, assert_status

pytestmark = pytest.mark.smoke

EXPECTED_PATHS = {
    "/segments",
    "/status",
    "/forecast/{segment_id}",
    "/history/{segment_id}",
    "/path/{source}/{target}",
    "/ask",
    "/stream",
}


def test_openapi_is_served(openapi):
    assert openapi["openapi"].startswith("3.")
    assert openapi["info"]["title"]


def test_openapi_lists_every_expected_path(openapi):
    missing = EXPECTED_PATHS - set(openapi["paths"])
    assert not missing, f"paths missing from the spec: {sorted(missing)}"


@pytest.mark.parametrize(
    "path",
    [
        "/segments",
        "/status",
        "/forecast/{seg}",
        "/history/{seg}",
        "/path/{seg}/{seg}",
    ],
)
def test_get_endpoint_answers(api, segment_id, path):
    response = api.get(path.format(seg=segment_id))
    assert_status(response, 200)
    assert_json(response)


def test_ask_answers(api):
    response = api.post("/ask", json={"question": "How is traffic right now?"})
    assert_status(response, 200)
    assert_json(response)


def test_stream_sends_a_first_event(api):
    with api.stream("GET", "/stream", params={"interval": 1}) as response:
        assert_status(response, 200)
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                json.loads(line.removeprefix("data:"))
                return
    pytest.fail("stream closed before sending an event")
