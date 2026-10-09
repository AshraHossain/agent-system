# CLAUDE.md — OpsPilot AI

Guidance for Claude Code when working in this repository.

## What this is

OpsPilot AI: a read-only, multi-agent network-operations investigation system
on **Google ADK 2.11.0** (pinned). Read [PLAN.md](PLAN.md) and
[ARCHITECTURE.md](ARCHITECTURE.md) before changing code.

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

## Conventions

- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): ...`.
- Keep the mock deterministic; never assert live-model outputs exactly.
- Do not claim production readiness.
