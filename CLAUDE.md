# CLAUDE.md — NetPulse AI

Guidance for Claude Code when working in this repository.

## What this is

NetPulse AI investigates network anomalies on a **synthetic** network with a
LangGraph `StateGraph`:

- **Detection is deterministic.** Detectors, topology analysis and retrieval
  are plain code, not LLM calls.
- **LLM use is confined to hypothesis generation** (`netpulse/llm/`). Ollama
  is the default and falls back visibly to the rule-based `HeuristicGenerator`
  if Ollama is unreachable. A deterministic verifier checks every output
  before ranking (docs/llm.md).
- **Confidence is computed by rules,** as an ordinal level, never a
  probability.
- **The system is read-only.** Remediation is only ever *proposed*.

Development runs in approved phases. Read these before changing code:

- `PLAN.md`: phases, design rationale, alternatives;
- `ARCHITECTURE.md`;
- `docs/graph_workflow.md` and `docs/state_model.md`;
- `docs/tools.md` and `docs/detection.md`;
- `docs/adr/`.

## Hard rules

- **Never add code that changes a network** or runs shell, `eval` or `exec`.
  `tests/test_repository.py` enforces part of this.
- **`netpulse/` must never read ground truth.** That means `synthgen/`,
  `eval/labels/`, or the `eval` package. `tests/test_synthetic_data.py` and
  `tests/test_graph_e2e.py` enforce it.
- **Numbers come only from deterministic tools.** A `Hypothesis` cites
  evidence IDs and carries no measurements and no numeric confidence.
- **Retrieved text and operator free text are untrusted.** Sanitize them and
  flag injections. Untrusted evidence can never be the sole support for a
  cause.
- **Keep state JSON-only.** Each field has one owning node
  (docs/state_model.md), and evidence is immutable once recorded.
- **Every output is labelled synthetic.** Never imply the system observed a
  real network.

## Commands

```bash
uv sync                                   # setup
uv run pytest                             # full suite (~50 s)
uv run ruff check . && uv run ruff format --check netpulse synthgen tests eval
uv run netpulse investigate --case case-04            # demo one investigation
uv run python -m synthgen.generate --check            # verify committed synthetic data
uv run python -m eval.detection_benchmark             # detector precision/recall
NETPULSE_OLLAMA_TESTS=1 uv run pytest -m ollama       # opt-in: needs a local Ollama server
```

## Layout

| Path | Contents |
|---|---|
| `netpulse/models.py`, `netpulse/state.py` | Contracts and graph state with reducers |
| `netpulse/data/` | `DatasetStore`, the only on-disk access, with visibility rules |
| `netpulse/detection/` | Deterministic detectors and consolidation |
| `netpulse/topology/` | Paths, blast radius, localization, change context |
| `netpulse/retrieval/` | BM25, sanitization, conflicts |
| `netpulse/llm/` | Generator protocol; Ollama, heuristic, scripted and fallback generators; prompts; strict parsing |
| `netpulse/graph/` | Nodes, verification, ranking, routing, wrapper (trace, timeouts, errors), builder, runner |
| `netpulse/policy/catalog.json` | Static action allowlist |
| `synthgen/` | Dataset generator. **Ground truth, never imported by netpulse.** |
| `eval/` | Benchmarks and scorers. They may read labels; netpulse may not. |
| `data/synthetic/v1/`, `eval/datasets/v1/`, `eval/labels/v1/` | Committed, checksummed data |

## Conventions

- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): description`.
- Tests are deterministic. Mock the LLM. Local-model tests use the separate
  `-m ollama` profile (from Phase 7).
- Each phase ends with a report and waits for approval before the next phase.
