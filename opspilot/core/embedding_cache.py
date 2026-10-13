"""Persistent cache for embedding vectors, so repeated and offline runs need no API.

Vectors are keyed by a hash of (embedder name, role, text); raw text is never
stored. A cache hit never touches the wrapped embedder, which is also what lets a
fully cached corpus be searched with no credentials or network.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path

import numpy as np

from opspilot.core.retrieval import Embedder

KEY_VERSION = "v1"


def cache_key(name: str, role: str, text: str) -> str:
    return hashlib.sha256(f"{KEY_VERSION}\x00{name}\x00{role}\x00{text}".encode()).hexdigest()


class EmbeddingCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS embeddings "
                "(key TEXT PRIMARY KEY, dims INTEGER NOT NULL, vec BLOB NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def get_many(self, keys: Sequence[str]) -> dict[str, np.ndarray]:
        found: dict[str, np.ndarray] = {}
        with closing(self._connect()) as db:
            for key in keys:
                row = db.execute(
                    "SELECT dims, vec FROM embeddings WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    continue
                dims, blob = row
                if len(blob) == dims * 4:  # a truncated or foreign row counts as a miss
                    found[key] = np.frombuffer(blob, dtype=np.float32)
        return found

    def put_many(self, items: dict[str, np.ndarray]) -> None:
        rows = [
            (key, len(vec), np.asarray(vec, dtype=np.float32).tobytes())
            for key, vec in items.items()
        ]
        with closing(self._connect()) as db, db:
            db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)", rows)


class CachedEmbedder:
    def __init__(self, inner: Embedder, cache: EmbeddingCache):
        self.inner = inner
        self.cache = cache
        self.name = getattr(inner, "name", type(inner).__name__)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return self._through_cache("document", texts, self.inner.embed)

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._through_cache(
            "query", texts, getattr(self.inner, "embed_queries", self.inner.embed)
        )

    def _through_cache(
        self, role: str, texts: Sequence[str], fetch: Callable[[list[str]], np.ndarray]
    ) -> np.ndarray:
        keys = [cache_key(self.name, role, t) for t in texts]
        have = self.cache.get_many(list(dict.fromkeys(keys)))
        missing = list(dict.fromkeys(t for t, k in zip(texts, keys, strict=True) if k not in have))
        if missing:
            fresh = fetch(missing)
            new = {cache_key(self.name, role, t): v for t, v in zip(missing, fresh, strict=True)}
            self.cache.put_many(new)  # only after the whole fetch succeeded
            have.update(new)
        return np.stack([have[k] for k in keys]) if keys else np.zeros((0, 1), dtype=np.float32)
