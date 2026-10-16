# Traffic Flow Analyzer

Congestion forecasting with PydanticAI agents and an interactive Gradio + Plotly dashboard.

```
SyntheticFeed / CsvFeed --> core (forecast, anomaly, congestion, status)   no AI imports
                               ^
                  read-only tools (validated, evidence-recording)
                               ^
   Forecast Analyst \   (parallel PydanticAI agents)
   Incident Analyst /-> Brief Writer -> verifier (citations) -> TrafficReport (status by rule)
                                                              -> Gradio dashboard
```

## Quick start

```bash
cd traffic-flow-analyzer
uv sync
uv run pytest
uv run trafficflow serve        # http://127.0.0.1:7860
```

Dashboard: speed history with forecast and 90% interval, a segment x time congestion heatmap
(observed + forecast), a status table, a "Live" mode that advances simulated time, an AI
briefing tab, and an Ask tab.

## Real data

```bash
uv run trafficflow fetch-data                      # PeMSD7 -> var/data (SHA-256 checked, not committed)
uv run trafficflow evaluate var/data/PeMSD7_V_228.csv   # model vs baselines, interval coverage
uv run trafficflow serve --replay var/data/PeMSD7_V_228.csv --sensors 8   # dashboard on real recordings
```

**Result on real data (honest summary):** the model beats persistence only at 60 minutes, is
worse at 15 and ties at 30, and its "90%" interval covers only ~59-66%. Details and caveats:
[docs/eval_results_pems.md](docs/eval_results_pems.md).

## Honest scope

- **There is no live feed.** The default is a simulated network (`SyntheticFeed`). Real
  recordings can be replayed (`ReplayFeed`, PeMSD7) or loaded from CSV (`CsvFeed`:
  `timestamp` + one speed column per segment, 5-minute grid); both only advance a clock over
  a recording. A live API adapter would implement the `Feed` protocol (not included).
- PeMSD7 timestamps and units are reconstructed (the file has neither); see the evaluation doc.
- The forecaster is an interpretable seasonal-median + decaying-residual model. On the
  simulated data it beats persistence (`trafficflow backtest`), but that is partly circular
  (see the real-data result above, which is more modest).
- The incident detector can flag small absolute drops on quiet real sensors as "incidents".
- Offline runs use deterministic mock models, not an LLM. For a real model set
  `TRAFFICFLOW_MODEL` (e.g. `google-gla:<model>`) plus that provider's API key; live output is
  untested here.
- Read-only decision support; not production-ready.

## Docs

[PLAN.md](PLAN.md) (why) · [ARCHITECTURE.md](ARCHITECTURE.md) (how) · [TASK.md](TASK.md) (backlog) · [docs/adr/](docs/adr/README.md)
