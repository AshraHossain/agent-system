# PLAN.md — OpsPilot AI

OpsPilot AI is a **read-only decision-support system** that investigates
network service degradation (latency, packet loss) using synthetic telemetry,
a synthetic topology, fictional runbooks and synthetic incident history. It
produces a validated, evidence-linked investigation report that separates
**facts**, **hypotheses** and **recommendations**. It never modifies network
infrastructure — it has no tool capable of doing so.

This document explains *why* the system is shaped the way it is. The *how* is
in [ARCHITECTURE.md](ARCHITECTURE.md) and `docs/`.

---

## 1. Why Google ADK

| Need | What ADK 2.11.0 provides (verified against the installed package) |
|---|---|
| Specialist LLM agents with typed outputs | `LlmAgent(output_schema=PydanticModel, output_key=...)`; ADK validates the final response against the schema and writes it to session state. With tools present, ADK injects a `set_model_response` tool so structure is enforced only on the final answer. |
| Deterministic orchestration | Workflow agents `SequentialAgent`, `ParallelAgent`, `LoopAgent` run sub-agents in a fixed order without an LLM deciding the route. |
| Deterministic, non-LLM steps inside the same tree | Subclassing `BaseAgent` and implementing `_run_async_impl` lets validators run as first-class agents that emit `Event`s with `state_delta`. |
| Tool declarations | Plain typed Python functions become `FunctionTool`s; a `tool_context: ToolContext` parameter gives controlled access to session state and is hidden from the model. |
| Guardrails | Agent/model/tool callbacks (`before_tool_callback`, `on_model_error_callback`, …) and runner-level `BasePlugin`s. |
| Bounded execution | `RunConfig.max_llm_calls`, plus plugin hooks for tool/time budgets. |
| Session state & persistence | `DatabaseSessionService` (SQLAlchemy; SQLite locally) persists sessions, events and state. `InMemorySessionService` for tests. |
| Model pluggability | `LlmAgent.model` accepts a Gemini model string *or* any `BaseLlm` subclass — which is how the deterministic mock model plugs in. |
| Dev UI / serving / eval | `adk web`, `adk api_server`, `adk run`, `adk eval`. |

ADK is used for **orchestration, LLM interaction, state, callbacks and
sessions**. Everything that can be computed (statistics, graph traversal,
retrieval scoring, validation, status rules) lives in framework-free Python
under `opspilot/core/`, so it is unit-testable without ADK and would survive a
framework change.

## 2. Why each agent exists

The prompt defines six logical responsibilities. Each maps to one or more ADK
agents. An agent exists only if it needs **language understanding** or is the
natural home of a **deterministic gate**; everything else is a tool.

| Logical role | ADK agent(s) | Kind | Why it exists |
|---|---|---|---|
| Operations Coordinator | `intake` | `BaseAgent` (deterministic) | Validates and redacts the request, fixes the time window and candidate services. Doing this deterministically means the scope can't be hallucinated and every later stage sees the same scope. |
| | `report_drafter` | `LlmAgent` | Writes the human-readable summary and orders recommendations — a language task. |
| | `finalizer` | `BaseAgent` (deterministic) | Assembles the validated `InvestigationReport`, strips policy-violating steps, computes status/escalation **by rule**. The LLM never sets status. |
| Telemetry Analyst | `telemetry_analyst` | `LlmAgent` + tools | Chooses which entities/metrics to inspect and interprets tool output. All statistics come from tools. |
| Network Topology Analyst | `topology_analyst` | `LlmAgent` + tools | Interprets dependency paths and blast radius computed by deterministic graph tools; reports topology gaps. |
| Knowledge Researcher | `knowledge_researcher` | `LlmAgent` + tools | Formulates searches, judges relevance, flags conflicting/deprecated/suspicious documents. Documents are returned as quarantined, untrusted data. |
| Incident Analyst | `incident_analyst` | `LlmAgent` + tools | Reconciles the three specialist findings into ranked hypotheses. Candidate hypotheses come from a deterministic rule tool; the agent explains, orders and proposes checks. |
| Verification Agent | `evidence_verifier` | `BaseAgent` (deterministic) | Primary verifier: citation existence, support, contradictions, read-only policy, secret leakage. |
| | `review_verifier` | `LlmAgent` | Secondary semantic review. It can only *add* concerns (which push the status to `requires_human_review`); it cannot clear deterministic failures. |

## 3. Why each orchestration pattern

```
SequentialAgent opspilot_investigation
 ├─ intake                 (deterministic)
 ├─ ParallelAgent specialists
 │    ├─ telemetry_analyst
 │    ├─ topology_analyst
 │    └─ knowledge_researcher
 ├─ incident_analyst
 ├─ report_drafter
 ├─ evidence_verifier      (deterministic)
 ├─ review_verifier
 └─ finalizer              (deterministic)
```

