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

## Honest scope

- **"Real-time" data is simulated** by default (`SyntheticFeed`, deterministic, advancing
  clock). Real data: `CsvFeed` loads a wide CSV (`timestamp` + one speed column per segment
  on a 5-minute grid); `trafficflow demo --csv file.csv`. A live API adapter would implement
  the `Feed` protocol in `data/feed.py` (not included).
- The forecaster is an interpretable seasonal-median + decaying-residual model. On the
  simulated data it beats a persistence baseline (`trafficflow backtest`); that says nothing
  about real roads.
- Offline runs use deterministic mock models, not an LLM. For a real model set
  `TRAFFICFLOW_MODEL` (e.g. `google-gla:<model>`) plus that provider's API key; live output is
  untested here.
- Read-only decision support; not production-ready.
