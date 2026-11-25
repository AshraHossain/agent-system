# Implementation Handoff — Engineering Process, Graph, Test & Eval Blueprint

This document captures the implementation architecture, planning workflow, knowledge-graph cadence, test/verification structure, and live-optimization playbook established in **OpsPilot AI** so the same pattern can be replicated across other projects in `agent-system` (`platewatch/`, `traffic-flow-analyzer/`, `ledger-hound/`, `api-tests/`, and future services).

---

## 1. Layered Module Architecture (Copy-Ready Pattern)

Every agentic project in this workspace should enforce a strict separation between **contracts**, **deterministic core**, **agent/framework glue**, and **HTTP/CLI boundaries**:

| Layer | Directory / File | Responsibility | Framework Imports |
|---|---|---|---|
| **Contracts** | `<pkg>/contracts/` | Frozen Pydantic schemas (`ConfigDict(frozen=True)`), bounded strings/lists, Gemini-compatible output schemas | **Forbidden** |
| **Pure Core** | `<pkg>/core/` | Deterministic math, z-scores, graph traversal, retrieval (BM25 + RRF), rule-based ranking, verification, policy gates, `<untrusted_context>` sanitization | **Forbidden** |
| **Agent Boundary** | `<pkg>/adk/` (or `agents/`) | Framework orchestration (`SequentialAgent` / `ParallelAgent` / `LoopAgent`), narrow `FunctionTool` allowlists, `BudgetPlugin`, `ScriptedLlm` / mock policies | Allowed |
| **Datasets / Eval** | `<pkg>/datasets/`, `<pkg>/eval/` | Deterministic synthetic generators, labelled `main` + `heldout` splits, guardrail perturbations, verifier red-team matrix, `--repeat N` harness | Eval calls runner |
| **API / CLI Edge** | `<pkg>/api.py`, `<pkg>/cli.py` | Thin FastAPI boundary (`BearerTokenAuth`, `RateLimitMiddleware`, `InputBoundaryMiddleware`, `TelemetryMiddleware`, `/health`, `/readyz`, `/runs/{id}`) | Thin adapter |

### Non-Negotiable Invariants
1. **Agents Interpret, Core Calculates**: Thresholds, rankings, confidence ceilings, status decisions, and read-only policy enforcement live in `core/`, never in prompt prose.
2. **Immutability**: Use `ConfigDict(frozen=True)` or `@dataclass(frozen=True)` everywhere; update via `model_copy(update=...)` or `dataclasses.replace(...)`.
3. **Ground-Truth Isolation**: Case labels and world specs never enter SQLite datasets, tool outputs, or prompts.
4. **Untrusted Data Quarantine**: External text/documents are normalized, secret-redacted, stripped of spoofed delimiter tags, and wrapped in `<untrusted_context id="...">...</untrusted_context>`.

---

## 2. Planning & Rule Governance (`GSD → Superpowers → Ruflo`)

Canonical process definition: [PLANNING.md](../PLANNING.md).

### Required Project Files
- **`AGENTS.md` / `GEMINI.md` / `CLAUDE.md`**: Verified commands, module layout, layering invariants, and commit conventions.
- **`PLAN.md` & `ARCHITECTURE.md`**: Design rationale, phase table, and dual Mermaid diagrams in `README.md` (`flowchart LR` layered system + `flowchart TD` orchestration + `sequenceDiagram` end-to-end flow).
- **`TASK.md`**: Live backlog (`Done`, `High priority`, `Medium priority`, `Low priority`) updated as phases complete.
- **`docs/adr/`**: Architecture Decision Records for non-obvious trade-offs (e.g., embedder caching, OTLP dependency constraints, bearer auth).
- **`.agents/rules/`** (path-scoped guardrails):
  - `contracts.md`, `core.md`, `adk.md`, `tests.md`
- **`.agents/skills/`** (repeatable checklists):
  - `add-llm-agent`, `eval-dataset`, `llm-eval`, `red-team`, `new-agent-project`

### Three-Layer Workflow
1. **GSD (Roadmap)**: Track milestones and phases per project (`TASK.md` + `PLAN.md`).
2. **Superpowers (Task Plan)**: `brainstorming` → `writing-plans` (explicit files, tests, acceptance command).
3. **Ruflo / Execution**: Parallel builders on disjoint files when tasks are independent, or `executing-plans` + TDD (`RED → GREEN → IMPROVE`) sequentially, followed by `verification-before-completion` (`make check` / `make check-all`) and code review before merging to `master`.

---

## 3. Knowledge Graph (`graphify-out/`)

OpsPilot maintains a local structural knowledge graph in `graphify-out/` (`GRAPH_REPORT.md`, `graph.json`, `graph.html`, `manifest.json`, `cache/`, `cost.json`).

- **When to update**: Refresh `graphify-out/` locally after adding/refactoring modules or merging a phase.
- **Why it matters**: Gives agents and reviewers an explicit map of module dependencies and architectural edges without scanning the entire codebase from scratch.

---

## 4. Test Structure & Quality Gates

Reference configuration: [pyproject.toml](../pyproject.toml) and [.agents/rules/tests.md](../.agents/rules/tests.md).

### Pytest Organization
- **Default Offline Execution**: `addopts = "-m 'not live' -p no:cacheprovider"` ensures `uv run pytest` never makes network calls or requires cloud keys.
- **Markers**:
  - `@pytest.mark.unit` — pure core, contracts, security, policy, retrieval, verification
  - `@pytest.mark.integration` — ADK pipeline with `ScriptedLlm`, SQLite sessions, FastAPI boundary, OTLP tracing
  - `@pytest.mark.live` — real Gemini / Vertex AI / OpenRouter smoke & eval checks (opt-in)
  - `@pytest.mark.slow` — full-matrix benchmarks
