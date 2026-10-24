# CLAUDE.md — OpsPilot AI

Guidance for Claude Code when working in this repository.

## What this is

OpsPilot AI: a read-only, multi-agent network-operations investigation system
on **Google ADK 2.11.0** (pinned). Read [PLAN.md](PLAN.md) and
[ARCHITECTURE.md](ARCHITECTURE.md) before changing code.

This file covers OpsPilot (`opspilot/`, `adk_apps/`, `tests/`). The repository
also holds independent projects with their own conventions: `ledger-hound/`,
`traffic-flow-analyzer/` and `platewatch/` (self-contained; see their
README/CLAUDE.md and run commands from inside the folder). The layering rules
below apply to OpsPilot only.

## Commands

```bash
uv sync                                   # setup (UV only; no raw pip)
uv run pytest                             # offline suite (live tests excluded by default)
uv run pytest -m live                     # needs GOOGLE_API_KEY or Vertex AI ADC
uv run ruff check . && uv run ruff format --check .
uv run opspilot demo --case C03           # end-to-end demo (mock model)
uv run opspilot eval --guardrails         # evaluation -> var/eval/
uv run adk web adk_apps                   # ADK dev UI
```

## Layering rules (do not break)

- `opspilot/contracts/` and `opspilot/core/` must not import `google.adk`.
- Calculations, thresholds, graph rules, status and escalation live in `core/`
  — never in prompts or LLM outputs.
- Tools are Pydantic-validated, read-only, return `{"status": "ok"|"error", ...}`
  and record evidence via `tool_context.state["evidence:<ID>"]`.
- Tools never accept dataset IDs, paths, SQL, URLs or commands from the model.
- Agents never see case labels (`cases.yaml` `labels`/`world` are read only by
  the generator and `opspilot/eval`). `tests/test_evaluation.py` guards this.
- New LLM agent ⇒ add an output contract, an instruction provider in
  `adk/prompts.py`, a mock policy + `failed_output` in `adk/mock_policies.py`,
  an allowlist entry in `adk/agents.py`, and contract tests.

## ADK specifics verified for 2.11.0 (see tests/test_adk_contract.py)

- `output_schema` + `tools` works via an injected `set_model_response` tool.
- `include_contents="none"` still forwards the current user message — we
  replace it in `before_model_callback` (`sanitize_user_content`).
- `before_agent_callback` content is validated against `output_schema`, so
  skips must return schema-valid JSON.
- `ctx.end_invocation` does not stop a `SequentialAgent`; stages check
  `intake_error` themselves.
- `LoopAgent` ends when any event it yields has `actions.escalate`;
  `SequentialAgent` ignores `escalate`, so the optional `reinvestigation_gate`
  ends the rounds without skipping review or the finalizer.

## Sub-projects

- `traffic-flow-analyzer/` — PydanticAI-based congestion forecasting with Gradio dashboard; see its own CLAUDE.md and PLAN.md.

## Conventions

- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): ...`.
- Keep the mock deterministic; never assert live-model outputs exactly.
- Do not claim production readiness.

## PlateWatch (`platewatch/`) — PydanticAI licence-plate module

Separate package beside `opspilot/` (see [platewatch/README.md](platewatch/README.md)); it does not use ADK.

- `platewatch/core/` must not import `pydantic_ai`, `torch`, `transformers` or `google`
  (`tests/test_platewatch.py` enforces it). Normalisation, tag thresholds, embedding and the
  store live there — never in prompts.
- Agent tools take no paths, URLs or SQL from the model (the inbox is fixed in `PlateDeps`); every
  plate id in agent output must have been returned by a tool (output validator).
- Detector/reader are `Protocol`s in `vision/`; Hugging Face backends import torch lazily and are
  covered only by `-m live` (`tests/test_platewatch_live.py`). Offline tests use `ScriptedVision`.
- Commands: `uv run platewatch demo`; `uv run pytest tests/test_platewatch.py`.
