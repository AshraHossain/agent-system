from langgraph.graph import StateGraph
from app.state import AgentState
from app.agents import planner_agent

def run_planner(state: AgentState):
    steps = planner_agent(state["query"])
    return {"steps": [steps]}


graph = StateGraph(AgentState)

graph.add_node("planner", run_planner)

graph.set_entry_point("planner")

graph.set_finish_point("planner")

app_graph = graph.compile()
