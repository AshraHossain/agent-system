"""Model configuration: live Gemini, OpenRouter fallback, or deterministic scripted mock.

Live: `LlmAgent.model` is a Gemini model string (or `GeminiWithFallbackLlm` when
`OPENROUTER_API_KEY` is present so quota/billing/permission errors fall back to
OpenRouter seamlessly). `OPSPILOT_MODEL_PROVIDER=openrouter` uses `OpenRouterLlm`
directly.

Mock: `ScriptedLlm` (a `BaseLlm`) runs a per-agent deterministic policy over the
same inputs a real model receives (system instruction + tool results). It emits
no token usage, so metrics honestly report tokens as unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types
from pydantic import BaseModel, PrivateAttr

from opspilot.adk.runtime import RunFaults
from opspilot.config import Settings

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CONTEXT_RE = re.compile(r'<context name="(\w+)">\s*(.*?)\s*</context>', re.S)
# Injected schema faults (RunFaults.invalid_output / invalid_args).
PROSE_ANSWER = "The evidence points to a congested link; I am confident about this."
INVALID_ARGS = {"status": "done", "summary": 42}


class ModelQuotaError(RuntimeError):
    """Simulated 429 RESOURCE_EXHAUSTED."""


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class ToolCalls:
    calls: list[tuple[str, dict]]


@dataclass(frozen=True)
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
    _finals: int = PrivateAttr(default=0)

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
        has_tools = "set_model_response" in llm_request.tools_dict
        if isinstance(action, Final):
            self._finals += 1
            broken = self.faults if not (self.transient and self._finals > 1) else frozenset()
            if "invalid_output" in broken:
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text=PROSE_ANSWER)])
                )
                return
            if "invalid_args" in broken and has_tools:
                yield to_response(Final(dict(INVALID_ARGS)), has_tools)
                return
        yield to_response(action, has_tools)


def _schema_dict(raw_schema: Any) -> dict[str, Any] | None:
    if raw_schema is None:
        return None
    if isinstance(raw_schema, type) and issubclass(raw_schema, BaseModel):
        return raw_schema.model_json_schema()
    if isinstance(raw_schema, BaseModel):
        return raw_schema.model_dump(exclude_none=True, mode="json")
    if isinstance(raw_schema, dict):
        return raw_schema
    return None


def _adk_to_openai_messages(llm_request: LlmRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    sys_text = llm_request.config.system_instruction if llm_request.config else None
    if sys_text:
        if not isinstance(sys_text, str):
            sys_text = str(sys_text)
        messages.append({"role": "system", "content": sys_text})
    for content in llm_request.contents or []:
        parts = content.parts or []
        role = "assistant" if content.role == "model" else "user"
        text_parts = [p.text for p in parts if p.text and not p.thought]
        fn_calls = [p.function_call for p in parts if p.function_call is not None]
        fn_resps = [p.function_response for p in parts if p.function_response is not None]
        if fn_resps:
            for fr in fn_resps:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": fr.id or fr.name,
                        "content": json.dumps(fr.response or {}, default=str),
                    }
                )
        elif fn_calls:
            msg: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": fc.id or f"call_{idx}_{fc.name}",
                        "type": "function",
                        "function": {
                            "name": fc.name,
                            "arguments": json.dumps(fc.args or {}),
                        },
                    }
                    for idx, fc in enumerate(fn_calls)
                ],
            }
            if text_parts:
                msg["content"] = "\n".join(text_parts)
            messages.append(msg)
        elif text_parts:
            messages.append({"role": role, "content": "\n".join(text_parts)})
    if not messages or all(m["role"] == "system" for m in messages):
        messages.append({"role": "user", "content": "Proceed."})
    return messages


def _adk_to_openai_tools(llm_request: LlmRequest) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in (llm_request.tools_dict or {}).values():
        decl = tool._get_declaration() if hasattr(tool, "_get_declaration") else None
        if decl is None:
            continue
        d = decl.model_dump(exclude_none=True, mode="json")
        params = (
            d.get("parameters_json_schema")
            or d.get("parameters")
            or {"type": "object", "properties": {}}
        )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": d["name"],
                    "description": d.get("description", ""),
                    "parameters": params,
                },
            }
        )
    return tools


def _openai_payload_to_response(data: dict[str, Any]) -> LlmResponse:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    parts: list[types.Part] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            args = {}
        parts.append(
            types.Part(
                function_call=types.FunctionCall(
                    id=tc.get("id"),
                    name=fn.get("name", ""),
                    args=args,
                )
            )
        )
    if not parts and message.get("content"):
        parts.append(types.Part(text=str(message["content"])))
    usage = data.get("usage") or {}
    usage_meta = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=usage.get("prompt_tokens"),
        candidates_token_count=usage.get("completion_tokens"),
        total_token_count=usage.get("total_tokens"),
    )
    return LlmResponse(
        content=types.Content(role="model", parts=parts or [types.Part(text="")]),
        usage_metadata=usage_meta,
    )


class OpenRouterLlm(BaseLlm):
    """OpenAI-compatible `BaseLlm` targeting OpenRouter (used directly or as Gemini fallback)."""

    model: str = "google/gemini-2.5-flash"
    timeout_s: float = 60.0
    endpoint: str = OPENROUTER_URL

    def _build_payload(self, llm_request: LlmRequest) -> dict[str, Any]:
        cfg = llm_request.config
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _adk_to_openai_messages(llm_request),
            "temperature": cfg.temperature if cfg and cfg.temperature is not None else 0.0,
            "max_tokens": cfg.max_output_tokens if cfg and cfg.max_output_tokens else 4096,
        }
        tools = _adk_to_openai_tools(llm_request)
        if tools:
            payload["tools"] = tools
        elif cfg and cfg.response_schema is not None:
            schema = _schema_dict(cfg.response_schema)
            if schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "strict": False, "schema": schema},
                }
        return payload

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        payload = self._build_payload(llm_request)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "OpsPilot AI",
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(self.endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        yield _openai_payload_to_response(data)


class GeminiWithFallbackLlm(BaseLlm):
    """Tries Google Gemini first and falls back to `OpenRouterLlm` on auth/quota/billing errors."""

    model: str = "gemini-2.5-flash"
    fallback_model: str = "google/gemini-2.5-flash"
    timeout_s: float = 60.0
    _gemini_failed: bool = PrivateAttr(default=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if not self._gemini_failed:
            from google.adk.models.google_llm import Gemini

            primary = Gemini(model=self.model)
            try:
                async for item in primary.generate_content_async(llm_request, stream=stream):
                    yield item
                return
            except Exception as exc:
                self._gemini_failed = True
                log.warning(
                    "Gemini request failed (%s); falling back to OpenRouter (%s)",
                    type(exc).__name__,
                    self.fallback_model,
                )
        fallback = OpenRouterLlm(model=self.fallback_model, timeout_s=self.timeout_s)
        async for item in fallback.generate_content_async(llm_request, stream=stream):
            yield item


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


def _has_gemini_creds() -> bool:
    return bool(
        os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_GENAI_USE_VERTEXAI")
    )


def make_model(agent: str, settings: Settings, faults: RunFaults | None = None) -> str | BaseLlm:
    if settings.provider == "openrouter":
        return OpenRouterLlm(
            model=settings.openrouter_model, timeout_s=settings.limits.model_timeout_s
        )
    if settings.provider == "gemini":
        if os.getenv("OPENROUTER_API_KEY"):
            if not _has_gemini_creds():
                return OpenRouterLlm(
                    model=settings.openrouter_model, timeout_s=settings.limits.model_timeout_s
                )
            return GeminiWithFallbackLlm(
                model=settings.model,
                fallback_model=settings.openrouter_model,
                timeout_s=settings.limits.model_timeout_s,
            )
        return settings.model
    faults = faults or RunFaults()
    f = set()
    if agent in faults.model_timeout:
        f.add("timeout")
    if agent in faults.model_quota:
        f.add("quota")
    if agent in faults.invalid_output:
        f.add("invalid_output")
    if agent in faults.invalid_args:
        f.add("invalid_args")
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
