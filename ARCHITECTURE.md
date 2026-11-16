# ARCHITECTURE.md — OpsPilot AI

## 1. Layered System Architecture (`flowchart LR`)

Rule: `opspilot/contracts/` and `opspilot/core/` never import `google.adk`, `google.genai`, or `fastapi`. Every Pydantic contract and core DTO is immutable (`ConfigDict(frozen=True)` or `@dataclass(frozen=True)`). The ADK and FastAPI layers are thin adapters over `opspilot/core/`.

```mermaid
flowchart LR
    subgraph Client["Client & CI Surface"]
        CLI["opspilot CLI (demo, investigate, resume, eval)"]
        HTTP["HTTP / WebSocket Client"]
    end

    subgraph Edge["Edge & Guardrails (opspilot/api.py)"]
        TMW["TelemetryMiddleware (X-Request-ID, X-Process-Time-Ms, OWASP Headers)"]
        RMW["RateLimitMiddleware (SlidingWindowRateLimiter 429)"]
        AMW["BearerTokenAuth (Bearer / X-API-Key, hmac.compare_digest 401)"]
        IMW["InputBoundaryMiddleware (Path 400, Payload 413, MIME 415)"]
        PRB["Probes (GET /health, GET /ready, GET /readyz)"]
    end

    subgraph Orchestration["ADK Orchestration & Guardrail Plugins (opspilot/adk)"]
        RUN["Runner + BudgetPlugin (LLM, Iteration, Tool, Duration Caps)"]
        SEQ["opspilot_investigation (SequentialAgent)"]
        PAR["specialists (ParallelAgent)"]
        LOOP["investigation_rounds (Optional LoopAgent, max 2)"]
    end

    subgraph Core["Deterministic Pure Core (opspilot/core)"]
        SEC["security.py (NFKC, Redact, Injection Scan, untrusted_context)"]
        TEL["telemetry.py (Baseline Z-Score, Delta %, Episode Detection)"]
        TOP["topology.py (Depth-Bounded BFS, Redundancy & Blast Radius)"]
        RET["retrieval.py + knowledge.py (BM25 + Vector RRF Fusion)"]
        ANA["analysis.py (Signature Rules & Historical Comparison)"]
        VER["verification.py + policy.py + report.py (Deterministic Gate & Status)"]
    end

    subgraph Backends["Live Cloud vs. Deterministic Offline Backends"]
        GEMINI[("Live Cloud: Gemini 2.5 Flash / Vertex AI + GeminiEmbedder")]
        MOCK[("Offline Default: ScriptedLlm + HashingEmbedder + SQLite mode=ro")]
    end

    CLI --> RUN
    HTTP --> TMW --> RMW --> AMW --> IMW --> RUN
    TMW --> PRB
    RUN --> SEQ --> PAR & LOOP
    PAR --> SEC & TEL & TOP & RET
    SEQ --> ANA & VER
    SEQ -. "Live Mode" .-> GEMINI
    SEQ -. "Mock Mode" .-> MOCK
    TEL & TOP & RET -. "SQLite URI mode=ro" .-> MOCK
```

## 2. End-to-End Runtime Sequence (`sequenceDiagram`)

```mermaid
sequenceDiagram
    autonumber
    participant U as Engineer / API Client
    participant E as Edge Middleware (api.py)
    participant R as Runner + BudgetPlugin
    participant I as intake (BaseAgent)
    participant S as specialists (ParallelAgent)
    participant A as incident_analyst (LlmAgent)
    participant D as report_drafter (LlmAgent)
    participant V as evidence_verifier (BaseAgent)
    participant L as review_verifier (LlmAgent)
    participant F as finalizer (BaseAgent)

    rect rgb(235, 245, 255)
        Note over U,I: Stage 1 — Edge Guardrails & Deterministic Scope Fixing
        U->>E: Submit incident report (+ dataset_id)
        E->>E: Validate X-Request-ID, Rate Limit (429), Bearer/X-API-Key (401), Payload/MIME (413/415)
        E->>R: Invoke opspilot_investigation
        R->>I: Validate, normalize, redact secrets, scan for prompt injection, fix 2h window & 6h baseline
        I-->>R: state_delta: scope (or intake_error)
    end

    rect rgb(240, 255, 240)
        Note over R,S: Stage 2 — Concurrent Specialist Investigation & Evidence Registration
        R->>S: Dispatch telemetry_analyst, topology_analyst, knowledge_researcher in parallel
        S-->>R: state_delta: telemetry_finding, topology_finding, knowledge_finding + evidence:EV-*
    end

    rect rgb(255, 248, 235)
        Note over R,D: Stage 3 — Hypothesis Synthesis & Read-Only Draft Generation
        R->>A: Reconcile specialist findings + deterministic candidate rules
        A-->>R: state_delta: incident_analysis (ranked hypotheses)
        R->>D: Synthesize summary, key facts, and read-only diagnostic steps
        D-->>R: state_delta: report_draft
    end

    rect rgb(252, 240, 255)
        Note over R,F: Stage 4 — Deterministic Verification, Secondary Review & Final Assembly
        R->>V: Verify citation existence, entity alignment, metric sufficiency, rule ceiling & policy
        V-->>R: state_delta: verification (VerificationResult)
        R->>L: Secondary semantic review (can only add concerns, never clear errors)
        L-->>R: state_delta: review (ReviewResult)
        R->>F: Assemble frozen InvestigationReport + rule-based status & escalation
        F-->>U: state_delta: final_report (+ OWASP & latency headers)
    end
```

## 3. Package Layout

