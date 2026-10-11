# Evaluation (Phase 10)

> All results are measured on the **synthetic** v1 dataset (20 cases). They say nothing about real networks.

```bash
uv run python -m eval.report --provider heuristic --check   # run, write report, gate on thresholds
uv run python -m eval.report --provider ollama              # reported, never gated
uv run python -m eval.detection_benchmark                   # detection layer only
```

Reports are written to `eval/reports/<dataset>-<provider>-<git-sha>.{json,md}` (git-ignored; CI uploads them as an
artifact; the API serves them at `GET /eval/reports`). `generators_used` in each report records which generator actually
produced hypotheses, so an Ollama run that fell back to the heuristic generator cannot be mistaken for an Ollama result.

## Method

Every case runs end to end first. A run that pauses for approval is approved once by a synthetic senior reviewer.
Only then are `eval/labels/v1/labels.jsonl` read. `netpulse/` has no path to labels (`tests/test_security.py`).

| Metric | Definition |
|---|---|
| detection precision / recall | `eval/detection_benchmark.py`, reported separately from the rest |
| `root_cause_top1` / `top3` | `cause_category` equals a labeled category and `suspected_root_entity` is in its entities; cases with no labeled cause are excluded |
| `outcome_accuracy` | `root_cause_identified` for root-cause cases; `inconclusive` or `escalated` otherwise |
| `citation_validity` | cited IDs that exist in the registry and name no entity or share one with the hypothesis |
| `unsupported_claim_rate` | hypotheses rejected by the verifier on any attempt, or with no trusted support |
| `recommendation_jaccard` / `recall` | recommended catalog IDs vs. labeled acceptable actions, root-cause cases only |
| `escalation_precision` / `recall` | positive = outcome `escalated` or escalation reasons present; against `should_escalate` |
| `escalation_plan_*` | the literal PLAN.md wording (`escalated` or `inconclusive`); counts every healthy-network "inconclusive" as an escalation, so it is reported, not gated |
| `tool_call_success_rate` | node-trace entries `ok` or `degraded` over all non-interrupted entries |
| `latency_ms_p50` / `p95` | summed node durations excluding `human_approval` (machine dependent, not gated) |
| `cost_usd` | 0: heuristic and local providers report no paid tokens |
| `executed_actions` | must be 0 |

The escalation definition differs from PLAN.md section 4 on purpose, and both are reported. Under the plan wording the
heuristic baseline scores precision and recall of 0.6, because two multi-fault and critical-severity cases are escalated
to a senior reviewer rather than ending `inconclusive`, and two healthy-network cases end `inconclusive` without needing
escalation.

## Regression gate

`eval/thresholds.json` holds `min` and `max` bounds, set just below the measured heuristic baseline. `--check` exits 1 on
any breach, and CI runs it (`.github/workflows/ci.yml`, job `eval-regression`). Tighten the bounds when the baseline
improves; never loosen them to make a change pass without recording why.

## Heuristic baseline (v1)

| Metric | Value |
|---|---|
| detection precision / recall | 0.941 / 1.000 |
| outcome accuracy | 1.000 |
| root cause top-1 / top-3 | 0.941 / 0.941 |
| citation validity | 0.980 |
| unsupported-claim rate | 0.000 |
| recommendation Jaccard / recall | 0.766 / 0.956 |
| escalation precision / recall | 1.000 / 1.000 |
| tool-call success | 1.000 |

Known weaknesses, from the per-case failure analysis in each report:

- `case-05` (missing data): the top hypothesis is `unknown@acc-6`, not the degraded link. The run escalates correctly.
- `case-10` and `case-17`: a topology evidence item cited by a hypothesis names entities outside that hypothesis.
- Several cases omit an acceptable diagnostic (`check_collector_health`, `inspect_interface_counters`).
- The heuristic investigator is written against the scenario taxonomy and may overfit the generator. It is a baseline,
  not the product. An Ollama run has not been made here: there is no Ollama server in this environment.
