# CLAUDE.md — agent-system

A workspace of independent GenAI projects: agents, multi-agent orchestration, RAG, LLM
evaluation and red teaming. Nothing here is production-ready; never claim it is.

## Projects

| Path | What | Stack | Detail |
|---|---|---|---|
| `opspilot/`, `adk_apps/`, `tests/` (repo root) | Multi-agent network-ops investigation, evidence-linked report | Google ADK 2.11.0 (pinned), Gemini / Vertex AI | `.claude/rules/opspilot.md`, [PLAN.md](PLAN.md), [ARCHITECTURE.md](ARCHITECTURE.md) |
| `ledger-hound/` | Agentic QA / red team of a Purchase-to-Pay API | OpenAI Agents SDK, DSPy, FastAPI | its README |
| `traffic-flow-analyzer/` | Congestion forecasting agents + dashboard | PydanticAI, Gradio | its CLAUDE.md |
| `platewatch/` | Licence-plate detection, tagging, vector search | PydanticAI, Hugging Face | its CLAUDE.md |
| `api-tests/` | Black-box API tests for the Traffic Flow API | pytest, httpx, schemathesis | its CLAUDE.md |

Each sub-project has its own `pyproject.toml`, `uv.lock`, tests and a path-filtered workflow in
`.github/workflows/`. Run its commands from inside its folder.

## Root commands (OpsPilot)

```bash
uv sync                                   # uv only; never raw pip
uv run pytest                             # offline suite; live tests are excluded by default
uv run ruff check . && uv run ruff format --check .
uv run opspilot demo --case C03           # end-to-end, mock model
uv run opspilot eval --guardrails         # evaluation -> var/eval/
uv run pytest -m live                     # needs GOOGLE_API_KEY (AI Studio) or Vertex AI ADC
```

## Rules for every project

- **Projects stay independent.** No project imports another. Never add a sub-project to the root
  uv workspace, the root `pyproject.toml`, the root CI job or a root `conftest.py` to make
  something pass; fix it inside the project.
- **Agents interpret, code calculates.** Numbers, thresholds, status, ranking and escalation are
  deterministic Python in a framework-free `core/`; `contracts/` and `core/` never import the
  agent framework. Never move a calculation into a prompt.
- **Tools are narrow.** Validated arguments, read-only unless the project says otherwise, and no
  paths, URLs, SQL or commands taken from the model.
- **Offline by default.** Every project runs and tests with a deterministic mock model and no
  credentials. Live-model tests carry the `live` marker. Never assert a live model's exact output.
- **Ground truth never reaches an agent.** Labels, answer keys and held-back data are read only by
  generators and evaluation code; keep the tests that guard this.
- **Report results as measured.** State the model, the number of runs and the spread. A mock result
  measures the pipeline, not the model. Never skip, loosen or delete a test to get green.
- **Secrets** come from the environment or ADC; never commit keys or put them in prompts or logs.
- Conventional commits: `feat|fix|test|refactor|docs|chore(scope): ...`.

## Where the procedures are

Multi-step work is in skills under `.claude/skills/` (also readable by Antigravity through
`.agents/skills/`): `new-agent-project`, `add-llm-agent`, `llm-eval`, `eval-dataset`, `red-team`.
