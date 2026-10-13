# ADR-0011: Optional Gemini embeddings behind a persistent cache

**Status:** Accepted. Extends [ADR-0005](0005-hybrid-retrieval.md); the default is unchanged.

The hashing vector in ADR-0005 only sees lexical overlap, so "dropped frames"
cannot find a runbook about "packet loss". The `Embedder` protocol already
allowed a semantic embedder; this adds one.

**Decision:** `OPSPILOT_EMBEDDER=gemini` replaces the vector side of knowledge
search with Gemini embeddings (`OPSPILOT_EMBEDDING_MODEL`, default
`gemini-embedding-001`, `OPSPILOT_EMBEDDING_DIMS`, default 768). BM25 and RRF
fusion are untouched. `hashing` stays the default, so offline runs, tests and CI
need nothing.

* **Persistent cache.** Vectors are stored in SQLite (`OPSPILOT_EMBEDDING_CACHE`,
  default `var/embeddings.db`) keyed by a hash of (model@dims, document or query,
  text). The text itself is not stored. Documents use the `RETRIEVAL_DOCUMENT` task
  type and queries `RETRIEVAL_QUERY`, cached separately. A cached corpus and
  repeated queries need no credentials or network, which also makes repeated
  evaluation runs cheaper and reproducible.
* **Degrades, never fails.** If Gemini cannot be reached and the cache cannot
  answer, the embedder raises `EmbeddingUnavailable` and retrieval falls back to
  keyword scoring with a logged warning (for the whole corpus at index time, for
  one query at query time). An index built without vectors stays keyword-only
  until the process restarts.
* **Layering.** The cache and the protocol live in `core/` (no ADK, no Google
  SDK); the Gemini client lives in `adk/embeddings.py`. The client is created only
  when a request must go out. Reading a document never builds an index or embeds.
* **Egress.** Document and query text is sent to the Gemini API, with secrets
  redacted first. The default configuration sends nothing.
* **Cutoff.** Cosine scales differ by embedder, so the vector cutoff is per
  embedder: 0.2 for hashing, 0.6 for Gemini (`OPSPILOT_EMBEDDING_MIN_COSINE`
  overrides). A cutoff that is too high only loses the vector signal; too low adds
  noise to the fused ranking.

**Not verified:** nothing here has run against the live API (no credentials in
the build environment). The model name, the 32-text batch size, the supported
dimensions and the 0.6 cutoff are starting points; run `opspilot --live eval` with
the option on and off before relying on it. The cache is not invalidated when the
model changes behind the same name; delete `var/embeddings.db` to rebuild.
