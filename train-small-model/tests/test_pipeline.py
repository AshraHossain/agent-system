"""Small, fast checks that each step of the pipeline does what notes.md says it does."""

from pathlib import Path

import pytest
import torch

from tinymodel.checkpoint import load_checkpoint
from tinymodel.config import ModelConfig, TrainConfig
from tinymodel.dataset import build_addition, make_addition_problems, split_list, split_text
from tinymodel.evaluate import answer, sequence_loss
from tinymodel.model import TinyGPT
from tinymodel.tokenizer import CharTokenizer
from tinymodel.train import get_batch, train


def test_addition_problems_are_correct_and_complete():
    problems = make_addition_problems(max_operand=9)
    assert len(problems) == 100
    for p in problems:
        q, ans = p.split("=")
        a, b = q.split("+")
        assert int(a) + int(b) == int(ans)


def test_splits_do_not_overlap():
    train, val, test = split_list(make_addition_problems(20), val_frac=0.1, test_frac=0.1)
    assert (
        not (set(train) & set(val)) and not (set(train) & set(test)) and not (set(val) & set(test))
    )
    t, v = split_text("abcdefghij", val_frac=0.2)
    assert t + v == "abcdefghij" and v == "ij"


def test_tokenizer_round_trip_and_unknown_chars():
    tok = CharTokenizer.from_text("hello world\n")
    assert tok.decode(tok.encode("hello")) == "hello"
    assert tok.vocab_size == len(set("hello world\n"))
    with pytest.raises(ValueError, match="never appeared"):
        tok.encode("xyz")


def test_get_batch_targets_are_inputs_shifted_by_one():
    data = torch.arange(100)
    x, y = get_batch(data, block_size=8, batch_size=4, device="cpu")
    assert x.shape == y.shape == (4, 8)
    assert torch.equal(x[:, 1:], y[:, :-1])


def test_model_shapes_and_initial_loss_is_near_random_guessing():
    torch.manual_seed(0)
    cfg = ModelConfig(vocab_size=13, block_size=16, n_embd=32, n_head=4, n_layer=1)
    model = TinyGPT(cfg)
    idx = torch.randint(13, (2, 16))
    logits, loss = model(idx, idx)
    assert logits.shape == (2, 16, 13)
    # An untrained model should be about as unsure as uniform guessing: loss ~ ln(13) ~ 2.56.
    assert abs(loss.item() - torch.log(torch.tensor(13.0)).item()) < 0.5


def test_causal_mask_hides_the_future():
    torch.manual_seed(0)
    model = TinyGPT(ModelConfig(vocab_size=10, block_size=8, n_embd=16, n_head=2, n_layer=1)).eval()
    a = torch.tensor([[1, 2, 3, 4, 5, 6, 7, 8]])
    b = a.clone()
    b[0, -1] = 0  # change only the last token
    la, _ = model(a)
    lb, _ = model(b)
    assert torch.allclose(la[0, :-1], lb[0, :-1])  # earlier predictions must not change


def test_short_training_run_lowers_loss_and_checkpoint_round_trips(tmp_path: Path):
    data_dir = tmp_path / "data"
    build_addition(data_dir, max_operand=20)
    out = tmp_path / "model.pt"
    mcfg = ModelConfig(block_size=16, n_embd=32, n_head=4, n_layer=1)
    tcfg = TrainConfig(batch_size=32, max_steps=150, eval_interval=50, eval_batches=5)
    result = train(data_dir, out, mcfg, tcfg)
    hist = result["history"]
    assert hist[-1]["train"] < hist[0]["train"] - 0.5

    model, tok, ckpt = load_checkpoint(out)
    assert ckpt["val_loss"] == pytest.approx(result["best_val"])
    val = (data_dir / "val.txt").read_text()
    assert sequence_loss(model, tok, val, "cpu") < hist[0]["val"]
    assert isinstance(answer(model, tok, "\n3+4=", "cpu"), str)  # inference runs end to end
