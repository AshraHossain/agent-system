---
paths:
  - "opspilot/contracts/**"
---

# OpsPilot Contracts Rules (`opspilot/contracts/**`)

- **Zero Framework Imports**: Never import `google.adk`, `google.genai`, `fastapi`, or `opspilot.core` inside `opspilot/contracts/`. Guarded by `tests/test_adk_contract.py` and `tests/test_agents.py`.
- **Strict Immutability**: Every Pydantic `BaseModel` in `opspilot/contracts/` MUST declare `model_config = ConfigDict(frozen=True)`. Never mutate contract instances in-place; use `.model_copy(update={...})`.
- **Gemini Schema Compatibility**: Models used as `LlmAgent(output_schema=...)` (`findings.py`) must use explicit typed fields, `Literal`s, `StrEnum`s, and typed `list[...]` — never raw `dict[str, Any]` (which Gemini response schema validation rejects).
- **Evidence Traceability**: Every specialist finding, anomaly, hypothesis, key fact, and recommended diagnostic step must carry `evidence_ids: list[str]` matching `^EV-(TEL|TOP|DOC|INC|DQ)-[0-9a-f]{8}$`.
- **Bounded Strings**: Free-text fields must set explicit `Field(max_length=...)` bounds so LLM outputs cannot blow up session state or downstream prompts.
