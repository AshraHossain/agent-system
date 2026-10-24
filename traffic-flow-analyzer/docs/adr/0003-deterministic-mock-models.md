# ADR-0003: Deterministic offline models via FunctionModel

**Status:** Accepted

## Context
Tests, CI and demos must run without credentials or network and give stable results.

## Decision
`agents/mock_models.py` provides one scripted `FunctionModel` policy per agent role. They
call the real tools and emit schema-valid output. Default model name is `mock`; any
`provider:model` string via `TRAFFICFLOW_MODEL` selects a live model.

## Consequences
* The offline suite verifies tools, orchestration, verification and the UI, **not** language
  quality. Live behaviour is unmeasured until a live validation is run (see TASK.md).
* The mock writer decides what is notable by matching words in analyst notes. That is
  brittle and exists only for the offline path.
* Never assert exact live-model output in tests.
