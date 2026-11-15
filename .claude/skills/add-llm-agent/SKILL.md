---
name: add-llm-agent
description: Adds or changes an LLM agent, a tool or a stage inside an existing multi-agent pipeline in this repository, keeping its output contract, prompt, mock policy, tool allowlist and tests in step. Use when the user asks to add an agent, sub-agent, specialist, reviewer, tool or node, to change an agent's output schema or instructions, or to rewire an orchestration (sequential, parallel, loop, graph, crew).
---

# Add or change an LLM agent

An agent here is five things that must change together. Changing one alone leaves the offline
suite green and the live run broken, or the reverse.

## Checklist

1. **Output contract.** A Pydantic model for what the agent returns. Fields that code will act on
   are enums or bounded numbers, not free text. Every claim field carries the evidence ids it
   relies on.
2. **Instructions.** A prompt provider that renders only what this agent may see. It never
   includes labels, answer keys or another agent's hidden state. Retrieved documents are passed as
   untrusted data, not as instructions.
3. **Tools and allowlist.** Give the agent the smallest set of tools that does the job and add it
   to the allowlist. A tool the agent does not have is the only reliable way to stop it being used.
4. **Mock policy and failed output.** A deterministic policy that reads the rendered prompt and
   tool results and returns tool calls or the final answer, plus a schema-valid `failed` output
   used when the model times out, exhausts its budget or breaks the schema.
5. **Tests.** A contract test for the schema, an offline end-to-end test through the mock, a
   failure test (timeout, tool error, invalid output), and an allowlist test.

## Where each piece lives in OpsPilot

| Piece | File |
|---|---|
| Contract | `opspilot/contracts/` |
| Instruction provider | `opspilot/adk/prompts.py` |
| Tools, `AGENT_TOOLS`, `TOOL_ALLOWLIST`, `OUTPUT_SCHEMAS`, tree | `opspilot/adk/tools.py`, `opspilot/adk/agents.py` |
| Mock policy, `failed_output`, `POLICIES` | `opspilot/adk/mock_policies.py` |
| Tests | `tests/test_agents.py`, `tests/test_adk_contract.py`, `tests/test_failures.py`, `tests/test_output_compliance.py` |
| Docs | `docs/agent_catalog.md`, `docs/orchestration.md`, `docs/tool_contracts.md` |

In other projects find the equivalents first (`grep -rn "output_type\|output_schema\|result_type"
src/`) and follow the pattern already there instead of introducing a second one.

## Rules that hold in every project

- A new agent must earn its place: say what decision it makes that a deterministic function or an
  existing agent cannot. If the answer is "it reformats", write a function.
- Calculations stay in `core/`. If the prompt contains a threshold, a formula or a ranking rule,
  move it to code and give the agent the result.
- A stage that follows a failed stage must check for the failure itself; do not assume the
  framework stops the pipeline.
- A deterministic verifier and finaliser run after the last LLM stage. When an agent's output
  gains a field, extend the verifier to check it.
- When the orchestration changes, update the diagram in `README.md` and `ARCHITECTURE.md`.

## Verify

Run the project's offline suite and its offline evaluation (`uv run opspilot eval --guardrails`
for OpsPilot). A change to an agent's schema or instructions changes model behaviour that the
mock cannot show: say so in the summary and name the live run that would measure it
(`llm-eval` skill).
