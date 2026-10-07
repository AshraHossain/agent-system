# agent_eval_log.md

Self-reflective coding agent iteration log.

## Task: Portfolio feature — expand agent-system into a real multi-step agent

[iter 1] score=6/10 | weakness=executor wires calculator_tool into the graph while it still uses eval(expression) on text derived from LLM plan output, turning a previously-unreachable dead stub into a real remote-code-execution path from the public GET /run?query= endpoint | fix=replace tools/calculator_tool.py's eval() with a restricted ast-based evaluator (whitelisted BinOp/UnaryOp operators, numeric literals only)

[iter 2] score=9/10 | weakness=new tests importing app.graph/app.agents would fail offline: pytest wasn't wired into pyproject.toml at all (uv run pytest fell back to an isolated global tool with none of fastapi/langgraph/openai installed), and app.agents crashes at import time without a real OPENROUTER_API_KEY | fix=add pytest to [dependency-groups].dev in pyproject.toml so `uv sync`/`uv run pytest` use the project venv, and set a placeholder OPENROUTER_API_KEY in tests/conftest.py so app.* imports succeed without a real secret; converged here (24/24 tests passing, full graph verified via FastAPI TestClient) — stopping before the 3-iteration cap.

## Task: Implement memory/store.py (next TASK.md item, same feature line)

[iter 1] score=9/10 | weakness=an in-place/mutable store (e.g. a single JSON object rewritten per run) risks corrupting history if two /run calls ever overlap, and a database dependency would be over-engineering for a single-file scaffold with no concurrent-write requirement yet | fix=append-only JSON-Lines (data/runs.jsonl): each save_run() call opens in "a" mode and writes one line, so concurrent writers can't clobber prior records, and load_run/list_runs just scan lines — no new dependency, no mutation. Converged on iteration 1: tests redirect STORE_PATH to a tmp_path so they never touch the real data/ dir, and a manual TestClient smoke test confirmed GET /run now returns a run_id and the record round-trips byte-for-byte (27/27 tests passing).

## Task: Add conditional edges for agent robustness and error handling

[iter 1] score=9/10 | weakness=the current graph follows a fixed linear pipeline (planner → executor → synthesizer); if a tool call fails (e.g., division by zero), the agent proceeds unchanged rather than attempting to recover. For a portfolio agent, the ability to detect errors and re-plan shows sophistication | fix=extend AgentState with `errors: List[str]` and `attempts: int` tracking; modify executor to detect tool errors (calculator_tool returns "Error in calculation") and populate errors list; add conditional_edges from executor that route to synthesizer on success or back to planner on error (up to 3 retry attempts to prevent infinite loops). Added one new test (test_run_executor_detects_errors) to verify error detection. Converged on iteration 1: all 28 tests passing, conditional routing verified end-to-end with mocked planner.

## Task: Replace regex-based tool routing with LLM-driven decisions

[iter 1] score=9/10 | weakness=regex-based routing (e.g., looking for "+" in text) is brittle and can't handle natural language like "multiply these two numbers". Routing should understand intent, not just patterns. For a portfolio agent, LLM-driven routing shows sophistication and real agentic capability | fix=add `route_step_to_tool(step: str)` function in `app/agents.py` that calls the LLM to categorize steps as "calculator", "search", or "passthrough"; update `execute_step` to use this instead of regexes; enhance `calculator_tool` to extract and handle nested arithmetic from prose (e.g., "Compute 3 + 4" → 7, "(2 + 3) * 4" → 20). Updated all graph tests to mock the LLM router. Added `test_calculator_tool_extracts_from_prose` to verify prose handling. Converged on iteration 1: all 29 tests passing (up from 28), LLM routing verified with mocked calls throughout.
