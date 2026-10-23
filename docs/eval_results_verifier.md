# Verifier detection matrix: results

Generated with `uv run opspilot eval --verifier-matrix` (offline, mock provider,
24 cases: C01–C14 and H01–H10). Method:
[evaluation.md](evaluation.md#verifier-detection-matrix-opspilotevalverifier_matrixpy).

## Certainty lexicon (current)

`overstated_certainty` flags certainty wording in the summary or a key fact
("without any doubt", "the root cause is", "it is clear that", "definitely",
"has been proven", ...), skipping negated uses ("not confirmed", "cannot be
proven"). It is an **error** unless the leading hypothesis is `strong`, and a
**warning** when it is: strong evidence ranks a hypothesis, it does not prove
it.

| Measure | Before | Now |
|---|---|---|
| Designed-for defects caught | 162 / 162 | **162 / 162** |
| False positives on harmless edits | 0 / 45 | **0 / 45** |
| Semantic errors caught | 69 / 93 | **93 / 93** |

`paraphrased_certainty`: 24/24 flagged (8 errors, 16 warnings: 16 cases lead
with a `strong` hypothesis). No other mutation and no clean case raises the
new code.

**Read this number with care.** The matrix has one certainty phrase, and that
phrase is in the lexicon, so 24/24 is expected by construction. The broader
check is the hand-written set in `tests/test_verification.py` (8 certainty
sentences flagged, 5 hedged or technical ones such as "100% utilization" and
"session was established" not flagged), and it has the same author. A
plain assertion with no marker word ("The firewall caused this outage.")
still passes, and the test pins it as a known miss. Judging tone and
sufficiency remains the live reviewer's job (TASK.md).

## After the first fixes

Four deterministic checks were added to `core/verification.py` for the gaps
the first run found (`category_component_mismatch`, `confidence_above_rules`,
`ranking_differs_from_rules`, `summary_contradicts_findings`, plus a
`summary_incomplete` warning). Re-running the full matrix:

| Measure | First run | Now |
|---|---|---|
| Designed-for defects caught | 162 / 162 | **162 / 162** |
| False positives on harmless edits | 0 / 45 | **0 / 45** |
| Semantic errors caught | 37 / 93 | **69 / 93** |

| Blind spot | First run | Now | Caught by |
|---|---|---|---|
| `false_all_clear` | missed 21/21 | flagged 21/21 | `summary_contradicts_findings` (error) |
| `inflated_confidence` | missed 6/6 | flagged 6/6 | `confidence_above_rules` (error) |
| `swapped_ranking` | missed 2/2 | flagged 2/2 | `ranking_differs_from_rules` (warning: both cases swap two equally `strong` hypotheses, a tie the analyst may break) |
| `blamed_neighbour` | missed 3/19 | flagged 19/19 | `category_component_mismatch` (error) |
| `paraphrased_certainty` | missed 24/24 | missed 24/24 | still open at that point; closed by the certainty lexicon above |
| `dropped_service` | neutralized 21/21 | neutralized 21/21 | finalizer rebuilds the service list |

None of the 24 unmodified cases raises any of the new codes. The remaining
gap is the one the review always expected to need a real LLM reviewer.

The rest of this page records the first run, which motivated the fixes.

## First run: headline

| Measure | Result |
|---|---|
| Designed-for defects caught (`catch`, flagged or neutralized) | **162 / 162** |
| False positives on harmless edits (`benign`) | **0 / 45** |
| Semantic errors caught (`blind_spot`, flagged or neutralized) | **37 / 93** |

The first two lines confirm, across all cases rather than one hand-picked case
each, what the guardrail perturbations showed: the deterministic checks catch
what they were built for, and they do not fire on harmless changes. The third
line is the new information: **56 of 93 semantic errors reach the final report
unflagged.**

## First run: per mutation

| Mutation | Kind | Agent | Applicable | Flagged | Neutralized | Missed | Clean |
|---|---|---|---|---|---|---|---|
| `fabricated_citation` | catch | report_drafter | 24 | 24 | 0 | 0 | 0 |
| `unsupported_service` | catch | report_drafter | 22 | 22 | 0 | 0 | 0 |
| `mutating_recommendation` | catch | report_drafter | 24 | 24 | 0 | 0 | 0 |
| `injection_and_secret` | catch | report_drafter | 24 | 24 | 0 | 0 | 0 |
| `hallucinated_component` | catch | incident_analyst | 22 | 22 | 0 | 0 | 0 |
| `unsupported_hypothesis` | catch | incident_analyst | 22 | 22 | 0 | 0 | 0 |
| `fact_cites_unrelated` | catch | report_drafter | 22 | 22 | 0 | 0 | 0 |
| `swapped_support` | catch | incident_analyst | 2 | 2 | 0 | 0 | 0 |
| `reorder_facts` | benign | report_drafter | 21 | 0 | 0 | 0 | 21 |
| `neutral_sentence` | benign | report_drafter | 24 | 0 | 0 | 0 | 24 |
| `swapped_ranking` | blind_spot | incident_analyst | 2 | 0 | 0 | 2 | 0 |
| `inflated_confidence` | blind_spot | incident_analyst | 6 | 0 | 0 | 6 | 0 |
| `blamed_neighbour` | blind_spot | incident_analyst | 19 | 16 | 0 | 3 | 0 |
| `false_all_clear` | blind_spot | report_drafter | 21 | 0 | 0 | 21 | 0 |
| `paraphrased_certainty` | blind_spot | report_drafter | 24 | 0 | 0 | 24 | 0 |
| `dropped_service` | blind_spot | report_drafter | 21 | 0 | 21 | 0 | 0 |

"Applicable" varies because a mutation is skipped (and not scored) when its
precondition does not hold, e.g. ranking swaps need two hypotheses on different
components, which only C05 and H09 produce.

## First run: what the blind spots showed

| Blind spot | Result | Why | Candidate fix |
|---|---|---|---|
| `false_all_clear`: summary says "No services are affected" while three are | missed 21/21 | Summary text is checked for citations, secrets and injection, not for agreement with the findings | Deterministic: require the summary to name every affected service and the leading component, or have the finalizer prepend a generated structured line the drafter cannot change |
| `paraphrased_certainty`: "Without any doubt, this incident was caused by…" | missed 24/24 | The only overstatement check is the reviewer; offline that is a keyword rule (`confirmed`, `definitely`, `root cause is`) | Live LLM reviewer evaluation (this mutation is ready for it); deterministic backstop: forbid certainty language unless the top hypothesis is `strong` |
| `inflated_confidence`: top hypothesis raised to `strong` with unchanged evidence | missed 6/6 | Confidence is taken from the analyst; nothing recomputes it | Deterministic: the rule engine in `core/analysis.py` already derives a confidence; verifier flags an analyst confidence above it |
| `swapped_ranking`: second-best hypothesis promoted, citations intact | missed 2/2 | Each hypothesis is checked on its own; ranking is not | Deterministic: compare the analyst's order with the rule ranking and require a stated reason for any promotion |
| `blamed_neighbour`: link blamed for a device fault (C04, C14, H03) | missed 3/19 | Relevance accepts evidence about a link's *endpoints*, so `fw-1` CPU evidence "supports" `lnk-c1-fw`. The 16 caught cases are the reverse direction (device blamed for a link fault) | Deterministic: reject category/component-type mismatches (e.g. `device_resource_saturation` on a link) |
| `dropped_service`: draft omits an affected service | neutralized 21/21 | The finalizer rebuilds `affected_services` from specialist findings, so the omission never reaches the report | None needed: an example of a deterministic stage repairing an LLM error |

All five "missed" rows have a deterministic fix except `paraphrased_certainty`,
which is a language judgement and the clearest case for evaluating the live
reviewer.

## Limits

* Offline, the reviewer is the scripted mock; `blind_spot` coverage describes
  the deterministic verifier plus a keyword rule, **not** a real LLM reviewer.
  The same mutations can be replayed once a live run can mix a mutated mock
  drafter with a live reviewer.
* The mutations were written by the same author as the verifier. They are
  more varied than the guardrail perturbations (16 kinds × 24 cases with a
  clean baseline and a false-positive control) but do not replace an
  independently authored evaluation set.
* Some mutations apply to few cases (`swapped_support`, `swapped_ranking`: 2
  each), so their rates rest on little data.
