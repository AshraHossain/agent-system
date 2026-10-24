"""Black-box security hygiene: error bodies, hostile input, headers, auth.

Not a penetration test; these are the checks every API should pass cheaply.
"""

from __future__ import annotations

import re

import pytest

from api_tests.assertions import assert_status

pytestmark = pytest.mark.security

LEAK_PATTERNS = [
    r"Traceback \(most recent call last\)",
    r'File "/[^"]+\.py", line \d+',
    r"/home/|/usr/lib/python|site-packages",
]

HOSTILE_IDS = [
    "../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "' OR '1'='1",
    "<script>alert(1)</script>",
    "%00",
    "A" * 4096,
]


def _assert_no_leak(response):
    for pattern in LEAK_PATTERNS:
        assert not re.search(pattern, response.text), (
            f"{response.request.url} leaks internals: matched {pattern!r}"
        )


@pytest.mark.parametrize("hostile", HOSTILE_IDS)
def test_hostile_segment_ids_are_rejected_cleanly(api, hostile):
    for path in (f"/forecast/{hostile}", f"/history/{hostile}"):
        response = api.get(path)
        assert response.status_code in (404, 422), f"{path[:80]} -> {response.status_code}"
        _assert_no_leak(response)


def test_error_bodies_do_not_leak_internals(api, segment_id):
    responses = [
        api.get("/forecast/NOPE"),
        api.get(f"/forecast/{segment_id}", params={"horizon": "x"}),
        api.post("/ask", content=b"{bad json", headers={"content-type": "application/json"}),
    ]
    for response in responses:
        _assert_no_leak(response)


def test_hostile_question_is_not_reflected_unescaped_as_html(api):
    payload = "<script>alert(1)</script>"
    response = api.post("/ask", json={"question": payload})
    assert_status(response, 200)
    assert response.headers["content-type"].startswith("application/json")


def test_large_question_does_not_crash_the_service(api):
    response = api.post("/ask", json={"question": "traffic? " * 50_000})  # ~450 KB
    assert response.status_code < 500, response.status_code


def test_server_header_does_not_disclose_a_version(api):
    server = api.get("/segments").headers.get("server", "")
    assert not re.search(r"\d+\.\d+", server), f"server header discloses a version: {server!r}"


def test_cors_does_not_allow_any_origin(api):
    response = api.get("/segments", headers={"Origin": "https://evil.example"})
    assert response.headers.get("access-control-allow-origin") not in ("*", "https://evil.example")


def test_auth_is_enforced_when_a_token_is_configured(settings, base_url):
    if not settings.token:
        pytest.skip("API_TOKEN not set; the target runs without auth")
    import httpx

    response = httpx.get(f"{base_url}/segments", timeout=settings.timeout_s)
    assert response.status_code in (401, 403)
