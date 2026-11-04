"""Hyperparameters: the knobs *you* choose before training starts.

The model *learns* its parameters (weights). Hyperparameters are different: nobody learns them,
you pick them. Keeping them in one dataclass means they can be saved inside the checkpoint, so the
exact same model can be rebuilt later for evaluation and inference.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch


@dataclass
class ModelConfig:
    vocab_size: int = 0  # filled in from the tokenizer: how many distinct characters exist
    block_size: int = 32  # context length: how many previous characters the model can look at
    n_embd: int = 64  # width: size of the vector that represents each character position
    n_head: int = 4  # attention heads per layer (n_embd must be divisible by n_head)
    n_layer: int = 2  # depth: how many transformer blocks are stacked
    dropout: float = 0.0  # randomly zero some activations during training (a regulariser)


@dataclass
class TrainConfig:
    batch_size: int = 64  # how many training snippets are processed together per step
    max_steps: int = 3000  # how many optimisation steps to take
    learning_rate: float = 3e-3  # how big each update to the weights is
    eval_interval: int = 250  # how often (in steps) to measure train/val loss
    eval_batches: int = 20  # how many batches to average when measuring loss
    seed: int = 1337  # fixes randomness so runs are repeatable


def to_dict(cfg: ModelConfig | TrainConfig) -> dict:
    return asdict(cfg)


def pick_device() -> str:
    """Use a GPU if there is one; otherwise the CPU (which is fine for a model this small)."""
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"  # Apple Silicon GPU
    return "cpu"
