"""Saving and loading a trained model.

A checkpoint must hold *everything* needed to rebuild the model later: the learned weights, the
hyperparameters (to rebuild the same shape of network) and the tokenizer (to map text to the same
ids). Forgetting the tokenizer is a classic beginner bug: same weights, different ids, garbage out.
"""

from __future__ import annotations

from pathlib import Path

import torch

from tinymodel.config import ModelConfig
from tinymodel.model import TinyGPT
from tinymodel.tokenizer import CharTokenizer


def save_checkpoint(path: Path, model: TinyGPT, tok: CharTokenizer, extra: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state": model.state_dict(),
            "model_config": vars(model.cfg),
            "tokenizer": tok.to_dict(),
            **extra,
        },
        path,
    )


def load_checkpoint(path: Path, device: str = "cpu") -> tuple[TinyGPT, CharTokenizer, dict]:
    ckpt = torch.load(path, map_location=device, weights_only=True)
    model = TinyGPT(ModelConfig(**ckpt["model_config"]))
    model.load_state_dict(ckpt["model_state"])
    model.to(device).eval()  # eval() switches off dropout for inference
    return model, CharTokenizer.from_dict(ckpt["tokenizer"]), ckpt
