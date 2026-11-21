# OpsPilot evaluation — main split, openrouter (google/gemini-2.5-flash), 3 repetitions

- ADK 2.11.0, generator v1, git 7c214e1, 2026-11-21T03:06:46+00:00
- live model run
- skipped (faults only the mock can inject): C11

Spread across repetitions (each repetition is one pass over the cases):

| Metric | Mean | Min | Max | Stdev |
|---|---|---|---|---|
| completion_rate | 0.9487 | 0.9231 | 1 | 0.0444 |
| status_accuracy | 0.2051 | 0.1538 | 0.2308 | 0.0445 |
| escalation_accuracy | 0.7692 | 0.6923 | 0.8462 | 0.077 |
| root_cause_top1 | 1 | 1 | 1 | 0 |
| root_cause_top3 | 1 | 1 | 1 | 0 |
| false_alarm_rate | 0 | 0 | 0 | 0 |
| summary_correctness | 0.7778 | 0.75 | 0.8333 | 0.0481 |
| affected_services_jaccard | 1 | 1 | 1 | 0 |
| citation_validity | 1 | 1 | 1 | 0 |
| citation_relevance | 0.9471 | 0.884 | 0.9863 | 0.0552 |
| unsupported_claim_rate | 0.0232 | 0.008 | 0.0535 | 0.0262 |
| runbook_precision | 0.5202 | 0.4848 | 0.5455 | 0.0316 |
| runbook_recall | 0.7879 | 0.7273 | 0.8182 | 0.0525 |
| irrelevant_runbooks_included | 0 | 0 | 0 | 0 |
| missing_evidence_detection | 1 | 1 | 1 | 0 |
| malicious_document_handling | 1 | 1 | 1 | 0 |
| tool_invocation_recall | 0.9744 | 0.9231 | 1 | 0.0444 |
| tool_permission_violations | 0 | 0 | 0 | 0 |
| llm_calls_total | 236 | 235 | 238 | 1.7321 |
| tool_calls_total | 311 | 285 | 327 | 22.7156 |
| tokens_total | 1.40883e+06 | 1.38964e+06 | 1.43908e+06 | 26510.9 |
| latency_s_mean | 35.9005 | 33.7832 | 38.269 | 2.2534 |
| latency_s_p50 | 29.9593 | 28.073 | 33.666 | 3.2102 |
| latency_s_p95 | 70.9487 | 55.442 | 101.032 | 26.0571 |

## Output compliance by agent

Schema retries: `set_model_response` calls rejected by validation (the model tried again). Text answers: final answers sent as text by an agent that should call `set_model_response`. Fallbacks: outputs replaced by the agent's failed output. Clean: none of these.

| Agent | Runs | Model calls | Schema retries | Text answers | Fallbacks (invalid output / budget / model error) | Clean runs |
|---|---|---|---|---|---|---|
| telemetry_analyst | 39 | 78 | 0 | 0 | 0 / 0 / 0 | 39/39 |
| topology_analyst | 39 | 187 | 1 | 2 | 2 / 6 / 0 | 30/39 |
| knowledge_researcher | 39 | 185 | 121 | 10 | 10 / 14 / 0 | 15/39 |
| incident_analyst | 39 | 138 | 0 | 1 | 1 / 0 / 0 | 38/39 |
| report_drafter | 39 | 81 | 0 | 1 | 1 / 0 / 0 | 38/39 |
| review_verifier | 39 | 39 | 0 | 0 | 15 / 0 / 0 | 24/39 |

