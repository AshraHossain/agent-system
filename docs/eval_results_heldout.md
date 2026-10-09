# Held-out evaluation

Held-out cases (`opspilot/datasets/cases/heldout.yaml`) were written after the rules, mock policies and main labels were frozen at `e54b3c2`, and aimed at suspected blind spots. Same author as the main set: this reduces but does not remove co-design bias. Run: `uv run opspilot eval --split heldout`.

## Baseline (before any held-out-driven change) — OpsPilot evaluation — heldout split, mock (scripted-mock)

- ADK 2.11.0, generator v1, git e54b3c2, 2026-10-09T19:30:44+00:00
- mock provider measures the deterministic pipeline, not LLM reasoning

| Metric | Value |
|---|---|
| cases | 10 |
| completion_rate | 1.0 |
| status_accuracy | 0.9 |
| escalation_accuracy | 0.8 |
| root_cause_top1 | 0.6667 |
| root_cause_top3 | 0.7778 |
| false_alarm_rate | 0.0 |
| summary_correctness | 0.8 |
| affected_services_jaccard | None |
| citation_validity | 1.0 |
| unsupported_claim_rate | 0.0 |
| runbook_precision | None |
| runbook_recall | None |
| irrelevant_runbooks_included | 0 |
| missing_evidence_detection | None |
| malicious_document_handling | 0.0 |
| tool_invocation_recall | 1.0 |
| tool_permission_violations | 0 |
| llm_calls_total | 134 |
| tool_calls_total | 150 |
| tokens_total | None |
| latency_s_mean | 1.0713 |
| latency_s_p95 | 1.655 |

| Case | Tags | Status (expected) | Esc | Top1 | Top3 | Cites valid | Runbook P/R | Tools | Latency s |
|---|---|---|---|---|---|---|---|---|---|
| H01 | packet_loss, core_layer | investigated (investigated) | ✓ | True | True | 29/29 | None/None | 1.0 | 1.132 |
| H02 | resource_saturation | investigated (investigated) | ✓ | True | True | 13/13 | None/None | 1.0 | 0.846 |
| H03 | resource_saturation, core_layer | investigated (investigated) | ✓ | True | True | 13/13 | None/None | 1.0 | 0.825 |
| H04 | incomplete_telemetry, congestion | inconclusive (inconclusive) | ✓ | False | False | 15/15 | None/None | 1.0 | 0.805 |
| H05 | application_side, normal_network | inconclusive (requires_human_review) | ✓ | False | True | 8/8 | None/None | 1.0 | 1.164 |
| H06 | packet_loss, recent_onset | investigated (investigated) | ✓ | True | True | 17/17 | None/None | 1.0 | 1.212 |
| H07 | transient, congestion | investigated (investigated) | ✗ | False | False | 0/0 | None/None | 1.0 | 0.826 |
| H08 | malicious_document, congestion | investigated (investigated) | ✗ | True | True | 16/16 | None/None | 1.0 | 1.655 |
| H09 | multiple_faults | investigated (investigated) | ✓ | True | True | 31/31 | None/None | 1.0 | 1.459 |
| H10 | normal | investigated (investigated) | ✓ | None | None | 0/0 | None/None | 1.0 | 0.789 |
