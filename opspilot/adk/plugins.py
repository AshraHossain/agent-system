"""Runner plugin enforcing execution budgets, tool permissions and collecting metrics.

Limits (see config.Limits): total model calls, model calls per agent (iteration
cap), total tool calls, wall-clock duration. Exceeding a model budget makes the
agent finish with a schema-valid 'failed' output; exceeding the tool budget
returns an error result to the model. `RunConfig.max_llm_calls` and the
runner's asyncio timeout are hard backstops.
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
    events: list[str] = field(default_factory=list)
    model_time_ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    tool_time_ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _model_start: dict[str, float] = field(default_factory=dict)
    _tool_start: dict[str, float] = field(default_factory=dict)

    def elapsed(self) -> float:
        return time.monotonic() - self.started


class BudgetPlugin(BasePlugin):
    def __init__(self, limits: Limits, tool_allowlist: dict[str, set[str]]):
        super().__init__(name="opspilot_budget")
        self.limits = limits
        self.allowlist = tool_allowlist
        self.runs: dict[str, RunCounters] = {}

    def counters(self, invocation_id: str) -> RunCounters:
        return self.runs.setdefault(invocation_id, RunCounters())

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        c = self.counters(callback_context.invocation_id)
        agent = callback_context.agent_name
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
            has_tools = "set_model_response" in (llm_request.tools_dict or {})
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
