# Graph workflow

> This document is the full target specification. The **implementation status**
> table below shows what exists today.

## Implementation status (Phase 7)

| Node / edge | Status |
|---|---|
| 1–11, 13b `escalate`, 14, `failure_report` | Implemented (`netpulse/graph/nodes.py`, `verification.py`, `ranking.py`) |
| Verify → generate retry loop; rank → telemetry extra round; recommend → escalate | Implemented (`routing.py`, unit-tested in `tests/test_routing.py`) |
| Fatal → `failure_report`; deadline → `escalate`; step budget → failure report | Implemented (routers, `runner.py`) |
| Per-node timeouts (tool / LLM), critical vs. degradable nodes | Implemented (`wrapper.py`) |
| Ollama generator with a visible fallback; scripted test generator | Implemented (`netpulse/llm/`, see [llm.md](llm.md)) |
| 12 `policy_review`, 13a `human_approval` (interrupt), remediation proposals, SQLite checkpointer | Phase 8 |

**Phase 7 escalation rule**, refined by the Phase 8 policy engine: escalate
when the assessment is not conclusive **and** at least one confirmed anomaly
exists, or when the operator-reported severity is critical. A run with no
anomaly and low severity ends as a plain inconclusive report.

**Critical nodes** are intake, telemetry, detection, verification, ranking,
escalate and report. In these nodes, a non-recoverable tool error or a
timeout ends the run via `failure_report`. In every other node it is
recorded and the run continues degraded. Unexpected exceptions are always
fatal.

## Node contracts

"Reads" and "Writes" refer to fields of `InvestigationState`
([state_model.md](state_model.md)). Every node also appends a `NodeTrace`,
and it appends an `ErrorRecord` whenever it degrades or fails.

| # | Node | Kind | Reads | Writes | On failure |
|---|---|---|---|---|---|
| 1 | `intake_validate` | deterministic | raw input | `incident_id`, `submission`, `request_metadata`, `budget`, `deadline_at`, `status=running`, counters = 0 | Validation error → `fatal_error` → `failure_report` |
| 2 | `classify_incident` | rules | `submission` | `classification` | Unknown → `category=unknown`, continue |
| 3 | `retrieve_telemetry` | tool | `submission`, `classification`, `investigation_rounds` | `telemetry_window` (ref only), data-quality evidence, `investigation_rounds += 1` | One bounded retry for transient errors. Missing data is recorded as `ev-dq-*` evidence, not as an error. If the dataset is unreachable → fatal. |
| 4 | `detect_anomalies` | deterministic | `telemetry_window` | `detected_anomalies`, `ev-anom-*` | Per-detector failure is recorded and the other detectors continue |
| 5 | `analyze_topology` | deterministic | anomalies, `submission.suspected_entities` | `affected_nodes`, `affected_services`, `topology_evidence`, `ev-topo-*`, `ev-evt-*`, `ev-mnt-*` | Degrade: no topology evidence plus an error record |
| 6 | `retrieve_history` | tool (BM25) | classification, anomalies | `historical_incidents`, `ev-hist-*` (untrusted) | Degrade to an empty list |
| 7 | `retrieve_runbooks` | tool (BM25) | classification, anomalies | `retrieved_runbooks`, `ev-rb-*` (untrusted) | Degrade to an empty list |
| 8 | `generate_hypotheses` | **LLM** (or heuristic) | evidence summaries by ID, `verifier_feedback` | `hypotheses`, `retry_count` | Parse or timeout error → counts as an attempt. Zero hypotheses is allowed and leads to an inconclusive result. |
| 9 | `verify_evidence` | deterministic | `hypotheses`, `evidence_references`, topology | `verification_results` (append), `verifier_feedback` | n/a (pure function) |
| 10 | `rank_hypotheses` | deterministic | accepted hypotheses, evidence | `confidence_assessment` | n/a |
| 11 | `recommend_actions` | deterministic (catalog) | ranking, runbook refs, topology | `recommended_actions` | n/a |
| 12 | `policy_review` | deterministic | actions, classification, assessment, services | `policy_decisions`, `approval_status` | n/a |
| 13a | `human_approval` | `interrupt()` | everything needed for review | `reviewer_decisions` (append), `approval_status` | Invalid resume payload → re-interrupt with an error message |
| 13b | `escalate` | deterministic | assessment, errors | `approval_status=escalated`, `status=escalated` | n/a |
| 14 | `compile_report` | deterministic | all | `final_report`, terminal `status` | If report validation fails → `failure_report` |
| — | `failure_report` | deterministic | all | structured `final_report` with `outcome=failed`, `status=failed` | Last resort. It must not raise. |

