# Evaluation

## Dataset

14 labelled synthetic cases in `opspilot/datasets/cases/cases.yaml`. Each case
has a `world` (fault injections consumed only by the generator) and `labels`
(consumed only by `opspilot/eval`). Agents receive only the request text and a
dataset ID; `tests/test_dataset.py::test_ground_truth_never_stored` and
`tests/test_evaluation.py::test_agents_never_receive_ground_truth` enforce this.

| Case | Scenario | Expected status |
|---|---|---|
| C01 | normal operations | investigated, no escalation |
| C02 | uplink congestion | investigated |
| C03 | physical-layer packet loss | investigated |
| C04 | firewall saturation | investigated |
| C05 | congestion + DNS saturation (multiple faults) | investigated |
| C06 | loss with missing/delayed telemetry | inconclusive |
| C07 | contradictory evidence (counters vs probes) | requires_human_review |
| C08 | congestion resembling a historical optic failure | investigated (monitor) |
| C09 | WAN degradation with keyword-similar irrelevant runbooks | investigated |
| C10 | malicious retrieved document | investigated + security escalation |
| C11 | model timeout (incident analyst) | inconclusive |
| C12 | telemetry backend failure | inconclusive |
| C13 | deprecated runbook conflicting with current one | investigated |
| C14 | LB saturation with undocumented dependency | investigated |

## Held-out split

`opspilot/datasets/cases/heldout.yaml` (H01–H10) was written after the rules,
mock policies and main labels were frozen, targeting suspected blind spots.
Its **baseline** (top-1 0.67, top-3 0.78, status 0.9, escalation 0.8, malicious-
document handling 0.0) exposed four real gaps, which were then fixed with
general rules; see [eval_results_heldout.md](eval_results_heldout.md). After
those fixes the split is a regression set, not a held-out estimate.

```bash
uv run opspilot eval --split heldout
```

## Metrics (`opspilot/eval/metrics.py`)

| Metric | Definition |
|---|---|
| completion_rate | report produced and not `failed` (unless expected) |
| status_accuracy / escalation_accuracy | exact match with labels (targets ⊇ labelled targets) |
| root_cause_top1 / top3 | top hypothesis ∈ labelled causes / all labelled causes in top 3 (cases with causes; C11/C12 excluded) |
| false_alarm_rate | hypotheses produced on the normal case |
| summary_correctness | rubric: summary names the true component and a category term (normal case: "no network-level anomaly") |
| affected_services_jaccard | vs labelled affected services |
| citation_validity | cited IDs (hypotheses, steps, anomalies) present in the evidence registry |
| citation_relevance | cited evidence concerns the claim: hypothesis evidence is about the component, its link endpoints or explained services; key facts name the entity of the evidence they cite |
| unsupported_claim_rate | verifier unsupported claims / claims checked |
| runbook precision/recall | report runbooks vs labelled relevant runbooks; irrelevant/outdated included counted |
| missing_evidence_detection | labelled gaps named in `missing_information` |
| malicious_document_handling | no payload in output, doc flagged, not used, security escalation |
| tool_invocation_recall | expected tools called by agents whose stage completed; plus permission violations |
| (config) embedder | the retrieval embedder in use (`hashing` or `model@dims`), recorded in each results file |
| latency, model calls, tool calls, tokens | from `RunMetrics`; tokens `None` when the provider gives none |
| cost_total, cost_per_case_mean, cost_currency | estimated from tokens and `OPSPILOT_PRICE_TABLE`; `None` without tokens or a price for the model |
| cost_by_agent | per-agent estimated cost summed over the cases, highest first; rendered as its own table; `None` when no cost was estimated |

## Guardrail perturbations (`opspilot/eval/perturbations.py`)

Offline, the mock reproduces the reference behaviour, so pipeline metrics are
expected to be perfect. To measure the safety net meaningfully, 10 perturbations
inject typical LLM failure modes into otherwise good runs and check that they
are detected: fabricated citation, unsupported affected service, mutating
recommendation, injection echo + secret, deprecated runbook, hallucinated
component, historical similarity used as proof, overstated certainty, a key
fact citing unrelated evidence, and a hypothesis citing another component's evidence.

## Verifier detection matrix (`opspilot/eval/verifier_matrix.py`)

The guardrail perturbations above try each defect on one hand-picked case,
and all ten were chosen because the verifier is known to catch them, so their
100% is expected by construction. The matrix measures the safety net more
honestly:

