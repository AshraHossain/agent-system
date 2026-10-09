# NetPulse AI — Implementation Plan

This document explains **why** NetPulse is built the way it is and which
alternatives were rejected. The structure itself is described in
[ARCHITECTURE.md](ARCHITECTURE.md), and each significant decision has an ADR
in [docs/adr/](docs/adr/).

NetPulse is a demonstration and learning platform that runs on synthetic
data. Passing its own tests and evaluation set does **not** make it
production-ready. Phase 12 is a skeptical readiness review that exists to
say what is still missing.

## 1. Starting point (Phase 1 findings)

| Finding | Consequence |
|---|---|
| The repo is a one-node FastAPI + LangGraph scaffold (`app/`), backed by OpenRouter/OpenAI | Almost nothing is reusable. NetPulse is built as a new `netpulse/` package, and the scaffold is retired (§6). |
| `tools/calculator_tool.py` calls `eval()` on its input | This is a code-execution hazard. It will be deleted with the scaffold and must not be bound into any graph. |
| `CLAUDE.md` still describes the API-key bug as open, but commit `1af9096` fixed it | `CLAUDE.md` is stale. It will be rewritten for NetPulse (Phase 10). |
| `uv.lock` pins `langgraph 1.2.10`, `fastapi 0.141`, `pydantic 2.13`. Current PyPI: `langgraph 1.2.14`, `langgraph-checkpoint-sqlite 3.1.1`, `langchain-ollama 1.1.0`, `streamlit 1.65`, `pandas 3.0` | We use current APIs: `interrupt()` / `Command(resume=…)` and `SqliteSaver`. Versions are pinned through `uv.lock`. |
| There is no Ollama daemon in this build container, and no GPU | Default tests use a mocked LLM. Local-model tests are a separate opt-in profile (`-m ollama`). Ollama behavior cannot be verified in CI. |
| Python 3.13 locally, Dockerfile on 3.11 | Target `>=3.11`. CI tests on 3.11 and 3.13. |
| `LICENSE` is proprietary | Unchanged. All synthetic data and runbooks are authored in-repo, so there are no third-party data licenses. |

## 2. Key decisions, with reasons and alternatives

### 2.1 One LangGraph `StateGraph` with mostly deterministic nodes — ADR-0001
**Why.** The workflow has a known shape: gather evidence, infer, verify,
decide, report. A single explicit graph makes every transition visible,
testable as a pure router function, and checkpointable. Only hypothesis
generation actually needs language reasoning.
**Alternatives.**
- A *multi-agent supervisor* (planner, telemetry, topology, and RCA agents):
  rejected. It multiplies LLM calls, makes control flow emergent rather than
  specified, and is hard to bound or test deterministically.
- A *ReAct tool-calling agent*: rejected for the default path. Tool order
  would be chosen by the model, so a missing tool call would silently lose
  evidence. ReAct may be reconsidered later only for the optional "suggest
  extra diagnostics" step.
- *Plain Python without LangGraph*: simpler, but we would have to
  reimplement checkpointing, interrupt/resume, and step limits.

### 2.2 Deterministic detection kept separate from LLM reasoning — ADR-0002
**Why.** Detection must be reproducible, measurable (precision/recall), and
immune to hallucination. Detectors write immutable `EvidenceItem`s. The LLM
only reads summaries and cites IDs.
**Alternatives.** Asking the LLM to "spot anomalies" in raw series:
rejected. It is non-reproducible, it is expensive with long contexts, and
it fabricates numbers. ML detectors (Isolation Forest, Prophet): deferred.
The robust statistics below are explainable and good enough on synthetic
data. Isolation Forest can be added later as a fifth detector behind the
same interface.

**Detectors.**
- Static thresholds, configured per metric.
- A rolling baseline (median over a trailing window).
- A robust z-score (median/MAD, with |z| ≥ 3.5 as the default).
- A window comparison (incident window vs. the same-length window one day
  earlier, by median ratio).

