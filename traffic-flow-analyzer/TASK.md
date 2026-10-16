# TASK.md — backlog

## Done
- [x] Contracts, simulator, seasonal forecaster, anomaly detection, rule-based status, backtest
- [x] Read-only tools, PydanticAI agents (Forecast, Incident, Brief Writer, Ask), mock models
- [x] Citation verifier; fixed parallel pipeline
- [x] Gradio + Plotly dashboard, CLI, CI, 21 offline tests

## High priority
- [ ] `ReplayFeed` over a public 5-minute speed benchmark (e.g. METR-LA / PEMS-BAY): confirm
      access and licence first; replay through the advancing clock with held-back ground truth
- [ ] Non-circular backtest on that data; add seasonal-naive and historical-average baselines
- [ ] Measure 90% interval coverage; recalibrate if it is far from 90%
- [ ] Verifier: check that numbers quoted in the brief match the cited evidence
- [ ] Run a live-model validation (many repetitions): schema-valid rate, citation pass rate.
      Needs an API key from the owner

## Medium priority
- [ ] More than one forecaster, with a `compare_models` tool that backtests candidates per
      segment; the best is chosen by rule and the agent only explains it
- [ ] Agent evaluation set with labelled cases, plus a test that agents never see the labels
- [ ] `serve --csv` / replay option; make "Live" meaningful for non-synthetic feeds
- [ ] Timezone-aware timestamps; handle stale or missing data explicitly
- [ ] Browser smoke test of the dashboard in CI (currently only built, not driven)
- [ ] Memory for the Ask tab across turns

## Low priority
- [ ] Dockerfile and deployment note; coverage floor in CI
- [ ] Persist reports for a history/trend panel
- [ ] Map view (needs segment coordinates)
- [ ] Optional live traffic API adapter behind the `Feed` protocol (needs key and terms review)
- [ ] Link the sub-project from the root README / `CLAUDE.md`
