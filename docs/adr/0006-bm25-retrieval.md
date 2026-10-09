# ADR-0006: BM25 lexical retrieval instead of a vector store

**Context.** The corpus is small and full of identifiers. Retrieval must be
deterministic so that test cases like "irrelevant historical incident" and
"prompt injection in a runbook" are reproducible.

**Decision.** Use an in-process BM25 index (`rank-bm25`, or a small built-in
implementation) over runbooks and past incidents, with metadata filters
(category, entity type). Retrieved text is sanitized: control characters
are stripped, length is capped, and the text is wrapped in delimiters. It is
recorded as `trusted=false` evidence.

**Alternatives.** Chroma/FAISS with embeddings (non-deterministic, model
download); LLM reranking (puts the LLM in evidence selection).

**Consequences.** Recall on paraphrased queries is weaker. The `Retriever`
interface allows swapping in a vector store later.
