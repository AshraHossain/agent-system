"""Offline unit tests for OpenRouterLlm and GeminiWithFallbackLlm."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from google.adk.models import LlmRequest, LlmResponse
from google.adk.tools import FunctionTool
from google.genai import types

from opspilot.adk.models import (
    GeminiWithFallbackLlm,
    OpenRouterLlm,
    _adk_to_openai_messages,
    _adk_to_openai_tools,
    _openai_payload_to_response,
    make_model,
)
from opspilot.adk.tools import summarize_anomalies
from opspilot.contracts.findings import ReviewResult

pytestmark = pytest.mark.unit


def test_adk_to_openai_messages_and_tools_conversion():
    ft = FunctionTool(summarize_anomalies)
    req = LlmRequest(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction="System rules",
            temperature=0.0,
            max_output_tokens=1024,
        ),
        contents=[
            types.Content(role="user", parts=[types.Part(text="Investigate")]),
            types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            id="call_1", name="summarize_anomalies", args={}
                        )
                    )
                ],
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="call_1", name="summarize_anomalies", response={"status": "ok"}
                        )
                    )
                ],
            ),
        ],
        tools_dict={"summarize_anomalies": ft},
    )
    msgs = _adk_to_openai_messages(req)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "summarize_anomalies"
    assert msgs[3]["tool_call_id"] == "call_1"

    tools = _adk_to_openai_tools(req)
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "summarize_anomalies"


def test_openai_payload_to_response_parses_tool_calls_and_text():
    tool_resp = _openai_payload_to_response(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "tc_1",
                                "function": {
                                    "name": "set_model_response",
                                    "arguments": '{"overall": "no_concerns", "concerns": []}',
                                },
                            }
                        ]
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )
    assert tool_resp.content.parts[0].function_call.name == "set_model_response"
    assert tool_resp.usage_metadata.total_token_count == 15

    text_resp = _openai_payload_to_response(
        {"choices": [{"message": {"content": '{"overall": "no_concerns", "concerns": []}'}}]}
    )
    assert text_resp.content.parts[0].text == '{"overall": "no_concerns", "concerns": []}'


async def test_openrouter_llm_posts_and_builds_json_schema_format(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"overall": "no_concerns", "concerns": []}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    llm = OpenRouterLlm(model="google/gemini-2.5-flash", timeout_s=30.0)
    req = LlmRequest(
        model="google/gemini-2.5-flash",
        config=types.GenerateContentConfig(response_schema=ReviewResult),
        contents=[types.Content(role="user", parts=[types.Part(text="Review")])],
    )
    items = [r async for r in llm.generate_content_async(req)]
    assert len(items) == 1
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-test-key"
    assert items[0].usage_metadata.total_token_count == 20


async def test_gemini_with_fallback_uses_openrouter_when_gemini_fails(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")

    async def failing_gemini(self, llm_request, stream=False):
        raise RuntimeError("402 RESOURCE_EXHAUSTED")
        yield  # pragma: no cover

    async def ok_openrouter(self, llm_request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="fallback")]))

    monkeypatch.setattr(
        "google.adk.models.google_llm.Gemini.generate_content_async", failing_gemini
    )
    monkeypatch.setattr(OpenRouterLlm, "generate_content_async", ok_openrouter)

    llm = GeminiWithFallbackLlm(model="gemini-2.5-flash", fallback_model="google/gemini-2.5-flash")
    req = LlmRequest(model="gemini-2.5-flash", contents=[])
    items = [r async for r in llm.generate_content_async(req)]
    assert len(items) == 1 and items[0].content.parts[0].text == "fallback"
    assert llm._gemini_failed is True


def test_make_model_selects_openrouter_or_fallback(settings, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")

    live = replace(settings, provider="gemini")
    assert isinstance(make_model("telemetry_analyst", live), OpenRouterLlm)

    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-fake")
    assert isinstance(make_model("telemetry_analyst", live), GeminiWithFallbackLlm)

    direct = replace(settings, provider="openrouter")
    assert isinstance(make_model("telemetry_analyst", direct), OpenRouterLlm)
