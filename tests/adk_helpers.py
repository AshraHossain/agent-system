"""Helpers for running small agent trees through ADK's Runner in tests."""

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from opspilot.adk.agents import TOOL_ALLOWLIST
from opspilot.adk.plugins import BudgetPlugin
from opspilot.adk.runtime import RunFaults, register


async def run_tree(
    agent,
    settings,
    dataset_id,
    text="Several services are slow with packet loss",
    state=None,
    faults=None,
    inv="inv-test",
):
    register(inv, settings, dataset_id, faults or RunFaults())
    svc = InMemorySessionService()
    plugin = BudgetPlugin(settings.limits, TOOL_ALLOWLIST)
    runner = Runner(app_name="t", agent=agent, session_service=svc, plugins=[plugin])
    s = await svc.create_session(
        app_name="t",
        user_id="u",
        state={"investigation_id": inv, "dataset_id": dataset_id, **(state or {})},
    )
    events = []
    async for ev in runner.run_async(
        user_id="u",
        session_id=s.id,
        new_message=types.Content(role="user", parts=[types.Part(text=text)]),
    ):
        events.append(ev)
    s = await svc.get_session(app_name="t", user_id="u", session_id=s.id)
    return s.state, events, plugin
