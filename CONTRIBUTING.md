# Contributing to NetPulse AI

1. Read [CLAUDE.md](CLAUDE.md) (hard rules) and [PLAN.md](PLAN.md) (current phase).
2. Set up with `uv sync`. Before every push, run `uv run pytest` and
   `uv run ruff check .`.
3. **Detection, topology, retrieval, ranking and policy stay deterministic.**
   LLM use belongs only behind the `HypothesisGenerator` protocol in
   `netpulse/llm/`.
4. **New state fields** need a single owning node and an entry in
   `docs/state_model.md`.
5. **Ground truth stays out of `netpulse/`.** Never import `synthgen` or read
   `eval/labels` from it.
6. **If you change `synthgen/`,** regenerate the data with
   `uv run python -m synthgen.generate`, commit the changed files and
   manifest, and explain the change in the PR.
7. Use conventional commits.

## Definition of done

- Tests and ruff are green.
- Behaviour changes are documented in the relevant doc or ADR.
- The PR says what was *not* verified, especially anything that depends on a
  real LLM.
