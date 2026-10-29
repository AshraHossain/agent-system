# CLAUDE.md — api-tests

Black-box HTTP test suite for the Traffic Flow API (`../traffic-flow-analyzer`). Self-contained:
own `pyproject.toml` and lockfile; run every command from inside `api-tests/`.

## Commands

```bash
uv sync
uv run pytest                     # starts the target locally; everything except perf
uv run pytest -m smoke -x         # fast gate
uv run pytest -m perf             # opt-in latency budgets
uv run ruff check . && uv run ruff format --check .
```

## Rules

- **Black-box only.** Never import `trafficflow` (or any target code). Expected shapes live in
  `src/api_tests/schemas.py` and are written from documented behaviour, not copied from the
  target's models.
- **No hiding failures.** Never retry on 5xx, skip, or loosen an assertion to get green. A real
  defect gets a finding ID in `docs/FINDINGS.md` and a strict-xfail test in
  `tests/known_defects/` that asserts the *correct* behaviour.
- **Spec patches are tagged.** Any change `src/api_tests/contract.py` makes to the OpenAPI
  document before fuzzing names the finding it works around; remove it when the finding is fixed.
- Assert invariants, not exact values. The target runs the synthetic feed and mock model, but
  tests must still pass against another deployment of the same API (e.g. `/ask` text is never
  compared exactly).
- Every request has a timeout; every test has the pytest-timeout limit. Endless responses
  (`/stream`) are read with `api.stream(...)` and stopped explicitly, and are excluded from fuzzing.
- New endpoint ⇒ add it to `EXPECTED_PATHS` and the smoke test, a response model, functional
  invariants, negative cases, and (if it is endless or slow) a fuzzing exclusion or bound.

## Conventions

- Conventional commits, scope `api-tests`: `test(api-tests): ...`, `docs(api-tests): ...`.
- Do not claim production readiness.
