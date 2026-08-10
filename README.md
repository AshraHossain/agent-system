# agent-system

A minimal FastAPI service that exposes a single-node LangGraph agent. A
`GET` request carries a natural-language `query`, which is handed to a
planner agent (an OpenAI chat-completion call) that breaks it into steps.
The graph returns the accumulated state as JSON.

This is an early-stage scaffold, not a production system: there is one
graph node, no persistence wired up, and the two tool stubs in `tools/`
are not yet called from the graph. See [PLANNING.md](PLANNING.md) and
[TASK.md](TASK.md) for the full, current picture — including a known
`OPENAI_API_KEY` / `OPENROUTER_API_KEY` mismatch that is tracked but not
yet fixed.

## Framework

This repo follows the **SuperClaude Framework** structure for AI-agent-assisted
development: [`PLANNING.md`](PLANNING.md) is the architecture reference
(module map, request flow, external dependencies, constraints),
[`TASK.md`](TASK.md) tracks prioritized work, and
[`plugins/README.md`](plugins/README.md) documents the (currently empty)
plugin extension point. Dependency management is UV-based (`pyproject.toml`
+ `uv.lock`).

## Getting Started

```bash
uv sync                       # installs FastAPI, LangGraph, OpenAI SDK, etc.
uv run uvicorn app.main:app --reload --port 8000
```

Then, with the server running:

```bash
curl "http://localhost:8000/run?query=plan+a+trip+to+Tokyo"
```

> **Known issue:** the OpenAI client in `app/agents.py` is constructed at
> import time from `OPENAI_API_KEY`, but `.env` currently only sets
> `OPENROUTER_API_KEY`. As shipped, `GET /run` will fail unless
> `OPENAI_API_KEY` is set in your environment. See
> [TASK.md](TASK.md#high-priority) for the tracked fix — not addressed in
> this documentation update.

### Docker

```bash
docker build -t agent-system .
docker run -p 8000:8000 -e OPENAI_API_KEY=sk-... agent-system
```

The image installs dependencies via `uv sync --frozen` at build time and
runs `uvicorn app.main:app` directly (see [Dockerfile](Dockerfile)).

### Tests

```bash
uv run pytest
```

`tests/` currently exists but is empty — no test suite yet (tracked in
[TASK.md](TASK.md)).

## Architecture

```
HTTP GET /run?query=...
        │
        ▼
  app/main.py            FastAPI app, single route: GET /run
        │
        ▼
  app/graph.py            LangGraph StateGraph
        │                 - one node: "planner" (entry point = finish point)
        ▼
  app/agents.py            planner_agent(query) -> str
        │                 - calls OpenAI chat.completions (model: gpt-4o-mini)
        ▼
  app/state.py              AgentState (TypedDict): query, steps, result
```

See [PLANNING.md](PLANNING.md) for the full module responsibility table,
request flow, and known constraints (single-node graph, no persistence,
tools not yet wired in).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.

## License

Proprietary. See [LICENSE](LICENSE). All rights reserved.