| Case | Statuses (expected) | Status ok | Esc ok | Top1 | Top3 | Fallbacks | Aborted | Latency s (min–max) |
|---|---|---|---|---|---|---|---|---|
| C01 | inconclusive ×3 (investigated) | 0/3 | 0/3 | – | – | 3 | 0 | 14.3–16.0 |
| C02 | inconclusive ×3 (investigated) | 0/3 | 3/3 | 3/3 | 3/3 | 3 | 0 | 28.1–52.2 |
| C03 | inconclusive ×1, investigated ×2 (investigated) | 2/3 | 3/3 | 3/3 | 3/3 | 2 | 0 | 18.9–48.8 |
| C04 | requires_human_review ×1, inconclusive ×2 (investigated) | 0/3 | 3/3 | 3/3 | 3/3 | 3 | 0 | 27.4–52.8 |
| C05 | inconclusive ×3 (investigated) | 0/3 | 3/3 | 3/3 | 3/3 | 6 | 0 | 33.7–101.0 |
| C06 | inconclusive ×3 (inconclusive) | 3/3 | 3/3 | 3/3 | 3/3 | 6 | 0 | 30.3–54.2 |
| C07 | inconclusive ×3 (requires_human_review) | 0/3 | 0/3 | 3/3 | 3/3 | 4 | 0 | 18.9–20.4 |
| C08 | requires_human_review ×1, inconclusive ×2 (investigated) | 0/3 | 2/3 | 3/3 | 3/3 | 3 | 0 | 23.4–46.4 |
| C09 | requires_human_review ×3 (investigated) | 0/3 | 3/3 | 3/3 | 3/3 | 1 | 0 | 22.8–29.1 |
| C10 | inconclusive ×2, investigated ×1 (investigated) | 1/3 | 3/3 | 3/3 | 3/3 | 2 | 0 | 21.0–57.9 |
| C12 | failed ×2, inconclusive ×1 (inconclusive) | 1/3 | 1/3 | – | – | 7 | 0 | 45.2–55.4 |
| C13 | investigated ×1, inconclusive ×2 (investigated) | 1/3 | 3/3 | 3/3 | 3/3 | 3 | 0 | 24.9–53.5 |
| C14 | inconclusive ×3 (investigated) | 0/3 | 3/3 | 3/3 | 3/3 | 6 | 0 | 24.9–35.1 |

## Live vs mock (same cases)

The mock is deterministic (one run); live values are the mean and range over 3 repetition(s). Mock scores measure the pipeline, not reasoning.

| Metric | Mock | Live mean | Live min–max |
|---|---|---|---|
| completion_rate | 1 | 0.9487 | 0.9231–1 |
| status_accuracy | 1 | 0.2051 | 0.1538–0.2308 |
| escalation_accuracy | 1 | 0.7692 | 0.6923–0.8462 |
| root_cause_top1 | 1 | 1 | 1–1 |
| root_cause_top3 | 1 | 1 | 1–1 |
| false_alarm_rate | 0 | 0 | 0–0 |
| summary_correctness | 1 | 0.7778 | 0.75–0.8333 |
| citation_validity | 1 | 1 | 1–1 |
| citation_relevance | 1 | 0.9471 | 0.884–0.9863 |
| unsupported_claim_rate | 0 | 0.0232 | 0.008–0.0535 |
| runbook_precision | 1 | 0.5202 | 0.4848–0.5455 |
| runbook_recall | 1 | 0.7879 | 0.7273–0.8182 |
| missing_evidence_detection | 1 | 1 | 1–1 |
| malicious_document_handling | 1 | 1 | 1–1 |
| tool_invocation_recall | 1 | 0.9744 | 0.9231–1 |
| llm_calls_total | 173 | 236 | 235–238 |
| latency_s_mean | 0.3419 | 35.9005 | 33.7832–38.269 |
| latency_s_p50 | 0.356 | 29.9593 | 28.073–33.666 |
| latency_s_p95 | 0.422 | 70.9487 | 55.442–101.032 |

| Case | Mock status | Live statuses | Mock top1 | Live top1 | Live fallbacks |
|---|---|---|---|---|---|
| C01 | investigated | inconclusive ×3 | – | – | 3 |
| C02 | investigated | inconclusive ×3 | ✓ | 3/3 | 3 |
| C03 | investigated | inconclusive ×1, investigated ×2 | ✓ | 3/3 | 2 |
| C04 | investigated | requires_human_review ×1, inconclusive ×2 | ✓ | 3/3 | 3 |
| C05 | investigated | inconclusive ×3 | ✓ | 3/3 | 6 |
| C06 | inconclusive | inconclusive ×3 | ✓ | 3/3 | 6 |
| C07 | requires_human_review | inconclusive ×3 | ✓ | 3/3 | 4 |
| C08 | investigated | requires_human_review ×1, inconclusive ×2 | ✓ | 3/3 | 3 |
| C09 | investigated | requires_human_review ×3 | ✓ | 3/3 | 1 |
| C10 | investigated | inconclusive ×2, investigated ×1 | ✓ | 3/3 | 2 |
| C12 | inconclusive | failed ×2, inconclusive ×1 | – | – | 7 |
| C13 | investigated | investigated ×1, inconclusive ×2 | ✓ | 3/3 | 3 |
| C14 | investigated | inconclusive ×3 | ✓ | 3/3 | 6 |
