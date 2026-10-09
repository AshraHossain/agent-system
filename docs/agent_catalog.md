# Agent catalog

All agents are defined in `opspilot/adk/agents.py` and `opspilot/adk/deterministic.py`.
LLM agents use `include_contents="none"`, cannot transfer control
(`disallow_transfer_to_parent/peers=True`, no `sub_agents`), receive only the
state they need via instruction providers (`opspilot/adk/prompts.py`), and
write one validated `output_schema` result to one `output_key`.

| Agent | ADK type | Logical role | Reads (state) | Tools (allowlist) | Writes | Output contract |
|---|---|---|---|---|---|---|
| `intake` | `BaseAgent` (deterministic) | Operations Coordinator — validation & scope | user message, `dataset_id` | — (calls core directly) | `scope`, `investigation_id`, service-health `evidence:*`, or `intake_error` | `InvestigationScope` |
| `telemetry_analyst` | `LlmAgent` | Telemetry Analyst | `scope` | `summarize_anomalies`, `compare_to_baseline`, `get_telemetry` | `telemetry_finding`, `evidence:*` | `TelemetryFinding` |
| `topology_analyst` | `LlmAgent` | Network Topology Analyst | `scope` | `lookup_component`, `get_service_dependencies`, `get_blast_radius`, `trace_dependency_paths` | `topology_finding`, `evidence:*` | `TopologyFinding` |
| `knowledge_researcher` | `LlmAgent` | Knowledge Researcher | `scope` | `search_runbooks`, `search_incidents`, `search_technical_docs`, `get_document` | `knowledge_finding`, `evidence:*` | `KnowledgeFinding` |
| `incident_analyst` | `LlmAgent` | Incident Analyst | `scope`, 3 findings, evidence catalog | `generate_hypothesis_candidates`, `compare_with_incident` | `incident_analysis`, `evidence:*` | `IncidentAnalysis` |
| `report_drafter` | `LlmAgent` | Operations Coordinator — synthesis | scope, findings, analysis, evidence catalog | `validate_evidence_ids`, `check_recommendation_policy` | `report_draft` | `ReportDraft` |
| `evidence_verifier` | `BaseAgent` (deterministic) | Verification Agent — primary | all findings, draft, `evidence:*` | — | `verification` | `VerificationResult` |
| `review_verifier` | `LlmAgent` | Verification Agent — secondary | analysis, draft, verification | — | `review` | `ReviewResult` |
| `finalizer` | `BaseAgent` (deterministic) | Operations Coordinator — final report | everything | — | `final_report` | `InvestigationReport` |

## Responsibilities and boundaries

**intake** validates (length, control characters), redacts secrets, flags
injection attempts, binds the dataset (never chosen by a model), fixes the time
windows (last 2 h vs. preceding 6 h), and determines candidate services: those
named in the request plus those whose service-level latency/error metrics are
anomalous. If telemetry is unavailable it widens the scope to all services and
flags it. Invalid requests set `intake_error`; every later stage then skips.

**telemetry_analyst** decides what to inspect and interprets results; all
statistics come from tools. Must report data gaps and tool failures (status
`partial`/`failed`).

**topology_analyst** works without telemetry (that is what makes it parallel-
safe): finds the most specific shared dependencies of the candidate services,
computes rule-based blast radius, traces propagation paths, and reports
uncertainty (inferred edges, unknown services, services that do not share the
leading component).

**knowledge_researcher** retrieves runbooks, incidents and documents; separates
applicable current runbooks from outdated/conflicting and quarantined ones.
Document text is untrusted data; suspicious documents are withheld.

**incident_analyst** reconciles: obtains rule-based candidate hypotheses (with
support/contradiction and categorical confidence), checks misleading historical
similarities with `compare_with_incident`, and returns ranked hypotheses,
alternatives, unexplained observations and read-only checks. Historical
incidents are references, never support.

**report_drafter** writes the summary and prioritized read-only steps and must
validate its citations and steps with tools before finishing.

**evidence_verifier** — see [security.md](security.md) and
`opspilot/core/verification.py`: citation existence, hypothesis support,
draft-claim support, contradictions, read-only policy, deprecated/quarantined
sources, secret/injection echoes, missing information.

**review_verifier** may only *add* concerns (a `warning` forces
`requires_human_review`); it cannot clear deterministic findings.

**finalizer** assembles the `InvestigationReport`; status and escalation are
rule-based (`opspilot/core/report.py`); policy-violating steps are removed and
listed in `removed_recommendations`.

## Failure behaviour

Any LLM agent whose model call fails (timeout, quota, transport) or exceeds its
budget finishes with a schema-valid `status: failed` output via
`on_model_error_callback` / `BudgetPlugin`, so downstream stages and the report
can state what is missing. See [orchestration.md](orchestration.md).
