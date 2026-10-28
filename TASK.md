# TASK.md — backlog

## Done (this iteration)
- [x] ADK 2.11.0 multi-agent pipeline with deterministic intake/verifier/finalizer
- [x] Synthetic datasets, 14 labelled cases, deterministic tools, evaluation harness
- [x] Durable SQLite sessions with safe resume; budgets and failure handling
- [x] Security controls and tests; demo CLI; `adk web` app; Docker; CI

## High priority
- [ ] Run live Gemini evaluation (several repetitions), record results, compare with mock — tooling ready: `opspilot --live eval --repeat 5`
- [ ] Validate Gemini `set_model_response` compliance for every output schema — measured per agent by the same run
- [x] Live-eval tooling: `--repeat N` with spread and per-case stability, per-agent output compliance (schema retries, text answers, fallbacks), mock baseline on the same cases; an answer that breaks its schema now fails only its stage (it used to abort the run)
- [x] Citation relevance check (entity alignment); sufficiency still unchecked
- [x] Held-out split with recorded pre-fix baseline (now a regression set)
- [ ] Independently authored evaluation set (different author)
- [x] Verifier detection matrix: planted defects x all cases vs a clean baseline (`opspilot eval --verifier-matrix`, docs/eval_results_verifier.md)
- [x] Close the matrix's deterministic blind spots: summary/findings agreement, confidence vs rule confidence, ranking vs rule ranking, category/component-type mismatch (semantic errors caught 37/93 -> 69/93)
- [x] Deterministic backstop for overstated certainty: `overstated_certainty` lexicon with negation handling (error unless the leading hypothesis is `strong`, warning otherwise); matrix 69/93 -> 93/93, still 0/45 false positives; plain assertions remain a known miss
- [ ] Replay the matrix's `paraphrased_certainty` (and other blind spots) against the live LLM reviewer

## Medium priority
- [x] Optional bounded re-investigation loop (`LoopAgent`, max 2) — retries failed stages; off by default (`OPSPILOT_MAX_INVESTIGATION_ROUNDS=2`)
- [x] Semantic embedder option (Gemini embeddings) with an offline cache (`OPSPILOT_EMBEDDER=gemini`, ADR-0011); live validation and cutoff tuning still open
- [x] Read-only `GET /runs/{run_id}` served next to ADK's API (`opspilot serve`, ADR-0010)
- [x] Cost estimation from recorded tokens with configurable price table (`OPSPILOT_PRICE_TABLE`), per run and per agent

## Low priority
- [x] OpenTelemetry exporter configuration example (`docs/observability.md`; export via `uv run --with`, not yet in the lockfile)
- [x] Decide whether to ship the OTLP exporter as an `otel` extra: not until `google-adk` allows `opentelemetry-sdk>=1.43` (ADR-0012)
- [x] Authentication for shared use: `opspilot serve` bearer tokens (`OPSPILOT_API_TOKEN` and/or digests in `OPSPILOT_API_TOKEN_SHA256`, ADR-0013), refuses non-loopback without one; plain `adk api_server` stays loopback-only
- [ ] Per-user identity (OIDC/JWT), roles, rate limiting and TLS options for the shared API; `adk web`/`adk api_server` and the Docker `CMD` remain unauthenticated

## PlateWatch backlog
- [x] Detect/read/tag/store pipeline, vector search, PydanticAI agent, offline demo + tests (#15)
- [x] Live Hugging Face test for user-supplied labelled photos (#16) — **never run against real models**
- [x] Per-image timeout and per-detection exception isolation; sightings count in search results
- [ ] Run the live test; tune model ids and the 80%/50% floors from real output
- [ ] Region-specific plate format rules (US states, EU) replacing the generic shape check
- [ ] Learned embeddings and an ANN index (Qdrant/pgvector) beyond ~10^5 plates
- [ ] Read-only `GET /plates/search` API; detection/OCR evaluation harness on a labelled set
