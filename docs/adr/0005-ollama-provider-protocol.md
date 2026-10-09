# ADR-0005: Ollama default behind a provider protocol, plus a heuristic baseline

**Context.** The default workflow must not require paid services. Tests must
be deterministic.

**Decision.** Define a `HypothesisGenerator` protocol with three
implementations:
- `OllamaGenerator` (`langchain-ollama` `ChatOllama`, JSON-schema output,
  timeout);
- `FakeGenerator` (scripted, for tests);
- `HeuristicGenerator` (rule-based, for CI and eval baseline).

The provider is chosen by `NETPULSE_LLM_PROVIDER`. Ollama-backed tests carry
`@pytest.mark.ollama` and are skipped unless enabled.

**Alternatives.** Hosted APIs as the default (paid, data egress); raw HTTP
calls (more code).

**Consequences.** CI never exercises a real model. LLM quality is measured
only in local eval runs, and reports always state which provider produced
them.
