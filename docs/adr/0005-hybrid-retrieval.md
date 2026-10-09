# ADR-0005: Hybrid retrieval (BM25 + local vector) with RRF

**Status:** Accepted

## Context
Queries mix exact tokens (`CRC`, `BGP`, `leaf-3`, `RB-002`) and paraphrase
("drops" vs "packet loss"). Must run locally, offline, deterministically.

## Decision
In-house BM25 (k1=1.5, b=0.75) + a local vector index (L2-normalised feature
hashing of word unigrams/bigrams and character trigrams, 512 dims, cosine),
fused with Reciprocal Rank Fusion (k=60). The embedder is an interface; a
Gemini embedding implementation can be configured for live runs.

## Consequences
* + Deterministic, dependency-free, testable.
* − The hashing embedder is **not semantic**: it captures lexical and
  morphological overlap only. Documented in docs/tool_contracts.md.
