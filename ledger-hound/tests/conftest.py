from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import httpx
import pytest

from ledger_hound.p2p_api.app import create_app
from ledger_hound.qa.brain import LiveBrain, LLMSettings
from ledger_hound.qa.discovery import discover
from ledger_hound.qa.http import ApiClient


@pytest.fixture
async def clean():
    """In-process client + catalog for a practice API with no bugs."""
    client = ApiClient.for_app(create_app(frozenset()))
    catalog = await discover(client)
    yield client, catalog
    await client.aclose()


def text(content: str) -> dict[str, Any]:
    return {"content": content}


def tool(name: str, **arguments: Any) -> dict[str, Any]:
    return {"tool": name, "arguments": arguments}


def fake_llm(script: Iterable[dict[str, Any]]) -> tuple[httpx.AsyncClient, list[dict[str, Any]]]:
    """An OpenAI-compatible /chat/completions endpoint that replays a script.

    Each script entry is either ``text(...)`` (a final message) or ``tool(...)``
    (one tool call). Returns the HTTP client to inject and the list of request
    payloads the SDK sent, so tests can inspect prompts and tool results.
    """
    queue = list(script)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        step = queue.pop(0) if queue else text("{}")
        message: dict[str, Any] = {"role": "assistant", "content": step.get("content")}
        finish = "stop"
        if "tool" in step:
            message["tool_calls"] = [
                {
                    "id": f"call_{len(seen)}",
                    "type": "function",
                    "function": {"name": step["tool"], "arguments": json.dumps(step["arguments"])},
                }
            ]
            finish = "tool_calls"
        return httpx.Response(
            200,
            json={
                "id": f"cmpl-{len(seen)}",
                "object": "chat.completion",
                "created": 0,
                "model": "fake",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def live_brain(script: Iterable[dict[str, Any]]) -> tuple[LiveBrain, list[dict[str, Any]]]:
    http, seen = fake_llm(script)
    settings = LLMSettings(api_key="test", base_url="http://fake-llm/v1", model="fake/model")
    return LiveBrain(settings, http_client=http), seen