```text
opspilot/
  contracts/   Frozen Pydantic models (ConfigDict(frozen=True)): requests, evidence, findings, verification, report (no ADK)
  core/        Deterministic pure logic: telemetry stats, topology graph, hybrid retrieval, hypothesis rules,
               verification, read-only policy, security & <untrusted_context> framing, auth, cost (no ADK)
  datasets/    Synthetic world: topology, knowledge corpus, main (C01-C14) & held-out (H01-H10) cases, SQLite generator
  adk/         Framework layer: tool wrappers, agents, callbacks, BudgetPlugin, model factory (Gemini or ScriptedLlm), runner
  eval/        Evaluation harness, p50/p95 latency & accuracy metrics, guardrail perturbations, verifier matrix
  api.py       GET /runs/{run_id}, GET /ready, GET /readyz, TelemetryMiddleware, RateLimitMiddleware,
               BearerTokenAuth, InputBoundaryMiddleware, create_app
  cli.py       opspilot command: build-data, investigate, demo, eval, list, show, resume, serve, token
adk_apps/opspilot_ai/agent.py   exposes root_agent for adk web / adk api_server
```

## 4. Agent Hierarchy

```mermaid
flowchart TD
    ROOT["opspilot_investigation (SequentialAgent)"]
    ROOT --> INTAKE["intake (BaseAgent · deterministic)"]
    ROOT --> PAR["specialists (ParallelAgent)"]
    PAR --> TEL["telemetry_analyst (LlmAgent)"]
    PAR --> TOP["topology_analyst (LlmAgent)"]
    PAR --> KNO["knowledge_researcher (LlmAgent)"]
    ROOT --> INC["incident_analyst (LlmAgent)"]
    ROOT --> DRAFT["report_drafter (LlmAgent)"]
    ROOT --> VER["evidence_verifier (BaseAgent · deterministic)"]
    ROOT --> REV["review_verifier (LlmAgent · secondary)"]
    ROOT --> FIN["finalizer (BaseAgent · deterministic)"]
```

With `OPSPILOT_MAX_INVESTIGATION_ROUNDS=2`, `specialists` through `evidence_verifier` run inside a `LoopAgent` (max 2 rounds) ending in a deterministic `reinvestigation_gate` that retries failed stages; see [docs/orchestration.md](docs/orchestration.md#optional-re-investigation-rounds).

## 5. Evidence & Untrusted Data Flow

Tools are pure functions in `opspilot/core/`. Each ADK wrapper in `opspilot/adk/tools.py` validates arguments via Pydantic, calls the core function, and registers every returned `Evidence` object in session state under `evidence:<ID>`. IDs are content-derived (`EV-TEL-1a2b3c4d`), making tool invocations idempotent across retries and parallel branches.

```mermaid
flowchart LR
    DB[("SQLite dataset (mode=ro)")] --> CORE["core/* pure functions"]
    CORE --> WRAP["adk/tools.py wrapper"]
    WRAP -- "compact result + evidence IDs (<untrusted_context>)" --> LLM["Specialist LlmAgent"]
    WRAP -- "state_delta evidence:ID" --> STATE[("Session state")]
    LLM -- "output_schema finding citing IDs" --> STATE
    STATE --> VER["evidence_verifier"]
    VER -- "every cited ID & metric verified" --> REPORT["final_report"]
```

## 6. Deterministic Verification Process

```mermaid
flowchart TD
    IN["incident_analysis + report_draft + findings + evidence:*"] --> C1{"Cited evidence IDs exist?"}
    C1 --> C2{"Each hypothesis has >=1 direct supporting reading?"}
    C2 --> C3{"Entity alignment & metric sufficiency match category?"}
    C3 --> C4{"Confidence & ranking within deterministic rule bounds?"}
    C4 --> C5{"Draft claims subset of specialist findings?"}
    C5 --> C6{"Recommendations pass read-only policy?"}
    C6 --> C7{"No secrets, injection echoes, or overstated certainty?"}
    C7 --> OUT["Frozen VerificationResult: checks, issues, verdict"]
    OUT --> REV["review_verifier adds semantic concerns only"]
    REV --> FIN["finalizer: deterministic status & escalation rules"]
```

Status rules (`opspilot/core/report.py::decide_status`, deterministic):

| Status | Condition |
|---|---|
| `failed` | Run aborted (timeout/budget/exception) **or** all three specialists failed. |
| `inconclusive` | No hypothesis with non-historical support while anomalies exist, **or** a specialist failed, **or** critical telemetry missing for the leading hypothesis' component. |
| `requires_human_review` | Verification issues of severity `error` (unsupported claims, invalid citations, policy violations), contradicting evidence on the top hypothesis, weak/telemetry-artifact/application-side leading hypothesis, or `review_verifier` warnings. |
| `investigated` | Otherwise (including "no fault found" in normal operations). |

## 7. Bounded Execution & Edge Limits

| Limit | Mechanism | Default |
|---|---|---|
| Model requests | `RunConfig.max_llm_calls` + `BudgetPlugin.before_model_callback` | 40 |
| Tool invocations | `BudgetPlugin.before_tool_callback` returns an error result once exceeded | 60 |
| Iterations per agent | Per-agent model-call cap in `BudgetPlugin` | 8 |
| Investigation duration | `asyncio.timeout` around the run | 120 s |
| HTTP rate limit | `RateLimitMiddleware` (`RATE_LIMIT_PER_MINUTE`) | 120 req/min (`429`, `Retry-After: 60`) |
| HTTP payload cap | `InputBoundaryMiddleware` (`OPSPILOT_MAX_PAYLOAD_BYTES`) | 5 MiB (`413`) |
| Output size | Pydantic `max_length` on report fields + finalizer truncation (`limit_report_size`) | 60,000 chars |
| Retries | Live: `google-genai` `HttpRetryOptions` (bounded attempts); no unbounded retries anywhere | 2 attempts |
