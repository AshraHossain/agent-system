"""Deterministic, offline plate embedder.

Character 1-3-grams are hashed into a fixed-size vector. Visually confusable characters
(O/0, I/1, B/8, S/5, Z/2, G/6) are folded together *for embedding only*, so an OCR slip such
as ``AB1234`` read as ``A81234`` still lands next to the true plate. This is lexical similarity,
not learned semantics; swap in a real embedder via the ``Embedder`` protocol.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np

from platewatch.core.text import normalize_plate

DIM = 256
_FOLD = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "B": "8", "S": "5", "Z": "2", "G": "6"}
)


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> np.ndarray: ...


class HashingEmbedder:
    dim = DIM

    def embed(self, text: str) -> np.ndarray:
        folded = normalize_plate(text).translate(_FOLD)
        vec = np.zeros(self.dim, dtype=np.float32)
        padded = f"^{folded}$"
        for n in (1, 2, 3):
            for i in range(len(padded) - n + 1):
                gram = padded[i : i + n]
                digest = hashlib.blake2b(gram.encode(), digest_size=4).digest()
                h = int.from_bytes(digest, "big")
                vec[h % self.dim] += (-1.0 if (h >> 31) & 1 else 1.0) * n
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec
