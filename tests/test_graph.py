"""Tests for the multi-node app/graph.py (planner -> executor -> synthesizer).

planner_agent is mocked everywhere here so these tests run offline.
"""

import app.graph as graph_module
from app.graph import execute_step, run_executor, run_synthesizer, app_graph


def test_execute_step_routes_arithmetic_to_calculator():
    assert execute_step("Calculate 6 * 7 to get the total") == "42"


def test_execute_step_routes_to_search():
    assert execute_step("Search for the capital of France") == "Search results for: Search for the capital of France"


def test_execute_step_passes_through_non_tool_steps():
    assert execute_step("Summarize the findings") == "Summarize the findings"


def test_run_executor_populates_tool_results():
    state = {"query": "q", "steps": ["2 + 2", "Summarize"], "tool_results": [], "result": ""}
    update = run_executor(state)
    assert update["tool_results"] == ["4", "Summarize"]


def test_run_synthesizer_populates_result():
    state = {
        "query": "q",
        "steps": ["2 + 2", "Summarize"],
        "tool_results": ["4", "Summarize"],
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
        {"query": "irrelevant", "steps": [], "tool_results": [], "result": ""}
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
