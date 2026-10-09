# Tool reference (deterministic tools)

Every tool has a Pydantic input and output schema, validates its input, and
fails with a typed error from [`netpulse/errors.py`](../netpulse/errors.py):

| Error | Meaning | `recoverable` |
|---|---|---|
| `ToolInputError` | Invalid input: unknown entity, malformed id, bad window | no |
| `DataNotFoundError` | The dataset, entity or document does not exist | no |
| `DataCorruptError` | Stored data failed validation | no |

Graph nodes (Phase 6+) map these errors to `ErrorRecord`s. No tool calls an LLM.

## Data access — `netpulse/data/`

`DatasetStore(root)` is the **only** path from `netpulse` to on-disk data. It
refuses a root inside a `labels` directory and has no code path to evaluation
labels.

| Tool | Input | Output |
|---|---|---|
| `telemetry_window` | `TelemetryQuery(dataset_id, window_start, window_end, as_of, history_hours=26, entity_ids?, metrics?)` | `TelemetryWindow`: visible rows (history plus window), per-entity `EntityCoverage`, and a `TelemetryWindowRef` for state |
| `events` | dataset, window, `as_of` | `EventRecord[]` |
| `maintenance` | dataset | `MaintenanceWindow[]` |
| `topology` | — | validated `Topology` |

- **Visibility:** a row is returned only if `timestamp + delay_s ≤ as_of`.
  Late rows are counted as `pending_points`, and their values are never
  exposed.
- **Missing data:** reported as gaps and coverage ratios, never imputed.
  `coverage_evidence()` renders it as `ev-dq-*` evidence, which states
  explicitly that missing data is evidence of neither health nor failure.
- **Path safety:** `dataset_id` must match `^[a-z0-9][a-z0-9_-]{0,63}$`, and
  the resolved path must sit directly under `cases/`.

## Topology — `netpulse/topology/`

| Tool | Input | Output |
|---|---|---|
| `shortest_path` | two node ids | `PathResult(nodes, links)` |
| `blast_radius` | node or link id | `BlastRadius`: isolated nodes, affected services, customers at risk, max criticality, `redundant` |
| `localize` | `LocalizationQuery(symptomatic_entities)` | `LocalizationResult`: ranked `Candidate`s, a greedy `minimal_cover`, `corroborated`, and a causation caveat |
| `related_events` / `overlapping_maintenance` | entities and window | the events and windows touching those entities, or the endpoints of those links |

**Localization rules.** A candidate *explains* a symptom when:

- it is the symptomatic entity itself;
- it is an endpoint of a symptomatic link; or
- it appears in a symptomatic service's `depends_on` or `probe_path`.

Candidates are ordered by:

1. whether they have a symptom of their own;
2. how many symptoms they explain;
3. how specific they are (smallest blast radius first).

`minimal_cover` greedily picks symptomatic candidates until every symptom is
explained. When it needs more than one entity, that indicates multiple
simultaneous faults. When no candidate has a symptom of its own,
`corroborated` is false. That is the deterministic signal for the "fault
outside the evidence" case.

Evidence renderers are `blast_radius_evidence`, `localization_evidence`
(verdict first, so truncation cannot drop it), `event_evidence` and
`maintenance_evidence`. Topology evidence is `trusted=True`, because it comes
from validated structure rather than prose.

## Retrieval — `netpulse/retrieval/`

| Tool | Input | Output |
|---|---|---|
| `search_runbooks` | `RetrievalQuery(text, categories, entity_ids, top_k, min_score)` | `RetrievalResult(documents, conflicts)` |
| `search_incidents` | same, plus `as_of` (incidents opened later are excluded) | `RetrievalResult(documents)` |

**Ranking.** BM25 (built in, about 40 lines, deterministic) over title,
metadata and body, multiplied by:

- 1.5 when a declared category matches the query's categories;
- an additional 0.25 when a queried entity is mentioned.

**Untrusted text handling:**

1. **Sanitization.** Control and bidirectional-override characters are
   removed and the length is capped. Nothing else is deleted, so the evidence
   stays faithful.
2. **Injection flags.** Instruction-like patterns are flagged:
   `ignore_instructions`, `role_override`, `imperative_action`,
   `bypass_approval`, `forced_conclusion`. On the v1 corpus, only RB-012
   (the injection fixture) is flagged.
3. **Prompt delimiting.** `untrusted_block()` wraps text in
   `<untrusted_document … trust="untrusted">` and defangs any embedded
   delimiter.
4. **Evidence.** `document_evidence()` produces `trusted=False` evidence.
   The verifier (Phase 7) rejects any hypothesis supported **only** by
   untrusted evidence.
5. **Actions.** `cited_actions` is intersected with the static action
   catalog. Unknown action names in a document are dropped.

**Conflicts.** "Conflicts with RB-xxx" and "RB-xxx takes precedence" are
parsed from runbook text and returned as `RunbookConflict`s. They are
surfaced, not silently resolved, so the investigator and reviewer both see
them.

## Limitations

- BM25 misses paraphrases that share no tokens with the query. The corpus is
  small and full of identifiers, which suits lexical search, but a real
  corpus would need hybrid retrieval.
- Injection detection is pattern-based and **will** miss novel phrasings.
  It is a visibility aid, not a defence. The real controls are structural:
  untrusted evidence cannot establish a cause alone, actions come only from
  the catalog, and policy and approval are deterministic.
- Conflict detection only finds conflicts that runbooks declare. Implicit
  contradictions between documents are not detected.
- Localization uses the static topology only. It does not model routing
  state, ECMP hashing, or traffic shifts.
