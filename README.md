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

**Status: Phase 7 of 12.** The end-to-end workflow runs with deterministic
verification, bounded retries and investigation rounds, deadlines, timeouts,
and an escalation path. The default LLM is local Ollama, which falls back
*visibly* to a rule-based investigator if Ollama is unreachable. Human
approval, the API, the UI and evaluation reports come in later phases. See
[PLAN.md](PLAN.md).

## Quick start

```bash
uv sync
uv run netpulse investigate --case case-04     # investigate one synthetic case
uv run netpulse investigate --case case-04 --json
uv run netpulse investigate --case case-04 --provider heuristic   # no Ollama needed
uv run pytest
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
| [docs/adr/](docs/adr/) | Architecture decision records |

## License

Proprietary; see [LICENSE](LICENSE).
