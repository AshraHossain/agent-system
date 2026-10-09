"""Build the investigation StateGraph.

Phase 6 topology (linear happy path):

    intake_validate → classify_incident → retrieve_telemetry → detect_anomalies
    → analyze_topology → retrieve_history → retrieve_runbooks → generate_hypotheses
    → rank_hypotheses → recommend_actions → compile_report → END

After every node, ``route_next`` sends a run whose ``fatal_error`` is set to
``failure_report`` (→ END). Verification, retries, budgets, policy and
approval are added in Phases 7–8.
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from netpulse.graph import nodes
from netpulse.graph.deps import Deps
from netpulse.graph.wrapper import instrument
from netpulse.state import InvestigationState

PIPELINE: list[tuple[str, Callable]] = [
    ("intake_validate", nodes.intake_validate),
    ("classify_incident", nodes.classify_incident),
    ("retrieve_telemetry", nodes.retrieve_telemetry),
    ("detect_anomalies", nodes.detect_anomalies),
    ("analyze_topology", nodes.analyze_topology),
    ("retrieve_history", nodes.retrieve_history),
    ("retrieve_runbooks", nodes.retrieve_runbooks),
    ("generate_hypotheses", nodes.generate_hypotheses),
    ("rank_hypotheses", nodes.rank_hypotheses),
    ("recommend_actions", nodes.recommend_actions),
    ("compile_report", nodes.compile_report),
]
FAILURE = "failure_report"


def route_next(next_node: str) -> Callable[[dict], str]:
    def route(state: dict) -> str:
        return FAILURE if state.get("fatal_error") else next_node

    route.__name__ = f"route_to_{next_node}"
    return route


def build_graph(deps: Deps, checkpointer=None):
    graph = StateGraph(InvestigationState)
    for name, fn in PIPELINE:
        graph.add_node(name, instrument(name, fn, deps))
    graph.add_node(FAILURE, instrument(FAILURE, nodes.failure_report, deps))

    graph.add_edge(START, PIPELINE[0][0])
    for (name, _), (next_name, _) in zip(PIPELINE[:-1], PIPELINE[1:], strict=True):
        graph.add_conditional_edges(name, route_next(next_name), [next_name, FAILURE])
    graph.add_conditional_edges(PIPELINE[-1][0], route_next(END), [END, FAILURE])
    graph.add_edge(FAILURE, END)
    return graph.compile(checkpointer=checkpointer)
