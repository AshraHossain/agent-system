# ARCHITECTURE.md — Traffic Flow Analyzer

## Layering

```
trafficflow/
  contracts/   Pydantic models: segments, forecasts, anomalies, agent outputs, report  (no AI libs)
  core/        simulate, forecast, congestion, anomaly, status, evaluation, backtest,
               selection (rule-based forecaster choice), freshness (stale data)  (no AI libs)
  data/        Feed protocol; SyntheticFeed (deterministic, advancing clock); CsvFeed;
               replay.py: ReplayFeed (recorded speeds, held-back truth) + PeMSD7 fetch/load
  agents/      deps, tools, prompts, agent factories, mock models, pipeline
  ui/          plots.py (pure Plotly builders), app.py (Gradio Blocks)
  reports.py   ReportStore: SQLite history of generated briefings
  cli.py       trafficflow demo | backtest | fetch-data | evaluate | serve
```

Rule: `contracts/` and `core/` never import `pydantic_ai` or `gradio`
(enforced by `tests/test_agents.py`). `agents/` adapts core functions into tools.

## Data flow

```mermaid
graph TD
  FEED["Feed<br/>SyntheticFeed / CsvFeed"] --> CORE["core<br/>forecast · anomaly · congestion"]
  CORE --> TOOLS["tools<br/>validated, read-only, record evidence"]
  TOOLS --> FA["Forecast Analyst"]
  TOOLS --> IA["Incident Analyst"]
  FA --> BW["Brief Writer"]
  IA --> BW
  BW --> VER["verify_brief<br/>citations exist"]
  CORE --> ST["network_status (rule)"]
  VER --> REP["TrafficReport"]
  ST --> REP
  FEED --> PLOTS["ui/plots.py"]
  CORE --> PLOTS
  PLOTS --> UI["Gradio dashboard"]
  REP --> UI
```

The plots and table are built straight from `core/` (no model calls). Only the briefing and
Ask tabs involve agents.

## Forecaster

For each segment, history is the last 7 days at 5-minute resolution:

* **Profile** — median speed per (minute-of-day, weekday/weekend).
* **Forecast(h)** — profile(t+h) + `0.92^h` × recent residual (mean of the last 3 readings).
* **Interval** — ± 1.645 × robust residual sigma (MAD), widening slowly with horizon.
* Needs at least 2 days of history, otherwise the tool returns `{"status": "error"}`.

Congestion index = 1 − speed / free-flow; levels at 0.2 / 0.4 / 0.6 (`core/congestion.py`).
Incidents: robust z ≤ −3.5 for ≥ 3 consecutive readings within the last 2 hours.

## Evaluation

`core/evaluation.py` runs a rolling-origin evaluation: at each origin it forecasts from history
only, then compares with the held-back next values (`feed.next_values`, used nowhere else).
It reports MAE / RMSE / MAPE at the step *h* minutes ahead for the model and three baselines
(persistence, seasonal naive, historical average), split into peak and off-peak, plus the
coverage and width of the 90% interval. `ReplayFeed` counts history in *recorded* points and
skips weekend gaps; the forecaster falls back to the other day-type's profile when a horizon
crosses a gap in weekday-only data. Results: [docs/eval_results_pems.md](docs/eval_results_pems.md).

## Evidence flow

Each tool call records an `EV-nnn` entry in `TrafficDeps.evidence` and returns its id. Agents
must cite those ids; `verify_brief` rejects unknown ids and unknown segment ids. Evidence
ids are sequential per run (`EV-001`, `EV-002`, ...), unlike OpsPilot's content-derived ids.
Both analysts share one `TrafficDeps`; this is safe because `record()` is synchronous and
asyncio runs it without interleaving. Repeating a call records a new entry rather than
reusing an id. The writer never touches tools; it sees only the analysts' outputs.

## Safety properties

* Tools are read-only and take only a validated `segment_id` and a bounded horizon
  (5–180 min). No paths, SQL, URLs or commands.
* Held-back future data (`truth`, `next_values`) is used only by `core/evaluation.py` and
  `core/backtest.py`, never by tools.
* Status and escalation are computed in `core/`, not by an LLM.
* Prompt injection via the Ask tab is limited by the tool surface (read-only), not solved.

## Models

`resolve_model(role)` returns a deterministic `FunctionModel` when the model name is `mock`
(default) and otherwise the string in `TRAFFICFLOW_MODEL` (e.g. `google-gla:<model>`).
No model name is hardcoded.
