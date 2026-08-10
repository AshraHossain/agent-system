# Contributing to agent-system

This is an early-stage FastAPI + LangGraph scaffold. Read
[PLANNING.md](PLANNING.md) first — it documents the current architecture,
module responsibilities, and known constraints (single-node graph, no
persistence, tools not yet wired in, an `OPENAI_API_KEY`/`OPENROUTER_API_KEY`
mismatch tracked in [TASK.md](TASK.md)) so you don't rediscover them from
scratch.

## Development Setup

Dependency management is [UV](https://docs.astral.sh/uv/)-based
(`pyproject.toml` + `uv.lock`):

```bash
uv sync
uv run pytest
```

Run the service locally:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

## Framework

This repo follows the SuperClaude Framework structure — see
[PLANNING.md](PLANNING.md) for architecture and [TASK.md](TASK.md) for the
prioritized backlog. Check `TASK.md` before starting work; several
known issues (API key mismatch, empty test suite, unwired tools) are
already tracked there rather than needing rediscovery.

## Guidelines

- Keep `app/state.py`'s `AgentState` the single source of truth for graph
  state shape; don't add ad hoc dicts alongside it.
- New graph nodes go in `app/graph.py`; new agent logic goes in
  `app/agents.py`. Keep the FastAPI route in `app/main.py` thin.
- Tools under `tools/` should be bound into the graph as proper
  LangGraph/LangChain tools when wired up, not called ad hoc.
- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): description`.

## Definition of done

- `uv run pytest` green (once a test suite exists — see `TASK.md`).
- New behavior documented in `PLANNING.md` if it changes the architecture
  or request flow.
