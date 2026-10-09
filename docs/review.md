# Skeptical architecture review (Phase 12)

Reviewed at commit `5b9bfba` + docs. Scope: does the system do what the
acceptance criteria require, and where is it weaker than it looks?

## Verdict

OpsPilot is a **credible, well-bounded prototype** of an ADK multi-agent
investigation pipeline with strong deterministic guardrails. It is **not
production-ready**, and its offline evaluation says little about how well a
live model would investigate real incidents.

## Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| Google ADK as primary framework | Met | `opspilot/adk/agents.py`; ADK behaviour pinned in `tests/test_adk_contract.py` |
| Synthetic investigation end to end | Met | `opspilot demo`, `tests/test_orchestration.py::test_end_to_end_matches_labels[*]`, REST run in Docker |
| Meaningful agent specialization | Partly | Distinct tools/contracts per agent; but offline, LLM agents follow scripted policies, so the *language* value-add is unmeasured |
| Deterministic tools for calculations | Met | `opspilot/core/*`; no arithmetic in prompts |
| Validated structured outputs | Met | ADK `output_schema` + re-validation in deterministic stages |
| Traceable evidence | Met | content-addressed `evidence:*` registry; citation validity 1.0 offline |
| Detect unsupported conclusions | Met (for defined checks) | verifier + 8/8 perturbations detected |
| Missing evidence / tool failures | Met | C06, C11, C12, failure tests |
| Bounded execution | Met | budgets, per-agent caps, wall clock, size limits; `tests/test_failures.py` |
| Measurable evaluation | Met offline; live not run | `docs/eval_results_offline.md` |
| Deterministic tests without live access | Met | 162 offline tests, `-m live` separate |
| Live prerequisites documented | Met | `docs/model_configuration.md` |
| Reproducible local setup | Met | `uv sync`, seeded generator, Docker image built and smoke-tested |

## Weaknesses, in order of importance

1. **Offline evaluation is circular.** The hypothesis rules, the mock policies
   and the case labels were designed together; perfect offline scores are
   expected by construction. They prove the pipeline is wired and guarded
   correctly, not that the approach finds real root causes. The guardrail
   perturbations are more informative but were also authored by us.
2. **The live Gemini path is unverified.** No credentials were available.
   Unknowns: `set_model_response` compliance for every schema, response-schema
   size/feature limits, whether per-agent `http_options` (timeout/retries) are
   honoured exactly as configured, token-usage reporting, cost and latency.
3. **Most "intelligence" is hand-written rules.** That is right for
   calculations, but the signature rules (`core/analysis.py`) cover only the
   failure modes in the synthetic world. Real networks add BGP churn, MTU and
   asymmetric-routing issues, sub-sampling microbursts, and correlated
   multi-domain faults. A live LLM might add value in re-ranking and
   explanation, or it might degrade a correct rule ranking; the current design
   lets the analyst override confidence with reasons, and only the verifier's
   *support* checks protect against that, not *correctness* checks.
4. **Thresholds are tuned to our own generator.** Detection rules and noise
   levels were developed together; there is no calibration on real telemetry.
5. **The Topology Analyst works blind by design** (to stay parallel). Its
   choice of blast-radius components is a heuristic (most specific shared
   dependencies). The finalizer now recomputes risk with the same rules when
   the analyst missed the culprit, so escalation does not depend on it, but
   the topology narrative can still focus on the wrong components.
6. **Prompt-injection defence is heuristic.** Regex detection is easy to evade
   (paraphrase, encoding, other languages). Undetected malicious text reaches
   live models, albeit wrapped as untrusted data. Architectural limits (no
   write tools, code-decided status, policy filter) cap the damage but cannot
   prevent a misleading *hypothesis*.
7. **Parallelism buys little offline.** Tools run in milliseconds; the gain
   is structural independence. With a live model the specialist stage should
   take about the time of the slowest specialist, not the sum (unmeasured).
8. **Process-global runtime registry.** Fine for CLI/tests; in a long-running
   `adk web` server, runtimes for dev-UI sessions are never unregistered
   (slow memory growth) and the registry is per process.
9. **Verifier scope.** It checks existence, kind, support, policy and
   echoes, not whether a cited, existing evidence item actually supports the
   specific claim (relevance). The secondary LLM reviewer is the only semantic
   check and is untested live.
10. **Small evaluation set.** 14 cases, one author. Rates such as top-1 have
    very wide uncertainty even when live runs are added.

## ADK 2.11.0 behaviours discovered (pinned by tests)

* `SequentialAgent` ignores `ctx.end_invocation` → stages check `intake_error`.
* `before_agent_callback` content is validated against `output_schema` →
  skips return schema-valid JSON.
* `include_contents="none"` still forwards the current user message → raw
  request replaced in `before_model_callback`.
* `output_schema` + tools uses an injected `set_model_response` tool; ADK logs
  an `[EXPERIMENTAL] JSON_SCHEMA_FOR_FUNC_DECL` warning for it.

## Defects found and fixed during the review

* Output-size and evidence-count limits were configured but not enforced →
  enforced, with recorded truncation and scrubbing of uncitable IDs.
* Escalation could degrade to "unknown impact" when the topology analyst did
  not compute blast radius for the culprit → deterministic fallback.
* A drafted summary echoing injection/secret text was flagged but still
  published → withheld.
* Live evaluation would have mis-scored mock-only fault cases → skipped and
  recorded.

## Recommended next steps

1. Run live Gemini evaluation (≥5 repetitions per case), compare with mock,
   and record schema-compliance failures per agent.
2. Commission an independently authored case set (different author, different
   fault signatures) to break label/rule co-design.
3. Add an evidence-relevance check (claim ↔ evidence entity/metric alignment)
   and evaluate the LLM reviewer with seeded semantic errors.
4. Decide whether to keep the Incident Analyst as an LLM; if live runs show no
   gain over the rule ranking, make it deterministic and use the LLM only for
   explanation.
5. Before any shared deployment: authentication, managed session DB,
   encryption at rest, secret manager, OTel export, real read-only data
   integrations.
