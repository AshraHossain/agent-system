"""Hybrid retrieval: BM25 keyword scoring + local hashing-vector cosine, fused by RRF.

The hashing embedder is deterministic and offline. It captures lexical and
morphological overlap (unigrams, bigrams, character trigrams), NOT semantics.
A live semantic embedder can be supplied through the `Embedder` protocol.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

import numpy as np

TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STOPWORDS = frozenset(
    (
        "a an and are as at be by for from has have in is it its of on or that the this to was "
        "were will with we our us you your not no do does did can could should would"
    ).split()
)
RRF_K = 60


def tokenize(text: str) -> list[str]:
    toks = TOKEN_RE.findall((text or "").lower())
    out: list[str] = []
    for t in toks:
        if t in STOPWORDS:
            continue
        out.append(t)
        if "-" in t:  # index both "leaf-3" and its parts
            out.extend(p for p in t.split("-") if p and p not in STOPWORDS)
    return out


class BM25:
    def __init__(self, docs: Sequence[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avgdl = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for d, dl in zip(self.docs, self.lens, strict=True):
            s = 0.0
            for t in set(query):
                if t not in d:
                    continue
                tf = d[t]
                s += (
                    self.idf[t]
                    * tf
                    * (self.k1 + 1)
                    / (tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
                )
            out.append(s)
        return out


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Feature-hashing bag of n-grams, L2-normalised. Deterministic across runs."""

    def __init__(self, dims: int = 512):
        self.dims = dims

    def _features(self, text: str) -> list[str]:
        toks = tokenize(text)
        feats = list(toks)
        feats += [f"{a}_{b}" for a, b in pairwise(toks)]
        for t in toks:
            padded = f"#{t}#"
            feats += [padded[i : i + 3] for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dims), dtype=np.float32)
        for row, text in enumerate(texts):
            for f in self._features(text):
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
                mat[row, h % self.dims] += 1.0 if (h >> 63) & 1 else -1.0
            norm = np.linalg.norm(mat[row])
            if norm > 0:
                mat[row] /= norm
        return mat


class VectorIndex:
    def __init__(self, embedder: Embedder, texts: Sequence[str]):
        self.embedder = embedder
        self.matrix = embedder.embed(list(texts)) if texts else np.zeros((0, 1))

    def scores(self, query: str) -> list[float]:
        if not len(self.matrix):
            return []
        q = self.embedder.embed([query])[0]
        return [float(x) for x in self.matrix @ q]


@dataclass(frozen=True)
class Hit:
    index: int
    score: float
    matched_by: tuple[str, ...]


class HybridRetriever:
    def __init__(
        self, texts: Sequence[str], embedder: Embedder | None = None, min_cosine: float = 0.2
    ):
        self.texts = list(texts)
        self.bm25 = BM25([tokenize(t) for t in self.texts])
        self.vectors = VectorIndex(embedder or HashingEmbedder(), self.texts)
        self.min_cosine = min_cosine

    def search(self, query: str, top_k: int = 5, allowed: set[int] | None = None) -> list[Hit]:
        q = tokenize(query)
        kw = self.bm25.scores(q)
        vec = self.vectors.scores(query)
        cand = [i for i in range(len(self.texts)) if allowed is None or i in allowed]
        kw_rank = sorted((i for i in cand if kw[i] > 0), key=lambda i: (-kw[i], i))
        vec_rank = sorted(
            (i for i in cand if vec[i] >= self.min_cosine), key=lambda i: (-vec[i], i)
        )
        fused: dict[int, float] = {}
        by: dict[int, list[str]] = {}
        for name, ranking in (("keyword", kw_rank), ("vector", vec_rank)):
            for r, i in enumerate(ranking, start=1):
                fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + r)
                by.setdefault(i, []).append(name)
        order = sorted(fused, key=lambda i: (-fused[i], i))[:top_k]
        return [Hit(i, round(fused[i], 5), tuple(by[i])) for i in order]
