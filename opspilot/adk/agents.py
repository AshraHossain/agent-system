"""Agent definitions and the orchestration tree (ADR-0002)."""

from __future__ import annotations

from google.adk.agents import LlmAgent, LoopAgent, ParallelAgent, SequentialAgent

from opspilot.adk import prompts
from opspilot.adk import tools as t
from opspilot.adk.callbacks import model_error_fallback, sanitize_user_content, skip_if_done
from opspilot.adk.deterministic import (
    EvidenceVerifierAgent,
    FinalizerAgent,
    IntakeAgent,
    ReinvestigationGateAgent,
)
from opspilot.adk.models import generate_config, make_model
from opspilot.adk.runtime import RunFaults
from opspilot.config import Settings
from opspilot.contracts.findings import (
    IncidentAnalysis,
    KnowledgeFinding,
    ReportDraft,
    ReviewResult,
    TelemetryFinding,
    TopologyFinding,
)

AGENT_TOOLS = {
    "telemetry_analyst": t.TELEMETRY_TOOLS,
    "topology_analyst": t.TOPOLOGY_TOOLS,
    "knowledge_researcher": t.KNOWLEDGE_TOOLS,
    "incident_analyst": t.ANALYSIS_TOOLS,
    "report_drafter": t.DRAFT_TOOLS,
    "review_verifier": [],
}
TOOL_ALLOWLIST = {agent: {f.__name__ for f in fns} for agent, fns in AGENT_TOOLS.items()}
PIPELINE_ORDER = [
    "intake",
    "specialists",
    "incident_analyst",
    "report_drafter",
    "evidence_verifier",
    "review_verifier",
    "finalizer",
]
# With max_investigation_rounds=2, these stages run inside `investigation_rounds`.
LOOP_ORDER = [
    "specialists",
    "incident_analyst",
    "report_drafter",
    "evidence_verifier",
    "reinvestigation_gate",
]


def _llm_agent(
    name: str,
    description: str,
    instruction,
    schema,
    output_key: str,
    settings: Settings,
    faults: RunFaults | None,
) -> LlmAgent:
    return LlmAgent(
        name=name,
        description=description,
        model=make_model(name, settings, faults),
        instruction=instruction,
        tools=list(AGENT_TOOLS[name]),
        output_schema=schema,
        output_key=output_key,
        include_contents="none",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=generate_config(settings),
        before_agent_callback=skip_if_done(output_key),
        before_model_callback=sanitize_user_content,
        on_model_error_callback=model_error_fallback(name),
    )


def telemetry_analyst(settings: Settings, faults: RunFaults | None = None) -> LlmAgent:
    return _llm_agent(
        "telemetry_analyst",
        "Summarises latency, loss, utilization and error anomalies via telemetry tools.",
        prompts.TELEMETRY,
        TelemetryFinding,
        "telemetry_finding",
        settings,
        faults,
    )


def topology_analyst(settings: Settings, faults: RunFaults | None = None) -> LlmAgent:
    return _llm_agent(
        "topology_analyst",
        "Analyses dependencies, propagation paths and rule-based blast radius.",
        prompts.TOPOLOGY,
        TopologyFinding,
        "topology_finding",
        settings,
        faults,
    )


def knowledge_researcher(settings: Settings, faults: RunFaults | None = None) -> LlmAgent:
    return _llm_agent(
        "knowledge_researcher",
        "Retrieves runbooks, incidents and docs as untrusted, cited evidence.",
        prompts.KNOWLEDGE,
        KnowledgeFinding,
        "knowledge_finding",
        settings,
        faults,
    )


def incident_analyst(settings: Settings, faults: RunFaults | None = None) -> LlmAgent:
    return _llm_agent(
        "incident_analyst",
        "Reconciles findings into ranked, evidence-cited root-cause hypotheses.",
        prompts.INCIDENT,
        IncidentAnalysis,
        "incident_analysis",
        settings,
        faults,
    )


def report_drafter(settings: Settings, faults: RunFaults | None = None) -> LlmAgent:
    return _llm_agent(
        "report_drafter",
        "Coordinator synthesis: drafts the summary and read-only investigation plan.",
        prompts.DRAFTER,
        ReportDraft,
        "report_draft",
        settings,
        faults,
    )


def review_verifier(settings: Settings, faults: RunFaults | None = None) -> LlmAgent:
    return _llm_agent(
        "review_verifier",
        "Secondary semantic review; can only add concerns.",
        prompts.REVIEWER,
        ReviewResult,
        "review",
        settings,
        faults,
    )


def build_root_agent(
    settings: Settings | None = None, faults: RunFaults | None = None
) -> SequentialAgent:
    settings = settings or Settings.from_env()
    specialists = ParallelAgent(
        name="specialists",
        description="Independent specialists that only need the intake scope.",
        sub_agents=[
            telemetry_analyst(settings, faults),
            topology_analyst(settings, faults),
            knowledge_researcher(settings, faults),
        ],
    )
    investigation = [
        specialists,
        incident_analyst(settings, faults),
        report_drafter(settings, faults),
        EvidenceVerifierAgent(
            name="evidence_verifier",
            description="Deterministic evidence and policy verification.",
        ),
    ]
    rounds = settings.limits.max_investigation_rounds
    if rounds > 1:
        gate = ReinvestigationGateAgent(
            name="reinvestigation_gate",
            description="Retries failed stages in another round, or ends the loop.",
            max_rounds=rounds,
        )
        investigation = [
            LoopAgent(
                name="investigation_rounds",
                description=f"Up to {rounds} rounds; later rounds re-run only failed stages.",
                sub_agents=[*investigation, gate],
                max_iterations=rounds,
            )
        ]
    return SequentialAgent(
        name="opspilot_investigation",
        description="Read-only network degradation investigation pipeline.",
        sub_agents=[
            IntakeAgent(name="intake", description="Validates the request and fixes the scope."),
            *investigation,
            review_verifier(settings, faults),
            FinalizerAgent(
                name="finalizer", description="Builds the validated report; rule-based status."
            ),
        ],
    )
