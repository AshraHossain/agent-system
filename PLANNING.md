# PLANNING.md — agent-system

## What this is

A minimal FastAPI service that exposes a 3-node LangGraph agent. A GET
request carries a natural-language `query`, which flows through a
plan → execute → synthesize pipeline: a planner agent (an LLM call via
OpenRouter) breaks the query into steps, an executor asks the LLM to route
each step to a tool (calculator or search) or passes it through unchanged,
and a synthesizer assembles the final `result`. If a calculator step fails,
a conditional edge sends control back to the planner, up to 3 planner runs
in total. The final state is saved to a JSON Lines file and returned as JSON.

This is still an early-stage system, not a production one: `search_tool` is
a placeholder, re-planning does not tell the planner what failed, and stored
runs are never read back into the agent. See "Key design constraints" and
"Next steps" below.

## Architecture

```
HTTP GET /run?query=...
        │
        ▼
  app/main.py            FastAPI app, single route: GET /run
        │
        ▼
  app/graph.py            LangGraph StateGraph
        │
        ├─ "planner" (entry point)
        │     └─ app/agents.py: planner_agent(query) -> str   (openai/gpt-4o-mini via OpenRouter)
        │        app/agents.py: parse_steps(raw) -> List[str]
        │        attempts += 1, errors cleared
        │
        ├─ "executor"
        │     └─ app/graph.py: execute_step(step) -> str, per step:
        │          app/agents.py: route_step_to_tool(step) -> label   (LLM call)
        │          - "calculator"  -> tools/calculator_tool.py
        │          - "search"      -> tools/search_tool.py
        │          - anything else -> passthrough (step text unchanged)
        │        steps whose result starts with "Error in calculation" -> errors
        │
        ├─ conditional edge: should_replan(state)
        │     errors and attempts < 3  -> back to "planner"
        │     otherwise                -> "synthesizer"
        │
        └─ "synthesizer" (finish point)
              └─ app/graph.py: run_synthesizer — joins each step with its tool result into `result`
        ▼
  app/state.py    AgentState (TypedDict): query, steps, tool_results, errors, attempts, result
        │
        ▼
  memory/store.py    save_run(state) -> run_id   (appends to data/runs.jsonl)
```

### Module responsibilities

| Module | Path | Purpose |
|---|---|---|
| FastAPI app | `app/main.py` | Defines the ASGI app and the `GET /run` endpoint. Builds the initial `AgentState`, invokes the compiled graph, persists the final state via `memory.store.save_run`, and returns it with `run_id`. |
| Graph | `app/graph.py` | Builds a `StateGraph(AgentState)` with three nodes — `"planner"` (entry) → `"executor"` → `"synthesizer"` (finish) — plus `add_conditional_edges("executor", should_replan, ...)` for re-planning (`_MAX_ATTEMPTS = 3`). Owns `execute_step`, which dispatches a step to the tool `route_step_to_tool` picks. Compiles to `app_graph`. |
| Agents | `app/agents.py` | Module-level OpenAI SDK client pointed at OpenRouter. `planner_agent(query)` breaks the query into steps. `parse_steps(raw)` splits that freeform text into a `List[str]`, stripping numbering/bullets. `route_step_to_tool(step)` asks the LLM to label a step `calculator`/`search`/`passthrough`. All use `openai/gpt-4o-mini`; API errors propagate. |
| State | `app/state.py` | `AgentState` TypedDict: `query: str`, `steps: List[str]`, `tool_results: List[str]`, `errors: List[str]`, `attempts: int`, `result: str`. |
| Memory | `memory/store.py` | `save_run(state) -> run_id`, `load_run(run_id)`, `list_runs()` — append-only JSON-Lines persistence to `data/runs.jsonl` (gitignored). Write-only from the graph's perspective: nothing reads past runs back into a new `/run` call yet. |
| Tools | `tools/calculator_tool.py`, `tools/search_tool.py` | `calculator_tool` evaluates arithmetic via a restricted `ast` evaluator (no `eval`); a bare expression is evaluated whole, otherwise the first `a op b` pair is extracted from prose. `search_tool` is a placeholder. Both are called from the `"executor"` node. |
| Tests | `tests/` | `test_tools.py`, `test_agents.py`, `test_graph.py`, `test_store.py`, `test_main.py` — 29 tests, all offline. `conftest.py` sets a placeholder API key and replaces `app.graph.route_step_to_tool` with a keyword-based fake (autouse fixture); `planner_agent` is mocked where the full graph runs; `test_store.py` redirects `STORE_PATH` to a tmp dir. |
| CI | `.github/workflows/ci.yml` | `uv sync` + `uv run pytest` on Python 3.11 for pushes and PRs to `master`. |

