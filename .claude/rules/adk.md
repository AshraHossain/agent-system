---
paths:
  - "opspilot/adk/**"
  - "adk_apps/**"
---

# OpsPilot ADK Layer Rules (`opspilot/adk/**`, `adk_apps/**`)

- **Thin Adapter Layer**: Tools in `opspilot/adk/tools.py` validate arguments via Pydantic, resolve the session-bound `Runtime` and `InvestigationScope`, delegate all computation to `opspilot/core/`, register returned `Evidence` under `tool_context.state["evidence:<ID>"]`, and return `{"status": "ok"|"error", ...}`.
- **No Model-Controlled Scope**: Tools never accept dataset IDs, file paths, SQL queries, URLs, or shell commands from the model.
- **ADK 2.11.0 Behavioral Invariants** (verified in `tests/test_adk_contract.py`):
  - `output_schema` + `tools` works via ADK's injected `set_model_response` tool.
  - `include_contents="none"` still forwards the current user turn; `sanitize_user_content` in `before_model_callback` replaces it with `NEUTRAL_USER_TEXT` so unredacted user input never reaches the model.
  - `before_agent_callback` output is validated against `output_schema`; `skip_if_done` must return schema-valid JSON.
  - `ctx.end_invocation` does not halt `SequentialAgent`; downstream stages check `intake_error` explicitly.
  - `LoopAgent` terminates on `actions.escalate=True` from `ReinvestigationGateAgent` without skipping `review_verifier` or `finalizer`.
  - Final text answers that fail `output_schema` are intercepted by `BudgetPlugin.after_model_callback` and replaced with `failed_output(agent, reason)`.
- **Lockstep Updates**: When adding or modifying an agent or tool, follow `.claude/skills/add-llm-agent/SKILL.md` (update contract, prompt provider, `AGENT_TOOLS`/`TOOL_ALLOWLIST`, `ScriptedLlm` mock policy, and tests together).
