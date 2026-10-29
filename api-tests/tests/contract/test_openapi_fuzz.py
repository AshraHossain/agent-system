"""Property-based contract tests generated from the target's OpenAPI document.

Schemathesis generates requests for every operation and checks: no 5xx,
documented status codes, documented content types and response bodies that
match the response schema. Known spec gaps are patched first; see
``api_tests.contract``.

Both schema-valid and invalid (negative mode) requests are generated: invalid
ones must get a documented 4xx. ``API_FUZZ_MODES=positive`` restricts fuzzing
to valid requests, e.g. while a new input-validation finding is pinned in
``tests/known_defects/`` and would otherwise fail every run.
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
    [GenerationMode.POSITIVE]
    if os.environ.get("API_FUZZ_MODES", "all") == "positive"
    else list(GenerationMode)
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
