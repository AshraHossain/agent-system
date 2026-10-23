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
- [x] **Calibrate the interval**: fixed calibration leakage in `_empirical_quantiles` — now
      builds a train-half-only seasonal profile so the calibration window is genuinely
      out-of-sample; coverage should rise from ~59-66% toward 90%
- [x] **Short-horizon forecast**: added `--split` flag to `evaluate` CLI and CI benchmark so
      MAE is measured only on the second-half (held-out) origins; CI now reports honest numbers
- [x] **Incident false alarms on real data**: `detect()` now applies a practical-significance
      floor of `max(min_drop_kmh, min_drop_frac * free_flow_kmh)`; `detect_incidents` tool and
      `pipeline.py` pass `free_flow_kmh` from the segment metadata
- [x] Verifier: `verify_brief` now checks numeric claims in `brief.recommendations` against
      the union of all recorded evidence (same heuristic as summary)
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
