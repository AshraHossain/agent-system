"""Build the investigation StateGraph (Phase 8: policy review and durable human approval).

    intake_validate → classify_incident → retrieve_telemetry → detect_anomalies → analyze_topology
    → retrieve_history → retrieve_runbooks → generate_hypotheses → verify_evidence
        verify_evidence ─(blocking issues, retries left)→ generate_hypotheses
        verify_evidence ─(passed | retries exhausted)→ rank_hypotheses
    rank_hypotheses ─(insufficient, rounds left)→ retrieve_telemetry   (wider window)
    rank_hypotheses → recommend_actions → policy_review
        policy_review ─(remediation needs approval)→ human_approval  [interrupt; resumed by a reviewer]
            human_approval ─(approve | reject)→ compile_report
            human_approval ─(more investigation, cycles left)→ retrieve_telemetry
            human_approval ─(invalid / unauthorized input)→ human_approval  [asks again]
        policy_review ─(uncertain with signal | critical | multi-fault, nothing to approve)→ escalate → compile_report
        policy_review ─(diagnostics only)→ compile_report → END
    any node ─(fatal_error)→ failure_report → END;  any node ─(deadline passed)→ escalate

Routers are pure functions in ``routing.py``; they receive ``deps.clock()`` for deadline checks.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from netpulse.graph import nodes, routing
from netpulse.graph.deps import Deps
from netpulse.graph.wrapper import instrument
from netpulse.state import InvestigationState

FAILURE, ESCALATE = routing.FAILURE, routing.ESCALATE

# name, function, instrumentation options
NODES = [
    ("intake_validate", nodes.intake_validate, {"critical": True}),
    ("classify_incident", nodes.classify_incident, {}),
    ("retrieve_telemetry", nodes.retrieve_telemetry, {"timeout": "tool", "critical": True}),
    ("detect_anomalies", nodes.detect_anomalies, {"timeout": "tool", "critical": True}),
    ("analyze_topology", nodes.analyze_topology, {"timeout": "tool"}),
    ("retrieve_history", nodes.retrieve_history, {"timeout": "tool"}),
    ("retrieve_runbooks", nodes.retrieve_runbooks, {"timeout": "tool"}),
    ("generate_hypotheses", nodes.generate_hypotheses, {"timeout": "llm", "on_timeout": nodes.on_generation_timeout}),
    ("verify_evidence", nodes.verify_evidence, {"critical": True}),
    ("rank_hypotheses", nodes.rank_hypotheses, {"critical": True}),
    ("recommend_actions", nodes.recommend_actions, {}),
    ("policy_review", nodes.policy_review, {"critical": True}),
    # No timeout wrapper: interrupt() must run in the graph's own thread and context.
    ("human_approval", nodes.human_approval, {"critical": True}),
    (ESCALATE, nodes.escalate, {"critical": True}),
    ("compile_report", nodes.compile_report, {"critical": True}),
    (FAILURE, nodes.failure_report, {}),
]
LINEAR = [
    ("intake_validate", "classify_incident"),
    ("classify_incident", "retrieve_telemetry"),
    ("retrieve_telemetry", "detect_anomalies"),
    ("detect_anomalies", "analyze_topology"),
    ("analyze_topology", "retrieve_history"),
    ("retrieve_history", "retrieve_runbooks"),
    ("retrieve_runbooks", "generate_hypotheses"),
    ("generate_hypotheses", "verify_evidence"),
]


def build_graph(deps: Deps, checkpointer=None):
    graph = StateGraph(InvestigationState)
    for name, fn, opts in NODES:
        graph.add_node(name, instrument(name, fn, deps, **opts))

    def bind(router):
        def route(state: dict) -> str:
            return router(state, deps.clock())

        route.__name__ = router.__name__
        return route

    graph.add_edge(START, "intake_validate")
    for src, dst in LINEAR:
        graph.add_conditional_edges(src, bind(routing.after_linear(dst)), [dst, FAILURE, ESCALATE])
    graph.add_conditional_edges(
        "verify_evidence", bind(routing.after_verify), ["rank_hypotheses", "generate_hypotheses", FAILURE, ESCALATE]
    )
    graph.add_conditional_edges(
        "rank_hypotheses", bind(routing.after_rank), ["recommend_actions", "retrieve_telemetry", FAILURE, ESCALATE]
    )
    graph.add_conditional_edges(
        "recommend_actions", bind(routing.after_recommend), ["policy_review", FAILURE, ESCALATE]
    )
    graph.add_conditional_edges(
        "policy_review", bind(routing.after_policy), ["human_approval", ESCALATE, "compile_report", FAILURE]
    )
    graph.add_conditional_edges(
        "human_approval",
        bind(routing.after_human),
        ["compile_report", "retrieve_telemetry", "human_approval", ESCALATE, FAILURE],
    )
    graph.add_conditional_edges(ESCALATE, bind(routing.after_escalate), ["compile_report", FAILURE])
    graph.add_conditional_edges("compile_report", lambda s: FAILURE if s.get("fatal_error") else END, [END, FAILURE])
    graph.add_edge(FAILURE, END)
    # interrupt() needs a checkpointer; the in-memory default is NOT durable (use PersistentStore for that).
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())
