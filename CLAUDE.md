# CLAUDE.md — agent-system

Guidance for Claude Code when working in this repository.

## What this is

A minimal FastAPI service exposing a single-node LangGraph agent: `GET
/run?query=...` invokes a `planner` agent (OpenAI chat completion,
`gpt-4o-mini`) that breaks the query into steps, and the resulting graph
state is returned as JSON. This is an early-stage scaffold — read
`PLANNING.md` before changing code; it documents the architecture, module
responsibilities, and constraints in full.

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

## Known issue — do not "fix" silently

`app/agents.py` constructs `OpenAI()` at **module import time**, reading
`OPENAI_API_KEY`. `.env` currently sets `OPENROUTER_API_KEY`, not
`OPENAI_API_KEY` — as shipped, importing `app.main` raises
`openai.OpenAIError: Missing credentials`. This is a confirmed, tracked bug
(see `TASK.md` "High priority"). Do not silently patch around it in
unrelated changes; if you're asked to fix it, the tracked fix requires
`load_dotenv()` plus either pointing the client at OpenRouter's
OpenAI-compatible endpoint or renaming the env var.

## Commands

```bash
uv sync                                          # setup
uv run uvicorn app.main:app --reload --port 8000 # run locally
uv run pytest                                    # tests (tests/ is currently empty)
```

## Layout

- `app/main.py` — FastAPI app, single route `GET /run`.
- `app/graph.py` — `StateGraph(AgentState)`, three nodes: `"planner"`
  (entry) → `"executor"` → `"synthesizer"` (finish). Also owns
  `execute_step`, the regex-based router that sends each step to a tool.
- `app/agents.py` — `planner_agent(query)` calls the OpenAI SDK;
  `parse_steps(raw)` splits its output into a list of step strings.
- `app/state.py` — `AgentState` TypedDict: `query`, `steps`, `tool_results`,
  `result` (`result` is now populated by the `"synthesizer"` node).
- `memory/store.py` — `save_run`/`load_run`/`list_runs`, append-only
  JSON-Lines persistence to `data/runs.jsonl` (gitignored). `main.py` saves
  every `/run` call; nothing reads past runs back into the graph yet.
- `tools/calculator_tool.py` — safe `ast`-based arithmetic evaluator (no
  `eval`), `tools/search_tool.py` — placeholder search. Both are called
  from `app/graph.py`'s `"executor"` node.
- `tests/` — `test_tools.py`, `test_agents.py`, `test_graph.py`,
  `test_store.py`, `test_main.py`; 27 tests, all offline.

## Conventions

- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): description`.
- Linear 3-node graph today (`planner` → `executor` → `synthesizer`): adding
  more agent steps means adding nodes/edges to the `StateGraph` in
  `app/graph.py`. No conditional routing yet.
- Persistence is write-only: `memory/store.py` records each run, but no
  route or graph node reads a past run back in yet.
