# CLAUDE.md — agent-system

Guidance for Claude Code when working in this repository.

## What this is

A minimal FastAPI service exposing a multi-node LangGraph agent: `GET
/run?query=...` invokes a `planner` agent (`openai/gpt-4o-mini` via
OpenRouter) that breaks the query into steps, an executor that asks the LLM
to route each step to a tool (calculator for arithmetic, search
placeholder, or passthrough), and a synthesizer that assembles the result.
A conditional edge re-runs the planner if a calculator step errors, up to 3
planner runs in total. The final graph state is saved and returned as JSON.
Read `PLANNING.md` before changing code; it documents the architecture,
module responsibilities, and constraints in full. `README.md` is the
user-facing guide (setup, API, configuration, limitations).

## Framework conventions

This repo follows the SuperClaude Framework structure:

- **`PLANNING.md`** — architecture reference: module map, request flow,
  external dependencies, and known constraints. Read this before touching
  `app/`.
- **`TASK.md`** — prioritized backlog (High/Medium/Low). Check here before
  starting work — several issues below are already tracked, not new
  discoveries.
- **`plugins/README.md`** — plugin extension point placeholder; no plugin
  system exists yet, `tools/` ships standalone functions instead.
- Dependency management is **UV-based** (`pyproject.toml` + `uv.lock`) —
  use `uv sync` / `uv run`, not raw `pip install` + `python`.

## API key

`app/agents.py` calls `load_dotenv()` and constructs the OpenAI SDK client
at **module import time** with `api_key=os.environ["OPENROUTER_API_KEY"]`
and `base_url="https://openrouter.ai/api/v1"`. `OPENAI_API_KEY` is not used.
Importing any `app.*` module without `OPENROUTER_API_KEY` set raises
`KeyError`; `tests/conftest.py` sets a placeholder so tests import offline.
(The earlier `OPENAI_API_KEY`/`OPENROUTER_API_KEY` mismatch is fixed; see
`TASK.md` "High priority".)

## Commands

```bash
uv sync                                          # setup
uv run uvicorn app.main:app --reload --port 8000 # run locally
uv run pytest                                    # tests (offline, no API key needed)
```

## Layout

- `app/main.py` — FastAPI app, single route `GET /run`.
- `app/graph.py` — `StateGraph(AgentState)`, three nodes: `"planner"`
  (entry) → `"executor"` → `"synthesizer"` (finish), with a conditional
  edge (`should_replan`) from `"executor"` back to `"planner"`. Also owns
  `execute_step`, which dispatches each step to the tool chosen by
  `route_step_to_tool`.
- `app/agents.py` — `planner_agent(query)` calls the LLM via OpenRouter;
  `parse_steps(raw)` splits its output into a list of step strings;
  `route_step_to_tool(step)` asks the LLM for `calculator`/`search`/
  `passthrough`. API errors propagate (no silent fallback).
- `app/state.py` — `AgentState` TypedDict: `query`, `steps`, `tool_results`,
  `errors` (steps that failed), `attempts` (planner run count), `result`
  (populated by `"synthesizer"`).
- `memory/store.py` — `save_run`/`load_run`/`list_runs`, append-only
  JSON-Lines persistence to `data/runs.jsonl` (gitignored). `main.py` saves
  every `/run` call; nothing reads past runs back into the graph yet.
- `tools/calculator_tool.py` — safe `ast`-based arithmetic evaluator (no
  `eval`), `tools/search_tool.py` — placeholder search. Both are called
  from `app/graph.py`'s `"executor"` node.
- `tests/` — `test_tools.py`, `test_agents.py`, `test_graph.py`,
  `test_store.py`, `test_main.py`; 29 tests, all offline. `conftest.py`
  replaces `app.graph.route_step_to_tool` with a keyword-based fake via an
  autouse fixture; tests that run the full graph also mock `planner_agent`.
  Mock LLM calls in tests, never add test-mode branches to `app/`.
- `.github/workflows/ci.yml` — runs `uv sync` + `uv run pytest` on pushes and
  PRs to `master`.

## Conventions

- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): description`.
- 3-node graph with conditional edges and LLM-driven routing:
  `planner` → `executor` (uses LLM to route each step to calculator/search/passthrough)
  → conditional split [`synthesizer` on success | `planner` on error, max 3 planner runs].
  The executor calls `route_step_to_tool` to decide tool usage; calculator tool
  extracts and evaluates arithmetic from prose. Adding more agent steps means
  adding nodes/edges to the `StateGraph` in `app/graph.py`.
- Persistence is write-only: `memory/store.py` records each run (including
  `errors` and `attempts` counters), but no route or graph node reads a past
  run back in yet.
