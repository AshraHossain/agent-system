# PLAN.md — Traffic Flow Analyzer

A **read-only decision-support** system that forecasts road congestion from a speed
feed and explains it to an operator. Numbers come from deterministic code; PydanticAI
agents choose which tools to call, interpret the results and write a cited briefing; a
Gradio + Plotly dashboard presents it. It controls nothing on the road network.

Architecture is in [ARCHITECTURE.md](ARCHITECTURE.md); open work is in [TASK.md](TASK.md).

## 1. Why PydanticAI

| Need | What it provides (verified against the pinned 2.56 install) |
|---|---|
| Typed agent outputs | `Agent(output_type=PydanticModel)`; invalid output triggers a model retry (`retries=2`). |
| Tools with injected state | Plain async functions taking `RunContext[TrafficDeps]`; the signature becomes the tool schema (the `horizon_minutes` bounds are tested). |
| Model pluggability | Any provider via a `provider:model` string, or a `FunctionModel` for deterministic offline policies (see ADR-0003). |
| Plain-Python orchestration | Agents are ordinary objects, so a fixed `asyncio.gather` pipeline needs no graph framework. |

PydanticAI handles the LLM loop. It is **not** used for anything computable: forecasts,
thresholds, incident detection and status live in `core/` with no AI imports.

## 2. Why each agent exists

| Agent | Kind | Why |
|---|---|---|
| Forecast Analyst | `Agent` + tools | Decides which segments to inspect and phrases per-segment outlooks from tool values. |
| Incident Analyst | `Agent` + tools | Interprets anomaly scans; reports nothing when scans are clean. |
| Brief Writer | `Agent`, no tools | Language task only: turns the two findings into a briefing. Cannot call data tools, so it can only restate evidence. |
| Ask | `Agent` + tools | Free-form operator questions, answered from tool values. |
| `verify_brief` | deterministic code | Rejects citations to evidence that was never recorded and unknown segment ids. |
| `network_status` | deterministic code | Status (`normal`/`watch`/`degraded`) is computed from forecasts and anomalies, never from LLM text. |

## 3. Orchestration

```
Forecast Analyst ─┐ (asyncio.gather)
Incident Analyst ─┴─► Brief Writer ─► verify_brief ─► network_status ─► TrafficReport
```

Fixed order, no LLM routing (ADR-0002): the two analysts are independent, the writer needs
both, verification needs the writer's output. The dashboard tick never calls a model;
briefing and Q&A are explicit button actions.

## 4. Phases

| # | Phase | Status |
|---|---|---|
| 1 | Contracts, simulator, forecaster, anomaly detection, status rules, backtest | done (PR #14) |
| 2 | Read-only tools, agents, mock models, pipeline, verifier | done (PR #14) |
| 3 | Gradio dashboard, CLI, CI | done (PR #14) |
| 4 | Documentation | done (PR #17) |
| 5 | Real-data replay feed and a non-circular evaluation | done (PR #17); see [eval](docs/eval_results_pems.md) |
| 6 | Interval calibration, better short-horizon forecast, stronger verification, multiple forecasters | next |
| 7 | Agent evaluation set; live-model validation | planned |

## 5. Non-goals and honest limits

* **Not production-ready.**
* **Data are simulated by default.** Real recorded data can be replayed (`ReplayFeed`, PeMSD7)
  and loaded from CSV, but there is no live feed: both only advance a clock over a recording.
* **Simulated results do not transfer.** The simulator and forecaster share a daily seasonal
  assumption, so the simulated backtest is partly circular. On real PeMSD7 data the model
  beats persistence only at 60 minutes, loses at 15 and ties at 30, and its "90%" interval
  covers ~59-66% ([results](docs/eval_results_pems.md)). That evaluation is one dataset, a
  weekday-only 2012 freeway recording, with reconstructed timestamps and assumed units.
* **Offline runs use scripted mock models, not an LLM.** They test tools, orchestration,
  verification and the UI, not language-model reasoning quality. No live model has been run.
* The verifier checks that citations exist, not that the numbers quoted match the evidence.
