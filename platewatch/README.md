# PlateWatch

Detect vehicle plates in images, read and tag them, store them in a searchable vector index, and
query them through a PydanticAI agent. A self-contained project (own `pyproject.toml`, `uv.lock`,
tests and CI workflow); run commands from inside this folder.

```
image -> Detector (HF object detection) -> crop -> Reader (TrOCR) -> normalise -> tag -> PlateStore
                                                                                     ^
PydanticAI agent tools: scan_new_images · search_plates · list_needs_review ----------+
```

- `core/` is framework-free: normalisation, tagging thresholds, hashing embedder, SQLite store.
- Tools take no paths/URLs/SQL from the model; the inbox is fixed in deps. The output validator
  rejects plate ids no tool returned.
- Setup: `uv sync`. Offline: `uv run platewatch demo` (scripted detector/OCR + mock agent).
  Tests: `uv run pytest`; lint: `uv run ruff check . && uv run ruff format --check .`.
- Real models: `uv run --with transformers --with torch platewatch ingest <dir>`. Defaults
  (`nickmuchi/yolos-small-finetuned-license-plate-detection`, `microsoft/trocr-base-printed`) are
  **untested** here; override with `PLATEWATCH_DETECTOR_MODEL` / `PLATEWATCH_OCR_MODEL`.
- Live agent: `PLATEWATCH_AGENT_MODEL=<provider:model>`.

Limits: the embedder is lexical (n-grams with OCR-confusable folding), not learned; search is a
brute-force scan (fine to ~10^5 plates); format rules are region-agnostic; not production-ready.
