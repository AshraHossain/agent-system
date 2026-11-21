# CLAUDE.md — agent-system (Repo Specifics)

@AGENTS.md

The import above loads the full project map, verified commands, module layout and layering
rules. It is required: a project `CLAUDE.md` means Claude Code reads it *instead of* `AGENTS.md`,
so without the import none of that reaches the session. Only repository-specific constraints are
listed below.

A personal `~/.claude/CLAUDE.md` is loaded alongside this file when it exists, but it is not in
version control and is absent from cloud sessions, so no rule this repository depends on may live
there alone. (`~/.claude/AGENTS.md` is never read by Claude Code.)

## Quick Verification

```bash
make check                                # ruff lint/format + offline pytest + guardrail eval
uv run opspilot demo --case C03           # offline end-to-end run (ScriptedLlm)
uv run opspilot eval --guardrails         # scorecard + guardrail perturbations -> var/eval/
```

## Repo-Specific Constraints

- **Sub-project isolation**: `ledger-hound/`, `traffic-flow-analyzer/`, `platewatch/`, and `api-tests/` each have their own `pyproject.toml`, `uv.lock`, and CI workflow. Run their commands from inside their respective directories; never add them to the root `pyproject.toml`, `uv.lock`, or `conftest.py`.
- **Google ADK 2.11.0 is pinned** in root `pyproject.toml`. Verify ADK behavioral contracts via `tests/test_adk_contract.py` whenever changing `opspilot/adk/`.
- **Ground-truth separation**: Case `labels` and `world` definitions in `opspilot/datasets/cases/*.yaml` are read exclusively by `opspilot/datasets/generate.py` and `opspilot/eval/`. Never expose them to tools, session state, or prompts (`tests/test_dataset.py` and `tests/test_evaluation.py` enforce this).
- **Honest reporting**: Offline runs use `ScriptedLlm` and measure deterministic pipeline, tool, and verifier invariants — never claim mock evaluation scores measure live LLM reasoning quality.
- **Tracing & OTLP**: `opentelemetry-exporter-otlp-proto-http==1.42.1` is pinned in `dev` dependencies to match `google-adk==2.11.0`'s `opentelemetry-sdk<=1.42.1` bound so all OTLP trace tests in `tests/test_tracing.py` run offline without skipping.

## Planning

Plan non-trivial work in every project (root and sub-projects) with GSD (roadmap) → Superpowers (`brainstorming`, `writing-plans`) → Ruflo (parallel execution), as set out in [PLANNING.md](PLANNING.md). Record outcomes in [TASK.md](TASK.md) and the project's `PLAN.md` phase table. If a plugin is missing in the session, follow the same steps by hand.

## Scoped Rules & Skills

- **Path rules** (`.claude/rules/`): `contracts.md`, `core.md`, `adk.md`, `tests.md`
- **Repo skills** (`.claude/skills/`): `add-llm-agent`, `eval-dataset`, `llm-eval`, `red-team`, `new-agent-project`
