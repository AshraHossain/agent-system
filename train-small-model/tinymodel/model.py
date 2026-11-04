"""Step 3: the model, a tiny GPT-style (decoder-only) transformer.

Data flow for one batch (B = batch size, T = sequence length, C = n_embd, V = vocab size):

    token ids (B, T)
      -> token embedding + position embedding     (B, T, C)
      -> n_layer x Block:
             x = x + MultiHeadAttention(LayerNorm(x))   "look at earlier characters"
             x = x + FeedForward(LayerNorm(x))          "think about what was gathered"
      -> final LayerNorm                          (B, T, C)
      -> linear "language-model head"             (B, T, V)  = logits, one score per possible next char

The model is trained to predict, at every position, the *next* character. That one simple
objective, repeated millions of times, is how all GPT-style models are trained.

The code is written out step by step (one attention head per class) for readability, not speed.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn

from tinymodel.config import ModelConfig


class Head(nn.Module):
    """One head of causal self-attention.

    Each position emits three vectors:
      * query: "what am I looking for?"
      * key:   "what do I contain?"
      * value: "what will I hand over if you pick me?"
    Attention scores = query . key. A mask stops a position from looking at the future,
    otherwise the model could cheat by reading the answer it is meant to predict.
    """

    def __init__(self, cfg: ModelConfig, head_size: int):
        super().__init__()
        self.key = nn.Linear(cfg.n_embd, head_size, bias=False)
        self.query = nn.Linear(cfg.n_embd, head_size, bias=False)
        self.value = nn.Linear(cfg.n_embd, head_size, bias=False)
        # Lower-triangular matrix of ones: row t has ones in columns 0..t (the past and present).
        self.register_buffer("tril", torch.tril(torch.ones(cfg.block_size, cfg.block_size)))
        self.dropout = nn.Dropout(cfg.dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, T, _ = x.shape
        k = self.key(x)  # (B, T, head_size)
        q = self.query(x)  # (B, T, head_size)
        # Similarity of every query with every key, scaled so values do not blow up.
        scores = q @ k.transpose(-2, -1) * k.shape[-1] ** -0.5  # (B, T, T)
        scores = scores.masked_fill(self.tril[:T, :T] == 0, float("-inf"))  # hide the future
        weights = F.softmax(scores, dim=-1)  # each row now sums to 1
        weights = self.dropout(weights)
        v = self.value(x)  # (B, T, head_size)
        return weights @ v  # weighted average of the values: (B, T, head_size)


class MultiHeadAttention(nn.Module):
    """Several heads in parallel, each free to look for a different pattern; outputs concatenated."""

    def __init__(self, cfg: ModelConfig):
        super().__init__()
        head_size = cfg.n_embd // cfg.n_head
        self.heads = nn.ModuleList([Head(cfg, head_size) for _ in range(cfg.n_head)])
        self.proj = nn.Linear(cfg.n_embd, cfg.n_embd)
        self.dropout = nn.Dropout(cfg.dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = torch.cat([h(x) for h in self.heads], dim=-1)
        return self.dropout(self.proj(out))


class FeedForward(nn.Module):
    """A small two-layer network applied to each position independently."""

    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(cfg.n_embd, 4 * cfg.n_embd),
            nn.GELU(),  # the non-linearity; without it stacked layers collapse into one linear map
            nn.Linear(4 * cfg.n_embd, cfg.n_embd),
            nn.Dropout(cfg.dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class Block(nn.Module):
    """Transformer block: communicate (attention), then compute (feed-forward).

    The ``x + ...`` pattern is a *residual connection*: each sub-layer only adds a correction to
    its input, which keeps gradients healthy as you stack more blocks.
    """

    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.ln1 = nn.LayerNorm(cfg.n_embd)
        self.attn = MultiHeadAttention(cfg)
        self.ln2 = nn.LayerNorm(cfg.n_embd)
        self.ffwd = FeedForward(cfg)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.ln1(x))
        x = x + self.ffwd(self.ln2(x))
        return x


class TinyGPT(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        assert cfg.vocab_size > 0, "set cfg.vocab_size from the tokenizer first"
        assert cfg.n_embd % cfg.n_head == 0, "n_embd must be divisible by n_head"
        self.cfg = cfg
        self.token_embedding = nn.Embedding(cfg.vocab_size, cfg.n_embd)  # "what character"
        self.position_embedding = nn.Embedding(cfg.block_size, cfg.n_embd)  # "where in the window"
        self.blocks = nn.Sequential(*[Block(cfg) for _ in range(cfg.n_layer)])
        self.ln_f = nn.LayerNorm(cfg.n_embd)
        self.lm_head = nn.Linear(cfg.n_embd, cfg.vocab_size)

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def forward(
        self, idx: torch.Tensor, targets: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        """idx: (B, T) token ids. targets: (B, T) the *next* token at each position, or None."""
        _, T = idx.shape
        assert T <= self.cfg.block_size, f"sequence of {T} is longer than block_size"
        pos = torch.arange(T, device=idx.device)
        x = self.token_embedding(idx) + self.position_embedding(pos)  # (B, T, C)
        x = self.blocks(x)
        x = self.ln_f(x)
        logits = self.lm_head(x)  # (B, T, V)

        loss = None
        if targets is not None:
            # Cross-entropy: how surprised the model is by the true next character, averaged.
            B, T, V = logits.shape
            loss = F.cross_entropy(logits.view(B * T, V), targets.view(B * T))
        return logits, loss

    @torch.no_grad()
    def generate(
        self,
        idx: torch.Tensor,
        max_new_tokens: int,
        temperature: float = 1.0,
        greedy: bool = False,
        stop_token: int | None = None,
    ) -> torch.Tensor:
        """Autoregressive generation: predict one character, append it, repeat."""
        for _ in range(max_new_tokens):
            idx_cond = idx[:, -self.cfg.block_size :]  # never feed more than the context window
            logits, _ = self(idx_cond)
            logits = logits[:, -1, :]  # only the prediction for the last position matters
            if greedy:
                next_id = logits.argmax(dim=-1, keepdim=True)
            else:
                probs = F.softmax(logits / temperature, dim=-1)
                next_id = torch.multinomial(probs, num_samples=1)
            idx = torch.cat([idx, next_id], dim=1)
            if stop_token is not None and bool((next_id == stop_token).all()):
                break
        return idx
