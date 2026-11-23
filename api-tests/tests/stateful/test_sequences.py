"""Request-sequence fuzzing: random walks of calls against one running server.

``tests/contract/`` fuzzes each operation in isolation. The API is read-only, so
there is no create/update/delete chain to test as a sequence (api-tests/PLAN.md
tracks that for if the API ever gains write endpoints) — but a long, randomly
ordered walk of *reads* still earns its keep: it is the one place this suite
runs many interleaved, differently parameterised calls against a single live
process over wall-clock time, which is exactly where shared mutable state (the
feed's "now", any caching) or a race between requests would show up. Every
rule either fails outright (a 5xx, a schema mismatch) or checks an invariant
that must hold across the whole sequence, not just within one call.

Opt-in (`-m stateful`): a Hypothesis state machine, slower than one request.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import HealthCheck
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

from api_tests.assertions import assert_ok
from api_tests.schemas import Answer, Forecast, History, Route, Segment, Status

pytestmark = pytest.mark.stateful

MAX_EXAMPLES = int(os.environ.get("API_STATEFUL_EXAMPLES", "15"))
STEP_COUNT = int(os.environ.get("API_STATEFUL_STEPS", "20"))

RUN_SETTINGS = hyp_settings(
    max_examples=MAX_EXAMPLES,
    stateful_step_count=STEP_COUNT,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# Printable ASCII only: this walk is about call sequencing, not character
# encoding (schemathesis' negative mode already covers hostile bytes, e.g. KD-09).
_question = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=80)


def build_machine(api, segment_ids: list[str]) -> type[RuleBasedStateMachine]:
    known_ids = frozenset(segment_ids)
    horizons = st.integers(min_value=1, max_value=36).map(lambda n: n * 5)  # 5..180, step 5
    days = st.floats(min_value=0.01, max_value=30, allow_nan=False, allow_infinity=False)
    weights = st.sampled_from(["time", "distance"])
    segment = st.sampled_from(sorted(known_ids))

    class ApiSequence(RuleBasedStateMachine):
        """One run = one random sequence of calls against the already-running server."""

        def __init__(self) -> None:
            super().__init__()
            self.last_as_of = None  # the feed's clock must never run backwards

        @rule()
        def segments(self):
            ids = {s.id for s in (Segment.model_validate(s) for s in api.get("/segments").json())}
            assert ids == known_ids, "the segment list changed mid-sequence"

        @rule()
        def status(self):
            body = assert_ok(api.get("/status"), Status)
            if self.last_as_of is not None:
                assert body.as_of >= self.last_as_of
            self.last_as_of = body.as_of
            assert set(body.segment_levels) == known_ids

        @rule(segment_id=segment, horizon=horizons)
        def forecast(self, segment_id, horizon):
            fc = assert_ok(
                api.get(f"/forecast/{segment_id}", params={"horizon": horizon}), Forecast
            )
            assert fc.segment_id == segment_id
            assert fc.horizon_minutes == horizon

        @rule(segment_id=segment, days=days)
        def history(self, segment_id, days):
            h = assert_ok(api.get(f"/history/{segment_id}", params={"days": days}), History)
            assert h.segment_id == segment_id

        @rule(source=segment, dest=segment, weight=weights)
        def path(self, source, dest, weight):
            response = api.get(f"/path/{source}/{dest}", params={"weight": weight})
            assert response.status_code in (200, 404), response.text
            if response.status_code == 200:
                route = Route.model_validate(response.json())
                assert route.path[0] == source and route.path[-1] == dest

        @rule(question=_question)
        def ask(self, question):
            assert_ok(api.post("/ask", json={"question": question}), Answer)

        @invariant()
        def never_5xx(self):
            # call-specific checks above already cover 2xx/4xx shapes; this is a
            # trip wire for anything a rule's own assertion missed.
            if api.history:
                assert api.history[-1].status < 500, api.history[-1]

    return ApiSequence


def test_random_walk_of_reads_never_breaks(api, segment_ids):
    machine_cls = build_machine(api, segment_ids)
    run_state_machine_as_test(machine_cls, settings=RUN_SETTINGS)
