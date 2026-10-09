# ADR-0006: Categorical, rule-derived confidence

**Status:** Accepted

## Decision
No numeric confidence. Hypotheses get `strong | moderate | weak`:
* `strong` — ≥2 independent *kinds* of direct telemetry support (e.g. errors +
  loss on the same link), no contradicting evidence, data complete.
* `moderate` — 1 kind of direct support, or ≥2 with gaps.
* `weak` — only indirect support (historical similarity, topology inference)
  or contradicted.
The report's `confidence_rationale` states which rule applied and why.

## Rationale
A numeric score implies calibration we do not have. Categories with stated
rules are auditable.
