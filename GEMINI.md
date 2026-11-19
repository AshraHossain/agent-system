# GEMINI.md — Antigravity Entrypoint (`agent-system` / OpsPilot AI)

This file configures **Google Antigravity (AGY)** for the `agent-system` workspace and links the repository's portable engineering standards with your global Antigravity workflow plugin.

## 1. Canonical Sources of Truth

- **Repository Architecture & Commands**: Read [AGENTS.md](AGENTS.md) first for the project map, verified CLI/Makefile commands, module boundaries, and layering invariants.
- **Design & System Diagrams**: Read [ARCHITECTURE.md](ARCHITECTURE.md), [PLAN.md](PLAN.md), and [SECURITY.md](SECURITY.md) before modifying agent orchestration, tools, or verification logic.
- **Planning Workflow**: [PLANNING.md](PLANNING.md) (GSD roadmap → Superpowers plan → Ruflo execution) applies to the root project and every sub-project; the backlog is [TASK.md](TASK.md).
- **Global Antigravity Plugin**: Follows `~/.gemini/config/plugins/gemini-core-workflow/rules/AGENTS.md` and `~/.gemini/config/plugins/gemini-core-workflow/skills/enterprise-readme-architecture/SKILL.md`.

## 2. Path-Specific Rules (`.agents/rules/` -> `.claude/rules/`)

Antigravity loads path-scoped rules from `.agents/rules/` (symlinked to `.claude/rules/`):

| Rule File | Path Filter | Enforced Invariants |
|---|---|---|
| [.agents/rules/contracts.md](.agents/rules/contracts.md) | `opspilot/contracts/**` | `ConfigDict(frozen=True)`, bounded collections/strings, Gemini-compatible schemas (no free-form dicts in `output_schema`), zero ADK imports |
| [.agents/rules/core.md](.agents/rules/core.md) | `opspilot/core/**` | Pure deterministic functions, zero `google.adk` imports, read-only SQLite (`mode=ro`), `<untrusted_context>` framing, explicit rule tables |
| [.agents/rules/adk.md](.agents/rules/adk.md) | `opspilot/adk/**`, `adk_apps/**` | ADK 2.11.0 contracts (`set_model_response`, `sanitize_user_content`, `BudgetPlugin`), per-agent tool allowlists, deterministic `ScriptedLlm` parity |
| [.agents/rules/tests.md](.agents/rules/tests.md) | `tests/**` | Offline-first (`-m 'not live'`), `@pytest.mark.unit` / `@pytest.mark.integration` / `@pytest.mark.live`, >=80% per-module coverage, zero skipped offline tests |

## 3. Repository Skills (`.agents/skills/` -> `.claude/skills/`)

Use these repo-specific multi-step procedures when extending agents, datasets, evaluations, or red-team checks:

- [add-llm-agent](.agents/skills/add-llm-agent/SKILL.md) — Add or modify an LLM agent, tool, output contract, prompt provider, and `ScriptedLlm` mock policy in lockstep.
- [eval-dataset](.agents/skills/eval-dataset/SKILL.md) — Add or modify synthetic cases (`cases.yaml`, `heldout.yaml`), RAG distractors, and ground-truth isolation guards.
- [llm-eval](.agents/skills/llm-eval/SKILL.md) — Run offline pipeline evaluations, repeated live Gemini benchmarks (`--repeat N`), and per-agent schema compliance reports.
- [red-team](.agents/skills/red-team/SKILL.md) — Add guardrail perturbations (`perturbations.py`) and verifier detection matrix mutations (`verifier_matrix.py`).
- [new-agent-project](.agents/skills/new-agent-project/SKILL.md) — Scaffold a new self-contained sub-project without coupling it to the root `pyproject.toml` or lockfile.

## 4. Verification Gate

Before completing any task or committing:
1. Run `make check` (`uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest --cov=opspilot --cov-fail-under=80`, and `uv run opspilot eval --guardrails`).
2. Update the `graphify` knowledge graph (`graphify-out/`) when modules or architectural edges change.
