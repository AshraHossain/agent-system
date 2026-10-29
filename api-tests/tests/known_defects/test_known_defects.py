"""Defects this suite found in the target, pinned as strict xfails.

Each test asserts the *correct* behaviour. While the defect exists it xfails;
once the service is fixed it passes, strict mode turns that into a failure,
and the marker (plus any patch in ``api_tests.contract``) should be removed.
Details and reproduction steps: docs/FINDINGS.md.
"""

from __future__ import annotations

import pytest

from api_tests.assertions import assert_status

pytestmark = pytest.mark.known_defect


def kd(finding: str, summary: str):
    return pytest.mark.xfail(reason=f"{finding}: {summary}", strict=True)


@kd("KD-01", "non-positive horizon is accepted and silently becomes 5 minutes")
@pytest.mark.parametrize("horizon", [0, -5])
def test_non_positive_horizon_is_rejected(api, segment_id, horizon):
    assert_status(api.get(f"/forecast/{segment_id}", params={"horizon": horizon}), 422)


@kd("KD-01", "a horizon that is not a multiple of 5 is rounded down without saying so")
def test_horizon_is_echoed_as_requested(api, segment_id):
    body = api.get(f"/forecast/{segment_id}", params={"horizon": 7}).json()
    assert body["horizon_minutes"] == 7


@kd("KD-03", "negative days is accepted and returns an empty history")
def test_negative_days_is_rejected(api, segment_id):
    assert_status(api.get(f"/history/{segment_id}", params={"days": -1}), 422)


@kd("KD-04", "an unknown route weight is accepted and silently treated as distance")
def test_unknown_weight_is_rejected(api, segment_ids):
    source, target = segment_ids[0], segment_ids[-1]
    assert_status(api.get(f"/path/{source}/{target}", params={"weight": "bogus"}), 422)


@kd("KD-05", "the OpenAPI document omits the 404 responses these operations return")
@pytest.mark.parametrize(
    "path", ["/forecast/{segment_id}", "/history/{segment_id}", "/path/{source}/{target}"]
)
def test_spec_documents_404(openapi, path):
    assert "404" in openapi["paths"][path]["get"]["responses"]


@kd("KD-06", "timestamps carry no UTC offset, so they are not RFC 3339 date-times")
def test_timestamps_carry_an_offset(api):
    as_of = api.get("/status").json()["as_of"]
    assert as_of.endswith("Z") or as_of[-6] in "+-", as_of


@kd("KD-07", "history timestamps use a space separator, unlike every other endpoint")
def test_history_timestamps_use_iso_format(api, segment_id):
    readings = api.get(f"/history/{segment_id}", params={"days": 0.05}).json()["readings"]
    assert "T" in readings[0]["ts"], readings[0]["ts"]
