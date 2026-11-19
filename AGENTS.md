# AGENTS.md — OpsPilot AI & Agent System Workspace

A workspace of independent GenAI projects led by **OpsPilot AI** (root project), a read-only multi-agent network-operations investigation system built on **Google ADK 2.11.0** and **Gemini / Vertex AI**.

> Research & portfolio engineering showcase. All datasets and fault injections are synthetic; offline evaluation runs against a deterministic scripted mock model (`ScriptedLlm`) to verify pipeline, tool, and guardrail invariants.

## Workspace Projects

| Path | System | Stack | Details |
|---|---|---|---|
| `opspilot/`, `adk_apps/`, `tests/` | **OpsPilot AI**: Multi-agent network-ops investigation & evidence-linked report | Google ADK 2.11.0, Gemini, FastAPI, SQLite | [ARCHITECTURE.md](ARCHITECTURE.md), [PLAN.md](PLAN.md), [SECURITY.md](SECURITY.md) |
| `ledger-hound/` | Agentic QA / red-team harness for a Purchase-to-Pay API (26 plantable bugs) | OpenAI Agents SDK, DSPy, FastAPI | [ledger-hound/README.md](ledger-hound/README.md) |
| `traffic-flow-analyzer/` | Road congestion forecasting agents + interactive dashboard | PydanticAI, Gradio, Plotly | [traffic-flow-analyzer/CLAUDE.md](traffic-flow-analyzer/CLAUDE.md) |
| `platewatch/` | Licence-plate detection, OCR normalization, tagging & vector search | PydanticAI, Hugging Face, Pillow | [platewatch/CLAUDE.md](platewatch/CLAUDE.md) |
| `api-tests/` | Black-box contract, fuzzing & security test suite for Traffic Flow API | pytest, httpx, Schemathesis | [api-tests/CLAUDE.md](api-tests/CLAUDE.md) |

Each sub-project is strictly independent (own `pyproject.toml`, `uv.lock`, test suite, and `.github/workflows/*.yml`). Never import across projects or merge sub-projects into the root `pyproject.toml`.

## Verified Root Commands (OpsPilot AI)

```bash
make check                                # Full gate: ruff lint/format + offline pytest (>=80% cov) + guardrail eval
uv sync                                   # Install pinned dependencies (google-adk[db]==2.11.0 + dev group)
uv run ruff check . && uv run ruff format --check .
uv run pytest                             # Offline test suite (unit + integration + OTLP tracing; live excluded)
uv run opspilot build-data                # Build 14 main + 10 held-out synthetic SQLite datasets -> var/datasets/
uv run opspilot demo --case C03           # End-to-end offline investigation using ScriptedLlm
uv run opspilot eval --guardrails         # Reproducible scorecard + 10 guardrail perturbations -> var/eval/
uv run opspilot eval --verifier-matrix    # Planted-defect red-team matrix across all 24 cases
uv run opspilot serve                     # FastAPI + ADK API server with X-Request-ID, /health, /ready, /runs/{id}
uv run pytest -m live                     # Live Gemini suite (requires GOOGLE_API_KEY or Vertex AI ADC)
```

## Module Layout (OpsPilot AI)

| Module | Responsibility | Framework Imports |
|---|---|---|
| `opspilot/contracts/` | Frozen Pydantic schemas (`ConfigDict(frozen=True)`) for requests, evidence, findings, verification, and reports | **Forbidden** (`no google.adk`) |
| `opspilot/core/` | Pure, deterministic Python: telemetry stats, topology BFS/blast radius, BM25+vector RRF retrieval, hypothesis rules, verification, read-only policy, `<untrusted_context>` sanitization | **Forbidden** (`no google.adk`) |
| `opspilot/adk/` | ADK `LlmAgent`/`SequentialAgent`/`ParallelAgent`/`LoopAgent` tree, narrow `FunctionTool` wrappers, `BudgetPlugin`, `ScriptedLlm`, session runner | Allowed (`google.adk`, `google.genai`) |
| `opspilot/datasets/` | Synthetic network topology, runbook/incident corpus, labelled cases (`C01–C14`, `H01–H10`), deterministic SQLite generator | **Forbidden** |
| `opspilot/eval/` | Evaluation harness, p50/p95 latency & accuracy metrics, guardrail perturbations, verifier matrix | Calls `opspilot.adk.runner` |
| `opspilot/api.py` | Thin FastAPI boundary (`GET /health`, `GET /ready`, `GET /runs/{run_id}`), `BearerTokenAuth`, `X-Request-ID` & latency middleware | Thin adapter |

## Repo-Specific Layering Invariants

1. **Agents Interpret, Deterministic Core Calculates**: All statistics, z-scores, graph traversals, blast-radius tables, RRF retrieval scores, hypothesis candidate rankings, verification checks, and final report statuses (`investigated`, `inconclusive`, `requires_human_review`, `failed`) are computed in `opspilot/core/` — never in prompts.
2. **Immutable Contracts**: Every Pydantic contract and dataclass DTO across `contracts/` and `core/` enforces `ConfigDict(frozen=True)` or `@dataclass(frozen=True)`. Never mutate state or DTOs in-place; use `model_copy(update=...)` or `dataclasses.replace(...)`.
3. **Content-Addressed Evidence & Ground-Truth Isolation**: Every tool registers deterministic `EV-(TEL|TOP|DOC|INC|DQ)-<sha256[:8]>` records in session state (`evidence:<ID>`). Case `labels` and `world` specs never enter SQLite datasets, tool outputs, or prompts.
4. **Untrusted Data Quarantine (`<untrusted_context>`)**: Retrieved documents and external texts are normalized, secret-redacted, scanned for prompt injection, and wrapped in `<untrusted_context id="...">...</untrusted_context>` (with legacy `<<untrusted_document>>` spoofing stripped). Suspected injections are withheld and trigger `security_team` escalation.
5. **Read-Only by Construction**: No tool can mutate infrastructure or files; datasets open with SQLite `mode=ro`; `core/policy.py` strips any non-observational recommendation.

## Planning

Root and every sub-project plan non-trivial work with GSD → Superpowers → Ruflo: see [PLANNING.md](PLANNING.md). Backlog: [TASK.md](TASK.md).
