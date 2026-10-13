"""Gemini embeddings for knowledge retrieval, behind a persistent cache (ADR-0011).

`OPSPILOT_EMBEDDER=gemini` swaps the hashing vector for Gemini embeddings. Vectors
are cached on disk, so a cached corpus and repeated queries need no credentials or
network. When Gemini cannot be reached (and the cache cannot answer), retrieval
falls back to keyword scoring; it never fails the investigation.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

import numpy as np

from opspilot.config import EmbeddingConfig
from opspilot.core.embedding_cache import CachedEmbedder, EmbeddingCache
from opspilot.core.retrieval import Embedder, EmbeddingUnavailable
from opspilot.core.security import redact

BATCH = 32
# Gemini cosines sit well above the hashing vector's. Starting point, not tuned:
# too high only loses the vector signal, too low adds noise to the fused ranking.
GEMINI_MIN_COSINE = 0.6


class GeminiEmbedder:
    def __init__(self, model: str, dims: int, timeout_s: float = 60.0, client=None):
        self.model, self.dims, self.timeout_s = model, dims, timeout_s
        self.name = f"{model}@{dims}"
        self._client = client

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts, "RETRIEVAL_QUERY")

    def _models(self):
        if self._client is None:
            from google import genai

            try:
                self._client = genai.Client()  # credentials come from the environment
            except Exception as e:
                raise EmbeddingUnavailable(f"no Gemini client ({type(e).__name__})") from e
        return self._client.models

    def _embed(self, texts: Sequence[str], task_type: str) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dims), dtype=np.float32)
        from google.genai import types

        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=self.dims,
            http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)),
        )
        rows: list[Sequence[float]] = []
        for start in range(0, len(texts), BATCH):
            batch = [redact(t)[0] for t in texts[start : start + BATCH]]
            try:
                response = self._models().embed_content(
                    model=self.model, contents=batch, config=config
                )
            except EmbeddingUnavailable:
                raise
            except Exception as e:
                raise EmbeddingUnavailable(
                    f"{self.model} embedding request failed ({type(e).__name__})"
                ) from e
            vectors = [item.values for item in response.embeddings or []]
            if len(vectors) != len(batch) or any(v is None or len(v) != self.dims for v in vectors):
                raise EmbeddingUnavailable(f"{self.model} returned an unexpected response")
            rows.extend(vectors)
        mat = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return mat / np.where(norms > 0, norms, 1.0)


@lru_cache(maxsize=8)
def retrieval_for(
    cfg: EmbeddingConfig, timeout_s: float = 60.0
) -> tuple[Embedder | None, float | None]:
    """(embedder, min_cosine) for knowledge search; (None, None) keeps the hashing default."""
    if cfg.provider == "hashing":
        return None, None
    inner = GeminiEmbedder(cfg.model, cfg.dims, timeout_s)
    cached = CachedEmbedder(inner, EmbeddingCache(cfg.cache_path))
    return cached, GEMINI_MIN_COSINE if cfg.min_cosine is None else cfg.min_cosine
