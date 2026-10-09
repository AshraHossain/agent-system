# ADR-0009: Scripted BaseLlm mock for offline runs

**Status:** Accepted

`opspilot.adk.models.ScriptedLlm` subclasses ADK's `BaseLlm`. Per agent, a
policy reads the same inputs a real model gets (system instruction, prior
function responses) and emits function calls / `set_model_response` calls.
It supports injected faults (timeout, quota error) for failure tests.

**Limitation:** offline evaluation therefore measures tools, orchestration,
verification and reporting — not LLM reasoning. Live evaluation is separate
(`opspilot eval --live`, `pytest -m live`).
