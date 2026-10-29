"""Runner plugin enforcing execution budgets, tool permissions and collecting metrics.

Limits (see config.Limits): total model calls, model calls per agent (iteration
cap), total tool calls, wall-clock duration. Exceeding a model budget makes the
agent finish with a schema-valid 'failed' output; exceeding the tool budget
returns an error result to the model. `RunConfig.max_llm_calls` and the
runner's asyncio timeout are hard backstops.

Output compliance: a final answer that does not match the agent's output schema
(prose, truncated JSON, no answer at all) is replaced by the agent's 'failed'
output; ADK would otherwise raise and abort the whole run. The plugin counts, per
agent, `set_model_response` calls rejected by validation (ADK lets the model
retry), final answers sent as text by agents that have `set_model_response`, and
every output replaced by a 'failed' one, by reason.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.adk.utils._schema_utils import validate_schema
from pydantic import BaseModel, ValidationError

from opspilot.adk.mock_policies import failed_output
from opspilot.adk.models import Final, to_response
from opspilot.config import Limits

ALWAYS_ALLOWED = {"set_model_response"}
log = logging.getLogger("opspilot.run")


@dataclass
class RunCounters:
    started: float = field(default_factory=time.monotonic)
    llm_calls: int = 0
    tool_calls: int = 0
    llm_by_agent: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tools_by_agent: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    usage_by_agent: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    events: list[str] = field(default_factory=list)
    model_time_ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    tool_time_ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    schema_retries: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    text_answers: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # agent -> reason ("invalid_output", "budget", "model_error") -> count
    fallbacks: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    _model_start: dict[str, float] = field(default_factory=dict)
    _tool_start: dict[str, float] = field(default_factory=dict)
    _has_tools: dict[str, bool] = field(default_factory=dict)

    def elapsed(self) -> float:
        return time.monotonic() - self.started


def output_problem(schema: type[BaseModel], response: LlmResponse) -> str | None:
    """Why a final answer would fail `schema` (the check ADK applies next), or None.

    Tool calls are not final answers: ADK validates `set_model_response` arguments
    itself and lets the model retry. A response without text counts only when it
    carries an error code (blocked, or cut off before any output): when streaming,
    a thought-only chunk can precede a tool call. The message never quotes the
    model's text.
    """
    if response.partial:
        return None
    parts = (response.content.parts or []) if response.content else []
    if any(p.function_call for p in parts):
        return None
    text = "".join(p.text for p in parts if p.text and not p.thought)
    if not text.strip():
        code = response.error_code
        return None if code is None else f"no answer ({getattr(code, 'name', code)})"
    finish = getattr(response.finish_reason, "name", response.finish_reason)
    ended = f" (finish_reason {finish})" if finish not in (None, "STOP") else ""
    try:
        validate_schema(schema, text)
    except ValidationError as e:
        first = e.errors(include_url=False, include_input=False)[0]
        where = ".".join(str(x) for x in first["loc"])
        return (
            f"final answer is not a valid {schema.__name__}{ended}: {e.error_count()} error(s), "
            f"first: {where}{': ' if where else ''}{first['msg']}"
        )
    return None


class BudgetPlugin(BasePlugin):
    def __init__(
        self,
        limits: Limits,
        tool_allowlist: dict[str, set[str]],
        output_schemas: dict[str, type[BaseModel]] | None = None,
    ):
        super().__init__(name="opspilot_budget")
        self.limits = limits
        self.allowlist = tool_allowlist
        self.schemas = output_schemas or {}
        self.runs: dict[str, RunCounters] = {}

    def counters(self, invocation_id: str) -> RunCounters:
        return self.runs.setdefault(invocation_id, RunCounters())

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        c = self.counters(callback_context.invocation_id)
        agent = callback_context.agent_name
        has_tools = c._has_tools[agent] = "set_model_response" in (llm_request.tools_dict or {})
        reason = None
        if c.llm_calls >= self.limits.max_llm_calls:
            reason = f"model request budget exhausted ({self.limits.max_llm_calls})"
        elif c.llm_by_agent[agent] >= self.limits.max_llm_calls_per_agent:
            reason = (
                f"iteration budget exhausted for {agent} ({self.limits.max_llm_calls_per_agent})"
            )
        elif c.elapsed() >= self.limits.max_duration_s:
            reason = f"duration budget exhausted ({self.limits.max_duration_s:.0f}s)"
        if reason:
            c.events.append(f"{agent}: {reason}")
            c.fallbacks[agent]["budget"] += 1
            return to_response(Final(failed_output(agent, reason)), has_tools)
        c.llm_calls += 1
        c.llm_by_agent[agent] += 1
        c._model_start[agent] = time.monotonic()
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        c = self.counters(callback_context.invocation_id)
        agent = callback_context.agent_name
        started = c._model_start.pop(agent, None)
        if started is not None:
            ms = (time.monotonic() - started) * 1000
            c.model_time_ms[agent] += ms
            log.info("model_call agent=%s ms=%.1f", agent, ms)
        usage = llm_response.usage_metadata
        if usage is not None:
            for attr, src in (
                ("prompt_tokens", usage.prompt_token_count),
                ("completion_tokens", usage.candidates_token_count),
                ("total_tokens", usage.total_token_count),
            ):
                if src is not None:
                    setattr(c, attr, (getattr(c, attr) or 0) + src)
                    mine = c.usage_by_agent[agent]
                    mine[attr] = mine.get(attr, 0) + src
        schema = self.schemas.get(agent)
        if schema is None:
            return None
        parts = (llm_response.content.parts or []) if llm_response.content else []
        final_text = not llm_response.partial and not any(p.function_call for p in parts)
        if final_text and c._has_tools.get(agent) and any(p.text and not p.thought for p in parts):
            c.text_answers[agent] += 1
        problem = output_problem(schema, llm_response)
        if problem is None:
            return None
        c.fallbacks[agent]["invalid_output"] += 1
        log.warning("invalid_output agent=%s %s", agent, problem)
        reason = f"invalid output: {problem}"
        return to_response(Final(failed_output(agent, reason)), c._has_tools.get(agent, False))

    async def on_model_error_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
    ) -> LlmResponse | None:
        # Counted here; the agent's own on_model_error_callback supplies its failed output.
        c = self.counters(callback_context.invocation_id)
        c.fallbacks[callback_context.agent_name]["model_error"] += 1
        return None

    async def before_tool_callback(
        self, *, tool: BaseTool, tool_args: dict, tool_context: ToolContext
    ) -> dict | None:
        c = self.counters(tool_context.invocation_id)
        agent = tool_context.agent_name
        if tool.name not in ALWAYS_ALLOWED and tool.name not in self.allowlist.get(agent, set()):
            c.events.append(f"{agent}: blocked tool {tool.name}")
            return {
                "status": "error",
                "error_type": "permission_denied",
                "message": f"{agent} may not call {tool.name}",
            }
        if tool.name in ALWAYS_ALLOWED:
            return None
        if c.tool_calls >= self.limits.max_tool_calls:
            c.events.append(f"{agent}: tool budget exhausted")
            return {
                "status": "error",
                "error_type": "budget_exceeded",
                "message": f"tool call budget ({self.limits.max_tool_calls}) exhausted",
            }
        c.tool_calls += 1
        c.tools_by_agent[agent].append(tool.name)
        c._tool_start[tool_context.function_call_id or tool.name] = time.monotonic()
        return None

    async def after_tool_callback(
        self, *, tool: BaseTool, tool_args: dict, tool_context: ToolContext, result: dict
    ) -> dict | None:
        c = self.counters(tool_context.invocation_id)
        if (
            tool.name == "set_model_response"
            and isinstance(result, dict)
            and set(result) == {"error"}
        ):
            c.schema_retries[tool_context.agent_name] += 1
            log.warning("schema_retry agent=%s", tool_context.agent_name)
        started = c._tool_start.pop(tool_context.function_call_id or tool.name, None)
        if started is not None:
            ms = (time.monotonic() - started) * 1000
            c.tool_time_ms[tool.name] += ms
            status = result.get("status") if isinstance(result, dict) else None
            log.info(
                "tool_call agent=%s tool=%s status=%s ms=%.1f",
                tool_context.agent_name,
                tool.name,
                status,
                ms,
            )
        return None
