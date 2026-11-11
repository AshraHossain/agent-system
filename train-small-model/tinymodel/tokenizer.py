"""Step 2: tokenization, i.e. turning text into numbers and numbers back into text.

Neural networks only do arithmetic, so text has to become integers first. The simplest scheme is
*character-level*: every distinct character gets its own id. Big models (GPT, Claude, Llama) use
*subword* tokenizers such as BPE instead, which give common chunks like "ing" their own id, but the
idea is the same: a fixed vocabulary and a lookup table in each direction.
"""

from __future__ import annotations

import json
from pathlib import Path


class CharTokenizer:
    def __init__(self, chars: list[str]):
        self.chars = chars
        self.stoi = {ch: i for i, ch in enumerate(chars)}  # "string to integer"
        self.itos = dict(enumerate(chars))  # "integer to string"

    @classmethod
    def from_text(cls, text: str) -> CharTokenizer:
        """The vocabulary is simply the sorted set of characters that appear in the data."""
        return cls(sorted(set(text)))

    @property
    def vocab_size(self) -> int:
        return len(self.chars)

    def encode(self, text: str) -> list[int]:
        unknown = sorted({ch for ch in text if ch not in self.stoi})
        if unknown:
            raise ValueError(
                f"characters {unknown!r} never appeared in the training data, so the model has no "
                "id for them. Use only characters from the dataset."
            )
        return [self.stoi[ch] for ch in text]

    def decode(self, ids: list[int]) -> str:
        return "".join(self.itos[i] for i in ids)

    def to_dict(self) -> dict:
        return {"chars": self.chars}

    @classmethod
    def from_dict(cls, data: dict) -> CharTokenizer:
        return cls(data["chars"])

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    tok = CharTokenizer.from_text("12+7=19\n")
    print("vocabulary:", tok.chars)
    ids = tok.encode("7+12=19")
    print("encode('7+12=19') ->", ids)
    print("decode(...)       ->", tok.decode(ids))
