"""PydanticAI agents. `model` is 'mock' (offline default) or any pydantic-ai model string,
e.g. TRAFFICFLOW_MODEL=google-gla:<model> / openai:<model> / anthropic:<model>."""

from __future__ import annotations

import os

from pydantic_ai import Agent
from pydantic_ai.models import Model

from trafficflow.agents import prompts, tools
from trafficflow.agents.deps import TrafficDeps
from trafficflow.agents.mock_models import mock_model
from trafficflow.contracts import ForecastFinding, IncidentFinding, TrafficBrief


def resolve_model(role: str, model: str | None = None) -> Model | str:
    name = model or os.environ.get("TRAFFICFLOW_MODEL", "mock")
    return mock_model(role) if name == "mock" else name


def forecast_analyst(model: str | None = None) -> Agent[TrafficDeps, ForecastFinding]:
    return Agent(
        resolve_model("forecast", model),
        deps_type=TrafficDeps,
        output_type=ForecastFinding,
        instructions=prompts.FORECAST_ANALYST,
        name="forecast_analyst",
        tools=[
            tools.list_segments,
            tools.forecast_segment,
            tools.get_current_conditions,
            tools.compare_models,
        ],
        retries=2,
    )


def incident_analyst(model: str | None = None) -> Agent[TrafficDeps, IncidentFinding]:
    return Agent(
        resolve_model("incident", model),
        deps_type=TrafficDeps,
        output_type=IncidentFinding,
        instructions=prompts.INCIDENT_ANALYST,
        name="incident_analyst",
        tools=[tools.list_segments, tools.detect_incidents],
        retries=2,
    )


def brief_writer(model: str | None = None) -> Agent[None, TrafficBrief]:
    return Agent(
        resolve_model("writer", model),
        output_type=TrafficBrief,
        instructions=prompts.BRIEF_WRITER,
        name="brief_writer",
        retries=2,
    )


def ask_agent(model: str | None = None) -> Agent[TrafficDeps, str]:
    return Agent(
        resolve_model("ask", model),
        deps_type=TrafficDeps,
        instructions=prompts.ASK,
        name="ask",
        tools=[
            tools.list_segments,
            tools.forecast_segment,
            tools.get_current_conditions,
            tools.detect_incidents,
            tools.compare_models,
        ],
        retries=2,
    )
