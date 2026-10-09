"""Tests for the multi-node app/graph.py (planner -> executor -> synthesizer).

route_step_to_tool is replaced by a keyword-based fake (tests/conftest.py) and
planner_agent is mocked where the full graph runs, so these tests stay offline.
"""

import app.graph as graph_module
from app.graph import execute_step, run_executor, run_synthesizer, app_graph


def test_execute_step_routes_arithmetic_to_calculator():
    assert execute_step("6 * 7") == "42"


def test_execute_step_routes_to_search():
    assert execute_step("Find information about Python") == "Search results for: Find information about Python"


def test_execute_step_passes_through_non_tool_steps():
    assert execute_step("Summarize the findings") == "Summarize the findings"


def test_run_executor_populates_tool_results():
    state = {"query": "q", "steps": ["2 + 2", "Summarize"], "tool_results": [], "errors": [], "attempts": 0, "result": ""}
    update = run_executor(state)
    assert update["tool_results"] == ["4", "Summarize"]


def test_run_executor_detects_errors():
    state = {"query": "q", "steps": ["5 / 0", "Valid step"], "tool_results": [], "errors": [], "attempts": 0, "result": ""}
    update = run_executor(state)
    assert len(update["errors"]) == 1
    assert update["errors"][0] == "5 / 0"


def test_run_synthesizer_populates_result():
    state = {
        "query": "q",
        "steps": ["2 + 2", "Summarize"],
        "tool_results": ["4", "Summarize"],
        "errors": [],
        "attempts": 0,
        "result": "",
    }
    update = run_synthesizer(state)
    assert "Step: 2 + 2" in update["result"]
    assert "Result: 4" in update["result"]


def test_full_graph_invoke_with_mocked_planner(monkeypatch):
    monkeypatch.setattr(
        graph_module,
        "planner_agent",
        lambda query: "1. Compute 3 + 4\n2. Search for LangGraph docs\n3. Report the answer",
    )

    final_state = app_graph.invoke(
        {"query": "irrelevant", "steps": [], "tool_results": [], "errors": [], "attempts": 0, "result": ""}
    )

    assert final_state["steps"] == [
        "Compute 3 + 4",
        "Search for LangGraph docs",
        "Report the answer",
    ]
    assert final_state["tool_results"][0] == "7"
    assert final_state["tool_results"][1].startswith("Search results for:")
    assert final_state["tool_results"][2] == "Report the answer"
    assert final_state["result"]
