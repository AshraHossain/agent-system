# TASK.md — backlog

## Done (this iteration)
- [x] ADK 2.11.0 multi-agent pipeline with deterministic intake/verifier/finalizer
- [x] Synthetic datasets, 14 labelled cases, deterministic tools, evaluation harness
- [x] Durable SQLite sessions with safe resume; budgets and failure handling
- [x] Security controls and tests; demo CLI; `adk web` app; Docker; CI

## High priority
- [ ] Run live Gemini evaluation (several repetitions), record results, compare with mock
- [ ] Validate Gemini `set_model_response` compliance for every output schema
- [x] Citation relevance check (entity alignment); sufficiency still unchecked
- [x] Held-out split with recorded pre-fix baseline (now a regression set)
- [ ] Independently authored evaluation set (different author)
- [x] Verifier detection matrix: planted defects x all cases vs a clean baseline (`opspilot eval --verifier-matrix`, docs/eval_results_verifier.md)
- [ ] Close the matrix's deterministic blind spots: summary/findings agreement, confidence vs rule confidence, ranking vs rule ranking, category/component-type mismatch
- [ ] Replay the matrix's `paraphrased_certainty` (and other blind spots) against the live LLM reviewer

## Medium priority
- [x] Optional bounded re-investigation loop (`LoopAgent`, max 2) — retries failed stages; off by default (`OPSPILOT_MAX_INVESTIGATION_ROUNDS=2`)
- [x] Semantic embedder option (Gemini embeddings) with an offline cache (`OPSPILOT_EMBEDDER=gemini`, ADR-0011); live validation and cutoff tuning still open
- [x] Read-only `GET /runs/{run_id}` served next to ADK's API (`opspilot serve`, ADR-0010)
- [x] Cost estimation from recorded tokens with configurable price table (`OPSPILOT_PRICE_TABLE`), per run and per agent

## Low priority
- [x] OpenTelemetry exporter configuration example (`docs/observability.md`; export via `uv run --with`, not yet in the lockfile)
- [ ] Decide whether to ship the OTLP exporter as an `otel` extra: it forces `protobuf` 7.x -> 6.x across the shared lockfile and Docker image
- [ ] Authentication in front of `adk api_server` for any shared use