* **Root `SequentialAgent`, not an LLM coordinator with `sub_agents` transfer.**
  LLM-driven transfer makes routing nondeterministic, makes "did every
  specialist run exactly once?" untestable, and allows delegation loops. A fixed
  sequence has an obvious termination condition (the last stage) and gives
  stable traces for evaluation. See [ADR-0002](docs/adr/0002-fixed-workflow-tree.md).
* **`ParallelAgent` only for the three specialists.** Their only input is the
  intake scope; none needs another's output. Telemetry and topology both read
  the scope's candidate services; knowledge search uses the scope's symptom
  terms. Each writes a distinct `output_key` and distinct `evidence:*` keys, so
  there are no shared-state write conflicts.
* **Sequential after the fan-out.** Incident analysis must reconcile all three
  findings, the draft must reflect the analysis, and verification must check the
  final draft — these are true data dependencies.
* **No `LoopAgent` in v1.** The verifier records `additional_evidence_requests`
  and the status becomes `inconclusive`/`requires_human_review` instead of
  re-running specialists. A bounded re-investigation loop is a documented
  future option (ADR-0002), not a default — loops multiply cost and make
  completion harder to reason about.
* **Not ADK 2.x graph `Workflow`.** The new `google.adk.workflow` graph API
  could express this, but the pipeline has no conditional routing that needs a
  graph, and the classic workflow agents are the longer-established API.

## 4. How this differs from a LangGraph stateful workflow

The repository previously held a one-node LangGraph scaffold, so the
comparison is concrete:

| Aspect | LangGraph (`StateGraph`) | This ADK design |
|---|---|---|
| Unit of composition | Nodes are functions over a shared typed state dict; edges (incl. conditional) are explicit graph structure. | Agents are objects; composition is a tree of workflow agents (`Sequential`/`Parallel`) with LLM agents as leaves. |
| State | Graph state object with reducers per key; checkpointers persist it. | Session state is a flat key/value map updated via event `state_delta`s; `SessionService` persists events + state. |
| LLM/tool loop | You wire the tool-calling loop (or use a prebuilt ReAct node). | Each `LlmAgent` owns its tool loop; ADK handles function-call/response turns and `output_schema` validation. |
| Branching | First-class conditional edges and cycles. | Fixed workflow agents; dynamic routing would need LLM transfer, `LoopAgent` escalation, or the 2.x `Workflow` graph. We deliberately avoid dynamic routing. |
| Parallelism | Fan-out via multiple edges / `Send`. | `ParallelAgent` runs sub-agents concurrently on branches of the same invocation. |
| Guardrails | Implemented in node code. | Framework callbacks + runner plugins intercept every model/tool call uniformly. |

Neither is "better" in general; ADK fits here because agents-with-tools,
callbacks for policy enforcement, session services, and the dev UI/eval tooling
come from one framework, and our control flow is a fixed pipeline.

## 5. Implementation phases (status)

| # | Phase | Status (commit) |
|---|---|---|
| 1 | Repository & environment inspection | done (report only) |
| 2 | ADK API verification; pinned dependency set (`google-adk[db]==2.11.0`) | done (`203af70`) |
| 3 | PLAN.md, ARCHITECTURE.md, ADRs | done (`e6520b8`) |
| 4 | Synthetic datasets (topology, telemetry generator, knowledge corpus, incidents, eval cases) | done (`586b966`) |
| 5 | Deterministic tools + unit tests | done (`1f26086`) |
| 6 | Smallest working ADK agent (mock model) | done (`0d3fc81`) |
| 7 | Specialist agents + output contracts | done (`637480d`) |
| 8 | Orchestration, verification, finalizer | done (`c45e226`) |
| 9 | Sessions (SQLite), resume, budgets, failure handling | done (`011bebe`) |
| 10 | Evaluation harness, security hardening, observability | done (`d8dee1a`) |
| 11 | Demo CLI, `adk web` app, Dockerfile, CI | done (`a1d4f5c`) |
| 12 | Full test run + skeptical architecture review ([docs/review.md](docs/review.md)) | done (`5b9bfba` + docs) |

## 6. Non-goals and honest limits

* Not production-ready. A passing demo and green offline tests show the
  *pipeline* works; they do not show that a live model reasons well.
* The offline mock model follows a scripted, deterministic policy. Offline
  evaluation therefore measures tools, orchestration, verification and
  reporting — **not LLM reasoning quality**. Live evaluation is separate.
* Prompt injection is mitigated, not solved.
* Data are synthetic; thresholds are illustrative, not calibrated on real
  networks.
