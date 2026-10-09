# ARCHITECTURE.md — OpsPilot AI

## Layering

```
opspilot/
  contracts/   Pydantic models: requests, evidence, tool I/O, findings, report  (no ADK)
  core/        Deterministic business logic: telemetry stats, topology graph,
               retrieval, hypothesis rules, verification, policy, security  (no ADK)
  datasets/    Synthetic world: topology, knowledge corpus, eval cases, generator
  adk/         Framework layer: tool wrappers, agents, callbacks, plugin,
               model factory (Gemini or mock), runner, sessions
  eval/        Evaluation harness and metrics
  cli.py       `opspilot` command: build-data, investigate, demo, eval, show
adk_apps/opspilot_ai/agent.py   exposes `root_agent` for `adk web` / `adk api_server`
```

Rule: `contracts/` and `core/` never import `google.adk`. The framework layer
adapts core functions into ADK tools and agents.

## Agent hierarchy

```mermaid
graph TD
  ROOT["opspilot_investigation<br/>SequentialAgent"]
  ROOT --> INTAKE["intake<br/>BaseAgent · deterministic"]
  ROOT --> PAR["specialists<br/>ParallelAgent"]
  PAR --> TEL["telemetry_analyst<br/>LlmAgent"]
  PAR --> TOP["topology_analyst<br/>LlmAgent"]
  PAR --> KNO["knowledge_researcher<br/>LlmAgent"]
  ROOT --> INC["incident_analyst<br/>LlmAgent"]
  ROOT --> DRAFT["report_drafter<br/>LlmAgent"]
  ROOT --> VER["evidence_verifier<br/>BaseAgent · deterministic"]
  ROOT --> REV["review_verifier<br/>LlmAgent · secondary"]
  ROOT --> FIN["finalizer<br/>BaseAgent · deterministic"]
```

With `OPSPILOT_MAX_INVESTIGATION_ROUNDS=2`, specialists through
`evidence_verifier` run inside a `LoopAgent` (max 2 rounds) ending in a
deterministic `reinvestigation_gate` that retries failed stages; see
[docs/orchestration.md](docs/orchestration.md#optional-re-investigation-rounds).

## Task flow

```mermaid
sequenceDiagram
  participant U as Engineer
  participant R as Runner (+BudgetPlugin)
  participant I as intake
  participant S as specialists (parallel)
  participant A as incident_analyst
  participant D as report_drafter
  participant V as evidence_verifier
  participant L as review_verifier
  participant F as finalizer
  U->>R: report text (+ dataset_id in session state)
  R->>I: validate, redact, detect injection, build scope
  I-->>R: state: request, scope
  R->>S: telemetry / topology / knowledge concurrently
  S-->>R: state: *_finding + evidence:* records
  R->>A: findings + evidence catalog
  A-->>R: state: incident_analysis (ranked hypotheses)
  R->>D: analysis + findings
  D-->>R: state: report_draft
  R->>V: deterministic checks over draft/analysis/evidence
  V-->>R: state: verification
  R->>L: secondary review
  L-->>R: state: review
  R->>F: assemble + rule-based status/escalation
  F-->>U: state: final_report (InvestigationReport)
```

## Evidence flow

Tools are pure functions in `core/`. The ADK wrapper for each tool calls the
core function, then registers every returned `Evidence` object in session state
under `evidence:<ID>`. IDs are content-derived (`EV-TEL-1a2b3c4d`), so a repeated
call yields the same ID (idempotent) and parallel agents never collide.

```mermaid
flowchart LR
  DB[(SQLite dataset<br/>telemetry · topology · documents)] --> CORE[core/* pure functions]
  CORE --> WRAP[adk/tools.py wrapper]
  WRAP -->|compact result + evidence IDs| LLM[Specialist LlmAgent]
  WRAP -->|state_delta evidence:ID| STATE[(Session state)]
  LLM -->|output_schema finding citing IDs| STATE
  STATE --> VER[evidence_verifier]
  VER -->|every cited ID must exist in state| REPORT[final_report]
```

Large data (raw telemetry series, full documents) never enter session state;
evidence records hold a short summary, the source reference (table/entity/
metric/window or document ID) and a few key numbers.

## Verification process

```mermaid
flowchart TD
  IN[incident_analysis + report_draft + findings + evidence:*] --> C1{Cited evidence IDs exist?}
  C1 --> C2{Each hypothesis has ≥1 non-historical supporting evidence?}
  C2 --> C3{Draft claims ⊆ specialist findings?<br/>services · components}
  C3 --> C4{Contradicting evidence on ranked hypotheses?}
  C4 --> C5{Recommendations pass read-only policy?}
  C5 --> C6{No secrets / injection payloads in output?}
  C6 --> C7{Missing / delayed telemetry affecting scope?}
  C7 --> OUT[VerificationResult: checks, issues, verdict]
  OUT --> REV[review_verifier adds semantic concerns only]
  REV --> FIN[finalizer: status & escalation rules]
```

Status rules (finalizer, deterministic):

| Status | Condition |
|---|---|
| `failed` | Run aborted (timeout/budget/exception) **or** all three specialists failed. |
| `inconclusive` | No hypothesis with non-historical support while anomalies exist, **or** a specialist failed, **or** critical telemetry missing for the leading hypothesis' component. |
| `requires_human_review` | Verification issues of severity `error` (unsupported claims, invalid citations, policy violations), contradicting evidence on the top hypothesis, or review_verifier concerns. |
| `investigated` | Otherwise (including "no fault found" in normal operations). |

## Bounded execution

| Limit | Mechanism | Default |
|---|---|---|
| Model requests | `RunConfig.max_llm_calls` + `BudgetPlugin.before_model_callback` | 40 |
| Tool invocations | `BudgetPlugin.before_tool_callback` returns an error result once exceeded | 60 |
| Iterations per agent | per-agent model-call cap in `BudgetPlugin` | 8 |
| Investigation duration | `asyncio.timeout` around the run | 120 s |
| Output size | Pydantic `max_length` on report fields + finalizer truncation | see `config.py` |
| Retries | live: google-genai `HttpRetryOptions` (bounded attempts); no unbounded retries anywhere | 2 attempts |

## Sessions

`DatabaseSessionService` on SQLite (`var/sessions.db`) persists events and
state; investigations can be listed, inspected and resumed (stages whose
`output_key` already exists are skipped). Tests use `InMemorySessionService`.
Details: [docs/session_management.md](docs/session_management.md).
