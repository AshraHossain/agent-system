# PLANNING.md — agent-system

## What this is

A minimal FastAPI service that exposes a single-node LangGraph agent. A GET
request carries a natural-language `query`, which is handed to a planner
agent (an OpenAI chat-completion call) that breaks it into steps. The graph
returns the accumulated state as JSON.

This is an early-stage scaffold, not a production system: there is one graph
node, no persistence wired up, and the two tool stubs in `tools/` are not yet
called from the graph.

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

### Module responsibilities

| Module | Path | Purpose |
|---|---|---|
| FastAPI app | `app/main.py` | Defines the ASGI app and the `GET /run` endpoint. Builds the initial `AgentState` and invokes the compiled graph. |
| Graph | `app/graph.py` | Builds a `StateGraph(AgentState)` with a single `"planner"` node that is both entry and finish point. Compiles to `app_graph`. |
| Agents | `app/agents.py` | `planner_agent(query)` — calls the OpenAI SDK (`OpenAI()` client, model `gpt-4o-mini`) to break the query into steps. |
| State | `app/state.py` | `AgentState` TypedDict: `query: str`, `steps: List[str]`, `result: str`. |
| Memory | `memory/store.py` | Currently empty — placeholder for a future memory/persistence layer. |
| Tools | `tools/calculator_tool.py`, `tools/search_tool.py` | Standalone utility functions (`calculator_tool`, `search_tool`). Not yet wired into the graph as LangGraph/LangChain tools. |
| Tests | `tests/` | Directory exists but is currently empty — no test suite yet. |

### Request flow

1. Client calls `GET /run?query=<text>`.
2. `main.py` invokes `app_graph.invoke({"query": query, "steps": [], "result": ""})`.
3. The graph runs the single `"planner"` node, which calls `planner_agent(query)`
   and appends its output to `steps`.
4. The resulting state dict (including `steps`) is returned as the JSON response.
5. Note: `result` is initialized but never populated by the current graph — the
   response's useful content is in `steps`.

### External dependencies

- **OpenAI API** — `app/agents.py` instantiates `OpenAI()` with no explicit key,
  so it relies on the `OPENAI_API_KEY` environment variable at runtime (the
  standard OpenAI SDK default).
- **`.env`** — currently defines `OPENROUTER_API_KEY`, not `OPENAI_API_KEY`.
  This is a real mismatch worth flagging: as written, `app/agents.py` will not
  pick up the OpenRouter key automatically unless the client is later
  reconfigured (e.g. `base_url` + `OPENAI_API_KEY=<openrouter key>`, or the
  code is changed to read `OPENROUTER_API_KEY` explicitly). Not fixed as part
  of this migration — flagged here for follow-up.

## Key design constraints

- **Single-node graph today** — `graph.py` only wires up `"planner"`. Adding
  more agent steps means adding nodes/edges to the `StateGraph` in that file.
- **No persistence yet** — `memory/store.py` is an empty stub; state does not
  survive a single `/run` call.
- **Tools not yet integrated** — `calculator_tool` and `search_tool` are plain
  functions, not bound into the LangGraph flow or exposed as LangChain tools.
- **No test framework configured** — `tests/` exists but is empty.

## Next steps (not yet done)

- Wire `OPENROUTER_API_KEY` (or switch to `OPENAI_API_KEY`) consistently
  between `.env` and `app/agents.py`.
- Bind `calculator_tool` / `search_tool` into the graph as callable tools.
- Add a persistence layer under `memory/store.py`.
- Add tests under `tests/`.
