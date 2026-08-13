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
- [ ] **Add a test suite** — `tests/` exists but is empty. No test framework is
  configured yet (no pytest in `requirements.txt` / `pyproject.toml`).

## Medium priority

- [ ] **Wire tools into the graph** — `tools/calculator_tool.py` and
  `tools/search_tool.py` are standalone functions, not yet bound as LangGraph
  nodes or LangChain tools callable by the planner.
- [ ] **Implement `memory/store.py`** — currently an empty file. Needed once
  the graph has more than one turn/node worth persisting.
- [ ] **Expand the graph beyond a single node** — `app/graph.py` currently has
  one node (`planner`) that is both entry and finish point. Real multi-step
  agent behavior needs additional nodes/edges.
- [ ] **Populate `AgentState.result`** — the state includes a `result` field
  that is initialized but never set by the current graph logic.

## Low priority / infra

- [x] Git repository initialized (`git init`, local identity set).
- [x] UV-based dependency management (`pyproject.toml` + `uv.lock`).
- [x] Dockerfile for containerized runs.
- [ ] CI workflow (lint/test on push) — not yet configured.
- [ ] Structured logging / observability for the FastAPI service.
