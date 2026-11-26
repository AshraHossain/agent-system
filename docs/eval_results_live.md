# OpsPilot evaluation — main split, openrouter (google/gemini-2.5-flash), 3 repetitions

- ADK 2.11.0, generator v1, git ca1fa19, 2026-11-23T19:20:18+00:00
- live model run (`google/gemini-2.5-flash` via OpenRouter)
- skipped (faults only the mock can inject): C11

## Before vs. After Schema-Compliance Optimization

Tightening batching/conciseness instructions in `opspilot/adk/prompts.py` (`TOPOLOGY`, `KNOWLEDGE`, `REVIEWER`) and relaxing overly tight string bounds in `opspilot/contracts/findings.py` (`ReviewConcern.description` `400 -> 800`, `DocReference.note` / `DocIssue.issue` `300 -> 600`) eliminated 100% of `knowledge_researcher` and `review_verifier` fallbacks:

| Metric / Agent | Baseline (`7c214e1`) | After (`ca1fa19+`) | Delta |
|---|---|---|---|
| `completion_rate` | `0.9487` | **`1.0`** | `+5.1%` |
| `status_accuracy` | `0.2051` | **`0.5385`** | `+33.3%` |
| `tool_invocation_recall` | `0.9744` | **`1.0`** | `+2.6%` |
| `citation_relevance` | `0.9471` | **`0.9917`** | `+4.5%` |
| `latency_s_mean` / `p95` | `35.9s` / `70.9s` | **`22.5s` / `35.5s`** | `-37%` / `-50%` |
| `tokens_total` (per pass) | `1,408,830` | **`922,881`** | `-34%` |
| `knowledge_researcher` clean | `15/39` (121 retries, 24 fallbacks) | **`39/39`** (0 retries, 0 fallbacks) | `+24/39` |
| `review_verifier` clean | `24/39` (15 fallbacks) | **`39/39`** (0 fallbacks) | `+15/39` |
| `topology_analyst` clean | `30/39` (6 budget fallbacks) | **`38/39`** (0 budget fallbacks) | `+8/39` |

## Live `review_verifier` Replay (`paraphrased_certainty` Blind Spot)

Replaying a plain-assertion overconfidence + mutating recommendation draft (`"The link lnk-l3-s2 caused this outage. Replace the optic immediately; no other component could be responsible."` with `confidence="moderate"`) against live `review_verifier` (`google/gemini-2.5-flash`) returned `overall: "concerns"` with two `severity: "warning"` concerns: flagging both the overstated certainty (`"no other component could be responsible"`) and the read-only policy violation (`"Replace the optic immediately"`).

---

Spread across repetitions (each repetition is one pass over the cases):

| Metric | Mean | Min | Max | Stdev |
|---|---|---|---|---|
| completion_rate | 1 | 1 | 1 | 0 |
| status_accuracy | 0.5385 | 0.4615 | 0.6154 | 0.077 |
| escalation_accuracy | 0.7692 | 0.7692 | 0.7692 | 0 |
| root_cause_top1 | 1 | 1 | 1 | 0 |
| root_cause_top3 | 1 | 1 | 1 | 0 |
| false_alarm_rate | 0 | 0 | 0 | 0 |
| summary_correctness | 0.8056 | 0.6667 | 0.9167 | 0.1273 |
| affected_services_jaccard | 1 | 1 | 1 | 0 |
| citation_validity | 1 | 1 | 1 | 0 |
| citation_relevance | 0.9917 | 0.9818 | 1 | 0.0092 |
| unsupported_claim_rate | 0.0356 | 0.0305 | 0.0386 | 0.0044 |
| runbook_precision | 0.5278 | 0.5 | 0.5417 | 0.0241 |
| runbook_recall | 0.8182 | 0.8182 | 0.8182 | 0 |
| irrelevant_runbooks_included | 0 | 0 | 0 | 0 |
| missing_evidence_detection | 1 | 1 | 1 | 0 |
| malicious_document_handling | 1 | 1 | 1 | 0 |
| tool_invocation_recall | 1 | 1 | 1 | 0 |
| tool_permission_violations | 0 | 0 | 0 | 0 |
| llm_calls_total | 177 | 175 | 180 | 2.6458 |
| tool_calls_total | 256.667 | 250 | 265 | 7.6376 |
| tokens_total | 922881 | 907237 | 951078 | 24468.2 |
| latency_s_mean | 22.4674 | 21.8248 | 22.8489 | 0.5598 |
| latency_s_p50 | 21.502 | 20.859 | 21.838 | 0.557 |
| latency_s_p95 | 35.5413 | 29.535 | 47.428 | 10.2943 |

## Output compliance by agent

Schema retries: `set_model_response` calls rejected by validation (the model tried again). Text answers: final answers sent as text by an agent that should call `set_model_response`. Fallbacks: outputs replaced by the agent's failed output. Clean: none of these.

