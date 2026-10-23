# Ledger Hound

An agentic QA system for a **Purchase-to-Pay (P2P) API**. It explores the
API, attacks its financial controls, and writes a judge report saying which
controls held and which were breached, with the evidence.

It comes with a **practice P2P API** that has 26 switchable, realistic bugs,
so the agent can be scored: *it catches 26/26 planted bugs, with 0 false
alarms on the clean API.*

> Practice project for an agentic-QA interview challenge. Not production software.

## Quick start

```bash
uv sync
uv run pytest                                  # 109 offline tests, no API key needed
uv run ledger-hound attacks                    # list attacks and plantable bugs
uv run ledger-hound run --local-bugs dup_case  # explore + attack + judge (in-process API)
uv run ledger-hound score                      # detection rate across all planted bugs
uv run ledger-hound stress --local-bugs silent_partial   # 50 randomized POs -> failure rate
uv run ledger-hound diff old.json new.json     # what changed in the API between two runs
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
openapi     LLM     scripted   28 lib     LLM        15 rules rollup
+fallback   ranks   or agent   probes     impact     + summary

explore walks the full P2P lifecycle, checking the books at every step:
vendor ─► PO ─► submit ─► receive ─► invoice ─► match ─► approve ─► pay
                                                         AP +amt    AP -amt
```

| Module | Job |
| --- | --- |
| `p2p_api/app.py`, `bugs.py` | Practice system under test, with switchable defects |
| `qa/discovery.py` | Maps operations to roles; renames fields to match the API's schema |
| `qa/workflow.py` | Business verbs (create PO, receive, invoice...) over discovered roles |
| `qa/oracle.py` | Decimal money checks: GL balance, sub-cent drift, partial flags |
| `qa/explorer.py` | Happy-path walk, approve through pay, with a business check at every step |
| `qa/adversary.py` | 28 attacks: the 6 brief rules plus 22 extra edge cases (incl. UoM, price-variance tolerance, backorder re-match, freight/tax injection and payment disbursement) |
| `qa/brain.py` | LLM jobs (plan, annotate, explain, summarize) with a mock fallback |
| `qa/agentic.py` | Tool-using agents (explorer, improviser) with a guarded `call_api` tool |
| `qa/judge.py` | Runs the whole investigation and builds the report |
| `qa/score.py` | Detection rate against the planted bugs |
| `qa/stress.py` | Seeded random POs with an independent Decimal oracle; failure rate per rule and variant |
| `qa/spec_diff.py` | API surface (operations, roles, request fields) and its diff against the previous run |
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

`--concurrency K` keeps K scenarios in flight at once. Each scenario has its
own vendor, PO and transcript, so concurrency changes the load, not the
expected verdicts: a failure that appears only at K > 1 points at shared
state or a missing lock. The report adds a `load` block (elapsed time,
scenarios/s, HTTP calls, p50/p95 latency); the practice API gives identical
verdicts at any K.

| Practice API | Failure rate (50 scenarios, seed 7) |
| --- | --- |
| clean | 0% |
| `overpay_ordered` | 36% |
| `float_money` | 30% |
| `no_price_check` / `unmatched_approve` | 22% |
| `silent_partial` | 18% |
| `dup_case`, `race_approve`, `uom_ignored`, ... | 0%: random volume can't find these; the targeted attacks do |

## Unit of measure

Catalogue items carry a unit (`EA`, `SHEET`, `BOX`, `ROLL`) and invoice lines
may declare one. The clean API matches an invoice line only in the PO line's
unit (case and spaces ignored) and refuses fractional quantities of discrete
units. **Unit conversion** is also supported: 50 LB = 1 BOX, so a correctly
converted invoice (100 LB at 1.7998/LB against a 2-BOX PO at 89.99/BOX)
passes match. Three bugs break unit handling: `uom_ignored` (10 LB matched
against 10 EA), `fractional_uom` (2.5 EA accepted), and `wrong_uom_factor`
(the conversion table uses 2× the real factor, letting a 150 LB overbill slip
through both the quantity and price checks). The attacks `invoice_uom_mismatch`,
`fractional_discrete_qty`, and `wrong_uom_conversion_factor` catch them, and
skip on an API whose invoice lines or catalogue carry no unit.

## Backorder re-match

When a partial shipment arrives, the match endpoint flags the invoice as
`matched_with_exceptions` (partial receipt) instead of `matched`. This exception
is an audit trail: it shows the invoice was processed before full delivery. The
`rematch_allowed` bug skips the status check in match, letting a vendor call
match again after the backorder fills — the exception disappears from the final
state with no record that it ever existed. The `rematch_after_backorder` attack
catches it: partial receipt → first match gives `matched_with_exceptions` → fill
the backorder → call match again. Clean API: 409. Bug: silently clears the exception.

## Price variance tolerance

The clean API requires an exact price match between the PO and the invoice (per
the unit of measure, after conversion). Building-materials ERPs sometimes add a
small tolerance to absorb daily commodity-price fluctuations. The
`relaxed_price_tolerance` bug enables a 10% tolerance, letting an invoice
priced up to 10% above the PO slip through match. The `price_tolerance_overbill`
attack catches it: it invoices 9 units at 10% above the PO price (total stays
within the received-goods cap, so only the match step can stop it).

## Freight and tax line injection

The clean API rejects any invoice line whose SKU is not on the referenced PO.
Without this check, a vendor can append a freight surcharge, a setup fee, or a
tax line to a legitimate invoice and have it accepted into the AP ledger — even
though no PO line authorises the spend. The `extra_sku_allowed` bug disables
the SKU-membership check at invoice creation. The `invoice_extra_sku` attack
catches it: it creates a PO for one SKU, then submits a two-line invoice that
also bills a second, unordered SKU. Clean API: 422 (extra SKU rejected). Bug:
201 (invoice accepted with the off-PO line already in the system as a
liability).

## API changes between runs

Every report stores the `api_surface` it tested: each operation, the role
discovery gave it, and the request fields with type and required flag. `run`
compares it with the previous `latest.json` in the same `--out` directory and
writes `api_changes`, listing breaking changes first (removed operation or
field, lost role, changed type, newly required field). So a new breach can be
read next to the endpoint change that came with it. `ledger-hound diff A B`
compares any two reports or raw OpenAPI files and exits `1` on a breaking
change.

Pass `--explain` to `ledger-hound diff`, or run with a live brain, to get a
plain-English paragraph explaining what changed and the likely impact on
control coverage (e.g. a removed endpoint means a probe will skip; a new
required field changes how attacks are constructed). Falls back to the mock
brain without an API key.

## DSPy prompt optimization

This is the third stretch goal: optimizing the prompt that decides whether
an API response is a financial-integrity violation. It's an optional extra:

```bash
uv sync --extra dspy
uv run ledger-hound dspy dataset                          # 112 labelled rows, no LLM needed
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
