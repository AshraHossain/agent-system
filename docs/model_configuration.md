# Model configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPSPILOT_MODEL_PROVIDER` | `mock` | `mock` (deterministic `ScriptedLlm`) or `gemini` |
| `OPSPILOT_MODEL` | `gemini-2.5-flash` | Gemini model ID passed to `LlmAgent.model` |
| `OPSPILOT_MODEL_TIMEOUT_S` | 60 | per-request HTTP timeout |
| `OPSPILOT_MAX_MODEL_ATTEMPTS` | 2 | `HttpRetryOptions.attempts` (429/500/503/504 only) |

CLI shortcut: `opspilot --live ...` sets the provider to `gemini`.

## Live Gemini authentication (google-genai, used by ADK)

**Gemini Developer API** — create a key in Google AI Studio, then:

```bash
export GOOGLE_API_KEY=...        # never commit; .env is git-ignored
```

**Vertex AI** — with a GCP project that has Vertex AI enabled:

```bash
gcloud auth application-default login
export GOOGLE_GENAI_USE_VERTEXAI=1
export GOOGLE_CLOUD_PROJECT=my-project
export GOOGLE_CLOUD_LOCATION=us-central1
```

Model availability differs by region/account; check the model ID before use.

## Mock model

`ScriptedLlm` (`opspilot/adk/models.py`) subclasses ADK `BaseLlm`. Each agent
gets a policy (`opspilot/adk/mock_policies.py`) that reads the same rendered
instruction and tool results a real model receives and emits function calls or
the final `set_model_response`. It supports injected `timeout` and `quota`
faults. It returns **no token usage**, and metrics report tokens as `None`
rather than inventing numbers.

## Metrics captured

`RunMetrics` in every report: provider, model, model calls (total and per
agent), tool calls (per agent), wall-clock duration, per-agent model time,
per-tool time, budget events, and — **only when the provider returns
`usage_metadata`** — prompt/completion/total tokens, plus an estimated cost when
a price table is configured (below).

## Cost estimation

Pricing varies by model, tier, region and contract, so **no prices ship in code**.
Point `OPSPILOT_PRICE_TABLE` at a JSON file you maintain
([`prices.example.json`](../prices.example.json) shows the format; its figures
are illustrative — replace them with your contract prices):

```json
{"currency": "USD",
 "models": {"gemini-2.5-flash": {"input_per_mtok": 0.30, "output_per_mtok": 2.50}}}
```

* `estimated_cost` and `cost_currency` appear in `RunMetrics`, in the CLI
  summary (`est. cost=0.0123 USD`) and in the evaluation aggregate
  (`cost_total`, `cost_per_case_mean`, `cost_currency`).
* The calculation is `core/cost.py`: tokens ÷ 1M × the price for
  `OPSPILOT_MODEL`. It is `None` — never a guess — when no table is set, the
  model has no entry (a warning is logged), or the provider reported no usage.
  The mock model reports none, so offline runs always show `n/a`.
* Gemini's total token count includes thinking tokens, which are billed as
  output but are not in the candidate count. Anything in the total beyond
  prompt + candidates is therefore charged at the output rate.
* The estimate covers model tokens only, for one run. It does not model
  context-cache discounts, tiered or batch pricing, or tool/infrastructure
  costs. An invalid or unreadable table fails at start-up.

## Failure handling

| Failure | Behaviour |
|---|---|
| Transient HTTP (429/5xx) | Retried by google-genai up to `max_model_attempts`, with backoff (1–8 s) |
| Timeout / quota after retries / other model error | `on_model_error_callback` returns a schema-valid `status: failed` output; pipeline continues; report becomes `inconclusive` or `failed` |
| Model or iteration budget exhausted | `BudgetPlugin` short-circuits with the same failed output |
| Wall-clock limit | Run cancelled; deterministic fallback report with status `failed` is persisted |

## Known live-mode risks (unverified offline)

* Gemini must call `set_model_response` with schema-conformant JSON; failures
  surface as validation errors → stage failure.
* Live models may cite irrelevant-but-existing evidence; the verifier checks
  existence and kind, not relevance.
* Live behaviour has not been evaluated in this environment (no credentials).
