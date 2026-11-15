---
paths:
  - "opspilot/**"
  - "adk_apps/**"
  - "tests/**"
---

# OpsPilot rules (Google ADK 2.11.0, pinned)

Read [PLAN.md](../../PLAN.md) and [ARCHITECTURE.md](../../ARCHITECTURE.md) before changing code.

## Layering (do not break)

- `opspilot/contracts/` and `opspilot/core/` must not import `google.adk`.
- Calculations, thresholds, graph rules, status and escalation live in `core/`, never in prompts
  or LLM outputs.
- Tools are Pydantic-validated, read-only, return `{"status": "ok"|"error", ...}` and record
  evidence via `tool_context.state["evidence:<ID>"]`.
- Tools never accept dataset IDs, paths, SQL, URLs or commands from the model.
- Agents never see case labels (`cases.yaml` `labels`/`world` are read only by the generator and
  `opspilot/eval`). `tests/test_evaluation.py` guards this.
- Adding or changing an LLM agent: follow the `add-llm-agent` skill.
- Keep the mock (`ScriptedLlm`) deterministic.

## ADK behaviour verified for 2.11.0 (`tests/test_adk_contract.py`)

- `output_schema` + `tools` works via an injected `set_model_response` tool.
- `include_contents="none"` still forwards the current user message; it is replaced in
  `before_model_callback` (`sanitize_user_content`).
- `before_agent_callback` content is validated against `output_schema`, so skips must return
  schema-valid JSON.
- `ctx.end_invocation` does not stop a `SequentialAgent`; stages check `intake_error` themselves.
- `LoopAgent` ends when any event it yields has `actions.escalate`; `SequentialAgent` ignores
  `escalate`, so the optional `reinvestigation_gate` ends the rounds without skipping review or
  the finalizer.
- A final *text* answer that fails `output_schema` raises `ValidationError` and aborts the whole
  run (JSON text, even fenced, is accepted); `BudgetPlugin` replaces such answers with the agent's
  `failed_output`. Invalid `set_model_response` arguments go back to the model as
  `{"error": ...}` for another try.
