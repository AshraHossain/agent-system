# Architecture Decision Records

Format: Context → Decision → Alternatives → Consequences. The longer
reasoning is in [PLAN.md §2](../../PLAN.md#2-key-decisions-with-reasons-and-alternatives).

| ADR | Title | Status |
|---|---|---|
| [0001](0001-single-stategraph-deterministic-nodes.md) | One StateGraph, LLM only where language reasoning is needed | Accepted |
| [0002](0002-deterministic-detection.md) | Deterministic detection separated from LLM investigation | Accepted |
| [0003](0003-json-state-evidence-registry.md) | JSON-only state with an immutable evidence registry | Accepted |
| [0004](0004-sqlite-checkpointer-interrupt.md) | SQLite checkpointer and `interrupt()` for human approval | Accepted |
| [0005](0005-ollama-provider-protocol.md) | Ollama default behind a provider protocol, plus a heuristic baseline | Accepted |
| [0006](0006-bm25-retrieval.md) | BM25 lexical retrieval instead of a vector store | Accepted |
| [0007](0007-ordinal-confidence.md) | Ordinal, rule-based confidence | Accepted |
