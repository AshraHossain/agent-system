"""Step 6: inference, i.e. using the trained model.

    uv run python -m tinymodel.generate --checkpoint checkpoints/addition.pt --prompt "41+37="
    uv run python -m tinymodel.generate --checkpoint checkpoints/my_text.pt --prompt "The " \
        --max-new 300 --temperature 0.8

Temperature controls randomness when sampling the next character:
low (0.2) = safe and repetitive, 1.0 = as trained, high (1.5) = creative and messy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch

from tinymodel.checkpoint import load_checkpoint
from tinymodel.config import pick_device


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Generate text with a trained TinyGPT.")
    p.add_argument("--checkpoint", type=Path, default=Path("checkpoints/addition.pt"))
    p.add_argument("--prompt", default="\n")
    p.add_argument("--max-new", type=int, default=None)
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--greedy", action="store_true", help="always pick the most likely character")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args(argv)

    torch.manual_seed(a.seed)
    device = pick_device()
    model, tok, _ = load_checkpoint(a.checkpoint, device)
    prompt = a.prompt.replace("\\n", "\n")

    # For an addition question, answer it and stop at the end of the line.
    is_question = prompt.rstrip().endswith("=")
    if is_question and not prompt.startswith("\n"):
        prompt = "\n" + prompt
    max_new = a.max_new or (6 if is_question else 200)
    stop = tok.stoi.get("\n") if is_question else None

    idx = torch.tensor([tok.encode(prompt)], dtype=torch.long, device=device)
    out = model.generate(
        idx, max_new, temperature=a.temperature, greedy=a.greedy or is_question, stop_token=stop
    )
    print(tok.decode(out[0].tolist()).strip("\n"))


if __name__ == "__main__":
    main()
