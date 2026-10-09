# ADR-0001: Google ADK as the agent framework

**Status:** Accepted

## Context
The project mandate is to demonstrate correct use of Google's Agent Development
Kit for multi-agent orchestration with typed outputs, tools, callbacks,
sessions and evaluation.

## Decision
Use `google-adk[db]==2.11.0` (latest at implementation time, verified by
installing it and probing its APIs). Pin exactly, because 2.x is young and
adds APIs (graph `Workflow`, `App`, resumability) between minor versions.

## Consequences
* + One framework covers agents, tool loops, schema validation, callbacks,
  plugins, session persistence, dev UI and eval CLI.
* + `LlmAgent.model` accepts a `BaseLlm` instance → deterministic offline mock.
* − Framework coupling; mitigated by keeping business logic in `opspilot/core`.
* − Upgrades need re-verification (`tests/test_adk_contract.py` pins the
  behaviours we rely on: `set_model_response`, `output_key`, state deltas,
  `on_model_error_callback` fallback).
