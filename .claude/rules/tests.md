---
paths:
  - "tests/**"
---

# OpsPilot Test Suite Rules (`tests/**`)

- **Offline by Default**: Every test run by `uv run pytest` must execute 100% offline using `ScriptedLlm`, `HashingEmbedder`, and synthetic SQLite datasets with zero network calls or cloud credentials.
- **Pytest Markers**:
  - Categorize every test module or test function with `@pytest.mark.unit` or `@pytest.mark.integration`.
  - Mark live Gemini/Vertex AI tests with `@pytest.mark.live` (deselected by default via `addopts = "-m 'not live'"`).
  - Mark full-matrix benchmarks with `@pytest.mark.slow`.
- **Zero Skipped Offline Tests**: Never use `pytest.skip` to hide missing test dependencies or broken assertions in the offline suite. All OTLP tracing tests (`tests/test_tracing.py`) must run and pass offline.
- **Coverage Floor**: Maintain >=80% coverage across every module (`fail_under = 80` in `pyproject.toml`, targeting 95–100% overall).
- **Ground-Truth & Layering Guards**: Never weaken `test_ground_truth_never_stored`, `test_agents_never_receive_ground_truth`, `test_core_and_contracts_never_import_adk`, or `test_cross_agent_tool_use_is_denied`.
