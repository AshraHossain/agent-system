---
paths:
  - "opspilot/core/**"
---

# OpsPilot Deterministic Core Rules (`opspilot/core/**`)

- **Framework-Free Pure Core**: `opspilot/core/` must NEVER import `google.adk`, `google.genai`, or `fastapi`. All business logic must be deterministic, side-effect-free, and unit-testable without ADK.
- **Code Calculates, Agents Interpret**: Statistics (`mean`, `p95`, `zscore`, `delta_pct`), episode detection, graph BFS/closure, blast-radius tables (`IMPACT_RULES`), BM25 + vector RRF fusion, hypothesis candidate generation, verification checks, read-only policy enforcement, and final status/escalation rules (`decide_status`, `decide_escalation`) live exclusively in `opspilot/core/`.
- **Immutable DTOs**: Any helper Pydantic model or dataclass in `opspilot/core/` (`Rule`, `Edge`, `Hit`, `PolicyDecision`, `ValidatedText`, `CandidateSet`, `IncidentComparison`, `ModelPrice`, `PriceTable`, `CostEstimate`, `Document`) MUST be frozen (`ConfigDict(frozen=True)` or `@dataclass(frozen=True)`).
- **Read-Only Dataset Access**: `Dataset` connections in `core/dataset.py` must always open SQLite with `mode=ro` URI flags and validate `dataset_id` against `^[A-Za-z0-9_-]{1,32}$` with path-escape checks.
- **Untrusted Context Isolation**: Every external/retrieved document snippet must pass through `security.isolate_untrusted`, which strips spoofed delimiters (`<untrusted_context>` and `<<untrusted_document>>`), normalizes control characters, redacts secrets, withholds suspected prompt injections, and wraps content in `<untrusted_context id="...">...</untrusted_context>`.
