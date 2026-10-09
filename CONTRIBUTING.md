# Contributing to agent-system

This is an early-stage FastAPI + LangGraph project. Start with
[README.md](README.md) for setup and behavior, then read
[PLANNING.md](PLANNING.md) — it documents the architecture, module
responsibilities, and known constraints (placeholder search tool,
re-planning that doesn't see what failed, write-only run storage) so you
don't rediscover them from scratch.

## Development Setup

Dependency management is [UV](https://docs.astral.sh/uv/)-based
(`pyproject.toml` + `uv.lock`):

```bash
uv sync
echo "OPENROUTER_API_KEY=sk-or-v1-..." > .env   # only needed to run the server
uv run pytest                                  # offline; no key needed
```

Run the service locally:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

## Framework

This repo follows the SuperClaude Framework structure — see
[PLANNING.md](PLANNING.md) for architecture and [TASK.md](TASK.md) for the
prioritized backlog. Check `TASK.md` before starting work; open gaps
(real search, feeding failures back into re-planning, word-phrased
arithmetic, a route for stored runs) are already tracked there.

## Guidelines

- Keep `app/state.py`'s `AgentState` the single source of truth for graph
  state shape; don't add ad hoc dicts alongside it.
- New graph nodes go in `app/graph.py`; new agent logic goes in
  `app/agents.py`. Keep the FastAPI route in `app/main.py` thin.
- Tools under `tools/` are plain functions dispatched by `execute_step` in
  `app/graph.py` based on `route_step_to_tool`'s label. A new tool needs a
  label in the router prompt and a branch in `execute_step`.
- Tests must stay offline: mock LLM calls (see `tests/conftest.py`) rather
  than adding test-mode branches to `app/`.
- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): description`.

## Definition of done

- `uv run pytest` green locally and in CI.
- New behavior documented in `PLANNING.md` if it changes the architecture
  or request flow.
