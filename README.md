# NetPulse AI

> **SIMULATED DATA.** NetPulse runs entirely on a synthetic network. It has
> never observed or modified a real network, and it is **read-only**: it
> proposes diagnostics and remediation, and it executes nothing.

NetPulse investigates network anomalies with a stateful **LangGraph**
workflow:

- **Detection** is deterministic: thresholds, robust statistics, and
  same-time-yesterday comparison.
- **Topology analysis** covers blast radius and symptom localization.
- **Retrieval** searches runbooks and past incidents, and treats what it
  finds as untrusted.
- **Root-cause hypotheses** are evidence-backed, ranked with rule-based
  ordinal confidence.
- **Uncertain cases** come out as inconclusive reports that list what
  evidence is missing.

**Status: Phase 10 of 12.** The workflow runs end to end with deterministic verification, bounded retries, a policy
engine and **durable human approval** (a run pauses with `interrupt()`, is checkpointed to SQLite, and resumes from
another process). A token-authenticated **FastAPI** service and a **Streamlit** UI sit on top, and an **evaluation
harness** scores detection and investigation against held-out labels, with a CI regression gate. The default LLM is local
Ollama, which falls back *visibly* to a rule-based investigator if Ollama is unreachable. See [PLAN.md](PLAN.md).

## Quick start

```bash
uv sync
uv run netpulse investigate --case case-04     # investigate one synthetic case
uv run netpulse --provider heuristic investigate --case case-04   # no Ollama needed; pauses for approval
uv run netpulse status --incident case-04
uv run netpulse review --incident case-04 --reviewer alice --role operator --choice approve
uv run pytest
uv run python -m eval.report --provider heuristic --check   # evaluation report + regression gate
```

API and UI (set tokens first; see [docs/operations.md](docs/operations.md)), or `docker compose up --build`:

```bash
export NETPULSE_API_TOKENS='op:operator:olga,senior:senior_operator:sam,view:viewer:vic'
uv run uvicorn netpulse.api.main:app --port 8000
NETPULSE_UI_TOKEN=op uv run streamlit run netpulse/ui/app.py
```

## Documentation

| Document | Contents |
|---|---|
| [PLAN.md](PLAN.md) | Phases, design decisions, and the alternatives considered |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, the graph, evidence and approval flows, trust boundaries |
| [docs/graph_workflow.md](docs/graph_workflow.md) | Node contracts, routing, budgets |
| [docs/state_model.md](docs/state_model.md) | State fields, ownership, reducers |
| [docs/synthetic_data.md](docs/synthetic_data.md) | Dataset, scenarios, ground truth |
| [docs/detection.md](docs/detection.md) | Detectors and measured precision/recall |
| [docs/tools.md](docs/tools.md) | Data, topology, and retrieval tool contracts |
| [docs/llm.md](docs/llm.md) | LLM providers, prompts, parsing, verification, budgets |
| [docs/human_approval.md](docs/human_approval.md) | Policy, interrupt/resume, checkpoints, authorization, audit |
| [docs/api_ui.md](docs/api_ui.md) | API endpoints, roles, UI rules |
| [docs/evaluation.md](docs/evaluation.md) | Metrics, regression gate, heuristic baseline, known weaknesses |
| [docs/security.md](docs/security.md) | Threats, controls, and the tests that enforce them |
| [docs/operations.md](docs/operations.md) | Configuration, Docker Compose, logging, CI |
| [docs/adr/](docs/adr/) | Architecture decision records |

## License

Proprietary; see [LICENSE](LICENSE).
