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
  pytest-timeout, pytest-xdist, ruff, uv.
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
6. ☐ Stateful/link-based fuzzing if the API gains write endpoints; load testing (e.g. Locust)
   kept separate from this suite.

## Out of scope

- Fixing the target's defects (reported in `docs/FINDINGS.md` with suggested fixes).
- Asserting live-model `/ask` output.
