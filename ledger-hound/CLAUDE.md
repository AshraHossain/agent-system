# CLAUDE.md — Ledger Hound

Agentic QA and financial-control red-team harness for a Purchase-to-Pay (P2P) API, built with the **OpenAI Agents SDK**, **FastAPI**, and **DSPy**. Self-contained sub-project (own `pyproject.toml`, `uv.lock`, tests, and `.github/workflows/ledger-hound.yml`); run all commands from inside `ledger-hound/`.

## Verified Commands

```bash
uv sync --extra dspy
uv run ruff check . && uv run ruff format --check .
uv run --extra dspy pytest                        # 130 offline tests (0 skipped), no API key needed
uv run ledger-hound attacks                       # list 28 attacks and 26 plantable bugs
uv run ledger-hound run --local-bugs dup_case     # explore + attack + judge against in-process P2P API
uv run ledger-hound score                         # verify 26/26 planted bug detection & 0 false alarms
uv run ledger-hound stress --local-bugs silent_partial --shrink 1
```

## Layering & Engineering Rules

- **Oracle Decides Pass/Fail, Never the LLM**: Financial verdicts (GL balance, 3-way match, sub-cent drift, partial receipt flags, AP exposure) are computed with exact `Decimal` arithmetic in `src/ledger_hound/qa/oracle.py` over re-read API state.
- **Deterministic Offline Mock**: Without `BYNARA_API_KEY` (or with `--mock`), `qa/brain.py` uses a deterministic mock brain and `tests/test_dspy_opt.py` uses `DummyLM`.
- **Ground-Truth Isolation**: Planted bug names and blind-practice answer keys are never passed to the explorer, adversary, or classifier inputs (`tests/test_labels.py` guards this).
- **Sub-Project Independence**: Never import from the root `opspilot` package or other sibling projects.

## Planning

Plan non-trivial work with GSD → Superpowers → Ruflo as set out in [../PLANNING.md](../PLANNING.md); run every check inside this directory.
