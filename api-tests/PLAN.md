# PLAN — api-tests

## Goal

A standalone, black-box API test project for the Traffic Flow API that a QA engineer would
recognise: layered test types, readable failures, configurable target, and defects reported
with evidence instead of being hidden.

## Decisions

- **Target:** `traffic-flow-analyzer`'s FastAPI layer. Read-only and deterministic on the
  synthetic feed and mock model, and already packaged as a container. The ledger-hound P2P API
  already has its own QA agent; OpsPilot's API is covered by its own tests.
- **Black-box over HTTP.** `API_BASE_URL` selects the server; without it a session fixture
  starts one on a free port. No target imports.
- **Stack:** pytest, httpx, Pydantic (response models), schemathesis (OpenAPI fuzzing),
  Hypothesis (call-sequence state machine), pytest-timeout, pytest-xdist, ruff, uv; Locust for
  the separate load test (`--group load`, not a default dependency).
- **Defects become strict xfails** with IDs in `docs/FINDINGS.md`, so a fix is noticed.

## Phases

1. ✅ Scaffold: project, settings, client, local-server fixture, smoke tests.
2. ✅ Functional invariants and negative tests; strict response models.
3. ✅ Contract fuzzing with tagged spec patches for known gaps.
4. ✅ Security hygiene and opt-in perf budgets; README and findings report.
5. ✅ CI: `.github/workflows/api-tests.yml` runs lint, a smoke gate and the default suite on
   changes to `api-tests/` or `traffic-flow-analyzer/`, with the target started by the session
   fixture (not the docker-compose image, which also installs the `ml` extra and is much slower
   to build). JUnit XML and the server log are uploaded as an artifact.
6. ✅ Request-sequence fuzzing: `tests/stateful/` runs a Hypothesis rule-based state machine —
   long random sequences of calls against one live server, checking invariants that only hold
   across a sequence (the segment list doesn't change mid-run, the feed's clock never runs
   backwards). The API is still read-only, so there is no create/update/delete chain to test as
   a *stateful* sequence in the CRUD sense; that stays pending if the API gains write endpoints.
   Load testing: `loadtest/locustfile.py` (Locust, kept out of the pytest suite and its default
   deps — see `docs/LOADTEST.md`). It found a real bottleneck in `/status` under concurrent
   load, documented there rather than fixed in this suite.
7. ✅ Fix the `/status`/`/forecast` concurrency bottleneck `docs/LOADTEST.md` found, in
   `traffic-flow-analyzer` (forecasts cached by input hash; before/after numbers in that doc) (PR #53).

## Out of scope

- Fixing the target's defects inside this suite: fixes land in `traffic-flow-analyzer/`
  (KD-01..KD-09 are fixed), and their tests move from `known_defects/` into the regular suite.
- Asserting live-model `/ask` output.
