# CLAUDE.md — PlateWatch

PydanticAI licence-plate module: detect, read, tag, store and vector-search plates. Self-contained
(own `pyproject.toml`, `uv.lock`, tests, `platewatch.yml`); it does not use ADK. See
[README.md](README.md). Not production-ready.

## Commands (run inside `platewatch/`)

```bash
uv sync
uv run pytest                              # offline; the live test is deselected
uv run ruff check . && uv run ruff format --check .
uv run platewatch demo                     # scripted detector/OCR + mock agent
PLATEWATCH_LIVE_DIR=<photos> uv run --with transformers --with torch \
  pytest -m live tests/test_platewatch_live.py -s    # needs network to huggingface.co
```

## Rules

- `src/platewatch/core/` must not import `pydantic_ai`, `torch`, `transformers` or `google`
  (`tests/test_platewatch.py` enforces it). Normalisation, tag thresholds, embedding and the store
  live there, never in prompts.
- Agent tools take no paths, URLs or SQL from the model (the inbox is fixed in `PlateDeps`); every
  plate id in agent output must have been returned by a tool (output validator).
- Detector and reader are `Protocol`s in `vision/`; Hugging Face backends import torch lazily and
  are covered only by `-m live`. Offline tests use `ScriptedVision`.
- The Hugging Face defaults have never been run against real photos; do not report accuracy for
  them until the live test has been run and its numbers recorded.
- Regional formats live in `core/formats.py`, one entry per region with the source URL it came
  from. Never add a pattern from memory, and never turn a format check into a rejection gate: old
  formats, personalised marks and non-car schemes are all legitimate and match nothing. A region
  whose format cannot survive normalisation is left out with a comment saying why, as Germany is.

## Planning

Plan non-trivial work with GSD → Superpowers → Ruflo as set out in [../PLANNING.md](../PLANNING.md); run every check inside this directory.