### Request flow

1. Client calls `GET /run?query=<text>`.
2. `main.py` invokes `app_graph.invoke({"query": query, "steps": [], "tool_results": [], "errors": [], "attempts": 0, "result": ""})`.
3. `"planner"` calls `planner_agent(query)`, parses the response into `steps`, increments `attempts` and clears `errors`.
4. `"executor"` runs `execute_step` over each entry in `steps`: `route_step_to_tool` picks a tool and the step is sent to `calculator_tool`, `search_tool`, or passed through. This produces `tool_results` (one per step) and `errors` (steps whose calculator result was an error).
5. `should_replan`: if `errors` is non-empty and `attempts < 3`, go back to step 3 with the same query; otherwise continue.
6. `"synthesizer"` zips `steps` with `tool_results` into a human-readable `result` string.
7. `main.py` calls `memory.store.save_run(result)`, which appends the state to `data/runs.jsonl` and returns a `run_id`.
8. The final state dict, plus `run_id`, is returned as JSON.

Each request therefore makes `attempts × (1 + number of steps)` LLM calls.

### External dependencies

- **OpenRouter** — `app/agents.py` calls `load_dotenv()` and constructs
  `OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url="https://openrouter.ai/api/v1")`
  at import time. `OPENROUTER_API_KEY` must be set (in the environment or
  `.env`) or importing `app.*` raises `KeyError`. Model IDs must be
  provider-prefixed (`openai/gpt-4o-mini`).
- **`.env`** — gitignored; holds `OPENROUTER_API_KEY`.

## Key design constraints

- **Conditional edges for re-planning** — `graph.py` uses
  `add_conditional_edges` to route the executor's output: if any step's
  calculator call failed (division by zero, unparseable input), the agent
  re-plans (back to `"planner"`); otherwise it proceeds to `"synthesizer"`.
  The `attempts` counter caps this at 3 planner runs. Re-planning re-sends
  the same query; the failed steps are not fed back to the planner.
- **LLM-driven tool routing** — `execute_step` calls `route_step_to_tool`
  (in `app/agents.py`) to ask the LLM which tool each step needs. Any label
  other than `calculator` or `search` is treated as passthrough. The router
  does not catch API errors: a failed call fails the request rather than
  silently disabling tools.
- **Calculator input** — only digits and operators are understood. A step
  routed to the calculator but phrased in words (e.g. "Multiply 6 by 7")
  returns `Error in calculation` and triggers a re-plan.
- **Persistence is write-only** — `memory/store.py` appends each run's final
  state to `data/runs.jsonl` and hands back a `run_id`, but nothing reads a
  past run back into a new `/run` call. There's no cross-run memory in the
  graph itself, and no HTTP route to fetch a stored run by `run_id` yet.
- **`search_tool` is still a placeholder** — it echoes the query rather than
  calling a real search API.
- **Tests never hit the network** — mock LLM calls from tests (see
  `tests/conftest.py`); don't add test-mode branches to `app/`.

## Next steps (not yet done)

- Feed failed steps (and their errors) back to the planner on re-plan, so a
  retry can actually change the plan.
- Have the calculator (or the router) handle arithmetic phrased in words, or
  have the LLM rewrite a calculator step into an expression first.
- Wire `search_tool` to a real search API.
- Read prior runs back into the graph (actual memory, not just storage), and/or
  add a `GET /runs/{run_id}` route to fetch a stored run.
