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
uv run pytest                                  # 31 offline tests, no API key needed
uv run ledger-hound attacks                    # list attacks and plantable bugs
uv run ledger-hound run --local-bugs dup_case  # explore + attack + judge (in-process API)
uv run ledger-hound score                      # detection rate across all planted bugs
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

**Design rule:** the LLM never decides pass/fail. Verdicts come from
arithmetic on data re-read from the API. The model plans, explores, invents
probes and explains.
