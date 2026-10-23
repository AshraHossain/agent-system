# TASK.md — backlog

## Done
- [x] Contracts, simulator, seasonal forecaster, anomaly detection, rule-based status, backtest
- [x] Read-only tools, PydanticAI agents (Forecast, Incident, Brief Writer, Ask), mock models
- [x] Citation verifier; fixed parallel pipeline
- [x] Gradio + Plotly dashboard, CLI, CI, offline tests
- [x] Docs: PLAN, ARCHITECTURE, backlog, ADRs
- [x] `ReplayFeed` + `fetch-data` for PeMSD7 (public, SHA-256 pinned); dashboard/demo `--replay`
- [x] Non-circular evaluation on real data with persistence / seasonal-naive / historical-average
      baselines and interval coverage ([results](docs/eval_results_pems.md))
- [x] Interval calibration: train-half profile in `_empirical_quantiles` (coverage ~61% → ~86%)
- [x] Train/test split: `evaluate --split` reports held-out origins only; CI benchmark uses it
- [x] Incident floor: `max(min_drop_kmh, min_drop_frac * free_flow_kmh)` in `detect()`
- [x] Verifier checks numbers in findings, summary and recommendations against evidence
- [x] Multiple forecasters (seasonal-residual, moving average, persistence; ETS optional) with
      rule-based selection: `core/selection.py` backtests candidates on the segment's own past
      (lowest MAE wins); `compare_models` tool exposes it to the Forecast and Ask agents, which
      only explain the choice
- [x] Labels-hidden agent evaluation (`run-eval`, `eval/cases.yaml`) plus a test that records
      every agent message during an eval run and asserts no label appears
- [x] `serve --csv` with `--poll-seconds`; live feed adapters (mock / TomTom / HERE) with a poller
- [x] Mock writer dedupes repeated findings and recommendations
- [x] Held-out (`--split`) results for 38 and all 228 sensors recorded in the results doc
- [x] PEMS08 replay (`fetch-data --dataset pems08`, weekends included) and a working
      `evaluate --period weekday|weekend|all`; weekend results recorded
- [x] Short horizon: blend toward the latest reading (`LAST_WEIGHT=0.9`, tuned on the train
      half); held-out 15-min MAE 4.69 vs persistence 4.92 (was 5.87)
- [x] Interval: leave-one-day-out conformal from the same time of day (±2 h, tail 0.05, both
      chosen on the train half); held-out coverage ~90% in peak and off-peak, 21-35 km/h wide
- [x] Stale segments rule out NORMAL status; reports list `stale_segments`
- [x] Timezone-aware feed option; history-gap warnings; `get_current_conditions` reports
      `age_minutes` and `stale` (> 15 min, `core/freshness.py`)
- [x] Browser smoke test in CI drives the dashboard (briefing, Ask, History), not just loads it
- [x] Ask tab keeps the last exchanges as context
- [x] Dockerfile(s), Compose file, deployment note in README; coverage floor (70%) in CI
- [x] Briefing history persisted in SQLite (`serve --reports var/reports.db`)
- [x] Map tab (fixed demo coordinates for the simulated segments)
- [x] Sub-project linked from the root README and `CLAUDE.md`

## Needs the owner
- [ ] **Live-model validation** (many repetitions): schema-valid rate, citation pass rate.
      Needs an API key; run `TRAFFICFLOW_MODEL=<provider:model> uv run trafficflow validate`
- [ ] Verify PeMS data-use terms (PeMSD7 and PEMS08) before any redistribution, and confirm
      the mph / reconstructed-timestamp assumptions in
      [ADR-0004](docs/adr/0004-pemsd7-data-assumptions.md) and
      [ADR-0005](docs/adr/0005-pems08-data-assumptions.md)
- [ ] Terms/key review before enabling the TomTom or HERE adapters against the real APIs

## Open
- [ ] Interval is calibrated but still wide (21-35 km/h); narrowing it further needs a
      better point model (errors are heavy-tailed)
- [ ] Weekends: model only ties persistence on PEMS08 weekends (60 min 2.81 vs 2.80; slightly
      worse at 15/30 min). Candidates: a longer history so the weekend profile has more days,
      or a weekend-specific blend weight (tune on PEMS08's first half, report the second)
- [ ] Record the PEMS08 weekday comparison (`evaluate var/data/pems08.npz --period weekday`)
- [ ] Real coordinates for replayed sensors on the map (neither PeMSD7 nor PEMS08 ships
      station IDs; METR-LA/PEMS-BAY have coordinates but their data is on Google Drive,
      unreachable from the dev container)