## Conditional edges

| From | Condition (evaluated in order) | To |
|---|---|---|
| any node | `fatal_error` | `failure_report` |
| any node | `now > deadline_at` | `escalate` (reason: budget exhausted) |
| `intake_validate` | otherwise | `classify_incident` |
| `verify_evidence` | latest result passed | `rank_hypotheses` |
| `verify_evidence` | blocking issues and `retry_count < max_hypothesis_retries` | `generate_hypotheses` |
| `verify_evidence` | otherwise (retries exhausted) | `rank_hypotheses`, using only accepted hypotheses (possibly none) |
| `rank_hypotheses` | `evidence_sufficient` | `recommend_actions` |
| `rank_hypotheses` | not sufficient and `investigation_rounds < max_investigation_rounds` | `retrieve_telemetry` (wider window) |
| `rank_hypotheses` | otherwise | `recommend_actions` (diagnostics only, marked inconclusive) |
| `policy_review` | any decision `escalate` or `deny`, or the assessment is not conclusive | `escalate` |
| `policy_review` | any decision `require_approval` | `human_approval` |
| `policy_review` | otherwise (read-only diagnostics on a conclusive result) | `compile_report` |
| `human_approval` | `approve` or `reject` | `compile_report` |
| `human_approval` | `request_more_investigation` and rounds left | `retrieve_telemetry` |
| `human_approval` | `request_more_investigation` and no rounds left | `escalate` |
| `escalate` | always | `compile_report` |

Routers are pure functions of state. Each one is unit-tested with
hand-built state dicts, with no graph execution needed.

## Budgets

| Budget | Default | Enforcement |
|---|---|---|
| `max_hypothesis_retries` | 2 | Router after `verify_evidence` |
| `max_investigation_rounds` | 2 | Routers after `rank_hypotheses` and `human_approval` |
| `max_graph_steps` | 60 | LangGraph `recursion_limit`. `GraphRecursionError` is caught by the runner and turned into a failure report. |
| `wall_clock_seconds` | 300 | `deadline_at` is checked by the node wrapper and by routers. Time spent paused for approval does not count: the deadline is re-based on resume. |
| `tool_timeout_seconds` | 20 | Tool calls run under a timeout. A timeout is a recoverable `ErrorRecord`. |
| `llm_timeout_seconds` | 120 | Passed to the Ollama client. A timeout counts as a failed attempt. |

The worst-case number of LLM calls per run is
`(max_hypothesis_retries + 1) × (max_investigation_rounds + reviewer re-investigations)`.
With the defaults and no reviewer loop, that is 6.

## Sufficiency rule (deterministic, in `rank_hypotheses`)

The result is `evidence_sufficient` only when all of the following hold:

- the top hypothesis has **≥ 2 trusted evidence items** from **≥ 2 different
  sources** (for example, detector + topology);
- the top hypothesis has **0 unrefuted contradictions**;
- the top hypothesis does not depend on entities whose telemetry is missing
  for more than 50% of the window.

The run is `conclusive` when it is sufficient **and** the runner-up ranks
strictly lower. Any other outcome is reported as inconclusive and lists
`missing_evidence`. These thresholds live in config and are covered by
evaluation regression tests.
