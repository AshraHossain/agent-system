"""Property-based contract tests generated from the target's OpenAPI document.

Schemathesis generates requests for every operation and checks: no 5xx,
documented status codes, documented content types and response bodies that
match the response schema. Known spec gaps are patched first; see
``api_tests.contract``.

By default only schema-valid requests are generated: invalid ones keep landing
on the known input-validation defects (KD-01/03/08), which are pinned in
``tests/known_defects/``. ``API_FUZZ_MODES=all`` adds schemathesis' negative
mode to look for anything new; expect those known defects among its failures.
"""

from __future__ import annotations

import os

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis import GenerationMode

from api_tests.contract import patched_spec

pytestmark = pytest.mark.contract

MAX_EXAMPLES = int(os.environ.get("API_FUZZ_EXAMPLES", "25"))
MODES = (
    list(GenerationMode)
    if os.environ.get("API_FUZZ_MODES", "positive") == "all"
    else [GenerationMode.POSITIVE]
)


@pytest.fixture(scope="session")
def api_schema(openapi, base_url):
    schema = schemathesis.openapi.from_dict(patched_spec(openapi))
    schema.config.update(base_url=base_url)
    schema.config.generation.update(modes=MODES)
    return schema


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_operation_honours_its_contract(case):
    case.call_and_validate(timeout=10)