- **Coverage & Linting**:
  - Branch coverage enabled (`branch = true`), `fail_under = 80` (OpsPilot actual: **95.58%** across **493** offline tests).
  - Zero skipped offline tests (`pytest.skip` is not used to hide broken/missing dependencies).
  - Ruff lint + format (`line-length = 100`, `select = ["E", "F", "W", "I", "B", "UP", "S", "SIM", "RUF"]`).

### Structural Invariant Tests to Replicate
Every new agent project should include equivalent guard tests:
- `test_core_and_contracts_never_import_adk` (AST/import check enforcing framework isolation)
- `test_ground_truth_never_stored` & `test_agents_never_receive_ground_truth`
- `test_cross_agent_tool_use_is_denied` (per-agent tool allowlists)
- Dedicated security boundary tests (`tests/test_security.py`, `tests/test_security_e2e.py`, `tests/test_auth.py`)

### Single-Command Verification Gates (`Makefile`)
- **Single project**: `make check` (`ruff check` + `ruff format --check` + `pytest --cov` + `eval --guardrails`).
- **Workspace-wide**: `make check-all` (runs `make check` plus `platewatch`, `traffic-flow-analyzer`, `ledger-hound`, and `api-tests`).

---

## 5. Evaluation & Live Optimization Playbook

OpsPilot uses a 3-tier evaluation ladder so prompt or schema changes are never evaluated blindly:

1. **Offline Pipeline + Guardrails (`make eval`)**:
   - `OPSPILOT_MODEL_PROVIDER=mock uv run opspilot eval --guardrails --out var/eval`
   - Verifies deterministic pipeline, citation validity, and 10 injected LLM failure perturbations (`guardrail_detection_rate: 1.0`).
2. **Verifier Red-Team Matrix (`opspilot eval --verifier-matrix`)**:
   - Injects planted defects across all 24 cases (`main` + `heldout`) vs. clean runs (`93/93` caught, `0/45` false positives).
   - Deterministic blind spots (e.g., `paraphrased_certainty`) are documented explicitly and replayed against the live LLM reviewer.
3. **Multi-Repetition Live Evaluation (`--repeat 3`)**:
   - Command: `[ -f ~/.secrets ] && source ~/.secrets && OPSPILOT_MODEL_PROVIDER=openrouter uv run opspilot eval --repeat 3 --out var/eval`
   - Records mean/min/max/stdev, token usage, latency (`p50`/`p95`), and **per-agent output compliance** (`schema retries`, `text answers`, `fallbacks`).

### Proven Optimization Loop (From `7c214e1` → `dc7e66c`)
When live evaluation reveals bottlenecks, follow this exact sequence:
1. **Instrument per-agent compliance first**: Identify which specific agents trigger schema retries, text-only answers, or budget fallbacks.
2. **Fix contract/prompt friction without weakening invariants**:
   - In `opspilot/contracts/findings.py`, relaxing tight `max_length` bounds (`DocReference.note` / `DocIssue.issue` `300 -> 600`, `ReviewConcern.description` `400 -> 800`) removed validation rejections while keeping schemas bounded and frozen.
   - In `opspilot/adk/prompts.py`, instructing `TOPOLOGY` to batch calls (up to 3 shared components) and `KNOWLEDGE`/`REVIEWER` to stay concise eliminated retry loops.
3. **Verify blind spots live**: Replay `paraphrased_certainty` against live `review_verifier` to confirm semantic catching of plain-assertion overconfidence and mutating recommendations.
4. **Re-run `--repeat 3` & compare before/after**:
   - `completion_rate`: `0.9487` → **`1.0`**
   - `status_accuracy`: `0.2051` → **`0.5385`** (next target: `0.85+` by tuning over-escalation on `C01`/`C03`/`C05`/`C08` and `C07`)
   - `knowledge_researcher` clean: `15/39` → **`39/39`**; `review_verifier` clean: `24/39` → **`39/39`**; `topology_analyst` clean: `30/39` → **`38/39`**
   - Mean latency: `35.9s` → **`22.5s`** (`-37%`); tokens per pass: `1,408,830` → **`922,881`** (`-34%`).

---

## 6. Sub-Project Replication Checklist

When upgrading `platewatch/`, `traffic-flow-analyzer/`, `ledger-hound/`, `api-tests/`, or scaffolding a new project via `.agents/skills/new-agent-project/SKILL.md`:

- [ ] Keep `pyproject.toml`, `uv.lock`, `PLAN.md`, `TASK.md`, `SECURITY.md`, and `.github/workflows/<name>.yml` strictly independent.
- [ ] Separate `contracts/` (frozen), `core/` (pure deterministic), `agents/` (thin LLM glue), and `api.py` (auth + rate-limit + input boundary + OWASP headers).
- [ ] Include dual Mermaid diagrams (`flowchart LR` + `sequenceDiagram`, plus `flowchart TD` if multi-agent) in the sub-project's `README.md`.
- [ ] Enforce offline-by-default `pytest` with deterministic mocks, `>=80%` coverage gate, and `ruff check` + `ruff format --check`.
- [ ] Add an evaluation harness (`--repeat N` + guardrail perturbations) and record baseline vs. optimized results in `docs/`.
- [ ] Wire the sub-project into root `Makefile` (`check-subprojects`) and verify with `make check-all`.
