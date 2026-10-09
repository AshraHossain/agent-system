"""Minimal, deterministic Okapi BM25 (no external dependency)."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
STOPWORDS = frozenset(
    "a an and are as at be by for from has have if in into is it its of on or that the this to was were when "
    "with which while will not no do does".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Compound identifiers (core-1, link-pe-1-fw-2) also emit their parts."""
    out: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        if token in STOPWORDS:
            continue
        out.append(token)
        if "-" in token or "_" in token:
            parts = re.split(r"[-_]", token)
            out.extend(p for p in parts if p and p not in STOPWORDS)
            # adjacent pairs keep "core-1" matchable inside "link-core-1-agg-1"
            out.extend(f"{a}-{b}" for a, b in zip(parts, parts[1:], strict=False))
    return out


class BM25:
    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [Counter(d) for d in documents]
        self.lengths = [len(d) for d in documents]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if documents else 0.0
        df = Counter(term for d in self.docs for term in d)
        n = len(documents)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        terms = set(query)
        out = []
        for doc, length in zip(self.docs, self.lengths, strict=True):
            norm = self.k1 * (1 - self.b + self.b * length / self.avg_len) if self.avg_len else self.k1
            s = 0.0
            for t in terms:
                f = doc.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out
