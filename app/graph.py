from langgraph.graph import StateGraph, END
from app.state import AgentState
from app.agents import planner_agent, parse_steps, route_step_to_tool
from tools.calculator_tool import calculator_tool
from tools.search_tool import search_tool

_ERROR_PREFIX = "Error in calculation"
_MAX_ATTEMPTS = 3


def run_planner(state: AgentState):
    raw = planner_agent(state["query"])
    new_attempts = state["attempts"] + 1
    return {"steps": parse_steps(raw), "attempts": new_attempts, "errors": []}


def execute_step(step: str) -> str:
    """Route a step to the appropriate tool using LLM-based decision."""
    tool = route_step_to_tool(step)

    if tool == "calculator":
        return calculator_tool(step)
    elif tool == "search":
        return search_tool(step)
    else:
        return step


def run_executor(state: AgentState):
    tool_results = [execute_step(step) for step in state["steps"]]
    errors = [step for step, result in zip(state["steps"], tool_results) if result.startswith(_ERROR_PREFIX)]
    return {"tool_results": tool_results, "errors": errors}


def run_synthesizer(state: AgentState):
    lines = [
        f"Step: {step}\nResult: {result}"
        for step, result in zip(state["steps"], state["tool_results"])
    ]
    return {"result": "\n\n".join(lines)}


def should_replan(state: AgentState):
    """Decide whether to re-plan or finish after execution."""
    if not state["errors"] or state["attempts"] >= _MAX_ATTEMPTS:
        return "synthesizer"
    return "planner"


graph = StateGraph(AgentState)

graph.add_node("planner", run_planner)
graph.add_node("executor", run_executor)
graph.add_node("synthesizer", run_synthesizer)

graph.set_entry_point("planner")
graph.add_edge("planner", "executor")
graph.add_conditional_edges("executor", should_replan, {"planner": "planner", "synthesizer": "synthesizer"})
graph.set_finish_point("synthesizer")

app_graph = graph.compile()
