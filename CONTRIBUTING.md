# Contributing

1. `uv sync` (UV only).
2. Read [PLAN.md](PLAN.md), [ARCHITECTURE.md](ARCHITECTURE.md) and [CLAUDE.md](CLAUDE.md).
3. Keep `opspilot/contracts` and `opspilot/core` free of `google.adk` imports.
4. Add or update tests; the default suite must pass without credentials:
   `uv run pytest && uv run ruff check . && uv run ruff format --check .`
5. If you change rules, data or agents, re-run `uv run opspilot eval --guardrails`
   and update `docs/eval_results_offline.md`.
6. Conventional commits: `feat|fix|test|refactor|docs|chore(scope): description`.
7. Never commit credentials; use `.env` (git-ignored) based on `.env.example`.
