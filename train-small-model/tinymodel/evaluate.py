"""Step 5: evaluation, i.e. how good is the model on data it has never seen?

Two kinds of numbers:

* **Loss / perplexity** works for any dataset. Perplexity = exp(loss): roughly "how many characters
  the model is still choosing between". 1.0 would be perfect certainty; ``vocab_size`` would be
  pure guessing.
* **Task accuracy** needs a task with a right answer. For the addition dataset we give the model
  ``23+58=`` and check whether it writes ``81``. This is what you actually care about; loss is
  only a proxy for it.

Run it:

    uv run python -m tinymodel.evaluate --checkpoint checkpoints/addition.pt --data data/addition
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import torch
import torch.nn.functional as F

from tinymodel.checkpoint import load_checkpoint
from tinymodel.config import pick_device
from tinymodel.model import TinyGPT
from tinymodel.tokenizer import CharTokenizer


@torch.no_grad()
def sequence_loss(model: TinyGPT, tok: CharTokenizer, text: str, device: str) -> float:
    """Average next-character loss over the whole text, in non-overlapping windows (no sampling)."""
    ids = torch.tensor(tok.encode(text), dtype=torch.long, device=device)
    bs = model.cfg.block_size
    total, count = 0.0, 0
    for start in range(0, len(ids) - 1, bs):
        x = ids[start : start + bs]
        y = ids[start + 1 : start + bs + 1]
        x = x[: len(y)]
        logits, _ = model(x.unsqueeze(0))
        total += F.cross_entropy(logits[0], y, reduction="sum").item()
        count += len(y)
    return total / count


@torch.no_grad()
def answer(model: TinyGPT, tok: CharTokenizer, prompt: str, device: str, max_len: int = 6) -> str:
    """Greedy-decode until a newline: always take the single most likely next character."""
    idx = torch.tensor([tok.encode(prompt)], dtype=torch.long, device=device)
    out = model.generate(idx, max_len, greedy=True, stop_token=tok.stoi.get("\n"))
    return tok.decode(out[0, len(prompt) :].tolist()).strip()


def addition_accuracy(
    model: TinyGPT, tok: CharTokenizer, lines: list[str], device: str, show: int = 8
) -> float:
    correct = 0
    mistakes = []
    for line in lines:
        question, expected = line.split("=")
        # Start from a newline so the model sees the same context it saw in training.
        got = answer(model, tok, "\n" + question + "=", device)
        if got == expected:
            correct += 1
        elif len(mistakes) < show:
            mistakes.append(f"    {question}= expected {expected}, model said {got!r}")
    if mistakes:
        print("  some mistakes:")
        print("\n".join(mistakes))
    return correct / len(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Evaluate a trained TinyGPT checkpoint.")
    p.add_argument("--checkpoint", type=Path, default=Path("checkpoints/addition.pt"))
    p.add_argument("--data", type=Path, default=Path("data/addition"))
    p.add_argument("--limit", type=int, default=500, help="max addition problems to check")
    a = p.parse_args(argv)

    device = pick_device()
    model, tok, ckpt = load_checkpoint(a.checkpoint, device)
    print(f"checkpoint from step {ckpt['step']} (val loss during training {ckpt['val_loss']:.4f})")

    # The test split exists only for addition; free text uses val as its held-out set.
    split = "test" if (a.data / "test.txt").exists() else "val"
    text = (a.data / f"{split}.txt").read_text(encoding="utf-8")
    loss = sequence_loss(model, tok, text, device)
    print(
        f"{split} loss {loss:.4f}  perplexity {math.exp(loss):.2f}  (vocab size {tok.vocab_size})"
    )

    if split == "test" and "+" in text:
        lines = [ln for ln in text.splitlines() if ln][: a.limit]
        acc = addition_accuracy(model, tok, lines, device)
        print(f"exact-match accuracy on {len(lines)} unseen problems: {acc:.1%}")


if __name__ == "__main__":
    main()
