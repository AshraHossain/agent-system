# TASK.md — backlog

## Done
- [x] Contracts, simulator, seasonal forecaster, anomaly detection, rule-based status, backtest
- [x] Read-only tools, PydanticAI agents (Forecast, Incident, Brief Writer, Ask), mock models
- [x] Citation verifier; fixed parallel pipeline
- [x] Gradio + Plotly dashboard, CLI, CI, 21 offline tests
- [x] Docs: PLAN, ARCHITECTURE, backlog, ADRs
- [x] `ReplayFeed` + `fetch-data` for PeMSD7 (public, SHA-256 pinned); dashboard/demo `--replay`
- [x] Non-circular evaluation on real data with persistence / seasonal-naive / historical-average
      baselines and interval coverage ([results](docs/eval_results_pems.md))

## High priority
- [ ] **Calibrate the interval**: measured coverage is ~59-66% vs the nominal 90%. Calibrate
      per-horizon from empirical residual quantiles of the training history, re-measure
- [ ] **Short-horizon forecast**: the model loses to persistence at 15 min (MAE 5.86 vs 4.86).
      Improve it with a train/test split (tune on early days, report on later days only)
- [ ] **Incident false alarms on real data**: a robust-z test alone flags small absolute drops
      on quiet sensors (e.g. P032 at 88.7 km/h flagged as a "sustained slowdown"). Add a
      practical-significance floor (minimum drop as a fraction of free flow) and re-check
- [ ] Verifier: check that numbers quoted in the brief match the cited evidence
- [ ] Run a live-model validation (many repetitions): schema-valid rate, citation pass rate.
      Needs an API key from the owner

## Medium priority
- [ ] More than one forecaster (e.g. an AR / gradient-boosted model on lagged speeds), with a `compare_models` tool that backtests candidates per
      segment; the best is chosen by rule and the agent only explains it
- [ ] Agent evaluation set with labelled cases, plus a test that agents never see the labels
- [ ] `serve` option for `CsvFeed`; a true live poller (replay and simulation only advance a clock)
- [ ] Mock writer: dedupe repeated recommendations for the same segment (seen on replay data)
- [ ] Evaluate on all 228 sensors, other periods, and weekends (data is weekday-only)
- [ ] Verify PeMS data terms before any redistribution; confirm the mph / timestamp assumptions
- [ ] Timezone-aware timestamps; handle stale or missing data explicitly
- [ ] Browser smoke test of the dashboard in CI (currently only built, not driven)
- [ ] Memory for the Ask tab across turns

## Low priority
- [ ] Dockerfile and deployment note; coverage floor in CI
- [ ] Persist reports for a history/trend panel
- [ ] Map view (needs segment coordinates)
- [ ] Optional live traffic API adapter behind the `Feed` protocol (needs key and terms review)
- [ ] Link the sub-project from the root README / `CLAUDE.md`
