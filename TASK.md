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

## Medium priority
- [x] Optional bounded re-investigation loop (`LoopAgent`, max 2) — retries failed stages; off by default (`OPSPILOT_MAX_INVESTIGATION_ROUNDS=2`)
- [ ] Semantic embedder option (Gemini embeddings) with an offline cache
- [ ] Cost estimation from recorded tokens with configurable price table

## Low priority
- [ ] OpenTelemetry exporter configuration example
- [ ] Authentication in front of `adk api_server` for any shared use
