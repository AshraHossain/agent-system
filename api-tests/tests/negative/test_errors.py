"""Bad input gets a clean 4xx with a predictable error body, never a 5xx."""

from __future__ import annotations

import time

import pytest

from api_tests.assertions import assert_error, assert_status
from api_tests.schemas import ErrorDetail, ValidationErrorBody

pytestmark = pytest.mark.negative

UNKNOWN = "NO_SUCH_SEGMENT"


@pytest.mark.parametrize(
    "path", ["/forecast/{x}", "/history/{x}", "/path/{x}/{seg}", "/path/{seg}/{x}"]
)
def test_unknown_segment_is_404(api, segment_id, path):
    body = assert_error(api.get(path.format(x=UNKNOWN, seg=segment_id)), 404, ErrorDetail)
    assert UNKNOWN in body.detail


@pytest.mark.parametrize(
    ("path", "params", "field"),
    [
        ("/forecast/{seg}", {"horizon": "abc"}, "horizon"),
        ("/forecast/{seg}", {"horizon": "1.5"}, "horizon"),
        ("/history/{seg}", {"days": "yesterday"}, "days"),
        ("/stream", {"interval": "fast"}, "interval"),
    ],
)
def test_malformed_query_parameter_is_422(api, segment_id, path, params, field):
    body = assert_error(
        api.get(path.format(seg=segment_id), params=params), 422, ValidationErrorBody
    )
    assert any(field in issue.loc for issue in body.detail)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": None},
        {"question": 42},
        {"question": "ok", "history": "not-a-list"},
        {"question": "ok", "history": [{"role": 1}]},
    ],
)
def test_ask_rejects_invalid_bodies(api, payload):
    assert_error(api.post("/ask", json=payload), 422, ValidationErrorBody)


@pytest.mark.parametrize("item", [{"content": "hi"}, {"role": "user"}, {}])
def test_ask_rejects_incomplete_history_items(api, item):
    """Was KD-08: a 500 echoing the missing key."""
    response = api.post("/ask", json={"question": "ok?", "history": [item]})
    assert_error(response, 422, ValidationErrorBody)


def test_oversized_horizon_is_rejected_quickly(api, segment_id):
    """Was KD-02: no upper bound; 10M minutes took ~88 s and returned 243 MB."""
    started = time.monotonic()
    response = api.get(f"/forecast/{segment_id}", params={"horizon": 100_000})
    assert_error(response, 422, ValidationErrorBody)
    assert time.monotonic() - started < 1.0


def test_ask_rejects_non_json(api):
    response = api.post("/ask", content=b"question=hi", headers={"content-type": "text/plain"})
    assert_error(response, 422, ValidationErrorBody)


def test_ask_rejects_broken_json(api):
    response = api.post(
        "/ask", content=b'{"question": ', headers={"content-type": "application/json"}
    )
    assert_error(response, 422, ValidationErrorBody)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/segments"),
        ("DELETE", "/segments"),
        ("PUT", "/status"),
        ("PATCH", "/forecast/{seg}"),
        ("GET", "/ask"),
    ],
)
def test_wrong_method_is_405(api, segment_id, method, path):
    response = api.request(method, path.format(seg=segment_id))
    assert_status(response, 405)
    assert "allow" in {k.lower() for k in response.headers}


def test_unknown_route_is_404(api):
    assert_error(api.get("/no-such-endpoint"), 404, ErrorDetail)