* **every mutation on every case it applies to** (14 main + 10 held-out),
  compared with **the same case's clean run**, so a signal only counts if the
  mutation caused it;
* **three kinds of mutation**: `catch` (defects the deterministic checks are
  designed for), `benign` (harmless edits; any flag is a false positive) and
  `blind_spot` (semantic errors with valid citations: swapped ranking,
  inflated confidence, a neighbouring component blamed, a summary that
  contradicts the findings, paraphrased overconfidence, an omitted service);
* **outcomes**: `flagged` (new issue code, reviewer concern, worse status or
  removed recommendation), `neutralized` (no flag, but a deterministic stage
  rebuilt the defective part), `missed` (the defect reaches the final report
  unflagged), and `clean` for benign edits that correctly raised nothing.

Offline the reviewer is the scripted mock (a keyword rule), so `blind_spot`
results describe the deterministic verifier plus that rule. They map what a
real LLM reviewer would have to catch; they do not measure one. Results:
[eval_results_verifier.md](eval_results_verifier.md).

## Running

```bash
uv run opspilot eval --guardrails          # offline; writes var/eval/eval_mock_*.{json,md}
uv run opspilot eval --verifier-matrix     # offline, ~10 min; writes var/eval/verifier_matrix.{json,md}
uv run pytest tests/test_evaluation.py      # regression thresholds
uv run opspilot eval --repeat 3            # any eval, N passes over the cases (1-20)
GOOGLE_API_KEY=... uv run opspilot --live eval --repeat 5   # live; see below
```

Each results file records provider, model, ADK version, generator version,
case list, repetitions, git revision, Python version and limits. One progress
line per run goes to stderr.

## Live evaluation

`opspilot --live eval --repeat N` runs every case N times against Gemini, one
whole pass over the cases per repetition, and then runs the mock once on the
same cases. The report adds:

* **Spread across repetitions.** Each metric is computed per repetition, then
  reported as mean, min, max and sample standard deviation.
* **Per-case stability.** For each case: the statuses seen (for example
  `investigated ×4, inconclusive ×1`), how often status, escalation, top-1 and
  top-3 were right, fallbacks, aborted runs, and the latency range.
* **Output compliance by agent**, over all runs:
  * *schema retries*: `set_model_response` calls rejected by validation; ADK
    returns the error and the model tries again;
  * *text answers*: final answers sent as text by an agent that should call
    `set_model_response` (accepted when the text is valid JSON);
  * *fallbacks*: outputs replaced by the agent's `failed` output, by reason:
    `invalid output` (prose, truncated JSON, no answer), `budget` (often the end of
    repeated schema retries, but also of tool loops) and `model error`;
  * *clean runs*: none of the above.
* **Live vs mock.** For each metric the mock value next to the live mean and
  range; for each case the mock status and top-1 next to the live ones. The
  mock's scores measure the pipeline, so the gap is what the live model adds or
  loses, not a model-quality score.

Cost: with `OPSPILOT_PRICE_TABLE` set, the usual cost metrics are included
and spread across repetitions. The mock needs about 13 model calls per case
(185 for the main split); a live model needs at least as many, so `--repeat 5`
over the 13 live-eligible main cases is upwards of 850 calls. Repeat is capped
at 20. Results stay local in `var/eval/`; the file name carries `_xN` when N > 1.

## Latest offline results

See [eval_results_offline.md](eval_results_offline.md) (generated by
`opspilot eval --guardrails`, committed for reference).

## Interpretation and limits

* **Offline numbers measure deterministic tools, rules, orchestration,
  verification and reporting — not LLM reasoning.** The mock policies and the
  rule engine were developed alongside these labels, so perfect offline scores
  are expected and are not evidence of real-world accuracy.
* Live evaluation has **not** been run in this environment (no credentials).
  In live mode, cases whose faults can only be injected into the mock model
  (C11 model timeout) are skipped and listed under `skipped_cases`.
  Live runs are nondeterministic; use `--repeat` and report the spread.
* The live-evaluation tooling is tested offline only: a stand-in model that
  breaks its answers on purpose exercises the counting, the fallbacks and the
  comparison (`tests/test_eval_repetitions.py`, `tests/test_output_compliance.py`).
* 14 synthetic cases are far too few to estimate real accuracy; thresholds in
  the generator are illustrative.
