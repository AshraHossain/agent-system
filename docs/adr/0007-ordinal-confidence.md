# ADR-0007: Ordinal, rule-based confidence

**Context.** Numeric confidence from an LLM is uncalibrated. The spec forbids
unjustified numerical confidence values.

**Decision.** `Hypothesis` has no numeric confidence field (enforced by
`extra="forbid"` and a test). `rank_hypotheses` assigns a `ConfidenceLevel`
from trusted-support counts, source diversity, contradictions, and data
coverage, and it writes a rationale that cites evidence IDs.

**Alternatives.** LLM self-rating; scores calibrated on eval labels (would
leak labels).

**Consequences.** Confidence is coarse but explainable and reproducible. The
thresholds are configuration and are covered by eval regression tests.
