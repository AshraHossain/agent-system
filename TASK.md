# TASK.md — backlog

## Done (this iteration)
- [x] ADK 2.11.0 multi-agent pipeline with deterministic intake/verifier/finalizer
- [x] Synthetic datasets, 14 labelled cases, deterministic tools, evaluation harness
- [x] Durable SQLite sessions with safe resume; budgets and failure handling
- [x] Security controls and tests; demo CLI; `adk web` app; Docker; CI

## High priority
- [ ] Run live Gemini evaluation (several repetitions), record results, compare with mock
- [ ] Validate Gemini `set_model_response` compliance for every output schema
- [ ] Add relevance checking for cited evidence (existence is checked; relevance is not)

## Medium priority
- [ ] Optional bounded re-investigation loop (`LoopAgent`, max 2) when verification requests evidence
- [ ] Semantic embedder option (Gemini embeddings) with an offline cache
- [ ] Larger, independently authored evaluation set to reduce label/rule co-design bias
- [ ] Cost estimation from recorded tokens with configurable price table

## Low priority
- [ ] OpenTelemetry exporter configuration example
- [ ] Authentication in front of `adk api_server` for any shared use
