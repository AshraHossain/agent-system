"""What the OpenAPI document promises, checked against what the API sends."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(
    "path", ["/forecast/{segment_id}", "/history/{segment_id}", "/path/{source}/{target}"]
)
def test_spec_documents_404(openapi, path):
    """Was KD-05: the 404s these operations return were undocumented."""
    assert "404" in openapi["paths"][path]["get"]["responses"]


def _has_offset(value: str) -> bool:
    return value.endswith("Z") or value[-6] in "+-"


def test_date_times_carry_an_offset_when_the_spec_says_so(api, openapi):
    """Was KD-06: offset-less timestamps were declared RFC 3339 "date-time"."""
    as_of = openapi["components"]["schemas"]["StatusOut"]["properties"]["as_of"]
    value = api.get("/status").json()["as_of"]
    if as_of.get("format") == "date-time":
        assert _has_offset(value), f"{value!r} is declared date-time but has no offset"
    else:
        assert as_of.get("description"), "an offset-less timestamp should say what clock it is"
