# TASK.md — agent-system

Priority task list. See `PLANNING.md` for architecture context.

## High priority

- [x] **Fix API key mismatch** — Fixed. `app/agents.py` now calls
  `load_dotenv()` and constructs the client with
  `OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])`,
  matching the OpenRouter-format key (`sk-or-v1-...`) that was already in
  `.env`. Model name updated to `openai/gpt-4o-mini` (OpenRouter requires
  provider-prefixed model IDs). Verified the client imports and configures
  correctly. **Note:** the key that was sitting in plaintext `.env` was
  visible in a terminal transcript during debugging — rotate it in the
  OpenRouter dashboard as a precaution.
- [x] **Add a test suite** — Fixed. `pytest` is now a `dependency-groups.dev`
  entry in `pyproject.toml` (`uv sync` installs it into the project venv, so
  `uv run pytest` sees `app.*`/`tools.*` and their real dependencies instead
  of a bare global pytest). `tests/conftest.py` sets a placeholder
  `OPENROUTER_API_KEY` so importing `app.agents` doesn't require a real
  secret. 24 tests passing, all offline (planner_agent is mocked wherever a
  test exercises the graph).
- [x] **Remove `eval()` from `calculator_tool`** — Fixed. Wiring
  `calculator_tool` into the graph (below) made it reachable end-to-end from
  the public `GET /run?query=...` endpoint via LLM-generated plan text,
  turning the old `eval(expression)` into a real remote-code-execution
  surface. `tools/calculator_tool.py` now parses the expression with `ast`
  and evaluates it through a fixed table of arithmetic operators only.

## Medium priority

- [x] **Wire tools into the graph** — Fixed. `app/graph.py`'s new `executor`
  node routes each parsed plan step to `calculator_tool`, `search_tool`, or
  passes the step through unchanged. (Originally regex-based; replaced by
  LLM routing, below.)
- [x] **Implement `memory/store.py`** — Fixed. Append-only JSON-Lines store
  (`data/runs.jsonl`, gitignored): `save_run(state)` writes a run's final
  state and returns a `run_id`; `load_run(run_id)` / `list_runs()` read it
  back. `GET /run` now calls `save_run` and includes `run_id` in its
  response. Still no cross-run memory read into the graph itself — this is
  storage, not recall.
- [x] **Expand the graph beyond a single node** — Fixed. `app/graph.py` is
  now a 3-node linear graph: `planner` → `executor` → `synthesizer`.
- [x] **Populate `AgentState.result`** — Fixed. The new `synthesizer` node
  joins each step with its tool result into `AgentState.result`.
- [x] **Add conditional edges for re-planning on errors** — Fixed. Added
  `errors` and `attempts` fields to `AgentState`. The `executor` node now
  detects when a tool returns an error (e.g., division by zero) and populates
  `errors` with failed steps. A conditional edge routes to `synthesizer` if
  no errors occurred, or back to `planner` for re-planning if errors detected
  (capped at 3 planner runs to prevent infinite loops). Tests expanded to
  verify error detection behavior (28/28 tests passing).
- [x] **Replace regex-based tool routing with LLM-driven decisions** — Fixed.
  Added `route_step_to_tool` function in `app/agents.py` that calls the LLM
  to categorize steps as "calculator", "search", or "passthrough". Updated
  `execute_step` to use LLM routing instead of regex patterns. Made
  `calculator_tool` smarter to extract and handle nested arithmetic expressions
  from prose. Tests replace the router with a keyword-based fake via an
  autouse fixture in `tests/conftest.py` (29/29 tests passing).
- [ ] **Feed failures back into re-planning** — a re-plan currently re-sends
  the same query, so the planner can't learn which steps failed and may
  return the same plan. Pass the failed steps/errors into `planner_agent`.
- [ ] **Wire `search_tool` to a real search API** — still a placeholder that
  echoes its input.
- [ ] **Handle word-phrased arithmetic** — steps like "Multiply 6 by 7" are
  routed to the calculator but fail to parse, triggering a re-plan.

## Low priority / infra

- [x] Git repository initialized (`git init`, local identity set).
- [x] UV-based dependency management (`pyproject.toml` + `uv.lock`).
- [x] Dockerfile for containerized runs.
- [x] **CI workflow (test on push)** — Fixed. GitHub Actions workflow
  (`.github/workflows/ci.yml`) runs `uv sync` + `uv run pytest` on Python
  3.11 for pushes and pull requests to `master`. No lint step yet.
- [ ] `GET /runs/{run_id}` route (and/or reading past runs back into the
  graph) so stored runs are actually used.
- [ ] Structured logging / observability for the FastAPI service.