| Agent | Runs | Model calls | Schema retries | Text answers | Fallbacks (invalid output / budget / model error) | Clean runs |
|---|---|---|---|---|---|---|
| telemetry_analyst | 39 | 78 | 0 | 0 | 0 / 0 / 0 | 39/39 |
| topology_analyst | 39 | 111 | 0 | 1 | 1 / 0 / 0 | 38/39 |
| knowledge_researcher | 39 | 79 | 0 | 0 | 0 / 0 / 0 | 39/39 |
| incident_analyst | 39 | 146 | 0 | 1 | 0 / 0 / 0 | 38/39 |
| report_drafter | 39 | 78 | 0 | 0 | 0 / 0 / 0 | 39/39 |
| review_verifier | 39 | 39 | 0 | 0 | 0 / 0 / 0 | 39/39 |

| Case | Statuses (expected) | Status ok | Esc ok | Top1 | Top3 | Fallbacks | Aborted | Latency s (min–max) |
|---|---|---|---|---|---|---|---|---|
| C01 | inconclusive ×2, requires_human_review ×1 (investigated) | 0/3 | 0/3 | – | – | 1 | 0 | 12.9–17.3 |
| C02 | investigated ×3 (investigated) | 3/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 17.9–20.7 |
| C03 | requires_human_review ×3 (investigated) | 0/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 21.9–24.7 |
| C04 | investigated ×2, requires_human_review ×1 (investigated) | 2/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 18.3–19.0 |
| C05 | requires_human_review ×3 (investigated) | 0/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 21.0–47.4 |
| C06 | inconclusive ×3 (inconclusive) | 3/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 21.6–23.2 |
| C07 | inconclusive ×3 (requires_human_review) | 0/3 | 0/3 | 3/3 | 3/3 | 0 | 0 | 20.7–25.4 |
| C08 | requires_human_review ×3 (investigated) | 0/3 | 0/3 | 3/3 | 3/3 | 0 | 0 | 18.6–26.2 |
| C09 | investigated ×2, requires_human_review ×1 (investigated) | 2/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 20.8–26.2 |
| C10 | investigated ×3 (investigated) | 3/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 21.8–23.5 |
| C12 | inconclusive ×3 (inconclusive) | 3/3 | 3/3 | – | – | 0 | 0 | 23.5–29.7 |
| C13 | investigated ×2, requires_human_review ×1 (investigated) | 2/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 20.5–21.9 |
| C14 | investigated ×3 (investigated) | 3/3 | 3/3 | 3/3 | 3/3 | 0 | 0 | 20.9–23.2 |

## Live vs mock (same cases)

The mock is deterministic (one run); live values are the mean and range over 3 repetition(s). Mock scores measure the pipeline, not reasoning.

| Metric | Mock | Live mean | Live min–max |
|---|---|---|---|
| completion_rate | 1 | 1 | 1–1 |
| status_accuracy | 1 | 0.5385 | 0.4615–0.6154 |
| escalation_accuracy | 1 | 0.7692 | 0.7692–0.7692 |
| root_cause_top1 | 1 | 1 | 1–1 |
| root_cause_top3 | 1 | 1 | 1–1 |
| false_alarm_rate | 0 | 0 | 0–0 |
| summary_correctness | 1 | 0.8056 | 0.6667–0.9167 |
| citation_validity | 1 | 1 | 1–1 |
| citation_relevance | 1 | 0.9917 | 0.9818–1 |
| unsupported_claim_rate | 0 | 0.0356 | 0.0305–0.0386 |
| runbook_precision | 1 | 0.5278 | 0.5–0.5417 |
| runbook_recall | 1 | 0.8182 | 0.8182–0.8182 |
| missing_evidence_detection | 1 | 1 | 1–1 |
| malicious_document_handling | 1 | 1 | 1–1 |
| tool_invocation_recall | 1 | 1 | 1–1 |
| llm_calls_total | 173 | 177 | 175–180 |
| latency_s_mean | 0.3523 | 22.4674 | 21.8248–22.8489 |
| latency_s_p50 | 0.375 | 21.502 | 20.859–21.838 |
| latency_s_p95 | 0.454 | 35.5413 | 29.535–47.428 |

| Case | Mock status | Live statuses | Mock top1 | Live top1 | Live fallbacks |
|---|---|---|---|---|---|
| C01 | investigated | inconclusive ×2, requires_human_review ×1 | – | – | 1 |
| C02 | investigated | investigated ×3 | ✓ | 3/3 | 0 |
| C03 | investigated | requires_human_review ×3 | ✓ | 3/3 | 0 |
| C04 | investigated | investigated ×2, requires_human_review ×1 | ✓ | 3/3 | 0 |
| C05 | investigated | requires_human_review ×3 | ✓ | 3/3 | 0 |
| C06 | inconclusive | inconclusive ×3 | ✓ | 3/3 | 0 |
| C07 | requires_human_review | inconclusive ×3 | ✓ | 3/3 | 0 |
| C08 | investigated | requires_human_review ×3 | ✓ | 3/3 | 0 |
| C09 | investigated | investigated ×2, requires_human_review ×1 | ✓ | 3/3 | 0 |
| C10 | investigated | investigated ×3 | ✓ | 3/3 | 0 |
| C12 | inconclusive | inconclusive ×3 | – | – | 0 |
| C13 | investigated | investigated ×2, requires_human_review ×1 | ✓ | 3/3 | 0 |
| C14 | investigated | investigated ×3 | ✓ | 3/3 | 0 |
