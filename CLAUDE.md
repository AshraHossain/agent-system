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
- `app/graph.py` — `StateGraph(AgentState)`, one node (`"planner"`, entry
  and finish point).
- `app/agents.py` — `planner_agent(query)`, calls the OpenAI SDK.
- `app/state.py` — `AgentState` TypedDict: `query`, `steps`, `result`
  (`result` is currently never populated — see `TASK.md`).
- `memory/store.py` — empty placeholder for a future persistence layer.
- `tools/calculator_tool.py`, `tools/search_tool.py` — standalone
  functions, not yet bound into the graph.
- `tests/` — empty; no test framework configured yet.

## Conventions

- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): description`.
- Single-node graph today: adding agent steps means adding nodes/edges to
  the `StateGraph` in `app/graph.py`.
- No persistence yet: state does not survive a single `/run` call.
