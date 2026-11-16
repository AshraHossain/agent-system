# Contributing

1. Install dependencies with `uv sync` (`uv` only; never raw `pip`).
2. Read [AGENTS.md](AGENTS.md), [GEMINI.md](GEMINI.md), [CLAUDE.md](CLAUDE.md), [PLAN.md](PLAN.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [SECURITY.md](SECURITY.md).
3. Enforce strict architectural layering:
   - `opspilot/contracts/` and `opspilot/core/` must never import `google.adk`, `google.genai`, or `fastapi`.
   - Every Pydantic contract and core DTO must declare `model_config = ConfigDict(frozen=True)` or `@dataclass(frozen=True)`.
   - All calculations, thresholds, ranking rules, verification checks, and status decisions belong in `opspilot/core/` — never in prompts.
4. Follow Test-Driven Development (TDD):
   - Categorize tests with `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.live`.
   - All default tests must run offline using `ScriptedLlm` with zero skipped tests and >=80% per-module coverage.
5. Run the full verification gate before committing:
   ```bash
   make check          # ruff lint/format + offline pytest (>=80% cov) + guardrail eval
   make check-all      # full workspace gate across OpsPilot + all 4 independent sub-projects
   ```
6. Update the `graphify` knowledge graph (`graphify-out/`) when adding modules or changing architectural boundaries.
7. Use Conventional Commits: `feat|fix|test|refactor|docs|chore|perf|ci(scope): description`.
8. Never commit credentials or `.env` files; copy `.env.example` to `.env` (git-ignored) for local live-model testing.