Consecutive flagged points are merged into one `Anomaly`. Missing points are
never imputed silently. The detector reports a coverage ratio, and gaps
become `ev-dq-*` evidence.

### 2.3 JSON-only state plus an immutable evidence registry — ADR-0003
**Why.** Checkpoints must survive restarts and code changes. Plain JSON
avoids pickle/msgpack type-allowlist problems. A single registry keyed by
evidence ID gives the verifier one thing to check, and it lets the
evaluation compute citation validity mechanically.
**Alternatives.**
- Pydantic objects in state: rejected. It couples checkpoints to class
  definitions and needs serializer allowlisting.
- Storing raw telemetry in state: rejected. It bloats checkpoints and prompts.

### 2.4 SQLite checkpointer + `interrupt()` for human approval — ADR-0004
**Why.** This is LangGraph's supported pause/resume mechanism. The pending
approval lives on disk, keyed by `thread_id = incident_id`, so it survives
API restarts. SQLite needs no extra service, and it is listed in the spec.
**Alternatives.**
- `MemorySaver`: rejected for anything except unit tests. A paused run would
  be lost on restart, which would look like a durable approval without being
  one.
- Postgres (`langgraph-checkpoint-postgres`): the right choice for
  multi-replica deployments. Documented as the production upgrade path.
- A custom "pending approvals" table that rebuilds the graph by hand:
  rejected because it duplicates what the checkpointer already does.

**Known caveat.** SQLite allows one writer at a time. The API therefore runs
investigations in a bounded thread pool, with one checkpointer connection
per process. That is fine for a demo and is not horizontally scalable.

### 2.5 Ollama by default, behind a small provider protocol — ADR-0005
**Why.** The spec requires no paid services. The adapter uses
`langchain-ollama`'s `ChatOllama` with JSON-schema structured output. Two
additional providers implement the same `HypothesisGenerator` protocol:
- `FakeLLM`: scripted outputs, including malformed, injected, and
  hallucinated ones, for deterministic tests.
- `heuristic`: a rule-based investigator that maps anomaly and topology
  patterns to `RootCauseCategory`. It is the CI and eval baseline, and it is
  the fallback when Ollama is unavailable. Using it is reported explicitly,
  never silently.

**Alternatives.**
- Hosted APIs (OpenAI/OpenRouter/Anthropic): allowed later as optional
  providers behind the same protocol. Never the default.
- Raw HTTP calls to Ollama: workable, but `langchain-ollama` gives
  structured output and timeouts with less code.

**Model default.** `qwen2.5:7b-instruct`, configurable through
`NETPULSE_OLLAMA_MODEL`. Small local models are unreliable at structured
output. The verifier and the bounded retry exist for that reason, and the
eval measures the effect.

### 2.6 Lexical retrieval (BM25) instead of a vector database — ADR-0006
**Why.** The corpus is small (12 runbooks and 40 past incidents) and
full of identifiers such as `core-1`, `BGP`, and `CRC errors`, which keyword
search handles well. BM25 is deterministic and adds no embedding model.
Being deterministic makes retrieval testable, including "irrelevant
historical incident" cases.
**Alternatives.**
- Chroma/FAISS with Ollama embeddings: possible later behind the same
  `Retriever` interface. It adds non-determinism and a model download.
- LLM-based reranking: rejected because it puts the LLM into evidence
  selection.

### 2.7 Ordinal, rule-based confidence — ADR-0007
**Why.** The spec forbids unjustified numerical confidence. A deterministic
ranker assigns `high`, `medium`, `low`, or `insufficient_evidence` from
counts of trusted support and contradictions (docs/graph_workflow.md
§Sufficiency), and it writes a rationale that cites evidence.
**Alternatives.** LLM self-rated probabilities: rejected as uncalibrated.
Calibrated scores trained on the eval set: rejected because they would leak
evaluation labels into the system.

