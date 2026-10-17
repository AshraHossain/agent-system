# ADR-0001: PydanticAI agents over a deterministic core

**Status:** Accepted

## Context
The idea called for PydanticAI agents on a traffic time-series problem. Forecasts and
thresholds must be reproducible and testable; language models are good at choosing tools and
writing prose, not at arithmetic.

## Decision
All numbers (forecast, intervals, congestion index, incident detection, status) are computed
in framework-free `core/`. PydanticAI agents only call read-only tools over that core and
write typed, cited outputs. `core/` and `contracts/` must not import `pydantic_ai`.

## Consequences
* The forecaster can be tested and backtested without any model.
* An LLM cannot invent a forecast or set the network status; the worst it can do is
  mis-describe tool results, which `verify_brief` partly catches (citations only, today).
* Swapping the agent framework would not touch `core/`.
