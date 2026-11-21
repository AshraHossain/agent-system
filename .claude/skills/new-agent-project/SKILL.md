---
name: new-agent-project
description: Scaffolds a new self-contained GenAI project in this repository (agent, multi-agent workflow, RAG pipeline or evaluation tool) on any framework, including LangGraph, LangChain, CrewAI, Google ADK, PydanticAI, OpenAI Agents SDK and A2A. Use when the user asks to start, add, bootstrap or prototype a new project, agent system, crew, graph or orchestration, or to port an existing one to another framework.
---

# New agent project

Every project in this repository is independent. A new one gets its own folder and never touches
another project's dependencies. An earlier attempt to make a sub-project pass by joining it to the
root uv workspace broke that project's own lockfile and had to be reverted; do not repeat it.

## 1. Agree the shape before writing code

Ask only for what is missing:

- The task, the inputs and the one artefact the system must produce (a typed report, not chat).
- Framework. If the user has no preference, pick by control flow: fixed pipeline with typed
  hand-offs → Google ADK workflow agents or LangGraph; cycles, branching and human-in-the-loop
  checkpoints → LangGraph; role-based delegation with little custom control flow → CrewAI; a few
  typed tools around one model → PydanticAI; agents that must talk to agents owned by another
  team or runtime → expose them over A2A and keep the internals in any of the above.
- Model access: Gemini through an AI Studio key (`GOOGLE_API_KEY`) or Vertex AI (ADC plus
  `GOOGLE_GENAI_USE_VERTEXAI=1`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`); or another
  provider. Read model ids and regions from the environment; never hard-code them.

Check the framework's current docs for API names before writing against it. Frameworks in this
area change between minor versions; pin the version in `pyproject.toml` and write a small contract
test for every framework behaviour the design relies on (see `tests/test_adk_contract.py`).

## 2. Layout

```
<project>/
  pyproject.toml        # own deps, own [tool.pytest.ini_options], own ruff config
  uv.lock               # generated inside the folder with `uv lock`
  README.md  CLAUDE.md  # CLAUDE.md: commands + the rules that are specific to this project
  src/<pkg>/
    contracts/          # Pydantic models for every hand-off and the final artefact; no framework import
    core/               # calculations, thresholds, scoring, retrieval maths; no framework import
    agents/ (or graph/, crew/)   # the only place that imports the framework
    tools.py            # narrow, validated, read-only wrappers over core/
    models.py           # model factory: mock by default, live by env var
    cli.py              # `demo` (offline) and `eval`
  tests/                # offline by default; live tests marked `live`
.github/workflows/<project>.yml   # path-filtered to `<project>/**`, working-directory: <project>
```

## 3. Build order

1. Contracts first: the final artefact and each agent's output schema.
2. `core/` with unit tests. Anything that is a number, a threshold, a status or a ranking goes
   here, not in a prompt.
3. Tools over `core/`: validated arguments, `{"status": "ok"|"error", ...}` results, no paths,
   URLs, SQL or commands from the model.
4. A deterministic mock model that reads the same prompt and tool results a real model would.
   The whole pipeline, the demo and CI must run on it with no credentials.
5. The orchestration, then a deterministic verifier or finaliser as the last stage: the model
   never sets the final status.
6. Budgets: caps on model calls, tool calls, iterations and wall-clock time; no unbounded retry.
7. An import-guard test that fails if `contracts/` or `core/` imports the framework.
8. Evaluation from day one: follow the `eval-dataset` and `llm-eval` skills.

## 4. Wire it in, and nothing more

- Add the workflow file and one row to the Workspace Projects table in `AGENTS.md`, and to the
  projects table in `README.md`.
- Do not edit the root `pyproject.toml`, `uv.lock`, `conftest.py` or `ci.yml`.

## 5. Verify before reporting done

From inside the folder: `uv sync --frozen`, `uv run ruff check . && uv run ruff format --check .`,
`uv run pytest`, and the offline demo. Then, from the repository root, `uv sync --frozen` must
still install no package that belongs only to the new project.