### 2.8 Static action catalog plus a deterministic policy engine
**Why.** The LLM never invents actions. `recommend_actions` selects entries
from `netpulse/policy/catalog.yaml`. Each entry carries a fixed `kind`
(diagnostic or remediation), `reversible`, and `required_role`. The policy
engine checks severity, service impact, evidence sufficiency, reversibility,
blast radius, and required role. There is no executor module at all, so the
read-only guarantee is structural, not a matter of discipline.
**Alternative.** LLM-proposed free-form actions with an LLM safety reviewer:
rejected because the reviewer could be fooled by the same injection.

### 2.9 Evaluation isolated from the investigator
**Why.** Label leakage would invalidate every metric. The setup is:
- `cases.jsonl` holds only what an operator would submit;
- `labels.jsonl` sits in a separate directory;
- `netpulse/data` has no code path to that directory;
- a test monkeypatches file access during an eval run and fails if
  `labels/` is opened before scoring;
- the past-incident corpus is generated from different seeds and scenarios
  than the eval cases, and is checked for overlap.

## 3. Synthetic domain (Phase 3, implemented)

Full description: [docs/synthetic_data.md](docs/synthetic_data.md).

- **Topology.** 18 monitored nodes (1 provider edge, 2 firewalls, 2 core,
  1 DNS host, 4 aggregation, 8 access), plus the unmonitored upstream
  `isp-transit`. 25 links, and 5 services with dependency lists and probe
  paths.
- **Telemetry.** 5-minute samples over 30 hours per case (24 hours of
  history, then 6 hours containing the incident). Tables: links, nodes, and
  service probes. Each row carries `delay_s`, so late-arriving data can be
  modelled.
- **Ground truth without hand-labelling.** Each case is rendered twice from
  the same noise draw, with and without its faults. A sample is anomalous
  when the faulted value departs materially from the fault-free value. This
  captures knock-on effects such as congestion raising latency and loss, and
  it never labels pure noise.
- **Scenarios.** 20 cases covering every category in the brief, plus all 8
  required difficult cases.
- **Documents.** 12 runbooks: RB-001 and RB-002 conflict, and RB-012 carries
  an unmarked prompt injection. 40 past incidents dated 2025, including
  look-alike `dns-1` records with different causes.
- **Generator location.** `synthgen/` is a top-level package, deliberately
  **outside** `netpulse/`, because it contains ground truth. Tests assert
  that `netpulse` never imports it and never references the labels path.
- **Format.** Gzip CSV (mtime zeroed) and sorted-key JSON. The output is
  byte-for-byte deterministic. `MANIFEST.json` records the seed, the
  generator version, and SHA-256 hashes. `python -m synthgen.generate
  --check` verifies the committed data.

Every dataset file, API response, report, and UI page carries the
`SIMULATED DATA` notice.

## 4. Evaluation design (Phase 10)

| Metric | Layer | Definition |
|---|---|---|
| Anomaly precision / recall | detection | Interval match per (entity, metric), with ±2 min tolerance |
| Root-cause top-1 / top-3 | investigation | `cause_category` + `suspected_root_entity` match in the ranked list |
| Evidence citation validity | investigation | Share of cited IDs that exist in the registry and concern the hypothesis's entities |
| Unsupported-claim rate | investigation | Share of hypotheses rejected by the verifier, or with only untrusted support |
| Recommendation relevance | investigation | Jaccard overlap and recall between recommended catalog IDs and the labeled acceptable set |
| Escalation correctness | control | Precision/recall of `escalated or inconclusive` against labeled `should_escalate` |
| Tool-call success rate | system | Successful tool calls / total tool calls, from `node_trace` and `errors` |
| End-to-end latency | system | p50/p95 per case, excluding human-wait time |
| Cost | system | Token counts when the provider reports them. $0 for local and heuristic providers. |

