# Human approval, checkpoints, and resume

NetPulse is **read-only**. Remediation is only ever *proposed*:
`ProposedAction.executed` is fixed to `False`, and no code path executes an
action. "Approval" means an authorized reviewer accepts a proposal **for
manual execution outside NetPulse**. The report says so explicitly.

## Flow

```mermaid
sequenceDiagram
    autonumber
    participant R as Reviewer (CLI / API)
    participant S as InvestigationService
    participant G as LangGraph (thread_id = incident_id)
    participant DB as SQLite (checkpoints + audit)
    R->>S: start(submission)
    S->>DB: audit "submitted"
    S->>G: stream(initial state)
    G->>DB: checkpoint after every super-step
    G-->>G: recommend_actions → policy_review (REQUIRE_APPROVAL)
    G->>G: human_approval: build_review_request(state) → interrupt(payload)
    G->>DB: checkpoint with the pending interrupt
    S->>DB: audit "run_paused"
    Note over S,DB: The process may exit here. Nothing is held in memory.
    R->>S: status(incident) (any process, same DB)
    S->>G: get_state → pending_review (the interrupt payload)
    R->>S: decide(incident, ReviewInput)
    S->>S: role ≥ required_role? otherwise AuthorizationError + audit "review_denied"
    S->>DB: audit "review_submitted"
    S->>G: stream(Command(resume=decision))
    G->>G: human_approval re-runs from its start: same payload; interrupt returns the decision
    G->>G: validate again (schema, role, allowed choice, action ids)
    alt approve / reject
        G-->>G: compile_report (approved for manual execution / rejected_by_reviewer)
    else request_more_investigation (cycles left)
        G-->>G: retrieve_telemetry (wider window) → … → policy_review → human_approval
    else invalid or unauthorized input
        G-->>G: back to human_approval with previous_error (asks again)
    end
    S->>DB: audit "run_finished" or "run_paused"
```

## What the reviewer sees (`ReviewRequest`)

- the top three ranked hypotheses, with their deterministic rationale;
- the cited evidence items, with their IDs and numbers as detectors produced
  them;
- each pending action together with its policy decision: reasons, required
  role, reversibility, blast radius and affected services;
- the escalation reasons, for example multiple simultaneous faults or
  critical severity;
- the allowed choices and the review cycles left;
- `previous_error`, if the last input was rejected;
- the synthetic-data notice.

The payload is a **pure function of state** (`build_review_request`).
LangGraph re-executes the node from its start on resume, so the code before
`interrupt()` has no side effects and rebuilds an identical payload. A test
asserts this.

## Decisions (`ReviewInput`)

| Choice | Effect |
|---|---|
| `approve` | All pending actions, or the subset in `approved_action_ids`, are recorded as approved. The run completes. Nothing is executed. |
| `reject` | Report outcome `rejected_by_reviewer`. |
| `request_more_investigation` | Allowed while `max_review_cycles` (default 1) remain. Starts another investigation round, then policy review and approval again. |

The reviewer's comment is sanitized and stored as `ev-rev-*` evidence with
`trusted=False`. It informs later steps but cannot establish a cause on its
own. The deadline is **re-based** on resume, so time spent waiting for a
human does not exhaust the investigation budget.

## Authorization

Required roles are `operator` or `senior_operator`. Policy derives them
(`netpulse/policy/engine.py`):

- an irreversible action, a large blast radius, runbooks that conflict about
  the action, critical severity, or any escalation condition requires
  `senior_operator`;
- any other remediation requires `operator`.

The role is checked **twice**:

1. by `InvestigationService.decide()`, which refuses the decision and audits
   `review_denied`;
2. inside the graph, as defense in depth, in case anyone resumes the
   thread directly.

`reviewer_id` must come from an authenticated caller. The CLI trusts its
command-line flags and is a **local demo only**. The Phase 9 API adds token
authentication.

## Durability

- **Store.** `PersistentStore` opens one SQLite file holding LangGraph's
  `SqliteSaver` tables and an `netpulse_audit` table. The audit table is
  append-only, enforced by `UPDATE`/`DELETE` triggers.
- **Restart.** A paused run is a checkpoint with a pending interrupt.
  `tests/test_human_approval.py::test_pause_survives_process_restart` starts
  an investigation in one OS process and approves it from a **different**
  process.
- **Crash recovery.** If a process dies mid-run, `resume()` continues from
  the last completed super-step without redoing earlier work. The test
  simulates this with a `BaseException` raised inside the generator.
- **In-memory mode.** Without a store, `InMemorySaver` is used and
  `RunStatus.durable` is `False`. That mode is for tests and demos and **is
  not durable approval**.

## Audit trail

The trail is built from three sources:

1. **The `netpulse_audit` table:**
   - `submitted`;
   - `run_paused` / `run_finished`;
   - `review_submitted` / `review_denied`;
   - `resumed`.
2. **Checkpointed state:** `reviewer_decisions` (append-only reducer),
   `policy_decisions`, and `escalation_reasons`.
3. **The final report:** it repeats the policy decisions, reviewer decisions
   and escalation reasons.

## Limitations

- SQLite allows one writer at a time. Run one service process per database
  file; Postgres (`langgraph-checkpoint-postgres`) is the multi-replica
  path.
- Checkpoints contain incident data and reviewer IDs. Protect the database
  file. There is no encryption at rest.
- No retention or expiry policy exists for paused runs or checkpoints.
- The audit triggers stop accidental changes, not a malicious database
  owner. Real tamper-evidence would need hash chaining or an external sink.
