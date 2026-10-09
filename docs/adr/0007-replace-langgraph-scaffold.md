# ADR-0007: Replace the LangGraph/OpenRouter scaffold

**Status:** Accepted (approved by repository owner)

The previous `app/` (FastAPI + one-node LangGraph + OpenRouter), `tools/`
(including an `eval()`-based calculator — arbitrary code execution) and
`memory/` were removed. They remain in git history (commit `2dc359e`).
