# Ledger Hound

An agentic QA system for a **Purchase-to-Pay (P2P) API**. It explores the
API, attacks its financial controls, and writes a judge report saying which
controls held and which were breached, with the evidence.

It comes with a **practice P2P API** that has 13 switchable, realistic bugs,
so the agent can be scored: *it catches 13/13 planted bugs, with 0 false
alarms on the clean API.*

> Practice project for an agentic-QA interview challenge. Not production software.

## Quick start

```bash
uv sync
uv run pytest                                  # 54 offline tests, no API key needed
uv run ledger-hound attacks                    # list attacks and plantable bugs
uv run ledger-hound run --local-bugs dup_case  # explore + attack + judge (in-process API)
uv run ledger-hound score                      # detection rate across all planted bugs
uv run ledger-hound stress --local-bugs silent_partial   # 50 randomized POs -> failure rate
```

Against a real server, which is the interview setup:

```bash
uv run ledger-hound api --bugs overpay_cumulative,race_approve --port 8000   # terminal 1
uv run ledger-hound run --base-url http://localhost:8000                     # terminal 2
```

Reports go to `var/reports/report-<timestamp>.json` (plus `latest.json`). The
exit code is `1` when any control is breached or the happy path fails, so a
CI job or a scheduler can alert on it.

## Live LLM mode

Copy `.env.example` to `.env` and set `BYNARA_API_KEY`. Any OpenAI-compatible
endpoint works; the default model is `openai/laguna-s-2.1`.

```bash
uv run ledger-hound run --base-url http://localhost:8000 --agent-explore --improvise 3
```

* Without a key, or with `--mock`, the deterministic mock brain is used.
* `--agent-explore`: the LLM drives the happy path with tools, and the oracle grades the end state.
* `--improvise N`: the LLM invents N new probes. Its findings are labelled *unverified*.

## How it works

```
discover ─► plan ─► explore ─► attack ─► explain ─► judge report
openapi     LLM     scripted   16 lib     LLM        rules rollup
+fallback   ranks   or agent   probes     impact     + summary
```

| Module | Job |
| --- | --- |
| `p2p_api/app.py`, `bugs.py` | Practice system under test, with switchable defects |
| `qa/discovery.py` | Maps operations to roles; renames fields to match the API's schema |
| `qa/workflow.py` | Business verbs (create PO, receive, invoice...) over discovered roles |
| `qa/oracle.py` | Decimal money checks: GL balance, sub-cent drift, partial flags |
| `qa/explorer.py` | Happy-path walk with a business check at every step |
| `qa/adversary.py` | 16 attacks: the 6 brief rules plus 10 extra edge cases |
| `qa/brain.py` | LLM jobs (plan, annotate, explain, summarize) with a mock fallback |
| `qa/agentic.py` | Tool-using agents (explorer, improviser) with a guarded `call_api` tool |
| `qa/judge.py` | Runs the whole investigation and builds the report |
| `qa/score.py` | Detection rate against the planted bugs |
| `qa/stress.py` | Seeded random POs with an independent Decimal oracle; failure rate per rule and variant |
| `qa/labels.py` | Labelled dataset for the classifier, built from the planted bugs (no LLM) |
| `qa/dspy_opt.py` | DSPy violation classifier: optimize, evaluate, second opinion (optional extra) |

## Stress test

`ledger-hound stress [-n 50] [--seed 7] [--base-url URL | --local-bugs ...]`
generates randomized POs: 1-3 lines, float-trap prices, and full, partial or
zero receipts. Each gets an invoice variant: `exact`, `under`, `over_qty`,
`over_cent` or `price_up_qty_down`. For every scenario, Python first computes
what a correct AP system must do. The scenario is then pushed through
create → match → approve, and every gap is a violation. The output is the
failure rate overall, per rule and per variant, written to `var/stress/`. The
same seed always gives the same scenarios.

| Practice API | Failure rate (50 scenarios, seed 7) |
| --- | --- |
| clean | 0% |
| `overpay_ordered` | 36% |
| `float_money` | 30% |
| `no_price_check` / `unmatched_approve` | 22% |
| `silent_partial` | 18% |
| `dup_case`, `race_approve`, ... | 0%: random volume can't find these; the targeted attacks do |

## DSPy prompt optimization

This is the third stretch goal: optimizing the prompt that decides whether
an API response is a financial-integrity violation. It's an optional extra:

```bash
uv sync --extra dspy
uv run ledger-hound dspy dataset                          # 72 labelled rows, no LLM needed
uv run ledger-hound dspy optimize --optimizer bootstrap   # needs BYNARA_API_KEY
uv run ledger-hound run --classifier var/dspy/classifier.json   # second opinion in the report
```

How it works:

* **Labels.** The labels come free from the planted bugs: every probe on the
  clean API and on each bugged API becomes an example (rule, attempt, HTTP
  transcript). It is labelled with the oracle's verified verdict, and the
  oracle's own conclusions never appear in the model's inputs.
* **Optimizers.** `labeled` (k demos, no compile calls), `bootstrap`
  (BootstrapFewShot keeps the model's own correct reasoning as demos) or
  `mipro` (MIPROv2, which also rewrites the instructions). Each prints
  baseline vs optimized accuracy, precision and recall on a held-out dev
  set, then saves `classifier.json` and a readable `prompt.md`.
* **Second opinion.** `run --classifier` adds the classifier's verdict to
  every probe, plus an agreement score against the oracle. The classifier
  is meant for probes with no oracle, such as `--improvise` findings.

**Design rule:** the LLM never decides pass/fail. Verdicts come from
arithmetic on data re-read from the API. The model plans, explores, invents
probes and explains.
