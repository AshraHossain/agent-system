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

## Docker / Deployment

```bash
# Build
docker build -t trafficflow .

# Run (synthetic feed by default; set TRAFFICFLOW_MODEL for a live LLM)
docker run -p 7860:7860 -e TRAFFICFLOW_MODEL=gemini-2.0-flash trafficflow
```

The dashboard is served at `http://localhost:7860`. To use a real CSV feed, mount it and pass
`--csv`: `docker run -p 7860:7860 -v /path/to/feed.csv:/data/feed.csv trafficflow uv run trafficflow serve --host 0.0.0.0 --csv /data/feed.csv`.

## Real data

```bash
uv run trafficflow fetch-data                      # PeMSD7 -> var/data (SHA-256 checked, not committed)
uv run trafficflow evaluate var/data/PeMSD7_V_228.csv   # model vs baselines, interval coverage
uv run trafficflow serve --replay var/data/PeMSD7_V_228.csv --sensors 8   # dashboard on real recordings
```

**Result on real data (honest summary):** the model beats persistence only at 60 minutes, is
worse at 15 and ties at 30, and its "90%" interval covers only ~59-66%. Details and caveats:
[docs/eval_results_pems.md](docs/eval_results_pems.md).

## Using a real CSV feed

Point the dashboard at any CSV that has a `timestamp` column (ISO-8601 or
parseable by pandas) and one `speed_kmh` column per segment (column names
become segment IDs).  An optional `free_flow_kmh` column sets the free-flow
speed; without it, the 95th-percentile of the first 7 days is used.

```bash
# One-shot: dashboard reads the file once at startup
uv run trafficflow serve --csv data/my_feed.csv

# Polling: re-read the file every 30 s so new rows appended by an upstream
# process are picked up on the next dashboard refresh
uv run trafficflow serve --csv data/my_feed.csv --poll-seconds 30
```

`--csv` and `--replay` are mutually exclusive; omit both for the simulated feed.

## Live model validation

To run the full agent pipeline with a real LLM instead of the offline mock:

```bash
# 1. Set the model string (pydantic-ai format) and the provider's API key.
#    Supported providers: google-gla (Gemini), openai, anthropic
export TRAFFICFLOW_MODEL=google-gla:gemini-2.0-flash
export GOOGLE_API_KEY=<your-key>          # or OPENAI_API_KEY / ANTHROPIC_API_KEY

# Other supported model strings:
#   openai:gpt-4o
#   anthropic:claude-3-5-sonnet-latest

# 2. Run the validate command against the simulated feed.
uv run trafficflow validate --segment S1 --horizon 60
```

The command prints the full `TrafficReport` as JSON to stdout. Any numeric-claim
warnings (numbers in the brief that don't match the recorded evidence) are written to
stderr. Exit code is 0 on success, 1 when the brief verifier finds >50% unsupported
numeric claims or when no model is configured.

`--model` overrides the env var for one run:
```bash
uv run trafficflow validate --model openai:gpt-4o --segment S2 --horizon 30
```

> **Note:** Live-model output is not asserted in the test suite (outputs vary).
> The mock model (`TRAFFICFLOW_MODEL` unset or `"mock"`) is the default for CI.

## Live traffic API adapter

`trafficflow/data/live.py` provides a `LiveFeed` base class that satisfies the `Feed`
protocol. Subclass it and override `history()` to connect a real provider:

```python
from trafficflow.data.live import LiveFeed
from trafficflow.contracts import SegmentInfo


class MyFeed(LiveFeed):
    def history(self, segment_id: str, days: float = 7):
        raw = my_client.get_speed_history(segment_id, days=days)
        return pd.Series(raw["speed_kmh"], index=pd.to_datetime(raw["ts"]))


feed = MyFeed(api_key="...", segments=[SegmentInfo(...)])
```

A `HereLiveFeed` stub is included as a starting point for the HERE Traffic API.
Possible backends: **HERE Traffic**, **TomTom Traffic**, **Google Maps Traffic**.
The `TRAFFICFLOW_MODEL` env var still controls which AI model the agents use.

## Honest scope

- **There is no live feed wired by default.** The default is a simulated network
  (`SyntheticFeed`). Real recordings can be replayed (`ReplayFeed`, PeMSD7) or loaded from
  CSV (`CsvFeed`); both only advance a clock over a recording. Use `LiveFeed` subclasses
  to connect a real API (see above).
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
