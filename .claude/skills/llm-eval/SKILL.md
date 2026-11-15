---
name: llm-eval
description: Runs, extends and reports evaluations of LLM agents, multi-agent workflows and RAG pipelines in this repository, offline on the mock model or live on Gemini through AI Studio or Vertex AI. Use when the user asks to evaluate, benchmark, score, measure or compare an agent, prompt, model, retriever or pipeline, to add a metric or an LLM judge, to run a live evaluation, to estimate cost, or to write up evaluation results.
---

# LLM evaluation

## Decide what is being measured

| Question | Run | What it can show |
|---|---|---|
| Does the pipeline still work? | offline, mock model | plumbing, contracts, verifier, guardrails. Perfect scores are expected and say nothing about a model. |
| How good is the model in this pipeline? | live, repeated | accuracy with spread, schema compliance, cost, latency |
| Does the safety net catch bad output? | planted defects | detection and false-positive rates (`red-team` skill) |

State which of these a number belongs to every time it is reported.

## Commands (OpsPilot)

```bash
uv run opspilot eval --guardrails               # offline -> var/eval/eval_mock_*.{json,md}
uv run opspilot eval --split heldout            # H01-H10 regression set
uv run opspilot eval --verifier-matrix          # offline, about 10 minutes
uv run opspilot --live eval --repeat 5          # live, then the mock once on the same cases
uv run opspilot --live eval --cases C03 C10 --repeat 3
uv run pytest tests/test_evaluation.py          # regression thresholds
```

Live access: `GOOGLE_API_KEY` from Google AI Studio, or Vertex AI with
`gcloud auth application-default login` and `GOOGLE_GENAI_USE_VERTEXAI=1`,
`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`. Model id: `OPSPILOT_MODEL`. Cost needs
`OPSPILOT_PRICE_TABLE` pointing at a price file the user maintains; without it cost is reported
as not available, never guessed. Other projects: read the `eval`/`evaluate`/`score` command in
their README or CLAUDE.md.

## Before a live run

1. Tell the user it spends API quota and roughly how many model calls it makes (cases ×
   repetitions × agents). Start with two or three cases and one repetition.
2. Confirm the model id resolves in the chosen region with one cheap call.
3. Run the same cases on the mock first; a failure there is a pipeline bug, not a model result.

## Rules

- **Repeat live runs** (at least 3, 5 when the result will be quoted) and report mean, min, max
  and standard deviation per metric, plus per-case stability. One run is an anecdote.
- **Never assert a live model's exact output** in a test. Assert schema validity, invariants and
  loose floors.
- **Record provenance** with every result: provider, model id, framework version, dataset and
  generator version, case list, repetitions, git revision, date.
- **Attribute per agent** in a multi-agent run: schema retries, text answers, fallbacks, tokens
  and cost by agent. A pipeline-level score hides which agent failed.
- **Separate retrieval from generation** in RAG. See [references/rag-metrics.md](references/rag-metrics.md).
- **Prefer deterministic metrics.** Use an LLM judge only for what code cannot check, give it a
  rubric with anchored levels, validate it against a hand-labelled sample and report its agreement.
  Never let the system under test be its own judge.
- **Do not tune on the test set.** A fix made after reading held-out failures turns that set into
  a regression set; say so and record the pre-fix baseline (`eval-dataset` skill).
- **A metric that cannot fail is not a metric.** When a score is 100% by construction, say that.

## Adding a metric

Define it in one sentence in the evaluation doc, implement it as a pure function over the final
artefact and the labels (`opspilot/eval/metrics.py`), add a unit test with one passing and one
failing artefact, and add it to the regression thresholds only after its offline value is known.

## Writing up

Lead with the question and the answer. Then: what was run (provenance), the table with spread,
the failures worth reading, what the result does not show, and the cost. Committed results go in
`docs/eval_results_*.md`; raw output stays in `var/eval/` (not committed).