Detection metrics are reported separately from investigation metrics. Every
eval run is executed for the `heuristic` provider (CI-gated regression
thresholds) and for `ollama` when it is available (reported, not gated). The
report is `eval/reports/<dataset>-<provider>-<git-sha>.{json,md}`, and it
includes per-case failure analysis.

## 5. Phases

Each phase ends with a report covering files changed, decisions, tests run,
actual results, limitations, and remaining work. **Each phase waits for
approval before the next starts.**

| Phase | Deliverable | Tests added |
|---|---|---|
| 1 ✅ | Repo and environment inspection | — |
| 2 ✅ | This plan, ARCHITECTURE.md, workflow and state docs, ADRs 0001–0007, typed state model | State and reducer contract tests |
| 3 ✅ | `synthgen/` generator, generated `data/synthetic/v1`, runbooks, past incidents, `eval/datasets/v1` cases and labels, manifest | Reproducibility checksum, schema validity, label/corpus separation |
| 4 ✅ | `netpulse/detection` (baseline, threshold, robust z, window comparison, plausibility, gap handling, consolidation) and `eval/detection_benchmark.py` — see docs/detection.md | Unit tests per algorithm, including noise, gaps, and flat series |
| 5 ✅ | `netpulse/data` (visibility-aware store), `netpulse/topology` (paths, blast radius, localization, change context), `netpulse/retrieval` (BM25, sanitization, conflicts) — see docs/tools.md | Visibility, gaps, traversal safety, blast radius, localization, retrieval ranking, injection flags, conflicts |
| 6 ✅ | Linear graph intake → … → report with the heuristic investigator, node wrapper (trace and errors), fatal → failure report, rule-based ranking, diagnostics-only recommendations, CLI; legacy scaffold removed | Node unit tests, ranking rules, end-to-end including insufficient-evidence and failure paths |
| 7 | Verifier, ranker, routing, retry and round budgets, deadline, failure report, Ollama adapter + FakeLLM | Routing tables, retry budget, tool failure, insufficient evidence → inconclusive |
| 8 | SQLite checkpointer, `interrupt`/resume, policy engine, action catalog | Checkpoint and resume across a fresh process, approve/reject/more-investigation, authz |
| 9 | FastAPI endpoints, Streamlit UI | API tests (TestClient), UI smoke import |
| 10 | Eval harness and report, structured logging, optional LangSmith, Dockerfile + Compose (api, ui, optional ollama), GitHub Actions (ruff, pytest, eval regression), docs/evaluation, security, human_approval, operations, README, CLAUDE.md | Eval regression, security (injection, label leakage, secret redaction) |
| 11 | Full end-to-end runs, failure investigation | Fixes plus regression tests |
| 12 | Skeptical production-readiness review | — |

## 6. Repository migration (done in Phase 6)

The original scaffold has been removed: the `app/` OpenRouter planner, the
`tools/` directory (including the `eval()` calculator), `memory/`,
`requirements.txt`, `PLANNING.md`, `TASK.md` and `plugins/`.
`tests/test_repository.py` guards against reintroducing dynamic code
execution.

## 7. Risks and open questions

| Risk | Mitigation |
|---|---|
| Small local models produce invalid JSON or ignore citation rules | Schema-constrained output, verifier, bounded retry, and an eval that measures the failure rate. The heuristic baseline shows what the LLM adds. |
| Synthetic data is too easy, so metrics look inflated | Hard cases (§3), a noise sweep, and the report states plainly that results on synthetic data do not transfer to real networks |
| The heuristic investigator overfits the generator | It is written against the scenario *taxonomy*, not specific cases. The eval reports it as a baseline, not as the product. |
| SQLite write contention under concurrent runs | Bounded worker pool, documented Postgres path |
| `interrupt()` re-executes the node on resume | Approval node is side-effect-free before `interrupt()`. Tested. |
| The plugins named in the request (Caveman, ponytail, gsd, graphify, superpowers, ruflo) are not installed in this environment | Work proceeds with standard tooling. Their workflows can be applied locally where they are available. |
