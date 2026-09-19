# agent_eval_log.md

Self-reflective coding agent iteration log.

## Task: Portfolio feature — expand agent-system into a real multi-step agent

[iter 1] score=6/10 | weakness=executor wires calculator_tool into the graph while it still uses eval(expression) on text derived from LLM plan output, turning a previously-unreachable dead stub into a real remote-code-execution path from the public GET /run?query= endpoint | fix=replace tools/calculator_tool.py's eval() with a restricted ast-based evaluator (whitelisted BinOp/UnaryOp operators, numeric literals only)

[iter 2] score=9/10 | weakness=new tests importing app.graph/app.agents would fail offline: pytest wasn't wired into pyproject.toml at all (uv run pytest fell back to an isolated global tool with none of fastapi/langgraph/openai installed), and app.agents crashes at import time without a real OPENROUTER_API_KEY | fix=add pytest to [dependency-groups].dev in pyproject.toml so `uv sync`/`uv run pytest` use the project venv, and set a placeholder OPENROUTER_API_KEY in tests/conftest.py so app.* imports succeed without a real secret; converged here (24/24 tests passing, full graph verified via FastAPI TestClient) — stopping before the 3-iteration cap.
