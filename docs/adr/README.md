# Architecture Decision Records

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-google-adk.md) | Google ADK 2.11.0 as the agent framework | Accepted |
| [0002](0002-fixed-workflow-tree.md) | Fixed Sequential/Parallel workflow tree instead of LLM-driven delegation | Accepted |
| [0003](0003-evidence-in-session-state.md) | Evidence registry as content-addressed keys in session state | Accepted |
| [0004](0004-sqlite-database-session-service.md) | `DatabaseSessionService` on SQLite for persistence | Accepted |
| [0005](0005-hybrid-retrieval.md) | BM25 + local hashing-vector retrieval fused with RRF | Accepted |
| [0006](0006-categorical-confidence.md) | Categorical, rule-derived confidence; no numeric scores | Accepted |
| [0007](0007-replace-langgraph-scaffold.md) | Replace the LangGraph/OpenRouter scaffold | Accepted |
| [0008](0008-no-custom-fastapi.md) | Use ADK serving (`adk web`/`api_server`) instead of a custom FastAPI app | Accepted |
| [0009](0009-deterministic-mock-model.md) | Scripted `BaseLlm` mock for offline tests and evaluation | Accepted |
