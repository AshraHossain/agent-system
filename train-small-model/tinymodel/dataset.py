"""Step 1: create (or bring) a custom dataset and split it into train / validation / test.

Two ways to get data:

1. ``addition``: generate a synthetic dataset of problems like ``23+58=81``. It is the default
   because you can *check* the model's answers exactly, which makes evaluation easy to understand.
2. ``text``: bring any plain-text file of your own (notes, poems, logs, code...). The file is split
   into train and validation parts.

Run it:

    uv run python -m tinymodel.dataset addition
    uv run python -m tinymodel.dataset text --input examples/my_text.txt --name my_text
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

DATA_ROOT = Path("data")


def make_addition_problems(max_operand: int = 99, seed: int = 0) -> list[str]:
    """Every problem ``a+b=c`` for 0 <= a, b <= max_operand, shuffled.

    Shuffling *before* splitting matters: if we split the ordered list, the test set would only
    contain large numbers the model never saw during training.
    """
    problems = [f"{a}+{b}={a + b}" for a in range(max_operand + 1) for b in range(max_operand + 1)]
    random.Random(seed).shuffle(problems)
    return problems


def split_list(items: list[str], val_frac: float, test_frac: float) -> tuple[list, list, list]:
    """Cut a list into three disjoint parts. Disjoint = no item appears in two parts."""
    n = len(items)
    n_test = int(n * test_frac)
    n_val = int(n * val_frac)
    test = items[:n_test]
    val = items[n_test : n_test + n_val]
    train = items[n_test + n_val :]
    return train, val, test


def split_text(text: str, val_frac: float = 0.1) -> tuple[str, str]:
    """For free text we cannot shuffle individual characters (that would destroy the meaning),
    so we keep the first 90% for training and hold out the last 10% for validation."""
    cut = int(len(text) * (1 - val_frac))
    return text[:cut], text[cut:]


def write_split(out_dir: Path, **parts: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in parts.items():
        (out_dir / f"{name}.txt").write_text(content, encoding="utf-8")
        print(f"  wrote {out_dir / (name + '.txt')}  ({len(content):,} characters)")


def build_addition(out_dir: Path, max_operand: int = 99, seed: int = 0) -> None:
    problems = make_addition_problems(max_operand, seed)
    train, val, test = split_list(problems, val_frac=0.1, test_frac=0.1)
    print(
        f"{len(problems):,} problems -> train {len(train):,} / val {len(val):,} / test {len(test):,}"
    )
    write_split(
        out_dir,
        train="\n".join(train) + "\n",
        val="\n".join(val) + "\n",
        test="\n".join(test) + "\n",
    )


def build_text(input_path: Path, out_dir: Path, val_frac: float = 0.1) -> None:
    text = input_path.read_text(encoding="utf-8")
    if len(text) < 1000:
        raise SystemExit(f"{input_path} has only {len(text)} characters; give it at least ~1,000.")
    train, val = split_text(text, val_frac)
    print(f"{len(text):,} characters -> train {len(train):,} / val {len(val):,}")
    write_split(out_dir, train=train, val=val)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="kind", required=True)
    add = sub.add_parser("addition", help="generate the synthetic addition dataset")
    add.add_argument("--max-operand", type=int, default=99)
    add.add_argument("--name", default="addition")
    txt = sub.add_parser("text", help="use your own plain-text file")
    txt.add_argument("--input", type=Path, required=True)
    txt.add_argument("--name", default="my_text")
    txt.add_argument("--val-frac", type=float, default=0.1)
    args = parser.parse_args(argv)

    out_dir = DATA_ROOT / args.name
    if args.kind == "addition":
        build_addition(out_dir, args.max_operand)
    else:
        build_text(args.input, out_dir, args.val_frac)


if __name__ == "__main__":
    main()
