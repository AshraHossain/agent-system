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
  node routes each parsed plan step to `calculator_tool` (arithmetic
  detected via regex), `search_tool` (steps mentioning "search"/"look up"/
  "find information"), or passes the step through unchanged otherwise.
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

## Low priority / infra

- [x] Git repository initialized (`git init`, local identity set).
- [x] UV-based dependency management (`pyproject.toml` + `uv.lock`).
- [x] Dockerfile for containerized runs.
- [x] **CI workflow (lint/test on push)** — Fixed. GitHub Actions workflow created
  (`.github/workflows/ci.yml`) that runs `pytest` on push and pull requests.
  Uses `uv` for dependency management and Python 3.11. Runs on all commits to
  main and feature branches, plus all PRs against main.
- [ ] Structured logging / observability for the FastAPI service.
