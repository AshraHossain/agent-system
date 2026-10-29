# api-tests

A black-box API test suite for the **Traffic Flow API** (`../traffic-flow-analyzer`,
`trafficflow serve-api`). It talks to the service over HTTP only and imports none of its
code, so the same suite runs against a local process, a container or a deployed instance.

> Practice project. Not production software.

## Quick start

```bash
cd api-tests
uv sync
uv run pytest                 # starts the target on a free port, runs everything but perf
```

Without `API_BASE_URL`, the session fixture runs `uv run trafficflow serve-api` in
`../traffic-flow-analyzer` (synthetic feed, mock model), waits for `/openapi.json` and
stops it at the end. Its log goes to `reports/server.log`.

Against a server you started yourself:

```bash
docker compose -f ../traffic-flow-analyzer/docker-compose.yml up -d api
API_BASE_URL=http://localhost:8000 uv run pytest
```

## What is tested

| Marker | Folder | What it checks |
|---|---|---|
| `smoke` | `tests/smoke/` | Every endpoint answers 2xx with the right content type; the spec lists every path. Run first: `-m smoke -x` |
| `functional` | `tests/functional/` | Response schemas (strict Pydantic models in `src/api_tests/schemas.py`) and data invariants: forecast steps and spacing, `lower ≤ speed ≤ upper`, peak = max point, level monotone in index, history window and ordering, routes are simple paths and each weight optimises its own metric, SSE events, interval clamp |
| `negative` | `tests/negative/` | Unknown ids → 404, malformed params/bodies → 422 with FastAPI's error shape, wrong methods → 405 |
| `contract` | `tests/contract/` | [Schemathesis](https://schemathesis.readthedocs.io) property-based fuzzing over `/openapi.json`: no 5xx, documented status codes and content types, schema-valid bodies |
| `security` | `tests/security/` | No stack traces or paths in error bodies, hostile ids (traversal, injection, 4 KB) rejected cleanly, large bodies don't crash it, no version in `server`, no permissive CORS, auth enforced when `API_TOKEN` is set |
| `known_defect` | `tests/known_defects/` | Defects found in the target, pinned as strict xfails — see [docs/FINDINGS.md](docs/FINDINGS.md) |
| `perf` | `tests/perf/` | p95 latency per endpoint against a budget. **Opt-in**: `-m perf` |

## Commands

```bash
uv run pytest -m smoke -x                          # fast liveness gate
uv run pytest -m "functional or negative"
uv run pytest -m contract                          # fuzzing, schema-valid requests only
API_FUZZ_MODES=all uv run pytest -m contract       # + negative mode (invalid requests too)
API_FUZZ_EXAMPLES=200 uv run pytest -m contract    # deeper fuzzing
uv run pytest -m perf                              # latency budgets
uv run pytest -n auto -m "not contract and not perf"   # parallel (pytest-xdist; one server per worker)
uv run pytest --junitxml=reports/junit.xml         # CI report
uv run ruff check . && uv run ruff format --check .
```

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `API_BASE_URL` | unset | Test this server instead of starting one |
| `API_TOKEN` | unset | Sent as `Authorization: Bearer …`; also enables the auth test |
| `API_TIMEOUT` | `10` | Per-request timeout, seconds |
| `API_TARGET_PROJECT` | `../traffic-flow-analyzer` | Project to start when `API_BASE_URL` is unset |
| `API_STARTUP_TIMEOUT` | `90` | Seconds to wait for the local server |
| `API_FUZZ_EXAMPLES` | `25` | Schemathesis examples per operation |
| `API_FUZZ_MODES` | `positive` | `all` adds negative-mode fuzzing |
| `API_PERF_SAMPLES` / `API_PERF_P95_MS` | `20` / `500` | Perf sample count and p95 budget |

CI (`.github/workflows/api-tests.yml`) runs lint, `-m smoke -x`, then the default suite with
`--junitxml`, whenever `api-tests/` or `traffic-flow-analyzer/` changes; the JUnit report and
`reports/server.log` are uploaded as the `api-tests-reports` artifact.

Each test also has a 120 s hard timeout (pytest-timeout), so a hang fails the run
instead of stalling it.

## Layout

```
src/api_tests/
  config.py      settings from the environment
  client.py      httpx wrapper: base URL, timeout, bearer token, connect-only retries, exchange log
  server.py      starts/stops the target on a free port
  schemas.py     expected response models (independent of the target's own models)
  assertions.py  assert_status / assert_schema / assert_ok / assert_error / p95
  contract.py    OpenAPI patches for known spec gaps, each tagged with its finding ID
tests/           one folder per marker (table above)
docs/FINDINGS.md defects found, with reproduction curls and suggested fixes
```

A failing test's report includes the last five HTTP exchanges (method, URL, status,
latency, body preview).

## Results on the current target

Eight findings so far. The two medium ones, an unbounded `horizon` (KD-02) and a 500 on
incomplete `/ask` history (KD-08), are fixed and now guarded by the negative tests; six low
ones are open and pinned as strict xfails. See [docs/FINDINGS.md](docs/FINDINGS.md).
