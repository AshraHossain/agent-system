# CLAUDE.md — Traffic Flow Analyzer

Guidance for Claude Code in this sub-project. It follows the conventions of the sibling
OpsPilot project (see `../CLAUDE.md`) but uses **PydanticAI** and **Gradio**, with its own
`pyproject.toml` and lockfile so ADK's pinned dependencies are never touched.

Read [PLAN.md](PLAN.md) and [ARCHITECTURE.md](ARCHITECTURE.md) before changing code; open work is in
[TASK.md](TASK.md).

## What this is

Forecasts road congestion from a (simulated) real-time speed feed. PydanticAI agents call
read-only tools; a deterministic core does all the numbers; a Gradio dashboard shows
interactive Plotly plots and an AI briefing. Not production-ready; do not claim it is.

## Commands (run inside `traffic-flow-analyzer/`)

```bash
uv sync
uv run pytest                              # offline suite (mock model), live excluded
uv run ruff check . && uv run ruff format --check .
uv run trafficflow demo                    # briefing in the terminal (mock model)
uv run trafficflow backtest --horizon 60   # forecaster vs persistence
uv run trafficflow fetch-data              # PeMSD7 real data -> var/data (not committed)
uv run trafficflow fetch-data --dataset pems08   # PEMS08, includes weekends
uv run trafficflow evaluate var/data/PeMSD7_V_228.csv
uv run trafficflow serve                   # Gradio dashboard on :7860 (add --replay <csv>)
TRAFFICFLOW_MODEL=<provider:model> uv run trafficflow demo   # live model (needs its API key)
```

## Layering rules (do not break)

- `trafficflow/contracts/` and `trafficflow/core/` must not import `pydantic_ai` or `gradio`
  (guarded by `tests/test_agents.py`).
- Forecasts, congestion thresholds, incident detection and network status live in `core/`,
  never in prompts or LLM output. The LLM never sets status.
- Tools are read-only, validate `segment_id` against the feed, bound numeric args, return
  `{"status": "ok"|"error", ...}` and record evidence via `TrafficDeps.record`.
  Tools never accept paths, SQL, URLs or commands from the model.
- Held-back future data (`truth`, `next_values`) is used only by `core/evaluation.py` and
  `core/backtest.py`, never by tools/agents.
- Never tune forecaster constants on the evaluation data; use a train/test split and report
  only the held-out part. Do not commit datasets (`var/` and `data/` are gitignored).
- New LLM agent => output contract in `contracts/`, prompt in `agents/prompts.py`, mock policy
  in `agents/mock_models.py`, factory in `agents/agents.py`, and tests.
- Every brief citation must exist in the recorded evidence (`verify_brief`).
- The live dashboard tick never calls a model; briefing and Q&A are explicit button actions.

## Conventions

- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): ...`.
- Keep the mock deterministic; never assert live-model outputs exactly.
- Default model is `mock`; no model name is hardcoded, use `TRAFFICFLOW_MODEL`.
