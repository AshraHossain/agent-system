"""Model configuration: live Gemini or the deterministic scripted mock.

Live: `LlmAgent.model` is a Gemini model string; google-genai reads credentials
from the environment (GOOGLE_API_KEY, or Vertex AI via GOOGLE_GENAI_USE_VERTEXAI=1,
GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION and ADC). Transient HTTP errors are
retried a bounded number of times through `HttpRetryOptions`.

Mock: `ScriptedLlm` (a `BaseLlm`) runs a per-agent deterministic policy over the
same inputs a real model receives (system instruction + tool results). It emits
no token usage, so metrics honestly report tokens as unavailable.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from opspilot.adk.runtime import RunFaults
from opspilot.config import Settings

CONTEXT_RE = re.compile(r'<context name="(\w+)">\s*(.*?)\s*</context>', re.S)


class ModelQuotaError(RuntimeError):
    """Simulated 429 RESOURCE_EXHAUSTED."""


@dataclass
class Turn:
    agent: str
    context: dict[str, Any]
    results: list[tuple[str, dict]]
    tools: set[str]

    def last(self, name: str) -> dict | None:
        for n, r in reversed(self.results):
            if n == name:
                return r
        return None

    def all(self, name: str) -> list[dict]:
        return [r for n, r in self.results if n == name]

    def called(self, name: str) -> bool:
        return any(n == name for n, _ in self.results)


@dataclass
class ToolCalls:
    calls: list[tuple[str, dict]]


@dataclass
class Final:
    output: dict = field(default_factory=dict)


def parse_turn(agent: str, req: LlmRequest) -> Turn:
    sys_text = req.config.system_instruction if req.config else ""
    if not isinstance(sys_text, str):
        sys_text = str(sys_text or "")
    context = {}
    for name, body in CONTEXT_RE.findall(sys_text):
        try:
            context[name] = json.loads(body)
        except json.JSONDecodeError:
            context[name] = body
    results = []
    for content in req.contents or []:
        for part in content.parts or []:
            if part.function_response is not None:
                results.append(
                    (part.function_response.name, dict(part.function_response.response or {}))
                )
    return Turn(agent=agent, context=context, results=results, tools=set(req.tools_dict))


class ScriptedLlm(BaseLlm):
    """Deterministic stand-in for Gemini used by tests, offline eval and the demo."""

    model: str = "scripted-mock"
    agent: str
    faults: frozenset[str] = frozenset()
    transient: bool = False
    _calls: int = PrivateAttr(default=0)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        from opspilot.adk.mock_policies import POLICIES

        self._calls += 1
        faults = frozenset() if self.transient and self._calls > 1 else self.faults
        if "timeout" in faults:
            await asyncio.sleep(0)
            raise TimeoutError(f"simulated model timeout for {self.agent}")
        if "quota" in faults:
            raise ModelQuotaError("429 RESOURCE_EXHAUSTED (simulated)")
        action = POLICIES[self.agent](parse_turn(self.agent, llm_request))
        yield to_response(action, "set_model_response" in llm_request.tools_dict)


def to_response(action: ToolCalls | Final, has_tools: bool) -> LlmResponse:
    if isinstance(action, ToolCalls):
        parts = [
            types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in action.calls
        ]
    elif has_tools:
        parts = [
            types.Part(
                function_call=types.FunctionCall(name="set_model_response", args=action.output)
            )
        ]
    else:
        parts = [types.Part(text=json.dumps(action.output))]
    return LlmResponse(content=types.Content(role="model", parts=parts))


def make_model(agent: str, settings: Settings, faults: RunFaults | None = None) -> str | BaseLlm:
    if settings.provider == "gemini":
        return settings.model
    faults = faults or RunFaults()
    f = set()
    if agent in faults.model_timeout:
        f.add("timeout")
    if agent in faults.model_quota:
        f.add("quota")
    return ScriptedLlm(agent=agent, faults=frozenset(f), transient=faults.transient)


def generate_config(
    settings: Settings, max_output_tokens: int = 4096
) -> types.GenerateContentConfig:
    """Bounded retries + timeout for live calls. Harmless for the mock."""
    return types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        http_options=types.HttpOptions(
            timeout=int(settings.limits.model_timeout_s * 1000),
            retry_options=types.HttpRetryOptions(
                attempts=settings.limits.max_model_attempts,
                initial_delay=1.0,
                max_delay=8.0,
                http_status_codes=[429, 500, 503, 504],
            ),
        ),
    )
