"""Pins the ADK 2.11.0 behaviours this design relies on (ADR-0001).

If an ADK upgrade changes any of these, this file fails first.
"""

import json

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.events import Event, EventActions
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import BaseModel


class Out(BaseModel):
    value: float


def probe(entity_id: str, tool_context: ToolContext) -> dict:
    """Probe tool."""
    tool_context.state["evidence:EV-TEL-00000000"] = {"entity": entity_id}
    return {"value": 4.2}


class Recorder(BaseLlm):
    model: str = "rec"
    seen: list = []  # noqa: RUF012 - pydantic field, copied per instance
    fail: bool = False

    async def generate_content_async(self, req, stream=False):
        if self.fail:
            raise TimeoutError("boom")
        self.seen.append(req)
        frs = [p.function_response for c in req.contents for p in c.parts if p.function_response]
        if not frs:
            part = types.Part(
                function_call=types.FunctionCall(name="probe", args={"entity_id": "x"})
            )
        else:
            part = types.Part(
                function_call=types.FunctionCall(
                    name="set_model_response", args={"value": frs[-1].response["value"]}
                )
            )
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class Writer(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={"scope": {"ok": True}}),
        )


async def _run(agent):
    svc = InMemorySessionService()
    r = Runner(app_name="c", agent=agent, session_service=svc)
    s = await svc.create_session(app_name="c", user_id="u")
    async for _ in r.run_async(
        user_id="u",
        session_id=s.id,
        new_message=types.Content(role="user", parts=[types.Part(text="hi")]),
    ):
        pass
    return (await svc.get_session(app_name="c", user_id="u", session_id=s.id)).state


async def test_tools_with_output_schema_use_set_model_response_and_output_key():
    m = Recorder(seen=[])
    agent = LlmAgent(
        name="a",
        model=m,
        instruction="i",
        tools=[probe],
        output_schema=Out,
        output_key="out",
        include_contents="none",
    )
    state = await _run(SequentialAgent(name="root", sub_agents=[Writer(name="w"), agent]))
    assert state["scope"] == {"ok": True}  # BaseAgent state_delta persisted
    assert state["out"] == {"value": 4.2}  # validated output stored under output_key
    assert "evidence:EV-TEL-00000000" in state  # ToolContext.state writes persisted
    assert "set_model_response" in m.seen[0].tools_dict
    # include_contents='none' still carries the agent's own tool loop
    assert any(p.function_response for c in m.seen[1].contents for p in c.parts)


async def test_on_model_error_callback_can_supply_schema_valid_output():
    def fallback(callback_context, llm_request, error):
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=json.dumps({"value": -1}))])
        )

    agent = LlmAgent(
        name="a",
        model=Recorder(fail=True),
        instruction="i",
        output_schema=Out,
        output_key="out",
        on_model_error_callback=fallback,
    )
    state = await _run(agent)
    assert state["out"] == {"value": -1}
