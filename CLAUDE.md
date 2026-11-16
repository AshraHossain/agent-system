# CLAUDE.md — agent-system (Repo Specifics)

Read [AGENTS.md](AGENTS.md) for the full project map, verified commands, module layout, and layering rules. Global workflow, TDD, immutability, and commit rules from `~/.claude/CLAUDE.md` and `~/.claude/AGENTS.md` apply automatically; only repository-specific constraints are listed below.

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

## Scoped Rules & Skills

- **Path rules** (`.claude/rules/`): `contracts.md`, `core.md`, `adk.md`, `tests.md`
- **Repo skills** (`.claude/skills/`): `add-llm-agent`, `eval-dataset`, `llm-eval`, `red-team`, `new-agent-project`
