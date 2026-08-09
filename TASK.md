# TASK.md — agent-system

Priority task list. See `PLANNING.md` for architecture context.

## High priority

- [ ] **Fix API key mismatch (confirmed — currently breaks import)** — `.env`
  sets `OPENROUTER_API_KEY`, but `app/agents.py` does `client = OpenAI()` at
  **module import time** with no `load_dotenv()` call anywhere in the codebase.
  Verified directly: `uv run python -c "from app.main import app"` raises
  `openai.OpenAIError: Missing credentials` because `OPENAI_API_KEY` is unset.
  Fix requires two changes: (1) load `.env` (e.g. `load_dotenv()` in
  `app/main.py` or `app/agents.py`), and (2) either point the client at
  OpenRouter's OpenAI-compatible endpoint
  (`OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])`)
  or rename the env var to `OPENAI_API_KEY`. Until fixed, the app fails at
  import time, not just at request time.
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
