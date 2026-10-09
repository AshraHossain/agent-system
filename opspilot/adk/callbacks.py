"""Agent/model callbacks shared by the LLM agents."""

from __future__ import annotations

import json
import logging

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

from opspilot.adk.mock_policies import failed_output
from opspilot.adk.models import Final, to_response

log = logging.getLogger(__name__)

NEUTRAL_USER_TEXT = (
    "Investigate according to the scope and context in your instructions. "
    "The original request has been validated and redacted into the scope."
)


def sanitize_user_content(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """Replace raw user text with a neutral prompt; the redacted request lives in `scope`.

    With include_contents='none' ADK still forwards the current user message. This
    ensures the unredacted request (which may contain secrets or injection attempts)
    never reaches a model directly.
    """
    for content in llm_request.contents or []:
        if content.role != "user":
            continue
        for part in content.parts or []:
            if part.text is not None and part.function_response is None:
                part.text = NEUTRAL_USER_TEXT
    return None


def _completed(value) -> bool:
    """An output counts as done unless missing or explicitly 'failed' (re-run on resume)."""
    return value is not None and not (isinstance(value, dict) and value.get("status") == "failed")


def skip_if_done(output_key: str):
    """before_agent_callback: skip a stage on resume or after an intake rejection.

    ADK validates the returned content against the agent's `output_schema`, so the
    skip returns schema-valid JSON: the existing output (resume) or a 'failed'
    output (rejected request).
    """

    def callback(callback_context: CallbackContext) -> types.Content | None:
        state = callback_context.state
        if state.get("intake_error"):
            payload = failed_output(callback_context.agent_name, "request rejected at intake")
        elif _completed(state.get(output_key)):
            payload = state.get(output_key)
        else:
            return None
        return types.Content(role="model", parts=[types.Part(text=json.dumps(payload))])

    return callback


def model_error_fallback(agent_name: str):
    """on_model_error_callback: degrade to a schema-valid 'failed' output, no retries here.

    Bounded retries for transient errors happen in the HTTP client (HttpRetryOptions);
    once those are exhausted, or on timeouts/quota errors, the stage fails explicitly
    and the pipeline continues so the report can say what is missing.
    """

    def callback(
        callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
    ) -> LlmResponse | None:
        reason = f"model error ({type(error).__name__}): {str(error)[:160]}"
        log.warning("%s: %s", agent_name, reason)
        has_tools = "set_model_response" in (llm_request.tools_dict or {})
        return to_response(Final(failed_output(agent_name, reason)), has_tools)

    return callback
