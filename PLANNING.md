# PLANNING.md — agent-system

## What this is

A minimal FastAPI service that exposes a 3-node LangGraph agent. A GET
request carries a natural-language `query`, which flows through a
plan → execute → synthesize pipeline: a planner agent (an OpenAI
chat-completion call) breaks the query into steps, an executor runs each
step through a tool (calculator or search) or passes it through unchanged,
and a synthesizer assembles the final `result`. The graph returns the
accumulated state as JSON.

This is still an early-stage scaffold, not a production system: the graph is
a fixed linear pipeline (no branching/looping), there is no persistence
wired up, and tool routing is regex-based rather than an LLM-driven decision.

## Architecture

```
HTTP GET /run?query=...
        │
        ▼
  app/main.py            FastAPI app, single route: GET /run
        │
        ▼
  app/graph.py            LangGraph StateGraph (linear: planner -> executor -> synthesizer)
        │
        ├─ "planner" (entry point)
        │     └─ app/agents.py: planner_agent(query) -> str   (OpenAI chat.completions, gpt-4o-mini)
        │        app/agents.py: parse_steps(raw) -> List[str]
        │
        ├─ "executor"
        │     └─ app/graph.py: execute_step(step) -> str, routed per step to:
        │          - tools/calculator_tool.py  (arithmetic steps, e.g. "12 * 4")
        │          - tools/search_tool.py      (steps mentioning search/look up/find information)
        │          - passthrough               (anything else, step text unchanged)
        │
        └─ "synthesizer" (finish point)
              └─ app/graph.py: run_synthesizer — joins each step with its tool result into `result`
        ▼
  app/state.py    AgentState (TypedDict): query, steps, tool_results, result
        │
        ▼
  memory/store.py    save_run(state) -> run_id   (appends to data/runs.jsonl)
```

### Module responsibilities

| Module | Path | Purpose |
|---|---|---|
| FastAPI app | `app/main.py` | Defines the ASGI app and the `GET /run` endpoint. Builds the initial `AgentState`, invokes the compiled graph, and persists the final state via `memory.store.save_run`. |
| Graph | `app/graph.py` | Builds a `StateGraph(AgentState)` with three nodes — `"planner"` (entry) → `"executor"` → `"synthesizer"` (finish) — wired with `add_edge`. Also owns `execute_step`, the regex-based tool router. Compiles to `app_graph`. |
| Agents | `app/agents.py` | `planner_agent(query)` — calls the OpenAI SDK (model `openai/gpt-4o-mini` via OpenRouter) to break the query into steps. `parse_steps(raw)` — splits that freeform text into a `List[str]`, stripping numbering/bullets. |
| State | `app/state.py` | `AgentState` TypedDict: `query: str`, `steps: List[str]`, `tool_results: List[str]`, `result: str`. |
| Memory | `memory/store.py` | `save_run(state) -> run_id`, `load_run(run_id)`, `list_runs()` — append-only JSON-Lines persistence to `data/runs.jsonl` (gitignored). Write-only from the graph's perspective: nothing reads past runs back into a new `/run` call yet. |
| Tools | `tools/calculator_tool.py`, `tools/search_tool.py` | `calculator_tool` safely evaluates arithmetic via `ast` (no `eval`); `search_tool` is still a placeholder. Both are now called from `app/graph.py`'s `"executor"` node. |
| Tests | `tests/` | `test_tools.py`, `test_agents.py`, `test_graph.py`, `test_store.py`, `test_main.py` — 27 tests, all offline (`planner_agent` mocked wherever the graph is exercised; `test_store.py` redirects `STORE_PATH` to a tmp dir). |

### Request flow

1. Client calls `GET /run?query=<text>`.
2. `main.py` invokes `app_graph.invoke({"query": query, "steps": [], "tool_results": [], "result": ""})`.
3. `"planner"` calls `planner_agent(query)` and `parse_steps(...)` the response into `steps`.
4. `"executor"` runs `execute_step` over each entry in `steps`, producing `tool_results`
   (one entry per step, from `calculator_tool`, `search_tool`, or the step text itself).
5. `"synthesizer"` zips `steps` with `tool_results` into a human-readable `result` string.
6. `main.py` calls `memory.store.save_run(result)`, which appends the state to `data/runs.jsonl` and returns a `run_id`.
7. The resulting state dict, plus `run_id`, is returned as JSON.

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

- **Linear graph, no branching** — `graph.py` wires `"planner"` →
  `"executor"` → `"synthesizer"` with fixed `add_edge` calls. There's no
  conditional routing (e.g. re-planning, retries) yet — real branching needs
  `add_conditional_edges`.
- **Tool routing is regex-based, not LLM-driven** — `execute_step` decides
  calculator vs. search vs. passthrough by pattern-matching the step text.
  It only recognizes simple two-operand arithmetic (`"3 + 4"`), not chained
  expressions embedded in prose (`"add 3 to 4 then double it"`).
- **Persistence is write-only** — `memory/store.py` appends each run's final
  state to `data/runs.jsonl` and hands back a `run_id`, but nothing reads a
  past run back into a new `/run` call. There's no cross-run memory in the
  graph itself, and no HTTP route to fetch a stored run by `run_id` yet.
- **`search_tool` is still a placeholder** — it echoes the query rather than
  calling a real search API.

## Next steps (not yet done)

- Add conditional edges (e.g. re-plan on a failed tool call) instead of the
  current fixed linear pipeline.
- Replace regex-based tool routing in `execute_step` with an LLM tool-call
  decision, and/or extend `calculator_tool`'s expression extraction to
  handle multi-step arithmetic phrased in prose.
- Wire `search_tool` to a real search API.
- Read prior runs back into the graph (actual memory, not just storage), and/or
  add a `GET /runs/{run_id}` route to fetch a stored run.
