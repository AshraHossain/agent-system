"""Coarse latency budgets. Opt-in (`-m perf`): timings depend on the machine.

Not load testing: sequential calls from one client, a p95 per endpoint
against ``API_PERF_P95_MS``.
"""

from __future__ import annotations

import time

import pytest

from api_tests.assertions import assert_status, p95

pytestmark = pytest.mark.perf


@pytest.mark.parametrize(
    "path",
    ["/segments", "/status", "/forecast/{seg}", "/history/{seg}", "/path/{seg}/{seg}"],
)
def test_p95_latency_within_budget(api, settings, segment_id, path):
    url = path.format(seg=segment_id)
    assert_status(api.get(url), 200)  # warm-up
    samples = []
    for _ in range(settings.perf_samples):
        started = time.perf_counter()
        response = api.get(url)
        samples.append((time.perf_counter() - started) * 1000)
        assert_status(response, 200)
    observed = p95(samples)
    assert observed <= settings.perf_p95_ms, (
        f"{url}: p95 {observed:.0f} ms > budget {settings.perf_p95_ms:.0f} ms "
        f"(min {min(samples):.0f}, max {max(samples):.0f})"
    )
