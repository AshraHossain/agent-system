# ADR-0002: Deterministic detection separated from LLM investigation

**Context.** LLMs fabricate numbers, and their output is not reproducible.
Detection quality must be measurable on its own.

**Decision.** Detectors (static threshold, rolling baseline, robust
median/MAD z-score, window comparison) are pure functions over pandas
series. They emit `Anomaly` and `EvidenceItem` records that carry
timestamps, entities, observed values, baselines, and thresholds. The LLM
receives summaries and IDs only.

**Alternatives.** LLM-based anomaly spotting (rejected). Isolation Forest or
Prophet (deferred; can be added behind the same interface).

**Consequences.** Detection precision and recall are reported separately
from RCA accuracy. Detector mistakes ("incorrect initial detector output")
reach the LLM, and the eval tests whether contradiction checks catch them.
