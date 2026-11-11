"""Step 4: the training loop.

Training = repeat these four lines thousands of times:

    logits, loss = model(x, y)    # 1. forward pass: make predictions, measure how wrong they are
    optimizer.zero_grad()         # 2. clear the gradients left over from the previous step
    loss.backward()               # 3. backward pass: work out how each weight affected the loss
    optimizer.step()              # 4. nudge every weight a little in the direction that lowers it

Run it:

    uv run python -m tinymodel.train --data data/addition
    uv run python -m tinymodel.train --data data/my_text --max-steps 2000
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch

from tinymodel.checkpoint import save_checkpoint
from tinymodel.config import ModelConfig, TrainConfig, pick_device, to_dict
from tinymodel.model import TinyGPT
from tinymodel.tokenizer import CharTokenizer


def load_splits(data_dir: Path) -> tuple[str, str]:
    train = (data_dir / "train.txt").read_text(encoding="utf-8")
    val = (data_dir / "val.txt").read_text(encoding="utf-8")
    return train, val


def get_batch(
    data: torch.Tensor, block_size: int, batch_size: int, device: str
) -> tuple[torch.Tensor, torch.Tensor]:
    """Pick ``batch_size`` random windows from the data.

    x is a window of ``block_size`` ids; y is the same window shifted one to the right, so y[t] is
    the character that comes *after* x[t]. One window therefore gives block_size training examples:
    predict x[1] from x[:1], x[2] from x[:2], and so on.
    """
    starts = torch.randint(len(data) - block_size - 1, (batch_size,))
    x = torch.stack([data[i : i + block_size] for i in starts])
    y = torch.stack([data[i + 1 : i + block_size + 1] for i in starts])
    return x.to(device), y.to(device)


@torch.no_grad()
def estimate_loss(model: TinyGPT, splits: dict, mcfg: ModelConfig, tcfg: TrainConfig, device: str):
    """Average the loss over several batches. A single batch is too noisy to trust."""
    model.eval()
    out = {}
    for name, data in splits.items():
        losses = torch.zeros(tcfg.eval_batches)
        for k in range(tcfg.eval_batches):
            x, y = get_batch(data, mcfg.block_size, tcfg.batch_size, device)
            _, loss = model(x, y)
            losses[k] = loss.item()
        out[name] = losses.mean().item()
    model.train()
    return out


def train(data_dir: Path, out_path: Path, mcfg: ModelConfig, tcfg: TrainConfig) -> dict:
    torch.manual_seed(tcfg.seed)
    device = pick_device()

    # --- data -> ids ------------------------------------------------------------------------
    train_text, val_text = load_splits(data_dir)
    tok = CharTokenizer.from_text(train_text + val_text)
    train_data = torch.tensor(tok.encode(train_text), dtype=torch.long)
    val_data = torch.tensor(tok.encode(val_text), dtype=torch.long)
    mcfg.vocab_size = tok.vocab_size

    # --- model + optimiser ------------------------------------------------------------------
    model = TinyGPT(mcfg).to(device)
    # AdamW: gradient descent with per-weight adaptive step sizes and a little weight decay.
    optimizer = torch.optim.AdamW(model.parameters(), lr=tcfg.learning_rate)
    print(f"device={device}  vocab={tok.vocab_size}  parameters={model.num_parameters():,}")
    print(f"train tokens={len(train_data):,}  val tokens={len(val_data):,}")

    history: list[dict] = []
    best_val = float("inf")
    t0 = time.time()
    for step in range(tcfg.max_steps + 1):
        # Every so often, stop and measure. Val loss is the honest number: the model never trains
        # on it, so it tells us whether we are learning patterns or just memorising.
        if step % tcfg.eval_interval == 0 or step == tcfg.max_steps:
            losses = estimate_loss(
                model, {"train": train_data, "val": val_data}, mcfg, tcfg, device
            )
            history.append({"step": step, **losses})
            marker = ""
            if losses["val"] < best_val:  # keep the best model seen so far ("early stopping")
                best_val = losses["val"]
                save_checkpoint(
                    out_path,
                    model,
                    tok,
                    {"train_config": to_dict(tcfg), "step": step, "val_loss": best_val},
                )
                marker = "  <- saved (best val so far)"
            print(
                f"step {step:5d} | train loss {losses['train']:.4f} | val loss {losses['val']:.4f}"
                f" | {time.time() - t0:5.1f}s{marker}"
            )
        if step == tcfg.max_steps:
            break

        x, y = get_batch(train_data, mcfg.block_size, tcfg.batch_size, device)
        _, loss = model(x, y)  # 1. forward
        optimizer.zero_grad(set_to_none=True)  # 2. reset gradients
        loss.backward()  # 3. backward (backpropagation)
        optimizer.step()  # 4. update weights

    history_path = out_path.with_suffix(".history.json")
    history_path.write_text(json.dumps(history, indent=2))
    print(f"best val loss {best_val:.4f}; checkpoint -> {out_path}; curve -> {history_path}")
    return {"best_val": best_val, "history": history}


def main(argv: list[str] | None = None) -> None:
    m, t = ModelConfig(), TrainConfig()
    p = argparse.ArgumentParser(description="Train TinyGPT on a prepared dataset folder.")
    p.add_argument("--data", type=Path, default=Path("data/addition"))
    p.add_argument("--out", type=Path, default=None, help="default: checkpoints/<dataset>.pt")
    p.add_argument("--block-size", type=int, default=m.block_size)
    p.add_argument("--n-embd", type=int, default=m.n_embd)
    p.add_argument("--n-head", type=int, default=m.n_head)
    p.add_argument("--n-layer", type=int, default=m.n_layer)
    p.add_argument("--dropout", type=float, default=m.dropout)
    p.add_argument("--batch-size", type=int, default=t.batch_size)
    p.add_argument("--max-steps", type=int, default=t.max_steps)
    p.add_argument("--lr", type=float, default=t.learning_rate)
    p.add_argument("--eval-interval", type=int, default=t.eval_interval)
    p.add_argument("--seed", type=int, default=t.seed)
    a = p.parse_args(argv)

    mcfg = ModelConfig(
        block_size=a.block_size,
        n_embd=a.n_embd,
        n_head=a.n_head,
        n_layer=a.n_layer,
        dropout=a.dropout,
    )
    tcfg = TrainConfig(
        batch_size=a.batch_size,
        max_steps=a.max_steps,
        learning_rate=a.lr,
        eval_interval=a.eval_interval,
        seed=a.seed,
    )
    out = a.out or Path("checkpoints") / f"{a.data.name}.pt"
    train(a.data, out, mcfg, tcfg)


if __name__ == "__main__":
    main()
