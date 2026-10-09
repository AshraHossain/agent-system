# State model

Implemented in [`netpulse/state.py`](../netpulse/state.py), with value schemas in
[`netpulse/models.py`](../netpulse/models.py). The contract tests are in
`tests/test_state_model.py`.

## Representation

- The graph state is a `TypedDict` (`InvestigationState`).
- Values are **JSON-compatible dicts** produced by
  `Model.model_dump(mode="json")`. Nodes re-validate the values they read
  through the Pydantic models. Keeping state as plain JSON means the SQLite
  checkpointer stores portable data instead of pickled or custom types, and
  it keeps old checkpoints readable when model classes change (ADR-0003).
- All models use `extra="forbid", frozen=True`. Unknown fields are a
  validation error, not something silently ignored.

## Update rules

| Rule | Fields | Behavior |
|---|---|---|
| Replace | most fields | The last write wins. Each such field has **one owning node**, so ownership is what keeps the behavior predictable. |
| Immutable merge | `evidence_references` | New IDs are added. Re-adding an identical item is a no-op. Changing an existing item raises `EvidenceConflictError`, which fails the step. |
| Append-only | `verification_results`, `reviewer_decisions`, `errors`, `node_trace` | Audit logs. Never rewritten. |

## Field ownership

| Field | Type | Owner (sole writer) | Notes |
|---|---|---|---|
| `incident_id` | `str` | intake_validate | Also the LangGraph `thread_id` |
| `request_metadata` | `RequestMetadata` | intake_validate | Includes the execution ID and LLM config snapshot |
| `submission` | `IncidentSubmission` | intake_validate | Free text is untrusted |
| `budget`, `deadline_at` | `Budget`, ISO time | intake_validate (`deadline_at` re-based by human_approval on resume) | |
| `classification` | `Classification` | classify_incident | Rule-based |
| `telemetry_window` | `TelemetryWindowRef` | retrieve_telemetry | **Reference only.** Rows stay in the data store. |
| `detected_anomalies` | `Anomaly[]` | detect_anomalies | Each item points at its `ev-anom-*` |
| `affected_nodes`, `affected_services` | `str[]` | analyze_topology | |
| `topology_evidence` | dict | analyze_topology | Candidate roots, blast radius, evidence IDs |
| `historical_incidents` | `RetrievedDocumentRef[]` | retrieve_history | Titles and scores only, no full text |
| `retrieved_runbooks` | `RetrievedDocumentRef[]` | retrieve_runbooks | Same |
| `evidence_references` | `{id: EvidenceItem}` | many (immutable merge) | The registry that every claim must cite |
| `hypotheses` | `Hypothesis[]` | generate_hypotheses | Replaced on each attempt. Earlier attempts survive in `verification_results`. |
| `verification_results` | `VerificationResult[]` | verify_evidence | Append-only |
| `verifier_feedback` | `str[]` | verify_evidence | Blocking issues for the next attempt |
| `confidence_assessment` | `ConfidenceAssessment` | rank_hypotheses | Ordinal confidence, deterministic |
| `recommended_actions` | `ProposedAction[]` | recommend_actions | `executed` is fixed to `False` |
| `policy_decisions` | `PolicyDecision[]` | policy_review | |
| `approval_status` | `ApprovalStatus` | policy_review → human_approval / escalate | Explicit hand-off order |
| `reviewer_decisions` | `ReviewerDecision[]` | human_approval | Append-only |
| `retry_count` | `int` | generate_hypotheses | Reset to 0 at the start of each investigation round |
| `investigation_rounds` | `int` | retrieve_telemetry | |
| `status` | `WorkflowStatus` | lifecycle nodes | |
| `fatal_error` | `bool` | node wrapper | |
| `errors` | `ErrorRecord[]` | all (append) | Messages are truncated and contain no secrets |
| `node_trace` | `NodeTrace[]` | node wrapper (append) | |
| `final_report` | `FinalReport` | compile_report / failure_report | |

## Size discipline

- Telemetry rows, full runbook text, and full incident write-ups are **never**
  stored in state. State holds IDs, refs, and summaries of at most 600
  characters per evidence item.
- The prompt for node 8 is built from evidence summaries. Tools re-read raw
  data by reference when they need it.
- Expected state size is roughly 20–80 KB per incident. That is small enough
  to checkpoint after every step.

## Facts vs. inferences

| Type | Created by | Contains numbers? | Can be cited? |
|---|---|---|---|
| `EvidenceItem` | deterministic tools, reviewer | yes (`observed_value`, `baseline_value`, `threshold`) | yes |
| `Hypothesis` | LLM or heuristic | **no** measurement fields | no — it *cites* evidence |
| `RankedHypothesis` / `ConfidenceAssessment` | deterministic ranker | counts only | no |
