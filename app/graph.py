import re

from langgraph.graph import StateGraph
from app.state import AgentState
from app.agents import planner_agent, parse_steps
from tools.calculator_tool import calculator_tool
from tools.search_tool import search_tool

_ARITHMETIC = re.compile(r"-?\d+(?:\.\d+)?\s*[-+*/%]\s*-?\d+(?:\.\d+)?")
_SEARCH_HINTS = re.compile(r"\bsearch\b|\blook ?up\b|\bfind information\b", re.IGNORECASE)


def run_planner(state: AgentState):
    raw = planner_agent(state["query"])
    return {"steps": parse_steps(raw)}


def execute_step(step: str) -> str:
    """Route a single plan step to a tool, or pass it through unchanged."""
    match = _ARITHMETIC.search(step)
    if match:
        return calculator_tool(match.group(0))
    if _SEARCH_HINTS.search(step):
        return search_tool(step)
    return step


def run_executor(state: AgentState):
    tool_results = [execute_step(step) for step in state["steps"]]
    return {"tool_results": tool_results}


def run_synthesizer(state: AgentState):
    lines = [
        f"Step: {step}\nResult: {result}"
        for step, result in zip(state["steps"], state["tool_results"])
    ]
    return {"result": "\n\n".join(lines)}


graph = StateGraph(AgentState)

graph.add_node("planner", run_planner)
graph.add_node("executor", run_executor)
graph.add_node("synthesizer", run_synthesizer)

graph.set_entry_point("planner")
graph.add_edge("planner", "executor")
graph.add_edge("executor", "synthesizer")
graph.set_finish_point("synthesizer")

app_graph = graph.compile()
